#!/usr/bin/env python3
"""Unit tests for smart_merge.py"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from smart_merge import *

passed = 0
failed = 0

def test(name, condition):
    global passed, failed
    if condition:
        print(f"  PASS: {name}")
        passed += 1
    else:
        print(f"  FAIL: {name}")
        failed += 1

# ── Classification tests ─────────────────────────────────────────────────────
print("Classification tests:")

test("whitespace-only diff",
     classify_hunk_change(['  int x=1;  \n'], ['  int x=1;\n'])
     == ChangeType.WHITESPACE_ONLY)

test("include reorder",
     classify_hunk_change(
         ['#include <stdio.h>\n'],
         ['#include <stdio.h>\n', '#include <string.h>\n'])
     == ChangeType.INCLUDE_REORDER)

test("comment-only",
     classify_hunk_change(['/* Old */\n'], ['/* New comment */\n'])
     == ChangeType.COMMENT_ONLY)

test("NULL check added",
     classify_hunk_change(['x = 42;\n'], ['if (ptr == NULL) return;\n'])
     == ChangeType.NULL_CHECK_ADDED)

test("functional change",
     classify_hunk_change(['return 1;\n'], ['return 2;\n'])
     == ChangeType.FUNCTIONAL)

# ── Merge tests ───────────────────────────────────────────────────────────────
print("\nMerge tests:")

merged = merge_includes(
    ['#include <stdio.h>\n'],
    ['#include <stdio.h>\n', '#include <string.h>\n'])
test("include merge dedup", len(merged) == 2)
test("include merge content",
     '#include <string.h>' in [l.strip() for l in merged])

# ── Safety detection ──────────────────────────────────────────────────────────
print("\nSafety detection:")

test("detects NULL check", detect_safety_improvement(['if (ptr == NULL) return;\n']))
test("detects close(fd)", detect_safety_improvement(['close(fd);\n']))
test("detects snprintf", detect_safety_improvement(['snprintf(buf, sz, fmt);\n']))
test("no safety in plain code", not detect_safety_improvement(['x = 42;\n']))

# ── Resolution tests ─────────────────────────────────────────────────────────
print("\nResolution tests:")

r = resolve_hunk(['  int x=1;  \n'], ['  int x=1;\n'], mode='cherry-pick')
test("whitespace -> HIGH", r.confidence == Confidence.HIGH)

r = resolve_hunk(
    ['#include <stdio.h>\n'],
    ['#include <stdio.h>\n', '#include <string.h>\n'],
    mode='cherry-pick')
test("includes -> HIGH", r.confidence == Confidence.HIGH)

r = resolve_hunk(
    ['x = 42;\n'],
    ['if (ptr == NULL) return;\n'],
    mode='cherry-pick', safety_prefer=True)
test("safety -> MEDIUM", r.confidence == Confidence.MEDIUM)
test("safety prefers theirs", r.resolved_lines == ['if (ptr == NULL) return;\n'])

r = resolve_hunk(['return 1;\n'], ['return 2;\n'], mode='cherry-pick')
test("functional -> LOW", r.confidence == Confidence.LOW)

r = resolve_hunk(['/* Old */\n'], ['/* New comment */\n'], mode='cherry-pick')
test("comment -> HIGH", r.confidence == Confidence.HIGH)

# ── Format test ───────────────────────────────────────────────────────────────
print("\nFormat test:")
r = resolve_hunk(['x=1;\n'], ['x=2;\n'], mode='cherry-pick')
rationale = format_resolution_rationale("test.c", 1, r)
test("rationale is string", isinstance(rationale, str) and len(rationale) > 0)

# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed > 0:
    sys.exit(1)
else:
    print("All tests passed!")
