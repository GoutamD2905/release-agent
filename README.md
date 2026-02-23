# 🤖 RDK-B Release Agent

**Hybrid AI-powered bi-weekly release automation for RDK-B components** — combining rule-based intelligence with LLM strategic decisions for safe, automated release management.

> **What's New**: Complete Logging & Reporting System (Feb 2026)  
> ✅ Structured logging to console and file  
> ✅ Comprehensive markdown reports for component owners  
> ✅ Auto-discover all PRs merged since last git tag  
> ✅ Automatic dependency detection between PRs  
> ✅ Intelligent warnings for missing/conflicting dependencies  
> ✅ Smart recommendations for include/exclude lists  
> ✅ Complete audit trail and action items

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

### Smart PR Discovery & Validation

**Automatic PR Discovery** - No manual PR lists needed (optional):
- 🏷️ Auto-discovers all PRs merged since the last git tag
- 📊 Compares discovered PRs with your configured include/exclude list
- ⚠️ Warns about missing dependencies
- 💡 Provides intelligent recommendations

**Dependency Intelligence**:
- ✅ Detects when included PRs require other PRs
- ❌ Warns when excluding PRs that others depend on
- 🔗 Identifies dependency chains automatically
- 📋 Shows all PRs found vs configured PRs

**Example Output**:
```
🔍 Smart PR Discovery
────────────────────────────────────────────────────────
Last Tag        : v2.1.0
Commits Since   : 47
PRs Found       : 12

All PRs since v2.1.0:
  [✓] PR #120: Add NULL checks to network handler
  [✓] PR #125: Fix memory leak in config parser
  [ ] PR #130: Refactor logging system
  [✓] PR #135: Security patch for buffer overflow
  ... and 8 more

Strategy        : INCLUDE
Configured      : 3 PRs to INCLUDE

⚠️  Dependency Warnings:
  • PR #135 requires PR #130, but #130 is not included

💡 Smart Recommendations:
  → Consider adding PRs [130] to satisfy dependencies
  → Found 9 additional PRs not in config: [130, 140, ...]
```

### Two-Phase Approach

**PHASE 0: Smart PR Discovery** (NEW!)
- 🏷️ Auto-discover all PRs from git history
- 📋 Validate configured PR lists
- 🔗 Detect dependency requirements

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
  - **Dependency requirements** (NEW!)
- Makes binary decisions: INCLUDE / EXCLUDE / MANUAL_REVIEW
- Identifies which other PRs are required
- Provides confidence level and detailed rationale

**PHASE 2.5: Dependency Validation** (NEW!)
- ✅ Validates all dependencies are satisfied
- ⚠️ Warns about missing or conflicting dependencies
- 💡 Provides smart recommendations

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
│   ├── pr_discovery.py                 # Phase 0: Smart PR discovery & validation (NEW!)
│   ├── pr_conflict_analyzer.py         # Phase 1: Detection + semantics
│   ├── llm_pr_decision.py              # Phase 2: LLM strategic decisions
│   ├── code_pattern_analyzer.py        # C/C++ semantic code analysis
│   ├── logger.py                       # Structured logging mechanism (NEW!)
│   ├── report_generator.py             # Comprehensive report generation (NEW!)
│   ├── llm_providers.py                # LLM API provider functions
│   ├── pr_level_resolver.py            # PR-level conflict resolution
│   └── utils.py                        # Shared utilities
└── config/
    └── .release-config.yml             # Main configuration (all options)
