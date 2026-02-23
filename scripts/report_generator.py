#!/usr/bin/env python3
"""
report_generator.py
===================
Comprehensive report generation for RDK-B release operations.

Generates detailed markdown reports for component owners including:
  - PR discovery summary
  - Conflict analysis
  - LLM decisions and rationale
  - Dependency validation
  - Recommended actions
  - Complete audit trail
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from dataclasses import dataclass


@dataclass
class ReleaseReport:
    """Complete release operation report."""
    # Configuration
    component_name: str
    version: str
    strategy: str
    base_branch: str
    release_branch: str
    
    # Discovery
    last_tag: Optional[str]
    total_prs_discovered: int
    prs_configured: List[int]
    
    # Analysis
    conflicts_detected: int
    conflicts_critical: int
    conflicts_medium: int
    conflicts_low: int
    
    # Decisions
    llm_decisions: Dict[int, Dict]
    prs_to_include: List[int]
    prs_to_exclude: List[int]
    prs_manual_review: List[int]
    
    # Dependency validation
    dependency_warnings: List[str]
    dependency_recommendations: List[str]
    missing_dependencies: Dict[int, List[int]]
    
    # Execution results
    successful_prs: List[int]
    failed_prs: List[int]
    skipped_prs: List[int]
    
    # Metadata
    execution_time: float
    dry_run: bool
    timestamp: str


class ReportGenerator:
    """Generate comprehensive release reports."""
    
    def __init__(self, 
                 output_dir: Path = Path("/tmp/rdkb-release-conflicts/reports"),
                 data_dir: Path = Path("/tmp/rdkb-release-conflicts")):
        """
        Initialize report generator.
        
        Args:
            output_dir: Directory to save reports
            data_dir: Directory containing analysis data
        """
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir = Path(data_dir)
    
    def generate_report(self, report_data: ReleaseReport) -> Path:
        """
        Generate comprehensive markdown report.
        
        Args:
            report_data: Report data
            
        Returns:
            Path to generated report file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.output_dir / f"{report_data.component_name}_{report_data.version}_report_{timestamp}.md"
        
        with open(report_file, 'w', encoding='utf-8') as f:
            self._write_header(f, report_data)
            self._write_executive_summary(f, report_data)
            self._write_pr_discovery(f, report_data)
            self._write_conflict_analysis(f, report_data)
            self._write_llm_decisions(f, report_data)
            self._write_dependency_validation(f, report_data)
            self._write_execution_results(f, report_data)
            self._write_recommendations(f, report_data)
            self._write_next_steps(f, report_data)
            self._write_footer(f, report_data)
        
        return report_file
    
    def _write_header(self, f, data: ReleaseReport) -> None:
        """Write report header."""
        f.write(f"# 🚀 Release Report — {data.component_name} v{data.version}\n\n")
        
        # Summary table
        f.write("| Field | Value |\n")
        f.write("|-------|-------|\n")
        f.write(f"| **Release Version** | `{data.version}` |\n")
        f.write(f"| **Component** | `{data.component_name}` |\n")
        f.write(f"| **Strategy** | `{data.strategy.upper()}` |\n")
        f.write(f"| **Base Branch** | `{data.base_branch}` |\n")
        f.write(f"| **Release Branch** | `{data.release_branch}` |\n")
        f.write(f"| **Mode** | {'🔍 DRY RUN (Simulation)' if data.dry_run else '✅ LIVE EXECUTION'} |\n")
        f.write(f"| **Execution Time** | {data.execution_time:.1f}s |\n")
        f.write(f"| **Report Generated** | {data.timestamp} |\n")
        f.write("\n---\n\n")
    
    def _write_executive_summary(self, f, data: ReleaseReport) -> None:
        """Write executive summary."""
        f.write("## 📊 Executive Summary\n\n")
        
        # Results table
        f.write("| Metric | Count | Status |\n")
        f.write("|--------|-------|--------|\n")
        f.write(f"| **PRs Discovered** | {data.total_prs_discovered} | 🔍 From git history |\n")
        f.write(f"| **PRs Configured** | {len(data.prs_configured)} | 📋 In config file |\n")
        f.write(f"| **Conflicts Detected** | {data.conflicts_detected} | ⚠️ {data.conflicts_critical} critical |\n")
        
        if not data.dry_run:
            success_icon = "✅" if len(data.failed_prs) == 0 else "⚠️"
            f.write(f"| **PRs Applied** | {len(data.successful_prs)} | {success_icon} Successfully applied |\n")
            if len(data.failed_prs) > 0:
                f.write(f"| **PRs Failed** | {len(data.failed_prs)} | ❌ Requires manual fix |\n")
            if len(data.skipped_prs) > 0:
                f.write(f"| **PRs Skipped** | {len(data.skipped_prs)} | ⏭️ Excluded by LLM |\n")
        
        if len(data.llm_decisions) > 0:
            f.write(f"| **LLM Decisions** | {len(data.llm_decisions)} | 🤖 AI-powered analysis |\n")
            f.write(f"| **LLM Include** | {len(data.prs_to_include)} | ✅ Recommended |\n")
            f.write(f"| **LLM Exclude** | {len(data.prs_to_exclude)} | ⏭️ Not recommended |\n")
            f.write(f"| **Manual Review** | {len(data.prs_manual_review)} | 🔍 Needs human review |\n")
        
        f.write("\n---\n\n")
    
    def _write_pr_discovery(self, f, data: ReleaseReport) -> None:
        """Write PR discovery section."""
        f.write("## 🔍 Smart PR Discovery\n\n")
        
        if data.last_tag:
            f.write(f"**Last Tag**: `{data.last_tag}`\n\n")
            f.write(f"**PRs Merged Since Tag**: {data.total_prs_discovered}\n\n")
        else:
            f.write("**Note**: No git tags found - using configured PR list only\n\n")
        
        if data.strategy == "include":
            f.write(f"### PRs Configured for Inclusion ({len(data.prs_configured)})\n\n")
            if data.prs_configured:
                for pr in data.prs_configured:
                    f.write(f"- PR #{pr}\n")
                f.write("\n")
            else:
                f.write("*No PRs configured*\n\n")
            
            unconfigured = data.total_prs_discovered - len(data.prs_configured)
            if unconfigured > 0:
                f.write(f"⚠️ **{unconfigured} PR(s) found but not configured**\n\n")
        else:
            f.write(f"### PRs Configured for Exclusion ({len(data.prs_configured)})\n\n")
            if data.prs_configured:
                for pr in data.prs_configured:
                    f.write(f"- PR #{pr}\n")
                f.write("\n")
            
            to_include = data.total_prs_discovered - len(data.prs_configured)
            f.write(f"**Will Include**: {to_include} PR(s)\n\n")
        
        f.write("---\n\n")
    
    def _write_conflict_analysis(self, f, data: ReleaseReport) -> None:
        """Write conflict analysis section with detailed information."""
        f.write("## ⚠️ Conflict Analysis\n\n")
        
        # Load detailed conflict data from runtime
        detailed_conflicts_file = self.data_dir / "detailed_conflicts.json"
        has_detailed_conflicts = detailed_conflicts_file.exists()
        
        if has_detailed_conflicts:
            with open(detailed_conflicts_file) as cf:
                detailed_conflicts = json.load(cf)
            
            if detailed_conflicts:
                total_conflicts = len(detailed_conflicts)
                total_files = sum(len(c['files']) for c in detailed_conflicts)
                
                f.write(f"**Total Conflicts Encountered**: {total_conflicts} PR(s) with conflicts\n")
                f.write(f"**Total Files Affected**: {total_files}\n\n")
                
                # Detailed conflicts table
                f.write("### 🔴 Detailed Conflict Information\n\n")
                f.write("| PR # | Operation | Files | Conflict Details |\n")
                f.write("|------|-----------|-------|------------------|\n")
                
                for conflict_info in detailed_conflicts:
                    pr_num = conflict_info['pr_number']
                    operation = conflict_info['operation']
                    files = conflict_info['files']
                    detailed = conflict_info.get('detailed_conflicts', [])
                    
                    # Build detailed info string
                    details_parts = []
                    for file_info in detailed[:3]:  # Show first 3 files
                        file_name = file_info['file']
                        conflicts_count = file_info['total_conflicts']
                        # Get line ranges
                        line_ranges = []
                        for conf in file_info['conflicts'][:2]:  # First 2 conflicts per file
                            line_ranges.append(f"L{conf['start_line']}-{conf['end_line']}")
                        if file_info['conflicts']:
                            range_str = ", ".join(line_ranges)
                            if file_info['total_conflicts'] > 2:
                                range_str += f" +{file_info['total_conflicts']-2} more"
                            details_parts.append(f"`{file_name}` ({range_str})")
                    
                    if len(detailed) > 3:
                        details_parts.append(f"...+{len(detailed)-3} more files")
                    
                    files_str = f"{len(files)} file(s)"
                    details_str = "<br>".join(details_parts) if details_parts else "No line details"
                    
                    f.write(f"| #{pr_num} | {operation} | {files_str} | {details_str} |\n")
                
                f.write("\n")
                
                # Expandable detailed view
                f.write("<details>\n")
                f.write("<summary><b>📋 Click to view full conflict details</b></summary>\n\n")
                
                for conflict_info in detailed_conflicts:
                    pr_num = conflict_info['pr_number']
                    f.write(f"\n#### PR #{pr_num} Conflicts\n\n")
                    
                    for file_info in conflict_info.get('detailed_conflicts', []):
                        f.write(f"**File**: `{file_info['file']}`\n\n")
                        f.write(f"- **Total Conflicts**: {file_info['total_conflicts']}\n")
                        
                        for i, conf in enumerate(file_info['conflicts'], 1):
                            f.write(f"\n**Conflict {i}**: Lines {conf['start_line']}-{conf['end_line']}\n")
                            f.write(f"- **Our Branch**: {conf.get('our_branch', 'N/A')}\n")
                            f.write(f"- **Their Branch**: {conf.get('their_branch', 'N/A')}\n")
                            
                            if conf.get('our_content'):
                                f.write(f"- **Our Changes**: {len(conf['our_content'])} line(s)\n")
                            if conf.get('their_content'):
                                f.write(f"- **Their Changes**: {len(conf['their_content'])} line(s)\n")
                        
                        f.write("\n")
                
                f.write("</details>\n\n")
            else:
                f.write("> ✅ **No conflicts encountered during execution**. All PRs applied cleanly.\n\n")
        else:
            # Fallback to simple format
            f.write(f"**Total Conflicts Detected**: {data.conflicts_detected}\n\n")
            
            if data.conflicts_detected == 0:
                f.write("> ✅ **No conflicts detected**. All PRs can be applied cleanly.\n\n")
        
        f.write("---\n\n")

    def _write_llm_decisions(self, f, data: ReleaseReport) -> None:
        """Write LLM decisions section."""
        f.write("## 🤖 LLM Strategic Decisions\n\n")
        
        if not data.llm_decisions:
            f.write("> *LLM is only used for conflict resolution. No strategic decisions were made.*\n\n")
            f.write("---\n\n")
            return
        
        f.write(f"**Total Decisions**: {len(data.llm_decisions)}\n\n")
        
        # Group by decision
        include = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'INCLUDE']
        exclude = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'EXCLUDE']
        manual = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'MANUAL_REVIEW']
        
        # Summary table
        f.write("### Decision Summary\n\n")
        f.write("| Decision | Count | Confidence | Action |\n")
        f.write("|----------|:-----:|:----------:|--------|\n")
        f.write(f"| ✅ **INCLUDE** | {len(include)} | AI-Recommended | Auto-applied to release |\n")
        f.write(f"| ⏭️ **EXCLUDE** | {len(exclude)} | AI-Recommended | Skipped from release |\n")
        f.write(f"| 🔍 **MANUAL_REVIEW** | {len(manual)} | Needs Human | Component owner must review |\n")
        f.write("\n")
        
        # Detailed decisions table
        if len(data.llm_decisions) > 0:
            f.write("### Detailed Decisions\n\n")
            f.write("| PR # | Decision | Confidence | Rationale |\n")
            f.write("|------|----------|:----------:|----------|\n")
            
            for pr_num, decision in sorted(data.llm_decisions.items(), key=lambda x: int(x[0])):
                emoji = "✅" if decision['decision'] == "INCLUDE" else "⏭️" if decision['decision'] == "EXCLUDE" else "🔍"
                conf_emoji = "🟢" if decision['confidence'] == "HIGH" else "🟡" if decision['confidence'] == "MEDIUM" else "🔴"
                rationale = decision['rationale'].replace('|', '\\|')[:80]
                f.write(f"| #{pr_num} | {emoji} {decision['decision']} | {conf_emoji} {decision['confidence']} | {rationale} |\n")
            f.write("\n")
        
        # High-value decisions expansion
        if include or manual:
            f.write("<details>\n")
            f.write("<summary>📋 Detailed Analysis (expand for full rationale)</summary>\n\n")
            
            for pr_num, decision in sorted(data.llm_decisions.items(), key=lambda x: int(x[0])):
                if decision['decision'] in ['INCLUDE', 'MANUAL_REVIEW']:
                    emoji = "✅" if decision['decision'] == "INCLUDE" else "🔍"
                    f.write(f"#### {emoji} PR #{pr_num}: {decision['decision']} ({decision['confidence']})\n\n")
                    f.write(f"**Rationale**: {decision['rationale']}\n\n")
                    
                    if decision.get('requires_prs'):
                        f.write(f"**Requires PRs**: {decision['requires_prs']}\n\n")
                    
                    if decision.get('benefits'):
                        f.write("**Benefits**:\n")
                        for benefit in decision['benefits']:
                            f.write(f"- {benefit}\n")
                        f.write("\n")
                    
                    if decision.get('risks'):
                        f.write("**Risks**:\n")
                        for risk in decision['risks']:
                            f.write(f"- {risk}\n")
                        f.write("\n")
            
            f.write("</details>\n\n")
        
        f.write("---\n\n")
    
    def _write_dependency_validation(self, f, data: ReleaseReport) -> None:
        """Write dependency validation section."""
        f.write("## 🔗 Dependency Validation\n\n")
        
        if not data.dependency_warnings and not data.missing_dependencies:
            f.write("> ✅ **No dependency issues detected**. All PRs are properly ordered.\n\n")
            f.write("---\n\n")
            return
        
        if data.missing_dependencies:
            f.write("### Missing Dependencies\n\n")
            f.write("| Included PR | Depends On PR(s) | Action Required |\n")
            f.write("|-------------|------------------|------------------|\n")
            for pr, deps in data.missing_dependencies.items():
                deps_str = ', '.join([f"#{d}" for d in deps])
                f.write(f"| PR #{pr} | {deps_str} | ⚠️ Add dependencies to config |\n")
            f.write("\n")
        
        if data.dependency_warnings:
            f.write("### ⚠️ Warnings\n\n")
            for warning in data.dependency_warnings:
                f.write(f"- {warning}\n")
            f.write("\n")
        
        if data.dependency_recommendations:
            f.write("### 💡 Recommendations\n\n")
            for rec in data.dependency_recommendations:
                f.write(f"- {rec}\n")
            f.write("\n")
        
        f.write("---\n\n")
    
    def _write_execution_results(self, f, data: ReleaseReport) -> None:
        """Write execution results section."""
        if data.dry_run:
            f.write("## 🧪 Dry Run - No Actual Changes Made\n\n")
            f.write("This was a simulation run. No git operations were performed.\n\n")
            f.write("---\n\n")
            return
        
        f.write("## 🚀 Execution Results\n\n")
        
        # Load PR metadata from conflict analysis file
        pr_metadata = {}
        conflict_file = self.data_dir / "conflict_analysis.json"
        if conflict_file.exists():
            with open(conflict_file) as cf:
                conflict_data = json.load(cf)
                pr_metadata = conflict_data.get("pr_metadata", {})
        
        if data.successful_prs:
            f.write(f"### ✅ Successfully Applied ({len(data.successful_prs)} PRs)\n\n")
            f.write("| PR # | Title | Author | Status |\n")
            f.write("|------|-------|--------|--------|\n")
            for pr_num in data.successful_prs:
                pr_info = pr_metadata.get(str(pr_num), {})
                title = pr_info.get('title', f'PR #{pr_num}').replace('|', '\\|')[:60]
                author = pr_info.get('author', 'unknown')
                f.write(f"| #{pr_num} | {title} | @{author} | ✅ Applied |\n")
            f.write("\n")
        
        if data.failed_prs:
            f.write(f"### ❌ Failed / Needs Manual Review ({len(data.failed_prs)} PRs)\n\n")
            f.write("| PR # | Title | Author | Action Required |\n")
            f.write("|------|-------|--------|------------------|\n")
            for pr_num in data.failed_prs:
                pr_info = pr_metadata.get(str(pr_num), {})
                title = pr_info.get('title', f'PR #{pr_num}').replace('|', '\\|')[:60]
                author = pr_info.get('author', 'unknown')
                f.write(f"| #{pr_num} | {title} | @{author} | 🔧 Manual resolution required |\n")
            f.write("\n")
        
        if data.skipped_prs:
            f.write(f"### ⏭️ Skipped ({len(data.skipped_prs)} PRs)\n\n")
            f.write("| PR # | Title | Author | Reason |\n")
            f.write("|------|-------|--------|--------|\n")
            for pr_num in data.skipped_prs:
                pr_info = pr_metadata.get(str(pr_num), {})
                title = pr_info.get('title', f'PR #{pr_num}').replace('|', '\\|')[:60]
                author = pr_info.get('author', 'unknown')
                # Find LLM decision reason
                reason = "Excluded by strategy"
                if data.llm_decisions and str(pr_num) in data.llm_decisions:
                    decision = data.llm_decisions[str(pr_num)]
                    reason = decision.get('rationale', 'Excluded')[:50]
                f.write(f"| #{pr_num} | {title} | @{author} | {reason} |\n")
            f.write("\n")
        
        f.write("---\n\n")
    
    def _write_recommendations(self, f, data: ReleaseReport) -> None:
        """Write recommendations section."""
        f.write("## 💡 Recommendations for Component Owner\n\n")
        
        recommendations = []
        
        # Check for manual review PRs
        if data.prs_manual_review:
            recommendations.append(
                f"**Manual Review Required**: {len(data.prs_manual_review)} PR(s) need human review: {data.prs_manual_review}"
            )
        
        # Check for failed PRs
        if data.failed_prs:
            recommendations.append(
                f"**Failed PRs**: Review and manually apply PRs {data.failed_prs}"
            )
        
        # Check for dependency issues
        if data.missing_dependencies:
            prs_to_add = set()
            for deps in data.missing_dependencies.values():
                prs_to_add.update(deps)
            recommendations.append(
                f"**Add Missing Dependencies**: Consider adding PRs {sorted(prs_to_add)} to satisfy dependencies"
            )
        
        # Check for unconfigured PRs (include mode)
        if data.strategy == "include" and data.total_prs_discovered > len(data.prs_configured):
            unconfigured = data.total_prs_discovered - len(data.prs_configured)
            recommendations.append(
                f"**Unconfigured PRs**: {unconfigured} PR(s) found but not in config - review if any should be included"
            )
        
        if recommendations:
            for i, rec in enumerate(recommendations, 1):
                f.write(f"{i}. {rec}\n\n")
        else:
            f.write("✅ **No specific recommendations** - Release plan looks good!\n\n")
        
        f.write("---\n\n")
    
    def _write_next_steps(self, f, data: ReleaseReport) -> None:
        """Write next steps section."""
        f.write("## 📋 Next Steps for Component Owner\n\n")
        
        if data.dry_run:
            f.write("### After Reviewing This Report:\n\n")
            f.write("1. **Review** all LLM decisions and dependency warnings\n")
            f.write("2. **Update** configuration if needed (add/remove PRs)\n")
            f.write("3. **Run** again without `--dry-run` to execute the release\n\n")
            f.write("```bash\n")
            f.write("# Execute the release\n")
            f.write(f"python3 release_orchestrator.py --repo YOUR_REPO --config .release-config.yml\n")
            f.write("```\n\n")
        else:
            if data.failed_prs:
                f.write("### ⚠️ Manual Intervention Required\n\n")
                f.write("1. **Resolve** failed PRs and conflicts manually\n")
                f.write("2. **Cherry-pick/revert** PRs as needed\n")
                f.write("3. **Test** the release branch thoroughly\n")
                f.write("4. **Create** pull request to merge release branch\n\n")
                f.write("```bash\n")
                f.write("# Checkout release branch\n")
                f.write(f"git checkout {data.release_branch}\n\n")
                f.write("# Manually resolve failed PRs\n")
                f.write("# Then continue with testing and merge\n")
                f.write("```\n\n")
            else:
                f.write("### ✅ Release Branch Ready\n\n")
                f.write(f"1. **Review** the PR list above and verify all expected changes are included\n")
                f.write(f"2. **Test** the `{data.release_branch}` branch on your target platform\n")
                f.write(f"3. **Merge** `{data.release_branch}` → `main` and tag as `{data.version}`:\n\n")
                f.write("```bash\n")
                f.write("# Merge release to main\n")
                f.write(f"git checkout main && git merge --no-ff {data.release_branch}\n\n")
                f.write(f"# Tag the release\n")
                f.write(f"git tag -a {data.version} -m 'Release {data.version}'\n\n")
                f.write(f"# Push to remote\n")
                f.write(f"git push origin main --tags\n")
                f.write("```\n\n")
        
        f.write("---\n\n")
    
    def _write_footer(self, f, data: ReleaseReport) -> None:
        """Write report footer."""
        f.write("## 📎 Appendix\n\n")
        
        f.write("### Generated Files\n\n")
        f.write("| File | Description | Path |\n")
        f.write("|------|-------------|------|\n")
        f.write("| Conflict Analysis | Detailed conflict detection data | `/tmp/rdkb-release-conflicts/conflict_analysis.json` |\n")
        if data.llm_decisions:
            f.write("| LLM Decisions | AI-powered decision rationale | `/tmp/rdkb-release-conflicts/llm_decisions.json` |\n")
        f.write("| Dependency Validation | PR dependency analysis | `/tmp/rdkb-release-conflicts/dependency_validation.json` |\n")
        f.write("| Logs | Complete execution logs | `/tmp/rdkb-release-conflicts/logs/` |\n")
        f.write("\n")
        
        f.write("### About This Release Agent\n\n")
        f.write("This release was orchestrated by an intelligent automation system that:\n")
        f.write("- 🔍 **Discovers** PRs automatically from git history\n")
        f.write("- ⚠️ **Detects** conflicts using rule-based pattern matching\n")
        if data.llm_decisions:
              f.write("- 🤖 **Analyzes** PRs using LLM-powered semantic understanding\n")
        f.write("- 🔄 **Resolves** conflicts using hybrid LLM + rule-based approach\n")
        f.write("- ✅ **Validates** dependencies and suggests improvements\n")
        f.write("- 📊 **Reports** comprehensive release status\n\n")
        
        f.write("---\n\n")
        f.write(f"*Report generated by Release Agent on {data.timestamp}*\n")
