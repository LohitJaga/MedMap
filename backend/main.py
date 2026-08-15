from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uuid

import extract
import pricing
import stubs

app = FastAPI(title="MedMap API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SESSIONS = {}


class IntakeRequest(BaseModel):
    session_id: str
    text: str


class DomesticRequest(BaseModel):
    session_id: str
    procedure_name: str
    user_deductible: float
    coverage: str = "standard"
    state: str | None = None
    plan_id: str | None = None


class InternationalRequest(BaseModel):
    session_id: str
    procedure_name: str


class CheckoutRequest(BaseModel):
    session_id: str
    hospital_id: str


def session(sid):
    return SESSIONS.setdefault(
        sid, {"facts": [], "deductible": 5000.0, "coverage": "standard", "quotes": {}}
    )


@app.get("/health")
def health():
    return {"ok": True}


@app.post("/intake")
def intake(req: IntakeRequest):
    try:
        parsed = extract.parse(req.text)
    except Exception:
        return stubs.intake(req.session_id)

    s = session(req.session_id)
    s["facts"] = parsed["facts"]
    s["deductible"] = parsed["user_deductible"]
    s["coverage"] = parsed["coverage"]
    s["procedure"] = parsed["procedure_name"]
    return {"session_id": req.session_id, **parsed}


@app.get("/procedures")
def procedures():
    return {"procedures": [
        {"procedure_name": "Total Knee Replacement", "category": "orthopedic"},
        {"procedure_name": "Total Hip Replacement", "category": "orthopedic"},
    ]}


@app.get("/plans")
def plan_search(state: str | None = None, q: str | None = None, limit: int = 50):
    """Real ACA marketplace plans — feeds the plan dropdown.

    healthcare.gov covers 30 federal-marketplace states. State-run exchanges
    (NY, CA, and others) are not in this dataset.
    """
    try:
        found = pricing.find_plans(state, q, limit)
        return {"plans": found, "count": len(found),
                "source": "CMS Plan Attributes PUF, PY2026"}
    except Exception:
        return {"plans": [], "count": 0, "degraded": True}


@app.post("/quote/domestic")
def quote_domestic(req: DomesticRequest):
    try:
        s = session(req.session_id)
        options, total = pricing.domestic_options(
            req.procedure_name, req.user_deductible, req.coverage, s["facts"], req.state,
            plan_id=req.plan_id,
        )
        if not options:
            return stubs.domestic()
        s["deductible"] = req.user_deductible
        s["coverage"] = req.coverage
        s["plan_id"] = req.plan_id
        s["procedure"] = req.procedure_name
        s["baseline"] = options[0]["expected_cost"]
        return {
            "options": options,
            "coverage": req.coverage,
            "plan": pricing.coverage_terms(req.coverage, req.plan_id),
            "hospitals_considered": total,
            "price_spread": pricing.price_spread(req.procedure_name),
            "rank_inversion": pricing.rank_inversion(options),
            "source": "CMS Medicare Inpatient Hospitals + Hospital Complications and Deaths",
        }
    except Exception:
        return stubs.domestic()


@app.post("/quote/international")
def quote_international(req: InternationalRequest):
    try:
        s = session(req.session_id)
        options, risk_factor, drivers = pricing.international_options(
            req.procedure_name, s["deductible"], s["facts"], s["coverage"], s.get("baseline")
        )
        if not options:
            return stubs.international()
        s["quotes"] = {o["hospital_id"]: o for o in options}
        return {
            "options": options,
            "risk_factor": risk_factor,
            "risk_drivers": [d["fact"] for d in drivers],
        }
    except Exception:
        return stubs.international()


@app.post("/checkout")
def checkout(req: CheckoutRequest):
    try:
        option = session(req.session_id)["quotes"][req.hospital_id]
        items = pricing.checkout_split(option)
        return {
            "order_id": f"MM-{uuid.uuid4().hex[:6].upper()}",
            "line_items": items,
            "total": round(sum(i["amount"] for i in items), 2),
        }
    except Exception:
        return stubs.checkout()
