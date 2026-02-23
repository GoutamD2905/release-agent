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
from pr_level_resolver import PRLevelResolver, check_for_conflicts, ResolutionAction
from pr_discovery import (
    discover_prs_since_tag,
    validate_pr_dependencies,
    print_discovery_summary,
    print_dependency_warnings
)
from logger import init_logger
from report_generator import ReportGenerator, ReleaseReport
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

# ── Initialize Logging ────────────────────────────────────────────────────────
logger = init_logger(COMPONENT_NAME, VERSION)
logger.info("=" * 60)
logger.info(f"Release Orchestrator Started")
logger.info(f"Component: {COMPONENT_NAME}, Version: {VERSION}")
logger.info(f"Strategy: {STRATEGY}, Dry Run: {DRY_RUN}")
logger.info("=" * 60)
print(f"\n  📋 Log file: {logger.get_log_file()}")


# ── Smart PR Discovery ────────────────────────────────────────────────────────
section(0, "Smart PR Discovery & Validation")

# Auto-discover PRs from git history
logger.info("Starting PR auto-discovery from git history")
discovery_result = discover_prs_since_tag(base_branch=BASE_BRANCH, repo_path=".")
if discovery_result:
    print_discovery_summary(discovery_result, CONFIGURED_PRS, STRATEGY)
    logger.info(f"Discovered {len(discovery_result.all_prs)} PRs since tag {discovery_result.last_tag}")
    
    # Store discovered PRs for later use
    all_discovered_prs = discovery_result.all_prs
else:
    print(f"  {warn('Could not auto-discover PRs - proceeding with configured list')}")
    logger.warning("Could not auto-discover PRs from git history")
    all_discovered_prs = CONFIGURED_PRS

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 1: RULE-BASED CONFLICT DETECTION
# ══════════════════════════════════════════════════════════════════════════════
section(1, "PHASE 1: Rule-Based Conflict Detection")

print(f"\n  📥 INPUTS:")
print(f"  ├─ Strategy: {STRATEGY.upper()}")
print(f"  ├─ Last Tag: {discovery_result.last_tag if discovery_result else 'N/A'}")
print(f"  ├─ PRs Merged Since Tag: {len(all_discovered_prs) if all_discovered_prs else 0}")
print(f"  └─ Configured PRs: {CONFIGURED_PRS}")

print(f"\n  🔄 PROCESSING:")

# Get PR list based on strategy
if STRATEGY == "include":
    pr_list = CONFIGURED_PRS
    print(f"  ├─ Mode: INCLUDE strategy")
    print(f"  ├─ Cherry-picking PRs: {pr_list}")
    print(f"  └─ Total PRs to process: {len(pr_list)}")
else:
    # Exclude strategy: use all discovered PRs minus excluded ones
    if all_discovered_prs:
        pr_list = [pr for pr in all_discovered_prs if pr not in CONFIGURED_PRS]
        print(f"  ├─ Mode: EXCLUDE strategy")
        print(f"  ├─ Total discovered: {len(all_discovered_prs)} PRs")
        print(f"  ├─ Excluding: {CONFIGURED_PRS}")
        print(f"  └─ PRs to process: {len(pr_list)}")
    else:
        print(f"  {warn('No PRs discovered - cannot use exclude strategy')}")
        pr_list = []

# Run rule-based conflict analyzer
print(f"\n  🔍 Running Conflict Detection Engine...")
logger.info(f"Phase 1: Analyzing {len(pr_list)} PRs for conflicts")
analyzer = PRConflictAnalyzer(args.repo)
analysis_results = analyzer.analyze(pr_list)
logger.info(f"Conflict analysis complete: {len(analysis_results.get('conflicts', {}).get('all', []))} conflicts detected")

# Save analysis results
analysis_file = Path("/tmp/rdkb-release-conflicts/conflict_analysis.json")
analysis_file.parent.mkdir(parents=True, exist_ok=True)
with open(analysis_file, "w") as f:
    json.dump(analysis_results, f, indent=2)

