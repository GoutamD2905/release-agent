#!/usr/bin/env python3
"""
pr_level_resolver.py
====================
PR-level conflict resolution strategy for the RDK-B release agent.

This module implements the refined resolution approach:
  1. Rule-based detection identifies potential conflicts
  2. LLM makes strategic decisions: include entire PR or exclude it
  3. NO code-level merging — binary choice only

When a conflict occurs during cherry-pick/revert:
  - Abort the operation (git cherry-pick --abort / git revert --abort)
  - Let LLM decide: Include this PR? Exclude this PR? Need other PRs?
  - Take action based on LLM decision
  - NO manual code merging at hunk level
"""

import json
import subprocess
import sys
from typing import List, Dict, Optional
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ResolutionAction:
    """Action to take for a conflicted PR."""
    pr_number: int
    action: str  # "INCLUDE", "EXCLUDE", "DEFER", "MANUAL"
    reason: str
    depends_on: List[int]  # PRs that must be processed first


class PRLevelResolver:
    """Resolver that makes PR-level decisions (no code merging)."""
    
    def __init__(self, mode: str, decision_maker=None):
        """
        Args:
            mode: "cherry-pick" or "revert"
            decision_maker: LLMPRDecisionMaker instance (optional)
        """
        self.mode = mode
        self.decision_maker = decision_maker
        self.resolution_log = Path("/tmp/rdkb-release-conflicts/pr_resolutions.json")
        self.resolution_log.parent.mkdir(parents=True, exist_ok=True)
        
        # Load existing resolutions
        self.resolutions = {}
        if self.resolution_log.exists():
            with open(self.resolution_log) as f:
                self.resolutions = json.load(f)
    
    def handle_conflict(self,
                       pr_number: int,
                       pr_metadata: Dict,
                       conflict_files: List[str],
                       all_prs_metadata: Dict[int, Dict],
                       detected_conflicts: List[Dict]) -> ResolutionAction:
        """
        Handle a conflict by making a PR-level decision.
        
        Returns:
            ResolutionAction indicating what to do with this PR
        """
        print(f"\n  🚨 CONFLICT detected for PR #{pr_number}")
        print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Files in conflict: {len(conflict_files)}")
        for f in conflict_files[:5]:
            print(f"    - {f}")
        if len(conflict_files) > 5:
            print(f"    ... and {len(conflict_files) - 5} more")
        
        # Abort the git operation
        self._abort_git_operation()
        
        # Check if we have an LLM decision maker
        if not self.decision_maker:
            print(f"  ⚠️  No LLM decision maker available - flagging for manual review")
            return ResolutionAction(
                pr_number=pr_number,
                action="MANUAL",
                reason="No LLM available, requires manual decision",
                depends_on=[]
            )
        
        # Get PR diff for LLM analysis
        pr_diff = self._get_pr_diff(pr_number)
        
        # Find conflicts specific to this PR
        pr_conflicts = [c for c in detected_conflicts if c.get("pr_number") == pr_number]
        
        # Call LLM to make decision
        print(f"  🤖 Consulting LLM for strategic decision...")
        decision = self.decision_maker.decide_pr(
            pr_number=pr_number,
            pr_metadata=pr_metadata,
            pr_diff=pr_diff,
            conflicts=pr_conflicts,
            all_prs_metadata=all_prs_metadata
        )
        
        if not decision:
            print(f"  ❌ LLM decision failed - flagging for manual review")
            return ResolutionAction(
                pr_number=pr_number,
                action="MANUAL",
                reason="LLM call failed",
                depends_on=[]
            )
        
        # Display LLM decision
        print(f"\n  📋 LLM Decision:")
        print(f"    • Decision: {decision.decision}")
        print(f"    • Confidence: {decision.confidence}")
        print(f"    • Rationale: {decision.rationale}")
        
        if decision.risks:
            print(f"    • Risks: {', '.join(decision.risks[:2])}")
        if decision.benefits:
            print(f"    • Benefits: {', '.join(decision.benefits[:2])}")
        if decision.requires_prs:
            print(f"    • Requires PRs: {decision.requires_prs}")
        
        # Convert LLM decision to action
        if decision.decision == "INCLUDE":
            action = ResolutionAction(
                pr_number=pr_number,
                action="INCLUDE",
                reason=decision.rationale,
                depends_on=decision.requires_prs
            )
        elif decision.decision == "EXCLUDE":
            action = ResolutionAction(
                pr_number=pr_number,
                action="EXCLUDE",
                reason=decision.rationale,
                depends_on=[]
            )
        else:  # MANUAL_REVIEW
            action = ResolutionAction(
                pr_number=pr_number,
                action="MANUAL",
                reason=decision.rationale,
                depends_on=decision.requires_prs
            )
        
        # Save resolution
        self._save_resolution(pr_number, action, decision)
        
        return action
    
    def _abort_git_operation(self):
        """Abort the current git cherry-pick or revert operation."""
        if self.mode == "cherry-pick":
            cmd = ["git", "cherry-pick", "--abort"]
        else:
            cmd = ["git", "revert", "--abort"]
        
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f"  ✅ Aborted git {self.mode} operation")
        else:
            # Already aborted or no operation in progress
            pass
    
    def _get_pr_diff(self, pr_number: int) -> str:
        """Fetch the full diff for a PR."""
        # Try to use gh CLI if available
        result = subprocess.run(
            ["gh", "pr", "diff", str(pr_number)],
            capture_output=True, text=True
        )
        
        if result.returncode == 0:
            return result.stdout
        
        # Fallback: empty diff
        return ""
    
    def _save_resolution(self, pr_number: int, action: ResolutionAction, decision):
        """Save the resolution for audit trail."""
        self.resolutions[str(pr_number)] = {
            "pr_number": pr_number,
            "action": action.action,
            "reason": action.reason,
            "depends_on": action.depends_on,
            "llm_decision": decision.decision if decision else None,
            "llm_confidence": decision.confidence if decision else None,
            "llm_rationale": decision.rationale if decision else None,
            "mode": self.mode
        }
        
        with open(self.resolution_log, "w") as f:
            json.dump(self.resolutions, f, indent=2)
    
    def apply_action(self, action: ResolutionAction, commit_sha: str) -> bool:
        """
        Apply the resolution action.
        
        Args:
            action: The action to take
            commit_sha: The commit SHA for the PR
            
        Returns:
            True if action was successfully applied
        """
        print(f"\n  🎯 Applying resolution: {action.action}")
        
        if action.action == "INCLUDE":
            # Proceed with the operation (try again)
            if action.depends_on:
                print(f"  ⚠️  This PR requires other PRs first: {action.depends_on}")
                print(f"  ℹ️  Deferring PR until dependencies are processed")
                return False
            
            print(f"  ✅ Attempting to {self.mode} PR (full PR accepted)")
            
            # Try the operation again
            if self.mode == "cherry-pick":
                result = subprocess.run(
                    ["git", "cherry-pick", commit_sha],
                    capture_output=True, text=True
                )
            else:
                result = subprocess.run(
                    ["git", "revert", "-m", "1", commit_sha],
                    capture_output=True, text=True
                )
            
            if result.returncode == 0:
                print(f"  ✅ Successfully applied PR")
                return True
            else:
                # Still conflicts - this shouldn't happen if LLM decided correctly
                print(f"  ⚠️  Conflict persists even after LLM approval")
                print(f"  ℹ️  This may require dependency PRs or manual intervention")
                self._abort_git_operation()
                return False
        
        elif action.action == "EXCLUDE":
            print(f"  ⏭️  Skipping PR entirely (excluded by LLM decision)")
            print(f"  Reason: {action.reason}")
            return True  # Successfully excluded (by skipping)
        
        elif action.action == "DEFER":
            print(f"  ⏸️  Deferring PR (dependencies not yet processed)")
            print(f"  Required PRs: {action.depends_on}")
            return False
        
        else:  # MANUAL
            print(f"  🔴 MANUAL REVIEW REQUIRED")
            print(f"  Reason: {action.reason}")
            print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
            print(f"  Please resolve manually:")
            print(f"    1. Review the conflict")
            print(f"    2. Decide to include or exclude this PR")
            print(f"    3. Update release configuration accordingly")
            return False


