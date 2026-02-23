#!/usr/bin/env python3
"""
pr_discovery.py
===============
Automatic PR discovery from git history for intelligent release planning.

This module provides smart capabilities to:
  - Discover all PRs merged since the last tag
  - Identify PR dependencies based on code analysis
  - Validate user's include/exclude configuration
  - Provide intelligent warnings and recommendations
"""

import re
import subprocess
from typing import List, Dict, Set, Tuple, Optional
from dataclasses import dataclass
from collections import defaultdict


@dataclass
class PRDiscoveryResult:
    """Result of PR auto-discovery."""
    all_prs: List[int]                    # All PRs found since last tag
    last_tag: str                          # Last tag found
    commits_since_tag: int                 # Number of commits since tag
    pr_commit_map: Dict[int, str]          # PR number -> commit hash
    pr_titles: Dict[int, str]              # PR number -> PR title from commit


@dataclass
class DependencyValidation:
    """Validation result for PR dependencies."""
    missing_dependencies: Dict[int, List[int]]  # PR -> missing required PRs
    orphaned_dependencies: Dict[int, List[int]] # Excluded PR -> PRs that depend on it
    warnings: List[str]                         # Human-readable warnings
    recommendations: List[str]                  # Smart recommendations


def get_last_tag(repo_path: str = ".") -> Optional[str]:
    """
    Get the last git tag in the repository.
    
    Returns:
        Tag name or None if no tags found
    """
    try:
        result = subprocess.run(
            ["git", "describe", "--tags", "--abbrev=0"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
        return None
    except Exception as e:
        print(f"Warning: Could not get last tag: {e}")
        return None


def get_commits_since_tag(tag: str, base_branch: str = "develop", repo_path: str = ".") -> List[str]:
    """
    Get all commit hashes since the specified tag on the base branch.
    
    Args:
        tag: Git tag to compare from
        base_branch: Branch to check commits on
        repo_path: Path to git repository
        
    Returns:
        List of commit hashes (newest first)
    """
    try:
        # Get commits between tag and base branch
        result = subprocess.run(
            ["git", "log", f"{tag}..{base_branch}", "--format=%H"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=30
        )
        if result.returncode == 0:
            commits = result.stdout.strip().split('\n')
            return [c for c in commits if c]
        return []
    except Exception as e:
        print(f"Warning: Could not get commits since {tag}: {e}")
        return []


def extract_pr_from_commit(commit_hash: str, repo_path: str = ".") -> Optional[Tuple[int, str]]:
    """
    Extract PR number and title from commit message.
    
    Common patterns:
      - "Merge pull request #123 from user/branch"
      - "PR #456: Add feature"
      - "(#789)"
      - "Closes #123"
      
    Returns:
        Tuple of (PR number, title) or None if no PR found
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--format=%s", commit_hash],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode != 0:
            return None
            
        commit_msg = result.stdout.strip()
        
        # Try various PR patterns
        patterns = [
            r'Merge pull request #(\d+)',      # GitHub merge commits
            r'Merge PR #(\d+)',                # Short merge format
            r'PR[:\s]#(\d+)',                  # PR #123: Title
            r'\(#(\d+)\)',                     # Title (#123)
            r'Closes[:\s]#(\d+)',              # Closes #123
            r'Fixes[:\s]#(\d+)',               # Fixes #123
            r'#(\d+)',                         # Generic #123 (fallback)
        ]
        
        for pattern in patterns:
            match = re.search(pattern, commit_msg, re.IGNORECASE)
            if match:
                pr_num = int(match.group(1))
                # Extract title (remove PR reference)
                title = re.sub(r'Merge pull request #\d+\s+from\s+[\w\-/]+\s*', '', commit_msg)
                title = re.sub(r'Merge PR #\d+[:\s]*', '', title)  # Handle "Merge PR #XX:"
                title = re.sub(r'PR[:\s]#\d+[:\s]*', '', title)
                title = re.sub(r'\(#\d+\)', '', title)
                title = title.strip()
                return (pr_num, title or commit_msg[:50])
                
        return None
        
    except Exception as e:
        print(f"Warning: Could not extract PR from commit {commit_hash}: {e}")
        return None


def discover_prs_since_tag(base_branch: str = "develop", 
                           repo_path: str = ".") -> Optional[PRDiscoveryResult]:
    """
    Discover all PRs merged since the last tag.
    
    Args:
        base_branch: Branch to check for merged PRs
        repo_path: Path to git repository
        
    Returns:
        PRDiscoveryResult with all discovered PRs, or None if error
    """
    # Get last tag
    last_tag = get_last_tag(repo_path)
    if not last_tag:
        print("  ⚠️  No git tags found - cannot auto-discover PRs")
        return None
    
    # Get commits since tag
    commits = get_commits_since_tag(last_tag, base_branch, repo_path)
    if not commits:
        print(f"  ℹ️  No commits found since tag {last_tag}")
        return PRDiscoveryResult(
            all_prs=[],
            last_tag=last_tag,
            commits_since_tag=0,
            pr_commit_map={},
            pr_titles={}
        )
    
    # Extract PRs from commits
    pr_commit_map = {}
    pr_titles = {}
    
    for commit in commits:
        pr_info = extract_pr_from_commit(commit, repo_path)
        if pr_info:
            pr_num, title = pr_info
            if pr_num not in pr_commit_map:  # Keep first (newest) occurrence
                pr_commit_map[pr_num] = commit
                pr_titles[pr_num] = title
    
    all_prs = sorted(pr_commit_map.keys())
    
    return PRDiscoveryResult(
        all_prs=all_prs,
        last_tag=last_tag,
        commits_since_tag=len(commits),
        pr_commit_map=pr_commit_map,
        pr_titles=pr_titles
    )


def validate_pr_dependencies(
    configured_prs: List[int],
    strategy: str,
    all_prs: List[int],
    llm_decisions: Dict[int, 'PRDecision']) -> DependencyValidation:
    """
    Validate PR configuration against discovered dependencies.
    
    Args:
        configured_prs: PRs configured by user (include or exclude list)
        strategy: "include" or "exclude"
        all_prs: All PRs discovered from git history
        llm_decisions: LLM decisions with dependency information
        
    Returns:
        DependencyValidation with warnings and recommendations
    """
    warnings = []
    recommendations = []
    missing_dependencies = defaultdict(list)
    orphaned_dependencies = defaultdict(list)
    
    # Determine which PRs will be included
    if strategy == "include":
        included_prs = set(configured_prs)
        excluded_prs = set(all_prs) - included_prs
    else:  # exclude
        excluded_prs = set(configured_prs)
        included_prs = set(all_prs) - excluded_prs
    
    # Check for missing dependencies
    for pr_num in included_prs:
        if pr_num in llm_decisions:
            decision = llm_decisions[pr_num]
            required_prs = decision.requires_prs
            
            for req_pr in required_prs:
                if req_pr not in included_prs:
                    missing_dependencies[pr_num].append(req_pr)
                    warnings.append(
                        f"PR #{pr_num} requires PR #{req_pr}, but #{req_pr} is not included"
                    )
    
    # Check for orphaned dependencies (excluded PRs that others need)
    for pr_num in excluded_prs:
        dependent_prs = []
        for other_pr in included_prs:
            if other_pr in llm_decisions:
                if pr_num in llm_decisions[other_pr].requires_prs:
                    dependent_prs.append(other_pr)
        
        if dependent_prs:
            orphaned_dependencies[pr_num] = dependent_prs
            warnings.append(
                f"PR #{pr_num} is excluded, but PRs {dependent_prs} depend on it"
            )
    
    # Generate recommendations
    if missing_dependencies:
        all_missing = set()
        for deps in missing_dependencies.values():
            all_missing.update(deps)
        recommendations.append(
            f"Consider adding PRs {sorted(all_missing)} to satisfy dependencies"
        )
    
    if orphaned_dependencies:
        all_orphaned = sorted(orphaned_dependencies.keys())
        recommendations.append(
            f"Consider including PRs {all_orphaned} (other PRs depend on them)"
        )
    
    # Check for PRs in config that weren't found in git history
    unknown_prs = set(configured_prs) - set(all_prs)
    if unknown_prs:
        warnings.append(
            f"PRs {sorted(unknown_prs)} are configured but not found in git history since last tag"
        )
        recommendations.append(
            f"Verify that PRs {sorted(unknown_prs)} are merged in {strategy} branch"
        )
    
    # Suggest PRs that might be missing
    if strategy == "include":
        unconfigured_prs = set(all_prs) - set(configured_prs)
        if unconfigured_prs and len(unconfigured_prs) <= 10:
            recommendations.append(
                f"Found {len(unconfigured_prs)} additional PRs not in config: {sorted(unconfigured_prs)}"
            )
    
    return DependencyValidation(
        missing_dependencies=dict(missing_dependencies),
        orphaned_dependencies=dict(orphaned_dependencies),
        warnings=warnings,
        recommendations=recommendations
    )


def print_discovery_summary(discovery: PRDiscoveryResult,
                           configured_prs: List[int],
                           strategy: str) -> None:
    """Print a formatted summary of PR discovery."""
    from utils import c, BOLD, GREEN, YELLOW, CYAN, DIM
    
    print(f"\n  {c(BOLD, '🔍 Smart PR Discovery')}")
    print(f"  {c(DIM, '─' * 56)}")
    print(f"  Last Tag        : {c(CYAN, discovery.last_tag)}")
    print(f"  Commits Since   : {discovery.commits_since_tag}")
    print(f"  PRs Found       : {len(discovery.all_prs)}")
    
    if discovery.all_prs:
        print(f"\n  All PRs since {discovery.last_tag}:")
        for pr_num in discovery.all_prs:  # Show all PRs
            title = discovery.pr_titles.get(pr_num, "")[:60]
            configured = "✓" if pr_num in configured_prs else " "
            print(f"    [{configured}] PR #{pr_num}: {title}")
    
    # Show strategy summary
    print(f"\n  Strategy        : {c(BOLD, strategy.upper())}")
    if strategy == "include":
        print(f"  Configured      : {len(configured_prs)} PRs to INCLUDE")
        not_in_config = len(set(discovery.all_prs) - set(configured_prs))
        if not_in_config > 0:
            print(f"  {c(YELLOW, f'⚠️  {not_in_config} PRs found but not in config')}")
    else:
        print(f"  Configured      : {len(configured_prs)} PRs to EXCLUDE")
        will_include = len(set(discovery.all_prs) - set(configured_prs))
        print(f"  Will Include    : {will_include} PRs")


def print_dependency_warnings(validation: DependencyValidation) -> None:
    """Print dependency validation warnings and recommendations."""
    from utils import c, BOLD, RED, YELLOW, GREEN
    
    if not validation.warnings and not validation.recommendations:
        print(f"\n  {c(GREEN, '✅ No dependency issues detected')}")
        return
    
    if validation.warnings:
        print(f"\n  {c(BOLD, '⚠️  Dependency Warnings:')}")
        for warning in validation.warnings:
            print(f"    {c(YELLOW, '•')} {warning}")
    
    if validation.recommendations:
        print(f"\n  {c(BOLD, '💡 Smart Recommendations:')}")
        for rec in validation.recommendations:
            print(f"    {c(GREEN, '→')} {rec}")