```

---

## 📊 Logging & Reporting

### Comprehensive Logging

Every release operation is **fully logged** with:

- **Console logging**: Real-time progress with color-coded levels
- **File logging**: Detailed session logs with timestamps
- **Structured format**: Easy to parse and analyze
- **Log levels**: DEBUG, INFO, WARNING, ERROR, CRITICAL

**Log files location**: `/tmp/rdkb-release-conflicts/logs/`

**Example log entry**:
```
2026-02-23 14:30:45 | INFO     | analyze              | Phase 1: Analyzing 12 PRs for conflicts
2026-02-23 14:30:48 | INFO     | analyze              | Conflict analysis complete: 3 conflicts detected
2026-02-23 14:30:48 | WARNING  | validate_deps        | PR #135 requires PR #130, but #130 is not included
```

### Comprehensive Release Reports

After each run, a **detailed markdown report** is generated for component owners:

**Report location**: `/tmp/rdkb-release-conflicts/reports/`

**Report includes**:
- ✅ Executive Summary with key metrics
- 🔍 Complete PR discovery details
- ⚠️ Conflict analysis breakdown
- 🤖 All LLM decisions with rationale
- 🔗 Dependency validation results
- 🚀 Execution results (if not dry run)
- 💡 Smart recommendations for next steps
- 📋 Action items for component owner

**Example report structure**:
```markdown
# Release Report: advanced-security v2.2.0

## 📊 Executive Summary
- PRs Discovered: 12
- Conflicts Detected: 3 (2 critical)
- LLM Decisions: 5
- To Include: 3 PRs
- Manual Review: 2 PRs

## 🔍 Smart PR Discovery
Last Tag: v2.1.0
PRs Found: [120, 125, 130, 135, ...]

## 🤖 LLM Strategic Decisions
### ✅ PR #120: INCLUDE (HIGH confidence)
**Rationale**: Adds critical NULL checks...
**Benefits**: Improves safety, low risk...

## 💡 Recommendations
1. Manual Review Required: PRs [135, 140]
2. Add Missing Dependencies: PR [130]
```

### Output Files Generated

Every run creates:

1. **Log file**: `/tmp/rdkb-release-conflicts/logs/{component}_{version}_{timestamp}.log`
2. **Comprehensive Report**: `/tmp/rdkb-release-conflicts/reports/{component}_{version}_report_{timestamp}.md`
3. **Conflict Analysis**: `/tmp/rdkb-release-conflicts/conflict_analysis.json`
4. **LLM Decisions**: `/tmp/rdkb-release-conflicts/llm_decisions.json`
5. **Dependency Validation**: `/tmp/rdkb-release-conflicts/dependency_validation.json`

**Component owners get**:
- Clear, readable markdown report
- Complete audit trail in logs
- All decisions explained with rationale
- Action items for next steps

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

## 🎯 Smart PR Discovery & Dependency Analysis

### How It Works

The agent automatically:

1. **Discovers PRs** - Finds all PRs merged since the last git tag
2. **Compares** - Shows which PRs you configured vs what was found
3. **Analyzes Dependencies** - LLM identifies which PRs require other PRs
4. **Validates** - Warns about missing or conflicting dependencies
5. **Recommends** - Suggests PRs to add or remove

### Example Workflow

**Step 1: Component owner creates config**
```yaml
# Only specify the PRs they want
strategy: "include"
prs:
  - 120  # Critical fix
  - 135  # Security patch
```

**Step 2: Agent auto-discovers and validates**
```
🔍 Smart PR Discovery
────────────────────────────────────────────────────────
Last Tag        : v2.1.0
Commits Since   : 47
PRs Found       : 12

All PRs since v2.1.0:
  [✓] PR #120: Add NULL checks to network handler
  [ ] PR #125: Fix memory leak in config parser
  [ ] PR #130: Refactor logging system
  [✓] PR #135: Security patch for buffer overflow
  ... and 8 more

Strategy        : INCLUDE
Configured      : 2 PRs to INCLUDE
⚠️  9 PRs found but not in config
```

**Step 3: LLM analyzes and detects dependencies**
```
🤖 LLM PR Decision Maker initialized

Analyzing PR #120... ✅ INCLUDE (HIGH)
  Rationale: Safety improvements with NULL checks
  Requires PRs: []

Analyzing PR #135... ✅ INCLUDE (HIGH)
  Rationale: Critical security fix
  Requires PRs: [130]  ⚠️  Dependency detected!
```

**Step 4: Dependency validation warns**
```
⚠️  Dependency Warnings:
  • PR #135 requires PR #130, but #130 is not included
  • PR #130 (Refactor logging) is needed by PR #135

