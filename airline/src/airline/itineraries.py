from __future__ import annotations

from flightcontrol import catalog, oracle

from .api.common import ApiError


def resolve_itinerary(itinerary_id: str):
    cat = catalog.get()
    legs = oracle.parse_itinerary_id(cat, itinerary_id)
    if legs is None:
        raise ApiError(404, "unknown_flight", "itinerary contains an unknown flight")
    if len(legs) > 2:
        raise ApiError(422, "invalid_itinerary", "at most one stop is allowed")

    reason = oracle.check_itinerary(
        cat,
        itinerary_id,
        legs[0].origin,
        legs[-1].destination,
        legs[0].origin_local_date,
        None,
    )
    if reason:
        raise ApiError(422, "invalid_itinerary", reason)
    return legs
