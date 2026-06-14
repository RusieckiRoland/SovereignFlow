from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from statistics import fmean, median


def recall(expected: set[str], actual: Sequence[str]) -> float:
    if not expected:
        return 1.0
    return len(expected.intersection(actual)) / len(expected)


def precision(expected: set[str], actual: Sequence[str]) -> float:
    if not actual:
        return 1.0 if not expected else 0.0
    return len(expected.intersection(actual)) / len(actual)


def reciprocal_rank(expected: set[str], actual: Sequence[str]) -> float:
    for rank, item in enumerate(actual, start=1):
        if item in expected:
            return 1 / rank
    return 0.0


def mean(values: Iterable[float]) -> float | None:
    collected = list(values)
    return None if not collected else fmean(collected)


def percentile(values: Sequence[float], percentage: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentage
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def latency_metrics(values: Sequence[float]) -> dict[str, float | None]:
    return {
        "mean_latency_ms": mean(values),
        "median_latency_ms": None if not values else median(values),
        "p90_latency_ms": percentile(values, 0.90),
        "p95_latency_ms": percentile(values, 0.95),
        "p99_latency_ms": percentile(values, 0.99),
    }