💡 Smart Recommendations:
  → Consider adding PRs [130] to satisfy dependencies
  → Found 9 additional PRs not in config: [125, 130, 140, ...]
  → Review PR #125 (Fix memory leak) - not in release plan
```

**Step 5: Component owner updates config**
```yaml
strategy: "include"
prs:
  - 120  # Critical fix
  - 130  # Required by 135 (dependency)
  - 135  # Security patch
```

### Exclude Strategy Benefits

With EXCLUDE strategy, you don't need to list ALL PRs:

```yaml
strategy: "exclude"
prs:
  - 99   # Known broken PR
  - 150  # Feature not ready
```

Agent will:
- Auto-discover all PRs since last tag
- Include ALL except those listed
- Warn if excluded PR has dependents
- Show full impact of exclusions

---

## 🚀 Release Strategies

### Visual Overview: INCLUDE vs EXCLUDE

Both strategies work with PRs from your `base_branch` (usually `develop`), but use different operations:

```
┌─────────────────────────────┬─────────────────────────────┐
│    INCLUDE Strategy         │    EXCLUDE Strategy         │
├─────────────────────────────┼─────────────────────────────┤
│ Start: Tag 2.1.1 (empty)    │ Start: develop (full)       │
│ Operation: CHERRY-PICK      │ Operation: REVERT           │
│ List: What to ADD           │ List: What to REMOVE        │
│                             │                             │
│ Example Config:             │ Example Config:             │
│   prs: [19, 20, 21]         │   prs: [1, 2]               │
│                             │                             │
│ Process:                    │ Process:                    │
│ 1. Start clean              │ 1. Start with everything    │
│ 2. + Add PR #19             │ 2. - Remove PR #2           │
│ 3. + Add PR #20             │ 3. - Remove PR #1           │
│ 4. + Add PR #21             │ 4. Done                     │
│                             │                             │
│ Result: ONLY 3 PRs          │ Result: ALL except 2 PRs    │
│        (19, 20, 21)         │        (3-21, 39)           │
│                             │                             │
│ Use When:                   │ Use When:                   │
│ • Hotfix release            │ • Regular release           │
│ • Few critical PRs          │ • Most PRs ready            │
│ • Minimal changes           │ • Few PRs problematic       │
└─────────────────────────────┴─────────────────────────────┘
```

---

### INCLUDE Strategy (Cherry-Pick) - Detailed Flow

**Starting Point**: Last git tag (clean baseline from previous release)

```
Git Timeline:
════════════════════════════════════════════════════════════

Tag 2.1.1 (Released 3 months ago)
   │  📦 Clean baseline - production code
   │
   ├─ [develop branch continues...]
   │
   ├─ PR #1 ✅ merged → develop
   ├─ PR #2 ✅ merged → develop  
   ├─ PR #3 ✅ merged → develop
   ├─ ...
   ├─ PR #19 ✅ merged → develop  ← We want this
   ├─ PR #20 ✅ merged → develop  ← We want this
   ├─ PR #21 ✅ merged → develop  ← We want this
   ├─ PR #39 ✅ merged → develop
   │
   ▼
Develop (HEAD) - Contains ALL 21 PRs
```

**Configuration**:
```yaml
strategy: "include"
base_branch: "develop"
prs: [19, 20, 21]
```

**Process**:
```
Step 1: Create release/2.2.0 from Tag 2.1.1
   ┌──────────────────────────────────────┐
   │ release/2.2.0                        │
   │ Starting point: Tag 2.1.1            │
   │ Content: Clean baseline (NO PRs)     │
   └──────────────────────────────────────┘

Step 2: Cherry-pick PR #19 from develop
   ┌──────────────────────────────────────┐
   │ release/2.2.0                        │
   │ Tag 2.1.1 + PR #19                   │
   └──────────────────────────────────────┘

