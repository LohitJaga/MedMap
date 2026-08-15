"""Add more procedures to us_hospitals.json using real CMS DRG pricing.

Only hip/knee have a published per-hospital complication rate (COMP_HIP_KNEE),
so every other procedure carries the national average and says so.
"""

import json
import time
from pathlib import Path

import httpx

DATA = Path(__file__).parent / "data"
PRICE_URL = "https://data.cms.gov/data-api/v1/dataset/690ddc6c-2767-4618-b277-420ffb2bf27c/data"

# DRG -> friendly procedure name. Elective, high-volume, plausible to travel for.
DRGS = {
    "470": "Total Knee Replacement",
    "233": "Coronary Artery Bypass",
    "460": "Spinal Fusion",
    "419": "Gallbladder Removal",
    "743": "Hysterectomy",
    "331": "Bowel Resection",
    "219": "Cardiac Valve Replacement",
    "483": "Shoulder Replacement",
}

# Published 90-day complication rates. Only hip/knee is per-hospital in CMS.
NATIONAL_RATES = {
    "Coronary Artery Bypass": 0.140,
    "Spinal Fusion": 0.062,
    "Gallbladder Removal": 0.031,
    "Hysterectomy": 0.038,
    "Bowel Resection": 0.115,
    "Cardiac Valve Replacement": 0.121,
    "Shoulder Replacement": 0.041,
}


def num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def fetch(client, drg):
    rows, offset = [], 0
    while True:
        r = client.get(PRICE_URL, params={"size": 1000, "offset": offset,
                                          "filter[DRG_Cd]": drg})
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < 1000:
            break
        time.sleep(0.15)
    return rows


def main():
    hospitals = json.loads((DATA / "us_hospitals.json").read_text(encoding="utf-8"))
    by_ccn = {h["hospital_id"]: h for h in hospitals}

    with httpx.Client(timeout=120) as client:
        for drg, name in DRGS.items():
            if name == "Total Knee Replacement":
                continue  # already present
            rows = fetch(client, drg)
            hit = 0
            for p in rows:
                ccn = str(p.get("Rndrng_Prvdr_CCN", "")).zfill(6)
                h = by_ccn.get(ccn)
                charge = num(p.get("Avg_Submtd_Cvrd_Chrg"))
                if not h or charge is None:
                    continue
                h["prices"][name] = round(charge, 2)
                hit += 1
            print(f"  DRG {drg} {name:<28} {len(rows):>5} rows -> {hit} hospitals matched")

    # Hip replacement was derived from knee; keep it, it shares the CMS measure.
    out = list(by_ccn.values())
    (DATA / "us_hospitals.json").write_text(json.dumps(out, indent=1), encoding="utf-8")

    counts = {}
    for h in out:
        for p in h["prices"]:
            counts[p] = counts.get(p, 0) + 1
    print("\nprocedure coverage:")
    for p, c in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {p:<30} {c:>5} hospitals")

    (DATA / "procedures.json").write_text(
        json.dumps(
            [
                {
                    "procedure_name": p,
                    "hospital_count": c,
                    "complication_source": (
                        "CMS per-hospital rate (COMP_HIP_KNEE)"
                        if "Knee" in p or "Hip" in p
                        else "national average — CMS publishes no per-hospital rate"
                    ),
                    "national_rate": NATIONAL_RATES.get(p),
                }
                for p, c in sorted(counts.items(), key=lambda x: -x[1])
                if c >= 50
            ],
            indent=1,
        ),
        encoding="utf-8",
    )
    print("\nwrote procedures.json")


if __name__ == "__main__":
    main()
