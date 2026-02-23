#!/usr/bin/env python3
"""
resolve_conflicts.py
====================
Intelligent conflict resolution engine for the RDK-B release agent.

Handles ALL git conflict types for both cherry-pick (include) and revert (exclude):

  CHERRY-PICK (include mode):
    modify/delete  → Accept PR's version (theirs) — the file exists in the PR
    delete/modify  → Accept the deletion (rm)     — PR deleted it intentionally
    add/add        → Accept PR's version (theirs) — PR is the source of truth
    modify/modify  → Smart merge with C-aware semantic analysis
    rename/rename  → Accept PR's rename (theirs)
    rename/delete  → Accept PR's rename (theirs)

  REVERT (exclude mode):
    modify/delete  → Accept base version (ours)   — we're keeping the file
    delete/modify  → Accept the deletion (rm)     — revert removes it
    add/add        → Accept base version (ours)   — we already have it
    modify/modify  → Smart merge with C-aware semantic analysis
    rename/rename  → Accept base rename (ours)

Smart merge (--smart):
    When enabled, modify/modify conflicts use semantic C-code analysis to make
    intelligent resolution decisions based on change type (whitespace, includes,
    NULL checks, etc.) with confidence scoring (HIGH/MEDIUM/LOW).
"""

import subprocess
import sys
import os
import re
import json
import argparse
from pathlib import Path
from datetime import datetime

# ── Args ──────────────────────────────────────────────────────────────────────
parser = argparse.ArgumentParser()
parser.add_argument("--mode", choices=["cherry-pick", "revert"], required=True,
                    help="Operation mode: cherry-pick (include) or revert (exclude)")
parser.add_argument("--pr", required=True, help="PR number for logging")
parser.add_argument("--smart", action="store_true", default=False,
                    help="Enable semantic-aware smart merge for C files")
parser.add_argument("--min-confidence", choices=["high", "medium", "review", "low"],
                    default="low",
                    help="Minimum confidence level to accept auto-resolution")
parser.add_argument("--safety-prefer", action="store_true", default=True,
                    help="Prefer side with safety improvements (NULL checks, etc.)")
parser.add_argument("--log-dir", default="/tmp/rdkb-release-conflicts",
                    help="Directory to write resolution logs")
parser.add_argument("--config", default="", help="Path to .release-config.yml for LLM settings")
args = parser.parse_args()

MODE    = args.mode
PR_NUM  = args.pr
SMART   = args.smart
LOG_DIR = Path(args.log_dir)
LOG_DIR.mkdir(parents=True, exist_ok=True)

# In cherry-pick: "theirs" = the incoming PR commit
# In revert:      "ours"   = the current branch (what we want to keep)
PREFER = "theirs" if MODE == "cherry-pick" else "ours"

# Import smart merge if enabled
smart_merge = None
if SMART:
    try:
        script_dir = Path(__file__).parent
        sys.path.insert(0, str(script_dir))
        import smart_merge as sm
        smart_merge = sm
        print(f"\n  🧠 Smart Merge Engine ACTIVE (mode={MODE}, prefer={PREFER}, "
              f"min_confidence={args.min_confidence})")
    except ImportError as e:
        print(f"\n  ⚠️  Smart merge requested but import failed: {e}")
        print(f"  Falling back to standard resolve mode.")
        SMART = False

if not SMART:
    print(f"\n  🤖 Conflict Resolution Engine (mode={MODE}, prefer={PREFER})")

# Import LLM resolver if config enables it
llm_resolver_instance = None
if SMART and args.config:
    try:
        import yaml
        config_path = Path(args.config)
        if config_path.exists():
            with open(config_path) as cf:
                release_cfg = yaml.safe_load(cf) or {}
                
            # Load release plan
            plan_data = None
            plan_file = Path("/tmp/rdkb-release-conflicts/release_plan.json")
            if plan_file.exists():
                with open(plan_file, "r") as pf:
                    plan_data = json.load(pf)
                    
            from llm_resolver import create_resolver_from_config
            llm_resolver_instance = create_resolver_from_config(release_cfg, release_plan=plan_data)
            if llm_resolver_instance:
                print(f"  🤖 LLM resolver ACTIVE "
                      f"({llm_resolver_instance.provider}/{llm_resolver_instance.model}, "
                      f"max {llm_resolver_instance.max_calls} calls)")
    except ImportError:
        pass  # yaml or llm_resolver not available — LLM disabled
    except Exception as e:
        print(f"  ⚠️  LLM init error: {e}. Continuing without LLM.")

