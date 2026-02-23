#!/usr/bin/env python3
"""
code_pattern_analyzer.py
=========================
Semantic code pattern analysis for C/C++ code in PR diffs.

This module provides rule-based intelligence for classifying the semantic
nature of code changes in Pull Requests. It helps distinguish between:
  - Cosmetic changes (whitespace, comments, formatting)
  - Safety improvements (NULL checks, error handling)
  - Functional changes (actual logic modifications)

Originally extracted from smart_merge.py and adapted for PR-level analysis.
Used by both pr_conflict_analyzer.py and llm_pr_decision.py to provide
semantic context for hybrid decision-making.
"""

import re
from enum import Enum
from typing import List, Dict, Set
from dataclasses import dataclass


# ── Change Type Classification ───────────────────────────────────────────────

class ChangeType(Enum):
    """Semantic classification of code changes."""
    WHITESPACE_ONLY = "whitespace_only"    # Pure formatting/indentation
    INCLUDE_REORDER = "include_reorder"    # #include reorganization
    COMMENT_ONLY = "comment_only"          # Documentation changes
    NULL_CHECK_ADDED = "null_check_added"  # Safety: NULL validation
    ERROR_HANDLING = "error_handling"      # Error handling improvements
    BRACE_STYLE = "brace_style"            # K&R vs Allman style
    SAFETY_IMPROVEMENT = "safety_improvement"  # snprintf, free, close, etc.
    FUNCTIONAL = "functional"              # Real logic changes
    MIXED = "mixed"                        # Multiple types


@dataclass
class SemanticAnalysis:
    """Semantic analysis result for a PR or diff section."""
    change_types: List[ChangeType]        # All detected change types
    dominant_type: ChangeType             # Most significant type
    null_checks_added: int                # Count of NULL check additions
    error_handling_added: int             # Count of error handling additions
    safety_patterns_added: int            # Count of safety improvements
    functional_changes: int               # Count of functional changes
    cosmetic_only: bool                   # True if all changes are cosmetic
    safety_focused: bool                  # True if primarily safety improvements
    confidence: str                       # "HIGH", "MEDIUM", "LOW"
    summary: str                          # Human-readable summary


# ── Pattern Matchers ──────────────────────────────────────────────────────────

# Matches #include lines
RE_INCLUDE = re.compile(r'^\s*#\s*include\s+[<"].*[>"]')

# Matches comment-only lines
RE_COMMENT_LINE = re.compile(r'^\s*(/\*.*\*/|//.*|\*.*|\*/)\s*$')
RE_COMMENT_START = re.compile(r'^\s*/\*')
RE_COMMENT_END = re.compile(r'.*\*/\s*$')

# Matches NULL checks and pointer validation
RE_NULL_CHECK = re.compile(
    r'(if\s*\(\s*\!?\s*\w+\s*(==|!=)\s*NULL\s*\)|'
    r'if\s*\(\s*NULL\s*(==|!=)\s*\w+\s*\)|'
    r'if\s*\(\s*\!\s*\w+\s*\)|'
    r'if\s*\(\s*\w+\s*\))',
    re.IGNORECASE
)

# Matches error handling patterns (RDK-B specific + common C patterns)
RE_ERROR_HANDLING = re.compile(
    r'(return\s+ANSC_STATUS_FAILURE|'
    r'return\s+ANSC_STATUS_SUCCESS|'
    r'return\s+(-1|NULL|false|FALSE)|'
    r'exit\s*\(\s*1\s*\)|'
    r'CcspTraceError|CcspTraceWarning|CcspTraceInfo|'
    r'ERR_CHK|ERROR_CHK|'
    r'goto\s+\w*error\w*|goto\s+\w*fail\w*|goto\s+cleanup)',
    re.IGNORECASE
)

# Matches safety improvement patterns
RE_SAFETY_PATTERNS = re.compile(
    r'(if\s*\(\s*\!\s*\w+\s*\)|'           # if (!ptr)
    r'if\s*\(\s*\w+\s*==\s*NULL|'          # if (ptr == NULL)
    r'if\s*\(\s*NULL\s*==|'                # if (NULL == ptr)
    r'snprintf\s*\(|'                       # snprintf (safer than sprintf)
    r'strncpy\s*\(|'                        # strncpy (safer than strcpy)
    r'strncat\s*\(|'                        # strncat (safer than strcat)
    r'close\s*\(\s*\w+\s*\)|'              # close(fd)
    r'free\s*\(\s*\w+\s*\)|'               # free(ptr)
    r'fclose\s*\(|'                         # fclose cleanup
    r'va_end\s*\(|'                         # va_end cleanup
    r'pthread_mutex_unlock|'                # mutex cleanup
    r'memset\s*\(\s*\w+\s*,\s*0)',         # memset zeroing
    re.IGNORECASE
)

