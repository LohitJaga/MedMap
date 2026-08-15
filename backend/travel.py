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


def flight_quote(dest):
    """Round-trip JFK -> destination. Returns (price, source)."""
    fallback = (float(dest["flight_cost"]), "estimate")
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
    fallback = (round(nightly_fb, 2), float(dest["lodging_cost_recovery_stay"]), "estimate")
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


def trip_cost(dest):
    """Flights + lodging for the planned stay, plus the per-night rate the
    disruption model needs when a complication extends the stay."""
    flight, f_src = flight_quote(dest)
    nightly, lodging, h_src = hotel_quote(dest)
    return {
        "flight_cost": flight,
        "lodging_cost": lodging,
        "nightly_rate": nightly,
        "travel_cost": round(flight + lodging, 2),
        "nights": RECOVERY_NIGHTS,
        "flight_source": f_src,
        "hotel_source": h_src,
        "live": f_src != "estimate" or h_src != "estimate",
    }
