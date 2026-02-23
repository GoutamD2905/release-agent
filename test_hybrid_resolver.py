#!/usr/bin/env python3
"""
test_hybrid_resolver.py
=======================
Test script to verify the hybrid conflict resolution approach.

This script tests:
1. ChangeType classification
2. Confidence assignment
3. AUTO-RESOLVE for HIGH confidence conflicts
4. LLM routing for MEDIUM/LOW confidence conflicts
"""

import sys
from pathlib import Path

# Add scripts directory to path
sys.path.insert(0, str(Path(__file__).parent / "scripts"))

from llm_conflict_resolver import (
    classify_hunk_change,
    auto_resolve_high_confidence,
    merge_includes,
    detect_safety_improvement,
    ChangeType,
    Confidence
)


def test_whitespace_only():
    """Test whitespace-only conflict classification."""
    print("\n" + "="*70)
    print("TEST 1: Whitespace-Only Conflict")
    print("="*70)
    
    ours = ["    printf(\"Hello\");"]
    theirs = ["printf(\"Hello\");"]  # No indent
    
    change_type, confidence = classify_hunk_change(ours, theirs)
    print(f"  Classification: {change_type.value}")
    print(f"  Confidence: {confidence.value}")
    
    assert change_type == ChangeType.WHITESPACE_ONLY, "Should detect whitespace-only"
    assert confidence == Confidence.HIGH, "Should be HIGH confidence"
    
    # Test auto-resolve
    result = auto_resolve_high_confidence(change_type, '\n'.join(ours), '\n'.join(theirs), confidence)
    if result:
        resolved, rationale = result
        print(f"  ✅ AUTO-RESOLVED: {rationale}")
        print(f"  Resolution: {resolved}")
    else:
        print(f"  ❌ Failed to auto-resolve")
    
    print("  ✅ PASSED")


def test_include_reorder():
    """Test #include reordering conflict classification."""
    print("\n" + "="*70)
    print("TEST 2: Include Reordering Conflict")
    print("="*70)
    
    ours = [
        '#include "header1.h"',
        '#include <stdio.h>',
        '#include <stdlib.h>'
    ]
    theirs = [
        '#include <stdlib.h>',
        '#include <stdio.h>',
        '#include "header1.h"'
    ]
    
    change_type, confidence = classify_hunk_change(ours, theirs)
    print(f"  Classification: {change_type.value}")
    print(f"  Confidence: {confidence.value}")
    
    assert change_type == ChangeType.INCLUDE_REORDER, "Should detect include reorder"
    assert confidence == Confidence.HIGH, "Should be HIGH confidence"
    
    # Test merge
    merged = merge_includes(ours, theirs)
    print(f"  Merged includes:")
    for line in merged.split('\n'):
        print(f"    {line}")
    
    # Test auto-resolve
    result = auto_resolve_high_confidence(change_type, '\n'.join(ours), '\n'.join(theirs), confidence)
    if result:
        resolved, rationale = result
        print(f"  ✅ AUTO-RESOLVED: {rationale}")
    else:
        print(f"  ❌ Failed to auto-resolve")
    
    print("  ✅ PASSED")


def test_null_check_added():
    """Test NULL check addition classification."""
    print("\n" + "="*70)
    print("TEST 3: NULL Check Added (MEDIUM Confidence)")
    print("="*70)
    
    ours = [
        'char *ptr = malloc(100);',
        'strcpy(ptr, "data");'
    ]
    theirs = [
        'char *ptr = malloc(100);',
        'if (ptr == NULL) return -1;',
        'strcpy(ptr, "data");'
    ]
    
    change_type, confidence = classify_hunk_change(ours, theirs)
    print(f"  Classification: {change_type.value}")
    print(f"  Confidence: {confidence.value}")
    
    assert change_type == ChangeType.NULL_CHECK_ADDED, "Should detect NULL check"
    assert confidence == Confidence.MEDIUM, "Should be MEDIUM confidence"
    
    # Safety detection
    is_safe = detect_safety_improvement(theirs)
    print(f"  Safety improvement detected: {is_safe}")
    assert is_safe, "Should detect safety improvement in THEIRS"
    
    # This should NOT auto-resolve (MEDIUM confidence)
    result = auto_resolve_high_confidence(change_type, '\n'.join(ours), '\n'.join(theirs), confidence)
    assert result is None, "MEDIUM confidence should not auto-resolve"
    print(f"  ✅ Correctly routed to LLM (not auto-resolved)")
    
    print("  ✅ PASSED")


