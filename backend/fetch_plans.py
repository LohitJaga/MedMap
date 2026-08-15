"""Pull every ACA marketplace medical plan for PY2026 from data.healthcare.gov.

Gives us the user's ACTUAL deductible and out-of-pocket max instead of an assumption.
Source: Plan Attributes PUF - PY2026 (CMS, no API key required).

Writes data/plans.json.
"""

import json
import re
import time
from pathlib import Path

import httpx

DATA = Path(__file__).parent / "data"
DATASET = "ca253298-c4ef-4a77-9c44-0de0bbe91941"
URL = f"https://data.healthcare.gov/api/1/datastore/query/{DATASET}/0"

# Metal level implies coinsurance; CMS actuarial value targets.
COINSURANCE_BY_METAL = {
    "Platinum": 0.10,
    "Gold": 0.20,
    "Silver": 0.30,
    "Bronze": 0.40,
    "Expanded Bronze": 0.40,
    "Catastrophic": 0.40,
}


def money(v):
    if not v:
        return None
    m = re.search(r"([\d,]+)", str(v))
    return float(m.group(1).replace(",", "")) if m else None


def main():
    rows, offset = [], 0
    with httpx.Client(timeout=120) as client:
        while True:
            r = client.get(URL, params={
                "limit": 500,
                "offset": offset,
                "conditions[0][property]": "dentalonlyplan",
                "conditions[0][value]": "No",
                "conditions[0][operator]": "=",
            })
            r.raise_for_status()
            batch = r.json().get("results", [])
            if not batch:
                break
            rows.extend(batch)
            offset += len(batch)
            print(f"  plans: {len(rows)}")
            if len(batch) < 500:
                break
            time.sleep(0.15)

    seen, plans = set(), []
    for p in rows:
        ded = money(p.get("tehbdedinntier1individual"))
        moop = money(p.get("tehbinntier1individualmoop"))
        if ded is None or moop is None:
            continue
        pid = (p.get("planid") or "")[:14]
        key = (pid, p.get("statecode"))
        if not pid or key in seen:
            continue
        seen.add(key)
        metal = p.get("metallevel") or "Silver"
        plans.append({
            "plan_id": pid,
            "state": p.get("statecode"),
            "issuer": p.get("issuermarketplacemarketingname"),
            "name": p.get("planmarketingname"),
            "metal": metal,
            "plan_type": p.get("plantype"),
            "deductible": ded,
            "oop_max": moop,
            "coinsurance": COINSURANCE_BY_METAL.get(metal, 0.30),
        })

    plans.sort(key=lambda p: (p["state"], p["issuer"] or "", p["deductible"]))
    out = DATA / "plans.json"
    out.write_text(json.dumps(plans, indent=1), encoding="utf-8")

    print(f"\n{len(rows)} rows -> {len(plans)} unique plans -> {out}")
    print(f"{len(set(p['state'] for p in plans))} states")
    ny = [p for p in plans if p["state"] == "NY"][:3]
    for p in ny:
        print(f"  NY: {p['name']} ({p['metal']}) ded ${p['deductible']:,.0f} "
              f"moop ${p['oop_max']:,.0f}")


if __name__ == "__main__":
    main()
