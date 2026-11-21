from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import gradio as gr
import httpx

from flightcontrol.catalog import AIRPORT_BY_CODE

API_BASE_URL = os.environ.get("AIRLINE_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")


async def _api_request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    payload: dict | None = None,
) -> dict:
    try:
        async with httpx.AsyncClient(base_url=API_BASE_URL, timeout=5) as client:
            response = await client.request(method, path, params=params, json=payload)
    except httpx.HTTPError as exc:
        return {"error": f"Airline API request failed: {exc}"}

    try:
        body = response.json()
    except ValueError:
        body = {"error": "invalid_response", "detail": response.text[:1000]}
    return {"status_code": response.status_code, "body": body}


def _integer(value: int | float | None, label: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"{label} must be a whole number from {minimum} to {maximum}")
    number = float(value)
    if not number.is_integer() or not minimum <= number <= maximum:
        raise ValueError(f"{label} must be a whole number from {minimum} to {maximum}")
    return int(number)


async def _search(
    origin: str,
    destination: str,
    travel_date: str,
    passengers: int | float,
) -> tuple[dict, str]:
    try:
        party_size = _integer(passengers, "Passengers", 1, 9)
    except ValueError as exc:
        return {"error": str(exc)}, ""
    response = await _api_request(
        "GET",
        "/search",
        params={
            "origin": origin,
            "destination": destination,
            "date": travel_date,
            "passengers": party_size,
        },
    )
    itineraries = response.get("body", {}).get("itineraries", [])
    first_id = (
        itineraries[0].get("itinerary_id")
        if isinstance(itineraries, list) and itineraries and isinstance(itineraries[0], dict)
        else ""
    )
    return response, first_id or ""


async def _create_hold(itinerary_id: str, passengers: int | float) -> tuple[dict, str]:
    try:
        party_size = _integer(passengers, "Passengers", 1, 9)
    except ValueError as exc:
        return {"error": str(exc)}, ""
    response = await _api_request(
        "POST",
        "/holds",
        payload={"itinerary_id": itinerary_id, "passengers": party_size},
    )
    body = response.get("body", {})
    hold_id = body.get("hold_id", "") if response.get("status_code") == 201 else ""
    return response, hold_id


async def _create_booking(
    hold_id: str,
    passenger_names: str,
    payment_token: str,
    idempotency_key: str,
) -> tuple[dict, str]:
    names = [
        {"name": line.strip()}
        for line in passenger_names.splitlines()
        if line.strip()
    ]
    response = await _api_request(
        "POST",
        "/bookings",
        payload={
            "hold_id": hold_id,
            "passengers": names,
            "payment_token": payment_token,
            "idempotency_key": idempotency_key,
        },
    )
    body = response.get("body", {})
    booking_id = (
        body.get("booking_id", "") if response.get("status_code") in (200, 201) else ""
    )
    return response, booking_id


async def _get_booking(booking_id: str) -> dict:
    return await _api_request("GET", f"/bookings/{quote(booking_id, safe='')}")


async def _cancel_booking(booking_id: str) -> dict:
    return await _api_request("DELETE", f"/bookings/{quote(booking_id, safe='')}")


async def _health() -> dict:
    return await _api_request("GET", "/health")


async def _stats() -> dict:
    return await _api_request("GET", "/admin/stats")


async def _flight_counts(flight_id: str) -> dict:
    return await _api_request(
        "GET", f"/admin/flights/{quote(flight_id, safe='')}"
    )


async def _booking_export(offset: int | float, limit: int | float) -> dict:
    try:
        page_offset = _integer(offset, "Offset", 0, 2_000_000)
        page_limit = _integer(limit, "Limit", 1, 500)
    except ValueError as exc:
        return {"error": str(exc)}
    return await _api_request(
        "GET",
        "/admin/bookings",
        params={"offset": page_offset, "limit": page_limit},
    )


async def _reset(confirm: bool) -> dict:
    if not confirm:
        return {"error": "Confirm that all bookings and holds should be deleted."}
    return await _api_request("POST", "/admin/reset")


async def _restart(confirm: bool) -> dict:
    if not confirm:
        return {"error": "Confirm that the airline service should restart."}
    return await _api_request("POST", "/admin/restart")


