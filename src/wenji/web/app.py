"""FastAPI app with Jinja2 SSR + minimal JSON API.

Routes:

- ``GET /`` — search box + result list + axis filter (server-side rendered)
- ``GET /api/search?q=&axis=&limit=`` — JSON results
- ``GET /api/axes`` — JSON list of axes present in DB
- ``GET /article/{article_id}`` — full article viewer (HTML)
- ``GET /healthz`` — JSON liveness probe

Server-state init is lazy: the ``Searcher`` is created on first search request,
so ``wenji serve`` returns immediately. If embedder model files are missing,
the search routes return a friendly 504 page (UX borrowed from open-design).
"""

from __future__ import annotations

import html
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from wenji.aggregate import Aggregator, Filter
from wenji.aggregate.llm import LLMClient
from wenji.ask import Asker
from wenji.classify.axes_loader import UNCLASSIFIED, AxesConfig, load_axes_config
from wenji.core.db import connect
from wenji.core.errors import ConfigError, WenjiError
from wenji.search import Searcher

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


_MD_RENDERER = None
_TAG_SPLIT_RE = re.compile(r"(<[^>]*>)")


def _markdown_renderer():
    global _MD_RENDERER
    if _MD_RENDERER is None:
        from markdown_it import MarkdownIt

        # linkify=False keeps the dep tree minimal (linkify-it-py not required)
        _MD_RENDERER = MarkdownIt("default", {"html": False, "breaks": False, "linkify": False})
    return _MD_RENDERER


def _highlight_in_html(html_text: str, query: str) -> str:
    """Wrap query terms in ``<mark>`` while staying outside HTML tags.

    Splits the string on tag boundaries; only text nodes get the substitution.
    Avoids the bug where naive replacement would corrupt attributes inside
    tags like ``<a href="勞動基準法.html">``.
    """
    if not query:
        return html_text
    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return html_text
    parts = _TAG_SPLIT_RE.split(html_text)
    out: list[str] = []
    for i, p in enumerate(parts):
        if i % 2 == 1:  # tag — leave as-is
            out.append(p)
            continue
        for term in terms:
            safe_term = html.escape(term)
            pattern = re.compile(re.escape(safe_term))
            p = pattern.sub(f"<mark>{safe_term}</mark>", p)
        out.append(p)
    return "".join(out)


def _render_chunk(text: str, query: str) -> str:
    """Render markdown ``text`` to HTML and highlight ``query`` terms inside text nodes."""
    rendered = _markdown_renderer().render(text)
    return _highlight_in_html(rendered, query)


def _plain_preview(text: str, n: int = 36) -> str:
    """Strip leading markdown markers + inline emphasis for sidebar preview."""
    s = text.lstrip()
    s = re.sub(r"^[#>*\-_`]+\s*", "", s)
    s = re.sub(r"[*_`]", "", s)
    return s[:n]


def _llm_client_from_env() -> LLMClient | None:
    """Build an LLMClient from WENJI_LLM_* env vars, or return None when unset."""
    base_url = os.environ.get("WENJI_LLM_BASE_URL")
    model = os.environ.get("WENJI_LLM_MODEL")
    api_key = os.environ.get("WENJI_LLM_API_KEY")
    if not (base_url and model and api_key):
        return None
    timeout = float(os.environ.get("WENJI_LLM_TIMEOUT", "10.0"))
    return LLMClient(base_url=base_url, model=model, api_key=api_key, timeout=timeout)


def _axes_config_from_env() -> AxesConfig | None:
    """Load AxesConfig from WENJI_AXES_YAML, or return None on unset/error."""
    path = os.environ.get("WENJI_AXES_YAML")
    if not path:
        return None
    try:
        return load_axes_config(path)
    except (ConfigError, FileNotFoundError, OSError) as exc:
        logger.warning("WENJI_AXES_YAML present but failed to load (%s); ignoring", exc)
        return None


