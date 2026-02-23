#!/usr/bin/env python3
"""
pr_conflict_analyzer.py
========================
Rule-based PR conflict detection for the RDK-B release agent.

This module provides PHASE 1 intelligence: detecting potential conflicts
between PRs based on file overlaps, timing, and dependency patterns.

Unlike code-level conflict resolution, this analyzer works at the PR level:
  - Identifies which PRs might conflict with each other
  - Detects file overlap patterns
  - Flags PRs that touch critical files
  - No code merging — just conflict detection

The LLM resolver then uses this information to make PR-level decisions:
  - Include entire PR
  - Exclude entire PR
  - Flag for manual review
"""

import json
import subprocess
from typing import List, Dict, Set, Optional
from dataclasses import dataclass
from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys

# Import code pattern analyzer for semantic analysis
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))
from code_pattern_analyzer import (
    analyze_pr_diff,
    SemanticAnalysis,
    ChangeType,
    get_pattern_hints
)


@dataclass
class PRConflictInfo:
    """Information about a potential PR conflict."""
    pr_number: int
    conflicting_with: List[int]  # Other PR numbers that conflict
    shared_files: List[str]      # Files that overlap
    conflict_type: str           # "file_overlap", "dependency", "timing"
    severity: str                # "critical", "medium", "low"
    reason: str                  # Human-readable explanation


@dataclass
class PRMetadata:
    """Metadata for a single PR."""
    number: int
    title: str
    author: str
    merged_at: str
    files_changed: Set[str]
    additions: int
    deletions: int
    commits: int
    merge_commit_sha: str = ""  # Merge commit SHA for cherry-picking


@dataclass
class PRSemanticInfo:
    """Semantic analysis information for a PR."""
    pr_number: int
    change_type: str              # dominant ChangeType value
    cosmetic_only: bool           # True if only formatting/comments
    safety_focused: bool          # True if primarily safety improvements
    null_checks_added: int        # Count of NULL check additions
    error_handling_added: int     # Count of error handling additions
    safety_patterns_added: int    # Count of safety improvements
    functional_changes: int       # Count of functional changes
    confidence: str               # "HIGH", "MEDIUM", "LOW"
    summary: str                  # Human-readable summary


