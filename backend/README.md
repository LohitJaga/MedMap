# Backend

FastAPI. Real CMS data, no database — everything loads from `data/*.json`.

## Run

```bash
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

`--host 0.0.0.0` matters: it lets the frontend hit this laptop over the venue wifi.
Find the LAN IP with `ipconfig` and point `frontend/src/api.js` at `http://<ip>:8000`.

Interactive docs at `/docs` — the whole flow can be driven from there if the frontend
isn't ready.

## Data

`data/us_hospitals.json` is committed, so nobody needs to re-pull. It was built by
`fetch_cms.py`, which joins two federal datasets on CMS Certification Number:

- **Medicare Inpatient Hospitals by Provider and Service** — DRG 469/470 average charges
- **Complications and Deaths - Hospital** — `COMP_HIP_KNEE` rate with confidence interval

4,790 hospitals have complication data, 1,282 have DRG pricing, **1,173 have both**.
Price range for the same procedure: $20,699 to $344,847. That 16.7x spread is the pitch.

Re-pull with `python fetch_cms.py` (takes ~2 min).

`data/hospitals.json` and `data/destinations.json` are the international side. No federal
equivalent exists abroad, so those are curated — say so in the demo rather than implying
they're sourced the same way.

## Live travel pricing

Flights and hotels come from Amadeus when credentials are set, and fall back to the
estimates in `destinations.json` when they aren't. Behaviour is identical either way,
so a missing key never breaks the demo.

```bash
export AMADEUS_CLIENT_ID=...
export AMADEUS_CLIENT_SECRET=...
```

Free self-service key, instant signup, no approval: https://developers.amadeus.com

Every international option reports `travel_source: {flights, hotels}` — either
`"Amadeus live"` or `"estimate"`. Render that honestly.

## The model

```
expected_cost = out_of_pocket + P(complication | this hospital) × revision_cost
```

Domestic options are ranked by **expected cost**, not sticker price, using each
hospital's own CMS-published complication rate. The ranking inverts often enough
that `rank_inversion` in the response names a real pair where the cheaper hospital
costs more.

The complication premium is priced actuarially — expected covered loss plus a 30%
load — where covered loss is medical revision **plus trip disruption**, since a
complication abroad also means extra hotel nights and a rebooked flight. That's why
live hotel rates feed the insurance price and not just the line items.

`cost_distribution` runs 10,000 draws and returns p50/p95/p99 both covered and
uncovered. Insurance is negative expected value by construction — the load
guarantees it. What the premium buys is the tail, and `tail_protection` is that number.

## Deploy

`render.yaml` at the repo root — connect the repo on Render and it picks it up.
`Dockerfile` here if you'd rather run it anywhere else.
