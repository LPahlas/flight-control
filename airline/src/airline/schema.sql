CREATE TABLE IF NOT EXISTS flights (
    flight_id TEXT PRIMARY KEY,
    flight_number TEXT NOT NULL,
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    departure TIMESTAMPTZ NOT NULL,
    arrival TIMESTAMPTZ NOT NULL,
    origin_local_date DATE NOT NULL,
    capacity INTEGER NOT NULL CHECK (capacity > 0),
    base_fare_cents INTEGER NOT NULL CHECK (base_fare_cents >= 0),
    distance_mi INTEGER NOT NULL CHECK (distance_mi > 0)
);

CREATE TABLE IF NOT EXISTS holds (
    hold_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('active', 'booked', 'declined', 'expired')),
    itinerary_id TEXT NOT NULL,
    flight_ids TEXT[] NOT NULL,
    passengers INTEGER NOT NULL CHECK (passengers BETWEEN 1 AND 9),
    price_cents INTEGER NOT NULL CHECK (price_cents >= 0),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS holds_live_by_expiry
    ON holds (expires_at) WHERE status = 'active';

CREATE TABLE IF NOT EXISTS hold_flights (
    hold_id TEXT NOT NULL REFERENCES holds(hold_id) ON DELETE CASCADE,
    flight_id TEXT NOT NULL REFERENCES flights(flight_id),
    leg_order SMALLINT NOT NULL CHECK (leg_order >= 0),
    PRIMARY KEY (hold_id, flight_id),
    UNIQUE (hold_id, leg_order)
);

CREATE INDEX IF NOT EXISTS hold_flights_by_flight ON hold_flights (flight_id, hold_id);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id TEXT PRIMARY KEY,
    status TEXT NOT NULL CHECK (status IN ('confirmed', 'cancelled')),
    itinerary_id TEXT NOT NULL,
    flight_ids TEXT[] NOT NULL,
    passengers JSONB NOT NULL,
    passenger_count INTEGER NOT NULL CHECK (passenger_count BETWEEN 1 AND 9),
    total_cents INTEGER NOT NULL CHECK (total_cents >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);

CREATE INDEX IF NOT EXISTS bookings_created_order ON bookings (created_at, booking_id);
CREATE INDEX IF NOT EXISTS bookings_status ON bookings (status);

CREATE TABLE IF NOT EXISTS booking_flights (
    booking_id TEXT NOT NULL REFERENCES bookings(booking_id) ON DELETE CASCADE,
    flight_id TEXT NOT NULL REFERENCES flights(flight_id),
    leg_order SMALLINT NOT NULL CHECK (leg_order >= 0),
    passengers INTEGER NOT NULL CHECK (passengers BETWEEN 1 AND 9),
    PRIMARY KEY (booking_id, flight_id),
    UNIQUE (booking_id, leg_order)
);

CREATE INDEX IF NOT EXISTS booking_flights_by_flight ON booking_flights (flight_id, booking_id);

CREATE TABLE IF NOT EXISTS idempotency_keys (
    idempotency_key TEXT PRIMARY KEY,
    request_body JSONB NOT NULL,
    response_status INTEGER,
    response_body JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    CHECK ((response_status IS NULL) = (response_body IS NULL))
);

CREATE TABLE IF NOT EXISTS service_state (
    key TEXT PRIMARY KEY,
    value TIMESTAMPTZ NOT NULL
);
