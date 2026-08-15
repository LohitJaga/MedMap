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

**Response**
```json
{
  "options": [
    {
      "hospital_id": "DOM_001",
      "name": "Apex Orthopedic Institute",
      "city": "Newark",
      "state": "NJ",
      "base_cost": 45000.0,
      "out_of_pocket": 13000.0,
      "safety_score": 9.4,
      "source_url": "https://..."
    }
  ],
  "filtered_count": 2,
  "filter_note": "2 hospitals hidden — safety grade below threshold (Leapfrog)"
}
```

Sorted by `out_of_pocket` ascending.

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
