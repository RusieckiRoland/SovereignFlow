from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from .bootstrap import bootstrap, load_settings


def _load_env_file(path: Path) -> None:
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


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
    parser.add_argument(
        "--env-file",
        default=None,
        help="Path to a .env file with environment variables (KEY=value lines).",
    )
    args = parser.parse_args()

    if args.env_file is not None:
        env_path = Path(args.env_file)
        if not env_path.is_file():
            print(f"SovereignFlow startup failed: env file not found: {env_path}", file=sys.stderr)
            return 1
        _load_env_file(env_path)

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
