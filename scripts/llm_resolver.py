#!/usr/bin/env python3
"""
llm_resolver.py
===============
LLM-powered conflict resolution for the RDK-B release agent.

When the rule-based smart_merge engine encounters a FUNCTIONAL conflict
(LOW confidence), this module calls an LLM API to semantically understand
both sides and produce an intelligent merged resolution.

Supported providers:
  - OpenAI  (gpt-4o-mini, gpt-4o, etc.)
#   - Google Gemini (gemini-2.0-flash, gemini-1.5-pro, etc.)
#   - Ollama (local deployment: deepseek-coder:6.7b, etc.)
#
# Safety guards:
#   - Response must look like valid C (balanced braces/brackets)
#   - Response cannot introduce function calls not in either side
#   - Response length bounded at 2× the longer side
#   - Per-call timeout (default 10s)
#   - Rate limit per release run (default 5 calls)
#
# Progressive learning:
#   - Every resolution is logged to llm_feedback.jsonl
#   - Component owners can mark accepted/rejected for future fine-tuning
# """

import os
import re
import json
import time
from pathlib import Path
from dataclasses import dataclass
from typing import List, Optional
from datetime import datetime


@dataclass
class LLMResolution:
    """Result from an LLM conflict resolution attempt."""
    lines: List[str]
    rationale: str
    model: str
    provider: str
    valid: bool
    elapsed_seconds: float
    tokens_used: int = 0


# ── Prompt template ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert C/C++ developer specializing in embedded systems (RDK-B platform).
Your task is to resolve a Git merge conflict by producing the CORRECT merged code.

RULES:
1. Output ONLY the resolved C code — no markdown, no explanations, no comments about the merge
2. The output must be syntactically valid C that compiles
3. Do NOT invent new function calls, variables, or logic not present in either side
4. Preserve ALL safety improvements (NULL checks, bounds checks, error handling)
5. Preserve ALL functional changes from the intended side
6. Keep the project's coding style (Allman brace style, 4-space indent)
7. If both sides add different features, include BOTH if they don't conflict
8. When in doubt, prefer the side that adds safety checks or error handling"""

RESOLVE_PROMPT = """## Conflict in `{filepath}`

### Operation: {mode}
{mode_explanation}

{pr_context}

### Code BEFORE the conflict (context):
```c
{context_before}
```

### OURS side (current branch):
```c
{ours_code}
```

### THEIRS side (incoming change):
```c
{theirs_code}
```

### Code AFTER the conflict (context):
```c
{context_after}
```

Produce the correctly merged code for this conflict hunk. Output ONLY the resolved C code lines, nothing else."""


# ── Validation ───────────────────────────────────────────────────────────────

def _validate_c_syntax(lines: List[str]) -> bool:
    """Basic validation that output looks like valid C."""
    code = ''.join(lines)

    # Check balanced braces
    brace_count = code.count('{') - code.count('}')
    if abs(brace_count) > 0:
        return False

    # Check balanced parentheses
    paren_count = code.count('(') - code.count(')')
    if abs(paren_count) > 0:
        return False

    # Check balanced brackets
    bracket_count = code.count('[') - code.count(']')
    if abs(bracket_count) > 0:
        return False

    # Must not be empty
    if not code.strip():
        return False

    return True


def _extract_function_calls(lines: List[str]) -> set:
    """Extract function call names from code lines."""
    pattern = re.compile(r'\b([a-zA-Z_]\w*)\s*\(')
    calls = set()
    for line in lines:
        # Skip preprocessor directives and comments
        stripped = line.strip()
        if stripped.startswith('#') or stripped.startswith('//') or stripped.startswith('/*'):
            continue
        calls.update(pattern.findall(line))
    # Remove C keywords that look like function calls
    keywords = {'if', 'for', 'while', 'switch', 'return', 'sizeof', 'typeof',
                'defined', 'else', 'case', 'do'}
    return calls - keywords


def _validate_no_hallucination(resolved: List[str], ours: List[str], theirs: List[str]) -> bool:
    """Ensure LLM didn't invent new function calls."""
    resolved_calls = _extract_function_calls(resolved)
    allowed_calls = _extract_function_calls(ours) | _extract_function_calls(theirs)
    # Allow common C stdlib functions the LLM might reasonably use
    safe_stdlib = {'printf', 'fprintf', 'snprintf', 'strcmp', 'strncmp', 'strlen',
                   'malloc', 'calloc', 'realloc', 'free', 'memset', 'memcpy',
                   'close', 'fclose', 'open', 'fopen', 'NULL'}
    new_calls = resolved_calls - allowed_calls - safe_stdlib
    return len(new_calls) == 0