# ── Helpers ───────────────────────────────────────────────────────────────────
def run(cmd, check=True):
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"  ⚠️  Command failed: {' '.join(cmd)}")
        print(f"      {result.stderr.strip()}")
    return result

def git_status_porcelain():
    """Returns list of (XY, filename) tuples from git status --porcelain."""
    result = run(["git", "status", "--porcelain"], check=False)
    entries = []
    for line in result.stdout.splitlines():
        if len(line) >= 4:
            xy   = line[:2]
            path = line[3:].strip().strip('"')
            # Handle rename: "old -> new"
            if " -> " in path:
                path = path.split(" -> ")[-1]
            entries.append((xy, path))
    return entries

def resolve_with_strategy(filepath, strategy):
    """
    Resolve a single file conflict using git checkout --ours/--theirs.
    strategy: 'ours' or 'theirs'
    """
    result = run(["git", "checkout", f"--{strategy}", "--", filepath], check=False)
    if result.returncode == 0:
        run(["git", "add", filepath])
        return True
    return False

def _is_c_source(filepath):
    """Check if the file is a C/C++ source or header."""
    ext = Path(filepath).suffix.lower()
    return ext in ('.c', '.h', '.cpp', '.hpp', '.cc', '.cxx')

def _confidence_meets_minimum(confidence, min_level):
    """Check if a confidence level meets the minimum threshold."""
    order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    return order.get(confidence.value, 0) >= order.get(min_level.upper(), 1)


# ── Smart modify/modify resolution ───────────────────────────────────────────
# Resolution records for reporting
resolution_records = []

def resolve_modify_modify_smart(filepath):
    """
    Semantic-aware conflict resolution for modify/modify conflicts.

    Uses smart_merge module to analyze each conflict hunk and resolve
    based on the nature of the change (whitespace, includes, safety, etc.).

    Returns (success: bool, below_confidence: bool)
    """
    try:
        with open(filepath, "r", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"    ⚠️  Cannot read {filepath}: {e}")
        return False, False

    if "<<<<<<< " not in content:
        run(["git", "add", filepath])
        return True, False

    min_conf_str = args.min_confidence.upper()

    # Parse conflict hunks
    resolved_lines = []
    hunk_idx = 0
    in_ours    = False
    in_theirs  = False
    ours_lines   = []
    theirs_lines = []
    any_below_min = False

    all_lines = content.splitlines(keepends=True)

    for line_idx, line in enumerate(all_lines):
        if line.startswith("<<<<<<< "):
            in_ours  = True
            ours_lines   = []
            theirs_lines = []
            # Capture context before this conflict (up to 10 lines)
            conflict_start_idx = line_idx
        elif line.startswith("=======") and in_ours:
            in_ours   = False
            in_theirs = True
        elif line.startswith(">>>>>>> ") and in_theirs:
            in_theirs = False
            hunk_idx += 1

            # Gather surrounding context for LLM
            ctx_before = all_lines[max(0, conflict_start_idx - 10):conflict_start_idx]
            ctx_after  = all_lines[line_idx + 1:line_idx + 11]

            # Use smart_merge to resolve this hunk
            result = smart_merge.resolve_hunk(
                ours_lines=ours_lines,
                theirs_lines=theirs_lines,
                mode=MODE,
                safety_prefer=args.safety_prefer,
                min_confidence=args.min_confidence,
                llm_resolver=llm_resolver_instance,
                filepath=filepath,
                context_before=ctx_before,
                context_after=ctx_after,
            )

            # Check confidence threshold
            if not _confidence_meets_minimum(result.confidence, min_conf_str):
                any_below_min = True
                print(f"    ⚠️  Hunk #{hunk_idx}: {result.confidence.value} confidence "
                      f"below minimum ({min_conf_str})")

            resolved_lines.extend(result.resolved_lines)

            # Log the resolution
            rationale = smart_merge.format_resolution_rationale(
                filepath, hunk_idx, result
            )
            print(f"    {rationale}")

            resolution_records.append({
                "file": filepath,
                "pr": PR_NUM,
                "hunk": hunk_idx,
                "change_type": result.change_type.value,
                "confidence": result.confidence.value,
                "reason": result.reason
            })

            ours_lines   = []
            theirs_lines = []
        elif in_ours:
            ours_lines.append(line)
        elif in_theirs:
            theirs_lines.append(line)
        else:
            resolved_lines.append(line)

    if any_below_min:
        return False, True

    try:
        with open(filepath, "w") as f:
            f.writelines(resolved_lines)
        run(["git", "add", filepath])
        return True, False
    except Exception as e:
        print(f"    ⚠️  Cannot write resolved {filepath}: {e}")
        return False, False