def create_app(
    *,
    db_path: str | Path | None = None,
    searcher: Searcher | None = None,
    llm_client: LLMClient | None = None,
    axes_config: AxesConfig | None = None,
    entity_scorer: Any | None = None,
    intent_classifier: Any | None = None,
) -> FastAPI:
    """Build a FastAPI app. ``searcher`` injection skips lazy load (test path)."""

    app = FastAPI(title="wenji", docs_url="/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    state: dict[str, Any] = {
        "db_path": Path(db_path)
        if db_path
        else Path(os.environ.get("WENJI_DB_PATH", "data/wenji.db")),
        "searcher": searcher,
        "llm_client": llm_client if llm_client is not None else _llm_client_from_env(),
        "axes_config": axes_config if axes_config is not None else _axes_config_from_env(),
        "entity_scorer": entity_scorer,
        "intent_classifier": intent_classifier,
    }

    def _get_conn() -> sqlite3.Connection:
        # FastAPI dispatches sync routes via a thread pool, so the lazy
        # Searcher connection (and any per-request connection) must accept
        # cross-thread access. SQLite still serialises writes via its file
        # lock, and the web app only writes to query_rewrite_cache /
        # aggregate_cache (low contention).
        return connect(state["db_path"], check_same_thread=False)

    def _get_searcher() -> Searcher | None:
        """Lazy-construct Searcher; return None if model files missing (degraded mode).

        v0.3.2: if WENJI_LLM_* env config is enabled (and not overridden by
        WENJI_REWRITE_OVERRIDE=disabled), instantiate a QueryRewriter and
        inject it into the Searcher.

        v0.3.6: WENJI_ENTITY_SOURCES (comma-separated source list) and
        WENJI_INTENT_SOURCES, when set, instantiate EntityScorer and
        IntentClassifier and inject them. Source items follow
        ``EntityScorer.from_sources`` syntax (``example:<name>`` or path).

        v0.3.6 (OPEN-7 補): WENJI_ENTITY_ALIAS_MAP (JSON file path mapping
        ``{alias: canonical_or_list}``) is forwarded to ``EntityScorer.from_sources``
        as ``alias_map``. WENJI_INTENT_SOURCE_TYPES (JSON file path mapping
        ``{intent: [source_type]}``) is forwarded to ``IntentClassifier.from_sources``
        as ``intent_source_types``. These enable full deployment-specific
        composition (e.g. logos consumer setup) over env-based loading.
        """
        if state["searcher"] is not None:
            return state["searcher"]
        try:
            from wenji.config import load_llm_config_from_env
            from wenji.ingest.embed import Embedder
            from wenji.search.entity import EntityScorer
            from wenji.search.intent import IntentClassifier
            from wenji.search.rewrite import QueryRewriter

            conn = _get_conn()
            llm_cfg = load_llm_config_from_env()
            override = os.environ.get("WENJI_REWRITE_OVERRIDE", "").lower()
            rewrite_enabled = (
                override == "enabled" or (override != "disabled" and llm_cfg.enabled)
            )
            rewriter: QueryRewriter | None = None
            if rewrite_enabled and llm_cfg.enabled:
                rewriter = QueryRewriter(
                    conn,
                    api_url=llm_cfg.base_url.rstrip("/") + "/chat/completions",
                    api_key=llm_cfg.api_key,
                    model=llm_cfg.model,
                    timeout=1.5,
                    ttl_days=llm_cfg.rewrite_cache_ttl_days,
                )
            else:
                logger.debug(
                    "query rewrite disabled (override=%r, llm_cfg.enabled=%s)",
                    override,
                    llm_cfg.enabled,
                )

            entity_scorer = state.get("entity_scorer")
            if entity_scorer is None:
                entity_sources = os.environ.get("WENJI_ENTITY_SOURCES", "").strip()
                if entity_sources:
                    try:
                        alias_map_path = os.environ.get("WENJI_ENTITY_ALIAS_MAP", "").strip()
                        alias_map: dict | None = None
                        if alias_map_path:
                            alias_map = json.loads(
                                Path(alias_map_path).read_text(encoding="utf-8")
                            )
                        entity_scorer = EntityScorer.from_sources(
                            [s.strip() for s in entity_sources.split(",") if s.strip()],
                            alias_map=alias_map,
                        )
                    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                        logger.warning("WENJI_ENTITY_SOURCES failed (%s); skipping", exc)

            intent_classifier = state.get("intent_classifier")
            if intent_classifier is None:
                intent_sources = os.environ.get("WENJI_INTENT_SOURCES", "").strip()
                if intent_sources:
                    try:
                        ist_path = os.environ.get("WENJI_INTENT_SOURCE_TYPES", "").strip()
                        intent_source_types: dict | None = None
                        if ist_path:
                            intent_source_types = json.loads(
                                Path(ist_path).read_text(encoding="utf-8")
                            )
                        intent_classifier = IntentClassifier.from_sources(
                            [s.strip() for s in intent_sources.split(",") if s.strip()],
                            intent_source_types=intent_source_types,
                        )
                    except (FileNotFoundError, ValueError, OSError, json.JSONDecodeError) as exc:
                        logger.warning("WENJI_INTENT_SOURCES failed (%s); skipping", exc)

            state["searcher"] = Searcher(
                conn,
                Embedder(),
                rewriter=rewriter,
                entity_scorer=entity_scorer,
                intent_classifier=intent_classifier,
            )
            return state["searcher"]
        except (ConfigError, WenjiError):
            return None

    def _get_aggregator() -> Aggregator:
        """Construct an Aggregator with a fresh DB connection + the configured LLM client."""
        return Aggregator(_get_conn(), llm_client=state["llm_client"])

    def _get_asker() -> Asker:
        """Construct an Asker; raise 503/504 when LLM or searcher are unavailable."""
        if state["llm_client"] is None:
            raise HTTPException(status_code=503, detail="LLM not configured")
        searcher = _get_searcher()
        if searcher is None:
            raise HTTPException(status_code=504, detail="search engine not ready")
        return Asker(_get_conn(), llm_client=state["llm_client"], searcher=searcher)

    def _build_filter(filter_dict: dict | None) -> Filter | None:
        if not filter_dict:
            return None
        try:
            return Filter(**filter_dict)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid filter: {exc}") from exc

    def _post_filter_results(
        conn: sqlite3.Connection,
        results: list[dict[str, Any]],
        *,
        tag: str | None,
        source_type: str | None,
    ) -> list[dict[str, Any]]:
        if not results or (tag is None and source_type is None):
            return results
        f = Filter(tag=tag, source_type=source_type)
        clause, params = f.to_sql_where(table_alias="m")
        if not clause:
            return results
        ids = [r["article_id"] for r in results]
        placeholders = ",".join(["?"] * len(ids))
        rows = conn.execute(
            f"SELECT m.article_id FROM articles_meta m "
            f"WHERE m.article_id IN ({placeholders}) AND {clause}",
            [*ids, *params],
        ).fetchall()
        allowed = {row[0] for row in rows}
        return [r for r in results if r["article_id"] in allowed]

    def _render_narrative(narrative: str | None) -> str | None:
        if not narrative:
            return None
        return _markdown_renderer().render(narrative)

    @app.get("/healthz")
    def healthz() -> dict[str, Any]:
        return {
            "status": "ok",
            "db_path": str(state["db_path"]),
            "searcher_ready": state["searcher"] is not None,
        }

    def _compute_facets(
        conn: sqlite3.Connection,
        top: int = 15,
        *,
        query_ids: set[str] | None = None,
    ) -> dict[str, Any]:
        """Compute corpus-wide tag/source_type facets, optionally narrowed.

        ``query_ids`` is the article_id set that the *current* search would
        surface (typically the hybrid top-50 candidate pool). Restricting
        ``query_count`` to that exact set guarantees that
        ``facet count = clicks-yields count``: if facet shows ``(c/q)`` then
        clicking the tag returns ``min(q, 10)`` results.
        """
        if top <= 0:
            top = 15
        top = min(top, 50)
        # query-aware tag/source_type counts, limited to articles matching `query`
        tag_q_counts: dict[str, int] = {}
        source_q_counts: dict[str, int] = {}
        if query_ids:
            ids = list(query_ids)
            # SQLite has a default 999-parameter limit; chunk if needed
            for offset in range(0, len(ids), 900):
                chunk = ids[offset : offset + 900]
                placeholders = ",".join(["?"] * len(chunk))
                try:
                    rows = conn.execute(
                        "SELECT je.value AS tag, COUNT(*) AS c "
                        f"FROM articles_meta m, json_each(NULLIF(m.tags, '')) je "
                        f"WHERE m.article_id IN ({placeholders}) "
                        "GROUP BY je.value",
                        chunk,
                    ).fetchall()
                    for name, c in rows:
                        tag_q_counts[name] = tag_q_counts.get(name, 0) + c
                except sqlite3.OperationalError:
                    pass
                try:
                    rows = conn.execute(
                        "SELECT source_type, COUNT(*) AS c "
                        f"FROM articles_meta "
                        f"WHERE article_id IN ({placeholders}) "
                        "AND source_type IS NOT NULL AND source_type != '' "
                        "GROUP BY source_type",
                        chunk,
                    ).fetchall()
                    for name, c in rows:
                        source_q_counts[name] = source_q_counts.get(name, 0) + c
                except sqlite3.OperationalError:
                    pass

        try:
            tag_rows = conn.execute(
                "SELECT je.value AS tag, COUNT(*) AS c "
                "FROM articles_meta m, json_each(NULLIF(m.tags, '')) je "
                "WHERE IFNULL(m.category, '') != 'excluded' "
                "GROUP BY je.value "
                "ORDER BY c DESC, je.value ASC "
                "LIMIT ?",
                (top,),
            ).fetchall()
        except sqlite3.OperationalError:
            tag_rows = []
        try:
            source_rows = conn.execute(
                "SELECT source_type, COUNT(*) AS c "
                "FROM articles_meta "
                "WHERE source_type IS NOT NULL AND source_type != '' "
                "AND IFNULL(category, '') != 'excluded' "
                "GROUP BY source_type "
                "ORDER BY c DESC, source_type ASC "
                "LIMIT ?",
                (top,),
            ).fetchall()
        except sqlite3.OperationalError:
            source_rows = []

        def _decorate(rows, q_counts):
            decorated = [
                {
                    "name": r[0],
                    "count": r[1],
                    "query_count": q_counts.get(r[0], 0) if query_ids is not None else None,
                }
                for r in rows
            ]
            # When a query narrows the corpus, surface query-relevant
            # facets first by sorting on query_count desc, breaking ties
            # by corpus count desc.
            if query_ids is not None:
                decorated.sort(key=lambda f: (-(f["query_count"] or 0), -f["count"]))
            return decorated

        return {
            "tags": _decorate(tag_rows, tag_q_counts),
            "source_types": _decorate(source_rows, source_q_counts),
            "query_aware": query_ids is not None,
        }

    @app.get("/api/facets")
    def api_facets(top: int = 15) -> JSONResponse:
        """Return top tags + source_types for the entity sidebar (D7).

        Always corpus-wide here — the query-aware variant is computed only
        inside the `/` index handler where search results are available.
        """
        conn = _get_conn()
        try:
            return JSONResponse(_compute_facets(conn, top))
        finally:
            conn.close()

    @app.get("/api/axes")
    def api_axes() -> dict[str, Any]:
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT axis_id, COUNT(*) FROM article_axes "
                "WHERE axis_id != ? GROUP BY axis_id ORDER BY 2 DESC",
                (UNCLASSIFIED,),
            ).fetchall()
        finally:
            conn.close()
        cfg: AxesConfig | None = state.get("axes_config")
        parents = {a.id: a.parent for a in cfg.axes} if cfg else {}
        return {"axes": [{"id": r[0], "parent": parents.get(r[0]), "count": r[1]} for r in rows]}

    @app.get("/api/stats")
    def api_stats() -> JSONResponse:
        """Read-only corpus + index stats (v0.3.3 observability)."""
        from wenji.observability import compute_stats

        conn = _get_conn()
        try:
            return JSONResponse(compute_stats(conn, state.get("axes_config")))
        finally:
            conn.close()

    @app.get("/api/segment")
    def api_segment(q: str = "") -> JSONResponse:
        """Read-only query pipeline trace (v0.3.3 observability).

        The rewriter is sourced from the lazy Searcher so that ``rewrite``
        reflects the same configuration ``/api/search`` sees. If the embedder
        model is absent, _get_searcher returns None and ``rewrite`` falls back
        to null (graceful degradation, not an error).
        """
        from wenji.observability import compute_segment_trace

        if not q.strip():
            return JSONResponse(
                status_code=400,
                content={"error": "query parameter 'q' is required"},
            )
        rewriter = None
        entity_scorer = None
        intent_classifier = None
        searcher = _get_searcher()
        if searcher is not None:
            rewriter = getattr(searcher, "rewriter", None)
            entity_scorer = getattr(searcher, "entity_scorer", None)
            intent_classifier = getattr(searcher, "intent_classifier", None)
        return JSONResponse(
            compute_segment_trace(
                q,
                rewriter=rewriter,
                entity_scorer=entity_scorer,
                intent_classifier=intent_classifier,
            )
        )

    @app.get("/api/search")
    def api_search(q: str, axis: str | None = None, limit: int = 10) -> JSONResponse:
        s = _get_searcher()
        if s is None:
            return JSONResponse(
                status_code=504,
                content={
                    "error": "search engine starting up",
                    "detail": (
                        "Embedder model files not found. "
                        "Run `wenji download-model` (Group 9) or set WENJI_MODEL_DIR."
                    ),
                },
            )
        try:
            # Compute rewritten_query for transparency (v0.3.2). Calling
            # rewriter.rewrite() here is safe — Searcher.search() also calls it
            # internally and the second call hits the cache (same row in
            # query_rewrite_cache, no extra LLM API call).
            rewritten_query: str | None = None
            if getattr(s, "rewriter", None) is not None:
                try:
                    effective = s.rewriter.rewrite(q)
                    if effective and effective != q:
                        rewritten_query = effective
                except Exception:
                    # Rewriter has its own timeout/fallback; ignore here.
                    pass
            results = s.search(q, axis=axis, limit=limit)
            return JSONResponse(
                {"results": results, "query": q, "rewritten_query": rewritten_query}
            )
        except WenjiError as exc:
            return JSONResponse(status_code=504, content={"error": str(exc)})

    @app.get("/api/aggregate/subtypes")
    def api_aggregate_subtypes() -> JSONResponse:
        """List distinct non-empty subtypes in the corpus, with article counts.

        Used by the chat panel to render a checkbox group instead of asking
        the user to type the exclusion list manually.
        """
        conn = _get_conn()
        try:
            rows = conn.execute(
                "SELECT subtype, COUNT(*) FROM articles_meta "
                "WHERE subtype IS NOT NULL AND subtype != '' "
                "GROUP BY subtype ORDER BY 2 DESC"
            ).fetchall()
        finally:
            conn.close()
        return JSONResponse({"subtypes": [{"name": r[0], "count": r[1]} for r in rows]})

    @app.post("/api/aggregate/topic")
    async def api_aggregate_topic(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        tag = body.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise HTTPException(status_code=400, detail="missing or empty 'tag'")
        k_raw = body.get("k", 5)
        if not isinstance(k_raw, int) or k_raw <= 0:
            raise HTTPException(status_code=400, detail="'k' must be a positive integer")
        filter_obj = _build_filter(body.get("filter"))
        agg = _get_aggregator()
        try:
            result = agg.topic_summary(tag, filter=filter_obj, k=k_raw)
        finally:
            agg.db.close()
        payload = asdict(result)
        payload["narrative_html"] = _render_narrative(result.narrative)
        return JSONResponse(payload)

    @app.post("/api/ask")
    async def api_ask(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        q = body.get("q")
        if not isinstance(q, str) or not q.strip():
            raise HTTPException(status_code=400, detail="missing or empty 'q'")
        k_raw = body.get("k", 5)
        if not isinstance(k_raw, int) or k_raw <= 0:
            raise HTTPException(status_code=400, detail="'k' must be a positive integer")
        axis = body.get("axis")
        if axis is not None and not isinstance(axis, str):
            raise HTTPException(status_code=400, detail="'axis' must be a string or null")
        filter_obj = _build_filter(body.get("filter"))
        asker = _get_asker()
        try:
            result = asker.ask(q, k=k_raw, axis=axis, filter=filter_obj)
        finally:
            asker.db.close()
        payload = asdict(result)
        payload["narrative_html"] = _render_narrative(result.answer)
        return JSONResponse(payload)

    @app.post("/api/aggregate/concept")
    async def api_aggregate_concept(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON body: {exc}") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        concept = body.get("concept")
        if not isinstance(concept, str) or not concept.strip():
            raise HTTPException(status_code=400, detail="missing or empty 'concept'")
        top_sources = body.get("top_sources", 4)
        per_source = body.get("per_source", 3)
        if not isinstance(top_sources, int) or top_sources <= 0:
            raise HTTPException(status_code=400, detail="'top_sources' must be a positive integer")
        if not isinstance(per_source, int) or per_source <= 0:
            raise HTTPException(status_code=400, detail="'per_source' must be a positive integer")
        filter_obj = _build_filter(body.get("filter"))
        agg = _get_aggregator()
        try:
            result = agg.concept_perspectives(
                concept,
                filter=filter_obj,
                top_sources=top_sources,
                per_source=per_source,
            )
        finally:
            agg.db.close()
        payload = asdict(result)
        payload["narrative_html"] = _render_narrative(result.narrative)
        return JSONResponse(payload)

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        q: str = "",
        axis: str | None = None,
        tag: str | None = None,
        source_type: str | None = None,
    ) -> HTMLResponse:
        results: list[dict[str, Any]] = []
        candidate_ids: set[str] | None = None
        error_message: str | None = None
        # Browse-by-facet: tag/source_type without a query lists matching
        # articles directly from articles_meta (newest first), so users can
        # explore a tag in isolation from the article-page tag chips.
        if not q and (tag or source_type):
            browse_conn = _get_conn()
            try:
                f = Filter(tag=tag, source_type=source_type)
                clause, params = f.to_sql_where(table_alias="m")
                sql = (
                    "SELECT m.article_id, m.title, m.source_type, m.category, m.pub_date "
                    "FROM articles_meta m "
                    "WHERE IFNULL(m.category, '') != 'excluded'"
                    + (f" AND {clause}" if clause else "")
                    + " ORDER BY COALESCE(m.pub_date, '') DESC LIMIT 30"
                )
                rows = browse_conn.execute(sql, params).fetchall()
                results = [
                    {
                        "article_id": r[0],
                        "title": r[1],
                        "source_type": r[2],
                        "category": r[3],
                        "pub_date": r[4],
                        "hybrid_score": None,
                        "content_snippet": "",
                    }
                    for r in rows
                ]
            finally:
                browse_conn.close()
        elif q:
            s = _get_searcher()
            if s is None:
                error_message = (
                    "搜尋引擎啟動中（embedder 模型檔尚未就緒）。"
                    "請執行 `wenji download-model` 或設定 WENJI_MODEL_DIR 後重試。"
                )
            else:
                try:
                    # Always pull 50 candidates so (a) the facet sidebar's
                    # query_count reflects the same set the user would see
                    # after clicking, and (b) tag/source_type post-filter
                    # has enough headroom for rare facets.
                    fetch_limit = 50
                    results = s.search(q, axis=axis, limit=fetch_limit)
                    candidate_ids = {r["article_id"] for r in results}
                    if tag or source_type:
                        filter_conn = _get_conn()
                        try:
                            results = _post_filter_results(
                                filter_conn, results, tag=tag, source_type=source_type
                            )
                        finally:
                            filter_conn.close()
                    results = results[:10]
                except WenjiError as exc:
                    error_message = f"搜尋失敗：{exc}"

        # axes for filter sidebar
        try:
            conn = _get_conn()
            axis_rows = conn.execute(
                "SELECT axis_id, COUNT(*) FROM article_axes "
                "WHERE axis_id != ? GROUP BY axis_id ORDER BY 2 DESC",
                (UNCLASSIFIED,),
            ).fetchall()
            conn.close()
            cfg: AxesConfig | None = state.get("axes_config")
            axes = []
            for r in axis_rows:
                axis_def = cfg.find_axis(r[0]) if cfg else None
                depth = len(cfg.ancestors(r[0])) if cfg and axis_def else 0
                axes.append(
                    {
                        "id": r[0],
                        "parent": axis_def.parent if axis_def else None,
                        "depth": depth,
                        "count": r[1],
                    }
                )
        except sqlite3.OperationalError:
            axes = []

        # Facet sidebar (top tags + source_types) — best-effort, ignore failures
        try:
            conn = _get_conn()
            try:
                facets = _compute_facets(conn, 15, query_ids=candidate_ids)
            finally:
                conn.close()
        except sqlite3.OperationalError:
            facets = {"tags": [], "source_types": [], "query_aware": False}

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "axis": axis,
                "tag": tag,
                "source_type": source_type,
                "results": results,
                "axes": axes,
                "facets": facets,
                "error": error_message,
            },
        )

    @app.get("/article/{article_id}", response_class=HTMLResponse)
    def article(
        request: Request,
        article_id: str,
        q: str = "",
    ) -> HTMLResponse:
        conn = _get_conn()
        try:
            meta = conn.execute(
                "SELECT article_id, title, source_type, author, pub_date, "
                "category, tags, source_url, description, chunk_count "
                "FROM articles_meta WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            if meta is None:
                raise HTTPException(status_code=404, detail=f"article not found: {article_id}")
            content_row = conn.execute(
                "SELECT content_raw FROM articles_fts WHERE article_id = ?",
                (article_id,),
            ).fetchone()
            content = (content_row[0] if content_row else "") or ""

            # If the article was chunked, list chunk_text_raw + chunk_index in
            # order so the template can render anchorable sections. Otherwise
            # fall back to whole-content render.
            chunk_count = int(meta[9] or 0)
            chunks: list[dict] = []
            matched_indexes: set[int] = set()
            if chunk_count > 0:
                chunk_rows = conn.execute(
                    """
                    SELECT chunk_index, chunk_text_raw
                    FROM chunks_fts
                    WHERE article_id = ?
                    ORDER BY CAST(chunk_index AS INTEGER)
                    """,
                    (article_id,),
                ).fetchall()
                chunks = [
                    {
                        "chunk_index": int(r[0]),
                        "chunk_text": r[1] or "",
                        "chunk_text_html": _render_chunk(r[1] or "", q),
                        "preview": _plain_preview(r[1] or "", 36),
                    }
                    for r in chunk_rows
                ]
                # If a query was passed, mark which chunks match (for highlight + scroll)
                if q.strip():
                    from wenji.search.bm25 import build_fts_query

                    fts_query = build_fts_query(q)
                    if fts_query:
                        try:
                            mrows = conn.execute(
                                """
                                SELECT chunk_index FROM chunks_fts
                                WHERE chunks_fts MATCH ? AND article_id = ?
                                """,
                                (fts_query, article_id),
                            ).fetchall()
                            matched_indexes = {int(r[0]) for r in mrows}
                        except sqlite3.OperationalError:
                            matched_indexes = set()
        finally:
            conn.close()

        import json as _json

        try:
            tag_list = _json.loads(meta[6]) if meta[6] else []
        except _json.JSONDecodeError:
            tag_list = []
        if not isinstance(tag_list, list):
            tag_list = []

        article_data = {
            "article_id": meta[0],
            "title": meta[1],
            "source_type": meta[2],
            "author": meta[3],
            "pub_date": meta[4],
            "category": meta[5],
            "tags": meta[6],
            "tag_list": tag_list,
            "source_url": meta[7],
            "description": meta[8],
            "content": content,
            "chunks": chunks,
            "matched_indexes": sorted(matched_indexes),
            "query": q,
        }
        return templates.TemplateResponse(request, "article.html", {"article": article_data})

    return app


# Module-level app for `uvicorn wenji.web.app:app`
app = create_app()