# Extract high-severity conflicts
critical_conflicts = analysis_results["conflicts"]["by_severity"]["critical"]
medium_conflicts = analysis_results["conflicts"]["by_severity"]["medium"]
low_conflicts = analysis_results["conflicts"]["by_severity"]["low"]
all_conflicts = analysis_results["conflicts"]["all"]

# PHASE 1 OUTPUT SUMMARY
print(f"\n  📤 OUTPUTS:")
print(f"  ├─ Total Conflicts Detected: {len(all_conflicts)}")
print(f"  │  ├─ 🔴 CRITICAL: {len(critical_conflicts)}")
print(f"  │  ├─ 🟡 MEDIUM: {len(medium_conflicts)}")
print(f"  │  └─ 🟢 LOW: {len(low_conflicts)}")
print(f"  ├─ PRs Analyzed: {len(pr_list)}")
print(f"  ├─ PRs with Conflicts: {len(set(c['pr_number'] for c in all_conflicts))}")
print(f"  └─ Analysis Report: {analysis_file}")

if critical_conflicts:
    print(f"\n  🔴 CRITICAL CONFLICTS DETAILS:")
    for i, conflict in enumerate(critical_conflicts[:5], 1):
        print(f"  {i}. PR #{conflict['pr_number']}: {conflict['reason']}")
        if conflict.get('shared_files'):
            print(f"     Files: {', '.join(conflict['shared_files'][:3])}")
    if len(critical_conflicts) > 5:
        print(f"  ... and {len(critical_conflicts) - 5} more")

if medium_conflicts:
    print(f"\n  🟡 MEDIUM CONFLICTS: {len(medium_conflicts)} detected")
    for conflict in medium_conflicts[:2]:
        print(f"  • PR #{conflict['pr_number']}: {conflict['reason'][:60]}...")

print(f"\n  ✅ Phase 1 Complete - Conflicts identified and categorized")
print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 2: LLM-BASED INTELLIGENT CONFLICT RESOLUTION
# ══════════════════════════════════════════════════════════════════════════════
section(2, "PHASE 2: LLM-Based Intelligent Conflict Resolution")

# Check if LLM is enabled
llm_enabled = cfg.get("llm", {}).get("enabled", False)

print(f"\n  📥 INPUTS FROM PHASE 1:")
print(f"  ├─ Total PRs Analyzed: {len(pr_list)}")
print(f"  ├─ Conflicts Detected: {len(all_conflicts)}")
print(f"  ├─ PRs Requiring Decisions: {len(set(c['pr_number'] for c in all_conflicts))}")
print(f"  └─ LLM Enabled: {llm_enabled}")

print(f"\n  🔄 PROCESSING:")

if not llm_enabled:
    print(f"  {warn('⚠️  LLM is NOT enabled in config')}")
    print(f"  ⚠️  Conflicts will require manual resolution")
    print(f"  ℹ️  To enable: Set 'llm.enabled: true' in config")
    decision_maker = None
else:
    try:
        decision_maker = LLMPRDecisionMaker(cfg)
        llm_provider = cfg.get("llm", {}).get("provider", "unknown")
        llm_model = cfg.get("llm", {}).get("model", "unknown")
        print(f"  ✅ LLM Engine Initialized")
        print(f"  ├─ Provider: {llm_provider}")
        print(f"  ├─ Model: {llm_model}")
        print(f"  └─ Capabilities: Context Building, Conflict Resolution, Strategic Decisions")
    except Exception as e:
        print(f"  {err(f'❌ Failed to initialize LLM: {e}')}")
        decision_maker = None

# For each PR with conflicts, get LLM decision
pr_decisions = {}