class PRConflictAnalyzer:
    """Rule-based analyzer for detecting PR-level conflicts."""
    
    def __init__(self, repo: str):
        self.repo = repo
        self.pr_metadata: Dict[int, PRMetadata] = {}
        self.pr_semantic_info: Dict[int, PRSemanticInfo] = {}
        self.file_to_prs: Dict[str, Set[int]] = defaultdict(set)
        
    def fetch_pr_metadata(self, pr_numbers: List[int]) -> None:
        """Fetch metadata for all specified PRs using GitHub CLI."""
        print(f"  📋 Fetching metadata for {len(pr_numbers)} PRs...")
        
        for pr_num in pr_numbers:
            try:
                # Fetch PR details including merge commit SHA
                result = subprocess.run(
                    ["gh", "pr", "view", str(pr_num), "--repo", self.repo,
                     "--json", "number,title,author,mergedAt,files,additions,deletions,commits,mergeCommit"],
                    capture_output=True, text=True, timeout=30
                )
                
                if result.returncode != 0:
                    print(f"    ⚠️  Failed to fetch PR #{pr_num}: {result.stderr}")
                    continue
                    
                data = json.loads(result.stdout)
                files = set(f["path"] for f in data.get("files", []))
                
                # Get merge commit SHA
                merge_commit = data.get("mergeCommit", {})
                merge_commit_sha = merge_commit.get("oid", "") if merge_commit else ""
                
                metadata = PRMetadata(
                    number=data["number"],
                    title=data.get("title", ""),
                    author=data.get("author", {}).get("login", "unknown"),
                    merged_at=data.get("mergedAt", ""),
                    files_changed=files,
                    additions=data.get("additions", 0),
                    deletions=data.get("deletions", 0),
                    commits=data.get("commits", 0),
                    merge_commit_sha=merge_commit_sha
                )
                
                self.pr_metadata[pr_num] = metadata
                
                # Build file index
                for file in files:
                    self.file_to_prs[file].add(pr_num)
                    
                print(f"    ✅ PR #{pr_num}: {len(files)} files, "
                      f"+{metadata.additions}/-{metadata.deletions}")
                      
            except Exception as e:
                print(f"    ❌ Error fetching PR #{pr_num}: {e}")
                
    def detect_file_overlaps(self, pr_numbers: List[int]) -> List[PRConflictInfo]:
        """Detect PRs that modify the same files (potential conflicts)."""
        conflicts = []
        checked_pairs = set()
        
        for pr_num in pr_numbers:
            if pr_num not in self.pr_metadata:
                continue
                
            pr_files = self.pr_metadata[pr_num].files_changed
            conflicting_prs = set()
            
            # Find all other PRs that touch the same files
            for file in pr_files:
                other_prs = self.file_to_prs[file] - {pr_num}
                conflicting_prs.update(other_prs & set(pr_numbers))
            
            # Create conflict records for each unique pair
            for other_pr in conflicting_prs:
                pair = tuple(sorted([pr_num, other_pr]))
                if pair in checked_pairs:
                    continue
                checked_pairs.add(pair)
                
                shared = pr_files & self.pr_metadata[other_pr].files_changed
                
                # Determine severity based on number of shared files
                if len(shared) >= 5:
                    severity = "critical"
                elif len(shared) >= 2:
                    severity = "medium"
                else:
                    severity = "low"
                
                conflicts.append(PRConflictInfo(
                    pr_number=pr_num,
                    conflicting_with=[other_pr],
                    shared_files=sorted(shared),
                    conflict_type="file_overlap",
                    severity=severity,
                    reason=f"Both PRs modify {len(shared)} common file(s)"
                ))
                
        return conflicts
    
    def detect_timing_conflicts(self, pr_numbers: List[int]) -> List[PRConflictInfo]:
        """Detect PRs merged close in time (likely related changes)."""
        conflicts = []
        
        # Sort PRs by merge time
        pr_times = []
        for pr_num in pr_numbers:
            if pr_num in self.pr_metadata:
                merged_at = self.pr_metadata[pr_num].merged_at
                if merged_at:
                    pr_times.append((pr_num, merged_at))
        
        pr_times.sort(key=lambda x: x[1])
        
        # Check for PRs merged within 24 hours of each other
        for i in range(len(pr_times) - 1):
            pr1, time1 = pr_times[i]
            pr2, time2 = pr_times[i + 1]
            
            # Simple time proximity check (can be enhanced)
            try:
                t1 = datetime.fromisoformat(time1.replace('Z', '+00:00'))
                t2 = datetime.fromisoformat(time2.replace('Z', '+00:00'))
                hours_diff = abs((t2 - t1).total_seconds() / 3600)
                
                if hours_diff <= 24 and hours_diff > 0:
                    # Check if they share files
                    shared = (self.pr_metadata[pr1].files_changed & 
                             self.pr_metadata[pr2].files_changed)
                    
                    if shared:
                        conflicts.append(PRConflictInfo(
                            pr_number=pr1,
                            conflicting_with=[pr2],
                            shared_files=sorted(shared),
                            conflict_type="timing",
                            severity="medium",
                            reason=f"Merged within {hours_diff:.1f} hours, modify same files"
                        ))
            except Exception:
                pass
                
        return conflicts
    
    def detect_critical_file_changes(self, pr_numbers: List[int], 
                                     critical_patterns: List[str] = None) -> List[PRConflictInfo]:
        """Detect PRs that modify critical/core files."""
        if critical_patterns is None:
            # Default critical file patterns for RDK-B
            critical_patterns = [
                r".*Makefile.*",
                r".*\.mk$",
                r".*configure\.ac",
                r".*CMakeLists\.txt",
                r".*_init\.c$",
                r".*_main\.c$",
                r".*_api\.h$",
                r".*_dml\.c$"
            ]
        
        import re
        critical_regexes = [re.compile(p) for p in critical_patterns]
        conflicts = []
        
        for pr_num in pr_numbers:
            if pr_num not in self.pr_metadata:
                continue
                
            critical_files = []
            for file in self.pr_metadata[pr_num].files_changed:
                if any(regex.match(file) for regex in critical_regexes):
                    critical_files.append(file)
            
            if critical_files:
                conflicts.append(PRConflictInfo(
                    pr_number=pr_num,
                    conflicting_with=[],
                    shared_files=critical_files,
                    conflict_type="critical_files",
                    severity="critical",
                    reason=f"Modifies {len(critical_files)} critical file(s)"
                ))
                
        return conflicts
    
    def analyze_pr_semantics(self, pr_numbers: List[int]) -> None:
        """Analyze semantic patterns in PR diffs using code pattern analysis."""
        print(f"\n  🧬 Analyzing code patterns and semantics...")
        
        for pr_num in pr_numbers:
            if pr_num not in self.pr_metadata:
                continue
            
            try:
                # Fetch PR diff using GitHub CLI
                result = subprocess.run(
                    ["gh", "pr", "diff", str(pr_num), "--repo", self.repo],
                    capture_output=True, text=True, timeout=30
                )
                
                if result.returncode != 0:
                    print(f"    ⚠️  Could not fetch diff for PR #{pr_num}")
                    continue
                
                diff_text = result.stdout
                
                # Analyze using code pattern analyzer
                analysis: SemanticAnalysis = analyze_pr_diff(diff_text)
                
                # Store semantic info
                self.pr_semantic_info[pr_num] = PRSemanticInfo(
                    pr_number=pr_num,
                    change_type=analysis.dominant_type.value,
                    cosmetic_only=analysis.cosmetic_only,
                    safety_focused=analysis.safety_focused,
                    null_checks_added=analysis.null_checks_added,
                    error_handling_added=analysis.error_handling_added,
                    safety_patterns_added=analysis.safety_patterns_added,
                    functional_changes=analysis.functional_changes,
                    confidence=analysis.confidence,
                    summary=analysis.summary
                )
                
                # Display semantic summary
                icon = "🎨" if analysis.cosmetic_only else ("🛡️" if analysis.safety_focused else "⚙️")
                print(f"    {icon} PR #{pr_num}: {analysis.summary}")
                
            except Exception as e:
                print(f"    ❌ Error analyzing PR #{pr_num}: {e}")
    
    def analyze(self, pr_numbers: List[int]) -> Dict[str, any]:
        """
        Run complete rule-based conflict analysis.
        
        Returns a dictionary with all detected conflicts and metadata.
        """
        print(f"\n  🔍 PHASE 1: Rule-Based Conflict Detection")
        print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        
        # Fetch metadata for all PRs
        self.fetch_pr_metadata(pr_numbers)
        
        # Analyze semantic patterns in PRs
        self.analyze_pr_semantics(pr_numbers)
        
        # Run all detection methods
        file_conflicts = self.detect_file_overlaps(pr_numbers)
        timing_conflicts = self.detect_timing_conflicts(pr_numbers)
        critical_conflicts = self.detect_critical_file_changes(pr_numbers)
        
        # Aggregate results
        all_conflicts = file_conflicts + timing_conflicts + critical_conflicts
        
        # Group by severity
        conflicts_by_severity = {
            "critical": [c for c in all_conflicts if c.severity == "critical"],
            "medium": [c for c in all_conflicts if c.severity == "medium"],
            "low": [c for c in all_conflicts if c.severity == "low"]
        }
        
        print(f"\n  📊 Detection Summary:")
        print(f"    • File Overlaps: {len(file_conflicts)}")
        print(f"    • Timing Conflicts: {len(timing_conflicts)}")
        print(f"    • Critical Files: {len(critical_conflicts)}")
        print(f"    • Total: {len(all_conflicts)}")
        print(f"      - Critical: {len(conflicts_by_severity['critical'])}")
        print(f"      - Medium: {len(conflicts_by_severity['medium'])}")
        print(f"      - Low: {len(conflicts_by_severity['low'])}")
        
        return {
            "total_prs_analyzed": len(pr_numbers),
            "pr_metadata": {k: {
                "number": v.number,
                "title": v.title,
                "author": v.author,
                "merged_at": v.merged_at,
                "files_count": len(v.files_changed),
                "additions": v.additions,
                "deletions": v.deletions
            } for k, v in self.pr_metadata.items()},
            "pr_semantics": {k: {
                "pr_number": v.pr_number,
                "change_type": v.change_type,
                "cosmetic_only": v.cosmetic_only,
                "safety_focused": v.safety_focused,
                "null_checks_added": v.null_checks_added,
                "error_handling_added": v.error_handling_added,
                "safety_patterns_added": v.safety_patterns_added,
                "functional_changes": v.functional_changes,
                "confidence": v.confidence,
                "summary": v.summary
            } for k, v in self.pr_semantic_info.items()},
            "conflicts": {
                "all": [self._conflict_to_dict(c) for c in all_conflicts],
                "by_severity": {
                    "critical": [self._conflict_to_dict(c) for c in conflicts_by_severity["critical"]],
                    "medium": [self._conflict_to_dict(c) for c in conflicts_by_severity["medium"]],
                    "low": [self._conflict_to_dict(c) for c in conflicts_by_severity["low"]]
                },
                "by_type": {
                    "file_overlap": [self._conflict_to_dict(c) for c in file_conflicts],
                    "timing": [self._conflict_to_dict(c) for c in timing_conflicts],
                    "critical_files": [self._conflict_to_dict(c) for c in critical_conflicts]
                }
            }
        }
    
    def _conflict_to_dict(self, conflict: PRConflictInfo) -> Dict:
        """Convert PRConflictInfo to dictionary for JSON serialization."""
        return {
            "pr_number": conflict.pr_number,
            "conflicting_with": conflict.conflicting_with,
            "shared_files": conflict.shared_files,
            "conflict_type": conflict.conflict_type,
            "severity": conflict.severity,
            "reason": conflict.reason
        }


# ── CLI for standalone testing ───────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument("--prs", required=True, help="Comma-separated PR numbers")
    parser.add_argument("--output", default="/tmp/pr_conflict_analysis.json",
                       help="Output JSON file")
    args = parser.parse_args()
    
    pr_numbers = [int(p.strip()) for p in args.prs.split(",")]
    
    analyzer = PRConflictAnalyzer(args.repo)
    results = analyzer.analyze(pr_numbers)
    
    # Save results
    from pathlib import Path
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n  💾 Results saved to: {output_path}")
