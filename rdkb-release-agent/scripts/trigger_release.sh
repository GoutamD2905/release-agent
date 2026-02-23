#!/usr/bin/env bash
# =============================================================================
# trigger_release.sh — RDK-B Release Framework: All-in-One Release Trigger
#
# This script does EVERYTHING for a bi-weekly release in one command:
#   1. Updates .release-config.yml with the new version and PR list
#   2. Commits and pushes the config to the component repo
#   3. Triggers the GitHub Actions release workflow via gh CLI
#   4. Optionally streams the workflow run logs
#
# Usage:
#   ./trigger_release.sh \
#     --component-repo  <org/repo>       (e.g. rdkcentral/rdkb-wifi)
#     --component-dir   <local-path>     (local clone of the component repo)
#     --version         <semver>         (e.g. 2.4.0)
#     [--prs            <num,num,...>]   (comma-separated PR numbers)
#     [--strategy       include|exclude] (default: keep existing)
#     [--base-branch    <branch>]        (default: keep existing or develop)
#     [--dry-run]                        (simulate workflow — no push)
#     [--watch]                          (stream workflow logs after triggering)
#
# First-time setup? Use bootstrap.sh first, then this script each cycle.
#
# Requirements:
#   - gh CLI installed and authenticated (gh auth login)
#   - git configured with push access to the component repo
#   - .github/workflows/rdkb-biweekly-release.yml exists in component repo
#   - .release-config.yml exists in component repo (created by bootstrap.sh)
# =============================================================================
set -uo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
COMPONENT_REPO=""
COMPONENT_DIR="."
VERSION=""
PRS=""
STRATEGY=""
BASE_BRANCH=""
DRY_RUN=false
WATCH=false

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --component-repo) COMPONENT_REPO="$2"; shift 2 ;;
    --component-dir)  COMPONENT_DIR="$2";  shift 2 ;;
    --version)        VERSION="$2";        shift 2 ;;
    --prs)            PRS="$2";            shift 2 ;;
    --strategy)       STRATEGY="$2";       shift 2 ;;
    --base-branch)    BASE_BRANCH="$2";    shift 2 ;;
    --dry-run)        DRY_RUN=true;        shift   ;;
    --watch)          WATCH=true;          shift   ;;
    --help|-h)
      grep '^#' "$0" | grep -v '^#!/' | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *) echo "❌ Unknown argument: $1"; exit 1 ;;
  esac
done

# ── Validate required ─────────────────────────────────────────────────────────
[[ -z "$COMPONENT_REPO" ]] && { echo "❌ --component-repo is required (e.g. rdkcentral/rdkb-wifi)"; exit 1; }
[[ -z "$VERSION"        ]] && { echo "❌ --version is required (e.g. 2.4.0)"; exit 1; }

CONFIG_FILE="${COMPONENT_DIR}/.release-config.yml"
WORKFLOW_FILE="${COMPONENT_DIR}/.github/workflows/rdkb-biweekly-release.yml"

# ── Check prerequisites ───────────────────────────────────────────────────────
echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  RDK-B Release Trigger"
echo "  Component:  ${COMPONENT_REPO}"
echo "  Version:    ${VERSION}"
echo "  Dry Run:    ${DRY_RUN}"
echo "════════════════════════════════════════════════════════════════"

if ! command -v gh &>/dev/null; then
  echo "❌ GitHub CLI (gh) not installed. Install from: https://cli.github.com"
  exit 1
fi

if ! gh auth status &>/dev/null; then
  echo "❌ Not authenticated with GitHub CLI. Run: gh auth login"
  exit 1
fi

if [[ ! -d "$COMPONENT_DIR/.git" ]]; then
  echo "❌ ${COMPONENT_DIR} is not a git repository."
  echo "   Clone the component repo first: git clone https://github.com/${COMPONENT_REPO}"
  exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  echo "❌ .release-config.yml not found at: ${CONFIG_FILE}"
  echo "   Run bootstrap.sh first to set up the component repo."
  exit 1
fi

if [[ ! -f "$WORKFLOW_FILE" ]]; then
  echo "❌ Workflow file not found at: ${WORKFLOW_FILE}"
  echo "   Run bootstrap.sh first to set up the component repo."
  exit 1
fi

echo "✅  Prerequisites OK"
echo ""

# ── Step 1: Update .release-config.yml ───────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  Step 1: Updating .release-config.yml"
echo "──────────────────────────────────────────────────────────────"

# Update version
sed -i.bak "s/^version:.*/version: \"${VERSION}\"/" "$CONFIG_FILE"

# Update PRs list if provided
if [[ -n "$PRS" ]]; then
  # Build YAML prs block from comma-separated input
  PRS_YAML=""
  IFS=',' read -ra PR_ARRAY <<< "$PRS"
  for pr in "${PR_ARRAY[@]}"; do
    pr=$(echo "$pr" | tr -d ' ')
    PRS_YAML+="  - ${pr}"$'\n'
  done

  # Replace everything between 'prs:' and the next non-indented key
  python3 - <<PYEOF