if decision_maker:
    # Get PRs that have conflicts
    conflicted_prs = set()
    for conflict in analysis_results["conflicts"]["all"]:
        conflicted_prs.add(conflict["pr_number"])
        conflicted_prs.update(conflict.get("conflicting_with", []))
    
    print(f"\n  🤖 LLM INTELLIGENT ANALYSIS:")
    print(f"  ├─ Building Context: PR metadata, diffs, conflicts, dependencies")
    print(f"  ├─ Analyzing: {len(conflicted_prs)} PRs with potential conflicts")
    print(f"  ├─ Applying: Continuous learning from past resolutions")
    print(f"  └─ Strategy: Risk assessment, impact analysis, dependency resolution")
    
    print(f"\n  🔍 Evaluating Each PR:")
    logger.info(f"Phase 2: LLM evaluating {len(conflicted_prs)} conflicted PRs")
    
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
        pr_conflicts = [conflict for conflict in analysis_results["conflicts"]["all"] 
                       if conflict["pr_number"] == pr_num]
        
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

# PHASE 2 OUTPUT SUMMARY
include_count = sum(1 for d in pr_decisions.values() if d.decision == "INCLUDE")
exclude_count = sum(1 for d in pr_decisions.values() if d.decision == "EXCLUDE")
manual_count = sum(1 for d in pr_decisions.values() if d.decision == "MANUAL_REVIEW")
high_confidence = sum(1 for d in pr_decisions.values() if d.confidence == "HIGH")

print(f"\n  📤 PHASE 2 OUTPUTS:")
print(f"  ├─ LLM Decisions Made: {len(pr_decisions)}")
print(f"  │  ├─ ✅ INCLUDE: {include_count} PRs")
print(f"  │  ├─ ⏭️  EXCLUDE: {exclude_count} PRs")
print(f"  │  └─ 🔍 MANUAL_REVIEW: {manual_count} PRs")
print(f"  ├─ High Confidence Decisions: {high_confidence}/{len(pr_decisions)}")
print(f"  └─ Decisions Report: {decisions_file}")

if include_count > 0:
    print(f"\n  ✅ PRs RECOMMENDED FOR INCLUSION:")
    for pr_num, decision in sorted(pr_decisions.items()):
        if decision.decision == "INCLUDE":
            pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
            pr_title = pr_meta.get("title", "N/A")[:60]
            print(f"  • PR #{pr_num}: {pr_title}")
            print(f"    Confidence: {decision.confidence} | Rationale: {decision.rationale[:80]}...")

if exclude_count > 0:
    print(f"\n  ⏭️  PRs RECOMMENDED FOR EXCLUSION:")
    for pr_num, decision in sorted(pr_decisions.items()):
        if decision.decision == "EXCLUDE":
            pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
            pr_title = pr_meta.get("title", "N/A")[:60]
            print(f"  • PR #{pr_num}: {pr_title}")
            print(f"    Reason: {decision.rationale[:80]}...")

print(f"\n  ✅ Phase 2 Complete - LLM decisions finalized")
print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

# ── Dependency Validation ──────────────────────────────────────────────────────
section(2.5, "Dependency Validation & Recommendations")

# Validate dependencies if we have discovery results and LLM decisions
if discovery_result and pr_decisions:
    validation = validate_pr_dependencies(
        configured_prs=CONFIGURED_PRS,
        strategy=STRATEGY,
        all_prs=all_discovered_prs,
        llm_decisions=pr_decisions
    )
    
    print_dependency_warnings(validation)
    
    # Save validation results
    validation_file = Path("/tmp/rdkb-release-conflicts/dependency_validation.json")
    with open(validation_file, "w") as f:
        json.dump({
            "missing_dependencies": validation.missing_dependencies,
            "orphaned_dependencies": validation.orphaned_dependencies,
            "warnings": validation.warnings,
            "recommendations": validation.recommendations
        }, f, indent=2)
    print(f"\n  💾 Validation saved to: {validation_file}")