# ── Standard modify/modify resolution (fallback) ─────────────────────────────

def resolve_modify_modify(filepath):
    """
    Standard line-level conflict resolution for modify/modify conflicts.
    Strategy:
      - Remove conflict markers
      - For cherry-pick: keep 'theirs' (incoming) side for each conflict hunk
      - For revert:      keep 'ours'   (current)  side for each conflict hunk
      - Keep all non-conflicting lines intact
    """
    try:
        with open(filepath, "r", errors="replace") as f:
            content = f.read()
    except Exception as e:
        print(f"    ⚠️  Cannot read {filepath}: {e}")
        return False

    if "<<<<<<< " not in content:
        # No conflict markers — already resolved or binary
        run(["git", "add", filepath])
        return True

    resolved_lines = []
    in_ours    = False
    in_theirs  = False
    ours_lines   = []
    theirs_lines = []

    for line in content.splitlines(keepends=True):
        if line.startswith("<<<<<<< "):
            in_ours  = True
            ours_lines   = []
            theirs_lines = []
        elif line.startswith("=======") and in_ours:
            in_ours   = False
            in_theirs = True
        elif line.startswith(">>>>>>> ") and in_theirs:
            in_theirs = False
            # Choose which side to keep
            if PREFER == "theirs":
                resolved_lines.extend(theirs_lines)
            else:
                resolved_lines.extend(ours_lines)
            ours_lines   = []
            theirs_lines = []
        elif in_ours:
            ours_lines.append(line)
        elif in_theirs:
            theirs_lines.append(line)
        else:
            resolved_lines.append(line)

    try:
        with open(filepath, "w") as f:
            f.writelines(resolved_lines)
        run(["git", "add", filepath])
        return True
    except Exception as e:
        print(f"    ⚠️  Cannot write resolved {filepath}: {e}")
        return False


# ── Main resolution loop ──────────────────────────────────────────────────────
entries      = git_status_porcelain()
resolved     = []
unresolvable = []

if not entries:
    print("  ℹ️  No conflicting files found.")
    sys.exit(0)

print(f"  Found {len(entries)} conflicting file(s):")

