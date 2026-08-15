# Datasets

Owner: data lane. Backend reads these, nobody else writes them.

## hospitals.json

3 procedures × (6 domestic + 6 international).

```json
[
  {
    "hospital_id": "DOM_001",
    "name": "Apex Orthopedic Institute",
    "city": "Newark",
    "state_or_country": "NJ",
    "location_type": "domestic",
    "procedure_name": "Total Knee Replacement",
    "base_cost": 45000.00,
    "safety_score": 9.4,
    "source_url": "https://..."
  }
]
```

Procedures: Total Knee Replacement, Total Hip Replacement, Coronary Artery Bypass.

Sources — domestic: CMS chargemaster / Medicare Procedure Price Lookup.
International: published medical-tourism price lists (Patients Beyond Borders, hospital sites).

Safety scores must come from something real — Leapfrog Hospital Safety Grade or CMS star ratings
domestically, JCI accreditation internationally. Do not invent them.

`source_url` on every row. That field is what lets us say "these are real numbers" on stage.

## destinations.json

```json
[
  {
    "country": "Costa Rica",
    "flight_hours_from_jfk": 5.5,
    "lodging_cost_10_nights": 900.00,
    "visa_note": "No visa required for US citizens under 90 days",
    "why_patients_go": "JCI-accredited network, short flight, large English-speaking care staff"
  }
]
```

## procedures.json

```json
[ { "procedure_name": "Total Knee Replacement", "category": "orthopedic", "recovery_days": 42 } ]
```

**Deadline: hand off hospitals.json by 13:00, partial is fine.** Backend is blocked on real math until it lands.
