#!/usr/bin/env python3
"""
llm_providers.py
================
LLM API provider functions for the RDK-B release agent.

This module provides unified API client functions for multiple LLM providers:
  - OpenAI (GPT-4, GPT-4o-mini, etc.)
  - Google Gemini (gemini-2.0-flash, gemini-1.5-pro, etc.)
  - Azure OpenAI (GPT-4, Claude via Azure, etc.)
  - GitHub Copilot (GitHub Models)
  - Generic OpenAI-compatible APIs (Ollama, vLLM, DeepSeek, etc.)

All provider functions follow the same signature:
  _call_<provider>(api_key, model, system, user, temperature, timeout, [endpoint])
  
  Returns: dict with {"content": str, "tokens": int}
  Raises: ConnectionError on API failures

Used by:
  - llm_pr_decision.py (PR-level strategic decisions)
  - Future LLM-powered modules

Configuration (in .release-config.yml):
  llm:
    enabled: true
    provider: "openai"                    # Provider name
    model: "gpt-4o-mini"                  # Model identifier
    api_key_env: "OPENAI_API_KEY"        # Env var for API key
    endpoint: ""                          # Required for custom providers
    temperature: 0.2
    timeout_seconds: 60
"""

import json


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _call_openai(api_key: str, model: str, system: str, user: str,
                 temperature: float, timeout: int) -> dict:
    """
    Call OpenAI Chat Completions API.
    
    Args:
        api_key: OpenAI API key
        model: Model name (e.g., "gpt-4o-mini", "gpt-4o", "o1")
        system: System prompt
        user: User prompt
        temperature: Sampling temperature (0.0-2.0)
        timeout: Request timeout in seconds
        
    Returns:
        dict: {"content": str, "tokens": int}
        
    Raises:
        ConnectionError: On API failures
    """
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


# ── Google Gemini ─────────────────────────────────────────────────────────────

def _call_gemini(api_key: str, model: str, system: str, user: str,
                 temperature: float, timeout: int) -> dict:
    """
    Call Google Gemini API.
    
    Args:
        api_key: Google API key
        model: Model name (e.g., "gemini-2.0-flash", "gemini-1.5-pro")
        system: System instruction
        user: User prompt
        temperature: Sampling temperature (0.0-2.0)
        timeout: Request timeout in seconds
        
    Returns:
        dict: {"content": str, "tokens": int}
        
    Raises:
        ConnectionError: On API failures
    """
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


# ── Azure OpenAI ──────────────────────────────────────────────────────────────

def _call_azureopenai(api_key: str, model: str, system: str, user: str,
                      temperature: float, timeout: int, endpoint: str) -> dict:
    """
    Call Azure OpenAI API (supports both OpenAI and Claude models via Azure).
    
    Args:
        api_key: Azure API key
        model: Model deployment name
        system: System prompt
        user: User prompt
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        endpoint: Azure endpoint URL (required)
        
    Returns:
        dict: {"content": str, "tokens": int}
        
    Raises:
        ConnectionError: On API failures
    """
    import urllib.request
    import urllib.error

    is_claude = "claude" in model.lower()

    if is_claude:
        # Claude on Azure uses Anthropic-style API
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
        # Standard Azure OpenAI API
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user}
            ],
            "temperature": temperature,
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


# ── GitHub Copilot ────────────────────────────────────────────────────────────

def _call_githubcopilot(api_key: str, model: str, system: str, user: str,
                        temperature: float, timeout: int) -> dict:
    """
    Call GitHub Copilot API (GitHub Models).
    
    Args:
        api_key: GitHub token (gh auth token)
        model: Model name (e.g., "gpt-4o", "claude-3.5-sonnet")
        system: System prompt
        user: User prompt
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        
    Returns:
        dict: {"content": str, "tokens": int}
        
    Raises:
        ConnectionError: On API failures
    """
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


# ── Generic OpenAI-Compatible API ────────────────────────────────────────────

def _call_generic(api_key: str, model: str, system: str, user: str,
                  temperature: float, timeout: int, endpoint: str) -> dict:
    """
    Call a generic OpenAI-compatible API endpoint.
    
    Supports: Ollama, vLLM, DeepSeek, Together AI, Replicate, and any
    OpenAI-compatible inference server.
    
    Args:
        api_key: API key (optional for local providers like Ollama)
        model: Model identifier
        system: System prompt
        user: User prompt
        temperature: Sampling temperature
        timeout: Request timeout in seconds
        endpoint: API endpoint URL (required)
        
    Returns:
        dict: {"content": str, "tokens": int}
        
    Raises:
        ConnectionError: On API failures
    """
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
            
            # Try OpenAI-style response format first
            if "choices" in data:
                content = data["choices"][0]["message"]["content"]
                tokens = data.get("usage", {}).get("total_tokens", 0)
            # Fallback to Ollama-style format
            else:
                content = data.get("message", {}).get("content", "")
                tokens = data.get("prompt_eval_count", 0) + data.get("eval_count", 0)
            
            return {"content": content, "tokens": tokens}
    except urllib.error.URLError as e:
        raise ConnectionError(f"Generic API error: {e}")
