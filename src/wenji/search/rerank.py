"""Optional cross-encoder reranking.

Default model: ``Xenova/bge-reranker-base`` (BGE Reranker base, ONNX). Disabled
by default — instantiate with ``enabled=True`` (typically driven by
``search.yaml``).

Cross-encoder scoring: tokenize ``(query, doc)`` pairs together, run forward,
take the single logit per pair as relevance score (BGE Reranker convention).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import numpy as np

from wenji.core.errors import ConfigError, WenjiError

DEFAULT_CACHE_DIR = Path.home() / ".cache" / "wenji" / "qwen3-reranker"


class CrossEncoderReranker:
    """Cross-encoder reranker.

    Args:
        model_dir: Directory containing ``tokenizer.json`` + ``model.onnx``.
            Falls back to ``$WENJI_RERANKER_DIR`` then :data:`DEFAULT_CACHE_DIR`.
        enabled: Master switch — when False, :meth:`score` is a no-op.
        max_length: Tokenizer truncation length per (query, doc) pair.
    """

    def __init__(
        self,
        model_dir: str | Path | None = None,
        *,
        enabled: bool = False,
        max_length: int = 512,
    ) -> None:
        resolved = (
            Path(model_dir)
            if model_dir is not None
            else Path(os.environ.get("WENJI_RERANKER_DIR", DEFAULT_CACHE_DIR))
        )
        self.model_dir: Path = resolved
        self.enabled = enabled
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
                f"reranker model files not found under {self.model_dir}. "
                "Run `wenji download-model --reranker` to fetch the default model."
            )

        import onnxruntime as ort
        from tokenizers import Tokenizer

        try:
            tokenizer = Tokenizer.from_file(str(tokenizer_path))
            tokenizer.enable_truncation(max_length=self.max_length)
            tokenizer.enable_padding()
            self._tokenizer = tokenizer
        except Exception as exc:
            raise WenjiError(f"failed to load tokenizer {tokenizer_path}: {exc}") from exc

        sess_options = ort.SessionOptions()
        sess_options.intra_op_num_threads = 1
        sess_options.inter_op_num_threads = 1
        try:
            self._session = ort.InferenceSession(
                str(model_path),
                providers=["CPUExecutionProvider"],
                sess_options=sess_options,
            )
        except Exception as exc:
            raise WenjiError(f"failed to load reranker ONNX {model_path}: {exc}") from exc
        self._input_names = {i.name for i in self._session.get_inputs()}

    def score(
        self,
        query: str,
        candidates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Annotate candidates with ``rerank_score`` and return them sorted desc.

        When ``enabled=False``, candidates are returned untouched.
        """
        if not self.enabled or not candidates:
            return candidates

        self._ensure_loaded()
        assert self._tokenizer is not None and self._session is not None

        # Build (query, doc) pairs
        docs: list[str] = []
        for c in candidates:
            text_parts = [c.get("title", "") or ""]
            snippet = c.get("content_snippet") or c.get("content_raw") or ""
            text_parts.append(snippet)
            docs.append(" ".join(p for p in text_parts if p))

        encoded = self._tokenizer.encode_batch(list(zip([query] * len(docs), docs, strict=True)))
        input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
        attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in self._input_names:
            type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)
            feed["token_type_ids"] = type_ids

        try:
            outputs = self._session.run(None, feed)
        except Exception as exc:
            raise WenjiError(f"reranker ONNX forward failed: {exc}") from exc

        logits = outputs[0]
        # Cross-encoder typical output: (N, 1) regression logit OR (N, 2) softmax-ish
        if logits.ndim == 2 and logits.shape[1] == 1:
            scores = logits[:, 0]
        elif logits.ndim == 2 and logits.shape[1] == 2:
            # take positive class logit
            scores = logits[:, 1]
        elif logits.ndim == 1:
            scores = logits
        else:
            raise WenjiError(f"unexpected reranker logits shape {logits.shape}")

        out = list(candidates)
        for c, s in zip(out, scores, strict=True):
            c["rerank_score"] = float(s)
        out.sort(key=lambda c: -c.get("rerank_score", 0.0))
        return out
