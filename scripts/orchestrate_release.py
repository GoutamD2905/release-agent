#!/usr/bin/env python3
"""
orchestrate_release.py
Main orchestrator for the RDK-B bi-weekly release agent.

Supports:
  - strategy: exclude  → start from develop, revert excluded PRs
  - strategy: include  → start from main, cherry-pick included PRs

Usage:
  python3 orchestrate_release.py \
    --repo rdkcentral/rdkb-component \
    --config .release-config.yml \
    [--dry-run]
"""

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:
    print("ERROR: PyYAML not installed. Run: pip install pyyaml")
    sys.exit(1)

SCRIPTS_DIR = Path(__file__).parent
START_TIME  = time.time()

# ── ANSI colours ──────────────────────────────────────────────────────────────
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
def err(msg):  return c(RED,    f"❌  {msg}")
def info(msg): return c(CYAN,   f"ℹ️   {msg}")
def dim(msg):  return c(DIM,    msg)

def banner(title, width=64):
    print("\n" + c(BOLD, "═" * width))
    print(c(BOLD, f"  {title}"))
    print(c(BOLD, "═" * width))

def section(step, title):
    elapsed = time.time() - START_TIME
    print(f"\n{c(BOLD, f'[Step {step}]')} {title}  {dim(f'+{elapsed:.1f}s')}")
    print(c(DIM, "  " + "─" * 56))


# ── Argument parsing ──────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="RDK-B Release Orchestrator")
parser.add_argument("--repo",    required=True,  help="GitHub repo, e.g. rdkcentral/rdkb-component")
parser.add_argument("--config",  default=".release-config.yml")
parser.add_argument("--version", default=None, help="Override version from config")
parser.add_argument("--dry-run", action="store_true")
args = parser.parse_args()

# ── Load config ───────────────────────────────────────────────────────────────
config_path = Path(args.config)
if not config_path.exists():
    print(err(f"Config file not found: {config_path}"))
    sys.exit(1)

with open(config_path) as f:
    cfg = yaml.safe_load(f)

VERSION         = args.version or cfg.get("version")
STRATEGY        = cfg.get("strategy", "").lower()
CONFIGURED_PRS  = [int(p) for p in cfg.get("prs") or []]
CONFLICT_POLICY = cfg.get("conflict_policy", "pause")
DRY_RUN         = args.dry_run or cfg.get("dry_run", False)
RELEASE_BRANCH  = cfg.get("release_branch", f"release/{VERSION}")
BASE_BRANCH     = cfg.get("base_branch", "develop")   # generic: supports any integration branch
COMPONENT_NAME  = cfg.get("component_name") or args.repo.split("/")[-1]  # default: repo basename

# ── Config validation ─────────────────────────────────────────────────────────
errors = []
if not VERSION:
    errors.append("'version' is required.")
if STRATEGY not in ("exclude", "include"):
    errors.append(f"'strategy' must be 'exclude' or 'include' (got: '{STRATEGY}').")
if STRATEGY == "include" and not CONFIGURED_PRS:
    errors.append("'prs' list is REQUIRED when strategy is 'include'.")
if errors:
    print(err("Configuration errors in .release-config.yml:"))
    for e in errors:
        print(f"   • {e}")
    sys.exit(1)

# ── Strategy description ──────────────────────────────────────────────────────
if STRATEGY == "exclude":
    if CONFIGURED_PRS:
        strategy_desc = f"Take ALL PRs from '{BASE_BRANCH}', REVERT: {CONFIGURED_PRS}"
    else:
        strategy_desc = f"Take ALL PRs from '{BASE_BRANCH}' (no exclusions)"
else:
    strategy_desc = f"Cherry-pick ONLY: {CONFIGURED_PRS}"

