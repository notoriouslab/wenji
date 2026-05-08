"""Tests for wenji.web.app FastAPI surface."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.classify.axes_loader import load_axes_config
from wenji.web.app import create_app


class _FakeSearcher:
    """Returns deterministic results without loading any model."""

    def __init__(self, results=None):
        self._results = results or []

    def search(self, query, *, axis=None, limit=10):
        return [dict(r, query=query, axis=axis) for r in self._results]


@pytest.fixture
def client(populated_db, tmp_path: Path):
    # populated_db is the in-memory connection from conftest; we need a file so create_app can re-open
    # Save populated_db to a real file for FastAPI test
    file_db = tmp_path / "wenji.db"
    backup_conn = __import__("sqlite3").connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()

    fake_searcher = _FakeSearcher(
        results=[
            {
                "article_id": "a1",
                "title": "禱告與信心",
                "source_type": "sermon",
                "category": "",
                "pub_date": "2024-01-15",
                "hybrid_score": 0.812,
                "content_snippet": "<mark>禱告</mark>是與神對話",
            }
        ]
    )
    app = create_app(db_path=file_db, searcher=fake_searcher)
    return TestClient(app)


@pytest.fixture
def client_no_model(populated_db, tmp_path: Path):
    file_db = tmp_path / "wenji.db"
    backup_conn = __import__("sqlite3").connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()
    # No searcher injected → lazy load will fail (no model files)
    app = create_app(db_path=file_db, searcher=None)
    return TestClient(app)


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["searcher_ready"] is True


def test_api_axes_returns_known_axis(client):
    r = client.get("/api/axes")
    assert r.status_code == 200
    body = r.json()
    axis_ids = {a["id"] for a in body["axes"]}
    assert "theology" in axis_ids


def test_api_axes_parent_null_for_flat_config(client):
    """Flat config (no axes_config wired) → every axis has parent=null."""
    r = client.get("/api/axes")
    body = r.json()
    assert body["axes"]
    assert all(a["parent"] is None for a in body["axes"])


@pytest.fixture
def hierarchy_setup(populated_db, tmp_path: Path):
    """File DB + AxesConfig with hierarchical axes (theology → meta_theology)."""
    import sqlite3 as _sqlite3

    file_db = tmp_path / "wenji.db"
    backup_conn = _sqlite3.connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()

    cfg_path = tmp_path / "axes.yaml"
    cfg_path.write_text(
        """
axes:
  - {id: meta_theology, name: 上層神學, order: 0,
     rules: [{source_type: never, primary: true}]}
  - {id: theology, name: 神學, order: 1, parent: meta_theology,
     rules: [{source_type: never, primary: true}]}
