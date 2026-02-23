#!/usr/bin/env bash
# =============================================================================
# safe_cherry_pick.sh
# Safely cherry-picks a commit onto the current branch.
# Uses the intelligent resolve_conflicts.py engine for all conflict types.
#
# Usage: ./safe_cherry_pick.sh --sha <commit-sha> --pr <pr-number> \
#          [--conflict-policy auto|pause|skip] [--dry-run]
#
# Exit codes:
#   0 = success (cherry-picked cleanly or with auto-resolved conflicts)
#   2 = conflict that could not be auto-resolved (caller decides what to do)
#   1 = unexpected error
# =============================================================================

# NOTE: We do NOT use 'set -e' here because git cherry-pick exits non-zero
# on conflict, and we need to handle that gracefully in the else branch.
set -uo pipefail

SHA=""
PR_NUM=""
DRY_RUN=false
CONFLICT_POLICY="pause"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --sha)              SHA="$2";              shift 2 ;;
    --pr)               PR_NUM="$2";           shift 2 ;;
    --conflict-policy)  CONFLICT_POLICY="$2";  shift 2 ;;
    --dry-run)          DRY_RUN=true;          shift   ;;
    *) echo "❌ Unknown argument: $1"; exit 1 ;;
  esac
done

[[ -z "$SHA"    ]] && { echo "❌ ERROR: --sha is required";    exit 1; }
[[ -z "$PR_NUM" ]] && { echo "❌ ERROR: --pr is required";     exit 1; }

# Locate resolve_conflicts.py relative to this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RESOLVER="${SCRIPT_DIR}/resolve_conflicts.py"

echo "→ Cherry-picking PR #${PR_NUM} (${SHA:0:12}...)..."

if [[ "$DRY_RUN" == "true" ]]; then
  echo "  [DRY-RUN] Would run: git cherry-pick -x ${SHA}"
  exit 0
fi

# ── Validate SHA exists ───────────────────────────────────────────────────────
if ! git cat-file -e "${SHA}^{commit}" 2>/dev/null; then
  echo "  ❌ SHA ${SHA} not found in local git history."
  echo "     Run: git fetch origin  — then retry."
  exit 1
fi

# ── Detect merge commit ───────────────────────────────────────────────────────
PARENT_COUNT=$(git cat-file -p "${SHA}" | grep -c "^parent " || true)
if [[ "$PARENT_COUNT" -gt 1 ]]; then
  echo "  ℹ️  Merge commit detected (parents: ${PARENT_COUNT}) — using -m 1 to pick the feature diff."
  CHERRY_PICK_CMD=(git cherry-pick -x -m 1 "${SHA}")
else
  CHERRY_PICK_CMD=(git cherry-pick -x "${SHA}")
fi

# ── Attempt cherry-pick ───────────────────────────────────────────────────────
# Run WITHOUT eval so set -e does not interfere with non-zero exit
if "${CHERRY_PICK_CMD[@]}" 2>&1; then
  echo "  ✅ PR #${PR_NUM} cherry-picked successfully (no conflicts)."
  exit 0
fi

# ── Conflict detected ─────────────────────────────────────────────────────────
echo ""
echo "  ⚠️  Cherry-pick hit conflicts on PR #${PR_NUM}."
echo "  Conflicting files:"
git diff --name-only --diff-filter=U 2>/dev/null | sed 's/^/     • /' || true
echo ""

# Log the conflict
mkdir -p /tmp/rdkb-release-conflicts
CONFLICT_FILES=$(git diff --name-only --diff-filter=U 2>/dev/null | tr '\n' ',' | sed 's/,$//')
echo "${PR_NUM}|${SHA}|${CONFLICT_FILES}" >> /tmp/rdkb-release-conflicts/conflicts.log

if [[ "$CONFLICT_POLICY" == "skip" ]]; then
  echo "  ⚠️  Skipping PR #${PR_NUM} (conflict_policy=skip)."
  echo "     The conflicting files will remain in an unresolved state."
  git cherry-pick --abort 2>/dev/null || true
  exit 2
fi

# auto or pause — always attempt auto-resolution first
echo "  🧠 Invoking smart conflict resolution engine (mode=cherry-pick)..."
CONFIG_FLAG=""
[[ -f ".release-config.yml" ]] && CONFIG_FLAG="--config .release-config.yml"
python3 "${RESOLVER}" --mode cherry-pick --pr "${PR_NUM}" --smart --safety-prefer ${CONFIG_FLAG}
RESOLVER_EXIT=$?

if [[ "$RESOLVER_EXIT" -eq 0 ]]; then
  echo ""
  echo "  ✅ All conflicts auto-resolved. Continuing cherry-pick..."
  if git cherry-pick --continue --no-edit 2>&1; then
    echo "  ✅ PR #${PR_NUM} cherry-picked with auto-resolved conflicts."
    exit 0
  else
    echo "  ❌ git cherry-pick --continue failed after auto-resolution."
    git cherry-pick --abort 2>/dev/null || true
    exit 2
  fi
else
  echo ""
  echo "  ❌ Could not fully auto-resolve all conflicts for PR #${PR_NUM}."
  echo "     Unresolved files are listed above."
  git cherry-pick --abort 2>/dev/null || true

  if [[ "$CONFLICT_POLICY" == "pause" || "$CONFLICT_POLICY" == "auto" ]]; then
    echo ""
    echo "  ⛔ ACTION REQUIRED — Manual resolution needed:"
    echo "     1. Apply the changes from PR #${PR_NUM} manually."
    echo "     2. Run: git cherry-pick -x -m 1 ${SHA}  (or without -m 1 if not a merge commit)"
    echo "     3. Resolve conflicts, then: git cherry-pick --continue --no-edit"
    echo "     4. Re-run the release orchestrator."
  fi
  exit 2
fi
