#!/usr/bin/env python3
"""
smart_merge.py
==============
Semantic-aware conflict resolution module for the RDK-B release agent.

Analyzes conflict hunks in C source files and makes intelligent merge
decisions based on the nature of each change (whitespace, includes,
NULL checks, functional, etc.).

Each resolution is tagged with a confidence level:
  HIGH   — safe to auto-resolve (whitespace, include ordering, both-sides merge)
  MEDIUM — likely correct (safety improvements, comment changes)
  LOW    — fallback to prefer strategy (truly conflicting functional changes)
"""

import re
from enum import Enum
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


# ── Confidence levels ─────────────────────────────────────────────────────────

class Confidence(Enum):
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    REVIEW = "REVIEW"
    LOW    = "LOW"

    def __ge__(self, other):
        order = {"HIGH": 4, "MEDIUM": 3, "REVIEW": 2, "LOW": 1}
        return order[self.value] >= order[other.value]

    def __gt__(self, other):
        order = {"HIGH": 4, "MEDIUM": 3, "REVIEW": 2, "LOW": 1}
        return order[self.value] > order[other.value]

    def __le__(self, other):
        return not self.__gt__(other)

    def __lt__(self, other):
        return not self.__ge__(other)


# ── Change classification ─────────────────────────────────────────────────────

class ChangeType(Enum):
    WHITESPACE_ONLY     = "whitespace_only"
    INCLUDE_REORDER     = "include_reorder"
    COMMENT_ONLY        = "comment_only"
    NULL_CHECK_ADDED    = "null_check_added"
    ERROR_HANDLING      = "error_handling"
    BRACE_STYLE         = "brace_style"
    FUNCTIONAL          = "functional"
    MIXED               = "mixed"


@dataclass
class ResolutionResult:
    """Result of resolving a single conflict hunk."""
    resolved_lines: List[str]
    confidence: Confidence
    change_type: ChangeType
    reason: str


@dataclass
class HunkResolution:
    """Resolution record for logging/reporting."""
    filepath: str
    hunk_index: int
    strategy: str           # "merge_both", "prefer_ours", "prefer_theirs", "prefer_safety"
    confidence: Confidence
    change_type: ChangeType
    reason: str


# ── Pattern matchers ──────────────────────────────────────────────────────────

# Matches #include lines
RE_INCLUDE = re.compile(r'^\s*#\s*include\s+[<"].*[>"]')

# Matches comment-only lines
RE_COMMENT_LINE = re.compile(r'^\s*(/\*.*\*/|//.*|\*.*|\*/)\s*$')
RE_COMMENT_START = re.compile(r'^\s*/\*')
RE_COMMENT_END   = re.compile(r'.*\*/\s*$')

# Matches NULL checks
RE_NULL_CHECK = re.compile(
    r'(if\s*\(\s*\!?\s*\w+\s*(==|!=)\s*NULL\s*\)|'
    r'if\s*\(\s*NULL\s*(==|!=)\s*\w+\s*\)|'
    r'if\s*\(\s*\!\s*\w+\s*\)|'
    r'if\s*\(\s*\w+\s*\))',
    re.IGNORECASE
)

# Matches error handling patterns
RE_ERROR_HANDLING = re.compile(
    r'(return\s+ANSC_STATUS_FAILURE|'
    r'return\s+(-1|NULL|false|FALSE)|'
    r'exit\s*\(\s*1\s*\)|'
    r'CcspTraceError|CcspTraceWarning|'
    r'ERR_CHK|'
    r'goto\s+\w+error\w*|goto\s+\w+fail\w*)',
    re.IGNORECASE
)

# Matches pure whitespace/formatting differences
RE_ONLY_SPACES = re.compile(r'^[\s]*$')


# ── Core analysis functions ───────────────────────────────────────────────────

def _normalize_whitespace(line: str) -> str:
    """Strip all whitespace for comparison."""
    return re.sub(r'\s+', '', line.rstrip('\n'))


def _is_whitespace_only_diff(lines_a: List[str], lines_b: List[str]) -> bool:
    """Check if two sets of lines differ only in whitespace/formatting."""
    norm_a = [_normalize_whitespace(l) for l in lines_a if _normalize_whitespace(l)]
    norm_b = [_normalize_whitespace(l) for l in lines_b if _normalize_whitespace(l)]
    return norm_a == norm_b


def _all_includes(lines: List[str]) -> bool:
    """Check if all non-empty lines are #include directives."""
    non_empty = [l for l in lines if l.strip()]
    return len(non_empty) > 0 and all(RE_INCLUDE.match(l) for l in non_empty)


