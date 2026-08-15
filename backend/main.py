from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid

app = FastAPI(title="MedMap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class IntakeRequest(BaseModel):
    session_id: str
    text: str


class DomesticRequest(BaseModel):
    session_id: str
    procedure_name: str
    user_deductible: float


class InternationalRequest(BaseModel):
    session_id: str
    procedure_name: str


class CheckoutRequest(BaseModel):
    session_id: str
    hospital_id: str


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/intake")
def intake(req: IntakeRequest):
    return {
        "session_id": req.session_id,
        "procedure_name": "Total Knee Replacement",
        "user_deductible": 5000.0,
        "insurance": "BCBS PPO",
        "facts": [
            {"fact": "type 2 diabetic", "category": "medical", "confidence": 0.95},
            {"fact": "age 62", "category": "medical", "confidence": 0.95},
            {"fact": "max 8 hour flight", "category": "travel", "confidence": 0.90},
        ],
        "degraded": True,
    }


@app.get("/procedures")
def procedures():
    return {
        "procedures": [
            {"procedure_name": "Total Knee Replacement", "category": "orthopedic"},
            {"procedure_name": "Total Hip Replacement", "category": "orthopedic"},
            {"procedure_name": "Coronary Artery Bypass", "category": "cardiac"},
        ]
    }


@app.post("/quote/domestic")
def quote_domestic(req: DomesticRequest):
    return {
        "options": [
            {
                "hospital_id": "DOM_001",
                "name": "Apex Orthopedic Institute",
                "city": "Newark",
                "state": "NJ",
                "base_cost": 45000.0,
                "out_of_pocket": 13000.0,
                "safety_score": 9.4,
                "source_url": "",
            },
            {
                "hospital_id": "DOM_002",
                "name": "Hudson Valley Surgical",
                "city": "White Plains",
                "state": "NY",
                "base_cost": 52000.0,
                "out_of_pocket": 14400.0,
                "safety_score": 9.0,
                "source_url": "",
            },
        ],
        "filtered_count": 2,
        "filter_note": "2 hospitals hidden — safety grade below threshold",
        "degraded": True,
    }


@app.post("/quote/international")
def quote_international(req: InternationalRequest):
    return {
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
                "reasoning": "Costa Rica over India — 5.5 hour flight vs 16, and you said 8 hours max.",
                "source_url": "",
            },
            {
                "hospital_id": "INT_002",
                "name": "Apollo Hospitals",
                "city": "Chennai",
                "country": "India",
                "flight_hours": 16.0,
                "base_cost": 8000.0,
                "travel_cost": 1800.0,
                "warranty_cost": 620.0,
                "true_cost": 10420.0,
                "savings_vs_domestic": 34580.0,
                "safety_score": 9.3,
                "reasoning": "Cheapest overall, but the 16 hour flight is outside the limit you gave.",
                "source_url": "",
            },
        ],
        "degraded": True,
    }


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    return {
        "order_id": f"MM-{uuid.uuid4().hex[:6].upper()}",
        "line_items": [
            {
                "payee": "San Jose Medical Center",
                "label": "Total Knee Replacement",
                "amount": 12000.0,
            },
            {
                "payee": "Airline / lodging partner",
                "label": "Round-trip JFK + 10 nights",
                "amount": 1200.0,
            },
            {
                "payee": "Complication coverage underwriter",
                "label": "180-day complication policy",
                "amount": 840.0,
            },
        ],
        "total": 14040.0,
        "degraded": True,
    }
