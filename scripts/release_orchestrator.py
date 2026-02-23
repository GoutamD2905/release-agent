#!/usr/bin/env python3
"""
release_orchestrator.py
=======================
Main release orchestrator using the two-phase hybrid intelligence approach:

PHASE 1: Rule-Based Intelligence
  - Detect PR list based on strategy
  - Identify file overlaps, timing conflicts, critical files
  - Analyze code patterns (NULL checks, error handling, safety improvements)
  - NO resolution — just detection and flagging

PHASE 2: LLM-Based Intelligence  
  - Deep semantic analysis of flagged PRs
  - Make strategic decisions: Include PR or Exclude PR
  - Consider dependencies, risks, benefits
  - Use code pattern analysis for better context
  - NO code-level merging — binary PR decisions only

RESOLUTION STRATEGY:
  - Cherry-pick/revert entire PRs (all or nothing)
  - If conflict occurs → LLM decides: include, exclude, or manual review
  - No partial merging of code hunks
  - Dependencies automatically resolved by including required PRs

Usage:
  python3 release_orchestrator.py \
    --repo rdkcentral/rdkb-component \
    --config .release-config.yml \
    [--dry-run]
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path
from datetime import datetime

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

# Add scripts directory to path
SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPTS_DIR))

from pr_conflict_analyzer import PRConflictAnalyzer
from llm_pr_decision import LLMPRDecisionMaker
from pr_level_resolver import PRLevelResolver, check_for_conflicts
from utils import BOLD, DIM, c, ok, warn, err, info, dim, banner, section as _section

START_TIME = time.time()


def section(step, title):
    """Section helper with start time."""
    _section(step, title, START_TIME)


# ── Parse Arguments ───────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="Refined RDK-B Release Orchestrator")
parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
parser.add_argument("--config", default=".release-config.yml")
parser.add_argument("--version", help="Override version from config")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

# ── Load Configuration ────────────────────────────────────────────────────────
config_path = Path(args.config)
if not config_path.exists():
    print(err(f"Config file not found: {config_path}"))
    sys.exit(1)

with open(config_path) as f:
    cfg = yaml.safe_load(f)

VERSION = args.version or cfg.get("version")
STRATEGY = cfg.get("strategy", "").lower()
CONFIGURED_PRS = [int(p) for p in cfg.get("prs") or []]
DRY_RUN = args.dry_run or cfg.get("dry_run", False)
RELEASE_BRANCH = cfg.get("release_branch", f"release/{VERSION}")
BASE_BRANCH = cfg.get("base_branch", "develop")
COMPONENT_NAME = cfg.get("component_name") or args.repo.split("/")[-1]

# Validate config
errors = []
if not VERSION:
    errors.append("'version' is required")
if STRATEGY not in ("exclude", "include"):
    errors.append(f"'strategy' must be 'exclude' or 'include' (got: '{STRATEGY}')")
if STRATEGY == "include" and not CONFIGURED_PRS:
    errors.append("'prs' list is REQUIRED when strategy is 'include'")
if errors:
    print(err("Configuration errors:"))
    for e in errors:
        print(f"   • {e}")
    sys.exit(1)

# ── Display Configuration ─────────────────────────────────────────────────────
banner("Refined Release Orchestrator (Two-Phase Intelligence)")
print(f"  {'Component':<16}: {COMPONENT_NAME}")
print(f"  {'Repo':<16}: {args.repo}")
print(f"  {'Version':<16}: {VERSION}")
print(f"  {'Strategy':<16}: {STRATEGY.upper()}")
print(f"  {'Base Branch':<16}: {BASE_BRANCH}")
print(f"  {'Release Branch':<16}: {RELEASE_BRANCH}")
print(f"  {'Dry Run':<16}: {DRY_RUN}")
print(c(BOLD, "═" * 64))

# ── PHASE 1: Rule-Based Conflict Detection ────────────────────────────────────
section(1, "PHASE 1: Rule-Based Conflict Detection")

print(f"  Strategy: {STRATEGY.upper()}")

# Get PR list based on strategy
if STRATEGY == "include":
    pr_list = CONFIGURED_PRS
    print(f"  Including PRs: {pr_list}")
else:
    # Get all PRs from base branch, exclude the configured ones
    print(f"  Fetching all PRs from {BASE_BRANCH}...")
    # TODO: Implement fetching all merged PRs
    # For now, use configured list as exclusion list
    pr_list = []  # Will be populated from git log
    print(f"  Excluding PRs: {CONFIGURED_PRS}")

# Run rule-based conflict analyzer
print(f"\n  🔍 Analyzing {len(pr_list)} PRs for conflicts...")
analyzer = PRConflictAnalyzer(args.repo)
analysis_results = analyzer.analyze(pr_list)

# Save analysis results
analysis_file = Path("/tmp/rdkb-release-conflicts/conflict_analysis.json")
analysis_file.parent.mkdir(parents=True, exist_ok=True)
with open(analysis_file, "w") as f:
    json.dump(analysis_results, f, indent=2)

print(f"\n  💾 Analysis saved to: {analysis_file}")

# Extract high-severity conflicts
critical_conflicts = analysis_results["conflicts"]["by_severity"]["critical"]
medium_conflicts = analysis_results["conflicts"]["by_severity"]["medium"]

if critical_conflicts:
    print(f"\n  {warn(f'Found {len(critical_conflicts)} CRITICAL conflicts')}")
    for c in critical_conflicts[:3]:
        print(f"    • PR #{c['pr_number']}: {c['reason']}")
    
if medium_conflicts:
    print(f"\n  {info(f'Found {len(medium_conflicts)} MEDIUM conflicts')}")

# ── PHASE 2: LLM-Based Strategic Decisions ────────────────────────────────────
section(2, "PHASE 2: LLM-Based Strategic Decision Making")

# Check if LLM is enabled
llm_enabled = cfg.get("llm", {}).get("enabled", False)

if not llm_enabled:
    print(f"  {warn('LLM is NOT enabled in config')}")
    print(f"  Conflicts will require manual resolution")
    decision_maker = None
else:
    try:
        decision_maker = LLMPRDecisionMaker(cfg)
        print(f"  ✅ LLM Decision Maker initialized")
    except Exception as e:
        print(f"  {err(f'Failed to initialize LLM: {e}')}")
        decision_maker = None

# For each PR with conflicts, get LLM decision
pr_decisions = {}

if decision_maker:
    # Get PRs that have conflicts
    conflicted_prs = set()
    for conflict in analysis_results["conflicts"]["all"]:
        conflicted_prs.add(conflict["pr_number"])
        conflicted_prs.update(conflict.get("conflicting_with", []))
    
    print(f"\n  🤖 Evaluating {len(conflicted_prs)} potentially conflicted PRs...")
    
    for pr_num in sorted(conflicted_prs):
        if pr_num not in analysis_results["pr_metadata"]:
            continue
        
        pr_meta = analysis_results["pr_metadata"][pr_num]
        
        # Get PR diff
        print(f"\n  Analyzing PR #{pr_num}...", end=" ")
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_num), "--repo", args.repo],
            capture_output=True, text=True, timeout=30
        )
        pr_diff = result.stdout if result.returncode == 0 else ""
        
        # Get conflicts for this PR
        pr_conflicts = [c for c in analysis_results["conflicts"]["all"] 
                       if c["pr_number"] == pr_num]
        
        # Get semantic analysis for this PR (if available)
        pr_semantic = analysis_results.get("pr_semantics", {}).get(pr_num)
        
        # Make decision
        decision = decision_maker.decide_pr(
            pr_number=pr_num,
            pr_metadata=pr_meta,
            pr_diff=pr_diff,
            conflicts=pr_conflicts,
            all_prs_metadata=analysis_results["pr_metadata"],
            semantic_info=pr_semantic
        )
        
        if decision:
            pr_decisions[pr_num] = decision
            emoji = "✅" if decision.decision == "INCLUDE" else "⏭️" if decision.decision == "EXCLUDE" else "🔍"
            print(f"{emoji} {decision.decision} ({decision.confidence})")
            print(f"    Rationale: {decision.rationale[:80]}...")
        else:
            print("❌ Decision failed")

# Save decisions
decisions_file = Path("/tmp/rdkb-release-conflicts/llm_decisions.json")
with open(decisions_file, "w") as f:
    json.dump({
        str(k): {
            "decision": v.decision,
            "confidence": v.confidence,
            "rationale": v.rationale,
            "requires_prs": v.requires_prs,
            "risks": v.risks,
            "benefits": v.benefits
        } for k, v in pr_decisions.items()
    }, f, indent=2)

print(f"\n  💾 Decisions saved to: {decisions_file}")

# ── PHASE 3: Execute Release Operations ───────────────────────────────────────
section(3, "Executing Release Operations")

if DRY_RUN:
    print(f"  {info('DRY RUN MODE - No actual git operations')}")
    print(f"\n  Summary:")
    print(f"    • Total PRs analyzed: {len(pr_list)}")
    print(f"    • Conflicts detected: {len(analysis_results['conflicts']['all'])}")
    print(f"    • LLM decisions made: {len(pr_decisions)}")
    
    if pr_decisions:
        include_count = sum(1 for d in pr_decisions.values() if d.decision == "INCLUDE")
        exclude_count = sum(1 for d in pr_decisions.values() if d.decision == "EXCLUDE")
        manual_count = sum(1 for d in pr_decisions.values() if d.decision == "MANUAL_REVIEW")
        
        print(f"    • INCLUDE: {include_count}")
        print(f"    • EXCLUDE: {exclude_count}")
        print(f"    • MANUAL_REVIEW: {manual_count}")
    
    sys.exit(0)

# Create release branch
print(f"\n  Creating release branch: {RELEASE_BRANCH}")
result = subprocess.run(
    ["git", "checkout", "-b", RELEASE_BRANCH, BASE_BRANCH],
    capture_output=True, text=True
)

if result.returncode != 0 and "already exists" not in result.stderr:
    print(err(f"Failed to create branch: {result.stderr}"))
    sys.exit(1)

# Create PR-level resolver
mode = "cherry-pick" if STRATEGY == "include" else "revert"
resolver = PRLevelResolver(mode, decision_maker)

# Process each PR
successful_prs = []
failed_prs = []
skipped_prs = []

for pr_num in pr_list:
    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  Processing PR #{pr_num}")
    
    # Check if we have an LLM decision
    if pr_num in pr_decisions:
        decision = pr_decisions[pr_num]
        
        if decision.decision == "EXCLUDE":
            print(f"  ⏭️  Skipping (LLM decided to EXCLUDE)")
            print(f"  Reason: {decision.rationale}")
            skipped_prs.append(pr_num)
            continue
    
    # Get commit SHA
    # TODO: Fetch actual commit SHA from PR
    commit_sha = "HEAD"  # Placeholder
    
    # Attempt operation
    if mode == "cherry-pick":
        cmd = ["git", "cherry-pick", commit_sha]
    else:
        cmd = ["git", "revert", "-m", "1", commit_sha]
    
    result = subprocess.run(cmd, capture_output=True, text=True)
    
    if result.returncode == 0:
        print(f"  ✅ Successfully applied")
        successful_prs.append(pr_num)
    else:
        # Check for conflicts
        conflict_files = check_for_conflicts()
        
        if conflict_files:
            print(f"  🚨 Conflict detected in {len(conflict_files)} files")
            
            # Use PR-level resolver
            pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
            action = resolver.handle_conflict(
                pr_number=pr_num,
                pr_metadata=pr_meta,
                conflict_files=conflict_files,
                all_prs_metadata=analysis_results["pr_metadata"],
                detected_conflicts=analysis_results["conflicts"]["all"]
            )
            
            # Apply the action
            if resolver.apply_action(action, commit_sha):
                if action.action == "INCLUDE":
                    successful_prs.append(pr_num)
                else:
                    skipped_prs.append(pr_num)
            elif action.action == "MANUAL":
                failed_prs.append(pr_num)
            else:
                failed_prs.append(pr_num)
        else:
            print(f"  ❌ Operation failed: {result.stderr}")
            failed_prs.append(pr_num)

# ── Summary ───────────────────────────────────────────────────────────────────
elapsed = time.time() - START_TIME
banner("Release Operation Complete")
print(f"  {'Total Time':<16}: {elapsed:.1f}s")
print(f"  {'Successful':<16}: {len(successful_prs)} PRs")
print(f"  {'Skipped':<16}: {len(skipped_prs)} PRs")
print(f"  {'Failed/Manual':<16}: {len(failed_prs)} PRs")

if successful_prs:
    print(f"\n  ✅ Successful: {successful_prs}")
if skipped_prs:
    print(f"\n  ⏭️  Skipped: {skipped_prs}")
if failed_prs:
    print(f"\n  ❌ Needs Manual Review: {failed_prs}")

print(c(BOLD, "═" * 64))

sys.exit(0 if not failed_prs else 1)