def build_ui() -> gr.Blocks:
    airport_codes = sorted(AIRPORT_BY_CODE)
    with gr.Blocks(title="Airline Reservation") as demo:
        gr.Markdown(
            "# Airline Reservation\n"
            "Use the airline API through a browser. Search, hold, book, look up, "
            "and cancel travel; the instructor tab exposes the contract's admin endpoints."
        )

        with gr.Tab("Search and book"):
            with gr.Row():
                origin = gr.Dropdown(airport_codes, value="BOI", label="Origin")
                destination = gr.Dropdown(airport_codes, value="JFK", label="Destination")
                travel_date = gr.Textbox(
                    value="2027-05-04",
                    label="Departure date",
                    placeholder="YYYY-MM-DD",
                )
                search_passengers = gr.Number(
                    value=1, precision=0, minimum=1, maximum=9, label="Passengers"
                )
            search_button = gr.Button("Search flights", variant="primary")
            search_result = gr.JSON(label="Search response")

            itinerary_id = gr.Textbox(
                label="Itinerary ID",
                placeholder="Select an ID from the search response or paste one",
            )
            with gr.Row():
                hold_passengers = gr.Number(
                    value=1, precision=0, minimum=1, maximum=9, label="Hold passengers"
                )
                hold_button = gr.Button("Hold seats")
            hold_result = gr.JSON(label="Hold response")
            hold_id_for_booking = gr.Textbox(label="Hold ID")

            gr.Markdown(
                "Enter one passenger name per line. Payment tokens beginning with "
                "`tok_ok_` succeed; `tok_decline_` simulates a declined payment."
            )
            passenger_names = gr.Textbox(
                label="Passenger names",
                placeholder="Ada Park\nLee Park",
                lines=4,
            )
            with gr.Row():
                payment_token = gr.Textbox(
                    value="tok_ok_demo",
                    label="Payment token",
                )
                idempotency_key = gr.Textbox(
                    value=lambda: uuid.uuid4().hex,
                    label="Idempotency key",
                )
                new_key_button = gr.Button("New key")
            book_button = gr.Button("Book hold", variant="primary")
            booking_result = gr.JSON(label="Booking response")
            created_booking_id = gr.Textbox(label="Booking ID")

            search_button.click(
                _search,
                inputs=[origin, destination, travel_date, search_passengers],
                outputs=[search_result, itinerary_id],
            )
            hold_button.click(
                _create_hold,
                inputs=[itinerary_id, hold_passengers],
                outputs=[hold_result, hold_id_for_booking],
            )
            book_button.click(
                _create_booking,
                inputs=[
                    hold_id_for_booking,
                    passenger_names,
                    payment_token,
                    idempotency_key,
                ],
                outputs=[booking_result, created_booking_id],
            )
            new_key_button.click(lambda: uuid.uuid4().hex, outputs=idempotency_key)

        with gr.Tab("Manage bookings"):
            with gr.Row():
                lookup_id = gr.Textbox(label="Booking ID to look up")
                lookup_button = gr.Button("Get booking")
            lookup_result = gr.JSON(label="Booking")
            lookup_button.click(_get_booking, inputs=lookup_id, outputs=lookup_result)

            with gr.Row():
                cancel_id = gr.Textbox(label="Booking ID to cancel")
                cancel_button = gr.Button("Cancel booking", variant="stop")
            cancel_result = gr.JSON(label="Cancellation response")
            cancel_button.click(_cancel_booking, inputs=cancel_id, outputs=cancel_result)

        with gr.Tab("Instructor / admin"):
            gr.Markdown(
                "These actions call the contract's `/admin/*` API endpoints. "
                "Reset deletes all bookings and holds; restart stops and restarts the service."
            )
            with gr.Row():
                health_button = gr.Button("Check health")
                stats_button = gr.Button("Get statistics")
            health_result = gr.JSON(label="Health")
            stats_result = gr.JSON(label="Statistics")
            health_button.click(_health, outputs=health_result)
            stats_button.click(_stats, outputs=stats_result)

            with gr.Row():
                flight_id = gr.Textbox(label="Flight ID")
                flight_button = gr.Button("Get flight seat counts")
            flight_result = gr.JSON(label="Flight")
            flight_button.click(_flight_counts, inputs=flight_id, outputs=flight_result)

            with gr.Row():
                offset = gr.Number(value=0, precision=0, minimum=0, label="Booking offset")
                limit = gr.Number(value=100, precision=0, minimum=1, maximum=500, label="Page size")
                export_button = gr.Button("Export bookings")
            export_result = gr.JSON(label="Booking export")
            export_button.click(
                _booking_export,
                inputs=[offset, limit],
                outputs=export_result,
            )

            reset_confirm = gr.Checkbox(
                label="I understand reset deletes every booking and hold",
                value=False,
            )
            reset_button = gr.Button("Reset all bookings and holds", variant="stop")
            reset_result = gr.JSON(label="Reset response")
            reset_button.click(_reset, inputs=reset_confirm, outputs=reset_result)

            restart_confirm = gr.Checkbox(
                label="I understand this will restart the API process",
                value=False,
            )
            restart_button = gr.Button("Restart airline service", variant="stop")
            restart_result = gr.JSON(label="Restart response")
            restart_button.click(
                _restart,
                inputs=restart_confirm,
                outputs=restart_result,
            )

    return demo
