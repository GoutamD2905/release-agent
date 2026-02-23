#!/usr/bin/env python3
"""
analyze_dependencies.py
=======================
PR Dependency Analysis Engine for the RDK-B release agent.

Detects when a cherry-picked PR (include list) touches the same files as
a non-included PR in the develop window — indicating a potential dependency.

Usage:
  python3 analyze_dependencies.py \
    --repo GoutamD2905/advanced-security \
    --include-prs '["1","5"]' \
    --all-prs '[{"number":1,...}, {"number":2,...}, ...]'

Output (stdout): JSON with dependency findings
Exit code: 0 = no deps, 1 = deps found (non-fatal), 2 = error
"""

import argparse
import json
import os
import subprocess
import sys
import yaml
from collections import defaultdict
from llm_resolver import create_resolver_from_config

# ── ANSI colours (same as orchestrator) ──────────────────────────────────────
GREEN  = "\033[92m"
YELLOW = "\033[93m"
RED    = "\033[91m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
DIM    = "\033[2m"
RESET  = "\033[0m"

def c(color, text): return f"{color}{text}{RESET}"
def ok(msg):   return c(GREEN,  f"✅  {msg}")
def warn(msg): return c(YELLOW, f"⚠️   {msg}")
def info(msg): return c(CYAN,   f"ℹ️   {msg}")
def dim(msg):  return c(DIM,    msg)

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--repo",         required=True)
parser.add_argument("--config",       default=".release-config.yml")
parser.add_argument("--include-prs",  required=True, help="JSON list of included PR numbers (strings)")
parser.add_argument("--all-prs",      required=True, help="JSON list of all PR dicts in window")
args = parser.parse_args()

REPO         = args.repo
INCLUDE_NUMS = set(int(p) for p in json.loads(args.include_prs))
ALL_PRS      = json.loads(args.all_prs)

# Load config and LLM resolver
try:
    with open(args.config) as f:
        cfg = yaml.safe_load(f)
except Exception:
    cfg = {}

llm = None
if cfg.get("llm", {}).get("enabled"):
    try:
        llm = create_resolver_from_config(cfg)
    except Exception as e:
        print(f"  {warn(f'Failed to initialize LLM for dependency analysis: {e}')}")