def _all_comments(lines: List[str]) -> bool:
    """Check if all non-empty lines are comments."""
    non_empty = [l for l in lines if l.strip()]
    if not non_empty:
        return False
    in_block = False
    for line in non_empty:
        stripped = line.strip()
        if in_block:
            if RE_COMMENT_END.match(line):
                in_block = False
            continue
        if RE_COMMENT_LINE.match(line):
            continue
        if RE_COMMENT_START.match(line):
            if not RE_COMMENT_END.match(line):
                in_block = True
            continue
        return False
    return True


def _has_null_check(lines: List[str]) -> bool:
    """Check if any line contains a NULL/pointer validation pattern."""
    return any(RE_NULL_CHECK.search(l) for l in lines)


def _has_error_handling(lines: List[str]) -> bool:
    """Check if any line contains error-handling patterns."""
    return any(RE_ERROR_HANDLING.search(l) for l in lines)


def _extract_includes(lines: List[str]) -> List[str]:
    """Extract #include lines, preserving their content."""
    return [l.rstrip('\n') for l in lines if RE_INCLUDE.match(l)]


def classify_hunk_change(ours_lines: List[str], theirs_lines: List[str]) -> ChangeType:
    """
    Classify the nature of changes in a conflict hunk.

    Returns the dominant change type to guide resolution strategy.
    """
    # Check whitespace-only difference
    if _is_whitespace_only_diff(ours_lines, theirs_lines):
        return ChangeType.WHITESPACE_ONLY

    # Check if both sides are #include blocks
    if _all_includes(ours_lines) and _all_includes(theirs_lines):
        return ChangeType.INCLUDE_REORDER

    # Check if one side is includes and other is too (with different order/additions)
    ours_inc = _all_includes(ours_lines)
    theirs_inc = _all_includes(theirs_lines)
    if ours_inc or theirs_inc:
        # At least one side is purely includes
        if ours_inc and theirs_inc:
            return ChangeType.INCLUDE_REORDER

    # Check comment-only changes
    if _all_comments(ours_lines) and _all_comments(theirs_lines):
        return ChangeType.COMMENT_ONLY

    # Check if one side adds NULL checks that the other doesn't have
    ours_null = _has_null_check(ours_lines)
    theirs_null = _has_null_check(theirs_lines)
    if theirs_null and not ours_null:
        return ChangeType.NULL_CHECK_ADDED
    if ours_null and not theirs_null:
        return ChangeType.NULL_CHECK_ADDED

    # Check if one side adds error handling
    ours_err = _has_error_handling(ours_lines)
    theirs_err = _has_error_handling(theirs_lines)
    if (theirs_err and not ours_err) or (ours_err and not theirs_err):
        return ChangeType.ERROR_HANDLING

    # Check brace style changes (K&R vs Allman)
    norm_a = [_normalize_whitespace(l) for l in ours_lines if _normalize_whitespace(l)]
    norm_b = [_normalize_whitespace(l) for l in theirs_lines if _normalize_whitespace(l)]
    # If only braces and whitespace differ
    brace_only_a = [l for l in norm_a if l not in ('{', '}')]
    brace_only_b = [l for l in norm_b if l not in ('{', '}')]
    if brace_only_a == brace_only_b and norm_a != norm_b:
        return ChangeType.BRACE_STYLE

    return ChangeType.FUNCTIONAL


def can_merge_both_sides(ours_lines: List[str], theirs_lines: List[str]) -> bool:
    """
    Check if both sides' changes can coexist without conflict.

    Returns True if the changes are additive and non-overlapping.
    This is conservative — returns True only for high-confidence cases.
    """
    # If one side is empty, trivially mergeable
    if not ours_lines or not theirs_lines:
        return True

    # If changes are whitespace-only, they're compatible
    if _is_whitespace_only_diff(ours_lines, theirs_lines):
        return True

    # If both are include blocks, we can merge them
    if _all_includes(ours_lines) and _all_includes(theirs_lines):
        return True

    return False