else:
    print(f"  {info('Skipping dependency validation (no LLM decisions or discovery data)')}")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 3: EXECUTE RELEASE OPERATIONS & CREATE DRAFT PR
# ══════════════════════════════════════════════════════════════════════════════
section(3, "Executing Release Operations & Creating Draft PR")

print(f"\n  📥 INPUTS FROM PHASE 2:")
print(f"  ├─ PRs to Include: {sum(1 for d in pr_decisions.values() if d.decision == 'INCLUDE') if pr_decisions else len(pr_list)}")
print(f"  ├─ PRs to Exclude: {sum(1 for d in pr_decisions.values() if d.decision == 'EXCLUDE') if pr_decisions else 0}")
print(f"  ├─ Conflict Resolution Strategy: LLM-powered automatic resolution")
print(f"  └─ Target Branch: {RELEASE_BRANCH}")

if DRY_RUN:
    print(f"\n  {info('🔍 DRY RUN MODE - Simulating operations (no actual changes)')}")
    print(f"\n  📊 SIMULATION SUMMARY:")
    print(f"  ├─ Total PRs analyzed: {len(pr_list)}")
    print(f"  ├─ Conflicts detected: {len(analysis_results['conflicts']['all'])}")
    print(f"  └─ LLM decisions made: {len(pr_decisions)}")
    
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
logger.info(f"Creating release branch: {RELEASE_BRANCH} from {BASE_BRANCH}")
logger.info(f"Creating release branch: {RELEASE_BRANCH} from {BASE_BRANCH}")
result = subprocess.run(
    ["git", "checkout", "-b", RELEASE_BRANCH, BASE_BRANCH],
    capture_output=True, text=True
)

if result.returncode != 0 and "already exists" not in result.stderr:
    print(err(f"Failed to create branch: {result.stderr}"))
    sys.exit(1)

print(f"\n  🔄 PROCESSING:")
print(f"  ├─ Creating release branch: {RELEASE_BRANCH}")
print(f"  ├─ Cherry-picking/reverting PRs based on LLM decisions")
print(f"  ├─ Auto-resolving conflicts using LLM intelligence")
print(f"  └─ Preparing draft PR for component review")

# Create PR-level resolver
mode = "cherry-pick" if STRATEGY == "include" else "revert"
resolver = PRLevelResolver(mode, decision_maker, cfg)

# Process each PR
successful_prs = []
failed_prs = []
skipped_prs = []
conflicts_resolved = 0

print(f"\n  📝 Processing {len(pr_list)} PRs:")