# ── Helper function for checking conflicts ───────────────────────────────────
def check_for_conflicts() -> List[str]:
    """
    Check if there are any conflicts in the current git state.
    
    Returns:
        List of files with conflicts (empty if no conflicts)
    """
    result = subprocess.run(
        ["git", "diff", "--name-only", "--diff-filter=U"],
        capture_output=True, text=True
    )
    
    if result.returncode != 0:
        return []
    
    return [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]


# ── CLI for testing ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import yaml
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["cherry-pick", "revert"], required=True)
    parser.add_argument("--pr", type=int, required=True)
    parser.add_argument("--config", default=".release-config.yml")
    parser.add_argument("--conflicts-file", help="JSON file with conflict analysis")
    args = parser.parse_args()
    
    # Check for conflicts
    conflict_files = check_for_conflicts()
    
    if not conflict_files:
        print("No conflicts detected in current git state")
        sys.exit(0)
    
    print(f"Found {len(conflict_files)} files with conflicts")
    
    # Load config
    config = {}
    if Path(args.config).exists():
        with open(args.config) as f:
            config = yaml.safe_load(f)
    
    # Load conflict analysis
    detected_conflicts = []
    if args.conflicts_file and Path(args.conflicts_file).exists():
        with open(args.conflicts_file) as f:
            conflict_data = json.load(f)
            detected_conflicts = conflict_data.get("conflicts", {}).get("all", [])
    
    # Create decision maker if LLM is enabled
    decision_maker = None
    if config.get("llm", {}).get("enabled"):
        sys.path.insert(0, str(Path(__file__).parent))
        from llm_pr_decision import LLMPRDecisionMaker
        decision_maker = LLMPRDecisionMaker(config)
    
    # Create resolver
    resolver = PRLevelResolver(args.mode, decision_maker)
    
    # Dummy PR metadata
    pr_metadata = {
        "number": args.pr,
        "title": f"PR #{args.pr}",
        "author": "unknown",
        "files_count": len(conflict_files)
    }
    
    # Handle conflict
    action = resolver.handle_conflict(
        pr_number=args.pr,
        pr_metadata=pr_metadata,
        conflict_files=conflict_files,
        all_prs_metadata={args.pr: pr_metadata},
        detected_conflicts=detected_conflicts
    )
    
    print(f"\n  Final action: {action.action}")
    print(f"  Reason: {action.reason}")