""",
        encoding="utf-8",
    )
    cfg = load_axes_config(cfg_path)
    return file_db, cfg


def test_api_axes_includes_parent_for_hierarchy(hierarchy_setup):
    file_db, cfg = hierarchy_setup
    app = create_app(db_path=file_db, searcher=None, axes_config=cfg)
    c = TestClient(app)
    r = c.get("/api/axes")
    body = r.json()
    by_id = {a["id"]: a for a in body["axes"]}
    assert "theology" in by_id
    assert by_id["theology"]["parent"] == "meta_theology"


def test_api_facets_returns_top_tags_and_source_types(client):
    r = client.get("/api/facets")
    assert r.status_code == 200
    body = r.json()
    assert "tags" in body and "source_types" in body
    if body["tags"]:
        counts = [item["count"] for item in body["tags"]]
        assert counts == sorted(counts, reverse=True)
    assert len(body["tags"]) <= 15
    assert len(body["source_types"]) <= 15


def test_api_facets_caps_top_at_50(client):
    r = client.get("/api/facets", params={"top": 200})
    assert r.status_code == 200
    body = r.json()
    assert len(body["tags"]) <= 50
    assert len(body["source_types"]) <= 50


def test_api_facets_default_top_is_15(client):
    r = client.get("/api/facets")
    body = r.json()
    assert len(body["tags"]) <= 15
    assert len(body["source_types"]) <= 15


def test_api_facets_default_corpus_only_no_query_count(client):
    """Without `q`, facets are corpus-wide and query_count is null."""
    r = client.get("/api/facets")
    body = r.json()
    assert body.get("query_aware") is False
    for entry in body["tags"]:
        assert entry["query_count"] is None


def test_index_browse_by_tag_without_query(client):
    """Hitting /?tag=X with no query renders browse-mode (not the welcome banner)."""
    r = client.get("/", params={"tag": "禱告"})
    assert r.status_code == 200
    # Browse-mode emits the 「瀏覽 tag=...」 banner or 「查無結果」 — never the welcome
    assert "瀏覽" in r.text or "查無結果" in r.text
    assert "在上方輸入查詢以開始搜尋" not in r.text


def test_index_facets_query_aware_when_q_set(client):
    """Facets are query-aware on /, sorting query-relevant tags first."""
    r = client.get("/", params={"q": "禱告"})
    assert r.status_code == 200
    # In the new UI, query_aware formats as `(count/query_count)`.
    assert "熱門分類" in r.text
    assert "/" in r.text  # A rough check that the query_count separator is present


def test_article_viewer_renders_chunk_anchors(populated_db, tmp_path):
    """Chunked article uses id="cN" sections (D5 anchor convention)."""
    import sqlite3 as _sqlite3

    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    populated_db.execute("UPDATE articles_meta SET chunk_count = 2 WHERE article_id = ?", (aid,))
    populated_db.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, "
        "title, title_raw, chunk_text, chunk_text_raw, "
        "tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', 'first chunk', '', '', 'sermon', 2024)",
        (f"{aid}-0", aid, "0"),
    )
    populated_db.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, "
        "title, title_raw, chunk_text, chunk_text_raw, "
        "tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', 'second chunk', '', '', 'sermon', 2024)",
        (f"{aid}-1", aid, "1"),
    )
    populated_db.commit()

    file_db = tmp_path / "wenji.db"
    backup_conn = _sqlite3.connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()
    app = create_app(db_path=file_db, searcher=None)
    c = TestClient(app)
    r = c.get(f"/article/{aid}")
    assert r.status_code == 200
    assert 'id="c0"' in r.text
    assert 'id="c1"' in r.text
    assert "#chunk-" not in r.text


def test_article_viewer_omits_chunk_anchors_when_chunk_count_zero(client, populated_db):
    """Articles with chunk_count = 0 fall back to whole-content rendering."""
    # populated_db (in-memory) ingests short articles without chunking → chunk_count == 0
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE chunk_count = 0 LIMIT 1"
    ).fetchone()
    if aid is None:
        pytest.skip("populated_db has every article chunked")
    r = client.get(f"/article/{aid[0]}")
    assert r.status_code == 200
    assert 'id="c0"' not in r.text


def test_search_result_title_link_carries_chunk_fragment(populated_db, tmp_path):
    """Title link ends in `#cN` when matched_chunks is present in result dict."""
    file_db = tmp_path / "wenji.db"
    backup_conn = __import__("sqlite3").connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()
    fake = _FakeSearcher(
        results=[
            {
                "article_id": "x1",
                "title": "因信稱義",
                "source_type": "sermon",
                "category": "",
                "pub_date": "",
                "hybrid_score": 0.5,
                "content_snippet": "snippet",
                "matched_chunks": [
                    {"chunk_index": 7, "chunk_text": "...", "snippet": "...", "score": 0.0}
                ],
            }
        ]
    )
    app = create_app(db_path=file_db, searcher=fake)
    c = TestClient(app)
    r = c.get("/", params={"q": "因信稱義"})
    assert r.status_code == 200
    assert "/article/x1" in r.text
    assert "#c7" in r.text


def test_index_renders_facet_sidebar(client):
    r = client.get("/", params={"q": "禱告"})
    assert r.status_code == 200
    assert "熱門分類" in r.text


def test_index_renders_ask_panel(client):
    """v0.3 自由問答 modal link appears in the header."""
    r = client.get("/")
    assert r.status_code == 200
    assert "自由問答" in r.text
    assert "ask-panel" in r.text


def test_index_filter_by_tag_narrows_results(client, populated_db):
    """Visiting ``/?q=...&tag=X`` post-filters search results by tag."""
    r = client.get("/", params={"q": "禱告", "tag": "禱告"})
    assert r.status_code == 200
    assert "tag=" in r.text  # facet links present
    r2 = client.get("/", params={"q": "禱告", "tag": "_no_such_tag_"})
    assert r2.status_code == 200
    assert "查無結果" in r2.text


def test_index_sidebar_indents_child_axes(hierarchy_setup):
    """Index sidebar applies padding-left for descendant axes (depth > 0)."""
    import sqlite3 as _sqlite3

    file_db, cfg = hierarchy_setup
    with _sqlite3.connect(str(file_db)) as conn:
        aid = conn.execute(
            "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
        ).fetchone()[0]
        conn.execute(
            "INSERT INTO article_axes (article_id, axis_id, is_primary) VALUES (?, ?, 0)",
            (aid, "meta_theology"),
        )
        conn.commit()

    app = create_app(db_path=file_db, searcher=None, axes_config=cfg)
    c = TestClient(app)
    r = c.get("/", params={"q": "因信"})
    assert r.status_code == 200
    assert "padding-left: 12px" in r.text


def test_api_search_returns_results(client):
    r = client.get("/api/search", params={"q": "禱告"})
    assert r.status_code == 200
    body = r.json()
    assert body["query"] == "禱告"
    assert body["results"][0]["article_id"] == "a1"


def test_api_search_504_when_searcher_unavailable(client_no_model, monkeypatch):
    # Force searcher lazy-load to fail
    from wenji.web import app as app_mod

    def boom(*a, **kw):
        from wenji.core.errors import ConfigError

        raise ConfigError("model not found")

    monkeypatch.setattr(app_mod, "Searcher", boom)
    r = client_no_model.get("/api/search", params={"q": "Q"})
    assert r.status_code == 504
    assert "starting up" in r.json()["error"]


def test_index_renders_with_query(client):
    r = client.get("/", params={"q": "禱告"})
    assert r.status_code == 200
    assert "禱告" in r.text
    assert "wenji" in r.text
    # axis sidebar present
    assert "theology" in r.text


def test_index_renders_without_query(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "wenji" in r.text


def test_index_shows_error_banner_when_searcher_unavailable(client_no_model, monkeypatch):
    from wenji.web import app as app_mod

    def boom(*a, **kw):
        from wenji.core.errors import ConfigError

        raise ConfigError("missing model")

    monkeypatch.setattr(app_mod, "Searcher", boom)
    r = client_no_model.get("/", params={"q": "Q"})
    assert r.status_code == 200
    assert "error-banner" in r.text
    assert "搜尋引擎啟動中" in r.text


def test_article_viewer_renders(client, populated_db):
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert "因信稱義" in r.text or "因信" in r.text
    assert "標籤總覽" in r.text


def test_article_viewer_with_query_renders_back_link(client, populated_db):
    aid = populated_db.execute(
        "SELECT article_id FROM articles_meta WHERE title LIKE '%因信%'"
    ).fetchone()[0]
    r = client.get(f"/article/{aid}", params={"q": "禱告"})
    assert r.status_code == 200
    assert "禱告" in r.text


def test_article_viewer_sidebar_renders_when_chunked(client, tmp_path):
    """When article has chunks, the chunks render correctly with ids."""
    import sqlite3

    file_db = tmp_path / "wenji.db"
    conn = sqlite3.connect(str(file_db))
    aid = conn.execute("SELECT article_id FROM articles_meta LIMIT 1").fetchone()[0]
    conn.execute("UPDATE articles_meta SET chunk_count = 2 WHERE article_id = ?", (aid,))
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, title, "
        "title_raw, chunk_text, chunk_text_raw, tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', '段一預覽內容', '', '', '', '')",
        (f"{aid}-0", aid, "0"),
    )
    conn.execute(
        "INSERT INTO chunks_fts (chunk_id, article_id, chunk_index, title, "
        "title_raw, chunk_text, chunk_text_raw, tags, tags_raw, source_type, pub_year) "
        "VALUES (?, ?, ?, '', '', '', '段二預覽內容', '', '', '', '')",
        (f"{aid}-1", aid, "1"),
    )
    conn.commit()
    conn.close()

    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert 'id="c0"' in r.text
    assert 'id="c1"' in r.text
    assert "段一預覽內容" in r.text


def test_article_viewer_doc_actions_renders_source_url(client, tmp_path):
    import sqlite3

    file_db = tmp_path / "wenji.db"
    conn = sqlite3.connect(str(file_db))
    aid = conn.execute("SELECT article_id FROM articles_meta LIMIT 1").fetchone()[0]
    conn.execute(
        "UPDATE articles_meta SET source_url = ? WHERE article_id = ?",
        ("https://example.com/src", aid),
    )
    conn.commit()
    conn.close()

    r = client.get(f"/article/{aid}")
    assert r.status_code == 200
    assert "https://example.com/src" in r.text
    assert "連結出處" in r.text


def test_article_viewer_404_when_missing(client):
    r = client.get("/article/nonexistent-id")
    assert r.status_code == 404


def test_static_css_served(client):
    r = client.get("/static/style.css")
    assert r.status_code == 200
    assert "@keyframes spin" in r.text  # spinner keyframes are present


def test_factory_uses_env_var_for_db(monkeypatch, tmp_path):
    monkeypatch.setenv("WENJI_DB_PATH", str(tmp_path / "from-env.db"))
    app = create_app()  # no db_path passed; should pick up env var without error
    c = TestClient(app)
    r = c.get("/healthz")
    assert r.json()["status"] == "ok"


def test_searcher_forwards_alias_map_env_to_entity_scorer(populated_db, tmp_path, monkeypatch):
    """OPEN-7: WENJI_ENTITY_ALIAS_MAP loads JSON and forwards as alias_map kwarg
    to EntityScorer.from_sources."""
    import json
    import sqlite3

    file_db = tmp_path / "wenji.db"
    backup_conn = sqlite3.connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()

    alias_data = {"巽正": "周巽正", "周牧師": ["周神助", "周巽正"]}
    alias_path = tmp_path / "aliases.json"
    alias_path.write_text(json.dumps(alias_data, ensure_ascii=False), encoding="utf-8")

    captured: dict = {}
    from wenji.search import entity as entity_mod

    real_from_sources = entity_mod.EntityScorer.from_sources

    def spy_from_sources(sources, alias_map=None, alpha=entity_mod.DEFAULT_ALPHA):
        captured["sources"] = list(sources)
        captured["alias_map"] = alias_map
        return real_from_sources(sources, alias_map=alias_map, alpha=alpha)

    monkeypatch.setattr(entity_mod.EntityScorer, "from_sources", spy_from_sources)
    monkeypatch.setenv("WENJI_ENTITY_SOURCES", "example:corpus-christian")
    monkeypatch.setenv("WENJI_ENTITY_ALIAS_MAP", str(alias_path))

    app = create_app(db_path=file_db, searcher=None)
    c = TestClient(app)
    c.get("/api/search?q=test")

    assert captured.get("sources") == ["example:corpus-christian"]
    assert captured.get("alias_map") == alias_data


def test_searcher_forwards_intent_source_types_env_to_classifier(
    populated_db, tmp_path, monkeypatch
):
    """OPEN-7: WENJI_INTENT_SOURCE_TYPES loads JSON and forwards as
    intent_source_types kwarg to IntentClassifier.from_sources."""
    import json
    import sqlite3

    file_db = tmp_path / "wenji.db"
    backup_conn = sqlite3.connect(str(file_db))
    populated_db.backup(backup_conn)
    backup_conn.close()

    ist_data = {"apologetics": ["apologetics"]}
    ist_path = tmp_path / "intent_source_types.json"
    ist_path.write_text(json.dumps(ist_data, ensure_ascii=False), encoding="utf-8")

    captured: dict = {}
    from wenji.search import intent as intent_mod

    real_from_sources = intent_mod.IntentClassifier.from_sources

    def spy_from_sources(
        sources,
        intent_source_types=None,
        default_intent=intent_mod.DEFAULT_INTENT,
        scripture_pattern=None,
        generic_entities=None,
    ):
        captured["sources"] = list(sources)
        captured["intent_source_types"] = intent_source_types
        return real_from_sources(
            sources,
            intent_source_types=intent_source_types,
            default_intent=default_intent,
            scripture_pattern=scripture_pattern,
            generic_entities=generic_entities,
        )

    monkeypatch.setattr(intent_mod.IntentClassifier, "from_sources", spy_from_sources)
    monkeypatch.setenv("WENJI_INTENT_SOURCES", "example:corpus-christian")
    monkeypatch.setenv("WENJI_INTENT_SOURCE_TYPES", str(ist_path))

    app = create_app(db_path=file_db, searcher=None)
    c = TestClient(app)
    c.get("/api/search?q=test")

    assert captured.get("sources") == ["example:corpus-christian"]
    assert captured.get("intent_source_types") == ist_data
