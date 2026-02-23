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
        f.write(f"# Release Report: {data.component_name} v{data.version}\n\n")
        f.write(f"**Generated**: {data.timestamp}\n\n")
        f.write(f"**Strategy**: {data.strategy.upper()}\n\n")
        f.write(f"**Mode**: {'DRY RUN (Simulation)' if data.dry_run else 'LIVE EXECUTION'}\n\n")
        f.write("---\n\n")
    
    def _write_executive_summary(self, f, data: ReleaseReport) -> None:
        """Write executive summary."""
        f.write("## 📊 Executive Summary\n\n")
        
        f.write("### Configuration\n\n")
        f.write(f"- **Component**: {data.component_name}\n")
        f.write(f"- **Target Version**: {data.version}\n")
        f.write(f"- **Base Branch**: {data.base_branch}\n")
        f.write(f"- **Release Branch**: {data.release_branch}\n")
        f.write(f"- **Strategy**: {data.strategy.upper()}\n")
        f.write(f"- **Execution Time**: {data.execution_time:.1f}s\n\n")
        
        f.write("### Results at a Glance\n\n")
        f.write(f"- **PRs Discovered**: {data.total_prs_discovered}\n")
        f.write(f"- **PRs Configured**: {len(data.prs_configured)}\n")
        f.write(f"- **Conflicts Detected**: {data.conflicts_detected} ({data.conflicts_critical} critical)\n")
        f.write(f"- **LLM Decisions**: {len(data.llm_decisions)}\n")
        f.write(f"- **To Include**: {len(data.prs_to_include)}\n")
        f.write(f"- **To Exclude**: {len(data.prs_to_exclude)}\n")
        f.write(f"- **Manual Review**: {len(data.prs_manual_review)}\n\n")
        
        if not data.dry_run:
            f.write("### Execution Results\n\n")
            f.write(f"- ✅ **Successful**: {len(data.successful_prs)} PRs\n")
            f.write(f"- ❌ **Failed**: {len(data.failed_prs)} PRs\n")
            f.write(f"- ⏭️  **Skipped**: {len(data.skipped_prs)} PRs\n\n")
        
        f.write("---\n\n")
    
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
        """Write conflict analysis section."""
        f.write("## ⚠️ Conflict Analysis\n\n")
        
        f.write(f"**Total Conflicts Detected**: {data.conflicts_detected}\n\n")
        
        f.write("### Severity Breakdown\n\n")
        f.write(f"- 🔴 **Critical**: {data.conflicts_critical}\n")
        f.write(f"- 🟡 **Medium**: {data.conflicts_medium}\n")
        f.write(f"- 🟢 **Low**: {data.conflicts_low}\n\n")
        
        # Load detailed conflict data
        conflict_file = self.data_dir / "conflict_analysis.json"
        if conflict_file.exists():
            with open(conflict_file) as cf:
                conflict_data = json.load(cf)
            
            critical_conflicts = conflict_data.get("conflicts", {}).get("by_severity", {}).get("critical", [])
            if critical_conflicts:
                f.write("### Critical Conflicts\n\n")
                for conflict in critical_conflicts[:10]:
                    f.write(f"- **PR #{conflict['pr_number']}**: {conflict['reason']}\n")
                    if conflict.get('shared_files'):
                        f.write(f"  - Files: {', '.join(conflict['shared_files'][:3])}\n")
                if len(critical_conflicts) > 10:
                    f.write(f"\n*...and {len(critical_conflicts) - 10} more critical conflicts*\n")
                f.write("\n")
        
        f.write("---\n\n")
    
    def _write_llm_decisions(self, f, data: ReleaseReport) -> None:
        """Write LLM decisions section."""
        f.write("## 🤖 LLM Strategic Decisions\n\n")
        
        if not data.llm_decisions:
            f.write("*No LLM decisions made*\n\n")
            f.write("---\n\n")
            return
        
        f.write(f"**Total Decisions**: {len(data.llm_decisions)}\n\n")
        
        # Group by decision
        include = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'INCLUDE']
        exclude = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'EXCLUDE']
        manual = [pr for pr, d in data.llm_decisions.items() if d['decision'] == 'MANUAL_REVIEW']
        
        f.write(f"- ✅ **Include**: {len(include)}\n")
        f.write(f"- ⏭️ **Exclude**: {len(exclude)}\n")
        f.write(f"- 🔍 **Manual Review**: {len(manual)}\n\n")
        
        # Detailed decisions
        f.write("### Detailed Decisions\n\n")
        
        for pr_num, decision in sorted(data.llm_decisions.items()):
            emoji = "✅" if decision['decision'] == "INCLUDE" else "⏭️" if decision['decision'] == "EXCLUDE" else "🔍"
            f.write(f"#### {emoji} PR #{pr_num}: {decision['decision']} ({decision['confidence']})\n\n")
            f.write(f"**Rationale**: {decision['rationale']}\n\n")
            
            if decision.get('requires_prs'):
                f.write(f"**Requires PRs**: {decision['requires_prs']}\n\n")
            
            if decision.get('risks'):
                f.write("**Risks**:\n")
                for risk in decision['risks']:
                    f.write(f"- {risk}\n")
                f.write("\n")
            
            if decision.get('benefits'):
                f.write("**Benefits**:\n")
                for benefit in decision['benefits']:
                    f.write(f"- {benefit}\n")
                f.write("\n")
        
        f.write("---\n\n")
    
    def _write_dependency_validation(self, f, data: ReleaseReport) -> None:
        """Write dependency validation section."""
        f.write("## 🔗 Dependency Validation\n\n")
        
        if not data.dependency_warnings and not data.missing_dependencies:
            f.write("✅ **No dependency issues detected**\n\n")
            f.write("---\n\n")
            return
        
        if data.dependency_warnings:
            f.write("### ⚠️ Warnings\n\n")
            for warning in data.dependency_warnings:
                f.write(f"- {warning}\n")
            f.write("\n")
        
        if data.missing_dependencies:
            f.write("### Missing Dependencies\n\n")
            for pr, deps in data.missing_dependencies.items():
                f.write(f"- **PR #{pr}** requires: {deps}\n")
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
        
        if data.successful_prs:
            f.write(f"### ✅ Successfully Applied ({len(data.successful_prs)})\n\n")
            for pr in data.successful_prs:
                f.write(f"- PR #{pr}\n")
            f.write("\n")
        
        if data.failed_prs:
            f.write(f"### ❌ Failed / Needs Manual Review ({len(data.failed_prs)})\n\n")
            for pr in data.failed_prs:
                f.write(f"- PR #{pr}\n")
            f.write("\n")
        
        if data.skipped_prs:
            f.write(f"### ⏭️ Skipped ({len(data.skipped_prs)})\n\n")
            for pr in data.skipped_prs:
                f.write(f"- PR #{pr}\n")
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
        f.write("## 📋 Next Steps\n\n")
        
        if data.dry_run:
            f.write("### After Reviewing This Report:\n\n")
            f.write("1. Review all LLM decisions and dependency warnings\n")
            f.write("2. Update configuration if needed (add/remove PRs)\n")
            f.write("3. Run again without `--dry-run` to execute the release\n\n")
        else:
            if data.failed_prs:
                f.write("### Manual Intervention Required:\n\n")
                f.write("1. Review failed PRs and resolve conflicts manually\n")
                f.write("2. Cherry-pick/revert PRs as needed\n")
                f.write("3. Test the release branch thoroughly\n")
                f.write("4. Create pull request to merge release branch\n\n")
            else:
                f.write("### Release Branch Ready:\n\n")
                f.write(f"1. Test the release branch: `{data.release_branch}`\n")
                f.write("2. Create pull request for review\n")
                f.write("3. Merge to production after approval\n\n")
        
        f.write("---\n\n")
    
    def _write_footer(self, f, data: ReleaseReport) -> None:
        """Write report footer."""
        f.write("## 📎 Appendix\n\n")
        f.write("### Generated Files\n\n")
        f.write("- **Conflict Analysis**: `/tmp/rdkb-release-conflicts/conflict_analysis.json`\n")
        f.write("- **LLM Decisions**: `/tmp/rdkb-release-conflicts/llm_decisions.json`\n")
        f.write("- **Dependency Validation**: `/tmp/rdkb-release-conflicts/dependency_validation.json`\n")
        f.write("- **Logs**: `/tmp/rdkb-release-conflicts/logs/`\n\n")
        
        f.write("---\n\n")
        f.write(f"*Report generated by RDK-B Release Agent on {data.timestamp}*\n")
