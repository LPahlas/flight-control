from __future__ import annotations

import asyncio
import os

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from psycopg_pool import AsyncConnectionPool

from ..db import fetch_all, fetch_one
from ..seats import availability
from .common import ApiError, booking_payload, get_pool

router = APIRouter()


@router.get("/admin/stats")
async def stats(pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    async with pool.connection() as conn:
        counts = await fetch_one(
            conn,
            """
            SELECT
                COUNT(*) FILTER (WHERE status = 'confirmed')::integer AS bookings_confirmed,
                COUNT(*) FILTER (WHERE status = 'cancelled')::integer AS bookings_cancelled,
                COALESCE(SUM(passenger_count) FILTER (WHERE status = 'confirmed'), 0)::integer
                    AS seats_sold,
                COALESCE(SUM(total_cents) FILTER (WHERE status = 'confirmed'), 0)::bigint
                    AS revenue_cents
            FROM bookings
            """,
        )
        holds = await fetch_one(
            conn,
            """
            SELECT COUNT(*)::integer AS active_holds,
                   COALESCE(SUM(passengers), 0)::integer AS seats_held
            FROM holds
            WHERE status = 'active' AND expires_at > clock_timestamp()
            """,
        )
    return {**counts, **holds}


@router.get("/admin/flights/{flight_id}")
async def flight(
    flight_id: str,
    pool: AsyncConnectionPool = Depends(get_pool),
) -> dict:
    async with pool.connection() as conn:
        row = await fetch_one(
            conn,
            "SELECT flight_id FROM flights WHERE flight_id = %s",
            (flight_id,),
        )
        if row is None:
            raise ApiError(404, "not_found", "flight not found")
        counts = await availability(conn, [flight_id])
    return {"flight_id": flight_id, **counts[flight_id]}


@router.get("/admin/bookings")
async def all_bookings(
    offset: int = Query(0, ge=0),
    limit: int = Query(500, ge=1, le=500),
    pool: AsyncConnectionPool = Depends(get_pool),
) -> dict:
    async with pool.connection() as conn:
        total_row = await fetch_one(conn, "SELECT COUNT(*)::integer AS total FROM bookings")
        rows = await fetch_all(
            conn,
            """
            SELECT *
            FROM bookings
            ORDER BY created_at, booking_id
            LIMIT %s OFFSET %s
            """,
            (limit, offset),
        )
    return {
        "total": int(total_row["total"]),
        "bookings": [booking_payload(row) for row in rows],
    }


@router.post("/admin/reset")
async def reset(pool: AsyncConnectionPool = Depends(get_pool)) -> dict:
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM idempotency_keys")
            await conn.execute("DELETE FROM bookings")
            await conn.execute("DELETE FROM holds")
    return {"status": "reset"}


@router.post("/admin/restart", status_code=202)
async def restart(pool: AsyncConnectionPool = Depends(get_pool)) -> JSONResponse:
    async with pool.connection() as conn:
        async with conn.transaction():
            await conn.execute(
                """
                INSERT INTO service_state (key, value)
                VALUES ('restart_requested_at', clock_timestamp())
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value
                """
            )

    asyncio.get_running_loop().call_later(0.5, os._exit, 1)
    return JSONResponse({"status": "restarting"}, status_code=202)