Step 3: Cherry-pick PR #20 from develop
   ┌──────────────────────────────────────┐
   │ release/2.2.0                        │
   │ Tag 2.1.1 + PR #19 + PR #20          │
   │ ⚠️  Conflict! (both change same lines)│
   │ 🤖 LLM resolves conflict             │
   └──────────────────────────────────────┘

Step 4: Cherry-pick PR #21 from develop
   ┌──────────────────────────────────────┐
   │ release/2.2.0 ✅ FINAL               │
   │ Tag 2.1.1 + PR #19 + PR #20 + PR #21 │
   │ ⚠️  Conflict! (same lines again)     │
   │ 🤖 LLM resolves conflict             │
   └──────────────────────────────────────┘

Result:
   Release 2.2.0 = Old baseline + ONLY 3 PRs
   Missing: PRs #1-18, #39 (intentionally excluded)
```

**Use INCLUDE when**:
- 🔥 **Hotfix release** - Only critical fixes needed
- 🎯 **Targeted release** - Specific features/fixes
- 🔒 **Tight control** - Minimal surface area for issues
- ⚡ **Quick release** - Few well-tested PRs

---

### EXCLUDE Strategy (Revert) - Detailed Flow

**Starting Point**: `base_branch` (develop) with all PRs

```
Git Timeline:
════════════════════════════════════════════════════════════

Tag 2.1.1
   │
   ├─ PR #1 ✅ merged → develop  ← We DON'T want
   ├─ PR #2 ✅ merged → develop  ← We DON'T want
   ├─ PR #3 ✅ merged → develop
   ├─ PR #4 ✅ merged → develop
   ├─ ...
   ├─ PR #19 ✅ merged → develop
   ├─ PR #20 ✅ merged → develop
   ├─ PR #21 ✅ merged → develop
   │
   ▼
Develop (HEAD) - Contains ALL 21 PRs
```

**Configuration**:
```yaml
strategy: "exclude"
base_branch: "develop"
prs: [1, 2]
```

**Process**:
```
Step 1: Create release/2.2.0 from develop
   ┌──────────────────────────────────────┐
   │ release/2.2.0                        │
   │ Starting point: develop              │
   │ Content: ALL 21 PRs                  │
   └──────────────────────────────────────┘

