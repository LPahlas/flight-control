from __future__ import annotations

import os
import uuid
from urllib.parse import quote

import gradio as gr
import httpx

from flightcontrol.catalog import AIRPORT_BY_CODE

API_BASE_URL = os.environ.get("AIRLINE_API_BASE_URL", "http://127.0.0.1:8080").rstrip("/")

USER_CSS = """
:root { --body-background-fill: #f4f1ea; --block-background-fill: #fffdf8; --block-border-color: #ddd5c6; --body-text-color: #1d232b; --color-accent: #c76a00; }
.gradio-container { max-width: 1120px !important; margin: auto; font-family: "IBM Plex Sans", system-ui, sans-serif; background: #f4f1ea; }
.gradio-container .prose { color: #1d232b; }
.block { border-radius: 12px !important; }
label span, .wrap label { color: #5d6672 !important; }
button.primary { background: #c76a00 !important; border-color: #c76a00 !important; color: #1b1203 !important; }
button.stop { background: #fffdf8 !important; border: 1px solid #b3261e !important; color: #b3261e !important; }
footer { display: none !important; }
"""


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
) -> tuple[str, list[str], dict[str, str]]:
    try:
        party_size = _integer(passengers, "Passengers", 1, 9)
    except ValueError as exc:
        return f"Please check your trip details: {exc}", [], {}
    if origin == destination:
        return "Choose two different airports.", [], {}

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
    if response["status_code"] != 200:
        body = response.get("body", {})
        message = body.get("detail") or body.get("error") or "We could not search for flights."
        return str(message), [], {}

    itineraries = response.get("body", {}).get("itineraries", [])
    if not itineraries:
        return "No flights are available for that trip.", [], {}

    choices: list[str] = []
    itinerary_map: dict[str, str] = {}
    for item in itineraries:
        stops = "Nonstop" if item["stops"] == 0 else f"{item['stops']} stop"
        label = (
            f"{stops}  |  "
            f"${item['price_cents'] / 100:.2f} per traveler  |  "
            f"{item['seats_available']} seats left"
        )
        choices.append(label)
        itinerary_map[label] = item["itinerary_id"]
    return f"Found {len(choices)} option{'s' if len(choices) != 1 else ''}.", choices, itinerary_map


async def _create_hold(itinerary_id: str, passengers: int | float) -> tuple[str, str]:
    try:
        party_size = _integer(passengers, "Passengers", 1, 9)
    except ValueError as exc:
        return f"Please check your traveler count: {exc}", ""
    if not itinerary_id:
        return "Search for a trip and choose a flight first.", ""

    response = await _api_request(
        "POST",
        "/holds",
        payload={"itinerary_id": itinerary_id, "passengers": party_size},
    )
    body = response.get("body", {})
    if response.get("status_code") != 201:
        message = body.get("detail") or body.get("error") or "Those seats could not be held."
        if body.get("error") == "sold_out":
            message = "That option just sold out. Please search again."
        return str(message), ""

    hold_id = body["hold_id"]
    return (
        f"Seats held successfully for 60 seconds. "
        f"Total: ${body['total_cents'] / 100:.2f}. "
        f"Continue to passenger details now.",
        hold_id,
    )


async def _hold_selected(
    label: str,
    itinerary_map: dict[str, str],
    passengers: int | float,
) -> tuple[str, str]:
    return await _create_hold(itinerary_map.get(label or "", ""), passengers)


async def _create_booking(
    passenger_names: str,
    payment_choice: str,
    hold_id: str,
) -> tuple[str, str]:
    if not hold_id:
        return "Hold seats before completing the booking.", ""

    names = [
        {"name": line.strip()}
        for line in passenger_names.splitlines()
        if line.strip()
    ]
    if not names:
        return "Enter at least one passenger name.", ""

    response = await _api_request(
        "POST",
        "/bookings",
        payload={
            "hold_id": hold_id,
            "passengers": names,
            "payment_token": (
                "tok_ok_demo" if payment_choice == "success" else "tok_decline_demo"
            ),
            "idempotency_key": uuid.uuid4().hex,
        },
    )
    body = response.get("body", {})
    if response.get("status_code") not in (200, 201):
        message = body.get("detail") or body.get("error") or "The booking could not be completed."
        if body.get("error") == "payment_declined":
            message = "The demo payment was declined. Try the successful demo payment."
        elif body.get("error") == "hold_expired":
            message = "The seat hold has expired. Search and hold the seats again."
        elif body.get("error") == "passenger_mismatch":
            message = "Enter one passenger name for each seat you held."
        return str(message), ""

    booking_id = body["booking_id"]
    return (
        f"Booking confirmed for {len(names)} traveler{'s' if len(names) != 1 else ''}. "
        f"Total: ${body['total_cents'] / 100:.2f}. "
        f"Keep your booking reference: {booking_id}",
        booking_id,
    )


