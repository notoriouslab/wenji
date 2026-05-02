"""Tests for wenji.ingest.embed (interface only — real ONNX in Group 9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from wenji.core.errors import ConfigError, WenjiError
from wenji.ingest.embed import DEFAULT_CACHE_DIR, Embedder


def test_default_dim_is_1024():
    assert Embedder.DIM == 1024


def test_default_cache_dir_under_home():
    assert DEFAULT_CACHE_DIR.is_relative_to(Path.home())


def test_init_with_custom_model_dir(tmp_path):
    e = Embedder(model_dir=tmp_path / "fake-model")
    assert e.model_dir == tmp_path / "fake-model"


def test_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("WENJI_MODEL_DIR", str(tmp_path / "env-model"))
    e = Embedder()
    assert e.model_dir == tmp_path / "env-model"


def test_encode_batch_empty_returns_empty_array():
    e = Embedder(model_dir="/nowhere")
    arr = e.encode_batch([])
    assert arr.shape == (0, 1024)
    assert arr.dtype.name == "float32"


def test_encode_batch_missing_model_files_raises_config_error(tmp_path):
    e = Embedder(model_dir=tmp_path / "empty-dir")
    with pytest.raises(ConfigError, match="model files not found"):
        e.encode_batch(["text"])


def test_encode_batch_with_invalid_model_files_raises(tmp_path):
    """Files exist but contents are not a valid ONNX/tokenizer payload."""
    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"")
    e = Embedder(model_dir=tmp_path)
    with pytest.raises(WenjiError):
        e.encode_batch(["text"])


def test_encode_batch_full_inference_with_mocked_runtime(tmp_path, monkeypatch):
    """Mock onnxruntime + tokenizers to verify wire-up + L2 norm output."""
    import numpy as np

    (tmp_path / "tokenizer.json").write_text("{}", encoding="utf-8")
    (tmp_path / "model.onnx").write_bytes(b"")

    class _FakeEnc:
        def __init__(self, n: int):
            self.ids = list(range(n))
            self.attention_mask = [1] * n

    class _FakeTok:
        def enable_truncation(self, max_length):
            pass

        def enable_padding(self):
            pass

        def encode_batch(self, texts):
            return [_FakeEnc(8) for _ in texts]

    class _FakeInput:
        def __init__(self, name):
            self.name = name

    class _FakeSess:
        def get_inputs(self):
            return [_FakeInput("input_ids"), _FakeInput("attention_mask")]

        def run(self, _, feed):
            B = feed["input_ids"].shape[0]
            # return last_hidden_state shape (B, T=8, D=1024)
            return [np.ones((B, 8, 1024), dtype=np.float32) * 0.5]

    import onnxruntime
    import tokenizers

    monkeypatch.setattr(
        tokenizers, "Tokenizer", type("T", (), {"from_file": staticmethod(lambda p: _FakeTok())})
    )
    monkeypatch.setattr(onnxruntime, "InferenceSession", lambda *a, **kw: _FakeSess())

    e = Embedder(model_dir=tmp_path)
    out = e.encode_batch(["text1", "text2"])
    assert out.shape == (2, 1024)
    # L2-normalised
    norms = np.linalg.norm(out, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5)
