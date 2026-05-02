"""Tests for wenji.search.rerank (interface only — real ONNX in Group 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wenji.core.errors import ConfigError, WenjiError
from wenji.search.rerank import DEFAULT_CACHE_DIR, CrossEncoderReranker


def test_default_disabled():
    r = CrossEncoderReranker()
    assert r.enabled is False


def test_default_cache_dir_under_home():
    assert DEFAULT_CACHE_DIR.is_relative_to(Path.home())


def test_score_no_op_when_disabled():
    r = CrossEncoderReranker(enabled=False)
    candidates = [{"article_id": "a"}, {"article_id": "b"}]
    out = r.score("query", candidates)
    assert out is candidates  # unchanged reference


def test_score_empty_candidates_returns_empty():
    r = CrossEncoderReranker(enabled=True, model_dir="/nowhere")
    assert r.score("query", []) == []


def test_score_enabled_missing_model_raises_config_error(tmp_path):
    r = CrossEncoderReranker(model_dir=tmp_path / "empty", enabled=True)
    with pytest.raises(ConfigError, match="reranker model files not found"):
        r.score("query", [{"article_id": "a"}])


def test_score_enabled_with_invalid_files_raises(tmp_path):
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"")
    r = CrossEncoderReranker(model_dir=tmp_path, enabled=True)
    with pytest.raises(WenjiError):
        r.score("query", [{"article_id": "a", "title": "T"}])


def test_score_with_mocked_runtime_reorders(tmp_path, monkeypatch):
    """Mock tokenizers + onnxruntime so we exercise the wire-up + sort."""
    import numpy as np

    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"")

    class _FakeEnc:
        def __init__(self, n: int):
            self.ids = list(range(n))
            self.attention_mask = [1] * n
            self.type_ids = [0] * n

    class _FakeTok:
        def enable_truncation(self, max_length):
            pass

        def enable_padding(self):
            pass

        def encode_batch(self, pairs):
            return [_FakeEnc(8) for _ in pairs]

    class _FakeInput:
        def __init__(self, name):
            self.name = name

    class _FakeSess:
        def get_inputs(self):
            return [_FakeInput("input_ids"), _FakeInput("attention_mask")]

        def run(self, _, feed):
            B = feed["input_ids"].shape[0]
            # Return logits in reverse order (1.0, 0.5) for B=2 → sort flips
            scores = np.linspace(0.1, 1.0, B, dtype=np.float32).reshape(-1, 1)
            return [scores]

    import onnxruntime
    import tokenizers

    monkeypatch.setattr(
        tokenizers, "Tokenizer", type("T", (), {"from_file": staticmethod(lambda p: _FakeTok())})
    )
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **kw: _FakeSess())

    r = CrossEncoderReranker(model_dir=tmp_path, enabled=True)
    candidates = [
        {"article_id": "a1", "title": "T1"},
        {"article_id": "a2", "title": "T2"},
    ]
    out = r.score("query", candidates)
    # higher score (a2) sorted first
    assert out[0]["article_id"] == "a2"
    assert out[0]["rerank_score"] > out[1]["rerank_score"]


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WENJI_RERANKER_DIR", str(tmp_path / "env-reranker"))
    r = CrossEncoderReranker()
    assert r.model_dir == tmp_path / "env-reranker"
