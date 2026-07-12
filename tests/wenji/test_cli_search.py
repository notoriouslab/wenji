"""Tests for wenji search CLI (thin-client fallback logic)."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from wenji.cli import app
from wenji.cli import search as search_cli

runner = CliRunner()


class _FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                "boom",
                request=httpx.Request("GET", "http://x"),
                response=httpx.Response(self.status_code),
            )

    def json(self):
        return self._payload


def test_try_server_returns_payload_on_200(monkeypatch):
    captured = {}

    def fake_get(url, params=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["timeout"] = timeout
        return _FakeResponse({"results": [{"article_id": "a1", "title": "T"}]})

    import httpx

    monkeypatch.setattr(httpx, "get", fake_get)

    out = search_cli._try_server("http://localhost:8000", "因信稱義", None, 5)
    assert out is not None
    assert out["results"][0]["article_id"] == "a1"
    assert "/api/search" in captured["url"]
    assert captured["timeout"] == search_cli.SERVER_PROBE_TIMEOUT


def test_try_server_returns_none_on_connect_error(monkeypatch):
    import httpx

    def fake_get(*a, **kw):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert search_cli._try_server("http://localhost:9999", "Q", None, 5) is None


def test_try_server_returns_none_on_timeout(monkeypatch):
    import httpx

    def fake_get(*a, **kw):
        raise httpx.TimeoutException("slow")

    monkeypatch.setattr(httpx, "get", fake_get)
    assert search_cli._try_server("http://localhost:8000", "Q", None, 5) is None


def test_search_uses_server_when_available(monkeypatch, tmp_path: Path):
    payload = {"results": [{"article_id": "a1", "title": "TT", "hybrid_score": 0.9}]}

    def fake_try(server, query, axis, limit):
        assert query == "禱告"
        return payload

    monkeypatch.setattr(search_cli, "_try_server", fake_try)
    result = runner.invoke(app, ["search", "禱告", "--json"])
    assert result.exit_code == 0
    parsed = json.loads(result.stdout)
    assert parsed["results"][0]["article_id"] == "a1"


def test_search_falls_back_to_in_process_when_server_unavailable(monkeypatch):
    monkeypatch.setattr(search_cli, "_try_server", lambda *a, **kw: None)

    fake_payload = {
        "results": [{"article_id": "a-fallback", "title": "InProc", "hybrid_score": 0.5}],
        "query": "Q",
    }
    monkeypatch.setattr(
        search_cli, "_in_process_search", lambda db, query, axis, limit, search_cfg, **kw: fake_payload
    )

    result = runner.invoke(app, ["search", "Q", "--json"])
    assert result.exit_code == 0
    assert "in-process" in result.stderr
    parsed = json.loads(result.stdout)
    assert parsed["results"][0]["article_id"] == "a-fallback"


def test_search_human_readable_output(monkeypatch):
    monkeypatch.setattr(
        search_cli,
        "_try_server",
        lambda *a, **kw: {
            "results": [
                {
                    "article_id": "a1",
                    "title": "Hello World",
                    "source_type": "sermon",
                    "hybrid_score": 0.812,
                    "content_snippet": "this is the snippet",
                }
            ]
        },
    )
    result = runner.invoke(app, ["search", "test"])
    assert result.exit_code == 0
    assert "Hello World" in result.stdout
    assert "0.812" in result.stdout
    assert "this is the snippet" in result.stdout


def test_search_axis_param_propagated(monkeypatch):
    captured = {}

    def fake_try(server, query, axis, limit):
        captured["axis"] = axis
        captured["limit"] = limit
        return {"results": []}

    monkeypatch.setattr(search_cli, "_try_server", fake_try)
    result = runner.invoke(app, ["search", "Q", "--axis", "theology", "--limit", "3"])
    assert result.exit_code == 0
    assert captured["axis"] == "theology"
    assert captured["limit"] == 3
