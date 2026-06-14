from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from sovereignflow.bootstrap import bootstrap_import, load_settings
from sovereignflow.domain import SovereignFlowError
from sovereignflow.infrastructure import JsonlDatasetReader, RelationshipScope


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sovereignflow-import",
        description="Import and verify neutral SovereignFlow datasets.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import", help="Import a JSONL dataset.")
    _common(import_parser)
    import_parser.add_argument("--nodes", type=Path, required=True)
    import_parser.add_argument("--edges", type=Path, required=True)
    import_parser.add_argument("--operations", type=Path, required=True)
    import_parser.add_argument("--workspace", type=Path, required=True)
    import_parser.add_argument("--import-id", required=True)
    import_parser.add_argument(
        "--relationship-scope",
        choices=tuple(RelationshipScope),
        required=True,
        help=(
            "'internal' imports only relationships whose endpoints are in the selected "
            "dataset boundary; 'complete' rejects every missing endpoint."
        ),
    )

    status_parser = subparsers.add_parser("status", help="Read dataset import status.")
    _common(status_parser)
    status_parser.add_argument("--import-id", required=True)

    verify_parser = subparsers.add_parser("verify", help="Verify active storage counts.")
    _common(verify_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    application = None
    try:
        settings = load_settings(args.config)
        application = bootstrap_import(settings, domain_name=args.domain)
        if args.command == "import":
            run = application.service.execute(
                JsonlDatasetReader(
                    import_id=args.import_id,
                    nodes_path=args.nodes,
                    edges_path=args.edges,
                    operations_path=args.operations,
                    workspace_path=args.workspace,
                    relationship_scope=RelationshipScope(args.relationship_scope),
                )
            )
            _print(_run_payload(run))
            return 0
        if args.command == "status":
            _print(_run_payload(application.service.status(args.import_id)))
            return 0
        report = application.service.consistency()
        _print(
            {
                "domain": report.domain,
                "tenant_id": report.tenant_id,
                "active_sources": report.active_sources,
                "active_chunks": report.active_chunks,
                "indexed_chunks": report.indexed_chunks,
                "active_relationships": report.active_relationships,
                "consistent": report.consistent,
            }
        )
        return 0 if report.consistent else 3
    except SovereignFlowError as exc:
        _print({"error": {"code": exc.code, "message": exc.safe_message}}, stream=sys.stderr)
        return 2
    except Exception:
        _print(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Dataset operation could not be completed",
                }
            },
            stream=sys.stderr,
        )
        return 2
    finally:
        if application is not None:
            application.close()


def _common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--domain", required=True)


def _run_payload(run) -> dict:
    return {
        "import_id": run.import_id,
        "domain": run.domain,
        "tenant_id": run.tenant_id,
        "dataset_hash": run.dataset_hash,
        "status": run.status.value,
        "source_count": run.source_count,
        "chunk_count": run.chunk_count,
        "relationship_count": run.relationship_count,
        "deletion_count": run.deletion_count,
        "indexed_sources": run.indexed_sources,
        "published_relationships": run.published_relationships,
        "deleted_sources": run.deleted_sources,
        "error_code": run.error_code,
        "error_message": run.error_message,
    }


def _print(payload: dict, *, stream=None) -> None:
    selected_stream = sys.stdout if stream is None else stream
    print(
        json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True),
        file=selected_stream,
    )


if __name__ == "__main__":
    raise SystemExit(main())
