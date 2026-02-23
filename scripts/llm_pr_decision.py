#!/usr/bin/env python3
"""
llm_pr_decision.py
==================
LLM-powered PR-level decision making for the RDK-B release agent.

This module provides PHASE 2 intelligence: deep semantic analysis to decide
whether to include or exclude entire PRs when conflicts are detected.

Key differences from llm_resolver.py:
  - Works at PR level (not code hunk level)
  - Makes BINARY decisions: Include PR OR Exclude PR
  - NO code merging — just strategic PR selection
  - Considers full PR context, dependencies, and impact

Decision Criteria:
  1. Functional necessity: Is this PR critical for the release?
  2. Conflict resolution: Can we safely include this despite conflicts?
  3. Dependency chain: Does including this require other PRs?
  4. Risk assessment: What's the risk of including vs excluding?
  5. Strategy alignment: Does this align with include/exclude strategy?
"""

import json
import time
import hashlib
import subprocess
from typing import List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

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


@dataclass
class PRDecision:
    """LLM decision for a single PR."""
    pr_number: int
    decision: str  # "INCLUDE", "EXCLUDE", "MANUAL_REVIEW"
    confidence: str  # "HIGH", "MEDIUM", "LOW"
    rationale: str
    requires_prs: List[int]  # Additional PRs needed if included
    risks: List[str]
    benefits: List[str]
    model: str
    provider: str
    elapsed_seconds: float


# ── Prompt Templates ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are an expert release engineer for embedded systems (RDK-B platform).
Your task is to decide whether a Pull Request should be INCLUDED or EXCLUDED from a release branch.

RULES:
1. Respond with a JSON object ONLY (no markdown, no extra text)
2. Consider the full context: PR changes, conflicts, dependencies, release strategy
3. Be conservative: when in doubt, flag for MANUAL_REVIEW
4. NEVER suggest code changes — only decide to include/exclude the entire PR
5. Consider both technical and strategic factors

JSON Response Format:
{
  "decision": "INCLUDE" | "EXCLUDE" | "MANUAL_REVIEW",
  "confidence": "HIGH" | "MEDIUM" | "LOW",
  "rationale": "Brief explanation of the decision (2-3 sentences)",
  "requires_prs": [123, 456],  // Other PR numbers that must be included if this is included
  "risks": ["Risk 1", "Risk 2"],  // Risks of INCLUDING this PR
  "benefits": ["Benefit 1", "Benefit 2"]  // Benefits of INCLUDING this PR
}"""

PR_DECISION_PROMPT = """## Release Strategy: {strategy}

### PR to Evaluate
**PR #{pr_number}**: {pr_title}
- **Author**: {pr_author}
- **Merged**: {merged_at}
- **Changes**: +{additions}/-{deletions} across {files_count} files
- **Files Modified**:
{files_list}

### Code Pattern Analysis
{semantic_analysis}

### PR Diff Summary
```diff
{pr_diff}
```

### Detected Conflicts
{conflicts_info}

### Context: Other PRs in This Release
{other_prs_context}

### Current Release Plan
- **Strategy**: {strategy}
- **Target Version**: {version}
- **Base Branch**: {base_branch}

---

**Task**: Decide whether PR #{pr_number} should be INCLUDED in this {version} release.

**Consider**:
1. Does this PR provide critical functionality needed for {version}?
2. Can the detected conflicts be resolved by including/excluding other PRs?
3. What are the risks of including this PR vs excluding it?
4. Does this align with the {strategy} strategy?

