from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query
from psycopg_pool import AsyncConnectionPool

from flightcontrol import catalog, oracle

from ..seats import availability
from .common import ApiError, get_pool

router = APIRouter()


@router.get("/search")
async def search(
    origin: str = Query(..., min_length=3, max_length=3),
    destination: str = Query(..., min_length=3, max_length=3),
    date: date = Query(...),
    passengers: int = Query(1, ge=1, le=9),
    pool: AsyncConnectionPool = Depends(get_pool),
) -> dict:
    cat = catalog.get()
    if origin not in catalog.AIRPORT_BY_CODE or destination not in catalog.AIRPORT_BY_CODE:
        raise ApiError(422, "unknown_airport", "origin and destination must be catalog airports")

    options = oracle.itineraries(cat, origin, destination, date)
    if not options:
        return {"itineraries": []}

    flight_ids = list(dict.fromkeys(f.flight_id for option in options for f in option.legs))
    async with pool.connection() as conn:
        seats = await availability(conn, flight_ids)

    results = []
    for option in options:
        available = min(seats[f.flight_id]["seats_available"] for f in option.legs)
        if available < passengers:
            continue
        results.append(
            {
                "itinerary_id": option.itinerary_id,
                "flight_ids": [f.flight_id for f in option.legs],
                "departure": catalog.iso(option.departure),
                "arrival": catalog.iso(option.arrival),
                "stops": option.stops,
                "price_cents": option.base_fare_cents,
                "seats_available": available,
            }
        )

    return {"itineraries": results[: oracle.MAX_RESULTS]}
