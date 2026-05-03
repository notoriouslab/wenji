"""Tests for wenji eval run-benchmark CLI."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from typer.testing import CliRunner

from wenji.cli import app

runner = CliRunner()
REPO_ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT = REPO_ROOT / "tests" / "benchmark_80_v2_snapshot.json"


class _FakeClient:
    """Mock httpx.Client that returns a canned response for every request."""

    def __init__(self, response_payload):
        self.response_payload = response_payload
        self.requests = []

    def get(self, url, params=None, **kw):
        self.requests.append((url, params))
        return _FakeResp(self.response_payload)

    def close(self):
        pass


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_run_benchmark_produces_v2_schema_output(tmp_path):
    out = tmp_path / "wenji_r0.json"
    fake_payload = {
        "results": [
            {
                "article_id": "x",
                "title": "T",
                "rank": 1,
                "score": 0.9,
                "content_full": "kw kw kw",
            }
        ]
    }

    def fake_client_factory(*args, **kwargs):
        return _FakeClient(fake_payload)

    with patch("httpx.Client", side_effect=fake_client_factory):
        result = runner.invoke(
            app,
            [
                "eval",
                "run-benchmark",
                "--snapshot",
                str(SNAPSHOT),
                "--db",
                str(tmp_path / "noop.db"),
                "--out",
                str(out),
            ],
        )
    # exit code 0/1 acceptable; we mainly check the schema below
    assert out.exists(), f"run output not written; stdout={result.stdout!r} stderr={result.stderr!r}"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["schema_version"] == "v2"
    assert data["pipeline_mode"] == "rag_full"
    assert "logos_source_commit" in data
    assert data["top_k_requested"] == 20
    assert len(data["questions"]) == 80
    assert "summary" in data
    assert "pass_rate_pct" in data["summary"]


def test_run_benchmark_writes_summary_digest(tmp_path):
    out = tmp_path / "wenji_r0.json"
    fake_payload = {"results": []}

    def fake_client_factory(*args, **kwargs):
        return _FakeClient(fake_payload)

    with patch("httpx.Client", side_effect=fake_client_factory):
        runner.invoke(
            app,
            [
                "eval",
                "run-benchmark",
                "--snapshot",
                str(SNAPSHOT),
                "--db",
                str(tmp_path / "noop.db"),
                "--out",
                str(out),
            ],
        )
    digest = out.with_suffix(out.suffix + ".summary.json")
    assert digest.exists()
    summary = json.loads(digest.read_text(encoding="utf-8"))
    assert "pass_count" in summary
    assert "elapsed_total_sec" in summary


def test_run_benchmark_pass_rate_with_full_match(tmp_path):
    out = tmp_path / "wenji_r0.json"
    # Provide a payload that contains keywords for the FIRST gold path of q1.
    # q1 first path keywords include 宇宙論論證 / 設計論論證 / 道德論論證 / 自然神學 / 有神論
    payload = {
        "results": [
            {
                "article_id": "match",
                "title": "T",
                "rank": 1,
                "score": 0.99,
                "content_full": "宇宙論論證 設計論論證 道德論論證 自然神學 有神論",
            }
        ]
    }

    def fake_client_factory(*args, **kwargs):
        return _FakeClient(payload)

    with patch("httpx.Client", side_effect=fake_client_factory):
        runner.invoke(
            app,
            [
                "eval",
                "run-benchmark",
                "--snapshot",
                str(SNAPSHOT),
                "--db",
                str(tmp_path / "noop.db"),
                "--out",
                str(out),
            ],
        )
    data = json.loads(out.read_text(encoding="utf-8"))
    # q1 should pass via the first path
    q1 = next(q for q in data["questions"] if q["id"] == 1)
    assert q1["pass"] is True
    assert "classical_theistic_arguments" in q1["passing_paths"]
