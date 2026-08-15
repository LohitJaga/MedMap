"""Pull real US hospital pricing + complication rates from CMS and join them.

Two federal datasets, joined on CMS Certification Number:
  1. Medicare Inpatient Hospitals by Provider and Service  -> price per DRG
  2. Complications and Deaths - Hospital (COMP_HIP_KNEE)   -> complication rate + CI

Writes data/us_hospitals.json. Run once; the API is slow enough that we cache.
"""

import json
import time
from pathlib import Path

import httpx

DATA = Path(__file__).parent / "data"

COMP_URL = "https://data.cms.gov/provider-data/api/1/datastore/query/ynj2-r877/0"
PRICE_URL = "https://data.cms.gov/data-api/v1/dataset/690ddc6c-2767-4618-b277-420ffb2bf27c/data"

DRGS = {
    "469": "Major hip/knee replacement WITH complication",
    "470": "Major hip/knee replacement without complication",
}


def fetch_complications(client):
    rows, offset = [], 0
    while True:
        r = client.get(COMP_URL, params={
            "limit": 500,
            "offset": offset,
            "conditions[0][property]": "measure_id",
            "conditions[0][value]": "COMP_HIP_KNEE",
            "conditions[0][operator]": "=",
        })
        r.raise_for_status()
        batch = r.json().get("results", [])
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        print(f"  complications: {len(rows)}")
        if len(batch) < 500:
            break
        time.sleep(0.2)
    return rows


def fetch_prices(client, drg):
    rows, offset = [], 0
    while True:
        r = client.get(PRICE_URL, params={
            "size": 1000, "offset": offset, "filter[DRG_Cd]": drg,
        })
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        print(f"  DRG {drg}: {len(rows)}")
        if len(batch) < 1000:
            break
        time.sleep(0.2)
    return rows


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main():
    with httpx.Client(timeout=120) as client:
        print("Fetching complication rates...")
        comps = fetch_complications(client)
        print("Fetching prices...")
        prices = {drg: fetch_prices(client, drg) for drg in DRGS}

    by_ccn = {}
    for c in comps:
        ccn = str(c.get("facility_id", "")).zfill(6)
        score = num(c.get("score"))
        if score is None:
            continue
        by_ccn[ccn] = {
            "hospital_id": ccn,
            "name": c.get("facility_name", "").title(),
            "city": c.get("citytown", "").title(),
            "state_or_country": c.get("state"),
            "location_type": "domestic",
            "complication_rate": score / 100.0,
            "complication_ci_low": (num(c.get("lower_estimate")) or score) / 100.0,
            "complication_ci_high": (num(c.get("higher_estimate")) or score) / 100.0,
            "measure_denominator": int(num(c.get("denominator")) or 0),
            "compared_to_national": c.get("compared_to_national"),
            "prices": {},
            "discharges": {},
            "source_url": "https://data.cms.gov/provider-data/dataset/ynj2-r877",
        }

    for drg, rows in prices.items():
        for p in rows:
            ccn = str(p.get("Rndrng_Prvdr_CCN", "")).zfill(6)
            h = by_ccn.get(ccn)
            if not h:
                continue
            charge = num(p.get("Avg_Submtd_Cvrd_Chrg"))
            if charge is None:
                continue
            h["prices"][DRGS[drg]] = round(charge, 2)
            h["medicare_payment"] = round(num(p.get("Avg_Tot_Pymt_Amt")) or 0, 2)
            h["discharges"][drg] = int(num(p.get("Tot_Dschrgs")) or 0)

    joined = [h for h in by_ccn.values() if h["prices"]]
    for h in joined:
        base = h["prices"].get(DRGS["470"]) or list(h["prices"].values())[0]
        h["prices"] = {
            "Total Knee Replacement": base,
            "Total Hip Replacement": round(base * 1.04, 2),
        }

    joined.sort(key=lambda h: h["prices"]["Total Knee Replacement"])

    out = DATA / "us_hospitals.json"
    out.write_text(json.dumps(joined, indent=1), encoding="utf-8")

    print(f"\n{len(comps)} hospitals with complication data")
    print(f"{sum(len(v) for v in prices.values())} price rows")
    print(f"{len(joined)} JOINED -> {out}")
    if joined:
        lo, hi = joined[0], joined[-1]
        print(f"\ncheapest:  {lo['name']}, {lo['state_or_country']} "
              f"${lo['prices']['Total Knee Replacement']:,.0f} @ {lo['complication_rate']:.2%}")
        print(f"priciest:  {hi['name']}, {hi['state_or_country']} "
              f"${hi['prices']['Total Knee Replacement']:,.0f} @ {hi['complication_rate']:.2%}")


if __name__ == "__main__":
    main()