def merge_includes(ours_lines: List[str], theirs_lines: List[str]) -> List[str]:
    """
    Merge two sets of #include lines.

    - Deduplicates
    - Groups: system includes (<...>) first, then local includes ("...")
    - Preserves conditional compilation blocks (#ifdef)
    """
    ours_includes  = _extract_includes(ours_lines)
    theirs_includes = _extract_includes(theirs_lines)

    # Collect all unique includes
    seen = set()
    merged = []
    for inc in ours_includes + theirs_includes:
        normalized = _normalize_whitespace(inc)
        if normalized not in seen:
            seen.add(normalized)
            merged.append(inc)

    # Sort: system includes first (<...>), then local ("...")
    system_inc = [i for i in merged if '<' in i and '>' in i]
    local_inc  = [i for i in merged if '"' in i]
    other_inc  = [i for i in merged if i not in system_inc and i not in local_inc]

    result = []
    if local_inc:
        result.extend(sorted(local_inc, key=lambda x: x.strip().lower()))
    if system_inc:
        result.extend(sorted(system_inc, key=lambda x: x.strip().lower()))
    if other_inc:
        result.extend(other_inc)

    return [line + '\n' for line in result]


def detect_safety_improvement(lines: List[str]) -> bool:
    """
    Detect if a set of lines represents a safety improvement.

    Safety improvements include:
    - Adding NULL parameter checks
    - Adding bounds checks
    - Replacing unsafe functions (strncpy → snprintf)
    - Adding error handling (return on failure)
    - Closing resource leaks (close(fd), free(ptr))
    """
    safety_patterns = [
        r'if\s*\(\s*\!\s*\w+\s*\)',           # if (!ptr)
        r'if\s*\(\s*\w+\s*==\s*NULL',          # if (ptr == NULL)
        r'if\s*\(\s*NULL\s*==',                 # if (NULL == ptr)
        r'snprintf\s*\(',                       # snprintf (safer than strncpy)
        r'close\s*\(\s*\w+\s*\)',               # close(fd)
        r'free\s*\(\s*\w+\s*\)',                # free(ptr)
        r'exit\s*\(\s*1\s*\)',                   # exit(1) instead of exit(0)
        r'va_end\s*\(',                          # va_end cleanup
        r'fclose\s*\(',                          # fclose cleanup
    ]
    combined = re.compile('|'.join(safety_patterns), re.IGNORECASE)
    return any(combined.search(l) for l in lines)


# ── Main resolution function ─────────────────────────────────────────────────