for pr_num in pr_list:
    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
    pr_title = pr_meta.get("title", "Unknown")[:70]
    print(f"  🔹 PR #{pr_num}: {pr_title}")
    print(f"  Author: {pr_meta.get('author', 'unknown')} | Files: {len(pr_meta.get('files_changed', []))}")
    
    # Check if we have an LLM decision
    if pr_num in pr_decisions:
        decision = pr_decisions[pr_num]
        print(f"  LLM Decision: {decision.decision} (Confidence: {decision.confidence})")
        
        if decision.decision == "EXCLUDE":
            print(f"  ⏭️  • EXCLUDED (per LLM recommendation)")
            print(f"  • Reason: {decision.rationale}")
            skipped_prs.append(pr_num)
            continue
    
    # Get commit SHA from PR metadata
    commit_sha = pr_meta.get("merge_commit_sha", "")
    if not commit_sha:
        print(f"  ❌ • ERROR: Could not find merge commit SHA")
        failed_prs.append(pr_num)
        continue
    
    print(f"  🔄 Attempting {mode}...")
    
    # Attempt operation with automatic conflict resolution
    action = ResolutionAction(
        pr_number=pr_num,
        action="INCLUDE",
        reason="Attempting to include PR",
        depends_on=[]
    )
    
    # Apply with LLM conflict resolution
    success = resolver.apply_action(action, commit_sha, pr_num, pr_meta)
    
    if success:
        print(f"  ✅ • SUCCESS: PR applied to {RELEASE_BRANCH}")
        successful_prs.append(pr_num)
    else:
        # Check for conflicts
        conflict_files = check_for_conflicts()
        
        if conflict_files:
            print(f"  🚨 • CONFLICT detected in {len(conflict_files)} files")
            print(f"  🤖 Invoking LLM conflict resolution...")
            
            # Use PR-level resolver for strategic decision
            action = resolver.handle_conflict(
                pr_number=pr_num,
                pr_metadata=pr_meta,
                conflict_files=conflict_files,
                all_prs_metadata=analysis_results["pr_metadata"],
                detected_conflicts=analysis_results["conflicts"]["all"]
            )
            
            # Apply the action with conflict resolution
            if resolver.apply_action(action, commit_sha, pr_num, pr_meta):
                if action.action == "INCLUDE":
                    print(f"  ✅ • RESOLVED: Conflicts auto-resolved by LLM")
                    successful_prs.append(pr_num)
                    conflicts_resolved += len(conflict_files)
                else:
                    print(f"  ⏭️  • SKIPPED: {action.reason}")
                    skipped_prs.append(pr_num)
            elif action.action == "MANUAL":
                print(f"  🔴 • MANUAL REVIEW REQUIRED")
                print(f"  • Reason: {action.reason}")
                failed_prs.append(pr_num)
            else:
                print(f"  ❌ • FAILED: {action.reason}")
                failed_prs.append(pr_num)
        else:
            print(f"  ❌ • FAILED: Operation failed without conflicts")
            failed_prs.append(pr_num)

# PHASE 3 OUTPUT SUMMARY
print(f"\n  📤 PHASE 3 OUTPUTS:")
print(f"  ├─ Total PRs Processed: {len(pr_list)}")
print(f"  │  ├─ ✅ Successful: {len(successful_prs)} PRs")
print(f"  │  ├─ ⏭️  Skipped: {len(skipped_prs)} PRs")
print(f"  │  └─ 🔴 Failed/Manual: {len(failed_prs)} PRs")
print(f"  ├─ Conflicts Auto-Resolved: {conflicts_resolved}")
print(f"  ├─ Release Branch: {RELEASE_BRANCH}")
print(f"  └─ Status: {'Ready for Draft PR' if successful_prs else 'No PRs to release'}")

if successful_prs:
    print(f"\n  ✅ SUCCESSFULLY APPLIED PRs:")
    for pr_num in successful_prs:
        pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
        pr_title = pr_meta.get("title", "N/A")[:60]
        print(f"  • PR #{pr_num}: {pr_title}")

if failed_prs:
    print(f"\n  🔴 REQUIRES MANUAL REVIEW:")
    for pr_num in failed_prs:
        pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
        pr_title = pr_meta.get("title", "N/A")[:60]
        print(f"  • PR #{pr_num}: {pr_title}")

print(f"\n  ✅ Phase 3 Complete - Release operations finished")
print(f"  ═════════════════════════════════════════════════════════════")

# ── Summary ───────────────────────────────────────────────────────────────────
elapsed = time.time() - START_TIME
banner("Release Operation Complete")
print(f"  {'Total Time':<20}: {elapsed:.1f}s")
print(f"  {'Successful PRs':<20}: {len(successful_prs)}")
print(f"  {'Skipped PRs':<20}: {len(skipped_prs)}")
print(f"  {'Failed/Manual PRs':<20}: {len(failed_prs)}")
print(f"  {'Conflicts Resolved':<20}: {conflicts_resolved}")

if successful_prs:
    print(f"\n  ✅ Successful: {successful_prs}")
if skipped_prs:
    print(f"\n  ⏭️  Skipped: {skipped_prs}")
if failed_prs:
    print(f"\n  ❌ Needs Manual Review: {failed_prs}")

