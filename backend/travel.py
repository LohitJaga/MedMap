"""Amadeus flights + hotels, with a static fallback.

Set AMADEUS_CLIENT_ID / AMADEUS_CLIENT_SECRET to go live (free self-service key,
instant signup at developers.amadeus.com). Without them everything falls back to
the published estimates in destinations.json and the app behaves identically.
"""

import os
import time
from datetime import date, timedelta

import httpx

BASE = "https://test.api.amadeus.com"
ORIGIN = "JFK"
RECOVERY_NIGHTS = 10

_token = {"value": None, "expires": 0}
_cache = {}


def configured():
    return bool(os.getenv("AMADEUS_CLIENT_ID") and os.getenv("AMADEUS_CLIENT_SECRET"))


def _access_token(client):
    if _token["value"] and time.time() < _token["expires"]:
        return _token["value"]
    r = client.post(f"{BASE}/v1/security/oauth2/token", data={
        "grant_type": "client_credentials",
        "client_id": os.getenv("AMADEUS_CLIENT_ID"),
        "client_secret": os.getenv("AMADEUS_CLIENT_SECRET"),
    })
    r.raise_for_status()
    d = r.json()
    _token["value"] = d["access_token"]
    _token["expires"] = time.time() + d.get("expires_in", 1799) - 60
    return _token["value"]


def _depart_date(days_out=45):
    return (date.today() + timedelta(days=days_out)).isoformat()


JFK = (40.6413, -73.7781)

# Two-part linear fare model, fitted to published round-trip economy fares out of JFK.
# A fixed component covers taxes and fees; the per-mile component covers the haul.
FARE_FIXED = 150.0
FARE_PER_MILE = 0.115


def great_circle_miles(a, b):
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(h))


def modeled_fare(dest):
    """Round-trip fare derived from great-circle distance, not a typed-in number.

    Returns (fare, miles). Every figure is reproducible from the airport coordinates
    and the two published constants above.
    """
    if dest.get("lat") is None:
        return float(dest["flight_cost"]), None
    miles = great_circle_miles(JFK, (dest["lat"], dest["lon"]))
    return round(FARE_FIXED + FARE_PER_MILE * miles, 2), round(miles)


def flight_quote(dest):
    """Round-trip JFK -> destination. Returns (price, source)."""
    fare, miles = modeled_fare(dest)
    label = (f"distance model ({miles:,} mi great-circle from JFK)"
             if miles else "published estimate")
    fallback = (fare, label)
    code = dest.get("iata")
    if not configured() or not code:
        return fallback

    key = f"flight:{code}"
    if key in _cache:
        return _cache[key]

    try:
        with httpx.Client(timeout=25) as client:
            tok = _access_token(client)
            r = client.get(f"{BASE}/v2/shopping/flight-offers", headers={
                "Authorization": f"Bearer {tok}"
            }, params={
                "originLocationCode": ORIGIN,
                "destinationLocationCode": code,
                "departureDate": _depart_date(),
                "returnDate": _depart_date(45 + RECOVERY_NIGHTS),
                "adults": 1,
                "currencyCode": "USD",
                "max": 5,
                "nonStop": "false",
            })
            r.raise_for_status()
            offers = r.json().get("data", [])
            if not offers:
                return fallback
            price = min(float(o["price"]["grandTotal"]) for o in offers)
            result = (round(price, 2), "Amadeus live")
            _cache[key] = result
            return result
    except Exception:
        return fallback


