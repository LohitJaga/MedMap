"""Fallback responses. Every endpoint drops to these instead of returning a 5xx."""

import uuid


def intake(session_id):
    return {
        "session_id": session_id,
        "procedure_name": "Total Knee Replacement",
        "user_deductible": 5000.0,
        "insurance": "BCBS PPO",
        "facts": [
            {"fact": "type 2 diabetic", "category": "diabetes", "confidence": 0.95},
            {"fact": "age 62", "category": "age_over_60", "confidence": 0.95},
            {"fact": "max 8 hour flight", "category": "max_flight_hours",
             "confidence": 0.90, "value": 8},
        ],
        "degraded": True,
    }


def domestic():
    return {
        "options": [
            {"hospital_id": "DOM_003", "name": "Newark Beth Israel Medical Center",
             "city": "Newark", "state": "NJ", "base_cost": 41000.0,
             "out_of_pocket": 9200.0, "safety_score": 8.8,
             "accreditation": "Leapfrog B", "source_url": ""},
        ],
        "filtered_count": 2,
        "filter_note": "2 hospitals hidden — safety grade below threshold",
        "degraded": True,
    }


def international():
    return {
        "options": [
            {"hospital_id": "INT_001", "name": "CIMA Hospital San Jose", "city": "San Jose",
             "country": "Costa Rica", "flight_hours": 5.5, "base_cost": 12000.0,
             "travel_cost": 1320.0, "warranty_cost": 462.0, "true_cost": 13782.0,
             "savings_vs_domestic": -4582.0, "safety_score": 9.1,
             "accreditation": "JCI accredited", "excluded_by_constraint": False,
             "reasoning": "5.5h from JFK. Premium is above baseline because of diabetes, "
                          "which you stated directly.",
             "source_url": ""},
        ],
        "degraded": True,
    }


def checkout():
    items = [
        {"payee": "CIMA Hospital San Jose", "label": "Procedure", "amount": 12000.0},
        {"payee": "Travel partner", "label": "Round-trip JFK + recovery stay", "amount": 1320.0},
        {"payee": "Complication coverage underwriter", "label": "180-day complication policy",
         "amount": 462.0},
    ]
    return {
        "order_id": f"MM-{uuid.uuid4().hex[:6].upper()}",
        "line_items": items,
        "total": round(sum(i["amount"] for i in items), 2),
        "degraded": True,
    }
