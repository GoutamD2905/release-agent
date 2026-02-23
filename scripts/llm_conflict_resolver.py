#!/usr/bin/env python3
"""
llm_conflict_resolver.py
========================
LLM-powered merge conflict resolution for the RDK-B release agent.

This module provides intelligent conflict resolution at the code level:
  - Detects merge conflicts in files
  - Parses conflict markers (<<<<<<< ======= >>>>>>>)
  - Uses LLM to intelligently resolve conflicts
  - Applies the resolution and continues the merge/cherry-pick

Unlike PR-level decisions, this resolves actual code conflicts.
"""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

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


@dataclass
class ConflictResolution:
    """Resolution for a single conflict."""
    conflict_index: int
    resolution_type: str  # "OURS", "THEIRS", "BOTH", "CUSTOM"
    resolved_content: str
    rationale: str
    risks: List[str]
    confidence: str


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
                
                conflicts.append(ConflictBlock(
                    file_path=file_path,
                    conflict_index=conflict_index,
                    ours_content='\n'.join(ours_lines),
                    theirs_content='\n'.join(theirs_lines),
                    base_content='\n'.join(base_lines) if base_lines else None,
                    start_line=start_line,
                    end_line=end_line
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
        Resolve all conflicts in a file using LLM.
        
        Args:
            file_path: Path to the conflicted file
            pr_number: PR number being processed
            pr_metadata: PR metadata dict
            operation: "cherry-pick" or "revert"
            
        Returns:
            True if conflicts were successfully resolved
        """
        print(f"\n  🔧 Resolving conflicts in {file_path}...")
        
        # Parse conflicts
        conflicts = self.parse_conflicts(file_path)
        if not conflicts:
            print(f"  ⚠️  No conflicts found in {file_path}")
            return False
        
        print(f"  Found {len(conflicts)} conflict blocks")
        
        # Build conflict details for LLM
        conflict_details = ""
        for i, conflict in enumerate(conflicts):
            conflict_details += f"\n### Conflict {i+1} (Lines {conflict.start_line}-{conflict.end_line})\n\n"
            conflict_details += "**OURS (current branch)**:\n```\n"
            conflict_details += conflict.ours_content[:500] + ("\n...(truncated)" if len(conflict.ours_content) > 500 else "")
            conflict_details += "\n```\n\n"
            conflict_details += "**THEIRS (incoming change)**:\n```\n"
            conflict_details += conflict.theirs_content[:500] + ("\n...(truncated)" if len(conflict.theirs_content) > 500 else "")
            conflict_details += "\n```\n"
            if conflict.base_content:
                conflict_details += "\n**BASE (common ancestor)**:\n```\n"
                conflict_details += conflict.base_content[:500] + ("\n...(truncated)" if len(conflict.base_content) > 500 else "")
                conflict_details += "\n```\n"
        
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
        print(f"  🤖 Consulting LLM for conflict resolution...")
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
            resolutions = []
            for res_data in resolutions_data:
                resolutions.append(ConflictResolution(
                    conflict_index=res_data.get('conflict_index', 0),
                    resolution_type=res_data.get('resolution_type', 'OURS'),
                    resolved_content=res_data.get('resolved_content', ''),
                    rationale=res_data.get('rationale', ''),
                    risks=res_data.get('risks', []),
                    confidence=res_data.get('confidence', 'LOW')
                ))
            
            print(f"  ✅ LLM provided {len(resolutions)} resolutions ({elapsed:.1f}s)")
            
        except Exception as e:
            print(f"  ❌ Failed to parse LLM response: {e}")
            print(f"  Response: {content[:200]}...")
            return False
        
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
                    "risks": r.risks
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
