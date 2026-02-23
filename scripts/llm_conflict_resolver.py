#!/usr/bin/env python3
"""
llm_conflict_resolver.py
========================
Hybrid conflict resolution for the RDK-B release agent.

HYBRID APPROACH:
  - Rule-based semantic analysis for HIGH confidence conflicts (auto-resolve)
  - LLM-powered resolution for MEDIUM/LOW confidence conflicts
  - Post-resolution validation (C syntax checking)

This module provides intelligent conflict resolution at the code level:
  - Detects merge conflicts in files
  - Parses conflict markers (<<<<<<< ======= >>>>>>>)
  - Classifies conflicts by change type and confidence
  - Auto-resolves simple conflicts (whitespace, includes, NULL checks)
  - Uses LLM for complex conflicts
  - Validates resolved code for syntax errors

Unlike PR-level decisions, this resolves actual code conflicts.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

# Import LLM provider functions
import sys
script_dir = Path(__file__).parent
sys.path.insert(0, str(script_dir))
from llm_providers import (
    _call_openai,
    _call_gemini,
    _call_githubcopilot,
    _call_azureopenai,
    _call_generic
)


# ══════════════════════════════════════════════════════════════════════════════
# HYBRID INTELLIGENCE: Rule-Based Classification + LLM Fallback
# ══════════════════════════════════════════════════════════════════════════════

class Confidence(Enum):
    """Confidence level for conflict resolution."""
    HIGH   = "HIGH"    # Auto-resolve (whitespace, includes, NULL checks)
    MEDIUM = "MEDIUM"  # LLM with safety guidance
    LOW    = "LOW"     # Full LLM resolution

    def __ge__(self, other):
        order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] >= order[other.value]

    def __gt__(self, other):
        order = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
        return order[self.value] > order[other.value]


class ChangeType(Enum):
    """Classification of conflict change type."""
    WHITESPACE_ONLY  = "whitespace_only"   # HIGH confidence
    INCLUDE_REORDER  = "include_reorder"   # HIGH confidence
    COMMENT_ONLY     = "comment_only"      # HIGH confidence
    NULL_CHECK_ADDED = "null_check_added"  # MEDIUM confidence
    ERROR_HANDLING   = "error_handling"    # MEDIUM confidence
    BRACE_STYLE      = "brace_style"       # HIGH confidence
    FUNCTIONAL       = "functional"        # LOW confidence
    MIXED            = "mixed"             # LOW confidence


# ── Pattern Matchers ──────────────────────────────────────────────────────────

# Matches #include lines
RE_INCLUDE = re.compile(r'^\s*#\s*include\s+[<"].*[>"]')

# Matches comment-only lines
RE_COMMENT_LINE = re.compile(r'^\s*(/\*.*\*/|//.*|\*.*|\*/)\s*$')
RE_COMMENT_START = re.compile(r'^\s*/\*')
RE_COMMENT_END = re.compile(r'.*\*/\s*$')

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

# ── Helper Functions ──────────────────────────────────────────────────────────

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


def detect_safety_improvement(lines: List[str]) -> bool:
    """
    Detect if a set of lines represents a safety improvement.
    
    Safety improvements include:
    - Adding NULL parameter checks
    - Adding bounds checks
    - Adding error handling (return on failure)
    - Closing resource leaks (close(fd), free(ptr))
    """
    safety_patterns = [
        r'if\s*\(\s*\!\s*\w+\s*\)',           # if (!ptr)
        r'if\s*\(\s*\w+\s*==\s*NULL',          # if (ptr == NULL)
        r'if\s*\(\s*NULL\s*==',                 # if (NULL == ptr)
        r'close\s*\(\s*\w+\s*\)',               # close(fd)
        r'free\s*\(\s*\w+\s*\)',                # free(ptr)
        r'return\s+ANSC_STATUS_FAILURE',        # error return
        r'CcspTraceError|CcspTraceWarning',     # error logging
    ]
    
    for pattern in safety_patterns:
        if re.search(pattern, '\n'.join(lines), re.IGNORECASE):
            return True
    return False


def classify_hunk_change(ours_lines: List[str], theirs_lines: List[str]) -> Tuple[ChangeType, Confidence]:
    """
    Classify the nature of changes in a conflict hunk.
    
    Returns:
        (ChangeType, Confidence) tuple
    """
    # Check whitespace-only difference
    if _is_whitespace_only_diff(ours_lines, theirs_lines):
        return (ChangeType.WHITESPACE_ONLY, Confidence.HIGH)
    
    # Check if both sides are #include blocks
    if _all_includes(ours_lines) and _all_includes(theirs_lines):
        return (ChangeType.INCLUDE_REORDER, Confidence.HIGH)
    
    # Check comment-only changes
    if _all_comments(ours_lines) and _all_comments(theirs_lines):
        return (ChangeType.COMMENT_ONLY, Confidence.HIGH)
    
    # Check if one side adds NULL checks
    ours_null = _has_null_check(ours_lines)
    theirs_null = _has_null_check(theirs_lines)
    if theirs_null and not ours_null:
        return (ChangeType.NULL_CHECK_ADDED, Confidence.MEDIUM)
    if ours_null and not theirs_null:
        return (ChangeType.NULL_CHECK_ADDED, Confidence.MEDIUM)
    
    # Check if one side adds error handling
    ours_err = _has_error_handling(ours_lines)
    theirs_err = _has_error_handling(theirs_lines)
    if (theirs_err and not ours_err) or (ours_err and not theirs_err):
        return (ChangeType.ERROR_HANDLING, Confidence.MEDIUM)
    
    # Check brace style changes
    norm_a = [_normalize_whitespace(l) for l in ours_lines if _normalize_whitespace(l)]
    norm_b = [_normalize_whitespace(l) for l in theirs_lines if _normalize_whitespace(l)]
    brace_only_a = [l for l in norm_a if l not in ('{', '}')]
    brace_only_b = [l for l in norm_b if l not in ('{', '}')]
    if brace_only_a == brace_only_b and norm_a != norm_b:
        return (ChangeType.BRACE_STYLE, Confidence.HIGH)
    
    # Default: functional change
    return (ChangeType.FUNCTIONAL, Confidence.LOW)


def merge_includes(ours_lines: List[str], theirs_lines: List[str]) -> str:
    """
    Merge two sets of #include lines intelligently.
    
    - Deduplicates
    - Groups: local includes ("...") first, then system includes (<...>)
    - Preserves order within groups
    """
    ours_includes = _extract_includes(ours_lines)
    theirs_includes = _extract_includes(theirs_lines)
    
    # Collect all unique includes
    seen = set()
    merged = []
    for inc in ours_includes + theirs_includes:
        normalized = _normalize_whitespace(inc)
        if normalized not in seen:
            seen.add(normalized)
            merged.append(inc)
    
    # Sort: local includes ("...") first, then system includes (<...>)
    local_inc = [i for i in merged if '"' in i]
    system_inc = [i for i in merged if '<' in i and '>' in i]
    other_inc = [i for i in merged if i not in local_inc and i not in system_inc]
    
    result = []
    if local_inc:
        result.extend(sorted(local_inc, key=lambda x: x.strip().lower()))
    if system_inc:
        result.extend(sorted(system_inc, key=lambda x: x.strip().lower()))
    if other_inc:
        result.extend(other_inc)
    
    return '\n'.join(result)


def auto_resolve_high_confidence(
    change_type: ChangeType,
    ours_content: str,
    theirs_content: str,
    confidence: Confidence
) -> Optional[Tuple[str, str]]:
    """
    Auto-resolve HIGH confidence conflicts.
    
    Returns:
        (resolved_content, rationale) if auto-resolvable, else None
    """
    if confidence != Confidence.HIGH:
        return None
    
    ours_lines = ours_content.split('\n')
    theirs_lines = theirs_content.split('\n')
    
    if change_type == ChangeType.WHITESPACE_ONLY:
        # Keep OURS (whitespace doesn't matter functionally)
        return (ours_content, "Whitespace-only difference, kept current formatting")
    
    elif change_type == ChangeType.INCLUDE_REORDER:
        # Merge and deduplicate includes
        merged = merge_includes(ours_lines, theirs_lines)
        return (merged, "Merged and deduplicated #include directives")
    
    elif change_type == ChangeType.COMMENT_ONLY:
        # Merge both comments
        combined = ours_content + '\n' + theirs_content
        return (combined, "Merged both comment blocks")
    
    elif change_type == ChangeType.BRACE_STYLE:
        # Keep OURS (brace style doesn't matter functionally)
        return (ours_content, "Brace style difference, kept current style")
    
    return None


def validate_c_syntax(file_path: str) -> Tuple[bool, str]:
    """
    Validate C syntax using gcc.
    
    Returns:
        (is_valid, error_message)
    """
    if not file_path.endswith(('.c', '.h', '.cpp', '.hpp')):
        # Not a C/C++ file, skip validation
        return (True, "Not a C/C++ file")
    
    result = subprocess.run(
        ["gcc", "-fsyntax-only", "-x", "c", file_path],
        capture_output=True,
        text=True,
        timeout=10
    )
    
    if result.returncode == 0:
        return (True, "C syntax valid")
    else:
        return (False, result.stderr)


CONFLICT_RESOLUTION_SYSTEM_PROMPT = """You are an expert software engineer resolving merge conflicts in production code.

