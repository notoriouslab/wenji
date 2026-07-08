#!/usr/bin/env bash
# Cleanup audit — run before public release.
# Greps known internal references that must NOT leak into wenji-public.
# Must produce 0 hits to release.

set -uo pipefail
cd "$(dirname "$0")/.."

# Patterns that indicate internal-only references.
# NOTE: 'breadoflife' deliberately excluded — examples/articles/sermon/ retains
# attribution to breadoflife.taipei, which is licensed/permitted for the demo.
PATTERN='PAIOP|主公|趙雲|梅大|paiop_secrets|/home/ubuntu|/Users/jacobmei|logos\.jacobmei|jacob\.mei@|mjdeimac'

# openspec + .claude added 2026-07-08: spec/skill docs are public too — persona
# terms and personal metadata leaked into archived changes for two months
# because they were outside the scan scope.
DIRS=(src/wenji tests/wenji examples .github openspec .claude)
FILES=(README.md LICENSE CHANGELOG.md CONTRIBUTING.md pyproject.toml)
# NOTE: logos repo `docs/` not scanned — those files belong to the logos
# consumer, not wenji-public. The wenji-specific top-level docs added in
# Group 12 (README / CHANGELOG / CONTRIBUTING / LICENSE) are scanned via FILES.

echo "[audit] scanning dirs : ${DIRS[*]}"
echo "[audit] scanning files: ${FILES[*]}"
echo "[audit] pattern       : $PATTERN"
echo

HITS=$(grep -irnE \
    --include='*.py' --include='*.md' --include='*.html' --include='*.css' \
    --include='*.yaml' --include='*.yml' --include='*.sql' --include='*.toml' \
    --include='*.txt' --include='*.jsonl' --include='*.json' --include='*.sh' \
    "$PATTERN" "${DIRS[@]}" "${FILES[@]}" 2>/dev/null || true)

if [ -z "$HITS" ]; then
    echo "✓ release-clean — 0 hits"
    exit 0
fi

echo "✗ internal references found — fix before release:"
echo "$HITS"
exit 1
