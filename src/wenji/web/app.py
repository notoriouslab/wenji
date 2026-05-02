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
from wenji.classify.axes_loader import UNCLASSIFIED
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


def create_app(
    *,
    db_path: str | Path | None = None,
    searcher: Searcher | None = None,
    llm_client: LLMClient | None = None,
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
    }

    def _get_conn() -> sqlite3.Connection:
        return connect(state["db_path"])

    def _get_searcher() -> Searcher | None:
        """Lazy-construct Searcher; return None if model files missing (degraded mode)."""
        if state["searcher"] is not None:
            return state["searcher"]
        try:
            from wenji.ingest.embed import Embedder

            conn = _get_conn()
            state["searcher"] = Searcher(conn, Embedder())
            return state["searcher"]
        except (ConfigError, WenjiError):
            return None

    def _get_aggregator() -> Aggregator:
        """Construct an Aggregator with a fresh DB connection + the configured LLM client."""
        return Aggregator(_get_conn(), llm_client=state["llm_client"])

    def _build_filter(filter_dict: dict | None) -> Filter | None:
        if not filter_dict:
            return None
        try:
            return Filter(**filter_dict)
        except TypeError as exc:
            raise HTTPException(status_code=400, detail=f"invalid filter: {exc}") from exc

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
        return {"axes": [{"id": r[0], "count": r[1]} for r in rows]}

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
            results = s.search(q, axis=axis, limit=limit)
            return JSONResponse({"results": results, "query": q})
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
    def index(request: Request, q: str = "", axis: str | None = None) -> HTMLResponse:
        results: list[dict[str, Any]] = []
        error_message: str | None = None
        if q:
            s = _get_searcher()
            if s is None:
                error_message = (
                    "搜尋引擎啟動中（embedder 模型檔尚未就緒）。"
                    "請執行 `wenji download-model` 或設定 WENJI_MODEL_DIR 後重試。"
                )
            else:
                try:
                    results = s.search(q, axis=axis, limit=10)
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
            axes = [{"id": r[0], "count": r[1]} for r in axis_rows]
        except sqlite3.OperationalError:
            axes = []

        return templates.TemplateResponse(
            request,
            "index.html",
            {
                "query": q,
                "axis": axis,
                "results": results,
                "axes": axes,
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

        article_data = {
            "article_id": meta[0],
            "title": meta[1],
            "source_type": meta[2],
            "author": meta[3],
            "pub_date": meta[4],
            "category": meta[5],
            "tags": meta[6],
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
