from pathlib import Path
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool
from . import config

pool = ConnectionPool(
    config.DATABASE_URL, min_size=1, max_size=8, open=False,
    kwargs={"row_factory": dict_row}, timeout=10,
)

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def run_migrations():
    with pool.connection() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(name TEXT PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        done = {r["name"] for r in conn.execute("SELECT name FROM schema_migrations")}
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if f.name in done:
                continue
            conn.execute(f.read_text())
            conn.execute("INSERT INTO schema_migrations(name) VALUES (%s)", (f.name,))
