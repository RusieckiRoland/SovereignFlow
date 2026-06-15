from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from pathlib import Path

from .analyzer import analyze_results
from .client import execute_queries
from .contracts import AnalysisConfig, EvaluationError, ExecutionConfig

LOGGER = logging.getLogger("dataset_generator.evaluation")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataset_generator.evaluation",
        description="Execute and analyze SovereignFlow synthetic dataset queries.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute queries through an HTTP API.")
    run_parser.add_argument("--queries", type=Path, required=True)
    run_parser.add_argument("--results", type=Path, required=True)
    run_parser.add_argument("--endpoint", required=True)
    run_parser.add_argument("--timeout", type=float, default=30.0)
    run_parser.add_argument("--access-token-env", required=True)
    run_parser.add_argument("--overwrite", action="store_true")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze saved query results.")
    analyze_parser.add_argument("--queries", type=Path, required=True)
    analyze_parser.add_argument("--results", type=Path, required=True)
    analyze_parser.add_argument("--ground-truth", type=Path, required=True)
    analyze_parser.add_argument("--out", type=Path, required=True)
    analyze_parser.add_argument("--manifest", type=Path)
    analyze_parser.add_argument("--thresholds", type=Path)
    analyze_parser.add_argument("--recall-at-k", type=int, default=10)
    analyze_parser.add_argument("--metrics-csv", action="store_true")
    analyze_parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            access_token = _required_environment_value(args.access_token_env)
            count = execute_queries(
                ExecutionConfig(
                    queries_path=args.queries,
                    output_path=args.results,
                    endpoint=args.endpoint,
                    timeout_seconds=args.timeout,
                    access_token=access_token,
                    overwrite=args.overwrite,
                )
            )
            LOGGER.info("Executed %d queries", count)
            return 0
        outcome = analyze_results(
            AnalysisConfig(
                queries_path=args.queries,
                results_path=args.results,
                ground_truth_path=args.ground_truth,
                output_directory=args.out,
                manifest_path=args.manifest,
                thresholds_path=args.thresholds,
                recall_at_k=args.recall_at_k,
                overwrite=args.overwrite,
                write_csv=args.metrics_csv,
            )
        )
        if not outcome.threshold_passed:
            LOGGER.error("Evaluation thresholds failed")
            return 3
        LOGGER.info("Evaluation complete")
        return 0
    except EvaluationError as exc:
        LOGGER.error("%s", exc)
        return 2


def _required_environment_value(environment_name: str) -> str:
    value = os.environ.get(environment_name)
    if not value:
        raise EvaluationError(f"Environment variable is missing or empty: {environment_name}")
    return value