print(c(BOLD, "═" * 64))

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4: CREATE DRAFT PULL REQUEST FOR COMPONENT REVIEW
# ══════════════════════════════════════════════════════════════════════════════
if successful_prs and not DRY_RUN:
    section(4, "Creating Draft Pull Request for Component Review")
    
    print(f"\n  📥 INPUTS:")
    print(f"  ├─ Successful PRs: {len(successful_prs)}")
    print(f"  ├─ Conflicts Resolved: {conflicts_resolved}")
    print(f"  ├─ Release Branch: {RELEASE_BRANCH}")
    print(f"  └─ Target Base: {BASE_BRANCH}")
    
    print(f"\n  🔄 PROCESSING:")
    
    # Push the release branch
    print(f"  📤 Step 1: Pushing {RELEASE_BRANCH} to remote...")
    push_result = subprocess.run(
        ["git", "push", "-u", "origin", RELEASE_BRANCH],
        capture_output=True, text=True
    )
    
    if push_result.returncode == 0:
        print(f"  ✅ Branch pushed successfully")
        
        # Create draft PR
        print(f"\n  📝 Step 2: Creating draft pull request...")
        print(f"  ├─ Building comprehensive PR description")
        print(f"  ├─ Including all LLM decisions and analysis")
        print(f"  └─ Adding detailed summary for component owner")
        
        # Build PR body with summary
        pr_body = f"""# Release {VERSION}

## Summary
- **Strategy**: {STRATEGY.upper()}
- **Base Branch**: {BASE_BRANCH}
- **PRs Included**: {len(successful_prs)}
- **PRs Skipped**: {len(skipped_prs)}
- **LLM Decisions**: {len(pr_decisions)}

## Included PRs
"""
        for pr_num in successful_prs:
            pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
            pr_title = pr_meta.get("title", f"PR #{pr_num}")
            pr_body += f"- #{pr_num}: {pr_title}\\n"
        
        if skipped_prs:
            pr_body += f"\\n## Skipped PRs\\n"
            for pr_num in skipped_prs:
                pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
                pr_title = pr_meta.get("title", f"PR #{pr_num}")
                if pr_num in pr_decisions:
                    reason = pr_decisions[pr_num].rationale[:100]
                    pr_body += f"- #{pr_num}: {pr_title} (Reason: {reason})\\n"
                else:
                    pr_body += f"- #{pr_num}: {pr_title}\\n"
        
        if failed_prs:
            pr_body += f"\\n## ⚠️ Requires Manual Review\\n"
            for pr_num in failed_prs:
                pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
                pr_title = pr_meta.get("title", f"PR #{pr_num}")
                pr_body += f"- #{pr_num}: {pr_title}\\n"
        
        pr_body += f"""
## LLM Analysis
- **Conflicts Detected**: {len(analysis_results.get('conflicts', {}).get('all', []))}
- **Critical Conflicts**: {len(analysis_results.get('conflicts', {}).get('by_severity', {}).get('critical', []))}
- **LLM Decisions Made**: {len(pr_decisions)}

## Next Steps
1. Review the included PRs and LLM decisions
2. Test the release branch thoroughly
3. If approved, convert this draft to a ready PR and merge

---
*This PR was automatically created by the Release Agent with LLM-powered conflict resolution*
"""
        
        # Create the draft PR using gh CLI
        pr_title = f"Release {VERSION}"
        create_pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--base", BASE_BRANCH,
             "--head", RELEASE_BRANCH,
             "--title", pr_title,
             "--body", pr_body,
             "--draft",
             "--repo", args.repo],
            capture_output=True, text=True
        )
        
        if create_pr_result.returncode == 0:
            pr_url = create_pr_result.stdout.strip()
            print(f"\n  📤 PHASE 4 OUTPUT:")
            print(f"  ├─ Draft PR Created: ✅")
            print(f"  ├─ PR URL: {pr_url}")
            print(f"  ├─ Status: Ready for Component Owner Review")
            print(f"  └─ Next Step: Component owner to review and approve")
            print(f"\n  ✅ Draft PR created successfully!")
            print(f"  🔗 {pr_url}")
            logger.info(f"Draft PR created: {pr_url}")
            
            print(f"\n  ✅ Phase 4 Complete - Draft PR created for review")
            print(f"  ═════════════════════════════════════════════════════════════")
        else:
            print(f"\n  ❌ Failed to create draft PR")
            print(f"  Error: {create_pr_result.stderr}")
            logger.warning(f"Failed to create draft PR: {create_pr_result.stderr}")
    else:
        print(f"\n  ❌ Failed to push branch")
        print(f"  Error: {push_result.stderr}")
        logger.warning(f"Failed to push branch: {push_result.stderr}")
