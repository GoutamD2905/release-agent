#!/usr/bin/env bash
# =============================================================================
# adopt.sh — Single-Command Adoption Hook for rdkb-release-agent
#
# Run this from any RDK-B component repo to automatically set up
# the bi-weekly release framework with smart conflict resolution.
#
# Usage:
#   # From inside your component repo:
#   curl -sL https://raw.githubusercontent.com/<org>/rdkb-release-agent/main/adopt.sh \
#     | bash -s -- --component-repo <org/repo> --version <X.Y.Z>
#
#   # Or clone and run locally:
#   ./adopt.sh --component-repo GoutamD2905/advanced-security --version 2.4.0
#
# What it does:
#   1. Generates .release-config.yml in your component repo
#   2. Generates .github/workflows/rdkb-biweekly-release.yml
#   3. Prints clear next-steps for the component owner
#
# The generated workflow clones rdkb-release-agent at runtime,
# so you always get the latest orchestrator + smart merge engine.
# =============================================================================
set -euo pipefail

# ── Defaults ──────────────────────────────────────────────────────────────────
COMPONENT_REPO=""
AGENT_REPO="GoutamD2905/rdkb-release-agent"
VERSION="1.0.0"
STRATEGY="exclude"
BASE_BRANCH="develop"
OUTPUT_DIR="."
DRY_RUN=false

# ── Colors ────────────────────────────────────────────────────────────────────
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
RESET='\033[0m'

# ── Parse arguments ───────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --component-repo)   COMPONENT_REPO="$2";   shift 2 ;;
    --agent-repo)       AGENT_REPO="$2";       shift 2 ;;
    --version)          VERSION="$2";          shift 2 ;;
    --strategy)         STRATEGY="$2";         shift 2 ;;
    --base-branch)      BASE_BRANCH="$2";      shift 2 ;;
    --output-dir)       OUTPUT_DIR="$2";       shift 2 ;;
    --dry-run)          DRY_RUN=true;          shift   ;;
    -h|--help)
      echo ""
      echo "  rdkb-release-agent: Single-Command Adoption"
      echo ""
      echo "  Usage:"
      echo "    ./adopt.sh --component-repo <org/repo> --version <X.Y.Z> [options]"
      echo ""
      echo "  Required:"
      echo "    --component-repo   GitHub org/repo (e.g. rdkcentral/rdkb-wifi)"
      echo "    --version          Release version (e.g. 2.4.0)"
      echo ""
      echo "  Optional:"
      echo "    --agent-repo       Agent repo (default: GoutamD2905/rdkb-release-agent)"
      echo "    --strategy         exclude or include (default: exclude)"
      echo "    --base-branch      Integration branch (default: develop)"
      echo "    --output-dir       Where to write files (default: current dir)"
      echo "    --dry-run          Show what would be created without writing"
      echo ""
      exit 0
      ;;
    *) echo "❌ Unknown argument: $1. Run with --help for usage."; exit 1 ;;
  esac
done

[[ -z "$COMPONENT_REPO" ]] && { echo "❌ --component-repo is required"; exit 1; }

COMPONENT_NAME=$(echo "$COMPONENT_REPO" | cut -d'/' -f2)
CONFIG_FILE="${OUTPUT_DIR}/.release-config.yml"
WORKFLOW_DIR="${OUTPUT_DIR}/.github/workflows"
WORKFLOW_FILE="${WORKFLOW_DIR}/rdkb-biweekly-release.yml"

# ── Banner ────────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}╔══════════════════════════════════════════════════════════════╗${RESET}"
echo -e "${BOLD}║       🤖 RDK-B Release Agent — Component Adoption          ║${RESET}"
echo -e "${BOLD}╚══════════════════════════════════════════════════════════════╝${RESET}"
echo ""
echo -e "  Component  : ${CYAN}${COMPONENT_REPO}${RESET}"
echo -e "  Agent Repo : ${CYAN}${AGENT_REPO}${RESET}"
echo -e "  Version    : ${CYAN}${VERSION}${RESET}"
echo -e "  Strategy   : ${CYAN}${STRATEGY}${RESET}"
echo -e "  Base Branch: ${CYAN}${BASE_BRANCH}${RESET}"
echo ""

# ── Check for existing files ─────────────────────────────────────────────────
SKIP_CONFIG=false
SKIP_WORKFLOW=false

if [[ -f "$CONFIG_FILE" ]]; then
  echo -e "${YELLOW}⚠️  .release-config.yml already exists. Skipping config generation.${RESET}"
  echo "   To regenerate, delete the file and re-run."
  SKIP_CONFIG=true
fi

if [[ -f "$WORKFLOW_FILE" ]]; then
  echo -e "${YELLOW}⚠️  Workflow already exists. Skipping workflow generation.${RESET}"
  echo "   To regenerate, delete the file and re-run."
  SKIP_WORKFLOW=true
fi