import re, sys

with open("${CONFIG_FILE}") as f:
    content = f.read()

prs_block  = "prs:\n${PRS_YAML}"
# Replace existing prs: block (handles both empty and populated lists)
new_content = re.sub(
    r'^prs:.*?(?=^\w|\Z)',
    prs_block,
    content,
    flags=re.MULTILINE | re.DOTALL
)

with open("${CONFIG_FILE}", "w") as f:
    f.write(new_content)

print("  ✅  prs list updated.")
PYEOF
fi

# Update strategy if provided
if [[ -n "$STRATEGY" ]]; then
  sed -i.bak "s/^strategy:.*/strategy: \"${STRATEGY}\"/" "$CONFIG_FILE"
  echo "  ✅  strategy updated to: ${STRATEGY}"
fi

# Update base_branch if provided
if [[ -n "$BASE_BRANCH" ]]; then
  if grep -q "^base_branch:" "$CONFIG_FILE"; then
    sed -i.bak "s/^base_branch:.*/base_branch: \"${BASE_BRANCH}\"/" "$CONFIG_FILE"
  else
    echo "base_branch: \"${BASE_BRANCH}\"" >> "$CONFIG_FILE"
  fi
  echo "  ✅  base_branch updated to: ${BASE_BRANCH}"
fi

# Cleanup sed backup
rm -f "${CONFIG_FILE}.bak"

echo ""
echo "  Updated config:"
grep -E "^(component_name|version|strategy|base_branch|prs|dry_run):" "$CONFIG_FILE" | sed 's/^/    /'
echo ""

# ── Step 2: Commit and push ───────────────────────────────────────────────────
echo "──────────────────────────────────────────────────────────────"
echo "  Step 2: Committing and pushing config"
echo "──────────────────────────────────────────────────────────────"

pushd "$COMPONENT_DIR" > /dev/null

BASE_BRANCH_CURRENT=$(git rev-parse --abbrev-ref HEAD)

git add .release-config.yml

if git diff --cached --quiet; then
  echo "  ℹ️   No changes to .release-config.yml — already up to date."
else
  git commit -m "chore: release ${VERSION} config [rdkb-release-agent]"
  echo "  ✅  Committed."

  if [[ "$DRY_RUN" == "true" ]]; then
    echo "  ℹ️   Dry run: skipping git push."
  else
    git push origin "${BASE_BRANCH_CURRENT}"
    echo "  ✅  Pushed to origin/${BASE_BRANCH_CURRENT}."
  fi
fi

popd > /dev/null

# ── Step 3: Trigger GitHub Actions workflow ───────────────────────────────────
echo ""
echo "──────────────────────────────────────────────────────────────"
echo "  Step 3: Triggering GitHub Actions workflow"
echo "──────────────────────────────────────────────────────────────"

DRY_RUN_INPUT="false"
[[ "$DRY_RUN" == "true" ]] && DRY_RUN_INPUT="true"

# Small delay to ensure push is visible to GitHub
[[ "$DRY_RUN" == "false" ]] && sleep 3

gh workflow run rdkb-biweekly-release.yml \
  --repo    "$COMPONENT_REPO" \
  --field   "version=${VERSION}" \
  --field   "dry_run=${DRY_RUN_INPUT}"

echo ""
echo "  ✅  Workflow triggered!"
echo ""

# ── Print run URL ─────────────────────────────────────────────────────────────
sleep 5
RUN_URL=$(gh run list \
  --repo "$COMPONENT_REPO" \
  --workflow rdkb-biweekly-release.yml \
  --limit 1 \
  --json url \
  --jq '.[0].url' 2>/dev/null || echo "")

echo "════════════════════════════════════════════════════════════════"
echo "  ✅  Release ${VERSION} triggered for: ${COMPONENT_REPO}"
if [[ -n "$RUN_URL" ]]; then
  echo "  🔗  Workflow run: ${RUN_URL}"
fi
echo "════════════════════════════════════════════════════════════════"
echo ""

# ── Optional: stream logs ─────────────────────────────────────────────────────
if [[ "$WATCH" == "true" ]]; then
  echo "  👀  Watching workflow run (Ctrl+C to detach)..."
  echo ""
  RUN_ID=$(gh run list \
    --repo "$COMPONENT_REPO" \
    --workflow rdkb-biweekly-release.yml \
    --limit 1 \
    --json databaseId \
    --jq '.[0].databaseId' 2>/dev/null || echo "")

  if [[ -n "$RUN_ID" ]]; then
    gh run watch "$RUN_ID" --repo "$COMPONENT_REPO"
    echo ""
    gh run view  "$RUN_ID" --repo "$COMPONENT_REPO"
  else
    echo "  ⚠️   Could not determine run ID — check Actions tab manually."
  fi
fi