# Matches resource acquisition (to pair with cleanup)
RE_RESOURCE_ACQUIRE = re.compile(
    r'(malloc\s*\(|calloc\s*\(|realloc\s*\(|'
    r'fopen\s*\(|open\s*\(|socket\s*\(|'
    r'pthread_mutex_lock)',
    re.IGNORECASE
)


# ── Core Analysis Functions ───────────────────────────────────────────────────

def _normalize_whitespace(line: str) -> str:
    """Strip all whitespace for comparison."""
    return re.sub(r'\s+', '', line.rstrip('\n'))


def _is_whitespace_only_diff(lines: List[str]) -> bool:
    """Check if all lines are whitespace-only changes."""
    for line in lines:
        normalized = _normalize_whitespace(line)
        if normalized and not normalized.startswith(('#', '//', '/*', '*')):
            return False
    return True


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


def _count_pattern(lines: List[str], pattern: re.Pattern) -> int:
    """Count how many lines match a regex pattern."""
    return sum(1 for line in lines if pattern.search(line))


def classify_diff_lines(added_lines: List[str], removed_lines: List[str]) -> ChangeType:
    """
    Classify the semantic nature of changes between added and removed lines.
    
    Args:
        added_lines: Lines added in the diff (+ lines)
        removed_lines: Lines removed in the diff (- lines)
        
    Returns:
        ChangeType indicating the dominant type of change
    """
    # Check whitespace-only difference
    if _is_whitespace_only_diff(added_lines + removed_lines):
        return ChangeType.WHITESPACE_ONLY

    # Check if all changes are #include modifications
    if _all_includes(added_lines) and _all_includes(removed_lines):
        return ChangeType.INCLUDE_REORDER
    if _all_includes(added_lines) or _all_includes(removed_lines):
        return ChangeType.INCLUDE_REORDER

    # Check comment-only changes
    if _all_comments(added_lines) and _all_comments(removed_lines):
        return ChangeType.COMMENT_ONLY
    if _all_comments(added_lines) or _all_comments(removed_lines):
        return ChangeType.COMMENT_ONLY

    # Check if NULL checks were added
    added_null = _count_pattern(added_lines, RE_NULL_CHECK)
    removed_null = _count_pattern(removed_lines, RE_NULL_CHECK)
    if added_null > removed_null:
        return ChangeType.NULL_CHECK_ADDED

    # Check if error handling was added
    added_err = _count_pattern(added_lines, RE_ERROR_HANDLING)
    removed_err = _count_pattern(removed_lines, RE_ERROR_HANDLING)
    if added_err > removed_err:
        return ChangeType.ERROR_HANDLING

    # Check if safety patterns were added
    added_safety = _count_pattern(added_lines, RE_SAFETY_PATTERNS)
    removed_safety = _count_pattern(removed_lines, RE_SAFETY_PATTERNS)
    if added_safety > removed_safety:
        return ChangeType.SAFETY_IMPROVEMENT

    # Check brace style changes (K&R vs Allman)
    norm_added = [_normalize_whitespace(l) for l in added_lines if _normalize_whitespace(l)]
    norm_removed = [_normalize_whitespace(l) for l in removed_lines if _normalize_whitespace(l)]
    
    brace_only_added = [l for l in norm_added if l not in ('{', '}')]
    brace_only_removed = [l for l in norm_removed if l not in ('{', '}')]
    
    if brace_only_added == brace_only_removed and norm_added != norm_removed:
        return ChangeType.BRACE_STYLE

    # Default to functional change
    return ChangeType.FUNCTIONAL


