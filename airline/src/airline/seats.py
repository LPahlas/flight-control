from __future__ import annotations

from psycopg import AsyncConnection

from .db import fetch_all


async def lock_flights(conn: AsyncConnection, flight_ids: list[str]) -> list[str]:
    """Lock each leg in a stable order so simultaneous itineraries cannot oversell."""
    cur = await conn.execute(
        """
        SELECT flight_id
        FROM flights
        WHERE flight_id = ANY(%s::text[])
        ORDER BY flight_id
        FOR UPDATE
        """,
        (flight_ids,),
    )
    rows = await cur.fetchall()
    return [row["flight_id"] for row in rows]


async def availability(
    conn: AsyncConnection, flight_ids: list[str]
) -> dict[str, dict[str, int]]:
    if not flight_ids:
        return {}
    rows = await fetch_all(
        conn,
        """
        WITH wanted AS (
            SELECT unnest(%s::text[]) AS flight_id
        ),
        sold AS (
            SELECT bf.flight_id, SUM(bf.passengers)::integer AS seats_sold
            FROM booking_flights AS bf
            JOIN bookings AS b ON b.booking_id = bf.booking_id
            WHERE b.status = 'confirmed'
              AND bf.flight_id = ANY(%s::text[])
            GROUP BY bf.flight_id
        ),
        held AS (
            SELECT hf.flight_id, SUM(h.passengers)::integer AS seats_held
            FROM hold_flights AS hf
            JOIN holds AS h ON h.hold_id = hf.hold_id
            WHERE h.status = 'active'
              AND h.expires_at > clock_timestamp()
              AND hf.flight_id = ANY(%s::text[])
            GROUP BY hf.flight_id
        )
        SELECT f.flight_id, f.capacity,
               COALESCE(s.seats_sold, 0) AS seats_sold,
               COALESCE(h.seats_held, 0) AS seats_held
        FROM wanted AS w
        JOIN flights AS f USING (flight_id)
        LEFT JOIN sold AS s USING (flight_id)
        LEFT JOIN held AS h USING (flight_id)
        """,
        (flight_ids, flight_ids, flight_ids),
    )
    result: dict[str, dict[str, int]] = {}
    for row in rows:
        sold = int(row["seats_sold"])
        held = int(row["seats_held"])
        capacity = int(row["capacity"])
        result[row["flight_id"]] = {
            "capacity": capacity,
            "seats_sold": sold,
            "seats_held": held,
            "seats_available": capacity - sold - held,
        }
    return result