# ── Helper: get files changed by a PR ────────────────────────────────────────
def get_pr_files(pr_number: int) -> set:
    """Returns set of file paths changed by a PR using gh pr diff."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", REPO, "--name-only"],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        # Fallback: try gh pr view --json files
        result2 = subprocess.run(
            ["gh", "pr", "view", str(pr_number), "--repo", REPO,
             "--json", "files"],
            capture_output=True, text=True
        )
        if result2.returncode == 0 and result2.stdout.strip():
            try:
                data = json.loads(result2.stdout)
                return set(f["path"] for f in data.get("files", []))
            except Exception:
                pass
        return set()
    return set(line.strip() for line in result.stdout.splitlines() if line.strip())

def get_pr_diff(pr_number: int) -> str:
    """Returns the full unified diff of a PR using gh pr diff."""
    result = subprocess.run(
        ["gh", "pr", "diff", str(pr_number), "--repo", REPO],
        capture_output=True, text=True
    )
    return result.stdout if result.returncode == 0 else ""

# ── Build file → PR mapping ───────────────────────────────────────────────────
print(f"\n  {info('Fetching changed files for all PRs in window...')}")

pr_map       = {p["number"]: p for p in ALL_PRS}
pr_files     = {}   # pr_number → set of files
file_to_prs  = defaultdict(set)  # file → set of pr_numbers that touch it

for pr in ALL_PRS:
    num = pr["number"]
    files = get_pr_files(num)
    pr_files[num] = files
    for f in files:
        file_to_prs[f].add(num)
    status = ok(f"PR #{num}: {len(files)} file(s)") if files else warn(f"PR #{num}: no files found")
    print(f"    {status}")

# ── Detect dependencies ───────────────────────────────────────────────────────
# For each included PR, find non-included PRs that share files with it
# and were merged BEFORE it (i.e. the included PR likely builds on them)

findings = []   # list of dependency dicts

for inc_num in sorted(INCLUDE_NUMS):
    inc_pr    = pr_map.get(inc_num)
    inc_files = pr_files.get(inc_num, set())
    if not inc_pr or not inc_files:
        continue

    inc_merged = inc_pr.get("mergedAt", "")

    for other_num, other_files in pr_files.items():
        if other_num in INCLUDE_NUMS:
            continue  # already included — not a missing dep
        if not other_files:
            continue

        shared = inc_files & other_files
        if not shared:
            continue

        other_pr     = pr_map.get(other_num, {})
        other_merged = other_pr.get("mergedAt", "")

        # Only flag as dependency if the other PR was merged BEFORE the included PR
        # (if merged after, it's not a prerequisite — it's a subsequent change)
        if other_merged and inc_merged and other_merged >= inc_merged:
            continue

        findings.append({
            "included_pr":    inc_num,
            "included_title": inc_pr.get("title", ""),
            "depends_on_pr":  other_num,
            "depends_on_title": other_pr.get("title", ""),
            "shared_files":   sorted(shared),
            "other_merged":   other_merged[:10] if other_merged else "?",
            "inc_merged":     inc_merged[:10]   if inc_merged   else "?",
            "auto_included":  False,
        })

# ── Sort findings: by included PR, then by dependency PR ─────────────────────
findings.sort(key=lambda x: (x["included_pr"], x["depends_on_pr"]))

# ── Deduplicate: one entry per (included_pr, depends_on_pr) pair ──────────────
seen = set()
unique_findings = []
for f in findings:
    key = (f["included_pr"], f["depends_on_pr"])
    if key not in seen:
        seen.add(key)
        unique_findings.append(f)
findings = unique_findings

# ── LLM Dependency Evaluation ──────────────────────────────────────────────────
if llm and findings:
    print(f"\n  {info('Evaluating dependencies using LLM...')}")
    validated_findings = []
    
    # Pre-fetch diffs to avoid redundant gh calls
    diff_cache = {}
    for f in findings:
        for pr_num in [f["included_pr"], f["depends_on_pr"]]:
            if pr_num not in diff_cache:
                diff_cache[pr_num] = get_pr_diff(pr_num)
                
    for f in findings:
        inc_num = f["included_pr"]
        dep_num = f["depends_on_pr"]
        print(f"    {dim(f'Evaluating PR #{inc_num} dependency on PR #{dep_num}... ')}", end="", flush=True)
        
        diff_a = diff_cache.get(inc_num, "")
        diff_b = diff_cache.get(dep_num, "")
        
        if not diff_a or not diff_b:
            print(c(YELLOW, "SKIPPED (Missing diff)"))
            validated_findings.append(f) # Fallback to true dependency
            continue
            
        is_dependent, is_critical = llm.evaluate_dependency(diff_a, diff_b)
        
        if is_dependent:
            if is_critical:
                print(c(GREEN, "YES (Critical Dependency)"))
            else:
                print(c(GREEN, "YES (Optional Dependency)"))
            f["is_critical"] = is_critical
            validated_findings.append(f)
        else:
            print(c(YELLOW, "NO (Independent - Dropping)"))
            
    findings = validated_findings

# ── Auto-resolve: mark dependency PRs as auto-included ───────────────────────
auto_added = []
dep_prs = sorted(set(f["depends_on_pr"] for f in findings))
for dep_num in dep_prs:
    # Auto-resolve only if LLM flagged it as a critical dependency
    should_auto_resolve = any(f.get("is_critical", False) for f in findings if f["depends_on_pr"] == dep_num)
    if should_auto_resolve and dep_num not in INCLUDE_NUMS:
        auto_added.append(dep_num)
        for f in findings:
            if f["depends_on_pr"] == dep_num:
                f["auto_included"] = True

# ── Print human-readable dependency report ────────────────────────────────────
if not findings:
    print(f"\n  {ok('No dependencies detected — all included PRs are self-contained.')}")
else:
    print(f"\n  {warn(f'{len(findings)} dependency relationship(s) detected:')}")
    print()

    # Group by included PR
    by_included = defaultdict(list)
    for f in findings:
        by_included[f["included_pr"]].append(f)

    for inc_num, deps in sorted(by_included.items()):
        inc_title = deps[0]["included_title"][:55]
        print(f"  {c(BOLD, f'PR #{inc_num}')} — {inc_title}")
        print(f"  {'─'*62}")
        for dep in deps:
            dep_num   = dep["depends_on_pr"]
            dep_title = dep["depends_on_title"][:50]
            shared    = dep["shared_files"]
            action    = c(GREEN, "AUTO-INCLUDED") if dep["auto_included"] else c(YELLOW, "NOT INCLUDED")

            print(f"    {warn(f'Depends on PR #{dep_num}')} — {dep_title}")
            print(f"    Merged before: {dep['other_merged']}  →  PR #{inc_num} merged: {dep['inc_merged']}")
            print(f"    Shared files ({len(shared)}):")
            for sf in shared[:8]:  # show max 8 files
                print(f"      • {sf}")
            if len(shared) > 8:
                print(f"      … and {len(shared)-8} more")
            print(f"    Action: {action}")
            print()

    if auto_added:
        print(f"  {ok(f'Auto-included critical dependency PRs: {auto_added}')}")
        print(f"  {dim('These will be cherry-picked BEFORE the PRs that depend on them.')}")
    else:
        print(f"  {warn('Non-critical dependencies were found, but not auto-included.')}")

# ── Output JSON result ────────────────────────────────────────────────────────
output = {
    "findings":   findings,
    "auto_added": auto_added,
    "has_deps":   len(findings) > 0,
}

# Write to temp file for orchestrator to read
out_path = "/tmp/rdkb-dep-analysis.json"
with open(out_path, "w") as f:
    json.dump(output, f, indent=2)

print(f"\n  {dim(f'Dependency analysis written to: {out_path}')}")

sys.exit(1 if findings else 0)
