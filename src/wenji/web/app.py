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

import hmac
import html
import json
import logging
import os
import re
import sqlite3
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.status import HTTP_401_UNAUTHORIZED

from wenji.aggregate import Aggregator, Filter
from wenji.aggregate.llm import LLMClient
from wenji.ask import Asker
from wenji.browse.tag import TagBrowser
from wenji.classify.axes_loader import UNCLASSIFIED, AxesConfig, load_axes_config
from wenji.core.db import connect
from wenji.core.errors import ConfigError, WenjiError
from wenji.search import Searcher
from wenji.web.branding import load_branding_from_env

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

TOPIC_SHORTCUTS = [
    {
        "category": "靈修與實踐",
        "topics": ["禱告的意義", "禁食禱告", "靈命成長", "屬靈爭戰", "讀經方法"],
    },
    {
        "category": "教會與事工",
        "topics": ["門訓落實", "小組事工", "宣教策略", "青年牧區", "領袖培育"],
    },
]


_MD_RENDERER = None
_TAG_SPLIT_RE = re.compile(r"(<[^>]*>)")


def _check_trusted_path(path_str: str, db_dir: Path) -> Path | None:
    """Resolve and validate *path_str* is under *db_dir*."""
    p = Path(path_str).resolve()
    db_dir = db_dir.resolve()
    try:
        p.relative_to(db_dir)
        return p
    except ValueError:
        logger.warning("ignoring untrusted path %s (must be under %s)", p, db_dir)
        return None


def _safe_link(url: str) -> bool:
    """Allow only http/https/mailto links."""
    return url.startswith(("http://", "https://", "mailto:"))


def _markdown_renderer():
    global _MD_RENDERER
    if _MD_RENDERER is None:
        from markdown_it import MarkdownIt
        try:
            from mdit_py_plugins.footnote import footnote_plugin
            from mdit_py_plugins.front_matter import front_matter_plugin
        except ImportError:
            front_matter_plugin = None
            footnote_plugin = None

        _MD_RENDERER = MarkdownIt(
            "default",
            {"html": True, "breaks": True, "linkify": True, "validateLink": _safe_link},
        )
        if front_matter_plugin:
            _MD_RENDERER.use(front_matter_plugin)
        if footnote_plugin:
            _MD_RENDERER.use(footnote_plugin)

    return _MD_RENDERER


_ALLOWED_LLM_BASE_URL_PREFIXES = (
    "https://",
    "http://localhost",
    "http://127.0.0.1",
    "http://100.",
)


