from __future__ import annotations

from sovereignflow.domain import DependencyUnavailableError


class PostgreSQLHealthProbe:
    name = "postgresql"

    def __init__(self, connection_url: str, *, timeout_seconds: int = 5) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    def check(self) -> None:
        try:
            import psycopg
        except ImportError as exc:
            raise DependencyUnavailableError("psycopg is not installed") from exc
        try:
            with (
                psycopg.connect(
                    self._connection_url,
                    connect_timeout=self._timeout_seconds,
                ) as connection,
                connection.cursor() as cursor,
            ):
                cursor.execute("SELECT 1")
                row = cursor.fetchone()
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL is unavailable") from exc
        if row != (1,):
            raise DependencyUnavailableError("PostgreSQL health query returned invalid data")
