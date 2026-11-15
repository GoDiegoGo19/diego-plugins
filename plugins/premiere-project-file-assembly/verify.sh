#!/usr/bin/env bash
# verify.sh - one command: get current, run the plugin's own tests, say yes or no.
#   bash plugins/premiere-project-file-assembly/verify.sh
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
echo "== 1/3 getting current"
# Verification is read-only, so it NEVER moves your work. A dirty tree means no pull, not a stash:
# 261114, an earlier version stashed tracked edits and left them stashed, which swallowed work in progress
# (its author's, on the first run) and is exactly the trap Diego already hit once with GUESTBOOK.md.
if [ -n "$(git diff --name-only; git diff --cached --name-only)" ]; then
  echo "   local edits present, so NOT pulling. Checking what is on disk right now:"
  git diff --name-only; git diff --cached --name-only
  echo "   (commit or stash them yourself, then run this again, to verify the published version)"
else
  git pull -q --ff-only || git pull -q || { echo "   PULL FAILED - fix that first"; exit 2; }
fi
echo "   at $(git log --oneline -1)"

echo "== 2/3 picking python"
for P in "$HOME/.scan-and-index/venv/bin/python" "$(command -v python3.12)" "$(command -v python3)" "$(command -v python)"; do
  [ -x "${P:-}" ] || continue
  # Prefer an interpreter that can also run the bundled MiniLM, so the organize lane is really exercised.
  if "$P" -c "import numpy, onnxruntime" >/dev/null 2>&1; then PY="$P"; break; fi
  [ -z "${FALLBACK:-}" ] && "$P" -c "import json,gzip,xml.etree.ElementTree" >/dev/null 2>&1 && FALLBACK="$P"
done
if [ -z "${PY:-}" ]; then
  PY="${FALLBACK:-}"
  [ -z "$PY" ] && { echo "   NO PYTHON FOUND"; exit 2; }
  echo "   no interpreter here has numpy + onnxruntime; the MiniLM lane will skip and organize falls back to tfidf"
fi
echo "   $PY ($("$PY" -V 2>&1))"

echo "== 3/3 running the plugin's tests"
S=plugins/premiere-project-file-assembly/skills/261112-premiere-project-file-assembly-v2
OUT=$(cd "$S" && "$PY" -I -B tests/run_tests.py 2>&1); RC=$?
printf '%s\n' "$OUT" | tail -15
echo
echo "=============================================================="
if [ $RC -eq 0 ]; then
  echo "VERDICT: WORKS. The plugin's own tests pass on this machine."
  echo "Writing a .prproj is verified by reparse here; it has still never been opened in Premiere."
else
  echo "VERDICT: NOT READY. The failing lines above are the list."
fi
echo "=============================================================="
exit $RC
