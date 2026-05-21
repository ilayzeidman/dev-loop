"""Final review report renderer (Markdown)."""

from __future__ import annotations

from typing import Any


def render_review_report(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"# Final review report — {report['task_id']}")
    lines.append("")
    lines.append(f"- Status: **{report['final_status']}**")
    lines.append(f"- Selected iteration: {report.get('selected_iteration')}")
    lines.append(f"- Base SHA: `{report.get('base_sha')}`")
    lines.append(f"- Generated: {report.get('generated_at_utc')}")
    lines.append("")

    lines.append("## Original request")
    lines.append("")
    lines.append("> " + (report.get("original_request") or "").replace("\n", "\n> "))
    lines.append("")

    lines.append("## Task contract")
    lines.append("")
    tc = report.get("task_contract") or {}
    lines.append(f"- Goal: {tc.get('implementation_goal')}")
    if tc.get("success_criteria"):
        lines.append("- Success criteria:")
        for c in tc["success_criteria"]:
            lines.append(f"  - {c}")
    if tc.get("non_goals"):
        lines.append("- Non-goals:")
        for c in tc["non_goals"]:
            lines.append(f"  - {c}")
    if tc.get("ambiguities"):
        lines.append("- Ambiguities flagged by agent:")
        for c in tc["ambiguities"]:
            lines.append(f"  - {c}")
    lines.append("")

    lines.append("## Profiles")
    lines.append("")
    lines.append(f"- Agent: `{(report.get('agent_profile') or {}).get('provider')}`"
                 f" / `{(report.get('agent_profile') or {}).get('model')}`")
    lines.append(f"- Harness: `{(report.get('harness_profile') or {}).get('harness_version')}`")
    lines.append("")

    lines.append("## Iterations")
    lines.append("")
    for it in report.get("iterations", []):
        lines.append(f"### Iteration {it['iteration']}")
        lines.append("")
        if it.get("summary"):
            lines.append(f"{it['summary']}")
            lines.append("")
        lines.append(f"- Patch hash: `{it.get('patch_hash')}`")
        if it.get("changed_files"):
            lines.append("- Changed files:")
            for f in it["changed_files"]:
                lines.append(f"  - `{f}`")
        if it.get("claim_mismatches"):
            lines.append("- Claim mismatches:")
            for k, v in it["claim_mismatches"].items():
                lines.append(f"  - {k}: {v}")
        for a in it.get("attempts", []):
            lines.append(f"- Attempt {a['attempt']}: outcome=`{a['outcome']}` "
                         f"e2e=`{a.get('e2e_status')}` triage=`{a.get('triage_action')}`")
        lines.append("")

    lines.append("## Final patch")
    lines.append("")
    fp = report.get("final_patch_summary") or {}
    lines.append(f"- Hash: `{fp.get('hash')}`")
    lines.append("- Changed files:")
    for f in report.get("changed_files", []):
        lines.append(f"  - `{f}`")
    if report.get("out_of_scope_files"):
        lines.append("")
        lines.append("### Files changed outside expected scope")
        for f in report["out_of_scope_files"]:
            lines.append(f"- `{f}`")
    lines.append("")

    if report.get("risk_notes"):
        lines.append("## Risk notes")
        for r in report["risk_notes"]:
            lines.append(f"- {r}")
        lines.append("")

    if report.get("human_review_focus_areas"):
        lines.append("## Human review focus areas")
        for r in report["human_review_focus_areas"]:
            lines.append(f"- {r}")
        lines.append("")

    return "\n".join(lines)
