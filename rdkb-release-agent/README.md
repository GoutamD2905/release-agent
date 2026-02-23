# 🤖 RDK-B Release Agent

**AI-powered bi-weekly release automation for RDK-B components** — with intelligent conflict resolution that understands C code semantics.

Any RDK-B component can adopt this framework with a **single command** and get automated release branch creation, cherry-pick/revert operations, smart merge conflict resolution, and detailed release reports.

---

## ⚡ Quick Start (3 Steps)

### Step 1: Adopt the framework

```bash
# From inside your component repo:
git clone https://github.com/GoutamD2905/rdkb-release-agent.git /tmp/release-agent
bash /tmp/release-agent/adopt.sh \
  --component-repo <org/repo> \
  --version <X.Y.Z>
```

This generates two files:
- `.release-config.yml` — release configuration (edit PRs list each cycle)
- `.github/workflows/rdkb-biweekly-release.yml` — GitHub Actions workflow

### Step 2: Edit your config

```yaml
# .release-config.yml
version: "2.4.0"
strategy: "exclude"
prs:
  - 205   # WIP — skip this PR
  - 310   # Experimental — not ready
```

### Step 3: Run the release

**Via GitHub Actions:**
Go to Actions → "RDK-B Bi-Weekly Release Agent" → Run workflow

**Or locally:**
```bash
python3 /tmp/release-agent/scripts/orchestrate_release.py \
  --repo <org/repo> --version 2.4.0 --dry-run
```

---

## 🧠 Smart Conflict Resolution

The heart of this agent — when cherry-pick or revert hits conflicts in C source files, the **semantic-aware merge engine** analyzes each conflict hunk and resolves automatically:

| Confidence | Change Type | Resolution |
|------------|-------------|------------|
| 🟢 HIGH | Whitespace/formatting | Keep either side (semantically identical) |
| 🟢 HIGH | `#include` reorder | Merge and deduplicate both sets |
| 🟢 HIGH | Comment-only changes | Keep more descriptive version |
| 🟡 MEDIUM | NULL check / error handling added | Prefer the safety improvement |
| 🟡 MEDIUM | Brace style differences | Keep project convention |
| 🔴 LOW | Functional changes | Fallback to ours/theirs (flagged for review) |

### How it works

```
git cherry-pick fails with conflicts
        │
        ▼
  resolve_conflicts.py --smart
        │
        ├─ DU/UD/AA/DD → standard ours/theirs strategy
        │
        └─ UU (modify/modify) on .c/.h files
                │
                ▼
          smart_merge.py analyzes each hunk
                │
                ├─ classify_hunk_change() → determines change type
                ├─ resolve_hunk() → picks best resolution + confidence
                └─ writes JSON report for audit trail
```

### Configuration

```yaml
# .release-config.yml
conflict_resolution:
  smart_merge: true          # Enable semantic C-aware merge
  min_confidence: "low"      # "high", "medium", or "low"
  safety_prefer: true        # Prefer NULL checks, error handling

# Optional: Enable AI resolution for functional conflicts
llm:
  enabled: true
  provider: "githubcopilot"               # Supported: "githubcopilot", "openai", "gemini"
  model: "gpt-5.2"                        # Copilot model version
  api_key_env: "GITHUB_COPILOT_API_TOKEN" # Must match your GitHub Actions secret name!
```

---

## 📁 Repository Structure

```
rdkb-release-agent/
├── adopt.sh                       # Single-command adoption script
├── README.md
├── scripts/
│   ├── orchestrate_release.py     # Main orchestrator
│   ├── resolve_conflicts.py       # Conflict resolver (--smart flag)
│   ├── smart_merge.py             # Semantic C-aware merge engine
│   ├── analyze_dependencies.py    # PR dependency analyzer
│   ├── generate_report.py         # Release report generator
│   ├── create_release_branch.sh   # Branch creation
│   ├── safe_cherry_pick.sh        # Safe cherry-pick + auto-resolve
│   ├── safe_revert.sh             # Safe revert + auto-resolve
│   └── trigger_release.sh         # Local trigger wrapper
├── config/
│   └── release-config-schema.yml  # Full config reference
├── examples/
│   ├── exclude-mode.release-config.yml
│   └── include-mode.release-config.yml
├── tests/
│   └── test_smart_merge.py        # 18 unit tests
└── .github/workflows/
    └── release-agent.yml          # CI for the agent itself
```

---

## 🔧 Configuration Reference

| Field | Required | Default | Description |
|-------|----------|---------|-------------|
| `version` | ✅ | — | Release version (semver) |
| `strategy` | ✅ | — | `"exclude"` or `"include"` |
| `prs` | ✅* | `[]` | PR numbers to exclude/include |
| `component_name` | — | repo name | Display name in reports |
| `base_branch` | — | `"develop"` | Integration branch |
| `dry_run` | — | `false` | Simulate without pushing |
| `conflict_policy` | — | `"pause"` | `"pause"` or `"skip"` |
| `conflict_resolution.smart_merge` | — | `true` | Enable smart merge |
| `conflict_resolution.min_confidence` | — | `"low"` | Minimum confidence level |
| `conflict_resolution.safety_prefer` | — | `true` | Prefer safety improvements |
| `auto_resolve_deps` | — | `false` | Auto-include dependency PRs |
| `notify` | — | `[]` | GitHub handles to @mention |

*Required for `include` strategy. Optional for `exclude` (empty = take all PRs).

---

## 📊 Release Reports

The agent generates a comprehensive Markdown release report including:

- ✅ PRs included in the release
- ⚠️ Conflicts detected (with resolution details)
- 🔗 Dependency analysis
- 🧠 Smart conflict resolution summary (per-hunk confidence breakdown)
- 📋 Next steps for component owner

Reports are automatically posted to the GitHub Actions summary.

---

## 🚀 Strategies

### Exclude Mode (default)
Start from `develop`, take ALL merged PRs **except** the ones you list:

```yaml
strategy: "exclude"
prs:
  - 205   # Not ready
  - 310   # Experimental
```

### Include Mode
Start from `main`, cherry-pick **only** the PRs you list:

```yaml
strategy: "include"
prs:
  - 100   # Bug fix
  - 150   # Security patch
```

---

## 🛠️ adopt.sh Options

```
Usage:
  ./adopt.sh --component-repo <org/repo> --version <X.Y.Z> [options]

Required:
  --component-repo   GitHub org/repo (e.g. rdkcentral/rdkb-wifi)
  --version          Release version (e.g. 2.4.0)

Optional:
  --agent-repo       Agent repo (default: GoutamD2905/rdkb-release-agent)
  --strategy         exclude or include (default: exclude)
  --base-branch      Integration branch (default: develop)
  --output-dir       Where to write files (default: current dir)
  --dry-run          Show what would be created without writing
```

---

## 📝 License

Apache 2.0

---

## 🤝 Contributing

1. Fork this repo
2. Create a feature branch
3. Run tests: `python3 tests/test_smart_merge.py`
4. Submit a PR
