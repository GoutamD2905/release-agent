#!/usr/bin/env python3
"""
release_orchestrator.py
=======================
Main release orchestrator for RDK-B components.

APPROACH: Rule-based workflow with LLM only for conflict resolution

WORKFLOW:
  1. PR Discovery (rule-based) - Find all PRs since last tag
  2. Operation Planning (rule-based) - Based on strategy & config
  3. Create Release Branch
  4. Execute Operations (cherry-pick/revert with LLM conflict resolution)
  5. Generate Comprehensive Report
  6. Push & Create Draft PR

STRATEGIES:
  - EXCLUDE: Start from develop, revert excluded PRs
  - INCLUDE: Start from develop, cherry-pick included PRs

LLM USAGE:
  - ONLY for resolving merge conflicts during cherry-pick/revert
  - Uses hybrid resolver: rule-based for simple conflicts, LLM for complex ones
  - NO LLM for PR selection decisions (purely configuration-driven)

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
COMPONENT_NAME = cfg.get("component_name") or args.repo.split("/")[-1]

# Both strategies: Start from develop, create release/x.x.x, merge to main
# - EXCLUDE: develop → revert excluded PRs → release/x.x.x → main
# - INCLUDE: develop → cherry-pick included PRs → release/x.x.x → main
DEFAULT_BASE_BRANCH = "develop"
DEFAULT_TARGET_BRANCH = "main"

BASE_BRANCH = cfg.get("base_branch", DEFAULT_BASE_BRANCH)
TARGET_BRANCH = cfg.get("target_branch", DEFAULT_TARGET_BRANCH)

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
print(f"  {'Create From':<16}: {BASE_BRANCH} (branch base)")
print(f"  {'Merge To':<16}: {TARGET_BRANCH} (draft PR target)")
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
section(1, "Smart PR Discovery from Git History")

# Auto-discover PRs from git history
logger.info("Starting PR auto-discovery from git history")
discovery_result = discover_prs_since_tag(base_branch=BASE_BRANCH, repo_path=".")
if discovery_result:
    print_discovery_summary(discovery_result, CONFIGURED_PRS, STRATEGY)
    logger.info(f"Discovered {len(discovery_result.all_prs)} PRs since tag {discovery_result.last_tag}")
    
    # Store discovered PRs for later use
    all_discovered_prs = discovery_result.all_prs
    last_tag = discovery_result.last_tag
else:
    print(f"  {warn('Could not auto-discover PRs - proceeding with configured list')}")
    logger.warning("Could not auto-discover PRs from git history")
    all_discovered_prs = CONFIGURED_PRS
    last_tag = None

# ── Resolve Operation Plan ────────────────────────────────────────────────────
section(2, "Resolving Operation Plan")

print(f"\n  📥 INPUTS:")
print(f"  ├─ Strategy: {STRATEGY.upper()}")
print(f"  ├─ Last Tag: {last_tag or 'N/A'}")
print(f"  ├─ PRs Since Tag: {len(all_discovered_prs) if all_discovered_prs else 0}")
print(f"  └─ Configured PRs: {CONFIGURED_PRS}")

print(f"\n  🔄 PLANNING:")

# Determine which PRs to operate on based on strategy
if STRATEGY == "exclude":
    # EXCLUDE: Take all discovered PRs, revert the configured ones
    excluded_prs = [pr for pr in CONFIGURED_PRS if pr in all_discovered_prs]
    not_found = [pr for pr in CONFIGURED_PRS if pr not in all_discovered_prs]
    intake_prs = [pr for pr in all_discovered_prs if pr not in excluded_prs]
    operation_prs = list(reversed(excluded_prs))  # Revert newest first
    operation_type = "revert"
    
    print(f"  ├─ Total PRs in window: {len(all_discovered_prs)}")
    print(f"  ├─ PRs to REVERT (exclude): {excluded_prs}")
    print(f"  ├─ PRs going into release: {intake_prs}")
    if not_found:
        print(f"  {warn(f'└─ PRs not found in window (ignored): {not_found}')}")
    else:
        print(f"  └─ All configured PRs found")
else:
    # INCLUDE: Cherry-pick only the configured PRs
    included_prs = [pr for pr in CONFIGURED_PRS if pr in all_discovered_prs]
    not_found = [pr for pr in CONFIGURED_PRS if pr not in all_discovered_prs]
    intake_prs = included_prs
    operation_prs = included_prs  # Cherry-pick oldest first
    operation_type = "cherry-pick"
    
    print(f"  ├─ Total PRs in window: {len(all_discovered_prs)}")
    print(f"  ├─ PRs to CHERRY-PICK (include): {included_prs}")
    if not_found:
        print(f"  {warn(f'└─ PRs not found in window (ignored): {not_found}')}")
    else:
        print(f"  └─ All configured PRs found")

print(f"\n  📤 OPERATION PLAN:")
print(f"  ├─ Operation: {operation_type.upper()}")
print(f"  ├─ PRs to process: {len(operation_prs)}")
print(f"  └─ PRs in final release: {len(intake_prs)}")

# Fetch PR metadata for reporting
print(f"\n  🔍 Fetching PR metadata...")
pr_metadata = {}
for pr_num in all_discovered_prs[:100]:  # Limit to avoid rate limits
    try:
        result = subprocess.run(
            ["gh", "pr", "view", str(pr_num), "--repo", args.repo, "--json", 
             "number,title,author,mergedAt,url"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            pr_data = json.loads(result.stdout)
            pr_metadata[pr_num] = pr_data
    except Exception as e:
        logger.warning(f"Failed to fetch metadata for PR #{pr_num}: {e}")

print(f"  ✅ Fetched metadata for {len(pr_metadata)} PRs")

# ── Create Release Branch ──────────────────────────────────────────────────────
section(3, f"Creating Release Branch: {RELEASE_BRANCH}")

print(f"\n  🔄 PROCESSING:")
print(f"  ├─ Checking if branch '{RELEASE_BRANCH}' exists...")

# Check if branch already exists
branch_check = subprocess.run(
    ["git", "rev-parse", "--verify", RELEASE_BRANCH],
    capture_output=True, text=True
)

if branch_check.returncode == 0:
    print(f"  {warn(f'Branch {RELEASE_BRANCH} already exists')}")
    print(f"  ├─ Checking out existing branch...")
    subprocess.run(["git", "checkout", RELEASE_BRANCH], check=True)
    print(f"  ✅ Switched to existing branch")
else:
    print(f"  ├─ Creating new branch from {BASE_BRANCH}...")
    subprocess.run(["git", "checkout", "-b", RELEASE_BRANCH, BASE_BRANCH], check=True)
    print(f"  ✅ Created and switched to {RELEASE_BRANCH}")

# ── Execute Operations ─────────────────────────────────────────────────────────
section(4, f"Executing {operation_type.upper()} Operations")

print(f"\n  📥 INPUTS:")
print(f"  ├─ Operation: {operation_type.upper()}")
print(f"  ├─ PRs to process: {len(operation_prs)}")
print(f"  └─ LLM Conflict Resolution: {'ENABLED' if cfg.get('llm', {}).get('enabled', False) else 'DISABLED'}")

print(f"\n  🔄 PROCESSING:")
logger.info(f"Executing {operation_type} on {len(operation_prs)} PRs")

# Initialize resolver
resolver = PRLevelResolver(
    repo=args.repo,
    strategy=STRATEGY,
    config=cfg,
    pr_metadata=pr_metadata,
    logger=logger
)

# Track results
successful_prs = []
failed_prs = []
skipped_prs = []
conflicts_resolved = 0

# Execute each operation
for i, pr_num in enumerate(operation_prs, 1):
    pr_meta = pr_metadata.get(pr_num, {})
    pr_title = pr_meta.get("title", f"PR #{pr_num}")[:50]
    
    print(f"\n  [{i}/{len(operation_prs)}] PR #{pr_num}: {pr_title}")
    
    if operation_type == "cherry-pick":
        action = ResolutionAction.INCLUDE
    else:
        action = ResolutionAction.EXCLUDE
    
    success = resolver.execute_pr(pr_num, action)
    
    if success:
        successful_prs.append(pr_num)
        print(f"  ✅ {operation_type.upper()} completed successfully")
        
        # Check if conflicts were resolved
        if resolver.last_had_conflicts:
            conflicts_resolved += 1
            logger.info(f"PR #{pr_num}: Conflict resolved by LLM")
    else:
        failed_prs.append(pr_num)
        print(f"  ❌ {operation_type.upper()} failed - requires manual resolution")
        logger.error(f"PR #{pr_num}: Operation failed")

# ── Execution Summary ──────────────────────────────────────────────────────────
elapsed = time.time() - START_TIME

print(f"\n  📤 EXECUTION SUMMARY:")
print(f"  ├─ Total PRs Attempted: {len(operation_prs)}")
print(f"  ├─ ✅ Successful: {len(successful_prs)}")
print(f"  ├─ ❌ Failed: {len(failed_prs)}")
print(f"  ├─ ⏭️  Skipped: {len(skipped_prs)}")
print(f"  ├─ 🤖 Conflicts Auto-Resolved: {conflicts_resolved}")
print(f"  └─ ⏱️  Time Elapsed: {elapsed:.1f}s")

if failed_prs:
    print(f"\n  ⚠️  FAILED PRs (require manual resolution):")
    for pr_num in failed_prs:
        pr_meta = pr_metadata.get(pr_num, {})
        pr_title = pr_meta.get("title", f"PR #{pr_num}")[:50]
        print(f"     • PR #{pr_num}: {pr_title}")
        print(f"       URL: {pr_meta.get('url', 'N/A')}")

logger.info(f"Execution complete: {len(successful_prs)} successful, {len(failed_prs)} failed")

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 5: GENERATE REPORT & CREATE DRAFT PR
# ══════════════════════════════════════════════════════════════════════════════
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
    print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
    pr_title = pr_meta.get("title", "Unknown")[:60]
    pr_files = pr_meta.get("files_changed", [])
    
    print(f"  📌 PR #{pr_num}: {pr_title}")
    print(f"     Author: {pr_meta.get('author', 'unknown')} | Files: {len(pr_files)} | +{pr_meta.get('additions', 0)}/-{pr_meta.get('deletions', 0)}")
    
    if pr_files:
        print(f"     Modified: {', '.join(pr_files[:3])}")
        if len(pr_files) > 3:
            print(f"     ... and {len(pr_files) - 3} more files")
    
    # Check if we have an LLM decision
    if pr_num in pr_decisions:
        decision = pr_decisions[pr_num]
        print(f"     LLM: {decision.decision} ({decision.confidence} confidence)")
        
        if decision.decision == "EXCLUDE":
            print(f"  ⏭️  SKIPPED - {decision.rationale[:80]}")
            skipped_prs.append(pr_num)
            continue
    
    # Get commit SHA from PR metadata
    commit_sha = pr_meta.get("merge_commit_sha", "")
    if not commit_sha:
        print(f"  ❌ ERROR: No merge commit SHA (PR may not be merged yet)")
        failed_prs.append(pr_num)
        continue
    
    print(f"     Commit: {commit_sha[:12]}...")
    print(f"  🔄 Cherry-picking...")
    
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
        print(f"  ✅ SUCCESS - PR applied to {RELEASE_BRANCH}")
        successful_prs.append(pr_num)
    else:
        # Check for conflicts
        conflict_files = check_for_conflicts()
        
        if conflict_files:
            print(f"  ⚠️  CONFLICT in {len(conflict_files)} file(s): {', '.join(conflict_files[:2])}")
            print(f"  🤖 Attempting LLM-powered resolution...")
            
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
                    print(f"  ✅ RESOLVED - Conflicts automatically fixed by LLM")
                    successful_prs.append(pr_num)
                    conflicts_resolved += len(conflict_files)
                else:
                    print(f"  ⏭️  SKIPPED - {action.reason[:60]}")
                    skipped_prs.append(pr_num)
            elif action.action == "MANUAL":
                print(f"  🔴 MANUAL REVIEW REQUIRED - {action.reason[:60]}")
                failed_prs.append(pr_num)
            else:
                print(f"  ❌ FAILED - {action.reason[:60]}")
                failed_prs.append(pr_num)
        else:
            print(f"  ❌ FAILED - Cherry-pick failed (no conflicts detected)")
            failed_prs.append(pr_num)

# PHASE 3 OUTPUT SUMMARY
print(f"\n")
print(f"  ═══════════════════════════════════════════════════════════════")
print(f"  📊 PHASE 3 SUMMARY")
print(f"  ═══════════════════════════════════════════════════════════════")
print(f"  ✅ Successful:         {len(successful_prs)} PR(s)")
print(f"  ⏭️  Skipped:            {len(skipped_prs)} PR(s)")
print(f"  🔴 Failed/Manual:      {len(failed_prs)} PR(s)")
print(f"  🔧 Conflicts Resolved: {conflicts_resolved}")
print(f"  ═══════════════════════════════════════════════════════════════")

if successful_prs:
    print(f"\n  ✅ SUCCESSFULLY APPLIED:")
    for pr_num in successful_prs:
        pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
        print(f"     • PR #{pr_num}: {pr_meta.get('title', 'N/A')[:60]}")

if failed_prs:
    print(f"\n  🔴 REQUIRES MANUAL REVIEW:")
    for pr_num in failed_prs:
        pr_meta = analysis_results["pr_metadata"].get(pr_num, {})
        print(f"     • PR #{pr_num}: {pr_meta.get('title', 'N/A')[:60]}")

print(f"\n  ✅ Phase 3 Complete")
print(f"  ═══════════════════════════════════════════════════════════════\n")

# ── Summary ───────────────────────────────────────────────────────────────────
elapsed = time.time() - START_TIME
print(f"\n")
print(c(BOLD, "═" * 70))
print(c(BOLD, "  RELEASE OPERATION COMPLETE"))
print(c(BOLD, "═" * 70))
print(f"  Total Time:          {elapsed:.1f}s")
print(f"  Successful:          {len(successful_prs)} PR(s)")
print(f"  Skipped:             {len(skipped_prs)} PR(s)")
print(f"  Failed/Manual:       {len(failed_prs)} PR(s)")
print(f"  Conflicts Resolved:  {conflicts_resolved}")
print(c(BOLD, "═" * 70))

if successful_prs:
    print(f"\n  ✅ Success: {successful_prs}")
if skipped_prs:
    print(f"  ⏭️  Skipped: {skipped_prs}")
if failed_prs:
    print(f"  ❌ Manual Review Needed: {failed_prs}")

print(c(BOLD, "═" * 70))

# ══════════════════════════════════════════════════════════════════════════════
# PHASE 4: CREATE DRAFT PULL REQUEST FOR COMPONENT REVIEW
# ══════════════════════════════════════════════════════════════════════════════
if successful_prs and not DRY_RUN:
    # ── Generate Comprehensive Report FIRST ─────────────────────────────────
    logger.info("Generating comprehensive release report...")
    print(f"\n  📄 Generating comprehensive report for PR body...")
    
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
        dry_run=False,  # Set to False for PR creation
        timestamp=datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    )
    
    # Generate report
    report_gen = ReportGenerator()
    report_file = report_gen.generate_report(report_data)
    print(f"  ✅ Report generated: {report_file}")
    
    # ── Push & Create Draft PR ──────────────────────────────────────────────
    section(4, "Creating Draft Pull Request for Component Review")
    
    print(f"\n  📥 INPUTS:")
    print(f"  ├─ Successful PRs: {len(successful_prs)}")
    print(f"  ├─ Conflicts Resolved: {conflicts_resolved}")
    print(f"  ├─ Release Branch: {RELEASE_BRANCH}")
    print(f"  └─ Target for Draft PR: {TARGET_BRANCH}")
    
    print(f"\n  🔄 PROCESSING:")
    
    # Check if remote branch exists
    print(f"  🔍 Checking if {RELEASE_BRANCH} exists on remote...")
    remote_check = subprocess.run(
        ["git", "ls-remote", "--heads", "origin", RELEASE_BRANCH],
        capture_output=True, text=True
    )
    
    remote_exists = remote_check.returncode == 0 and remote_check.stdout.strip()
    
    # Push the release branch
    print(f"  📤 Step 1: Pushing {RELEASE_BRANCH} to remote...")
    
    if remote_exists:
        print(f"  ⚠️  Remote branch already exists, updating with --force-with-lease...")
        # Use --force-with-lease for safe force push (won't overwrite unexpected changes)
        push_result = subprocess.run(
            ["git", "push", "--force-with-lease", "origin", RELEASE_BRANCH],
            capture_output=True, text=True
        )
    else:
        print(f"  📤 Creating new remote branch...")
        # First time push
        push_result = subprocess.run(
            ["git", "push", "-u", "origin", RELEASE_BRANCH],
            capture_output=True, text=True
        )
    
    if push_result.returncode == 0:
        if remote_exists:
            print(f"  ✅ Branch updated successfully (force-pushed)")
        else:
            print(f"  ✅ Branch pushed successfully")
        
        # Create draft PR
        print(f"\n  📝 Step 2: Creating draft pull request...")
        print(f"  ├─ Using comprehensive report as PR body")
        print(f"  ├─ Base: {TARGET_BRANCH}")
        print(f"  └─ Head: {RELEASE_BRANCH}")
        
        # Read the report content to use as PR body
        pr_body = Path(report_file).read_text()
        
        # Add notification if configured
        notify = cfg.get("notify", [])
        if notify:
            notify_str = " ".join([f"@{n}" for n in notify])
            pr_body += f"\n\n---\ncc: {notify_str}"
        
        # Create the draft PR using gh CLI
        pr_title = f"Release {VERSION}"
        create_pr_result = subprocess.run(
            ["gh", "pr", "create",
             "--base", TARGET_BRANCH,
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
        
        # Provide helpful troubleshooting steps
        if "non-fast-forward" in push_result.stderr or "rejected" in push_result.stderr:
            print(f"\n  💡 TROUBLESHOOTING:")
            print(f"  The remote branch has changes not in your local branch.")
            print(f"  Options:")
            print(f"    1. Delete remote branch and try again:")
            print(f"       git push origin --delete {RELEASE_BRANCH}")
            print(f"       (then re-run the orchestrator)")
            print(f"    2. Pull remote changes first:")
            print(f"       git pull origin {RELEASE_BRANCH}")
            print(f"       (then re-run the orchestrator)")
            print(f"    3. Fetch and reset to latest:")
            print(f"       git fetch origin")
            print(f"       git reset --hard origin/{BASE_BRANCH}")
            print(f"       (then re-run the orchestrator)")
        
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

# ── Generate Comprehensive Report ─────────────────────────────────────────────
# Generate report for dry-run or when PR wasn't created
if 'report_file' not in locals():
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
else:
    print(f"\n  📄 Report already generated for PR body: {report_file}")

print(f"\n  📋 Review the report for complete analysis and recommendations")
print(c(BOLD, "═" * 64))

sys.exit(0 if not failed_prs else 1)