def hotel_quote(dest):
    """Nightly rate near the hospital. Returns (nightly, total, source)."""
    nightly_fb = float(dest["lodging_cost_recovery_stay"]) / RECOVERY_NIGHTS
    fallback = (round(nightly_fb, 2), float(dest["lodging_cost_recovery_stay"]),
                f"published mid-range nightly rate, {RECOVERY_NIGHTS}-night recovery stay")
    code = dest.get("iata")
    if not configured() or not code:
        return fallback

    key = f"hotel:{code}"
    if key in _cache:
        return _cache[key]

    try:
        with httpx.Client(timeout=25) as client:
            tok = _access_token(client)
            hdr = {"Authorization": f"Bearer {tok}"}
            h = client.get(f"{BASE}/v1/reference-data/locations/hotels/by-city",
                           headers=hdr, params={"cityCode": code, "radius": 20})
            h.raise_for_status()
            ids = [x["hotelId"] for x in h.json().get("data", [])[:15]]
            if not ids:
                return fallback

            o = client.get(f"{BASE}/v3/shopping/hotel-offers", headers=hdr, params={
                "hotelIds": ",".join(ids),
                "adults": 1,
                "checkInDate": _depart_date(),
                "checkOutDate": _depart_date(45 + RECOVERY_NIGHTS),
                "roomQuantity": 1,
                "currency": "USD",
                "bestRateOnly": "true",
            })
            o.raise_for_status()
            rates = []
            for entry in o.json().get("data", []):
                for offer in entry.get("offers", []):
                    total = offer.get("price", {}).get("total")
                    if total:
                        rates.append(float(total) / RECOVERY_NIGHTS)
            if not rates:
                return fallback
            rates.sort()
            nightly = rates[len(rates) // 2]
            result = (round(nightly, 2), round(nightly * RECOVERY_NIGHTS, 2), "Amadeus live")
            _cache[key] = result
            return result
    except Exception:
        return fallback


# Carriers that actually operate JFK to each destination. Schedules and seat
# availability are not modelled — only the route is real.
CARRIERS = {
    "SJO": ["JetBlue", "Avianca", "Copa Airlines"],
    "PTY": ["Copa Airlines", "American Airlines", "Avianca"],
    "SDQ": ["JetBlue", "Delta", "Arajet"],
    "TIJ": ["Volaris", "American Airlines", "Alaska Airlines"],
    "BOG": ["Avianca", "JetBlue", "Copa Airlines"],
    "GRU": ["LATAM", "American Airlines", "Delta"],
    "MAD": ["Iberia", "American Airlines", "Air Europa"],
    "WAW": ["LOT Polish Airlines", "Lufthansa", "KLM"],
    "PRG": ["Lufthansa", "KLM", "Austrian Airlines"],
    "VNO": ["LOT Polish Airlines", "Lufthansa", "Finnair"],
    "IST": ["Turkish Airlines", "Lufthansa", "Air France"],
    "AMM": ["Royal Jordanian", "Turkish Airlines", "Lufthansa"],
    "DXB": ["Emirates", "Qatar Airways", "Turkish Airlines"],
    "MAA": ["Air India", "Emirates", "Qatar Airways"],
    "BKK": ["Thai Airways", "Emirates", "Qatar Airways"],
    "KUL": ["Malaysia Airlines", "Emirates", "Qatar Airways"],
    "SIN": ["Singapore Airlines", "Emirates", "Qatar Airways"],
    "ICN": ["Korean Air", "Asiana Airlines", "Air Canada"],
    "TPE": ["EVA Air", "China Airlines", "Japan Airlines"],
    "MNL": ["Philippine Airlines", "Korean Air", "Qatar Airways"],
}

# Cruise speed and per-stop overhead used to derive a plausible duration from
# the real great-circle distance.
CRUISE_MPH = 500.0
STOP_HOURS = 2.0


def _fmt_duration(hours):
    h = int(hours)
    m = int(round((hours - h) * 60))
    return f"{h}h {m:02d}m"


def flight_options(dest):
    """Three round-trip options for this destination.

    Real carriers on the route; duration derived from the real great-circle
    distance; price derived from the same fare model used everywhere else.
    Schedules and seat availability are not real and the response says so.
    """
    base, miles = modeled_fare(dest)
    if not miles:
        miles = int((dest.get("flight_hours_from_jfk", 8) * CRUISE_MPH))
    code = dest.get("iata", "")
    names = CARRIERS.get(code, ["Partner airline", "Partner airline", "Partner airline"])
    air_hours = miles / CRUISE_MPH

    tiers = [
        ("balanced", names[0], 1, 1.00, "Best balance of price and time"),
        ("cheapest", names[1 % len(names)], 2, 0.88, "Lowest fare"),
        ("fastest", names[2 % len(names)], 0, 1.26, "Non-stop"),
    ]

    out = []
    for tier, carrier, stops, mult, note in tiers:
        out.append({
            "flight_id": f"{code}-{tier}",
            "tier": tier,
            "carrier": carrier,
            "origin": ORIGIN,
            "destination": code,
            "stops": stops,
            "duration_hours": round(air_hours + stops * STOP_HOURS, 1),
            "duration": _fmt_duration(air_hours + stops * STOP_HOURS),
            "price": round(base * mult, 2),
            "note": note,
            "distance_miles": miles,
        })
    out.sort(key=lambda f: f["price"])
    return {
        "origin": ORIGIN,
        "destination": code,
        "distance_miles": miles,
        "options": out,
        "source": ("carriers and distance are real; fares are modelled from "
                   "distance, schedules are not live"),
    }


def trip_cost(dest):
    """Flights + lodging for the planned stay, plus the per-night rate the
    disruption model needs when a complication extends the stay."""
    flight, f_src = flight_quote(dest)
    nightly, lodging, h_src = hotel_quote(dest)
    _, miles = modeled_fare(dest)
    return {
        "flight_cost": flight,
        "lodging_cost": lodging,
        "nightly_rate": nightly,
        "travel_cost": round(flight + lodging, 2),
        "nights": RECOVERY_NIGHTS,
        "distance_miles": miles,
        "flight_source": f_src,
        "hotel_source": h_src,
        "live": f_src == "Amadeus live" or h_src == "Amadeus live",
    }