def analyze_pr_diff(diff_text: str) -> SemanticAnalysis:
    """
    Analyze a complete PR diff and classify all changes.
    
    Args:
        diff_text: Full diff text from a PR (git diff or gh pr diff)
        
    Returns:
        SemanticAnalysis with comprehensive pattern detection results
    """
    added_lines = []
    removed_lines = []
    change_types = []
    
    # Parse diff to extract added/removed lines
    for line in diff_text.split('\n'):
        if line.startswith('+') and not line.startswith('+++'):
            added_lines.append(line[1:])  # Remove + prefix
        elif line.startswith('-') and not line.startswith('---'):
            removed_lines.append(line[1:])  # Remove - prefix
    
    # Count pattern occurrences
    null_checks = _count_pattern(added_lines, RE_NULL_CHECK) - _count_pattern(removed_lines, RE_NULL_CHECK)
    error_handling = _count_pattern(added_lines, RE_ERROR_HANDLING) - _count_pattern(removed_lines, RE_ERROR_HANDLING)
    safety_patterns = _count_pattern(added_lines, RE_SAFETY_PATTERNS) - _count_pattern(removed_lines, RE_SAFETY_PATTERNS)
    
    # Classify overall change type
    dominant_type = classify_diff_lines(added_lines, removed_lines)
    change_types.append(dominant_type)
    
    # Determine if changes are cosmetic only
    cosmetic_types = {ChangeType.WHITESPACE_ONLY, ChangeType.INCLUDE_REORDER, 
                      ChangeType.COMMENT_ONLY, ChangeType.BRACE_STYLE}
    cosmetic_only = dominant_type in cosmetic_types
    
    # Determine if changes are safety-focused
    safety_types = {ChangeType.NULL_CHECK_ADDED, ChangeType.ERROR_HANDLING, 
                    ChangeType.SAFETY_IMPROVEMENT}
    safety_focused = dominant_type in safety_types or (null_checks + error_handling + safety_patterns) > 2
    
    # Count functional changes (lines that aren't cosmetic or comments)
    functional_changes = 0
    for line in added_lines:
        normalized = _normalize_whitespace(line)
        if normalized and not RE_COMMENT_LINE.match(line) and not RE_INCLUDE.match(line):
            if not line.strip() in ('{', '}'):
                functional_changes += 1
    
    # Determine confidence level
    total_changes = len(added_lines) + len(removed_lines)
    if total_changes < 10:
        confidence = "HIGH"
    elif total_changes < 50:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"
    
    # Generate summary
    if cosmetic_only:
        summary = f"Cosmetic changes only ({dominant_type.value})"
    elif safety_focused:
        summary = f"Safety improvements: {null_checks} NULL checks, {error_handling} error handlers, {safety_patterns} safety patterns"
    else:
        summary = f"Functional changes: {functional_changes} lines modified"
    
    return SemanticAnalysis(
        change_types=change_types,
        dominant_type=dominant_type,
        null_checks_added=max(0, null_checks),
        error_handling_added=max(0, error_handling),
        safety_patterns_added=max(0, safety_patterns),
        functional_changes=functional_changes,
        cosmetic_only=cosmetic_only,
        safety_focused=safety_focused,
        confidence=confidence,
        summary=summary
    )


def get_pattern_hints(analysis: SemanticAnalysis) -> Dict[str, any]:
    """
    Convert semantic analysis into LLM-friendly hints.
    
    Returns a dictionary suitable for inclusion in LLM prompts.
    """
    return {
        "change_type": analysis.dominant_type.value,
        "cosmetic_only": analysis.cosmetic_only,
        "safety_focused": analysis.safety_focused,
        "null_checks_added": analysis.null_checks_added,
        "error_handling_added": analysis.error_handling_added,
        "safety_patterns_added": analysis.safety_patterns_added,
        "functional_changes": analysis.functional_changes,
        "confidence": analysis.confidence,
        "summary": analysis.summary
    }


# ── Quick Classification Helpers ──────────────────────────────────────────────

def is_cosmetic_change(diff_text: str) -> bool:
    """Quick check if a diff contains only cosmetic changes."""
    analysis = analyze_pr_diff(diff_text)
    return analysis.cosmetic_only


def is_safety_improvement(diff_text: str) -> bool:
    """Quick check if a diff primarily adds safety improvements."""
    analysis = analyze_pr_diff(diff_text)
    return analysis.safety_focused


def get_change_severity(diff_text: str) -> str:
    """Classify change severity: 'cosmetic', 'safety', or 'functional'."""
    analysis = analyze_pr_diff(diff_text)
    if analysis.cosmetic_only:
        return "cosmetic"
    elif analysis.safety_focused:
        return "safety"
    else:
        return "functional"