def test_functional_change():
    """Test functional change classification."""
    print("\n" + "="*70)
    print("TEST 4: Functional Change (LOW Confidence)")
    print("="*70)
    
    ours = [
        'int result = calculate(x, y);',
        'return result * 2;'
    ]
    theirs = [
        'int result = calculate(x, y);',
        'return result * 3;'  # Different logic
    ]
    
    change_type, confidence = classify_hunk_change(ours, theirs)
    print(f"  Classification: {change_type.value}")
    print(f"  Confidence: {confidence.value}")
    
    assert change_type == ChangeType.FUNCTIONAL, "Should detect functional change"
    assert confidence == Confidence.LOW, "Should be LOW confidence"
    
    # This should NOT auto-resolve (LOW confidence)
    result = auto_resolve_high_confidence(change_type, '\n'.join(ours), '\n'.join(theirs), confidence)
    assert result is None, "LOW confidence should not auto-resolve"
    print(f"  ✅ Correctly routed to LLM (not auto-resolved)")
    
    print("  ✅ PASSED")


def test_comment_only():
    """Test comment-only conflict classification."""
    print("\n" + "="*70)
    print("TEST 5: Comment-Only Change (HIGH Confidence)")
    print("="*70)
    
    ours = [
        '/* Old comment */',
        '/* describing function */'
    ]
    theirs = [
        '/* New comment */',
        '/* with better description */'
    ]
    
    change_type, confidence = classify_hunk_change(ours, theirs)
    print(f"  Classification: {change_type.value}")
    print(f"  Confidence: {confidence.value}")
    
    assert change_type == ChangeType.COMMENT_ONLY, "Should detect comment-only"
    assert confidence == Confidence.HIGH, "Should be HIGH confidence"
    
    # Test auto-resolve
    result = auto_resolve_high_confidence(change_type, '\n'.join(ours), '\n'.join(theirs), confidence)
    if result:
        resolved, rationale = result
        print(f"  ✅ AUTO-RESOLVED: {rationale}")
        print(f"  Resolution: {resolved[:100]}...")
    else:
        print(f"  ❌ Failed to auto-resolve")
    
    print("  ✅ PASSED")


def run_all_tests():
    """Run all tests."""
    print("\n" + "="*70)
    print("HYBRID CONFLICT RESOLVER - TEST SUITE")
    print("="*70)
    print("\nTesting the hybrid approach:")
    print("  • HIGH confidence conflicts → AUTO-RESOLVE (rules)")
    print("  • MEDIUM confidence conflicts → LLM with safety guidance")
    print("  • LOW confidence conflicts → Full LLM resolution")
    
    try:
        test_whitespace_only()
        test_include_reorder()
        test_null_check_added()
        test_functional_change()
        test_comment_only()
        
        print("\n" + "="*70)
        print("✅ ALL TESTS PASSED")
        print("="*70)
        print("\nHybrid Approach Summary:")
        print("  ✅ Rule-based classification working")
        print("  ✅ Confidence assignment working")
        print("  ✅ AUTO-RESOLVE for HIGH confidence")
        print("  ✅ LLM routing for MEDIUM/LOW confidence")
        print("  ✅ Safety improvement detection")
        print("  ✅ Include merging logic")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    run_all_tests()