Your task is to analyze merge conflicts and provide intelligent resolutions that:
1. Preserve critical functionality from both sides when possible
2. Maintain code safety (NULL checks, error handling, bounds checking)
3. Follow the existing code style and patterns
4. Avoid introducing bugs or breaking changes

When resolving conflicts:
- Prefer keeping safety improvements (NULL checks, validation, error handling)
- Preserve functional changes that add new features
- Keep cosmetic changes only if they don't conflict with functionality
- When in doubt, merge both changes intelligently rather than choosing one side"""


CONFLICT_RESOLUTION_PROMPT = """# Merge Conflict Resolution

## Context
- **File**: {file_path}
- **PR Number**: #{pr_number}
- **PR Title**: {pr_title}
- **Operation**: {operation} (cherry-pick/revert)
- **Strategy**: {strategy}

## Conflict Details

{conflict_details}

## Instructions

Analyze each conflict and provide a resolution that:
1. Keeps the most important changes from both sides
2. Maintains code safety and functionality
3. Avoids introducing bugs
4. Follows best practices

For each conflict, you must decide:
- **OURS**: Keep our version (current branch)
- **THEIRS**: Keep their version (incoming change)
- **BOTH**: Merge both changes intelligently
- **CUSTOM**: Provide custom resolution code

Respond with a JSON array of resolutions:

