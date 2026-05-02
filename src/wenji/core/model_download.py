"""HuggingFace model auto-download with file lock + cache.

Uses ``huggingface_hub.snapshot_download`` (already in deps) for resumable
downloads + integrity checks. Output directory layout matches what
:class:`wenji.ingest.embed.Embedder` and
:class:`wenji.search.rerank.CrossEncoderReranker` expect:

- ``{cache_dir}/bge-m3-onnx-int8/{tokenizer.json, model.onnx, config.json}``
- ``{cache_dir}/qwen3-reranker/{tokenizer.json, model.onnx, config.json}``

A ``.lock`` file under each target dir prevents concurrent download races
(e.g. when CLI + serve start simultaneously).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path

from huggingface_hub import snapshot_download
from huggingface_hub.errors import HfHubHTTPError

from wenji.core.errors import WenjiError

DEFAULT_CACHE_ROOT = Path.home() / ".cache" / "wenji"

# Known model presets — can be overridden by passing repo_id explicitly
EMBED_MODEL_DEFAULT = "Xenova/bge-m3"  # community ONNX export of BAAI/bge-m3
RERANKER_MODEL_DEFAULT = "Xenova/bge-reranker-base"  # community ONNX cross-encoder


@contextmanager
def _file_lock(lock_path: Path):
    """Crude exclusive file lock — atomic O_CREAT|O_EXCL."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError as exc:
            raise WenjiError(
                f"another download is in progress (lock {lock_path}); "
                "delete the lock file if you're sure no other process is downloading."
            ) from exc
        yield
    finally:
        if fd is not None:
            os.close(fd)
            lock_path.unlink(missing_ok=True)


def _is_already_downloaded(target_dir: Path, required_files: tuple[str, ...]) -> bool:
    return all((target_dir / fn).exists() for fn in required_files)


def download_embed_model(
    target_dir: Path | None = None,
    *,
    repo_id: str = EMBED_MODEL_DEFAULT,
    revision: str | None = None,
    onnx_file: str = "onnx/model_quantized.onnx",
) -> Path:
    """Download an embedding ONNX model + tokenizer to ``target_dir``.

    Default: BGE-M3 community quantized ONNX. Returns the resolved target dir.
    Idempotent: if the required files already exist, no network calls.
    """
    target = Path(target_dir) if target_dir else DEFAULT_CACHE_ROOT / "bge-m3-onnx-int8"
    if _is_already_downloaded(target, ("tokenizer.json", "model.onnx")):
        return target

    target.mkdir(parents=True, exist_ok=True)
    with _file_lock(target / ".lock"):
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_dir=str(target),
                allow_patterns=[
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "config.json",
                    "special_tokens_map.json",
                    onnx_file,
                ],
            )
        except HfHubHTTPError as exc:
            raise WenjiError(f"failed to download {repo_id}: {exc}") from exc
        # Symlink/move the onnx file to the expected name if nested
        nested = target / onnx_file
        flat = target / "model.onnx"
        if nested.exists() and not flat.exists():
            try:
                flat.symlink_to(nested)
            except OSError:
                # filesystem doesn't support symlinks → copy
                import shutil

                shutil.copyfile(nested, flat)
    return target


def download_reranker_model(
    target_dir: Path | None = None,
    *,
    repo_id: str = RERANKER_MODEL_DEFAULT,
    revision: str | None = None,
    onnx_file: str = "onnx/model_quantized.onnx",
) -> Path:
    """Download a cross-encoder reranker ONNX model + tokenizer."""
    target = Path(target_dir) if target_dir else DEFAULT_CACHE_ROOT / "qwen3-reranker"
    if _is_already_downloaded(target, ("tokenizer.json", "model.onnx")):
        return target

    target.mkdir(parents=True, exist_ok=True)
    with _file_lock(target / ".lock"):
        try:
            snapshot_download(
                repo_id=repo_id,
                revision=revision,
                local_dir=str(target),
                allow_patterns=[
                    "tokenizer.json",
                    "tokenizer_config.json",
                    "config.json",
                    "special_tokens_map.json",
                    onnx_file,
                ],
            )
        except HfHubHTTPError as exc:
            raise WenjiError(f"failed to download {repo_id}: {exc}") from exc
        nested = target / onnx_file
        flat = target / "model.onnx"
        if nested.exists() and not flat.exists():
            try:
                flat.symlink_to(nested)
            except OSError:
                import shutil

                shutil.copyfile(nested, flat)
    return target