def _validate_length(resolved: List[str], ours: List[str], theirs: List[str]) -> bool:
    """Ensure response length is reasonable."""
    max_len = max(len(ours), len(theirs)) * 2 + 5
    return len(resolved) <= max_len


# ── Provider clients ─────────────────────────────────────────────────────────

def _call_openai(api_key: str, model: str, system: str, user: str,
                 temperature: float, timeout: int) -> dict:
    """Call OpenAI Chat Completions API."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": temperature,
        "max_tokens": 2000,
    }).encode('utf-8')

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"content": content, "tokens": tokens}
    except urllib.error.URLError as e:
        raise ConnectionError(f"OpenAI API error: {e}")


def _call_gemini(api_key: str, model: str, system: str, user: str,
                 temperature: float, timeout: int) -> dict:
    """Call Google Gemini API."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "system_instruction": {"parts": [{"text": system}]},
        "contents": [{"parts": [{"text": user}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": 2000,
        }
    }).encode('utf-8')

    url = (f"https://generativelanguage.googleapis.com/v1beta/models/"
           f"{model}:generateContent?key={api_key}")

    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data["candidates"][0]["content"]["parts"][0]["text"]
            tokens = data.get("usageMetadata", {}).get("totalTokenCount", 0)
            return {"content": content, "tokens": tokens}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Gemini API error: {e}")


def _call_azureopenai(api_key: str, model: str, system: str, user: str,
                      temperature: float, timeout: int, endpoint: str) -> dict:
    """Call Azure OpenAI API."""
    import urllib.request
    import urllib.error

    is_claude = "claude" in model.lower()

    if is_claude:
        payload = json.dumps({
            "model": model,
            "system": system,
            "messages": [
                {"role": "user", "content": user}
            ],
            "temperature": 0.7,
            "max_tokens": 2000,
        }).encode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "anthropic-version": "2023-06-01"
        }
    else:
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": 1,
            "max_completion_tokens": 2000,
        }).encode('utf-8')
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers=headers
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if is_claude:
                content = data["content"][0]["text"]
                tokens = data.get("usage", {}).get("output_tokens", 0) + data.get("usage", {}).get("input_tokens", 0)
            else:
                content = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"content": content, "tokens": tokens}
    except urllib.error.HTTPError as e:
        error_msg = e.read().decode('utf-8', errors='ignore')
        raise ConnectionError(f"Azure OpenAI API error: HTTP {e.code}: {error_msg}")
    except urllib.error.URLError as e:
        raise ConnectionError(f"Azure OpenAI API error: {e}")