banner(f"RDK-B Release Orchestrator")
print(f"  {'Component':<16}: {COMPONENT_NAME}")
print(f"  {'Repo':<16}: {args.repo}")
print(f"  {'Version':<16}: {VERSION}")
print(f"  {'Base Branch':<16}: {BASE_BRANCH}")
print(f"  {'Strategy':<16}: {STRATEGY.upper()} — {strategy_desc}")
print(f"  {'Conflict Policy':<16}: {CONFLICT_POLICY}")
print(f"  {'Dry Run':<16}: {DRY_RUN}")
print(f"  {'Release Branch':<16}: {RELEASE_BRANCH}")
print(f"  {'Started':<16}: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
print(c(BOLD, "═" * 64))

# ── Helper: run shell command ─────────────────────────────────────────────────
def run(cmd, check=True, capture=False):
    print(dim(f"  $ {' '.join(str(x) for x in cmd)}"))
    result = subprocess.run(cmd, capture_output=capture, text=True)
    if not capture:
        if result.stdout:
            for line in result.stdout.splitlines():
                print(f"  {line}")
        if result.stderr:
            for line in result.stderr.splitlines():
                print(dim(f"  {line}"))
    if check and result.returncode not in (0, 2):
        print(err(f"Command failed (exit {result.returncode}): {' '.join(str(x) for x in cmd)}"))
        sys.exit(result.returncode)
    return result

# ── Step 1: Detect last release tag ──────────────────────────────────────────
section(1, "Detecting last release tag & fetching PRs from develop")

SEMVER_RE = re.compile(r'^v?\d+\.\d+\.\d+')
last_tag_date = ""
last_tag_name = ""

tag_result = subprocess.run(["git", "tag", "--sort=-creatordate"],
                            capture_output=True, text=True)
if tag_result.returncode == 0:
    for tag in tag_result.stdout.strip().splitlines():
        if SEMVER_RE.match(tag.strip()):
            last_tag_name = tag.strip()
            date_r = subprocess.run(["git", "log", "-1", "--format=%aI", last_tag_name],
                                    capture_output=True, text=True)
            if date_r.returncode == 0 and date_r.stdout.strip():
                last_tag_date = date_r.stdout.strip()
            break

if last_tag_name:
    print(f"  {ok(f'Last semver tag: {last_tag_name}  ({last_tag_date})')}")
else:
    print(f"  {warn(f'No semver release tag found — fetching ALL PRs from {BASE_BRANCH}')}")

gh_cmd = ["gh", "pr", "list", "--repo", args.repo, "--base", BASE_BRANCH,
          "--state", "merged",
          "--json", "number,title,mergeCommit,mergedAt,author,url",
          "--limit", "500"]
result = run(gh_cmd, capture=True)
all_prs = json.loads(result.stdout) if result.stdout.strip() else []

if last_tag_date:
    all_prs = [p for p in all_prs if (p.get("mergedAt") or "") > last_tag_date]

all_prs.sort(key=lambda p: p.get("mergedAt", ""))
tag_label = last_tag_name or "(none)"
print(f"\n  {ok(f'Found {len(all_prs)} PR(s) in {BASE_BRANCH} since tag {tag_label}')}:")
print(f"\n  {'#':>5}  {'Title':<45}  {'Author':<16}  {'Merged At'}")
print(f"  {'─'*5}  {'─'*45}  {'─'*16}  {'─'*20}")
for p in all_prs:
    merged = (p.get("mergedAt") or "")[:10]
    author = p.get("author", {}).get("login", "?")
    title  = p.get("title", "")[:45]
    print(f"  #{p['number']:>4}  {title:<45}  @{author:<15}  {merged}")

# ── Step 2: Resolve PR sets ───────────────────────────────────────────────────
section(2, "Resolving operation plan")

pr_map = {p["number"]: p for p in all_prs}

if STRATEGY == "exclude":
    excluded_prs  = [n for n in CONFIGURED_PRS if n in pr_map]
    not_found     = [n for n in CONFIGURED_PRS if n not in pr_map]
    intake_prs    = [p for p in all_prs if p["number"] not in excluded_prs]
    operation_prs = list(reversed(excluded_prs))  # revert newest-first
    op_type       = "revert"

    print(f"  {'Total PRs in window':<30}: {len(all_prs)}")
    print(f"  {'PRs to REVERT (exclude)':<30}: {excluded_prs}")
    print(f"  {'PRs going into release':<30}: {[p['number'] for p in intake_prs]}")
    if not_found:
        print(f"  {warn(f'PRs not found in window (ignored): {not_found}')}")
else:
    included_prs  = [n for n in CONFIGURED_PRS if n in pr_map]
    not_found     = [n for n in CONFIGURED_PRS if n not in pr_map]
    intake_prs    = [pr_map[n] for n in included_prs]
    operation_prs = included_prs  # cherry-pick oldest-first
    op_type       = "cherry-pick"

    print(f"  {'Total PRs in window':<30}: {len(all_prs)}")
    print(f"  {'PRs to CHERRY-PICK (include)':<30}: {included_prs}")
    if not_found:
        print(f"  {warn(f'PRs not found in window (ignored): {not_found}')}")

print(f"\n  {info(f'Operation: {op_type.upper()} × {len(operation_prs)} PR(s)')}")

# ── Step 2b: Dependency analysis (include mode only) ─────────────────────────
dep_findings  = []
dep_auto_added = []

if STRATEGY == "include" and operation_prs:
    section("2b", "Analyzing PR dependencies")

    dep_cmd = [
        "python3", str(SCRIPTS_DIR / "analyze_dependencies.py"),
        "--repo",         args.repo,
        "--config",       args.config,
        "--include-prs",  json.dumps([str(n) for n in operation_prs]),
        "--all-prs",      json.dumps(all_prs),
    ]

    dep_result = run(dep_cmd, check=False)

    # Read the JSON output written by the analyzer
    dep_json_path = "/tmp/rdkb-dep-analysis.json"
    if Path(dep_json_path).exists():
        with open(dep_json_path) as f:
            dep_data = json.load(f)
        dep_findings   = dep_data.get("findings", [])
        dep_auto_added = dep_data.get("auto_added", [])

    if dep_auto_added:
        # Insert auto-added PRs in chronological order BEFORE the PRs that need them
        # Build: for each auto-added dep, find the earliest included PR that needs it
        # and insert it just before that PR in operation_prs
        new_ops = list(operation_prs)
        for dep_num in dep_auto_added:
            if dep_num in new_ops:
                continue
            # Find the first included PR that depends on dep_num
            first_needing = None
            for finding in dep_findings:
                if finding["depends_on_pr"] == dep_num and finding["included_pr"] in new_ops:
                    first_needing = finding["included_pr"]
                    break
            if first_needing is not None:
                idx = new_ops.index(int(str(first_needing)))
                new_ops.insert(idx, int(str(dep_num)))
                # Also add to pr_map if not already there
                if int(str(dep_num)) not in pr_map:
                    # Fetch from all_prs (it should be there)
                    dep_pr = next((p for p in all_prs if p["number"] == dep_num), None)
                    if dep_pr:
                        pr_map[int(str(dep_num))] = dep_pr
            else:
                new_ops.insert(0, int(str(dep_num)))

        operation_prs = new_ops
        intake_prs    = [pr_map[n] for n in operation_prs if n in pr_map]
        print(f"\n  {ok(f'Updated cherry-pick order: {operation_prs}')}")

# ── Step 3: Create release branch ────────────────────────────────────────────
section(3, f"Creating release branch '{RELEASE_BRANCH}'")

# base_ref: explicit override > config base_branch (for exclude) or empty (include uses last tag)
base_ref = cfg.get("base_ref") or (BASE_BRANCH if STRATEGY == "exclude" else "")
branch_cmd = ["bash", str(SCRIPTS_DIR / "create_release_branch.sh"),
              "--version", VERSION, "--strategy", STRATEGY]
if base_ref:
    branch_cmd += ["--base-ref", base_ref]
if DRY_RUN:
    branch_cmd.append("--dry-run")
run(branch_cmd)
# ── Step 4: Execute git operations ───────────────────────────────────────────
section(4, f"Executing {op_type.upper()} operations on {len(operation_prs)} PR(s)")

# Write release plan for the LLM conflict resolver to use as context
plan_path = Path("/tmp/rdkb-release-conflicts/release_plan.json")
plan_path.parent.mkdir(parents=True, exist_ok=True)
try:
    with open(plan_path, "w") as pf:
        json.dump({
            "strategy": STRATEGY,
            "operation_prs": [
                {
                    "number": n,
                    "title": pr_map[n].get("title", ""),
                    "author": pr_map[n].get("author", {}).get("login", "")
                }
                for n in operation_prs if n in pr_map
            ]
        }, pf, indent=2)
except Exception as e:
    print(f"  {warn(f'Failed to write release plan: {e}')}")

conflicts_found  = []
skipped_prs      = []
successful_prs   = []
op_log           = []   # list of dicts for summary table

script = str(SCRIPTS_DIR / ("safe_revert.sh" if STRATEGY == "exclude" else "safe_cherry_pick.sh"))



for pr_num in operation_prs:
    pr = pr_map.get(pr_num)
    if not pr:
        print(f"  #{pr_num:>4}  {op_type:<12}  {'(not found in window)':<45}  {warn('SKIPPED')}")
        skipped_prs.append(pr_num)
        op_log.append({"pr": pr_num, "op": op_type, "status": "not-found", "note": "not in window"})
        continue

    sha    = pr.get("mergeCommit", {}).get("oid", "")
    title  = pr.get("title", "")[:45]
    author = pr.get("author", {}).get("login", "?")

    if not sha:
        print(f"  #{pr_num:>4}  {op_type:<12}  {title:<45}  {warn('SKIPPED — no SHA')}")
        skipped_prs.append(pr_num)
        op_log.append({"pr": pr_num, "op": op_type, "status": "no-sha", "note": "no merge commit SHA"})
        continue

    print(f"\n  {c(BOLD, f'PR #{pr_num}')} — {title}  {dim(f'@{author}')}")
    print(f"  {dim(f'SHA: {sha[:12]}...')}  {dim(f'Operation: {op_type}')}")

    op_cmd = ["bash", script, "--sha", sha, "--pr", str(pr_num),
              "--conflict-policy", CONFLICT_POLICY]
    if DRY_RUN:
        op_cmd.append("--dry-run")

    t0 = time.time()
    result = run(op_cmd, check=False)
    elapsed_op = time.time() - t0

    if result.returncode == 0:
        print(f"  {ok(f'PR #{pr_num} {op_type} completed in {elapsed_op:.1f}s')}")
        successful_prs.append(pr_num)
        op_log.append({"pr": pr_num, "op": op_type, "status": "ok",
                       "note": f"{elapsed_op:.1f}s"})
    elif result.returncode == 2:
        conflicts_found.append(pr_num)
        op_log.append({"pr": pr_num, "op": op_type, "status": "conflict",
                       "note": "auto-resolution failed"})
        if CONFLICT_POLICY in ("pause", "auto"):
            print(f"\n  {err(f'PR #{pr_num} — conflict could not be auto-resolved.')}")
            print(f"")
            print(f"  {c(BOLD, 'ACTION REQUIRED — Manual steps for component owner:')}")
            print(f"  {'─'*56}")
            print(f"  1. The release branch is: {c(CYAN, RELEASE_BRANCH)}")
            print(f"  2. Conflict is in PR #{pr_num}: {pr.get('title','')[:50]}")
            print(f"     URL: {pr.get('url','')}")
            print(f"  3. Manually apply the changes from PR #{pr_num}:")
            if STRATEGY == "exclude":
                sha_short = sha[:12]
                print(f"     git revert --no-edit -m 1 {sha_short}")
                print(f"     # Resolve conflicts, then:")
                print(f"     git revert --continue --no-edit")
            else:
                sha_short = sha[:12]
                print(f"     git cherry-pick -x -m 1 {sha_short}")
                print(f"     # Resolve conflicts, then:")
                print(f"     git cherry-pick --continue --no-edit")
            print(f"  4. Re-run the orchestrator after resolution.")
            print(f"  {'─'*56}")
            break
        else:
            print(f"  {warn(f'PR #{pr_num} — conflict, skipping (policy=skip)')}")
            skipped_prs.append(pr_num)
    else:
        print(f"  {err(f'PR #{pr_num} — unexpected error (exit {result.returncode}). Aborting.')}")
        op_log.append({"pr": pr_num, "op": op_type, "status": "error",
                       "note": f"exit {result.returncode}"})
        sys.exit(result.returncode)
else:
    pass  # loop completed normally — no break

# ── Operation summary table ───────────────────────────────────────────────────
print(f"\n  {'─'*64}")
print(f"  {'PR #':>6}  {'Operation':<12}  {'Status':<12}  {'Note'}")
print(f"  {'─'*6}  {'─'*12}  {'─'*12}  {'─'*20}")
STATUS_ICON = {"ok": ok("OK"), "conflict": warn("CONFLICT"), "no-sha": warn("NO SHA"),
               "not-found": warn("NOT FOUND"), "error": err("ERROR")}
for entry in op_log:
    status_str = str(entry.get("status", ""))
    icon = STATUS_ICON.get(status_str, status_str)
    print(f"  #{entry['pr']:>5}  {entry['op']:<12}  {icon:<12}  {dim(entry['note'])}")

if conflicts_found and CONFLICT_POLICY in ("pause", "auto"):
    print(f"\n  {err('Halted — unresolvable conflict on PR(s): ' + str(conflicts_found))}")
    print(f"  {warn('Fix the conflict above, then re-run the orchestrator.')}")
    sys.exit(2)

# ── Step 5: Generate report ───────────────────────────────────────────────────
section(5, "Generating release report")

intake_pr_numbers = [p["number"] if isinstance(p, dict) else int(p) for p in intake_prs]

report_file = f"release-report-{VERSION}.md"
report_cmd = [
    "python3", str(SCRIPTS_DIR / "generate_report.py"),
    "--version", VERSION,
    "--repo", args.repo,
    "--strategy", STRATEGY,
    "--output", report_file,
    "--config", str(config_path),
    "--intake-prs",   json.dumps([str(n) for n in intake_pr_numbers]),
    "--conflict-prs", json.dumps([str(p) for p in conflicts_found]),
    "--dep-findings", json.dumps(dep_findings),
]
run(report_cmd)


# ── Step 6: Push & open draft PR ─────────────────────────────────────────────
section(6, f"Pushing branch & opening draft PR: {RELEASE_BRANCH} → main")

if not DRY_RUN:
    print(f"  {info(f'Pushing {RELEASE_BRANCH} to origin...')}")
    run(["git", "push", "origin", RELEASE_BRANCH], check=False)

    ahead_result = subprocess.run(
        ["git", "rev-list", "--count", f"origin/main..origin/{RELEASE_BRANCH}"],
        capture_output=True, text=True
    )
    commits_ahead = int(ahead_result.stdout.strip() or "0")
    print(f"  {info(f'Branch is {commits_ahead} commit(s) ahead of main')}")

    if commits_ahead == 0:
        print(f"  {warn('No new commits vs main — skipping draft PR creation.')}")
        print(f"  {dim('Tip: This happens when all cherry-picks were empty (file already absent in base).')}")
    else:
        notify  = " ".join(cfg.get("notify", []))
        pr_body = Path(report_file).read_text()
        if notify:
            pr_body += f"\n\n---\ncc: {notify}"
        pr_body_file = f"/tmp/rdkb-pr-body-{VERSION}.md"
        Path(pr_body_file).write_text(pr_body)

        run([
            "gh", "pr", "create",
            "--repo", args.repo,
            "--base", "main",
            "--head", RELEASE_BRANCH,
            "--title", f"Release {VERSION}",
            "--body-file", pr_body_file,
            "--draft"
        ])
else:
    print(f"  {dim('[DRY-RUN] Would push branch and open draft PR')}")

# ── Final Summary ─────────────────────────────────────────────────────────────
elapsed_total = time.time() - START_TIME
banner("Release Orchestration Complete")
print(f"  {'Branch':<20}: {RELEASE_BRANCH}")
print(f"  {'Strategy':<20}: {STRATEGY.upper()} ({op_type})")
print(f"  {'PRs in release':<20}: {len(intake_prs)}")
print(f"  {'Successful ops':<20}: {c(GREEN, str(len(successful_prs)))}  {successful_prs}")
print(f"  {'Conflicts':<20}: {c(RED if conflicts_found else GREEN, str(len(conflicts_found)))}  {conflicts_found}")
print(f"  {'Skipped':<20}: {c(YELLOW if skipped_prs else GREEN, str(len(skipped_prs)))}  {skipped_prs}")
print(f"  {'Report':<20}: {report_file}")
print(f"  {'Total time':<20}: {elapsed_total:.1f}s")
print(c(BOLD, "═" * 64))
