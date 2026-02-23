#!/usr/bin/env python3
"""
test_llm_resolver.py
====================
Unit tests for the LLM resolver module.
Uses mock API responses — no real API calls made.
"""

import sys
import os
import json
import tempfile

# Add scripts dir to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from llm_resolver import (
    LLMResolver, LLMResolution,
    _validate_c_syntax, _validate_no_hallucination, _validate_length,
    _extract_function_calls, create_resolver_from_config
)

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


# ── Validation tests ──────────────────────────────────────────────────────────
print("C syntax validation:")
test("balanced braces",
     _validate_c_syntax(["int main() {\n", "  return 0;\n", "}\n"]))

test("unbalanced braces rejected",
     not _validate_c_syntax(["int main() {\n", "  return 0;\n"]))

test("empty code rejected",
     not _validate_c_syntax(["\n", "  \n"]))

test("balanced parens",
     _validate_c_syntax(["printf(\"hello\");\n"]))

test("unbalanced parens rejected",
     not _validate_c_syntax(["printf(\"hello\";\n", "extra(((\n"]))


# ── Function call extraction ─────────────────────────────────────────────────
print("\nFunction call extraction:")
test("extracts function names",
     _extract_function_calls(["  foo(bar);\n", "  baz(x);\n"]) == {"foo", "baz"})

test("ignores keywords",
     "if" not in _extract_function_calls(["  if (x) {\n"]))

test("ignores preprocessor",
     _extract_function_calls(["#include <stdio.h>\n"]) == set())

test("ignores comments",
     _extract_function_calls(["// foo(bar)\n"]) == set())


# ── Hallucination detection ──────────────────────────────────────────────────
print("\nHallucination detection:")
test("no hallucination when only using existing calls",
     _validate_no_hallucination(
         ["foo(x);\n"],
         ["foo(x);\n", "bar(y);\n"],
         ["foo(x);\n"]))

test("detects hallucinated function",
     not _validate_no_hallucination(
         ["totally_new_func(x);\n"],
         ["foo(x);\n"],
         ["bar(y);\n"]))

test("allows safe stdlib functions",
     _validate_no_hallucination(
         ["printf(\"hello\");\n", "free(ptr);\n"],
         ["int x = 1;\n"],
         ["int y = 2;\n"]))


# ── Length validation ─────────────────────────────────────────────────────────
print("\nLength validation:")
test("reasonable length",
     _validate_length(
         ["line1\n", "line2\n"],
         ["a\n"],
         ["b\n", "c\n"]))

test("too long rejected",
     not _validate_length(
         ["l\n"] * 20,
         ["a\n", "b\n"],
         ["c\n"]))


# ── Config factory ────────────────────────────────────────────────────────────
print("\nConfig factory:")
test("disabled config returns None",
     create_resolver_from_config({"llm": {"enabled": False}}) is None)

test("empty config returns None",
     create_resolver_from_config({}) is None)

test("enabled without API key returns None",
     create_resolver_from_config({
         "llm": {"enabled": True, "api_key_env": "NONEXISTENT_KEY_12345"}
     }) is None)


# ── Prompt generation ─────────────────────────────────────────────────────────
print("\nPrompt generation:")

# Mock _call_openai to capture prompt
last_prompt = [""]
def mock_call_openai(api_key, model, system, user, temperature, timeout):
    last_prompt[0] = user
    return {"content": "int x = 1;\n", "tokens": 10}

import llm_resolver
original_call_openai = llm_resolver._call_openai
llm_resolver._call_openai = mock_call_openai

try:
    resolver = LLMResolver(provider="openai", model="test", api_key="test", max_calls=1)
    resolver.resolve_conflict("test.c", ["ours\n"], ["theirs\n"])
    test("prompt without release plan has no context", "Context: Release Operation" not in last_prompt[0])

    plan = {
        "strategy": "include",
        "operation_prs": [{"number": 123, "title": "Add feature X"}]
    }
    resolver = LLMResolver(provider="openai", model="test", api_key="test", max_calls=1, release_plan=plan)
    resolver.resolve_conflict("test.c", ["ours\n"], ["theirs\n"])
    test("prompt with release plan includes strategy", "INCLUDE release strategy" in last_prompt[0])
    test("prompt with release plan includes PR info", "PR #123: Add feature X" in last_prompt[0])
finally:
    llm_resolver._call_openai = original_call_openai


# ── Rate limiting ─────────────────────────────────────────────────────────────
print("\nRate limiting:")

# Create resolver with a fake key (we won't actually call API)
try:
    os.environ["_TEST_LLM_KEY"] = "test-key-12345"
    resolver = LLMResolver(
        provider="openai",
        model="test-model",
        api_key="test-key-12345",
        max_calls=2,
        timeout=1,
    )
    test("initial calls remaining", resolver.calls_remaining == 2)

    # Simulate calls (they'll fail but count)
    resolver._call_count = 2
    result = resolver.resolve_conflict(
        filepath="test.c",
        ours_lines=["int x = 1;\n"],
        theirs_lines=["int x = 2;\n"],
    )
    test("rate limited returns None", result is None)
    test("calls remaining after limit", resolver.calls_remaining == 0)
finally:
    if "_TEST_LLM_KEY" in os.environ:
        del os.environ["_TEST_LLM_KEY"]


# ── Feedback logging ─────────────────────────────────────────────────────────
print("\nFeedback logging:")

with tempfile.TemporaryDirectory() as tmpdir:
    resolver = LLMResolver(
        provider="openai",
        model="test-model",
        api_key="test-key",
        max_calls=5,
        feedback_dir=tmpdir,
    )
    resolver._log_feedback(
        "test.c", ["a\n"], ["b\n"], "cherry-pick",
        ["c\n"], "accepted", "test rationale", 1.5
    )
    feedback_file = os.path.join(tmpdir, "llm_feedback.jsonl")
    test("feedback file created", os.path.exists(feedback_file))

    with open(feedback_file) as f:
        entry = json.loads(f.readline())
    test("feedback has filepath", entry["filepath"] == "test.c")
    test("feedback has status", entry["status"] == "accepted")
    test("feedback has timestamp", "timestamp" in entry)
    test("feedback has elapsed", entry["elapsed_seconds"] == 1.5)


# ── LLMResolution dataclass ──────────────────────────────────────────────────
print("\nLLMResolution dataclass:")
res = LLMResolution(
    lines=["int x = 1;\n"],
    rationale="test reason",
    model="gpt-4o-mini",
    provider="openai",
    valid=True,
    elapsed_seconds=0.5,
    tokens_used=100,
)
test("resolution lines", res.lines == ["int x = 1;\n"])
test("resolution valid", res.valid is True)
test("resolution model", res.model == "gpt-4o-mini")


# ── Summary ───────────────────────────────────────────────────────────────────
print(f"\n{'='*40}")
print(f"Results: {passed} passed, {failed} failed")
if failed == 0:
    print("All tests passed!")
else:
    print("Some tests FAILED!")
    sys.exit(1)