def _call_githubcopilot(api_key: str, model: str, system: str, user: str,
                        temperature: float, timeout: int) -> dict:
    """Call GitHub Copilot API (compatible with Copilot API responses endpoint)."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": temperature,
        "max_tokens": 2000,
    }).encode('utf-8')

    # Many Copilot endpoints require a specific accept header
    req = urllib.request.Request(
        "https://api.githubcopilot.com/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
            "Editor-Version": "vscode/1.85.0",
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            content = data["choices"][0]["message"]["content"]
            tokens = data.get("usage", {}).get("total_tokens", 0)
            return {"content": content, "tokens": tokens}
    except urllib.error.URLError as e:
        raise ConnectionError(f"GitHub Copilot API error: {e}")

def _call_generic(api_key: str, model: str, system: str, user: str,
                  temperature: float, timeout: int, endpoint: str) -> dict:
    """Call a generic OpenAI-compatible REST API (e.g. Ollama, vLLM, DeepSeek, custom)."""
    import urllib.request
    import urllib.error

    payload = json.dumps({
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user}
        ],
        "temperature": temperature,
        "stream": False
    }).encode('utf-8')

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    req = urllib.request.Request(
        endpoint,
        data=payload,
        headers=headers
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            if "choices" in data:
                content = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 0)
            else:
                content = data.get("message", {}).get("content", "")
                tokens = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
            return {"content": content, "tokens": tokens}
    except urllib.error.URLError as e:
        raise ConnectionError(f"API error for generic provider: {e}")


# ── Main resolver class ──────────────────────────────────────────────────────

class LLMResolver:
    """
    LLM-powered conflict resolver for functional C code conflicts.

    Usage:
        resolver = LLMResolver(
            provider="openai",
            model="gpt-4o-mini",
            api_key="sk-...",
        )
        result = resolver.resolve_conflict(
            filepath="source/main.c",
            ours_lines=["int x = 1;\n"],
            theirs_lines=["int x = 2;\n"],
            context_before=["void foo() {\n"],
            context_after=["}\n"],
            mode="cherry-pick"
        )
    """

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o-mini",
        api_key: str = "",
        temperature: float = 0.1,
        timeout: int = 10,
        max_calls: int = 5,
        feedback_dir: str = "/tmp/rdkb-release-conflicts",
        release_plan: Optional[dict] = None,
        endpoint: str = "",
    ):
        self.provider = provider.lower()
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.max_calls = max_calls
        self.feedback_dir = Path(feedback_dir)
        self.feedback_dir.mkdir(parents=True, exist_ok=True)
        self._call_count = 0
        self.release_plan = release_plan
        self.endpoint = endpoint

        if not self.api_key and self.provider in ("openai", "gemini", "githubcopilot", "azureopenai"):
            raise ValueError(f"LLM API key is empty. Set the environment variable for {self.provider}.")

        if self.provider not in ("openai", "gemini", "githubcopilot"):
            if not self.endpoint:
                raise ValueError(f"Provider '{self.provider}' requires an 'endpoint' setting in the config.")

    def evaluate_dependency(self, diff_a: str, diff_b: str) -> tuple[bool, bool]:
        """
        Call LLM to decide if two PRs that touch the same files are functionally dependent,
        and whether the dependency is critical enough to mandate auto-inclusion.
        Returns (is_dependent, is_critical).
        """
        if self._call_count >= self.max_calls:
            print(f"    ⚠️  LLM rate limit reached ({self.max_calls} calls). Defaulting to dependent.")
            return True, False # Fallback to True but not critical

        self._call_count += 1
        
        sys_prompt = "You are a senior C developer. You have absolute precision in determining if two code diffs have a functional dependency."
        
        user_prompt = (
            "Determine if there is a functional, syntactical, or logical dependency between the two Git diffs.\n"
            "Diff B was merged BEFORE Diff A.\n\n"
            "Diff B (The older PR):\n"
            f"```diff\n{diff_b}\n```\n\n"
            "Diff A (The newer PR):\n"
            f"```diff\n{diff_a}\n```\n\n"
            "Does Diff A functionally depend on the structural changes introduced by Diff B? "
            "For example, did Diff B add a variable, function, struct, or macro that Diff A uses? "
            "Or did Diff B rewrite a mechanism that Diff A implicitly relies upon? "
            "If they touch different independent lines or features within the same file without relying on each other, reply exactly with: NO\n"
            "If there is a true dependency, determine if it is CRITICAL (code will fail to compile or run without Diff B) or OPTIONAL (code can be cherry-picked without Diff B, maybe with minor conflicts that can be easily resolved).\n"
            "Reply exactly with: YES_CRITICAL, YES_OPTIONAL, or NO.\n"
            "IMPORTANT: Reply ONLY with one of these three options."
        )
        
        try:
            if self.provider == "openai":
                response = _call_openai(self.api_key, self.model, sys_prompt, user_prompt, self.temperature, self.timeout)
            elif self.provider == "githubcopilot":
                response = _call_githubcopilot(self.api_key, self.model, sys_prompt, user_prompt, self.temperature, self.timeout)
            elif self.provider == "azureopenai":
                response = _call_azureopenai(self.api_key, self.model, sys_prompt, user_prompt, self.temperature, self.timeout, self.endpoint)
            elif self.provider == "gemini":
                response = _call_gemini(self.api_key, self.model, sys_prompt, user_prompt, self.temperature, self.timeout)
            else:
                # Generic fallback for ollama, vllm, deepseek, or any custom API
                response = _call_generic(self.api_key, self.model, sys_prompt, user_prompt, self.temperature, self.timeout, self.endpoint)
            
            content = response.get("content", "").strip().upper()
            is_dependent = "YES" in content
            is_critical = "YES_CRITICAL" in content
            return is_dependent, is_critical
            
        except ConnectionError as e:
            print(f"    ❌ LLM call failed: {e}")
            return True, False # Fallback to True but not critical

    def resolve_conflict(
        self,
        filepath: str,
        ours_lines: List[str],
        theirs_lines: List[str],
        context_before: Optional[List[str]] = None,
        context_after: Optional[List[str]] = None,
        mode: str = "cherry-pick",
        pr_context_str: str = "",
    ) -> Optional[LLMResolution]:
        """
        Call LLM to resolve a functional conflict hunk.

        Returns LLMResolution if successful, None if failed/skipped.
        """
        # Rate limit check
        if self._call_count >= self.max_calls:
            print(f"    ⚠️  LLM rate limit reached ({self.max_calls} calls). Skipping.")
            return None

        self._call_count += 1

        # Build the prompt
        mode_explanations = {
            "cherry-pick": "We are cherry-picking a PR's commit into the release branch. "
                          "'THEIRS' is the incoming PR change we want to include.",
            "revert": "We are reverting a PR's commit from the release branch. "
                     "'THEIRS' is the change being reverted — we want to UNDO its effect.",
        }

        # Build context from release plan
        plan = self.release_plan
        if isinstance(plan, dict):
            strategy = plan.get("strategy", "unknown")
            prs = plan.get("operation_prs", [])
            if prs:
                pr_list = "\n".join([f"  - PR #{p.get('number')}: {p.get('title')}" for p in prs])
                pr_context_str = (
                    f"### Context: Release Operation\n"
                    f"We are performing a {strategy.upper()} release strategy.\n"
                    f"The following PRs are being processed in this run:\n{pr_list}\n"
                )

        prompt = RESOLVE_PROMPT.format(
            filepath=os.path.basename(filepath),
            mode=mode.upper(),
            mode_explanation=mode_explanations.get(mode, ""),
            pr_context=pr_context_str,
            context_before=''.join(context_before or ["// (no context available)\n"]),
            ours_code=''.join(ours_lines),
            theirs_code=''.join(theirs_lines),
            context_after=''.join(context_after or ["// (no context available)\n"]),
        )

        # Call the LLM
        t0 = time.time()
        try:
            if self.provider == "openai":
                response = _call_openai(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout
                )
            elif self.provider == "githubcopilot":
                response = _call_githubcopilot(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout
                )
            elif self.provider == "azureopenai":
                response = _call_azureopenai(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout, self.endpoint
                )
            elif self.provider == "gemini":
                response = _call_gemini(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout
                )
            else:
                response = _call_generic(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout, self.endpoint
                )
        except (ConnectionError, TimeoutError, Exception) as e:
            elapsed = time.time() - t0
            print(f"    ❌ LLM call failed ({elapsed:.1f}s): {e}")
            self._log_feedback(filepath, ours_lines, theirs_lines, mode,
                             None, "error", str(e), elapsed)
            return None

        elapsed = time.time() - t0
        content = response["content"]
        tokens = response.get("tokens", 0)

        # Strip markdown code fences if present
        content = re.sub(r'^```[a-z]*\n', '', content, flags=re.MULTILINE)
        content = re.sub(r'\n```\s*$', '', content, flags=re.MULTILINE)

        resolved_lines = [line + '\n' for line in content.split('\n') if content.strip()]
        # Fix: if the content doesn't end with newline, ensure proper line splitting
        if content.strip():
            resolved_lines = [line + '\n' if not line.endswith('\n') else line
                            for line in content.split('\n')]
            # Remove trailing empty line if present
            while resolved_lines and not resolved_lines[-1].strip():
                resolved_lines.pop()
            if resolved_lines:
                # Ensure last line has newline
                if not resolved_lines[-1].endswith('\n'):
                    resolved_lines[-1] += '\n'

        # ── Validate the response ────────────────────────────────────────
        valid = True
        rejection_reason = ""

        if not _validate_c_syntax(resolved_lines):
            valid = False
            rejection_reason = "Invalid C syntax (unbalanced braces/brackets)"

        elif not _validate_no_hallucination(resolved_lines, ours_lines, theirs_lines):
            valid = False
            rejection_reason = "LLM introduced new function calls not in either side"

        elif not _validate_length(resolved_lines, ours_lines, theirs_lines):
            valid = False
            rejection_reason = "Response too long (exceeds 2× max side length)"

        # Build rationale from the model
        rationale = f"{self.provider}/{self.model} merged {len(ours_lines)}+{len(theirs_lines)} lines → {len(resolved_lines)} lines"
        if not valid:
            rationale = f"REJECTED: {rejection_reason}"

        result = LLMResolution(
            lines=resolved_lines if valid else [],
            rationale=rationale,
            model=self.model,
            provider=self.provider,
            valid=valid,
            elapsed_seconds=float(f"{elapsed:.2f}") if elapsed is not None else 0.0,
            tokens_used=tokens,
        )

        # Log for progressive learning
        status = "accepted" if valid else "rejected"
        self._log_feedback(filepath, ours_lines, theirs_lines, mode,
                         resolved_lines if valid else None, status, rationale, elapsed)

        if valid:
            print(f"    🤖 LLM resolved ({elapsed:.1f}s, {tokens} tokens): {rationale}")
        else:
            print(f"    ❌ LLM response rejected ({elapsed:.1f}s): {rejection_reason}")

        return result

    def _log_feedback(
        self,
        filepath: str,
        ours: List[str],
        theirs: List[str],
        mode: str,
        resolved: Optional[List[str]],
        status: str,
        rationale: str,
        elapsed: float,
    ):
        """Log resolution to feedback file for progressive learning."""
        feedback_file = self.feedback_dir / "llm_feedback.jsonl"
        ours_preview = ""
        if ours:
            for i, line in enumerate(ours):
                if i == 5: break
                ours_preview += line
            ours_preview = ours_preview[0:200]

        theirs_preview = ""
        if theirs:
            for i, line in enumerate(theirs):
                if i == 5: break
                theirs_preview += line
            theirs_preview = theirs_preview[0:200]

        resolved_preview = ""
        if resolved:
            for i, line in enumerate(resolved):
                if i == 5: break
                resolved_preview += line
            resolved_preview = resolved_preview[0:200]

        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "filepath": filepath,
            "mode": mode,
            "provider": self.provider,
            "model": self.model,
            "status": status,
            "rationale": rationale,
            "elapsed_seconds": float(f"{elapsed:.2f}") if elapsed is not None else 0.0,
            "ours_lines": len(ours) if ours is not None else 0,
            "theirs_lines": len(theirs) if theirs is not None else 0,
            "resolved_lines": len(resolved) if resolved is not None else 0,
            "ours_preview": ours_preview,
            "theirs_preview": theirs_preview,
            "resolved_preview": resolved_preview,
        }
        try:
            with open(feedback_file, "a") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception:
            pass  # Non-critical — don't break the release for logging

    @property
    def calls_remaining(self) -> int:
        """Number of LLM calls remaining in this run."""
        return max(0, self.max_calls - self._call_count)


def create_resolver_from_config(config: dict, release_plan: Optional[dict] = None) -> Optional[LLMResolver]:
    """
    Create an LLMResolver from release config dict.

    Expected config structure:
        llm:
          enabled: true
          provider: "openai"
          model: "gpt-4o-mini"
          api_key_env: "OPENAI_API_KEY"
          temperature: 0.1
          max_calls_per_run: 5
          timeout_seconds: 10
    """
    llm_cfg = config.get("llm", {})
    if not llm_cfg.get("enabled", False):
        return None

    provider = llm_cfg.get("provider", "openai")
    model = llm_cfg.get("model", "gpt-4o-mini")
    api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
    api_key = os.environ.get(api_key_env, "")
    endpoint = llm_cfg.get("endpoint", "")

    if not api_key and provider in ("openai", "gemini", "githubcopilot", "azureopenai"):
        print(f"  ⚠️  LLM enabled but {api_key_env} not set. Disabling LLM resolution.")
        return None

    if provider not in ("openai", "gemini", "githubcopilot") and not endpoint:
        print(f"  ⚠️  LLM enabled for {provider} but no 'endpoint' configured. Disabling.")
        return None

    try:
        return LLMResolver(
            provider=provider,
            model=model,
            api_key=api_key,
            temperature=llm_cfg.get("temperature", 0.1),
            timeout=llm_cfg.get("timeout_seconds", 10),
            max_calls=llm_cfg.get("max_calls_per_run", 5),
            release_plan=release_plan,
            endpoint=endpoint,
        )
    except ValueError as e:
        print(f"  ⚠️  LLM initialization failed: {e}. Disabling LLM resolution.")
        return None
