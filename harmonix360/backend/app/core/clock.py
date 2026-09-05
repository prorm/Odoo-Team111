"""Server-authoritative time for anything a sync cursor compares against.

Every worker process (backend, Taskiq worker, a future second backend replica)
has its own clock, and clock drift between them silently reopens the exact race
the offline-sync cursor is supposed to close: two processes racing to write the
"last modified" timestamp for a cursor comparison must agree on what "now" means,
and only the single Postgres engine both of them talk to can guarantee that.
`utc_now()` in app/models/mixins.py (a Python `datetime.now(timezone.utc)`
callable) is therefore wrong for any column a sync cursor reads — it was fine
for created_at/updated_at as plain audit metadata, but not as a cursor key.
"""
from sqlalchemy import func
from sqlalchemy.sql.elements import ColumnElement


def server_utc_now() -> ColumnElement:
    """SQL expression for Postgres engine time, forced to UTC.

    `now()` already returns an absolute instant (TIMESTAMPTZ), but comparing
    it against other expressions is only guaranteed correct if every session
    agrees on a timezone. Round-tripping through `timezone('UTC', ...)` twice
    (instant -> naive UTC wall-clock reading -> re-interpreted as UTC) lands
    back on the identical instant `now()` started with, but does so in a way
    that is immune to whatever the connection's session `TimeZone` setting
    happens to be — so every worker's query resolves to the same value
    regardless of pool/session configuration drift.
    """
    return func.timezone("UTC", func.timezone("UTC", func.now()))