# ── Step 1: Generate .release-config.yml ──────────────────────────────────────
if [[ "$SKIP_CONFIG" == "false" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${CYAN}[DRY-RUN]${RESET} Would create: ${CONFIG_FILE}"
  else
    cat > "$CONFIG_FILE" << CONFIGEOF
# ─────────────────────────────────────────────────────────────────────────────
# RDK-B Bi-Weekly Release Configuration
# Component: ${COMPONENT_REPO}
# Generated by: rdkb-release-agent/adopt.sh
# ─────────────────────────────────────────────────────────────────────────────

# Target release version (update each cycle)
version: "${VERSION}"

# Component identity (shown in release reports)
component_name: "${COMPONENT_NAME}"

# Base integration branch
base_branch: "${BASE_BRANCH}"

# Strategy: "exclude" (take all PRs except listed) or "include" (take only listed)
strategy: "${STRATEGY}"

# PRs to exclude/include (update each cycle)
prs:
  # - 205   # Example: WIP — not ready for release

# Dry run mode (set false for real releases)
dry_run: false

# Conflict policy: "pause" (halt on conflict) or "skip" (skip conflicting PRs)
conflict_policy: "pause"

# Smart conflict resolution (auto-resolves merge conflicts in C source files)
conflict_resolution:
  smart_merge: true          # Enable semantic-aware merge
  min_confidence: "low"      # Accept all confidence levels
  safety_prefer: true        # Prefer side with NULL checks, error handling

# LLM-powered conflict resolution (fallback for functional conflicts)
llm:
  enabled: false                 # Set true to enable LLM resolution
  provider: "openai"             # Any provider: openai, gemini, claude, ollama, etc.
  model: "gpt-4o-mini"           # Model name
  api_key_env: "OPENAI_API_KEY"  # Secret containing the API key (if required)
  endpoint: ""                   # Custom endpoint URL (for local models/proxies)
  temperature: 0.1
  max_calls_per_run: 5
  timeout_seconds: 10
# GitHub handles to notify in the release PR
notify:
  - "@${COMPONENT_NAME}-maintainer"
CONFIGEOF
    echo -e "  ${GREEN}✅  Created: ${CONFIG_FILE}${RESET}"
  fi
fi

# ── Step 2: Generate Trigger Script ───────────────────────────────────────────
TRIGGER_FILE="trigger.sh"

if [[ "$SKIP_WORKFLOW" == "false" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${CYAN}[DRY-RUN]${RESET} Would create: ${TRIGGER_FILE}"
  else
    cat > "$TRIGGER_FILE" << TRIGGERSCRIPT
#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# trigger.sh
# 
# Local bootstrap script for the RDK-B Release Agent. 
# Executed by the component repository CI to clone the agent and run release.
# ─────────────────────────────────────────────────────────────────────────────

set -e

echo "🚀 Bootstrapping RDK-B Release Agent..."

AGENT_REPO="rdkcentral/rdkb-release-agent"
DRY_RUN_FLAG=""
VERSION_ARG=""
CONFIG_FILE=".release-config.yml"

# Parse arguments
while [[ "\$#" -gt 0 ]]; do
    case \$1 in
        --agent-repo) AGENT_REPO="\$2"; shift ;;
        --version) VERSION_ARG="\$2"; shift ;;
        --dry-run) [[ "\$2" == "true" ]] && DRY_RUN_FLAG="--dry-run"; shift ;;
        --config) CONFIG_FILE="\$2"; shift ;;
    esac
    shift
done

# Extract version from local component config if not passed
if [[ -z "\$VERSION_ARG" ]] && [[ -f "\$CONFIG_FILE" ]]; then
  VERSION_ARG=\$(grep '^version:' "\$CONFIG_FILE" | awk '{print \$2}' | tr -d '"')
fi

