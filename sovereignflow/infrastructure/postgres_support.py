from __future__ import annotations

from sovereignflow.domain import DependencyUnavailableError


def psycopg_module():
    try:
        import psycopg
    except ImportError as exc:
        raise DependencyUnavailableError("psycopg is not installed") from exc
    return psycopg
