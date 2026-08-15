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


@app.get("/")
def root():
    try:
        spread = pricing.price_spread("Total Knee Replacement")
        counts = {
            "us_hospitals": len(pricing.us_hospitals()),
            "international_hospitals": len(pricing.intl_hospitals()),
            "countries": len(pricing.destinations()),
            "insurance_plans": len(pricing.plans()),
        }
    except Exception:
        spread, counts = None, {}
    return {
        "service": "MedMap API",
        "docs": "/docs",
        "data": counts,
        "headline": (
            f"Same knee replacement: ${spread['min']:,.0f} to ${spread['max']:,.0f} "
            f"across {spread['hospital_count']:,} US hospitals ({spread['multiple']}x)"
            if spread else None
        ),
        "sources": [
            "CMS Medicare Inpatient Hospitals - by Provider and Service",
            "CMS Complications and Deaths - Hospital (COMP_HIP_KNEE)",
            "CMS Marketplace Plan Attributes PUF PY2026",
        ],
        "endpoints": {
            "GET  /health": "liveness",
            "GET  /procedures": "available procedures",
            "GET  /plans?state=&q=": "real ACA plan lookup",
            "POST /intake": "free text -> structured facts",
            "POST /quote/domestic": "US hospitals ranked by expected cost",
            "POST /quote/international": "bundled international options",
            "POST /explain": "narrative payload for the LLM layer",
            "POST /checkout": "three-way payment split",
        },
    }


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


class ExplainRequest(BaseModel):
    session_id: str
    procedure_name: str = "Total Knee Replacement"


@app.post("/explain")
def explain(req: ExplainRequest):
    """Compact, pre-computed narrative payload for the LLM layer.

    Deliberately small and pre-formatted: the model gets facts and a strict
    instruction, so it writes prose and never does arithmetic.
    """
    try:
        s = session(req.session_id)
        dom, total = pricing.domestic_options(
            req.procedure_name, s["deductible"], s["coverage"], s["facts"],
            plan_id=s.get("plan_id")
        )
        intl, risk_factor, drivers = pricing.international_options(
            req.procedure_name, s["deductible"], s["facts"], s["coverage"],
            dom[0]["expected_cost"] if dom else None
        )
        spread = pricing.price_spread(req.procedure_name)
        inv = pricing.rank_inversion(dom)
        best_us, best_intl = dom[0], intl[0]
        eligible = [o for o in intl if not o["excluded_by_constraint"]]
        best_eligible = eligible[0] if eligible else best_intl

        facts = {
            "patient": {
                "conditions": [f["fact"] for f in s["facts"]],
                "coverage": s["coverage"],
                "risk_multiplier": risk_factor,
            },
            "us_market": {
                "hospitals_analyzed": total,
                "cheapest_price": spread["min"],
                "most_expensive_price": spread["max"],
                "price_multiple": spread["multiple"],
            },
            "best_us_option": {
                "name": best_us["name"], "state": best_us["state"],
                "sticker_price": best_us["base_cost"],
                "your_cost": best_us["out_of_pocket"],
                "complication_rate_pct": round(best_us["complication_rate"] * 100, 1),
                "expected_cost": best_us["expected_cost"],
            },
            "rank_inversion": inv["note"] if inv else None,
            "best_abroad": {
                "hospital": best_eligible["name"], "country": best_eligible["country"],
                "flight_hours": best_eligible["flight_hours"],
                "procedure": best_eligible["base_cost"],
                "travel": best_eligible["travel_cost"],
                "coverage_premium": best_eligible["warranty_cost"],
                "total": best_eligible["true_cost"],
                "savings_vs_best_us": best_eligible["savings_vs_domestic"],
                "worst_case_without_coverage": best_eligible["distribution"]["p99_uncovered"],
                "worst_case_with_coverage": best_eligible["distribution"]["p99"],
                "tail_removed": best_eligible["distribution"]["tail_protection"],
                "cost_of_that_certainty": best_eligible["distribution"]["cost_of_certainty"],
            },
            "excluded_by_your_constraints": [
                {"country": o["country"], "total": o["true_cost"],
                 "flight_hours": o["flight_hours"]}
                for o in intl if o["excluded_by_constraint"]
            ][:3],
        }

        return {
            "facts": facts,
            "instruction": (
                "You are explaining a surgery cost analysis to a patient. Using ONLY the "
                "numbers in `facts`, write 4 short sentences: (1) what the same procedure "
                "costs across US hospitals, (2) why the recommended hospital ranks first "
                "given its complication rate, (3) whether going abroad is worth it for this "
                "patient and why, (4) what the coverage premium buys in terms of worst case. "
                "Never calculate, estimate, round, or introduce any number not present in "
                "`facts`. Plain language, no jargon, no bullet points."
            ),
        }
    except Exception:
        return {"facts": {}, "instruction": "", "degraded": True}


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
