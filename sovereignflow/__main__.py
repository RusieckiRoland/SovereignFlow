from __future__ import annotations

import argparse
import sys

from .bootstrap import bootstrap, load_settings


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="sovereignflow",
        description="Run SovereignFlow with real retrieval and model providers.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="Path to the SovereignFlow YAML configuration file.",
    )
    args = parser.parse_args()

    try:
        settings = load_settings(args.config)
        application = bootstrap(settings)
    except Exception as exc:
        print(f"SovereignFlow startup failed: {exc}", file=sys.stderr)
        return 1

    try:
        from waitress import serve
    except ImportError:
        print("SovereignFlow startup failed: waitress is not installed", file=sys.stderr)
        application.close()
        return 1
    serve(
        application.app,
        host=settings.server.host,
        port=settings.server.port,
        threads=settings.server.threads,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