# 1. Download the agent directly into the GitHub Actions runner
echo "📥 Cloning agent framework from \$AGENT_REPO..."
rm -rf _release-agent
git clone --depth 1 "https://github.com/\${AGENT_REPO}.git" _release-agent
chmod +x _release-agent/scripts/*.sh

# 2. Run the orchestrator with the repository context
echo "⚙️ Running orchestrator for version \$VERSION_ARG..."
python3 _release-agent/scripts/orchestrate_release.py \\
  --repo "\${GITHUB_REPOSITORY:-local/repo}" \\
  --config "\$CONFIG_FILE" \\
  --version "\$VERSION_ARG" \\
  \$DRY_RUN_FLAG
TRIGGERSCRIPT
    chmod +x "$TRIGGER_FILE"
    echo -e "  ${GREEN}✅  Created: ${TRIGGER_FILE}${RESET}"
  fi
fi

# ── Step 3: Generate GitHub Actions Workflow ──────────────────────────────────
if [[ "$SKIP_WORKFLOW" == "false" ]]; then
  if [[ "$DRY_RUN" == "true" ]]; then
    echo -e "${CYAN}[DRY-RUN]${RESET} Would create: ${WORKFLOW_FILE}"
  else
    mkdir -p "$WORKFLOW_DIR"
    cat > "$WORKFLOW_FILE" << 'WFEOF'
# ─────────────────────────────────────────────────────────────────────────────
# RDK-B Bi-Weekly Release Agent
# Auto-generated by rdkb-release-agent/adopt.sh
#
# This workflow clones the shared release framework at runtime and runs the
# orchestrator against THIS component repo. No framework code is stored here.
#
# Trigger:
# 1. Pushing changes to .release-config.yml
# 2. Actions → "RDK-B Bi-Weekly Release Agent" → Run workflow
# ─────────────────────────────────────────────────────────────────────────────
name: "RDK-B Bi-Weekly Release Agent"

on:
  push:
    paths:
      - '.release-config.yml'
  workflow_dispatch:
    inputs:
      version:
        description: "Release version (e.g. 2.4.0)"
        required: true
        type: string
      dry_run:
        description: "Dry run (simulate only)"
        required: false
        default: "false"
        type: choice
        options:
          - "false"
          - "true"
      config_file:
        description: "Config file path"
        required: false
        default: ".release-config.yml"
        type: string

permissions:
  contents: write
  pull-requests: write

jobs:
  release-agent:
    name: "Release ${{ inputs.version }}"
    runs-on: ubuntu-latest

    steps:
      # ── 1. Checkout component repo ──────────────────────────────────────
      - name: Checkout component repo
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

      # ── 2. Set up Python ────────────────────────────────────────────────
      - name: Set up Python
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install pyyaml

      # ── 3. Configure Git ────────────────────────────────────────────────
      - name: Configure Git
        run: |
          git config user.name  "RDK-B Release Agent"
          git config user.email "rdkb-release-agent@noreply.github.com"
WFEOF

    # Write the dynamic parts (agent repo variable)
    cat >> "$WORKFLOW_FILE" << WFEOF2

      # ── 4. Trigger central release execution ────────────────────────────
      - name: Trigger Release Agent
        env:
          GH_TOKEN: \${{ secrets.GITHUB_TOKEN }}
        run: |
          ./trigger.sh \\
            --agent-repo "${AGENT_REPO}" \\
            --version "\${{ inputs.version }}" \\
            --dry-run "\${{ inputs.dry_run }}" \\
            --config ".release-config.yml"
WFEOF2

    cat >> "$WORKFLOW_FILE" << 'WFEOF3'

      # ── 5. Publish report ────────────────────────────────────────────────
      - name: Publish release report
        if: always()
        run: |
          REPORT="release-report-${{ inputs.version }}.md"
          if [[ -f "$REPORT" ]]; then
            cat "$REPORT" >> $GITHUB_STEP_SUMMARY
          else
            echo "⚠️ Report not generated." >> $GITHUB_STEP_SUMMARY
          fi

      # ── 6. Upload artifacts ──────────────────────────────────────────────
      - name: Upload release artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: release-report-${{ inputs.version }}
          path: |
            release-report-*.md
            /tmp/rdkb-release-conflicts/
WFEOF3
    echo -e "  ${GREEN}✅  Created: ${WORKFLOW_FILE}${RESET}"
  fi
fi

# ── Next Steps ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}════════════════════════════════════════════════════════════════${RESET}"
echo -e "${BOLD}  ✅ Adoption Complete!${RESET}"
echo -e "${BOLD}════════════════════════════════════════════════════════════════${RESET}"
echo ""
echo -e "  ${BOLD}Next Steps:${RESET}"
echo ""
echo -e "  ${CYAN}1.${RESET} Edit ${GREEN}.release-config.yml${RESET}:"
echo "     - Update the 'prs' list for this release cycle"
echo "     - Set strategy to 'include' or 'exclude' as needed"
echo ""
echo -e "  ${CYAN}2.${RESET} Commit and push:"
echo -e "     ${GREEN}git add .release-config.yml trigger.sh .github/workflows/rdkb-biweekly-release.yml${RESET}"
echo -e "     ${GREEN}git commit -m 'chore: adopt rdkb-release-agent for bi-weekly releases'${RESET}"
echo -e "     ${GREEN}git push origin ${BASE_BRANCH}${RESET}"
echo ""
echo -e "  ${CYAN}3.${RESET} Run the release:"
echo "     Go to GitHub → Actions → 'RDK-B Bi-Weekly Release Agent'"
echo "     → Run workflow → enter version: ${VERSION}"
echo ""
echo -e "  ${CYAN}Or run locally:${RESET}"
echo "     git clone https://github.com/${AGENT_REPO}.git /tmp/release-agent"
echo "     python3 /tmp/release-agent/scripts/orchestrate_release.py \\"
echo "       --repo ${COMPONENT_REPO} --version ${VERSION} --dry-run"
echo ""
echo -e "  📖 Full docs: ${CYAN}https://github.com/${AGENT_REPO}#readme${RESET}"
echo ""
