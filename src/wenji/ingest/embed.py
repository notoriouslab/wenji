"""BGE-M3 INT8 ONNX in-process embedder.

Loads tokenizer + ONNX session lazily on first encode. CLS-pooled output is
L2-normalised so cosine similarity = dot product. Deterministic on a single
CPU thread (intra/inter op = 1) — required for byte-identical rebuild.

.. warning::
   Calling ``encode_batch`` with more than one text is measured (2026-07-09,
   M2 + this INT8 model) to (a) give NO throughput benefit over one-at-a-time
   calls (0.97x — CPU INT8 inference is compute-bound; batching pays off on
   GPUs, not here) and (b) DRIFT the vectors: padding changes the quantized
   numeric path, cosine vs single-text encoding floors around 0.98. The
   ingest pipeline therefore always passes a single text. Do not batch
   without re-running the equivalence experiment (see the
   ingest-throughput-and-operability change's G4 record).

Real ONNX wiring lands here in Group 9. For tests, inject a duck-typed mock
exposing ``DIM`` + ``encode_batch(list[str]) -> ndarray``.
"""

from __future__ import annotations

import os
from pathlib import Path

import numpy as np

from wenji.core.errors import ConfigError, WenjiError

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "wenji" / "bge-m3-onnx-int8"


class Embedder:
    """In-process BGE-M3 ONNX embedder.

    Args:
        model_dir: Directory containing ``tokenizer.json`` + ``model.onnx``.
            Falls back to ``$WENJI_MODEL_DIR`` then :data:`DEFAULT_CACHE_DIR`.
        batch_size: Maximum texts per ONNX forward call.
        max_length: Tokenizer truncation length (BGE-M3 supports up to 8192,
            but 512 keeps inference fast for typical Chinese paragraphs).
    """

    DIM = 1024

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        batch_size: int = 32,
        max_length: int = 512,
    ) -> None:
        resolved = (
            Path(model_dir)
            if model_dir is not None
            else Path(os.environ.get("WENJI_MODEL_DIR", DEFAULT_CACHE_DIR))
        )
        self.model_dir: Path = resolved
        self.batch_size = batch_size
        self.max_length = max_length
        self._session = None
        self._tokenizer = None
        self._input_names: set[str] = set()

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        tokenizer_path = self.model_dir / "tokenizer.json"
        model_path = self.model_dir / "model.onnx"
        if not tokenizer_path.exists() or not model_path.exists():
            raise ConfigError(
                f"BGE-M3 model files not found under {self.model_dir}. "
                "Run `wenji download-model` or set WENJI_MODEL_DIR to a directory "
                "containing tokenizer.json + model.onnx."
            )

        import onnxruntime as ort  # heavy import; deferred
        from tokenizers import Tokenizer  # heavy import; deferred

        try:
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=self.max_length)
            tokenizer.enable_padding()
            self._tokenizer = tokenizer
        except Exception as exc:
            raise WenjiError(f"failed to load tokenizer {tokenizer_path}: {exc}") from exc

        # Deterministic single-threaded CPU inference (byte-identical rebuild).
        # WENJI_ONNX_THREADS overrides the default 1 thread for batch ingest
        # speed-ups; multi-thread inference is not byte-identical (floating-
        # point sum-order non-determinism) so set this only for one-shot
        # batch jobs where exact reproducibility is not required.
        n_threads = int(os.environ.get("WENJI_ONNX_THREADS", "1"))
        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = n_threads
        sess_options.inter_op_num_threads = 1
        sess_options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
        try:
            self._session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
                sess_options=sess_options,
            )
        except Exception as exc:
            raise WenjiError(f"failed to load ONNX model {model_path}: {exc}") from exc
        self._input_names = {i.name for i in self._session.get_inputs()}

    def _pool(self, output: np.ndarray) -> np.ndarray:
        """Reduce model output to (B, D)."""
        if output.ndim == 3:
            # last_hidden_state (B, T, D) → CLS pooling (first token)
            return output[:, 0, :]
        if output.ndim == 2:
            # already pooled (B, D)
            return output
        raise WenjiError(f"unexpected ONNX output shape {output.shape}")

    def encode_batch(self, texts: list[str]) -> np.ndarray:
        """Encode a batch of texts to float32 ``(N, DIM)`` array, L2-normalised."""
        if not texts:
            return np.zeros((0, self.DIM), dtype=np.float32)

        self._ensure_loaded()
        assert self._tokenizer is not None and self._session is not None

        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for batch_start in range(0, len(texts), self.batch_size):
            batch = texts[batch_start : batch_start + self.batch_size]
            encoded = self._tokenizer.encode_batch(batch)
            input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
            attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
            feed: dict[str, np.ndarray] = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
            }
            if "token_type_ids" in self._input_names:
                feed["token_type_ids"] = np.zeros_like(input_ids)
            try:
                outputs = self._session.run(None, feed)
            except Exception as exc:
                raise WenjiError(f"ONNX forward failed: {exc}") from exc

            pooled = self._pool(outputs[0])
            if pooled.shape[1] != self.DIM:
                raise WenjiError(f"embed dim mismatch: expected {self.DIM}, got {pooled.shape[1]}")
            norms = np.linalg.norm(pooled, axis=1, keepdims=True)
            norms = np.maximum(norms, 1e-12)
            out[batch_start : batch_start + len(batch)] = (pooled / norms).astype(np.float32)
        return out
