#!/usr/bin/env bash
# verify.sh - one command: get current, check every weight, say yes or no.
#   bash plugins/watch-video/verify.sh
# Stashes local edits (kept in `git stash list`, nothing deleted), pulls, finds a usable Python,
# then runs watch_setup.py check, which hashes every weight and tries to open the reader's graphs.
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
# Pick the interpreter that HAS the stack, not the one whose version matches the pin. 261114: an earlier
# version preferred python3.12 because 3.12 is the pin, found no onnxruntime in it, and reported NOT READY
# on a machine where the scan venv had everything.
CANDS=("$HOME/.scan-and-index/venv/bin/python" "$(command -v python3.12)" "$(command -v python3)" "$(command -v python)")
for P in "${CANDS[@]}"; do
  [ -x "${P:-}" ] || continue
  if "$P" -c "import onnxruntime" >/dev/null 2>&1; then PY="$P"; break; fi
  [ -z "${FALLBACK:-}" ] && FALLBACK="$P"
done
if [ -z "${PY:-}" ]; then
  PY="${FALLBACK:-}"
  [ -z "$PY" ] && { echo "   NO PYTHON FOUND"; exit 2; }
  echo "   no interpreter here has onnxruntime; reporting with $PY so the check can name what is missing"
fi
echo "   $PY ($("$PY" -V 2>&1))"

echo "== 3/3 checking every weight"
S=plugins/watch-video/skills/261113-watch-video-v10/scripts/watch_setup.py
OUT=$("$PY" "$S" check 2>&1); RC=$?
echo "$OUT"
echo
READER=$(printf '%s\n' "$OUT" | grep -E "^reader .* load " || true)
echo "=============================================================="
if [ $RC -eq 0 ] && printf '%s\n' "$READER" | grep -q "ok, loaded"; then
  echo "VERDICT: WORKS. Every weight hashes clean and the reader's graphs opened."
elif [ $RC -eq 0 ]; then
  echo "VERDICT: SEE PASS READY, READER NOT INSTALLED. read-shots runs; every read slot stays unresolved."
  echo "To install the reader (~260 MB, pinned): see plugins/watch-video/INSTALL_MAC.md step 3."
else
  echo "VERDICT: NOT READY. The lines above marked MISSING or MISMATCH are what to fix."
fi
echo "=============================================================="
exit $RC
