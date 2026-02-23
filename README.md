# 🤖 RDK-B Release Agent

**Hybrid AI-powered bi-weekly release automation for RDK-B components** — combining rule-based intelligence with LLM strategic decisions for safe, automated release management.

> **What's New**: Two-phase hybrid intelligence architecture (Feb 2026)  
> ✅ Rule-based conflict detection + C code pattern analysis  
> ✅ LLM strategic PR-level decisions (no code mutation)  
> ✅ Complete semantic analysis (NULL checks, error handling, safety patterns)

---

## ⚡ Quick Start

### Run from Component Directory

```bash
# 1. Clone the release agent into your component repo
cd /path/to/your-component
git clone https://github.com/GoutamD2905/release-agent.git

# 2. Create your config file
cat > .release-config.yml << 'EOF'
version: "2.2.0"
strategy: "include"
base_branch: "develop"

prs:
  - 123  # Bug fix
  - 456  # Feature
  - 789  # Security patch

llm:
  enabled: true
  provider: "openai"
  model: "gpt-4o-mini"
  api_key_env: "OPENAI_API_KEY"
EOF

# 3. Run the release orchestrator
python3 release-agent/scripts/release_orchestrator.py \
  --repo GoutamD2905/your-component \
  --config .release-config.yml \
  --version 2.2.0
```

---

## 🧠 Hybrid Intelligence Architecture

### Two-Phase Approach

**PHASE 1: Rule-Based Intelligence**
- 📋 Fetch PR metadata (via GitHub CLI)
- 🔍 Detect file overlaps between PRs
- ⏰ Detect timing conflicts (PRs merged close together)
- ⚠️ Identify critical files (Makefile, *_init.c, etc.)
- 🧬 Analyze C code patterns:
  - NULL checks, error handling
  - Safety improvements (snprintf, free, etc.)
  - Change type (cosmetic vs functional)

**PHASE 2: LLM Strategic Decisions**
- 🤖 LLM analyzes each conflicted PR with:
  - PR metadata + diff
  - Detected conflicts
  - Code pattern analysis (semantic context)
  - Other PRs in release
- Makes binary decisions: INCLUDE / EXCLUDE / MANUAL_REVIEW
- Provides confidence level and detailed rationale

**PHASE 3: Execute Operations**
- ✅ Cherry-pick/revert entire PRs
- ❌ NO code-level merging (safe!)
- 📊 Generate analysis reports

### Code Pattern Analysis

The agent performs **semantic C code analysis** on every PR:

| Pattern | Detection | Impact |
|---------|-----------|--------|
| **NULL Checks** | `if (!ptr)`, `if (ptr == NULL)` | Safety improvement |
| **Error Handling** | `return ANSC_STATUS_FAILURE`, `CcspTraceError` | Robustness |
| **Safety Patterns** | `snprintf`, `free`, `close` | Memory safety |
| **Cosmetic Changes** | Whitespace, braces, comments | Low risk |
| **Functional Changes** | Logic modifications | Requires review |

**Example LLM Decision**:
```json
{
  "pr_number": 123,
  "decision": "INCLUDE",
  "confidence": "HIGH",
  "rationale": "PR adds 3 NULL checks and 2 error handlers. Safety improvements with low risk.",
  "semantic_analysis": {
    "change_type": "null_check_added",
    "null_checks_added": 3,
    "error_handling_added": 2,
    "safety_focused": true
  }
}
```

---

## 📁 Repository Structure

```
release-agent/
├── README.md                           # This file
├── scripts/
│   ├── release_orchestrator.py         # Main orchestrator (creates branch + orchestrates)
│   ├── pr_conflict_analyzer.py         # Phase 1: Detection + semantics
│   ├── llm_pr_decision.py              # Phase 2: LLM strategic decisions
│   ├── code_pattern_analyzer.py        # C/C++ semantic code analysis
│   ├── llm_providers.py                # LLM API provider functions
│   ├── pr_level_resolver.py            # PR-level conflict resolution
│   └── utils.py                        # Shared utilities
└── config/
    └── .release-config.yml             # Main configuration (all options)
```

---

## 🔧 Configuration

### Basic Configuration

```yaml
# .release-config.yml
version: "2.4.0"
strategy: "include"              # or "exclude"
base_branch: "develop"

prs:
  - 100  # Bug fix
  - 150  # Security patch

component_name: "rdkb-wifi"      # optional
dry_run: false                   # true to simulate
```

### LLM Configuration

```yaml
llm:
  enabled: true
  provider: "openai"              # openai, gemini, githubcopilot, azureopenai, ollama
  model: "gpt-4o-mini"
  api_key_env: "OPENAI_API_KEY"
  temperature: 0.2
  timeout_seconds: 60
  max_calls_per_run: 50
```

### Supported LLM Providers

| Provider | API Key | Endpoint | Example Model |
|----------|---------|----------|---------------|
| `openai` | ✅ | ❌ | `gpt-4o-mini` |
| `gemini` | ✅ | ❌ | `gemini-2.0-flash` |
| `githubcopilot` | ✅ | ❌ | `gpt-4o` |
| `azureopenai` | ✅ | ✅ | `gpt-4` |
| `ollama` | ❌ | ✅ | `deepseek-coder:6.7b` |

---

## 🚀 Release Strategies

### Include Mode (Whitelist)

Cherry-pick only specified PRs:

