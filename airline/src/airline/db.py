from __future__ import annotations

import os
from datetime import timezone
from pathlib import Path

from psycopg import AsyncConnection
from psycopg.conninfo import make_conninfo
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from flightcontrol import catalog

DATABASE_URL = os.environ.get("DATABASE_URL")
POOL_MIN_SIZE = int(os.environ.get("AIRLINE_DB_POOL_MIN", "4"))
POOL_MAX_SIZE = int(os.environ.get("AIRLINE_DB_POOL_MAX", "32"))
POOL_MAX_WAITING = int(os.environ.get("AIRLINE_DB_MAX_WAITING", "128"))
_CONNECTION_OPTIONS = (
    "-c lock_timeout=1200ms "
    "-c statement_timeout=1800ms "
    "-c idle_in_transaction_session_timeout=5s "
    "-c synchronous_commit=on"
)
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def create_pool() -> AsyncConnectionPool:
    return AsyncConnectionPool(
        conninfo=DATABASE_URL
        or make_conninfo(
            host=os.environ.get("AIRLINE_DB_HOST", "localhost"),
            port=os.environ.get("AIRLINE_DB_PORT", "5432"),
            dbname=os.environ.get("AIRLINE_DB_NAME", "airline"),
            user=os.environ.get("AIRLINE_DB_USER", "airline"),
            password=os.environ.get("AIRLINE_DB_PASSWORD", "airline"),
        ),
        min_size=POOL_MIN_SIZE,
        max_size=POOL_MAX_SIZE,
        max_waiting=POOL_MAX_WAITING,
        timeout=1.5,
        kwargs={"row_factory": dict_row, "options": _CONNECTION_OPTIONS},
        open=False,
    )


async def initialize_database(pool: AsyncConnectionPool) -> None:
    schema = SCHEMA_PATH.read_text(encoding="utf-8")
    async with pool.connection() as conn:
        for statement in schema.split(";"):
            if statement.strip():
                await conn.execute(statement)

    await seed_catalog(pool)


async def seed_catalog(pool: AsyncConnectionPool) -> None:
    """Seed once under a transaction-scoped lock; a failed COPY rolls back fully."""
    cat = catalog.get()
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(4340)")
            cur = await conn.execute("SELECT COUNT(*) AS count FROM flights")
            count = (await cur.fetchone())["count"]
            if count == len(cat.flights):
                return
            if count:
                raise RuntimeError(
                    f"flight catalog has {count} rows; expected {len(cat.flights)}"
                )
            await conn.execute("SET LOCAL statement_timeout = 0")
            async with conn.cursor() as cur:
                async with cur.copy(
                    """
                    COPY flights (
                        flight_id, flight_number, origin, destination, departure, arrival,
                        origin_local_date, capacity, base_fare_cents, distance_mi
                    ) FROM STDIN
                    """
                ) as copy:
                    for flight in cat.flights:
                        await copy.write_row(
                            (
                                flight.flight_id,
                                flight.flight_number,
                                flight.origin,
                                flight.destination,
                                flight.departure.astimezone(timezone.utc),
                                flight.arrival.astimezone(timezone.utc),
                                flight.origin_local_date,
                                flight.capacity,
                                flight.base_fare_cents,
                                flight.distance_mi,
                            )
                        )


async def fetch_all(conn: AsyncConnection, query: str, params: tuple = ()) -> list[dict]:
    cur = await conn.execute(query, params)
    return await cur.fetchall()


async def fetch_one(conn: AsyncConnection, query: str, params: tuple = ()) -> dict | None:
    cur = await conn.execute(query, params)
    return await cur.fetchone()


def jsonb(value: object) -> Jsonb:
    return Jsonb(value)
