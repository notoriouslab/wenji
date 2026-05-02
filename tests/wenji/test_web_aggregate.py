"""Tests for the /api/aggregate/{topic,concept} web endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from wenji.core.db import connect, initialise_schema
from wenji.ingest import ingest_dir
from wenji.web.app import create_app


class _MockLLMClient:
    def __init__(self, response: str = "narrative") -> None:
        self.response = response
        self.calls: list[list[dict]] = []

    def chat(self, messages: list[dict]) -> str:
        self.calls.append(messages)
        return self.response


@pytest.fixture
def web_db_path(tmp_path: Path, mock_embedder) -> Path:
    db_path = tmp_path / "wenji.db"
    sermons = tmp_path / "sermons"
    sermons.mkdir()
    (sermons / "prayer.md").write_text(
        "---\ntitle: 禱告生命\ntags: [禱告]\npubDate: 2024-03-10\n---\n"
        "禱告是與神親近的方式，是基督徒屬靈生命的呼吸。\n",
        encoding="utf-8",
    )
    (sermons / "weekly.md").write_text(
        "---\ntitle: 週報\ntags: [禱告, 公告]\nsubtype: weekly\npubDate: 2024-03-17\n---\n"
        "本週禱告會週三晚上七點。\n",
        encoding="utf-8",
    )
    classical = tmp_path / "classical"
    classical.mkdir()
    (classical / "calvin.md").write_text(
        "---\ntitle: 加爾文論因信稱義\ntags: [因信稱義]\npubDate: 1559-01-01\n---\n"
        "因信稱義乃宗教改革的核心教義。\n",
        encoding="utf-8",
    )
    (classical / "luther.md").write_text(
        "---\ntitle: 路德論因信稱義\ntags: [因信稱義]\npubDate: 1520-01-01\n---\n"
        "因信稱義是基督徒得救的唯一道路。\n",
        encoding="utf-8",
    )
    conn = connect(db_path)
    initialise_schema(conn)
    ingest_dir(
        tmp_path,
        conn,
        mock_embedder,
        directory_map={"sermons": "sermon", "classical": "classical"},
    )
    conn.close()
    return db_path


def _client(db_path: Path, llm_client=None) -> TestClient:
    app = create_app(db_path=db_path, llm_client=llm_client)
    return TestClient(app)


class TestAggregateTopicEndpoint:
    def test_returns_structured_payload_no_llm(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post("/api/aggregate/topic", json={"tag": "禱告", "k": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["tag"] == "禱告"
        assert data["narrative"] is None
        assert data["narrative_html"] is None
        assert isinstance(data["top_sources"], list)
        assert len(data["top_sources"]) >= 1

    def test_with_llm_returns_narrative_and_html(self, web_db_path: Path) -> None:
        client_llm = _MockLLMClient(response="**重點** 內容")
        with _client(web_db_path, llm_client=client_llm) as client:
            resp = client.post("/api/aggregate/topic", json={"tag": "禱告", "k": 5})
        assert resp.status_code == 200
        data = resp.json()
        assert data["narrative"] == "**重點** 內容"
        assert "<strong>" in data["narrative_html"]

    def test_filter_excludes_subtype(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post(
                "/api/aggregate/topic",
                json={"tag": "禱告", "k": 5, "filter": {"subtype__not_in": ["weekly"]}},
            )
        assert resp.status_code == 200
        titles = {s["title"] for s in resp.json()["top_sources"]}
        assert "週報" not in titles
        assert "禱告生命" in titles

    def test_missing_tag_returns_400(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post("/api/aggregate/topic", json={"k": 5})
        assert resp.status_code == 400

    def test_invalid_filter_field_returns_400(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post(
                "/api/aggregate/topic",
                json={"tag": "禱告", "filter": {"unknown_field": "x"}},
            )
        assert resp.status_code == 400

    def test_malformed_json_returns_400(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post(
                "/api/aggregate/topic",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
        assert resp.status_code == 400


class TestAggregateConceptEndpoint:
    def test_returns_structured_payload_no_llm(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post(
                "/api/aggregate/concept",
                json={"concept": "因信稱義", "top_sources": 2, "per_source": 2},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["concept"] == "因信稱義"
        assert data["narrative"] is None
        assert data["narrative_html"] is None
        assert data["consensus"] == []
        assert data["disagreements"] == []
        assert len(data["per_source_views"]) >= 1

    def test_missing_concept_returns_400(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.post("/api/aggregate/concept", json={"top_sources": 2})
        assert resp.status_code == 400

    def test_with_llm_populates_narrative_html(self, web_db_path: Path) -> None:
        client_llm = _MockLLMClient(
            response="## 共識\n- 救恩唯獨憑信\n## 分歧\n- 路德與加爾文的重心不同"
        )
        with _client(web_db_path, llm_client=client_llm) as client:
            resp = client.post(
                "/api/aggregate/concept",
                json={"concept": "因信稱義", "top_sources": 2, "per_source": 2},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert "救恩唯獨憑信" in data["consensus"]
        assert "<h2>" in data["narrative_html"]


class TestStatePassthrough:
    def test_get_aggregator_uses_injected_llm_client(self, web_db_path: Path) -> None:
        client_llm = _MockLLMClient(response="一次")
        with _client(web_db_path, llm_client=client_llm) as client:
            client.post("/api/aggregate/topic", json={"tag": "禱告", "k": 3})
        assert len(client_llm.calls) == 1


class TestSubtypesEndpoint:
    def test_lists_distinct_subtypes_with_counts(self, web_db_path: Path) -> None:
        with _client(web_db_path) as client:
            resp = client.get("/api/aggregate/subtypes")
        assert resp.status_code == 200
        data = resp.json()
        names = {s["name"] for s in data["subtypes"]}
        assert "weekly" in names
        # ordered by count descending
        counts = [s["count"] for s in data["subtypes"]]
        assert counts == sorted(counts, reverse=True)

    def test_empty_when_no_subtypes(self, tmp_path: Path, mock_embedder) -> None:
        from wenji.core.db import connect, initialise_schema

        db_path = tmp_path / "empty.db"
        articles_dir = tmp_path / "articles"
        articles_dir.mkdir()
        (articles_dir / "a.md").write_text(
            "---\ntitle: A\ntags: [x]\n---\nbody.\n", encoding="utf-8"
        )
        conn = connect(db_path)
        initialise_schema(conn)
        ingest_dir(tmp_path, conn, mock_embedder, directory_map={"articles": "article"})
        conn.close()
        with _client(db_path) as client:
            resp = client.get("/api/aggregate/subtypes")
        assert resp.status_code == 200
        assert resp.json()["subtypes"] == []