```yaml
strategy: "include"
base_branch: "main"
prs: [123, 456, 789]  # Only these PRs
```

**Use when**: Tight control over release content.

### Exclude Mode (Blacklist)

Take everything except specified PRs:

```yaml
strategy: "exclude"
base_branch: "develop"
prs: [205, 310]  # Skip these PRs
```

**Use when**: Most changes are release-ready.

---

## 🛠️ Command-Line Usage

```bash
python3 release-agent/scripts/release_orchestrator.py \
  --repo <owner/repo>              # Required
  --config <path>                   # Optional (default: .release-config.yml)
  --version <version>               # Optional (overrides config)
  --dry-run                         # Optional (simulate)
```

### Examples

**Production Release**:
```bash
python3 release-agent/scripts/release_orchestrator.py \
  --repo rdkcentral/rdkb-CcspPandM \
  --config .release-config.yml \
  --version 2.4.0
```

**Test Run**:
```bash
python3 release-agent/scripts/release_orchestrator.py \
  --repo rdkcentral/rdkb-wifi \
  --dry-run
```

---

## 📊 Output & Results

The orchestrator generates detailed JSON files in `/tmp/rdkb-release-conflicts/`:

### `conflict_analysis.json`
Contains:
- PR metadata (title, author, files changed)
- **Semantic analysis** (change types, pattern counts)
- Conflict detection (file overlaps, timing, critical files)

### `llm_decisions.json`
Contains:
- Per-PR decisions (INCLUDE/EXCLUDE/MANUAL_REVIEW)
- Confidence levels
- Detailed rationale
- Required dependencies
- Risk/benefit analysis

---

## 🎯 Pattern Intelligence

### Change Type Classification

| Type | Examples | Risk |
|------|----------|------|
| `whitespace_only` | Formatting | 🟢 Very Low |
| `include_reorder` | `#include` reorg | 🟢 Very Low |
| `comment_only` | Docs | 🟢 Very Low |
| `null_check_added` | `if (!ptr)` | 🟡 Low (beneficial) |
| `error_handling` | `CcspTraceError` | 🟡 Low (beneficial) |
| `safety_improvement` | `snprintf` | 🟡 Low (beneficial) |
| `functional` | Logic changes | 🔴 Medium-High |

### Detection Examples

**NULL Check**:
```c
+ if (!ptr) return ANSC_STATUS_FAILURE;
```
→ `null_check_added`, `safety_focused: true`

**Safety Improvement**:
```c
- strcpy(buffer, source);
+ snprintf(buffer, sizeof(buffer), "%s", source);
```
→ `safety_improvement`

---

## 🐛 Troubleshooting

### Common Issues

**`ModuleNotFoundError: No module named 'yaml'`**
```bash
pip install pyyaml
```

**`gh: command not found`**
- Install GitHub CLI: https://cli.github.com

**`LLM API key not set`**
```bash
export OPENAI_API_KEY="sk-..."
```

### Debug

```bash
# View conflict analysis
cat /tmp/rdkb-release-conflicts/conflict_analysis.json | jq '.pr_semantics'

# View LLM decisions
cat /tmp/rdkb-release-conflicts/llm_decisions.json | jq '.'
```

---

## 🧪 Testing

### Compile Check
```bash
cd release-agent/scripts
python3 -m py_compile *.py
```

### Test Pattern Analyzer
```bash
python3 -c "
from code_pattern_analyzer import analyze_pr_diff

diff = '''
+ if (!ptr) {
+     CcspTraceError(\"NULL pointer\");
+     return ANSC_STATUS_FAILURE;
+ }
'''

result = analyze_pr_diff(diff)
print(f'Type: {result.dominant_type.value}')
print(f'Safety: {result.safety_focused}')
print(f'Summary: {result.summary}')
"
```

---

## 🔐 Security Best Practices

### API Key Management

```bash
# Set environment variables (never commit!)
export OPENAI_API_KEY="sk-..."

# For GitHub Actions, use encrypted secrets
```

### Always Test First

```bash
# Always use --dry-run first
python3 release-agent/scripts/release_orchestrator.py \
  --repo your/repo \
  --dry-run
```

---

## 📚 Architecture

### Active Modules (2,280 lines)

| Module | Purpose | Lines |
|--------|---------|-------|
| `release_orchestrator.py` | Main orchestrator | 358 |
| `pr_conflict_analyzer.py` | Phase 1 detection | 368 |
| `llm_pr_decision.py` | Phase 2 LLM | 413 |
| `code_pattern_analyzer.py` | Semantic analysis | 368 |
| `llm_providers.py` | API clients | 366 |
| `pr_level_resolver.py` | Conflict handling | 346 |
| `utils.py` | Utilities | 61 |

### Deprecated Modules

See [scripts/deprecated/README.md](scripts/deprecated/README.md) for:
- Old code-level merging approach
- Why it was replaced
- Migration guide

---

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make changes and test
4. Submit a Pull Request

---

## 📄 License

MIT License

---

## 👥 Authors

**Goutam Das** ([@GoutamD2905](https://github.com/GoutamD2905))

---

## 📞 Support

- **Issues**: https://github.com/GoutamD2905/release-agent/issues
- **Discussions**: https://github.com/GoutamD2905/release-agent/discussions

---

**Last Updated**: February 23, 2026  
**Version**: 2.0.0 (Hybrid Intelligence Architecture)