Step 2: Revert PR #2 (newest first)
   ┌──────────────────────────────────────┐
   │ release/2.2.0                        │
   │ develop - PR #2                      │
   │ (Removes ONLY PR #2's changes)       │
   └──────────────────────────────────────┘

Step 3: Revert PR #1
   ┌──────────────────────────────────────┐
   │ release/2.2.0 ✅ FINAL               │
   │ develop - PR #2 - PR #1              │
   │ (Removes PR #1's changes)            │
   └──────────────────────────────────────┘

Result:
   Release 2.2.0 = develop - 2 PRs
   Contains: PRs #3-21, #39 (19 PRs total)
   Missing: PRs #1, #2 (intentionally excluded)
```

**Use EXCLUDE when**:
- 📦 **Regular release** - Most changes are ready
- 🚫 **Few problematic PRs** - Only 1-2 PRs causing issues
- ✅ **Comprehensive release** - Want most of develop
- 🔄 **Bi-weekly cadence** - Standard release cycle

---

### Key Differences

| Aspect | INCLUDE | EXCLUDE |
|--------|---------|---------|
| **Starting Point** | Last tag (empty) | base_branch (full) |
| **Git Operation** | `cherry-pick` | `revert` |
| **PR List Meaning** | What to ADD | What to REMOVE |
| **Result Size** | Usually smaller | Usually larger |
| **Best For** | Hotfixes, targeted releases | Regular releases |
| **Risk Level** | Lower (fewer PRs) | Higher (more PRs) |
| **Testing Scope** | Minimal | Comprehensive |

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

**Empty Commit During Cherry-Pick**

When using INCLUDE strategy with a release branch from `develop`, you may see:
```
⚠️  Operation failed: The previous cherry-pick is now empty
```

**Why this happens**: The PR changes are already present in the target branch (inherited from develop).

**Solution**: The agent auto-detects this and treats it as SUCCESS:
```
ℹ️  Changes already present in target branch (empty commit)
✅ Skipping cherry-pick (PR changes already applied)
```

This is **NOT a failure** - the changes are already there!

**Manual handling** (if needed):
```bash
# Skip the empty commit
git cherry-pick --skip

# Or commit anyway
git commit --allow-empty
```

### Debug

```bash
# View conflict analysis
cat /tmp/rdkb-release-conflicts/conflict_analysis.json | jq '.pr_semantics'

# View LLM decisions cat /tmp/rdkb-release-conflicts/llm_decisions.json | jq '.'

# Check logs for detailed execution trace
tail -f /tmp/rdkb-release-conflicts/logs/*.log
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

### Implementation Philosophy

This release agent uses a **Hybrid Intelligence Approach** combining:
- ✅ Rule-based semantic analysis (fast, deterministic)
- ✅ LLM strategic intelligence (contextual, intelligent)
- ✅ Post-resolution C syntax validation
- ✅ Safety-first conflict resolution

### Three-Tier Conflict Resolution

```
┌──────────────────────────────────────────┐
│ HIGH Confidence (Rules)                  │
│  → AUTO-RESOLVE (instant)                │
│  → No LLM call needed                    │
│  Examples: Whitespace, comments, braces  │
├──────────────────────────────────────────┤
│ MEDIUM Confidence (Hybrid)               │
│  → LLM with safety guidance              │
│  → Prefer safer side when detected       │
│  Examples: NULL checks, error handling   │
├──────────────────────────────────────────┤
│ LOW Confidence (Full LLM)                │
│  → Complete context to LLM               │
│  → Strategic decision making             │
│  Examples: Functional changes, complex   │
└──────────────────────────────────────────┘
```

### Change Classification

Sophisticated conflict classification enables intelligent resolution:

| Conflict Type | Confidence | Resolution Strategy |
|---------------|-----------|-------------------|
| **WHITESPACE_ONLY** | HIGH | Keep OURS (formatting) |
| **INCLUDE_REORDER** | HIGH | Merge and deduplicate |
| **COMMENT_ONLY** | HIGH | Merge both comments |
| **BRACE_STYLE** | HIGH | Keep OURS (style consistency) |
| **NULL_CHECK_ADDED** | MEDIUM | Prefer safety improvements |
| **ERROR_HANDLING** | MEDIUM | Prefer error handling side |
| **FUNCTIONAL** | LOW | Full LLM analysis |
| **MIXED** | LOW | Full LLM analysis |

**Performance**: HIGH confidence conflicts resolve instantly without LLM calls.

### Safety Improvement Detection

Automatically detects and preserves safety improvements:
- NULL pointer checks (`if (ptr == NULL)`)
- Resource cleanup (`free()`, `close()`)
- Error handling (`return ANSC_STATUS_FAILURE`)
- Bounds checking
- RDK-B specific patterns (`CcspTraceError`)

### Post-Resolution Validation

After LLM conflict resolution, C syntax validation ensures correctness:
```bash
gcc -fsyntax-only -x c <file>
```

This catches syntax errors introduced by LLM and prevents broken code from being committed.

### Active Modules (2,280 lines)

| Module | Purpose | Lines |
|--------|---------|-------|
| `release_orchestrator.py` | Main orchestrator | 570 |
| `pr_discovery.py` | Smart PR discovery & validation | 350 |
| `pr_conflict_analyzer.py` | Phase 1 detection | 368 |
| `llm_pr_decision.py` | Phase 2 LLM decisions | 413 |
| `code_pattern_analyzer.py` | Semantic C analysis | 368 |
| `pr_level_resolver.py` | PR-level conflict resolution | 346 |
| `llm_conflict_resolver.py` | Hybrid conflict resolver | 285 |
| `llm_providers.py` | Multi-provider LLM API | 366 |
| `report_generator.py` | Comprehensive reports | 240 |
| `logger.py` | Structured logging | 120 |
| `utils.py` | Shared utilities | 61 |

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
