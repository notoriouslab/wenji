#!/usr/bin/env bash
# Stage-2 trim + wenji_r1 baseline run helper.
#
# Prereq:
#   - wenji_r0 already produced + objective sanity gate passed
#   - 主公 (user) provides a trim list at $TRIM_LIST (newline-delimited
#     article_id or content_hash; comments / blanks ignored)
#
# What it does:
#   1. Snapshot pre-trim corpus size + run `wenji corpus trim`.
#   2. Restart `wenji serve` to pick up the new wenji.db state.
#   3. Run `wenji eval run-benchmark` → wenji_r1 run output (with trim_manifest).
#   4. Re-run objective sanity gate (post-trim numbers may differ).
#   5. Produce docs/wenji_r1_baseline.md with trim_manifest section.
#
# Run:
#   bash scripts/run_wenji_r1_trim_baseline.sh path/to/trim_list.txt

set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "usage: $0 <trim_list.txt>" >&2
  exit 2
fi
TRIM_LIST="$1"
if [[ ! -f "$TRIM_LIST" ]]; then
  echo "ERROR: trim list not found: $TRIM_LIST" >&2
  exit 2
fi

WENJI=/tmp/wenji_install_test/bin/wenji
PYTHON=/tmp/wenji_install_test/bin/python
DB=/tmp/wenji_logos.db
SNAPSHOT=tests/benchmark_80_v2_snapshot.json
BASELINE_OUTPUT=tests/benchmark_v2_r13.json
OUT=tests/wenji_r1_run.json
REPORT=docs/wenji_r1_baseline.md
SERVE_PORT=8765
SERVE_LOG=/tmp/wenji_serve_r1.log

if [[ ! -f "$DB" ]]; then
  echo "ERROR: $DB not found." >&2
  exit 2
fi

# Capture pre-trim size + source_type distribution
PRE_SIZE=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB').execute('SELECT COUNT(*) FROM articles_meta').fetchone()[0])")
echo "[helper] pre-trim corpus size: $PRE_SIZE" >&2

echo "[helper] running corpus trim..." >&2
"$WENJI" corpus trim --ids "$TRIM_LIST" --db "$DB"

POST_SIZE=$($PYTHON -c "import sqlite3; print(sqlite3.connect('$DB').execute('SELECT COUNT(*) FROM articles_meta').fetchone()[0])")
REMOVED=$((PRE_SIZE - POST_SIZE))
echo "[helper] post-trim size: $POST_SIZE (removed $REMOVED)" >&2

cleanup() {
  if [[ -n "${SERVE_PID:-}" ]] && kill -0 "$SERVE_PID" 2>/dev/null; then
    kill "$SERVE_PID" || true
    wait "$SERVE_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

echo "[helper] starting wenji serve" >&2
nohup "$WENJI" serve --db "$DB" --port $SERVE_PORT > "$SERVE_LOG" 2>&1 &
SERVE_PID=$!

for i in $(seq 1 60); do
  if curl -sf "http://localhost:$SERVE_PORT/api/search?q=test" -o /dev/null 2>/dev/null; then
    break
  fi
  sleep 1
done

echo "[helper] running r1 benchmark..." >&2
"$WENJI" eval run-benchmark \
  --snapshot "$SNAPSHOT" \
  --db "$DB" \
  --port $SERVE_PORT \
  --top-k 20 \
  --out "$OUT"

echo "[helper] generating r1 report..." >&2
"$PYTHON" - <<EOF
import json
from pathlib import Path
from wenji.eval.sanity_check import compute_objective_overlap
from wenji.eval.report import render_baseline_report, write_baseline_report

wenji_r1 = json.loads(Path("$OUT").read_text(encoding="utf-8"))
baseline = json.loads(Path("$BASELINE_OUTPUT").read_text(encoding="utf-8"))
obj = compute_objective_overlap(wenji_r1, baseline)
overlaps = [{"qid": pq.qid, "overlap_rate": pq.overlap_rate} for pq in obj.per_question]

# Inject trim_manifest into r1 metadata for transparency in the report.
trim_manifest = {
    "removed_count": $REMOVED,
    "corpus_size_before": $PRE_SIZE,
    "corpus_size_after": $POST_SIZE,
    "removed_by_source_type": {},
}
wenji_r1["trim_manifest"] = trim_manifest
Path("$OUT").write_text(json.dumps(wenji_r1, ensure_ascii=False, indent=2), encoding="utf-8")

md = render_baseline_report(
    wenji_r1,
    per_question_overlaps=overlaps,
    trim_manifest=trim_manifest,
    corpus_size=$POST_SIZE,
)
write_baseline_report("$REPORT", md)
print(f"[helper] wrote r1 report → $REPORT")
print(f"[helper] r1 pass_rate_pct={wenji_r1['summary']['pass_rate_pct']}%")
EOF

echo "[helper] r1 done. Review $REPORT and ACK with /spectra:archive when ready." >&2