Respond with the JSON decision object."""


class LLMPRDecisionMaker:
    """LLM-powered decision maker for PR inclusion/exclusion."""
    
    def __init__(self, config: Dict):
        llm_cfg = config.get("llm", {})
        
        if not llm_cfg.get("enabled"):
            raise ValueError("LLM must be enabled for PR decision making")
        
        self.provider = llm_cfg.get("provider", "openai")
        self.model = llm_cfg.get("model", "gpt-4o-mini")
        self.temperature = llm_cfg.get("temperature", 0.2)  # Higher for strategic decisions
        self.timeout = llm_cfg.get("timeout_seconds", 60)  # Longer for complex analysis
        self.max_calls = llm_cfg.get("max_calls_per_run", 50)
        
        # Get API key
        api_key_env = llm_cfg.get("api_key_env", "OPENAI_API_KEY")
        import os
        self.api_key = os.environ.get(api_key_env, "")
        
        # Endpoint for custom providers
        self.endpoint = llm_cfg.get("endpoint", "")
        
        # Config
        self.release_config = config
        self.strategy = config.get("strategy", "unknown")
        self.version = config.get("version", "unknown")
        self.base_branch = config.get("base_branch", "develop")
        
        # State
        self._call_count = 0
        self._decision_cache = {}
        self._feedback_log = Path("/tmp/rdkb-release-conflicts/llm_pr_decisions.jsonl")
        self._feedback_log.parent.mkdir(parents=True, exist_ok=True)
        
        print(f"  🤖 LLM PR Decision Maker initialized: {self.provider}/{self.model}")
    
    def decide_pr(self,
                  pr_number: int,
                  pr_metadata: Dict,
                  pr_diff: str,
                  conflicts: List[Dict],
                  all_prs_metadata: Dict[int, Dict],
                  semantic_info: Optional[Dict] = None) -> Optional[PRDecision]:
        """
        Use LLM to decide whether to include or exclude a PR.
        
        Args:
            pr_number: The PR to decide on
            pr_metadata: Metadata dict for this PR
            pr_diff: Full unified diff of the PR
            conflicts: List of detected conflict dicts
            all_prs_metadata: Metadata for all PRs in the release
            semantic_info: Optional semantic analysis results from code_pattern_analyzer
            
        Returns:
            PRDecision if successful, None if failed
        """
        # Check cache
        cache_key = hashlib.md5(
            f"{pr_number}|{self.strategy}|{self.version}".encode()
        ).hexdigest()
        
        if cache_key in self._decision_cache:
            print(f"    📋 Using cached decision for PR #{pr_number}")
            return self._decision_cache[cache_key]
        
        # Rate limit check
        if self._call_count >= self.max_calls:
            print(f"    ⚠️  LLM rate limit reached ({self.max_calls} calls)")
            return None
        
        self._call_count += 1
        
        # Build prompt context
        files_list = "\n".join([f"  - {f}" for f in pr_metadata.get("files_changed", [])])
        
        # Format conflicts info
        if conflicts:
            conflicts_info = "**Detected Issues**:\n"
            for c in conflicts:
                conflicts_info += f"- {c['severity'].upper()}: {c['reason']}\n"
                if c.get('shared_files'):
                    conflicts_info += f"  Files: {', '.join(c['shared_files'][:3])}\n"
                if c.get('conflicting_with'):
                    conflicts_info += f"  Conflicts with PRs: {c['conflicting_with']}\n"
        else:
            conflicts_info = "No conflicts detected. This PR appears safe to include."
        
        # Format other PRs context
        other_prs = [p for p in all_prs_metadata.values() if p['number'] != pr_number]
        if other_prs:
            other_prs_context = "**Other PRs being processed**:\n"
            for p in other_prs[:10]:  # Limit to first 10 for context length
                other_prs_context += f"- PR #{p['number']}: {p.get('title', 'N/A')}\n"
        else:
            other_prs_context = "This is the only PR being evaluated."
        
        # Truncate diff if too long (keep first 200 lines)
        diff_lines = pr_diff.split('\n')
        if len(diff_lines) > 200:
            pr_diff = '\n'.join(diff_lines[:200]) + f"\n\n... ({len(diff_lines) - 200} more lines truncated)"
        
        # Format semantic analysis information
        if semantic_info:
            semantic_analysis = "**Semantic Code Analysis**:\n"
            semantic_analysis += f"- **Change Type**: {semantic_info.get('change_type', 'unknown')}"
            
            if semantic_info.get('cosmetic_only'):
                semantic_analysis += " (cosmetic changes only - low risk)"
            elif semantic_info.get('safety_focused'):
                semantic_analysis += " (safety improvements - generally beneficial)"
            
            semantic_analysis += "\n"
            
            # Add pattern counts if significant
            null_checks = semantic_info.get('null_checks_added', 0)
            error_handling = semantic_info.get('error_handling_added', 0)
            safety_patterns = semantic_info.get('safety_patterns_added', 0)
            functional = semantic_info.get('functional_changes', 0)
            
            if null_checks > 0:
                semantic_analysis += f"- **NULL Checks Added**: {null_checks}\n"
            if error_handling > 0:
                semantic_analysis += f"- **Error Handling Added**: {error_handling}\n"
            if safety_patterns > 0:
                semantic_analysis += f"- **Safety Patterns Added**: {safety_patterns}\n"
            if functional > 0:
                semantic_analysis += f"- **Functional Changes**: {functional} lines\n"
            
            semantic_analysis += f"- **Summary**: {semantic_info.get('summary', 'N/A')}\n"
            semantic_analysis += f"- **Analysis Confidence**: {semantic_info.get('confidence', 'UNKNOWN')}"
        else:
            semantic_analysis = "**Code analysis not available** (diff could not be analyzed)"
        
        prompt = PR_DECISION_PROMPT.format(
            strategy=self.strategy.upper(),
            pr_number=pr_number,
            pr_title=pr_metadata.get("title", "N/A"),
            pr_author=pr_metadata.get("author", "unknown"),
            merged_at=pr_metadata.get("merged_at", "unknown"),
            additions=pr_metadata.get("additions", 0),
            deletions=pr_metadata.get("deletions", 0),
            files_count=pr_metadata.get("files_count", 0),
            files_list=files_list,
            pr_diff=pr_diff,
            conflicts_info=conflicts_info,
            semantic_analysis=semantic_analysis,
            other_prs_context=other_prs_context,
            version=self.version,
            base_branch=self.base_branch
        )
        
        # Call LLM
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
            elif self.provider == "gemini":
                response = _call_gemini(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout
                )
            elif self.provider == "azureopenai":
                response = _call_azureopenai(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout, self.endpoint
                )
            else:
                response = _call_generic(
                    self.api_key, self.model, SYSTEM_PROMPT, prompt,
                    self.temperature, self.timeout, self.endpoint
                )
        except Exception as e:
            elapsed = time.time() - t0
            print(f"    ❌ LLM call failed ({elapsed:.1f}s): {e}")
            self._log_decision(pr_number, None, "error", str(e), elapsed)
            return None
        
        elapsed = time.time() - t0
        content = response["content"]
        
        # Parse JSON response
        try:
            # Strip markdown fences if present
            if '```json' in content:
                content = content.split('```json')[1].split('```')[0]
            elif '```' in content:
                content = content.split('```')[1].split('```')[0]
            
            decision_data = json.loads(content.strip())
            
            # Validate required fields
            required = ["decision", "confidence", "rationale"]
            if not all(k in decision_data for k in required):
                raise ValueError(f"Missing required fields: {required}")
            
            # Validate decision value
            if decision_data["decision"] not in ["INCLUDE", "EXCLUDE", "MANUAL_REVIEW"]:
                raise ValueError(f"Invalid decision: {decision_data['decision']}")
            
            # Create decision object
            decision = PRDecision(
                pr_number=pr_number,
                decision=decision_data["decision"],
                confidence=decision_data.get("confidence", "LOW"),
                rationale=decision_data.get("rationale", ""),
                requires_prs=decision_data.get("requires_prs", []),
                risks=decision_data.get("risks", []),
                benefits=decision_data.get("benefits", []),
                model=self.model,
                provider=self.provider,
                elapsed_seconds=elapsed
            )
            
            # Cache the decision
            self._decision_cache[cache_key] = decision
            
            # Log for feedback
            self._log_decision(pr_number, decision, "success", "", elapsed)
            
            return decision
            
        except json.JSONDecodeError as e:
            print(f"    ❌ Failed to parse LLM JSON response: {e}")
            print(f"    Response: {content[:200]}...")
            self._log_decision(pr_number, None, "parse_error", str(e), elapsed)
            return None
        except Exception as e:
            print(f"    ❌ Validation error: {e}")
            self._log_decision(pr_number, None, "validation_error", str(e), elapsed)
            return None
    
    def _log_decision(self, pr_number: int, decision: Optional[PRDecision],
                     status: str, error: str, elapsed: float):
        """Log decision for audit trail and feedback."""
        entry = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "pr_number": pr_number,
            "status": status,
            "decision": decision.decision if decision else None,
            "confidence": decision.confidence if decision else None,
            "rationale": decision.rationale if decision else None,
            "error": error,
            "elapsed_seconds": elapsed,
            "model": self.model,
            "provider": self.provider,
            "strategy": self.strategy,
            "version": self.version
        }
        
        with open(self._feedback_log, "a") as f:
            f.write(json.dumps(entry) + "\n")


# ── CLI for standalone testing ───────────────────────────────────────────────
if __name__ == "__main__":
    import argparse
    import yaml
    
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, help="Path to .release-config.yml")
    parser.add_argument("--pr", required=True, type=int, help="PR number to evaluate")
    parser.add_argument("--repo", required=True, help="GitHub repo (owner/name)")
    parser.add_argument("--conflicts", help="JSON file with conflict analysis")
    args = parser.parse_args()
    
    # Load config
    with open(args.config) as f:
        config = yaml.safe_load(f)
    
    # Load conflicts if provided
    conflicts = []
    if args.conflicts and Path(args.conflicts).exists():
        with open(args.conflicts) as f:
            conflict_data = json.load(f)
            conflicts = conflict_data.get("conflicts", {}).get("all", [])
    
    # Fetch PR metadata
    result = subprocess.run(
        ["gh", "pr", "view", str(args.pr), "--repo", args.repo,
         "--json", "number,title,author,mergedAt,files,additions,deletions"],
        capture_output=True, text=True
    )
    pr_metadata = json.loads(result.stdout)
    
    # Get PR diff
    diff_result = subprocess.run(
        ["gh", "pr", "diff", str(args.pr), "--repo", args.repo],
        capture_output=True, text=True
    )
    pr_diff = diff_result.stdout
    
    # Make decision
    decision_maker = LLMPRDecisionMaker(config)
    decision = decision_maker.decide_pr(
        args.pr,
        pr_metadata,
        pr_diff,
        [c for c in conflicts if c["pr_number"] == args.pr],
        {args.pr: pr_metadata}
    )
    
    if decision:
        print(f"\n  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  📋 Decision for PR #{decision.pr_number}")
        print(f"  ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        print(f"  Decision: {decision.decision}")
        print(f"  Confidence: {decision.confidence}")
        print(f"  Rationale: {decision.rationale}")
        if decision.requires_prs:
            print(f"  Requires PRs: {decision.requires_prs}")
        if decision.risks:
            print(f"  Risks: {', '.join(decision.risks)}")
        if decision.benefits:
            print(f"  Benefits: {', '.join(decision.benefits)}")
        print(f"  Model: {decision.provider}/{decision.model}")
        print(f"  Time: {decision.elapsed_seconds:.1f}s")
