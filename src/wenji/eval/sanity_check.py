"""Stage-1 baseline sanity check: dual gate (objective overlap + subjective eyeball).

Objective gate: per-question top-10 hits from wenji_r0 are intersected with
logos_r13 hits using ``(content_hash, normalized_title)`` dual-keys. The mean
overlap rate across 80 questions SHALL be ≥ 0.70.

Subjective gate: 8 questions are sampled at random and presented to the human
reviewer as ``wenji top-5`` vs ``logos top-5``; if more than 1 question is
flagged unreasonable, the gate fails.

Both gates are independent; both MUST pass for the run to be promoted to
``wenji_r0_baseline``.
"""

from __future__ import annotations

import json
import random
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

OBJECTIVE_THRESHOLD = 0.70
SUBJECTIVE_MAX_FLAGS = 1
EYEBALL_SAMPLE_N = 8
TOP_K_FOR_OVERLAP = 10


def _normalize_title(t: str | None) -> str:
    if not t:
        return ""
    # collapse whitespace + lowercase + strip punctuation that may differ.
    s = re.sub(r"\s+", "", t).lower()
    return s


def _hits_to_keyset(hits: list[dict], top_k: int = TOP_K_FOR_OVERLAP) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for h in hits[:top_k]:
        ch = h.get("content_hash") or ""
        title = _normalize_title(h.get("title"))
        keys.add((ch, title))
    return keys


@dataclass(frozen=True)
class PerQuestionOverlap:
    qid: int
    overlap_rate: float
    wenji_count: int
    logos_count: int
    intersection_count: int


@dataclass(frozen=True)
class ObjectiveGateResult:
    mean_overlap: float
    threshold: float
    passed: bool
    per_question: list[PerQuestionOverlap]


def compute_objective_overlap(
    wenji_r0: dict,
    logos_r13: dict,
    *,
    top_k: int = TOP_K_FOR_OVERLAP,
) -> ObjectiveGateResult:
    """Compute per-question and mean top-K hit overlap.

    Both inputs SHALL conform to the v2 run schema (see
    ``wenji-retrieval-baseline.spec``). Questions are matched by ``id``.
    Overlap formula: ``|intersection| / |wenji_set|``; ``|wenji_set|`` may be
    less than ``top_k`` after rollup, which is intentional.
    """
    logos_by_id = {q["id"]: q for q in logos_r13.get("questions", [])}
    per_question: list[PerQuestionOverlap] = []
    for q in wenji_r0.get("questions", []):
        qid = q["id"]
        wenji_keys = _hits_to_keyset(_extract_hits(q), top_k=top_k)
        logos_q = logos_by_id.get(qid)
        if logos_q is None:
            continue
        logos_keys = _hits_to_keyset(_extract_hits(logos_q), top_k=top_k)
        if not wenji_keys:
            overlap_rate = 0.0
        else:
            intersection = wenji_keys & logos_keys
            overlap_rate = len(intersection) / len(wenji_keys)
        per_question.append(
            PerQuestionOverlap(
                qid=qid,
                overlap_rate=round(overlap_rate, 4),
                wenji_count=len(wenji_keys),
                logos_count=len(logos_keys),
                intersection_count=len(wenji_keys & logos_keys) if wenji_keys else 0,
            )
        )

    if per_question:
        mean_overlap = sum(pq.overlap_rate for pq in per_question) / len(per_question)
    else:
        mean_overlap = 0.0

    return ObjectiveGateResult(
        mean_overlap=round(mean_overlap, 4),
        threshold=OBJECTIVE_THRESHOLD,
        passed=mean_overlap >= OBJECTIVE_THRESHOLD,
        per_question=sorted(per_question, key=lambda p: p.overlap_rate),
    )


def _extract_hits(question: dict) -> list[dict]:
    """Extract per-hit list from a v2 question entry, normalising shapes."""
    hits = question.get("article_results") or question.get("hits") or []
    return list(hits)


@dataclass(frozen=True)
class SubjectiveSample:
    qid: int
    query: str
    wenji_top5: list[dict]
    logos_top5: list[dict]


def sample_eyeball_questions(
    wenji_r0: dict, logos_r13: dict, *, n: int = EYEBALL_SAMPLE_N, seed: int | None = None
) -> list[SubjectiveSample]:
    """Sample N questions for the subjective eyeball gate."""
    rng = random.Random(seed)
    logos_by_id = {q["id"]: q for q in logos_r13.get("questions", [])}
    wenji_qs = wenji_r0.get("questions", [])
    if not wenji_qs:
        return []
    sampled = rng.sample(wenji_qs, min(n, len(wenji_qs)))
    out: list[SubjectiveSample] = []
    for q in sampled:
        logos_q = logos_by_id.get(q["id"], {})
        out.append(
            SubjectiveSample(
                qid=q["id"],
                query=q.get("query", ""),
                wenji_top5=_extract_hits(q)[:5],
                logos_top5=_extract_hits(logos_q)[:5],
            )
        )
    return out


@dataclass(frozen=True)
class SubjectiveGateResult:
    sampled_qids: list[int]
    flagged_qids: list[int]
    threshold: int
    passed: bool


def evaluate_subjective_gate(
    flagged_qids: list[int], sampled_qids: list[int]
) -> SubjectiveGateResult:
    """Apply the > 1 flag → fail rule; return the gate verdict."""
    return SubjectiveGateResult(
        sampled_qids=list(sampled_qids),
        flagged_qids=list(flagged_qids),
        threshold=SUBJECTIVE_MAX_FLAGS,
        passed=len(flagged_qids) <= SUBJECTIVE_MAX_FLAGS,
    )


def write_promotion_marker(
    out_path: Path,
    *,
    objective: ObjectiveGateResult,
    subjective: SubjectiveGateResult,
    wenji_r0_path: str,
) -> None:
    """Write the wenji_r0_baseline promotion marker file (both gates passed)."""
    if not objective.passed or not subjective.passed:
        raise RuntimeError(
            "cannot write promotion marker: gates did not both pass "
            f"(objective={objective.passed}, subjective={subjective.passed})"
        )
    marker = {
        "promoted_at": datetime.now(timezone.utc).isoformat(),
        "wenji_r0_path": wenji_r0_path,
        "objective_gate": {
            "mean_overlap": objective.mean_overlap,
            "threshold": objective.threshold,
            "passed": True,
        },
        "subjective_gate": {
            "sampled_qids": subjective.sampled_qids,
            "flagged_qids": subjective.flagged_qids,
            "threshold": subjective.threshold,
            "passed": True,
        },
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")


def emit_objective_diagnostic(result: ObjectiveGateResult) -> str:
    """Format an ascending diagnostic of per-question overlap rates."""
    lines = [
        f"# Objective gate diagnostic — mean={result.mean_overlap:.4f} "
        f"threshold={result.threshold:.2f} passed={result.passed}",
        "",
        f"{'qid':>4}  {'overlap':>8}  {'inter':>5}  {'wenji':>5}  {'logos':>5}",
    ]
    for pq in result.per_question:
        lines.append(
            f"{pq.qid:>4}  {pq.overlap_rate:>8.4f}  "
            f"{pq.intersection_count:>5}  {pq.wenji_count:>5}  {pq.logos_count:>5}"
        )
    return "\n".join(lines)
