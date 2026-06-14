from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path

from .generation import generate_dataset
from .models import GeneratorConfig, GeneratorError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataset_generator",
        description="Generate deterministic synthetic RAG and graph test data.",
    )
    parser.add_argument("--out", type=Path, required=True, help="Output directory.")
    parser.add_argument("--nodes", type=int, required=True, help="Exact node count.")
    parser.add_argument("--domains", type=int, required=True, help="Domain count.")
    parser.add_argument("--seed", type=int, required=True, help="Non-negative random seed.")
    parser.add_argument("--queries", type=int, required=True, help="Exact query count.")
    parser.add_argument(
        "--tenants",
        type=int,
        default=1,
        help="Tenant count.",
    )
    parser.add_argument(
        "--max-edges-per-node",
        type=int,
        default=6,
        help="Maximum outgoing edge count.",
    )
    parser.add_argument(
        "--versions",
        type=int,
        default=1,
        help="Source version count.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=100_000,
        help="Log progress after this many generated nodes.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace existing output files.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = build_parser().parse_args(argv)
    config = GeneratorConfig(
        output_directory=args.out,
        nodes=args.nodes,
        domains=args.domains,
        seed=args.seed,
        queries=args.queries,
        progress_every=args.progress_every,
        overwrite=args.overwrite,
        tenants=args.tenants,
        max_edges_per_node=args.max_edges_per_node,
        versions=args.versions,
    )
    try:
        summary = generate_dataset(config)
    except GeneratorError as exc:
        logging.getLogger("dataset_generator").error("%s", exc)
        return 2
    logging.getLogger("dataset_generator").info(
        (
            "Dataset complete: %d nodes, %d edges, %d queries, "
            "%d ground-truth records, %d operations"
        ),
        summary.nodes,
        summary.edges,
        summary.queries,
        summary.ground_truth,
        summary.operations,
    )
    return 0
