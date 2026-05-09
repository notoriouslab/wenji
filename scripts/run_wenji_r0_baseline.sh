#!/usr/bin/env bash
# Stage-1 wenji_r0 baseline run helper.
#
# Prereq:
#   - /tmp/wenji_logos.db ingested from /tmp/logos_full (12k+ articles, jieba + BGE-M3)
#   - /Users/jacobmei/Projects/notoriouslab/logos/tests/benchmark_v2_r13.json present
#   - wenji binary available (assumed at /tmp/wenji_install_test/bin/wenji)
#
# What it does:
#   1. Start `wenji serve --db /tmp/wenji_logos.db` in background.
#   2. Run `wenji eval run-benchmark` against it → wenji_r0 run output.
#   3. Run objective sanity gate (top-10 hits overlap vs baseline run).
#   4. Generate stage-1 baseline markdown report (without subjective gate yet).
#   5. Stop the server.
#   6. Exit 0 on objective gate pass; 1 on fail. Subjective gate (eyeball) is
#      a manual follow-up via `wenji eval sanity-eyeball`.
#
# Run:
#   bash scripts/run_wenji_r0_baseline.sh

set -euo pipefail

WENJI=/tmp/wenji_install_test/bin/wenji
PYTHON=/tmp/wenji_install_test/bin/python
DB=/tmp/wenji_logos.db
SNAPSHOT=tests/benchmark_80_v2_snapshot.json
BASELINE_OUTPUT=tests/benchmark_v2_r13.json
OUT=tests/wenji_r0_run.json
REPORT=docs/wenji_r0_baseline.md
SERVE_PORT=8765
SERVE_LOG=/tmp/wenji_serve.log

if [[ ! -f "$DB" ]]; then
  echo "ERROR: $DB not found. Run 'wenji ingest dir /tmp/logos_full --db $DB --config /tmp/wenji_logos_chunk_config.yaml' first." >&2
  exit 2
fi

cleanup() {
  if [[ -n "${SERVE_PID:-}" ]] && kill -0 "$SERVE_PID" 2>/dev/null; then
    echo "[helper] stopping wenji serve PID=$SERVE_PID" >&2
    kill "$SERVE_PID" || true
    wait "$SERVE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[helper] starting wenji serve on port $SERVE_PORT" >&2
nohup "$WENJI" serve --db "$DB" --port $SERVE_PORT > "$SERVE_LOG" 2>&1 &
SERVE_PID=$!
echo "[helper] serve PID=$SERVE_PID, log=$SERVE_LOG" >&2

# Wait for server readiness (up to 60s).
for i in $(seq 1 60); do
  if curl -sf "http://localhost:$SERVE_PORT/api/search?q=test" -o /dev/null 2>/dev/null; then
    echo "[helper] server ready after ${i}s" >&2
    break
  fi
  sleep 1
done

echo "[helper] running benchmark..." >&2
"$WENJI" eval run-benchmark \
  --snapshot "$SNAPSHOT" \
  --db "$DB" \
  --port $SERVE_PORT \
  --top-k 20 \
  --out "$OUT"

echo "[helper] objective sanity gate vs baseline run..." >&2
"$PYTHON" - <<EOF
import json
from pathlib import Path
from wenji.eval.sanity_check import compute_objective_overlap, emit_objective_diagnostic
from wenji.eval.report import render_baseline_report, write_baseline_report

wenji_r0 = json.loads(Path("$OUT").read_text(encoding="utf-8"))
baseline = json.loads(Path("$BASELINE_OUTPUT").read_text(encoding="utf-8"))
obj = compute_objective_overlap(wenji_r0, baseline)
print(emit_objective_diagnostic(obj))
print(f"\n[helper] mean_overlap={obj.mean_overlap:.4f} threshold={obj.threshold:.2f} passed={obj.passed}")

# Build a partial sanity marker (objective only; subjective deferred).
marker = {
    "promoted_at": "(stage-1 objective gate only; eyeball pending)",
    "objective_gate": {
        "mean_overlap": obj.mean_overlap,
        "threshold": obj.threshold,
        "passed": obj.passed,
    },
    "subjective_gate": {
        "sampled_qids": [],
        "flagged_qids": [],
        "threshold": 1,
        "passed": None,
    },
}
overlaps = [
    {"qid": pq.qid, "overlap_rate": pq.overlap_rate}
    for pq in obj.per_question
]

corpus_size = wenji_r0["summary"].get("corpus_size") or len(wenji_r0.get("questions", []))
md = render_baseline_report(
    wenji_r0,
    sanity_marker=marker,
    per_question_overlaps=overlaps,
    corpus_size=None,
)
write_baseline_report("$REPORT", md)
print(f"[helper] wrote report → $REPORT")
import sys
sys.exit(0 if obj.passed else 1)
EOF

echo "[helper] done." >&2