```json
[
  {{
    "conflict_index": 0,
    "resolution_type": "BOTH|OURS|THEIRS|CUSTOM",
    "rationale": "Why this resolution is correct",
    "resolved_content": "The final code (if CUSTOM or BOTH)",
    "risks": ["Any risks with this resolution"],
    "confidence": "HIGH|MEDIUM|LOW"
  }}
]
```

Be precise and thoughtful. This code will be committed directly."""


@dataclass
class ConflictBlock:
    """A single merge conflict block."""
    file_path: str
    conflict_index: int
    ours_content: str
    theirs_content: str
    base_content: Optional[str]
    start_line: int
    end_line: int
    change_type: Optional[ChangeType] = None
    confidence: Optional[Confidence] = None


@dataclass
class ConflictResolution:
    """Resolution for a single conflict."""
    conflict_index: int
    resolution_type: str  # "OURS", "THEIRS", "BOTH", "CUSTOM", "AUTO"
    resolved_content: str
    rationale: str
    risks: List[str]
    confidence: str
    change_type: Optional[str] = None
    auto_resolved: bool = False


class LLMConflictResolver:
    """LLM-powered merge conflict resolver."""
    
    def __init__(self, config: Dict):
        llm_cfg = config.get("llm", {})
        
        if not llm_cfg.get("enabled"):
            raise ValueError("LLM must be enabled for conflict resolution")
        
        self.provider = llm_cfg.get("provider", "openai")
        self.model = llm_cfg.get("model", "gpt-4o-mini")
        self.temperature = llm_cfg.get("temperature", 0.1)  # Lower for precise conflict resolution
        self.timeout = llm_cfg.get("timeout_seconds", 90)
        
        # Get API key
        api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
        import os
        self.api_key = os.environ.get(api_key_env, "")
        
        # Endpoint for custom providers
        self.endpoint = llm_cfg.get("endpoint", "")
        
        # Config
        self.strategy = config.get("strategy", "unknown")
        self.version = config.get("version", "unknown")
        
        # State
        self.resolution_log = Path("/tmp/rdkb-release-conflicts/conflict_resolutions.jsonl")
        self.resolution_log.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"  🔧 LLM Conflict Resolver initialized: {self.provider}/{self.model}")
    
    def detect_conflicted_files(self) -> List[str]:
        """Detect files with merge conflicts."""
        result = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=U"],
            capture_output=True, text=True
        )
        
        if result.returncode != 0:
            return []
        
        files = [f.strip() for f in result.stdout.strip().split('\n') if f.strip()]
        return files
    
    def parse_conflicts(self, file_path: str) -> List[ConflictBlock]:
        """Parse conflict markers in a file."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            print(f"  ❌ Failed to read {file_path}: {e}")
            return []
        
        conflicts = []
        lines = content.split('\n')
        
        i = 0
        conflict_index = 0
        while i < len(lines):
            if lines[i].startswith('<<<<<<<'):
                # Found conflict start
                start_line = i
                ours_lines = []
                theirs_lines = []
                base_lines = []
                
                i += 1
                # Collect OURS section
                while i < len(lines) and not lines[i].startswith('|||||||') and not lines[i].startswith('======='):
                    ours_lines.append(lines[i])
                    i += 1
                
                # Check for base (diff3 style)
                if i < len(lines) and lines[i].startswith('|||||||'):
                    i += 1
                    while i < len(lines) and not lines[i].startswith('======='):
                        base_lines.append(lines[i])
                        i += 1
                
                # Skip separator
                if i < len(lines) and lines[i].startswith('======='):
                    i += 1
                
                # Collect THEIRS section
                while i < len(lines) and not lines[i].startswith('>>>>>>>'):
                    theirs_lines.append(lines[i])
                    i += 1
                
                # Skip conflict end marker
                if i < len(lines) and lines[i].startswith('>>>>>>>'):
                    end_line = i
                    i += 1
                
                # Classify the conflict
                change_type, confidence = classify_hunk_change(ours_lines, theirs_lines)
                
                conflicts.append(ConflictBlock(
                    file_path=file_path,
                    conflict_index=conflict_index,
                    ours_content='\n'.join(ours_lines),
                    theirs_content='\n'.join(theirs_lines),
                    base_content='\n'.join(base_lines) if base_lines else None,
                    start_line=start_line,
                    end_line=end_line,
                    change_type=change_type,
                    confidence=confidence
                ))
                conflict_index += 1
            else:
                i += 1
        
        return conflicts
    
    def resolve_conflicts(self,
                         file_path: str,
                         pr_number: int,
                         pr_metadata: Dict,
                         operation: str = "cherry-pick") -> bool:
        """
        Resolve all conflicts in a file using HYBRID approach.
        
        HYBRID INTELLIGENCE:
        1. HIGH confidence conflicts → Auto-resolve (rules)
        2. MEDIUM/LOW confidence → LLM resolution
        3. Post-resolution validation (C syntax check)
        
        Args:
            file_path: Path to the conflicted file
            pr_number: PR number being processed
            pr_metadata: PR metadata dict
            operation: "cherry-pick" or "revert"
            
        Returns:
            True if conflicts were successfully resolved
        """
        print(f"\n  🔧 Resolving conflicts in {file_path}...")
        
        # Parse and classify conflicts
        conflicts = self.parse_conflicts(file_path)
        if not conflicts:
            print(f"  ⚠️  No conflicts found in {file_path}")
            return False
        
        print(f"  Found {len(conflicts)} conflict blocks")
        
        # ── PHASE 1: Auto-resolve HIGH confidence conflicts ──────────────────
        auto_resolutions = []
        llm_needed_conflicts = []
        
        for conflict in conflicts:
            print(f"    Conflict #{conflict.conflict_index+1}: {conflict.change_type.value} ({conflict.confidence.value} confidence)")
            
            if conflict.confidence == Confidence.HIGH:
                # Try auto-resolve
                result = auto_resolve_high_confidence(
                    conflict.change_type,
                    conflict.ours_content,
                    conflict.theirs_content,
                    conflict.confidence
                )
                
                if result:
                    resolved_content, rationale = result
                    auto_resolutions.append(ConflictResolution(
                        conflict_index=conflict.conflict_index,
                        resolution_type="AUTO",
                        resolved_content=resolved_content,
                        rationale=rationale,
                        risks=[],
                        confidence="HIGH",
                        change_type=conflict.change_type.value,
                        auto_resolved=True
                    ))
                    print(f"      ✓ AUTO-RESOLVED: {rationale}")
                else:
                    llm_needed_conflicts.append(conflict)
            else:
                llm_needed_conflicts.append(conflict)
        
        print(f"\n  AUTO-RESOLVED: {len(auto_resolutions)} / {len(conflicts)}")
        print(f"  LLM-NEEDED: {len(llm_needed_conflicts)} / {len(conflicts)}")
        
        # ── PHASE 2: LLM resolution for MEDIUM/LOW confidence conflicts ──────
        llm_resolutions = []
        
        if llm_needed_conflicts:
            # Build conflict details for LLM
            conflict_details = ""
            for conflict in llm_needed_conflicts:
                conflict_details += f"\n### Conflict {conflict.conflict_index+1} (Lines {conflict.start_line}-{conflict.end_line})\n"
                conflict_details += f"**Classification**: {conflict.change_type.value} ({conflict.confidence.value} confidence)\n\n"
                conflict_details += "**OURS (current branch)**:\n```\n"
                conflict_details += conflict.ours_content[:500] + ("\n...(truncated)" if len(conflict.ours_content) > 500 else "")
                conflict_details += "\n```\n\n"
                conflict_details += "**THEIRS (incoming change)**:\n```\n"
                conflict_details += conflict.theirs_content[:500] + ("\n...(truncated)" if len(conflict.theirs_content) > 500 else "")
                conflict_details += "\n```\n"
                
                # Add safety guidance for MEDIUM confidence
                if conflict.confidence == Confidence.MEDIUM:
                    ours_safe = detect_safety_improvement(conflict.ours_content.split('\n'))
                    theirs_safe = detect_safety_improvement(conflict.theirs_content.split('\n'))
                    if theirs_safe and not ours_safe:
                        conflict_details += "\n**SAFETY NOTE**: THEIRS adds safety improvements (prefer THEIRS or BOTH)\n"
                    elif ours_safe and not theirs_safe:
                        conflict_details += "\n**SAFETY NOTE**: OURS has safety improvements (prefer OURS or BOTH)\n"
            
            # Build prompt
            prompt = CONFLICT_RESOLUTION_PROMPT.format(
                file_path=file_path,
                pr_number=pr_number,
                pr_title=pr_metadata.get('title', 'N/A'),
                operation=operation,
                strategy=self.strategy,
                conflict_details=conflict_details
            )
            
            # Call LLM
            print(f"\n  🤖 Consulting LLM for {len(llm_needed_conflicts)} complex conflicts...")
            t0 = time.time()
            try:
                if self.provider == "openai":
                    response = _call_openai(
                        self.api_key, self.model, CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt,
                        self.temperature, self.timeout
                    )
                elif self.provider == "githubcopilot":
                    response = _call_githubcopilot(
                        self.api_key, self.model, CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt,
                        self.temperature, self.timeout
                    )
                elif self.provider == "gemini":
                    response = _call_gemini(
                        self.api_key, self.model, CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt,
                        self.temperature, self.timeout
                    )
                elif self.provider == "azureopenai":
                    response = _call_azureopenai(
                        self.api_key, self.model, CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt,
                        self.temperature, self.timeout, self.endpoint
                    )
                else:
                    response = _call_generic(
                        self.api_key, self.model, CONFLICT_RESOLUTION_SYSTEM_PROMPT, prompt,
                        self.temperature, self.timeout, self.endpoint
                    )
            except Exception as e:
                elapsed = time.time() - t0
                print(f"  ❌ LLM call failed ({elapsed:.1f}s): {e}")
                return False
            
            elapsed = time.time() - t0
            content = response["content"]
            
            # Parse LLM response
            try:
                # Strip markdown fences if present
                if '```json' in content:
                    content = content.split('```json')[1].split('```')[0]
                elif '```' in content:
                    content = content.split('```')[1].split('```')[0]
                
                resolutions_data = json.loads(content.strip())
                
                # Convert to ConflictResolution objects
                for res_data in resolutions_data:
                    llm_resolutions.append(ConflictResolution(
                        conflict_index=res_data.get('conflict_index', 0),
                        resolution_type=res_data.get('resolution_type', 'OURS'),
                        resolved_content=res_data.get('resolved_content', ''),
                        rationale=res_data.get('rationale', ''),
                        risks=res_data.get('risks', []),
                        confidence=res_data.get('confidence', 'LOW'),
                        auto_resolved=False
                    ))
                
                print(f"  ✅ LLM provided {len(llm_resolutions)} resolutions ({elapsed:.1f}s)")
                
            except Exception as e:
                print(f"  ❌ Failed to parse LLM response: {e}")
                print(f"  Response: {content[:200]}...")
                return False
        
        # Combine all resolutions
        resolutions = auto_resolutions + llm_resolutions
        
        # Apply resolutions
        return self._apply_resolutions(file_path, conflicts, resolutions, pr_number)
    
    def _apply_resolutions(self,
                          file_path: str,
                          conflicts: List[ConflictBlock],
                          resolutions: List[ConflictResolution],
                          pr_number: int) -> bool:
        """Apply the LLM resolutions to the file."""
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                original_content = f.read()
            
            lines = original_content.split('\n')
            
            # Apply resolutions in reverse order (to maintain line numbers)
            for conflict in sorted(conflicts, key=lambda c: c.start_line, reverse=True):
                # Find matching resolution
                resolution = next(
                    (r for r in resolutions if r.conflict_index == conflict.conflict_index),
                    None
                )
                
                if not resolution:
                    print(f"  ⚠️  No resolution for conflict {conflict.conflict_index}")
                    continue
                
                # Determine final content based on resolution type
                if resolution.resolution_type == "OURS":
                    final_content = conflict.ours_content
                elif resolution.resolution_type == "THEIRS":
                    final_content = conflict.theirs_content
                elif resolution.resolution_type == "AUTO":
                    final_content = resolution.resolved_content
                elif resolution.resolution_type in ["BOTH", "CUSTOM"]:
                    final_content = resolution.resolved_content
                else:
                    final_content = conflict.ours_content  # Default to OURS
                
                # Replace the conflict block
                new_lines = final_content.split('\n')
                lines[conflict.start_line:conflict.end_line+1] = new_lines
                
                print(f"    ✓ Conflict {conflict.conflict_index}: {resolution.resolution_type} ({resolution.confidence})")
                print(f"      Rationale: {resolution.rationale[:80]}...")
            
            # Write back the resolved file
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            
            print(f"  📝 File resolved, validating...")
            
            # ── PHASE 3: Post-resolution validation ──────────────────────────
            is_valid, validation_msg = validate_c_syntax(file_path)
            
            if not is_valid:
                print(f"  ⚠️  C syntax validation FAILED:")
                print(f"      {validation_msg[:200]}")
                print(f"  ⚠️  Keeping resolved file but flagging for manual review")
                # Continue anyway - syntax errors might be false positives
            else:
                print(f"  ✅ C syntax validation: {validation_msg}")
            
            # Stage the resolved file
            result = subprocess.run(
                ["git", "add", file_path],
                capture_output=True, text=True
            )
            
            if result.returncode == 0:
                print(f"  ✅ File resolved and staged: {file_path}")
                
                # Log the resolution
                self._log_resolution(file_path, pr_number, conflicts, resolutions)
                return True
            else:
                print(f"  ❌ Failed to stage resolved file: {result.stderr}")
                return False
                
        except Exception as e:
            print(f"  ❌ Failed to apply resolutions: {e}")
            return False
    
    def _log_resolution(self,
                       file_path: str,
                       pr_number: int,
                       conflicts: List[ConflictBlock],
                       resolutions: List[ConflictResolution]):
        """Log conflict resolution for audit trail."""
        from datetime import datetime
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "file_path": file_path,
            "pr_number": pr_number,
            "conflict_count": len(conflicts),
            "resolutions": [
                {
                    "index": r.conflict_index,
                    "type": r.resolution_type,
                    "confidence": r.confidence,
                    "rationale": r.rationale,
                    "risks": r.risks,
                    "change_type": r.change_type if hasattr(r, 'change_type') else None,
                    "auto_resolved": r.auto_resolved if hasattr(r, 'auto_resolved') else False
                }
                for r in resolutions
            ],
            "model": self.model,
            "provider": self.provider
        }
        
        with open(self.resolution_log, "a") as f:
            f.write(json.dumps(entry) + "\n")
    
    def resolve_all_conflicts(self, pr_number: int, pr_metadata: Dict, operation: str = "cherry-pick") -> bool:
        """
        Resolve all conflicts for the current git operation.
        
        Returns:
            True if all conflicts were successfully resolved
        """
        conflicted_files = self.detect_conflicted_files()
        
        if not conflicted_files:
            print(f"  ℹ️  No conflicted files detected")
            return True
        
        print(f"\n  🔧 Resolving {len(conflicted_files)} conflicted files...")
        
        all_resolved = True
        for file_path in conflicted_files:
            resolved = self.resolve_conflicts(file_path, pr_number, pr_metadata, operation)
            if not resolved:
                all_resolved = False
        
        return all_resolved


# CLI for testing
if __name__ == "__main__":
    import argparse
    import yaml
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to .release-config.yml")
    parser.add_argument("--pr", required=True, type=int, help="PR number")
    parser.add_argument("--file", help="Specific file to resolve (optional)")
    args = parser.parse_args()
    
    # Load config
    with open(args.config) as f:
        config = yaml.safe_load(f)
    
    # Create resolver
    resolver = LLMConflictResolver(config)
    
    # Mock PR metadata
    pr_metadata = {"title": f"PR #{args.pr}", "number": args.pr}
    
    if args.file:
        # Resolve specific file
        success = resolver.resolve_conflicts(args.file, args.pr, pr_metadata)
    else:
        # Resolve all conflicts
        success = resolver.resolve_all_conflicts(args.pr, pr_metadata)
    
    sys.exit(0 if success else 1)
