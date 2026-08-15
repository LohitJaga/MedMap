"""Find real hotels near each international hospital.

Two keyless OpenStreetMap services:
  1. Nominatim  - geocode the hospital name to coordinates
  2. Overpass   - hotels within a radius, sorted by distance to that hospital

Hotel names and distances are real. Nightly rate is the destination's published
mid-range average — OSM carries no pricing — and is labelled as such.

Writes data/hotels.json keyed by hospital_id.
"""

import json
import time
from pathlib import Path

import httpx

DATA = Path(__file__).parent / "data"
UA = {"User-Agent": "MedMaps/1.0 (hackathon project; contact lohitjagarlamudi@gmail.com)"}
NOMINATIM = "https://nominatim.openstreetmap.org/search"
OVERPASS = "https://overpass-api.de/api/interpreter"
RADIUS_M = 6000
KEEP = 5


def miles(a, b):
    from math import radians, sin, cos, asin, sqrt
    lat1, lon1 = map(radians, a)
    lat2, lon2 = map(radians, b)
    h = sin((lat2 - lat1) / 2) ** 2 + cos(lat1) * cos(lat2) * sin((lon2 - lon1) / 2) ** 2
    return 2 * 3958.8 * asin(sqrt(h))


def geocode(client, hospital):
    for q in (f"{hospital['name']}, {hospital['city']}, {hospital['state_or_country']}",
              f"{hospital['city']}, {hospital['state_or_country']}"):
        try:
            r = client.get(NOMINATIM, params={"q": q, "format": "json", "limit": 1}, headers=UA)
            r.raise_for_status()
            hits = r.json()
            if hits:
                return float(hits[0]["lat"]), float(hits[0]["lon"])
        except Exception:
            pass
        time.sleep(1.1)  # Nominatim asks for <=1 request/sec
    return None


def hotels_near(client, lat, lon):
    q = (f'[out:json][timeout:30];node["tourism"="hotel"]'
         f'(around:{RADIUS_M},{lat},{lon});out body 40;')
    try:
        r = client.get(OVERPASS, params={"data": q}, headers=UA)
        r.raise_for_status()
        out = []
        for e in r.json().get("elements", []):
            name = (e.get("tags") or {}).get("name")
            if not name:
                continue
            out.append({
                "name": name,
                "lat": e["lat"],
                "lon": e["lon"],
                "stars": (e.get("tags") or {}).get("stars"),
                "distance_miles": round(miles((lat, lon), (e["lat"], e["lon"])), 2),
            })
        out.sort(key=lambda h: h["distance_miles"])
        return out[:KEEP]
    except Exception:
        return []


def main():
    hospitals = json.loads((DATA / "hospitals.json").read_text(encoding="utf-8"))
    dests = {d["country"]: d for d in json.loads((DATA / "destinations.json").read_text(encoding="utf-8"))}
    intl = [h for h in hospitals if h["location_type"] == "international"]

    result = {}
    with httpx.Client(timeout=60, follow_redirects=True) as client:
        for i, h in enumerate(intl, 1):
            coords = geocode(client, h)
            if not coords:
                print(f"  [{i}/{len(intl)}] {h['name']}: geocode failed")
                continue
            lat, lon = coords
            near = hotels_near(client, lat, lon)
            dest = dests.get(h["state_or_country"], {})
            nightly = round(float(dest.get("lodging_cost_recovery_stay", 900)) / 10, 2)
            for x in near:
                x["nightly_rate"] = nightly
                x["rate_basis"] = "destination mid-range average; OSM carries no pricing"
            result[h["hospital_id"]] = {
                "hospital_id": h["hospital_id"],
                "hospital_name": h["name"],
                "hospital_lat": lat,
                "hospital_lon": lon,
                "hotels": near,
            }
            print(f"  [{i}/{len(intl)}] {h['name']}: {len(near)} hotels")
            time.sleep(1.1)

    (DATA / "hotels.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    total = sum(len(v["hotels"]) for v in result.values())
    print(f"\n{len(result)} hospitals geocoded, {total} real hotels -> data/hotels.json")


if __name__ == "__main__":
    main()
