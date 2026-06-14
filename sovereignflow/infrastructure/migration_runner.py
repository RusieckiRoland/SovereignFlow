from __future__ import annotations

import hashlib
from importlib.resources import files

from sovereignflow.domain import DependencyUnavailableError

from .postgres_support import psycopg_module


class PostgreSQLMigrationRunner:
    def __init__(self, connection_url: str, *, timeout_seconds: int) -> None:
        self._connection_url = connection_url
        self._timeout_seconds = timeout_seconds

    def migrate(self) -> None:
        migration_root = files("sovereignflow.infrastructure.migrations")
        migrations = sorted(
            (item for item in migration_root.iterdir() if item.name.endswith(".sql")),
            key=lambda item: item.name,
        )
        try:
            psycopg = psycopg_module()
            with psycopg.connect(
                self._connection_url,
                connect_timeout=self._timeout_seconds,
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_advisory_xact_lock(%s)", (821347129,))
                    cursor.execute(
                        """
                        CREATE TABLE IF NOT EXISTS public.sovereignflow_schema_migrations (
                            version TEXT PRIMARY KEY,
                            checksum TEXT NOT NULL,
                            applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    for migration in migrations:
                        sql = migration.read_text(encoding="utf-8")
                        checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                        cursor.execute(
                            """
                            SELECT checksum
                            FROM public.sovereignflow_schema_migrations
                            WHERE version = %s
                            """,
                            (migration.name,),
                        )
                        existing = cursor.fetchone()
                        if existing is not None:
                            if existing[0] != checksum:
                                raise DependencyUnavailableError(
                                    f"Migration checksum mismatch: {migration.name}"
                                )
                            continue
                        cursor.execute(sql)
                        cursor.execute(
                            """
                            INSERT INTO public.sovereignflow_schema_migrations (version, checksum)
                            VALUES (%s, %s)
                            """,
                            (migration.name, checksum),
                        )
                connection.commit()
        except DependencyUnavailableError:
            raise
        except Exception as exc:
            raise DependencyUnavailableError("PostgreSQL migration failed") from exc