def resolve_hunk(
    ours_lines: List[str],
    theirs_lines: List[str],
    mode: str,                  # "cherry-pick" or "revert"
    safety_prefer: bool = True,
    min_confidence: str = "low",
    llm_resolver = None,        # Optional LLMResolver instance
    filepath: str = "",         # File path for LLM context
    context_before: List[str] = None,  # Lines before conflict
    context_after: List[str] = None,   # Lines after conflict
    pr_context_str: str = "",          # PR context string for LLM
) -> ResolutionResult:
    """
    Resolve a single conflict hunk using semantic analysis.

    Args:
        ours_lines:     Lines from the "ours" side of the conflict
        theirs_lines:   Lines from the "theirs" side of the conflict
        mode:           "cherry-pick" or "revert"
        safety_prefer:  Prefer the side with safety improvements
        min_confidence: Minimum confidence level to accept ("high", "medium", "low")

    Returns:
        ResolutionResult with resolved lines, confidence, and rationale
    """
    change_type = classify_hunk_change(ours_lines, theirs_lines)

    # ── Strategy 1: Whitespace-only → keep either side (they're equivalent) ──
    if change_type == ChangeType.WHITESPACE_ONLY:
        # Prefer the side that's more consistently formatted
        preferred = theirs_lines if mode == "cherry-pick" else ours_lines
        return ResolutionResult(
            resolved_lines=preferred,
            confidence=Confidence.HIGH,
            change_type=change_type,
            reason="Changes are whitespace/formatting only — semantically identical"
        )

    # ── Strategy 2: Include reorder → merge and sort ────────────────────────
    if change_type == ChangeType.INCLUDE_REORDER:
        merged = merge_includes(ours_lines, theirs_lines)
        return ResolutionResult(
            resolved_lines=merged,
            confidence=Confidence.HIGH,
            change_type=change_type,
            reason="Both sides modify #include order — merged and deduplicated"
        )

    # ── Strategy 3: Comment-only → prefer the more descriptive side ──────────
    if change_type == ChangeType.COMMENT_ONLY:
        # Prefer whichever side has more content (more descriptive)
        preferred = theirs_lines if len(''.join(theirs_lines)) >= len(''.join(ours_lines)) else ours_lines
        label = "theirs" if preferred is theirs_lines else "ours"
        return ResolutionResult(
            resolved_lines=preferred,
            confidence=Confidence.HIGH,
            change_type=change_type,
            reason=f"Both sides are comment changes — kept {label} (more descriptive)"
        )

    # ── Strategy 4: Brace style → keep whichever matches project convention ──
    if change_type == ChangeType.BRACE_STYLE:
        # RDK-B convention: Allman style (brace on new line)
        # Prefer whichever has more newlines (Allman-style has more)
        preferred = ours_lines if len(ours_lines) >= len(theirs_lines) else theirs_lines
        label = "ours" if preferred is ours_lines else "theirs"
        return ResolutionResult(
            resolved_lines=preferred,
            confidence=Confidence.MEDIUM,
            change_type=change_type,
            reason=f"Brace style difference — kept {label} (Allman-style preference)"
        )

    # ── Strategy 5: Safety improvement → prefer the safer side ───────────────
    if safety_prefer and change_type in (ChangeType.NULL_CHECK_ADDED, ChangeType.ERROR_HANDLING):
        ours_safe   = detect_safety_improvement(ours_lines)
        theirs_safe = detect_safety_improvement(theirs_lines)

        if theirs_safe and not ours_safe:
            return ResolutionResult(
                resolved_lines=theirs_lines,
                confidence=Confidence.MEDIUM,
                change_type=change_type,
                reason="Theirs adds safety checks (NULL/error handling) — preferred for robustness"
            )
        elif ours_safe and not theirs_safe:
            return ResolutionResult(
                resolved_lines=ours_lines,
                confidence=Confidence.MEDIUM,
                change_type=change_type,
                reason="Ours adds safety checks (NULL/error handling) — preferred for robustness"
            )
        elif ours_safe and theirs_safe:
            # Both add safety — try to merge
            if can_merge_both_sides(ours_lines, theirs_lines):
                merged = ours_lines + theirs_lines
                return ResolutionResult(
                    resolved_lines=merged,
                    confidence=Confidence.MEDIUM,
                    change_type=change_type,
                    reason="Both sides add safety improvements — merged both"
                )

    # ── Strategy 6: Non-overlapping functional changes → try merge ────────────
    if can_merge_both_sides(ours_lines, theirs_lines):
        preferred = theirs_lines if mode == "cherry-pick" else ours_lines
        return ResolutionResult(
            resolved_lines=preferred,
            confidence=Confidence.HIGH,
            change_type=change_type,
            reason="Changes are compatible — merged successfully"
        )

    # ── Strategy 7: LLM-powered resolution (functional conflicts) ────────────
    if llm_resolver is not None and change_type == ChangeType.FUNCTIONAL:
        try:
            llm_result = llm_resolver.resolve_conflict(
                filepath=filepath,
                ours_lines=ours_lines,
                theirs_lines=theirs_lines,
                context_before=context_before,
                context_after=context_after,
                mode=mode,
            )
            if llm_result and llm_result.valid:
                return ResolutionResult(
                    resolved_lines=llm_result.lines,
                    confidence=Confidence.MEDIUM,
                    change_type=ChangeType.FUNCTIONAL,
                    reason=f"LLM-resolved ({llm_result.provider}/{llm_result.model}): {llm_result.rationale}"
                )
            elif llm_result:
                # LLM attempted but failed strict validation; downgrade to REVIEW instead of LOW
                return ResolutionResult(
                    resolved_lines=llm_result.lines if llm_result.lines else (theirs_lines if mode == "cherry-pick" else ours_lines),
                    confidence=Confidence.REVIEW,
                    change_type=ChangeType.FUNCTIONAL,
                    reason=f"LLM-assumed ({llm_result.provider}/{llm_result.model}) but failed strict validation. {llm_result.rationale}"
                )
        except Exception as e:
            # LLM threw an exception (rate limits, context window, API down)
            pass

    # ── Fallback: prefer ours/theirs based on mode ───────────
    preferred = theirs_lines if mode == "cherry-pick" else ours_lines
    label = "theirs (incoming PR)" if mode == "cherry-pick" else "ours (current branch)"
    
    # If the LLM wasn't instantiated or failed severely, fallback to REVIEW to prompt the owner.
    # We reserve LOW strictly for impossible non-text conflicts or system-halting panics.
    return ResolutionResult(
        resolved_lines=preferred,
        confidence=Confidence.REVIEW,
        change_type=change_type,
        reason=f"Functional conflict — fallback to {label}. Manual review required."
    )


def format_resolution_rationale(
    filepath: str,
    hunk_idx: int,
    result: ResolutionResult
) -> str:
    """Format a human-readable log entry for a hunk resolution."""
    return (
        f"  [{result.confidence.value:6s}] {filepath} hunk#{hunk_idx}: "
        f"{result.change_type.value} → {result.reason}"
    )
