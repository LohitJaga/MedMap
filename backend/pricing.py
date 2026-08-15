import json
from pathlib import Path

DATA = Path(__file__).parent / "data"

DEFAULT_COINSURANCE = 0.20
DEFAULT_OOP_MAX = 9200.0
SAFETY_THRESHOLD = 8.6

RISK_WEIGHTS = {
    "diabetes": 0.35,
    "heart_condition": 0.40,
    "smoker": 0.30,
    "obesity": 0.25,
    "blood_thinners": 0.25,
    "age_over_70": 0.35,
    "age_over_60": 0.20,
    "prior_surgery_same_site": 0.20,
}

RISK_LABELS = {
    "diabetes": "diabetes",
    "heart_condition": "a cardiac history",
    "smoker": "smoking",
    "obesity": "elevated BMI",
    "blood_thinners": "blood thinners",
    "age_over_70": "age over 70",
    "age_over_60": "age over 60",
    "prior_surgery_same_site": "prior surgery at the same site",
}


def _load(name):
    with open(DATA / name, encoding="utf-8") as f:
        return json.load(f)


def hospitals():
    return _load("hospitals.json")


def destinations():
    return {d["country"]: d for d in _load("destinations.json")}


COVERAGE = {
    "uninsured": {"coinsurance": 1.0, "oop_max": float("inf"), "self_pay_discount": 0.65},
    "high_deductible": {"coinsurance": 0.30, "oop_max": 17400.0, "self_pay_discount": 1.0},
    "standard": {"coinsurance": 0.20, "oop_max": 9200.0, "self_pay_discount": 1.0},
}


def out_of_pocket(base_cost, deductible_remaining, coverage="standard"):
    c = COVERAGE.get(coverage, COVERAGE["standard"])
    billed = base_cost * c["self_pay_discount"]
    if billed <= deductible_remaining:
        return round(billed, 2)
    after_deductible = billed - deductible_remaining
    total = deductible_remaining + c["coinsurance"] * after_deductible
    return round(min(total, c["oop_max"]), 2)


def patient_risk_factor(facts):
    """1.0 baseline. Each risk fact adds its weight scaled by how confident we are in it."""
    factor = 1.0
    drivers = []
    for f in facts:
        w = RISK_WEIGHTS.get(f.get("category"))
        if not w:
            continue
        contribution = w * f.get("confidence", 1.0)
        factor += contribution
        drivers.append((contribution, f))
    drivers.sort(key=lambda d: -d[0])
    return round(factor, 3), [d[1] for d in drivers]


def warranty_cost(base_cost, destination_risk, risk_factor):
    return round(base_cost * destination_risk * risk_factor, 2)


def domestic_options(procedure, deductible, coverage="standard"):
    kept, filtered = [], 0
    for h in hospitals():
        if h["location_type"] != "domestic" or procedure not in h["prices"]:
            continue
        if h["safety_score"] < SAFETY_THRESHOLD:
            filtered += 1
            continue
        base = float(h["prices"][procedure])
        kept.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "state": h["state_or_country"],
            "base_cost": base,
            "out_of_pocket": out_of_pocket(base, deductible, coverage),
            "safety_score": h["safety_score"],
            "accreditation": h["accreditation"],
            "source_url": h["source_url"],
        })
    kept.sort(key=lambda o: (o["out_of_pocket"], o["base_cost"]))
    return kept, filtered


def cheapest_domestic_oop(procedure, deductible, coverage="standard"):
    opts, _ = domestic_options(procedure, deductible, coverage)
    return opts[0]["out_of_pocket"] if opts else 0.0


def _reasoning(option, dest, risk_drivers, max_flight, baseline_oop, cheaper_but_excluded):
    parts = []

    if max_flight and dest["flight_hours_from_jfk"] > max_flight:
        parts.append(
            f"{dest['flight_hours_from_jfk']:.0f}h flight exceeds the {max_flight:.0f}h limit you gave"
        )
    elif cheaper_but_excluded:
        parts.append(
            f"cheaper options exist ({cheaper_but_excluded}) but sit outside your {max_flight:.0f}h flight limit"
        )
    else:
        parts.append(f"{dest['flight_hours_from_jfk']:.1f}h from JFK")

    if risk_drivers:
        top = risk_drivers[0]
        label = RISK_LABELS.get(top["category"], top["category"])
        conf = top.get("confidence", 1.0)
        if conf >= 0.85:
            parts.append(f"premium is above baseline because of {label}, which you stated directly")
        else:
            parts.append(
                f"premium reflects {label}, weighted down to {int(conf * 100)}% since you were unsure"
            )
    else:
        parts.append("premium is at baseline — no elevated risk factors on file")

    saved = baseline_oop - option["true_cost"]
    if saved > 0:
        parts.append(f"saves ${saved:,.0f} against your US out-of-pocket")

    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def international_options(procedure, deductible, facts, coverage="standard"):
    risk_factor, risk_drivers = patient_risk_factor(facts)
    dests = destinations()
    baseline_oop = cheapest_domestic_oop(procedure, deductible, coverage)

    max_flight = None
    for f in facts:
        if f.get("category") == "max_flight_hours":
            max_flight = float(f["value"])

    raw = []
    for h in hospitals():
        if h["location_type"] != "international" or procedure not in h["prices"]:
            continue
        if h["safety_score"] < SAFETY_THRESHOLD:
            continue
        dest = dests.get(h["state_or_country"])
        if not dest:
            continue
        base = float(h["prices"][procedure])
        travel = float(dest["flight_cost"]) + float(dest["lodging_cost_recovery_stay"])
        warranty = warranty_cost(base, dest["risk_multiplier"], risk_factor)
        raw.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "country": h["state_or_country"],
            "flight_hours": dest["flight_hours_from_jfk"],
            "base_cost": base,
            "travel_cost": round(travel, 2),
            "warranty_cost": warranty,
            "true_cost": round(base + travel + warranty, 2),
            "savings_vs_domestic": round(baseline_oop - (base + travel + warranty), 2),
            "safety_score": h["safety_score"],
            "accreditation": h["accreditation"],
            "risk_factor": risk_factor,
            "source_url": h["source_url"],
            "_dest": dest,
        })

    raw.sort(key=lambda o: o["true_cost"])

    if max_flight:
        eligible = [o for o in raw if o["flight_hours"] <= max_flight]
        excluded = [o for o in raw if o["flight_hours"] > max_flight]
        ordered = eligible + excluded
    else:
        eligible, excluded, ordered = raw, [], raw

    options = []
    for o in ordered:
        dest = o.pop("_dest")
        o["excluded_by_constraint"] = bool(max_flight and o["flight_hours"] > max_flight)
        cheaper_excluded = None
        if eligible and o is eligible[0] and excluded and excluded[0]["true_cost"] < o["true_cost"]:
            cheaper_excluded = excluded[0]["country"]
        o["reasoning"] = _reasoning(o, dest, risk_drivers, max_flight, baseline_oop, cheaper_excluded)
        options.append(o)

    return options, risk_factor, risk_drivers


def checkout_split(option):
    return [
        {"payee": option["name"], "label": "Procedure", "amount": option["base_cost"]},
        {"payee": "Travel partner", "label": "Round-trip JFK + recovery stay",
         "amount": option["travel_cost"]},
        {"payee": "Complication coverage underwriter", "label": "180-day complication policy",
         "amount": option["warranty_cost"]},
    ]
