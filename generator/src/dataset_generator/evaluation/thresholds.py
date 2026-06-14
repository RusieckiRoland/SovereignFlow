from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .contracts import ContractError

THRESHOLD_RULES = {
    "minimum_seed_recall": ("seed_recall", "minimum"),
    "minimum_graph_recall": ("graph_recall", "minimum"),
    "minimum_seed_concept_recall": ("seed_concept_recall", "minimum"),
    "minimum_graph_concept_recall": ("graph_concept_recall", "minimum"),
    "minimum_citation_coverage": ("citation_coverage", "minimum"),
    "minimum_success_rate": ("success_rate", "minimum"),
    "maximum_forbidden_leaks": ("forbidden_leaks", "maximum"),
    "maximum_error_rate": ("error_rate", "maximum"),
    "maximum_p95_latency_ms": ("p95_latency_ms", "maximum"),
}


def evaluate_thresholds(
    thresholds: Mapping[str, Any],
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    unknown = sorted(set(thresholds) - set(THRESHOLD_RULES))
    if unknown:
        raise ContractError(f"Unknown threshold keys: {', '.join(unknown)}")
    checks = []
    for threshold_name in sorted(thresholds):
        expected = thresholds[threshold_name]
        if not isinstance(expected, int | float) or isinstance(expected, bool):
            raise ContractError(f"{threshold_name} must be a number")
        metric_name, mode = THRESHOLD_RULES[threshold_name]
        actual = summary.get(metric_name)
        if actual is None:
            passed = False
            reason = "metric unavailable"
        else:
            passed = actual >= expected if mode == "minimum" else actual <= expected
            reason = None if passed else f"{actual} does not satisfy {mode} {expected}"
        checks.append(
            {
                "threshold": threshold_name,
                "metric": metric_name,
                "expected": expected,
                "actual": actual,
                "passed": passed,
                "reason": reason,
            }
        )
    return {
        "passed": all(check["passed"] for check in checks),
        "checks": checks,
    }
