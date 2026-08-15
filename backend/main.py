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
    # Optional overrides so this endpoint works standalone, without /intake first.
    coverage: str | None = None
    user_deductible: float | None = None


class CheckoutRequest(BaseModel):
    session_id: str
    hospital_id: str
    hotel_name: str | None = None
    flight_id: str | None = None


def session(sid):
    # Defaults to uninsured: that is the population this product exists for, and it
    # keeps a frontend that skips /intake from silently comparing against the wrong
    # baseline. Any explicit coverage on a request overrides it.
    return SESSIONS.setdefault(
        sid, {"facts": [], "deductible": 5000.0, "coverage": "uninsured", "quotes": {}}
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
    try:
        return {"procedures": pricing._load("procedures.json"),
                "source": "CMS Medicare Inpatient Hospitals, by DRG"}
    except Exception:
        return {"procedures": [
            {"procedure_name": "Total Knee Replacement"},
            {"procedure_name": "Total Hip Replacement"},
        ], "degraded": True}


@app.get("/plans")
def plan_search(state: str | None = None, q: str | None = None, limit: int = 50):
    """Real ACA marketplace plans — feeds the plan dropdown.

    healthcare.gov covers 30 federal-marketplace states. State-run exchanges
    (NY, CA, and others) are not in this dataset.
    """
    try:
        found, scope = pricing.find_plans(state, q, limit)
        note = None
        if scope == "national" and state:
            note = (f"{state} runs its own exchange and is not on healthcare.gov — "
                    f"showing comparable plans nationally")
        return {"plans": found, "count": len(found), "scope": scope, "note": note,
                "source": "CMS Plan Attributes PUF, PY2026"}
    except Exception:
        return {"plans": [], "count": 0, "degraded": True}


@app.get("/flights")
def flights(hospital_id: str):
    """Three round-trip options from JFK to this hospital's city.

    Carriers on the route and the great-circle distance are real; fares are
    modelled from distance and schedules are not live.
    """
    try:
        found = pricing.flights_for(hospital_id)
        if not found:
            return {"hospital_id": hospital_id, "options": [], "degraded": True}
        return found
    except Exception:
        return {"hospital_id": hospital_id, "options": [], "degraded": True}


@app.get("/hotels")
def hotels(hospital_id: str, nights: int | None = None):
    """Real hotels near an international hospital, nearest first.

    Names, coordinates and distances are from OpenStreetMap. Rates are the
    destination's published mid-range average — OSM has no pricing.
    """
    try:
        found = pricing.hotels_for(hospital_id, nights)
        if not found:
            return {"hospital_id": hospital_id, "hotels": [], "degraded": True}
        return found
    except Exception:
        return {"hospital_id": hospital_id, "hotels": [], "degraded": True}


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
        if req.coverage:
            s["coverage"] = req.coverage
        if req.user_deductible is not None:
            s["deductible"] = req.user_deductible
        # If /quote/domestic hasn't run, derive the baseline now rather than
        # comparing against a stale default.
        baseline = s.get("baseline")
        if baseline is None:
            dom, _ = pricing.domestic_options(
                req.procedure_name, s["deductible"], s["coverage"], s["facts"], limit=1
            )
            baseline = dom[0]["expected_cost"] if dom else None
            s["baseline"] = baseline
        options, risk_factor, drivers = pricing.international_options(
            req.procedure_name, s["deductible"], s["facts"], s["coverage"], baseline
        )
        if not options:
            return stubs.international()
        s["quotes"] = {o["hospital_id"]: o for o in options}
        return {
            "options": options,
            "coverage": s["coverage"],
            "us_baseline_expected_cost": baseline,
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

        terms = pricing.coverage_terms(s["coverage"], s.get("plan_id"))
        chosen = pricing.get_plan(s["plan_id"]) if s.get("plan_id") else None

        facts = {
            "patient": {
                "conditions": [f["fact"] for f in s["facts"]],
                "coverage": s["coverage"],
                "risk_multiplier": risk_factor,
            },
            "insurance_plan": ({
                "issuer": chosen["issuer"],
                "plan": chosen["name"],
                "metal_level": chosen["metal"],
                "plan_type": chosen["plan_type"],
                "deductible": chosen["deductible"],
                "out_of_pocket_max": chosen["oop_max"],
                "coinsurance_pct": round(chosen["coinsurance"] * 100),
                "source": "CMS Marketplace Plan Attributes PUF, plan year 2026",
            } if chosen else {
                "plan": terms["source"],
                "out_of_pocket_max": (terms["oop_max"]
                                      if terms["oop_max"] < pricing.NO_CAP else None),
                "coinsurance_pct": round(terms["coinsurance"] * 100),
            }),
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
                "costs across US hospitals, (2) what their own insurance plan leaves them "
                "paying, naming the plan and its deductible and out-of-pocket max, "
                "(3) whether going abroad is worth it for this patient and why, "
                "(4) what the coverage premium buys in terms of worst case. "
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
        hotel = None
        if req.hotel_name:
            found = pricing.hotels_for(req.hospital_id)
            if found:
                hotel = next((h for h in found["hotels"] if h["name"] == req.hotel_name), None)
        flight = pricing.get_flight(req.hospital_id, req.flight_id) if req.flight_id else None
        items = pricing.checkout_split(option, hotel, flight)
        return {
            "order_id": f"MM-{uuid.uuid4().hex[:6].upper()}",
            "line_items": items,
            "total": round(sum(i["amount"] for i in items), 2),
        }
    except Exception:
        return stubs.checkout()