async def _get_booking(booking_id: str) -> str:
    if not booking_id.strip():
        return "Enter your booking reference."
    response = await _api_request("GET", f"/bookings/{quote(booking_id.strip(), safe='')}")
    if response["status_code"] == 404:
        return "We could not find that booking reference."
    body = response["body"]
    names = ", ".join(passenger["name"] for passenger in body["passengers"])
    status = "Confirmed" if body["status"] == "confirmed" else "Cancelled"
    return (
        f"**{status} booking**\n\n"
        f"- Reference: `{body['booking_id']}`\n"
        f"- Travelers: {names}\n"
        f"- Flights: {' → '.join(body['flight_ids'])}\n"
        f"- Total: ${body['total_cents'] / 100:.2f}"
    )


async def _cancel_booking(booking_id: str) -> str:
    if not booking_id.strip():
        return "Enter your booking reference."
    response = await _api_request(
        "DELETE", f"/bookings/{quote(booking_id.strip(), safe='')}"
    )
    if response["status_code"] == 404:
        return "We could not find that booking reference."
    body = response["body"]
    return (
        f"Booking {body['booking_id']} is cancelled. "
        f"${body['total_cents'] / 100:.2f} would be refunded."
    )


def build_ui() -> gr.Blocks:
    airport_choices = [
        (f"{airport.city} ({airport.code})", airport.code)
        for airport in AIRPORT_BY_CODE.values()
    ]
    with gr.Blocks(title="Northwind Air") as demo:
        gr.Markdown(
            "# Northwind Air\n"
            "### Find your next flight"
        )
        hold_id = gr.State("")
        booking_id = gr.State("")
        itinerary_map = gr.State({})

        with gr.Tab("Book a trip"):
            with gr.Group():
                with gr.Row():
                    origin = gr.Dropdown(
                        airport_choices,
                        value="BOI",
                        label="Flying from",
                        interactive=True,
                    )
                    destination = gr.Dropdown(
                        airport_choices,
                        value="JFK",
                        label="Flying to",
                        interactive=True,
                    )
                with gr.Row():
                    travel_date = gr.Textbox(
                        value="2027-05-04",
                        label="Travel date",
                        placeholder="Use YYYY-MM-DD",
                    )
                    passengers = gr.Number(
                        value=1,
                        precision=0,
                        minimum=1,
                        maximum=9,
                        label="Travelers",
                    )
                search_button = gr.Button("Search flights", variant="primary")

            search_message = gr.Markdown()
            itinerary = gr.Dropdown(
                choices=[],
                label="Available flights",
                interactive=True,
            )
            hold_button = gr.Button("Hold these seats")
            hold_message = gr.Markdown()

            gr.Markdown("#### Traveler and payment details")
            passenger_names = gr.Textbox(
                label="Traveler names",
                placeholder="One name per line",
                lines=3,
            )
            payment = gr.Radio(
                choices=[
                    ("Use successful demo payment", "success"),
                    ("Test a declined payment", "decline"),
                ],
                value="success",
                label="Payment",
            )
            book_button = gr.Button("Confirm booking", variant="primary")
            booking_message = gr.Markdown()
            booking_reference = gr.Textbox(
                label="Your booking reference",
                interactive=False,
            )

            search_button.click(
                _search,
                inputs=[origin, destination, travel_date, passengers],
                outputs=[search_message, itinerary, itinerary_map],
            )
            hold_button.click(
                _hold_selected,
                inputs=[itinerary, itinerary_map, passengers],
                outputs=[hold_message, hold_id],
            )
            book_button.click(
                _create_booking,
                inputs=[passenger_names, payment, hold_id],
                outputs=[booking_message, booking_reference],
            ).then(
                lambda reference: reference,
                inputs=booking_reference,
                outputs=booking_id,
            )

        with gr.Tab("My booking"):
            gr.Markdown(
                "Enter the booking reference from your confirmation to look up or "
                "cancel your trip."
            )
            reference_lookup = gr.Textbox(
                label="Booking reference",
                placeholder="For example: b_...",
            )
            lookup_button = gr.Button("View booking")
            lookup_message = gr.Markdown()
            lookup_button.click(
                _get_booking,
                inputs=reference_lookup,
                outputs=lookup_message,
            )

            gr.Markdown("#### Cancel a trip")
            cancel_button = gr.Button("Cancel this booking", variant="stop")
            cancel_message = gr.Markdown()
            cancel_button.click(
                _cancel_booking,
                inputs=reference_lookup,
                outputs=cancel_message,
            )

    return demo
