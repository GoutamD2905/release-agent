#!/usr/bin/env bash
# =============================================================================
# create_release_branch.sh
# Creates the release branch based on the configured strategy.
# Usage: ./create_release_branch.sh --version 2.4.0 --strategy exclude [--dry-run]
# =============================================================================
set -euo pipefail

VERSION=""
STRATEGY=""
DRY_RUN=false
BASE_REF=""

# ── Parse arguments ──────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --version)   VERSION="$2";   shift 2 ;;
    --strategy)  STRATEGY="$2";  shift 2 ;;
    --base-ref)  BASE_REF="$2";  shift 2 ;;
    --dry-run)   DRY_RUN=true;   shift   ;;
    *) echo "Unknown arg: $1"; exit 1 ;;
  esac
done

[[ -z "$VERSION"  ]] && { echo "ERROR: --version required";  exit 1; }
[[ -z "$STRATEGY" ]] && { echo "ERROR: --strategy required"; exit 1; }

RELEASE_BRANCH="release/${VERSION}"

# ── Determine base ref ───────────────────────────────────────────────────────
if [[ -z "$BASE_REF" ]]; then
  if [[ "$STRATEGY" == "exclude" ]]; then
    BASE_REF="develop"
  else
    # For inclusion mode, always start from main.
    # (Tags are used for PR date filtering only — not as branch bases.)
    BASE_REF="main"
    echo "INFO: Inclusion mode – base ref: ${BASE_REF}"
  fi
fi

echo "=============================================="
echo " RDK-B Release Branch Creator"
echo " Version  : ${VERSION}"
echo " Strategy : ${STRATEGY}"
echo " Base Ref : ${BASE_REF}"
echo " Branch   : ${RELEASE_BRANCH}"
echo " Dry Run  : ${DRY_RUN}"
echo "=============================================="

# ── Fetch latest ─────────────────────────────────────────────────────────────
echo "[1/4] Fetching latest from origin..."
git fetch origin

# ── Check if release branch already exists ───────────────────────────────────
if git ls-remote --exit-code --heads origin "${RELEASE_BRANCH}" &>/dev/null; then
  echo "WARNING: Branch '${RELEASE_BRANCH}' already exists on origin."
  read -rp "Delete and recreate? [y/N]: " CONFIRM
  CONFIRM_LOWER=$(echo "$CONFIRM" | tr '[:upper:]' '[:lower:]')
  if [[ "$CONFIRM_LOWER" == "y" ]]; then
    if [[ "$DRY_RUN" == "false" ]]; then
      git push origin --delete "${RELEASE_BRANCH}"
    else
      echo "[DRY-RUN] Would delete remote branch: ${RELEASE_BRANCH}"
    fi
  else
    echo "Aborting."
    exit 1
  fi
fi

# ── Create local branch ───────────────────────────────────────────────────────
echo "[2/4] Creating local branch '${RELEASE_BRANCH}' from '${BASE_REF}'..."
if [[ "$DRY_RUN" == "false" ]]; then
  # Clean up any leftover local branch from a previous failed run
  if git show-ref --verify --quiet "refs/heads/${RELEASE_BRANCH}"; then
    echo "  INFO: Removing stale local branch '${RELEASE_BRANCH}'..."
    git branch -D "${RELEASE_BRANCH}"
  fi
  git checkout -b "${RELEASE_BRANCH}" "origin/${BASE_REF}"
else
  echo "[DRY-RUN] Would run: git checkout -b ${RELEASE_BRANCH} origin/${BASE_REF}"
fi

echo "[3/4] Branch created successfully."

# ── Push to origin ────────────────────────────────────────────────────────────
echo "[4/4] Pushing branch to origin..."
if [[ "$DRY_RUN" == "false" ]]; then
  git push -u origin "${RELEASE_BRANCH}"
else
  echo "[DRY-RUN] Would run: git push -u origin ${RELEASE_BRANCH}"
fi

echo ""
echo "✅ Release branch '${RELEASE_BRANCH}' is ready."
