from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from psycopg_pool import AsyncConnectionPool

from flightcontrol import catalog

from ..db import fetch_one
from ..itineraries import resolve_itinerary
from ..seats import availability, lock_flights
from .common import ApiError, get_pool

router = APIRouter()


class HoldRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    itinerary_id: str = Field(min_length=1, max_length=100)
    passengers: int = Field(strict=True, ge=1, le=9)


@router.post("/holds", status_code=201)
async def make_hold(
    request: HoldRequest,
    pool: AsyncConnectionPool = Depends(get_pool),
) -> dict:
    legs = resolve_itinerary(request.itinerary_id)
    flight_ids = [flight.flight_id for flight in legs]
    cat = catalog.get()
    price_cents = sum(cat.by_id[flight_id].base_fare_cents for flight_id in flight_ids)
    hold_id = f"h_{uuid.uuid4().hex}"

    async with pool.connection() as conn:
        async with conn.transaction():
            locked = await lock_flights(conn, flight_ids)
            if len(locked) != len(flight_ids):
                raise ApiError(404, "unknown_flight", "itinerary contains an unknown flight")

            seats = await availability(conn, flight_ids)
            if any(
                seats[flight_id]["seats_available"] < request.passengers
                for flight_id in flight_ids
            ):
                raise ApiError(409, "sold_out", "one or more flights lack enough seats")

            row = await fetch_one(
                conn,
                """
                INSERT INTO holds (
                    hold_id, status, itinerary_id, flight_ids, passengers,
                    price_cents, total_cents, expires_at
                )
                VALUES (
                    %s, 'active', %s, %s, %s, %s, %s,
                    clock_timestamp() + INTERVAL '60 seconds'
                )
                RETURNING expires_at
                """,
                (
                    hold_id,
                    request.itinerary_id,
                    flight_ids,
                    request.passengers,
                    price_cents,
                    price_cents * request.passengers,
                ),
            )
            for order, flight_id in enumerate(flight_ids):
                await conn.execute(
                    """
                    INSERT INTO hold_flights (hold_id, flight_id, leg_order)
                    VALUES (%s, %s, %s)
                    """,
                    (hold_id, flight_id, order),
                )

    expires_at: datetime = row["expires_at"]
    return {
        "hold_id": hold_id,
        "itinerary_id": request.itinerary_id,
        "passengers": request.passengers,
        "price_cents": price_cents,
        "total_cents": price_cents * request.passengers,
        "expires_at": catalog.iso(expires_at),
    }
