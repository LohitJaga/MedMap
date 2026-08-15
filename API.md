# API Contract

Base URL: `http://localhost:8000` (deployed URL posted in the group chat once live)

All endpoints return JSON. The backend never returns a 5xx to the frontend — if something fails
internally it returns stub data with `"degraded": true`. The frontend can ignore that flag.

Every request carries `session_id` (any stable string the frontend generates once per visit).

---

## POST /intake

Free-text intake. Extracts structured facts from what the user typed.

**Request**
```json
{
  "session_id": "abc123",
  "text": "I need a knee replacement. BCBS PPO, about $5k deductible left. I'm 62, type 2 diabetic. Can't do more than an 8 hour flight."
}
```

**Response**
```json
{
  "session_id": "abc123",
  "procedure_name": "Total Knee Replacement",
  "user_deductible": 5000.0,
  "insurance": "BCBS PPO",
  "facts": [
    { "fact": "type 2 diabetic", "category": "medical", "confidence": 0.95 },
    { "fact": "age 62", "category": "medical", "confidence": 0.95 },
    { "fact": "max 8 hour flight", "category": "travel", "confidence": 0.90 }
  ]
}
```

`confidence` is 0–1. Facts stated directly score high; hedged statements score low. The frontend
can render these as chips with the confidence shown — it's a demo highlight.

---

## GET /procedures

Populates the fallback dropdown.

**Response**
```json
{ "procedures": [ { "procedure_name": "Total Knee Replacement", "category": "orthopedic" } ] }
```

---

## POST /quote/domestic

**Request**
```json
{ "session_id": "abc123", "procedure_name": "Total Knee Replacement", "user_deductible": 5000.0 }
```

Optional `"state": "NY"` filters to one state. `"coverage"` is `uninsured` | `high_deductible` | `standard`.

**Response**
```json
{
  "options": [
    {
      "hospital_id": "210024",
      "name": "University Of Md St Joseph Medical Center",
      "city": "Towson",
      "state": "MD",
      "base_cost": 20699.0,
      "out_of_pocket": 13455.0,
      "complication_rate": 0.035,
      "complication_ci": [0.024, 0.051],
      "measure_denominator": 412,
      "compared_to_national": "No Different Than the National Rate",
      "expected_cost": 15161.0,
      "source_url": "https://data.cms.gov/provider-data/dataset/ynj2-r877"
    }
  ],
  "hospitals_considered": 1173,
  "price_spread": { "hospital_count": 1173, "min": 20699, "median": 80133,
                    "max": 344847, "multiple": 16.7 },
  "rank_inversion": {
    "cheaper_sticker": { },
    "better_value": { },
    "expected_cost_gap": 1474.0,
    "note": "Adventist Healthcare Shady Grove (MD) lists $31,251 — $282 less than The Miriam Hospital (RI). But its complication rate is 5.7% vs 2.3%, so it costs $1,474 more in expectation."
  },
  "source": "CMS Medicare Inpatient Hospitals + Hospital Complications and Deaths"
}
```

**Sorted by `expected_cost`, not sticker price.** That's the whole point — `expected_cost = out_of_pocket + P(complication) × revision_cost`, using each hospital's own CMS-published complication rate.

Three things to render prominently:

- **`price_spread`** — the headline. 1,173 real hospitals, same procedure, 16.7x price range.
- **`rank_inversion.note`** — a plain-English sentence naming two real hospitals where the cheaper one costs more once you weight by risk. This is the demo money-shot. May be `null` if no inversion exists in the returned slice.
- **`complication_ci`** — real published confidence interval. A hospital with 30 discharges has a wide one; render it as a range, not a point.

---

## POST /quote/international

**Request**
```json
{ "session_id": "abc123", "procedure_name": "Total Knee Replacement" }
```

**Response**
```json
{
  "options": [
    {
      "hospital_id": "INT_001",
      "name": "San Jose Medical Center",
      "city": "San Jose",
      "country": "Costa Rica",
      "flight_hours": 5.5,
      "base_cost": 12000.0,
      "travel_cost": 1200.0,
      "warranty_cost": 840.0,
      "true_cost": 14040.0,
      "savings_vs_domestic": 30960.0,
      "safety_score": 9.1,
      "reasoning": "Costa Rica over India — 5.5 hour flight vs 16, and you said 8 hours max. Premium is above baseline because you're diabetic, stated directly.",
      "source_url": "https://..."
    }
  ]
}
```

Sorted by `true_cost` ascending. `reasoning` is always present — render it prominently, it's the
money shot of the demo.

---

## POST /checkout

**Request**
```json
{ "session_id": "abc123", "hospital_id": "INT_001" }
```

**Response**
```json
{
  "order_id": "MM-8F3A21",
  "line_items": [
    { "payee": "San Jose Medical Center", "label": "Total Knee Replacement", "amount": 12000.0 },
    { "payee": "Airline / lodging partner", "label": "Round-trip JFK + 10 nights", "amount": 1200.0 },
    { "payee": "Complication coverage underwriter", "label": "180-day complication policy", "amount": 840.0 }
  ],
  "total": 14040.0
}
```

`line_items` always sums to `total`. Render as the escrow split — one payment fanning out to three payees.
