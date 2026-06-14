from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def markdown_report(report: Mapping[str, Any]) -> str:
    summary = report["summary"]
    lines = [
        "# SovereignFlow dataset evaluation",
        "",
        "## Summary",
        "",
        f"- Queries: {summary['query_count']}",
        f"- Successful queries: {summary['successful_queries']}",
        f"- Failed queries: {summary['failed_queries']}",
        f"- Retrieval trace coverage: {_format(summary['retrieval_trace_coverage'])}",
        f"- Seed recall: {_format(summary['seed_recall'])}",
        f"- Graph recall: {_format(summary['graph_recall'])}",
        f"- Seed concept recall: {_format(summary['seed_concept_recall'])}",
        f"- Graph concept recall: {_format(summary['graph_concept_recall'])}",
        f"- Citation coverage: {_format(summary['citation_coverage'])}",
        f"- Forbidden leaks: {summary['forbidden_leaks']}",
        f"- P95 latency: {_format(summary['p95_latency_ms'])} ms",
        "",
        "## Thresholds",
        "",
    ]
    thresholds = report["thresholds"]
    if thresholds is None:
        lines.append("No acceptance thresholds were supplied.")
    else:
        lines.append(f"Overall result: **{'PASS' if thresholds['passed'] else 'FAIL'}**")
        lines.append("")
        lines.append("| Threshold | Actual | Expected | Result |")
        lines.append("|---|---:|---:|---|")
        for check in thresholds["checks"]:
            lines.append(
                "| {threshold} | {actual} | {expected} | {result} |".format(
                    threshold=check["threshold"],
                    actual=_format(check["actual"]),
                    expected=_format(check["expected"]),
                    result="PASS" if check["passed"] else "FAIL",
                )
            )
    lines.extend(("", "## Grouped Results", ""))
    for group_name, groups in report["groups"].items():
        lines.append(f"### {group_name}")
        lines.append("")
        lines.append("| Value | Queries | Seed Recall | Graph Recall | Leaks | P95 ms |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for value, group in groups.items():
            lines.append(
                "| {value} | {count} | {seed} | {graph} | {leaks} | {p95} |".format(
                    value=value,
                    count=group["query_count"],
                    seed=_format(group["seed_recall"]),
                    graph=_format(group["graph_recall"]),
                    leaks=group["forbidden_leaks"],
                    p95=_format(group["p95_latency_ms"]),
                )
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _format(value: Any) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.4f}"
    return str(value)