elif DRY_RUN:
    print(f"\n  ℹ️  DRY RUN MODE: Skipping draft PR creation")
    print(f"  ℹ️  In production, this would create a draft PR with:")
    print(f"  ├─ {len(successful_prs)} included PRs")
    print(f"  ├─ {conflicts_resolved} auto-resolved conflicts")
    print(f"  └─ Full summary for component owner review")
else:
    print(f"\n  ℹ️  No successful PRs to create draft PR")
    print(f"  ℹ️  All PRs were either skipped or failed")

# ── Generate Comprehensive Report ─────────────────────────────────────────────
logger.info("Generating comprehensive release report...")
print(f"\n  📄 Generating comprehensive report...")

# Collect validation data
validation_warnings = []
validation_recommendations = []
missing_deps = {}
if 'validation' in locals():
    validation_warnings = validation.warnings
    validation_recommendations = validation.recommendations
    missing_deps = validation.missing_dependencies

# Build report data
report_data = ReleaseReport(
    component_name=COMPONENT_NAME,
    version=VERSION,
    strategy=STRATEGY,
    base_branch=BASE_BRANCH,
    release_branch=RELEASE_BRANCH,
    last_tag=discovery_result.last_tag if discovery_result else None,
    total_prs_discovered=len(all_discovered_prs) if all_discovered_prs else 0,
    prs_configured=CONFIGURED_PRS,
    conflicts_detected=len(analysis_results.get('conflicts', {}).get('all', [])),
    conflicts_critical=len(analysis_results.get('conflicts', {}).get('by_severity', {}).get('critical', [])),
    conflicts_medium=len(analysis_results.get('conflicts', {}).get('by_severity', {}).get('medium', [])),
    conflicts_low=len(analysis_results.get('conflicts', {}).get('by_severity', {}).get('low', [])),
    llm_decisions={
        k: {
            'decision': v.decision,
            'confidence': v.confidence,
            'rationale': v.rationale,
            'requires_prs': v.requires_prs,
            'risks': v.risks,
            'benefits': v.benefits
        } for k, v in pr_decisions.items()
    } if pr_decisions else {},
    prs_to_include=[k for k, v in pr_decisions.items() if v.decision == "INCLUDE"],
    prs_to_exclude=[k for k, v in pr_decisions.items() if v.decision == "EXCLUDE"],
    prs_manual_review=[k for k, v in pr_decisions.items() if v.decision == "MANUAL_REVIEW"],
    dependency_warnings=validation_warnings,
    dependency_recommendations=validation_recommendations,
    missing_dependencies=missing_deps,
    successful_prs=successful_prs,
    failed_prs=failed_prs,
    skipped_prs=skipped_prs,
    execution_time=elapsed,
    dry_run=DRY_RUN,
    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
)

# Generate report
report_gen = ReportGenerator()
report_file = report_gen.generate_report(report_data)

print(f"  ✅ Comprehensive report generated: {report_file}")
logger.info(f"Report generated: {report_file}")

print(f"\n  📋 Review the report for complete analysis and recommendations")
print(c(BOLD, "═" * 64))

sys.exit(0 if not failed_prs else 1)