def _highlight_in_html(html_text: str, query: str) -> str:
    """Wrap query terms in ``<mark>`` while staying outside HTML tags."""
    if not query:
        return html_text
    query = query[:5000]
    terms = [t.strip() for t in query.split() if t.strip()][:32]
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
    """Build an LLMClient from WENJI_LLM_* env vars, or return None when unset.

    *WENJI_LLM_BASE_URL* must start with one of ``_ALLOWED_LLM_BASE_URL_PREFIXES``
    (HTTPS, localhost, 127.0.0.1, or RFC 1918).  Anything else is treated as
    unset to prevent SSRF via env injection.
    """
    base_url = os.environ.get("WENJI_LLM_BASE_URL")
    model = os.environ.get("WENJI_LLM_MODEL")
    api_key = os.environ.get("WENJI_LLM_API_KEY")
    if not (base_url and model and api_key):
        return None
    base_url = base_url.strip()
    if not base_url.startswith(_ALLOWED_LLM_BASE_URL_PREFIXES):
        logger.warning(
            "WENJI_LLM_BASE_URL (%s) not in allowed prefixes; treating as unset",
            base_url[:30],
        )
        return None
    timeout = min(float(os.environ.get("WENJI_LLM_TIMEOUT", "10.0")), 30.0)
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

    cors_origins_raw = os.environ.get("WENJI_CORS_ORIGINS", "https://logos.jacobmei.com")
    cors_origins = [o.strip() for o in cors_origins_raw.split(",") if o.strip()]
    if "*" in cors_origins:
        logger.warning("WENJI_CORS_ORIGINS contains '*'; ignoring for security")
        cors_origins = [o for o in cors_origins if o != "*"]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-API-Key"],
    )

    api_key = os.environ.get("WENJI_API_KEY", "").strip()

    class APIKeyMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next):
            if api_key:
                if request.url.path in ("/healthz",):
                    return await call_next(request)
                received = request.headers.get("X-API-Key", "") or ""
                if not hmac.compare_digest(received, api_key):
                    return JSONResponse(
                        status_code=HTTP_401_UNAUTHORIZED,
                        content={"error": "missing or invalid X-API-Key"},
                    )
            return await call_next(request)

    app.add_middleware(APIKeyMiddleware)

    # Rate-limit is NOT YET IMPLEMENTED.  Deploy behind a reverse-proxy
    # (Cloudflare, nginx) for per-IP throttling.
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

    # Branding env vars (all optional). Validated at startup so misconfiguration
    # cannot reach a serving process. Unset = no SEO meta rendered, brand text
    # falls back to neutral "wenji". See src/wenji/web/branding.py.
    branding = load_branding_from_env()
    templates.env.globals["site_url"] = branding.site_url
    templates.env.globals["site_name"] = branding.site_name
    templates.env.globals["og_image_url"] = branding.og_image_url

    state: dict[str, Any] = {
        "db_path": Path(db_path)
        if db_path
        else Path(os.environ.get("WENJI_DB_PATH", "data/wenji.db")),
        "searcher": searcher,
        "llm_client": llm_client if llm_client is not None else _llm_client_from_env(),
        "axes_config": axes_config if axes_config is not None else _axes_config_from_env(),
        "entity_scorer": entity_scorer,
        "intent_classifier": intent_classifier,
        "tag_browser": None,  # Lazy init
        # Demo/tenant mode: when set, all queries are pre-filtered to this source_type.
        # Set via WENJI_DEMO_SOURCE env var. Remove env var + restart to restore full corpus.
        "demo_source": os.environ.get("WENJI_DEMO_SOURCE", "").strip() or None,
    }

    def _get_tag_browser() -> TagBrowser:
        if state["tag_browser"] is None:
            sfilter = state["demo_source"]
            state["tag_browser"] = TagBrowser(str(state["db_path"]), source_filter=sfilter)
        return state["tag_browser"]

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
            rewrite_enabled = override == "enabled" or (override != "disabled" and llm_cfg.enabled)
            rewriter: QueryRewriter | None = None
            if rewrite_enabled and llm_cfg.enabled:
                base_url = (llm_cfg.base_url or "").strip()
                if base_url.startswith(_ALLOWED_LLM_BASE_URL_PREFIXES):
                    rewriter = QueryRewriter(
                        conn,
                        api_url=base_url.rstrip("/") + "/chat/completions",
                        api_key=llm_cfg.api_key,
                        model=llm_cfg.model,
                        timeout=1.5,
                        ttl_days=llm_cfg.rewrite_cache_ttl_days,
                    )
                else:
                    logger.warning(
                        "WENJI_LLM_BASE_URL (%s) not in allowed prefixes; rewrite disabled",
                        (llm_cfg.base_url or "")[:30],
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
                            trusted = _check_trusted_path(alias_map_path, state["db_path"].parent)
                            if trusted is not None:
                                alias_map = json.loads(trusted.read_text(encoding="utf-8"))
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
                            trusted = _check_trusted_path(ist_path, state["db_path"].parent)
                            if trusted is not None:
                                intent_source_types = json.loads(
                                    trusted.read_text(encoding="utf-8")
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

    def _build_filter(filter_dict: dict | None, demo_src: str | None = None) -> Filter | None:
        """Build a Filter from request body dict, merging demo_source constraint.

        ``demo_src`` (from WENJI_DEMO_SOURCE) is always enforced as the
        source_type baseline — caller-supplied filter can further narrow
        (e.g. add a tag) but cannot widen beyond the demo source.
        """
        merged = dict(filter_dict) if filter_dict else {}
        if demo_src:
            merged.setdefault("source_type", demo_src)
        if not merged:
            return None
        try:
            return Filter(**merged)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid filter: {exc}") from exc

    def _post_filter_results(
        conn: sqlite3.Connection,
        results: list[dict[str, Any]],
        *,
        tag: str | None,
        source_type: str | None,
        year: str | None = None,
    ) -> list[dict[str, Any]]:
        if not results or (tag is None and source_type is None and year is None):
            return results
        f_kwargs = {"tag": tag, "source_type": source_type}
        if year:
            f_kwargs["pub_year"] = int(year)
        f = Filter(**f_kwargs)
        clause, params = f.to_sql_where(table_alias="m")
        if not clause:
            return results
        f_kwargs = {"tag": tag, "source_type": source_type}
        if year:
            f_kwargs["pub_year"] = int(year)
        f = Filter(**f_kwargs)
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
            "searcher_ready": state["searcher"] is not None,
        }

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots_txt() -> PlainTextResponse:
        # When WENJI_SITE_URL is unset, default to conservative deny so private
        # corpora are not crawled by default. When set, emit a permissive policy
        # plus a sitemap line for that site.
        if not branding.site_url:
            return PlainTextResponse("User-agent: *\nDisallow: /\n")
        body = (
            "User-agent: *\n"
            "Allow: /\n"
            "Disallow: /api/\n"
            "\n"
            "# AI Bots\n"
            "User-agent: GPTBot\nAllow: /\n"
            "User-agent: ChatGPT-User\nAllow: /\n"
            "User-agent: Claude-WebCheck\nAllow: /\n"
            "User-agent: ClaudeBot\nAllow: /\n"
            "User-agent: Google-Extended\nAllow: /\n"
            "User-agent: PerplexityBot\nAllow: /\n"
            "User-agent: YouBot\nAllow: /\n"
            "\n"
            f"Sitemap: {branding.site_url}/sitemap.xml\n"
        )
        return PlainTextResponse(body)

    @app.get("/llms.txt", response_class=PlainTextResponse)
    def llms_txt() -> PlainTextResponse:
        if not branding.site_url:
            raise HTTPException(status_code=404)
        name = branding.site_name or "wenji"
        body = (
            f"# {name} Knowledge Engine\n"
            "中文 markdown 知識搜尋引擎。\n"
            "\n"
            "## Core Links\n"
            f"- [Home]({branding.site_url}/)\n"
            f"- [Sitemap]({branding.site_url}/sitemap.xml)\n"
        )
        return PlainTextResponse(body)

    @app.get("/ai.txt", response_class=PlainTextResponse)
    def ai_txt() -> str:
        return "User-agent: *\nAllow: /article/*.md\nAllow: /article/*\n"

    @app.get("/sitemap.xml")
    def sitemap_xml() -> Response:
        if not branding.site_url:
            raise HTTPException(status_code=404)
        body = (
            '<?xml version="1.0" encoding="UTF-8"?>\n'
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
            f"  <url><loc>{branding.site_url}/</loc><priority>1.0</priority></url>\n"
            "</urlset>"
        )
        return Response(content=body, media_type="application/xml")

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
        demo_src = state["demo_source"]
        try:
            facets = _compute_facets(conn, top)
            # In demo mode, hide source_types other than the pinned one
            if demo_src:
                facets["source_types"] = [
                    s for s in facets["source_types"] if s["name"] == demo_src
                ]
            return JSONResponse(facets)
        finally:
            conn.close()

    @app.get("/api/axes")
    def api_axes() -> dict[str, Any]:
        conn = _get_conn()
        demo_src = state["demo_source"]
        try:
            if demo_src:
                sql = (
                    "SELECT a.axis_id, COUNT(*) FROM article_axes a "
                    "JOIN articles_meta m ON a.article_id = m.article_id "
                    "WHERE a.axis_id != ? AND m.source_type = ? "
                    "GROUP BY a.axis_id ORDER BY 2 DESC"
                )
                params = (UNCLASSIFIED, demo_src)
            else:
                sql = (
                    "SELECT axis_id, COUNT(*) FROM article_axes "
                    "WHERE axis_id != ? GROUP BY axis_id ORDER BY 2 DESC"
                )
                params = (UNCLASSIFIED,)
            rows = conn.execute(sql, params).fetchall()
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
        limit = max(0, min(limit, 200))
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
        demo_src = state["demo_source"]
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
            if demo_src:
                filter_conn = _get_conn()
                try:
                    results = _post_filter_results(
                        filter_conn, results, tag=None, source_type=demo_src
                    )
                finally:
                    filter_conn.close()
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
        demo_src = state["demo_source"]
        try:
            if demo_src:
                sql = (
                    "SELECT subtype, COUNT(*) FROM articles_meta "
                    "WHERE subtype IS NOT NULL AND subtype != '' AND source_type = ? "
                    "GROUP BY subtype ORDER BY 2 DESC"
                )
                params = (demo_src,)
            else:
                sql = (
                    "SELECT subtype, COUNT(*) FROM articles_meta "
                    "WHERE subtype IS NOT NULL AND subtype != '' "
                    "GROUP BY subtype ORDER BY 2 DESC"
                )
                params = ()
            rows = conn.execute(sql, params).fetchall()
        finally:
            conn.close()
        return JSONResponse({"subtypes": [{"name": r[0], "count": r[1]} for r in rows]})

    @app.post("/api/aggregate/topic")
    async def api_aggregate_topic(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        tag = body.get("tag")
        if not isinstance(tag, str) or not tag.strip():
            raise HTTPException(status_code=400, detail="missing or empty 'tag'")
        k_raw = body.get("k", 5)
        if not isinstance(k_raw, int) or k_raw <= 0:
            raise HTTPException(status_code=400, detail="'k' must be a positive integer")
        k_raw = min(k_raw, 50)
        filter_obj = _build_filter(body.get("filter"), demo_src=state["demo_source"])
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
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")
        q = body.get("q")
        if not isinstance(q, str) or not q.strip():
            raise HTTPException(status_code=400, detail="missing or empty 'q'")
        k_raw = body.get("k", 5)
        if not isinstance(k_raw, int) or k_raw <= 0:
            raise HTTPException(status_code=400, detail="'k' must be a positive integer")
        k_raw = min(k_raw, 50)
        axis = body.get("axis")
        if axis is not None and not isinstance(axis, str):
            raise HTTPException(status_code=400, detail="'axis' must be a string or null")
        filter_obj = _build_filter(body.get("filter"), demo_src=state["demo_source"])
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
        except ValueError:
            raise HTTPException(status_code=400, detail="invalid JSON body") from None
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
        top_sources = min(top_sources, 20)
        per_source = min(per_source, 10)
        filter_obj = _build_filter(body.get("filter"), demo_src=state["demo_source"])
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
        payload["consensus_html"] = [_render_narrative(c) for c in (result.consensus or [])]
        payload["disagreements_html"] = [_render_narrative(d) for d in (result.disagreements or [])]
        return JSONResponse(payload)

    @app.get("/tags", response_class=HTMLResponse)
    def tags_index(request: Request):
        browser = _get_tag_browser()
        tags = browser.list_tags()
        return templates.TemplateResponse(
            "tags_index.html",
            {"request": request, "tags": tags, "title": "所有標籤"}
        )

    @app.get("/tag/{name}", response_class=HTMLResponse)
    def tag_detail(request: Request, name: str):
        browser = _get_tag_browser()
        detail = browser.get_tag_detail(name)
        if not detail:
            raise HTTPException(status_code=404, detail="Tag not found")
        related = browser.get_related_tags(name)
        return templates.TemplateResponse(
            "tag_detail.html",
            {"request": request, "tag": detail, "related_tags": related}
        )

    @app.get("/api/tags")
    def api_tags():
        browser = _get_tag_browser()
        return {"tags": [{"name": t[0], "count": t[1]} for t in browser.list_tags()]}

    @app.get("/", response_class=HTMLResponse)
    def index(
        request: Request,
        q: str = "",
        axis: str | None = None,
        tag: str | None = None,
        source_type: str | None = None,
        year: str | None = None,
        limit: int = Query(default=50, ge=1, le=200),
    ) -> HTMLResponse:
        results: list[dict[str, Any]] = []
        candidate_ids: set[str] | None = None
        error_message: str | None = None
        # Demo mode: pre-fill source_type if user is actively searching/browsing
        demo_src = state["demo_source"]
        if demo_src and not source_type and (q or tag or year):
            source_type = demo_src
        # Browse-by-facet: tag/source_type/year without a query lists matching
        # articles directly from articles_meta (newest first), so users can
        # explore a tag in isolation from the article-page tag chips.
        if not q and (tag or source_type or year):
            browse_conn = _get_conn()
            try:
                f_kwargs = {"tag": tag, "source_type": source_type}
                if year:
                    f_kwargs["pub_year"] = int(year)
                f = Filter(**f_kwargs)
                clause, params = f.to_sql_where(table_alias="m")
                sql = (
                    "SELECT m.article_id, m.title, m.source_type, m.category, m.pub_date "
                    "FROM articles_meta m "
                    "WHERE IFNULL(m.category, '') != 'excluded'"
                    + (f" AND {clause}" if clause else "")
                    + " ORDER BY COALESCE(m.pub_date, '') DESC LIMIT ?"
                )
                params.append(limit)
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
                    if tag or source_type or year:
                        filter_conn = _get_conn()
                        try:
                            results = _post_filter_results(
                                filter_conn, results, tag=tag, source_type=source_type, year=year
                            )
                        finally:
                            filter_conn.close()
                    results = results[:10]
                except WenjiError as exc:
                    error_message = f"搜尋失敗：{exc}"

        # axes for filter sidebar
        try:
            conn = _get_conn()
            if demo_src:
                sql = (
                    "SELECT a.axis_id, COUNT(*) FROM article_axes a "
                    "JOIN articles_meta m ON a.article_id = m.article_id "
                    "WHERE a.axis_id != ? AND m.source_type = ? "
                    "GROUP BY a.axis_id ORDER BY 2 DESC"
                )
                params = (UNCLASSIFIED, demo_src)
            else:
                sql = (
                    "SELECT axis_id, COUNT(*) FROM article_axes "
                    "WHERE axis_id != ? GROUP BY axis_id ORDER BY 2 DESC"
                )
                params = (UNCLASSIFIED,)
            axis_rows = conn.execute(sql, params).fetchall()
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
                if demo_src:
                    facets["source_types"] = [
                        s for s in facets["source_types"] if s["name"] == demo_src
                    ]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            facets = {"tags": [], "source_types": [], "query_aware": False}

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "q": q,
                "axis": axis,
                "tag": tag,
                "source_type": source_type,
                "year": year,
                "topic_shortcuts": TOPIC_SHORTCUTS,
                "results": results,
                "axes": axes,
                "facets": facets,
                "error": error_message,
            },
        )

    @app.get("/article/{article_id}", response_model=None)
    @app.get("/article/{article_id}.md", response_model=None)
    def article(
        request: Request,
        article_id: str,
        q: str = "",
    ) -> HTMLResponse | PlainTextResponse:
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

        # AEO: Markdown Content Negotiation
        accept_header = request.headers.get("Accept", "")
        if "text/markdown" in accept_header or request.url.path.endswith(".md"):
            md_content = f"# {meta[1]}\n\n"
            md_content += f"- **Date**: {meta[4]}\n"
            md_content += f"- **Author**: {meta[3]}\n"
            md_content += f"- **Source**: {meta[2]}\n\n"
            md_content += content
            return PlainTextResponse(md_content, media_type="text/markdown")

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
            "content_html": _render_chunk(content or "", q),
            "chunks": chunks,
            "matched_indexes": sorted(matched_indexes),
            "query": q,
        }
        return templates.TemplateResponse(request, "article.html", {"article": article_data})

    return app


# Module-level app for `uvicorn wenji.web.app:app`
app = create_app()