for xy, filepath in entries:
    # Only process conflict states (U = unmerged)
    if "U" not in xy and xy not in ("AA", "DD"):
        continue

    print(f"\n  📄 {filepath}  [{xy}]")

    # ── modify/delete: DU = deleted in ours, modified in theirs ──────────────
    if xy == "DU":
        if MODE == "cherry-pick":
            # PR added/modified this file → accept it
            print(f"    → modify/delete: accepting PR's version (checkout --theirs)")
            if resolve_with_strategy(filepath, "theirs"):
                resolved.append((filepath, "modify/delete → accepted PR version"))
            else:
                unresolvable.append((filepath, "modify/delete → checkout --theirs failed"))
        else:
            # Revert: we want to keep our version
            print(f"    → modify/delete: keeping current version (checkout --ours)")
            if resolve_with_strategy(filepath, "ours"):
                resolved.append((filepath, "modify/delete → kept current version"))
            else:
                unresolvable.append((filepath, "modify/delete → checkout --ours failed"))

    # ── delete/modify: UD = modified in ours, deleted in theirs ──────────────
    elif xy == "UD":
        if MODE == "cherry-pick":
            # PR deleted this file → accept the deletion
            print(f"    → delete/modify: PR deleted this file → accepting deletion (git rm)")
            run(["git", "rm", "-f", filepath], check=False)
            resolved.append((filepath, "delete/modify → accepted PR deletion"))
        else:
            # Revert: keep the file
            print(f"    → delete/modify: keeping current version (checkout --ours)")
            if resolve_with_strategy(filepath, "ours"):
                resolved.append((filepath, "delete/modify → kept current version"))
            else:
                unresolvable.append((filepath, "delete/modify → checkout --ours failed"))

    # ── add/add: AA = both sides added the file ───────────────────────────────
    elif xy == "AA":
        print(f"    → add/add: both sides added this file → prefer {PREFER}")
        if resolve_with_strategy(filepath, PREFER):
            resolved.append((filepath, f"add/add → kept {PREFER} version"))
        else:
            # Fall back to marker resolution
            print(f"    → falling back to marker-based resolution")
            if resolve_modify_modify(filepath):
                resolved.append((filepath, f"add/add → marker-resolved (prefer {PREFER})"))
            else:
                unresolvable.append((filepath, "add/add → could not resolve"))

    # ── both deleted: DD ──────────────────────────────────────────────────────
    elif xy == "DD":
        print(f"    → both deleted: removing file")
        run(["git", "rm", "-f", filepath], check=False)
        resolved.append((filepath, "both-deleted → removed"))

    # ── modify/modify: UU = both sides modified same lines ───────────────────
    elif xy == "UU":
        if SMART and _is_c_source(filepath):
            print(f"    → modify/modify: 🧠 Smart merge (C-aware semantic analysis)")
            success, below_min = resolve_modify_modify_smart(filepath)
            if success:
                resolved.append((filepath, f"modify/modify → smart-resolved"))
            elif below_min:
                # Confidence too low — try standard fallback
                print(f"    → Smart merge below confidence threshold, "
                      f"falling back to standard")
                if resolve_modify_modify(filepath):
                    resolved.append((filepath,
                        f"modify/modify → fallback-resolved (prefer {PREFER})"))
                else:
                    unresolvable.append((filepath,
                        "modify/modify → smart + fallback resolution both failed"))
            else:
                unresolvable.append((filepath,
                    "modify/modify → smart resolution failed"))
        elif SMART and not _is_c_source(filepath):
            # Smart mode but non-C file → use standard with note
            print(f"    → modify/modify: non-C file, using standard resolver "
                  f"(prefer {PREFER})")
            if resolve_modify_modify(filepath):
                resolved.append((filepath,
                    f"modify/modify → marker-resolved (prefer {PREFER})"))
            else:
                unresolvable.append((filepath,
                    "modify/modify → marker resolution failed"))
        else:
            print(f"    → modify/modify: resolving conflict markers "
                  f"(prefer {PREFER})")
            if resolve_modify_modify(filepath):
                resolved.append((filepath,
                    f"modify/modify → marker-resolved (prefer {PREFER})"))
            else:
                unresolvable.append((filepath,
                    "modify/modify → marker resolution failed"))

    # ── rename conflicts ──────────────────────────────────────────────────────
    elif xy in ("RD", "DR", "RR"):
        print(f"    → rename conflict [{xy}]: prefer {PREFER}")
        if resolve_with_strategy(filepath, PREFER):
            resolved.append((filepath, f"rename conflict → kept {PREFER}"))
        else:
            unresolvable.append((filepath, f"rename conflict → could not resolve"))

    else:
        print(f"    ⚠️  Unknown conflict state [{xy}] — skipping")
        unresolvable.append((filepath, f"unknown state [{xy}]"))

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n  ✅ Auto-resolved: {len(resolved)} file(s)")
for f, reason in resolved:
    print(f"     • {f}: {reason}")

if unresolvable:
    print(f"\n  ❌ Could not resolve: {len(unresolvable)} file(s)")
    for f, reason in unresolvable:
        print(f"     • {f}: {reason}")

# ── Write resolution log ──────────────────────────────────────────────────────
log_file = LOG_DIR / "auto_resolved.log"
with open(log_file, "a") as lf:
    for f, reason in resolved:
        lf.write(f"PR#{PR_NUM}|RESOLVED|{f}|{reason}\n")
    for f, reason in unresolvable:
        lf.write(f"PR#{PR_NUM}|FAILED|{f}|{reason}\n")

# ── Write smart resolution JSON (for report generation) ──────────────────────
if resolution_records:
    json_file = LOG_DIR / f"smart_resolution_pr{PR_NUM}.json"
    report_data = {
        "pr": PR_NUM,
        "mode": MODE,
        "smart": SMART,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "resolutions": resolution_records,
        "summary": {
            "total_hunks": len(resolution_records),
            "high_confidence": sum(1 for r in resolution_records
                                   if r["confidence"] == "HIGH"),
            "medium_confidence": sum(1 for r in resolution_records
                                     if r["confidence"] == "MEDIUM"),
            "review_confidence": sum(1 for r in resolution_records
                                     if r["confidence"] == "REVIEW"),
            "low_confidence": sum(1 for r in resolution_records
                                  if r["confidence"] == "LOW"),
        }
    }
    with open(json_file, "w") as jf:
        json.dump(report_data, jf, indent=2)
    print(f"\n  📊 Smart resolution report: {json_file}")

# ── Exit code ─────────────────────────────────────────────────────────────────
if unresolvable:
    sys.exit(2)   # Partial resolution — caller should abort cherry-pick
else:
    sys.exit(0)   # All resolved — caller should run git cherry-pick --continue
