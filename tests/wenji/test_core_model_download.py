"""Tests for wenji.core.model_download (mocked huggingface_hub)."""

from __future__ import annotations

import pytest

from wenji.core import model_download
from wenji.core.errors import WenjiError


def test_already_downloaded_skips_network(monkeypatch, tmp_path):
    target = tmp_path / "embed"
    target.mkdir()
    (target / "tokenizer.json").write_text("{}", encoding="utf-8")
    (target / "model.onnx").write_bytes(b"")

    called = {"count": 0}

    def fake_snapshot(*a, **kw):
        called["count"] += 1
        return str(target)

    monkeypatch.setattr(model_download, "snapshot_download", fake_snapshot)
    out = model_download.download_embed_model(target_dir=target)
    assert out == target
    assert called["count"] == 0  # network not called


def test_download_creates_target_dir_and_calls_snapshot(monkeypatch, tmp_path):
    target = tmp_path / "embed"
    captured = {}

    def fake_snapshot(repo_id, revision=None, local_dir=None, allow_patterns=None):
        captured["repo_id"] = repo_id
        captured["local_dir"] = local_dir
        captured["allow_patterns"] = allow_patterns
        # simulate the download placing required files
        local = type(target)(local_dir)
        local.mkdir(parents=True, exist_ok=True)
        (local / "tokenizer.json").write_text("{}", encoding="utf-8")
        (local / "onnx").mkdir(exist_ok=True)
        (local / "onnx" / "model_quantized.onnx").write_bytes(b"\x00\x01")
        return local_dir

    monkeypatch.setattr(model_download, "snapshot_download", fake_snapshot)
    out = model_download.download_embed_model(target_dir=target)
    assert out == target
    assert (target / "tokenizer.json").exists()
    assert (target / "model.onnx").exists()  # nested onnx symlink/copy
    assert captured["repo_id"] == model_download.EMBED_MODEL_DEFAULT


def test_download_propagates_hf_error(monkeypatch, tmp_path):
    from huggingface_hub.errors import HfHubHTTPError

    class _FakeHFError(HfHubHTTPError):
        def __init__(self, msg):
            Exception.__init__(self, msg)

    def fake_snapshot(*a, **kw):
        raise _FakeHFError("404 not found")

    monkeypatch.setattr(model_download, "snapshot_download", fake_snapshot)
    with pytest.raises(WenjiError, match="failed to download"):
        model_download.download_embed_model(target_dir=tmp_path / "embed")


def test_concurrent_lock_blocks_second_download(monkeypatch, tmp_path):
    target = tmp_path / "embed"
    target.mkdir()
    lock = target / ".lock"
    lock.write_text("locked", encoding="utf-8")

    def fake_snapshot(*a, **kw):
        return str(target)

    monkeypatch.setattr(model_download, "snapshot_download", fake_snapshot)
    with pytest.raises(WenjiError, match="another download is in progress"):
        model_download.download_embed_model(target_dir=target)
