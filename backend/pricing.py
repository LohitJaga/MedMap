import json
import random
from pathlib import Path

DATA = Path(__file__).parent / "data"

SAFETY_THRESHOLD = 8.6
DRAWS = 10000
POLICY_LIMIT = 150000
LOAD_FACTOR = 0.30

# CMS national averages, used as the fallback and as the international baseline.
NATIONAL_COMPLICATION_RATE = 0.024
REVISION_COST = {
    "Total Knee Replacement": 75000,
    "Total Hip Replacement": 78000,
}

COVERAGE = {
    "uninsured": {"coinsurance": 1.0, "oop_max": float("inf"), "self_pay_discount": 0.65},
    "high_deductible": {"coinsurance": 0.30, "oop_max": 17400.0, "self_pay_discount": 1.0},
    "standard": {"coinsurance": 0.20, "oop_max": 9200.0, "self_pay_discount": 1.0},
}

RISK_WEIGHTS = {
    "diabetes": 0.35,
    "heart_condition": 0.40,
    "smoker": 0.30,
    "obesity": 0.25,
    "blood_thinners": 0.25,
    "age_over_70": 0.35,
    "age_over_60": 0.20,
}

RISK_LABELS = {
    "diabetes": "diabetes",
    "heart_condition": "a cardiac history",
    "smoker": "smoking",
    "obesity": "elevated BMI",
    "blood_thinners": "blood thinners",
    "age_over_70": "age over 70",
    "age_over_60": "age over 60",
}

_cache = {}


def _load(name):
    if name not in _cache:
        with open(DATA / name, encoding="utf-8") as f:
            _cache[name] = json.load(f)
    return _cache[name]


def us_hospitals():
    return _load("us_hospitals.json")


def intl_hospitals():
    return [h for h in _load("hospitals.json") if h["location_type"] == "international"]


def destinations():
    return {d["country"]: d for d in _load("destinations.json")}


def out_of_pocket(base_cost, deductible_remaining, coverage="standard"):
    c = COVERAGE.get(coverage, COVERAGE["standard"])
    billed = base_cost * c["self_pay_discount"]
    if billed <= deductible_remaining:
        return round(billed, 2)
    total = deductible_remaining + c["coinsurance"] * (billed - deductible_remaining)
    return round(min(total, c["oop_max"]), 2)


def patient_risk_factor(facts):
    factor, drivers = 1.0, []
    for f in facts:
        w = RISK_WEIGHTS.get(f.get("category"))
        if w:
            factor += w
            drivers.append(f)
    return round(factor, 3), drivers


def warranty_cost(procedure, p_comp):
    """Actuarial: expected covered loss plus a 30% load. Not a made-up multiplier."""
    loss = REVISION_COST.get(procedure, 75000)
    return round(p_comp * min(loss, POLICY_LIMIT) * (1 + LOAD_FACTOR), 2)


def domestic_options(procedure, deductible, coverage="standard", facts=(), state=None, limit=40):
    """Every US hospital with both a real price and a real CMS complication rate.

    Sorted by EXPECTED cost, not sticker price. The ranking usually inverts.
    """
    risk_factor, _ = patient_risk_factor(facts)
    revision = REVISION_COST.get(procedure, 75000)
    out = []

    for h in us_hospitals():
        base = h["prices"].get(procedure)
        if base is None:
            continue
        if state and h["state_or_country"] != state:
            continue

        p_comp = min(0.6, h["complication_rate"] * risk_factor)
        oop = out_of_pocket(base, deductible, coverage)
        revision_oop = out_of_pocket(revision, max(0.0, deductible - base), coverage)

        out.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "state": h["state_or_country"],
            "base_cost": base,
            "out_of_pocket": oop,
            "complication_rate": round(p_comp, 4),
            "complication_ci": [h["complication_ci_low"], h["complication_ci_high"]],
            "measure_denominator": h["measure_denominator"],
            "compared_to_national": h["compared_to_national"],
            "expected_cost": round(oop + p_comp * revision_oop, 2),
            "source_url": h["source_url"],
        })

    out.sort(key=lambda o: o["expected_cost"])
    return out[:limit], len(out)


def rank_inversion(options):
    """The finding: a cheaper sticker price can cost more once you weight by that
    hospital's own complication rate. Returns the sharpest such pair in the list."""
    best = None
    for cheap in options:
        for other in options:
            if cheap["base_cost"] >= other["base_cost"]:
                continue
            if cheap["expected_cost"] <= other["expected_cost"]:
                continue
            gap = cheap["expected_cost"] - other["expected_cost"]
            if not best or gap > best["gap"]:
                best = {"gap": gap, "cheaper_sticker": cheap, "better_value": other}
    if not best:
        return None
    a, b = best["cheaper_sticker"], best["better_value"]
    return {
        "cheaper_sticker": a,
        "better_value": b,
        "expected_cost_gap": round(best["gap"], 2),
        "note": (
            f"{a['name']} ({a['state']}) lists ${a['base_cost']:,.0f} — "
            f"${b['base_cost'] - a['base_cost']:,.0f} less than {b['name']} ({b['state']}). "
            f"But its complication rate is {a['complication_rate']:.1%} vs "
            f"{b['complication_rate']:.1%}, so it costs ${best['gap']:,.0f} more in expectation."
        ),
    }


def price_spread(procedure):
    """Headline stat: what the same procedure costs across the country."""
    prices = [h["prices"][procedure] for h in us_hospitals() if procedure in h["prices"]]
    if not prices:
        return None
    prices.sort()
    lo, hi = prices[0], prices[-1]
    return {
        "hospital_count": len(prices),
        "min": lo,
        "max": hi,
        "median": prices[len(prices) // 2],
        "multiple": round(hi / lo, 1),
    }


def cost_distribution(fixed, premium, p_comp, procedure, seed=7):
    """Total cost is a random variable. The procedure is certain; the complication isn't."""
    mean_loss = REVISION_COST.get(procedure, 75000)
    rng = random.Random(seed)
    uncovered, covered = [], []

    for _ in range(DRAWS):
        loss = rng.lognormvariate(0, 0.55) * mean_loss if rng.random() < p_comp else 0.0
        uncovered.append(fixed + loss)
        covered.append(fixed + premium + max(0.0, loss - POLICY_LIMIT))

    def pct(xs, q):
        s = sorted(xs)
        return round(s[int(q * (len(s) - 1))], 2)

    p99_unc, p99_cov = pct(uncovered, 0.99), pct(covered, 0.99)
    return {
        "complication_probability": round(p_comp, 4),
        "expected_cost_uncovered": round(sum(uncovered) / DRAWS, 2),
        "expected_cost_covered": round(sum(covered) / DRAWS, 2),
        "cost_of_certainty": round((sum(covered) - sum(uncovered)) / DRAWS, 2),
        "tail_protection": round(p99_unc - p99_cov, 2),
        "p50": pct(covered, 0.50),
        "p95": pct(covered, 0.95),
        "p99": p99_cov,
        "p95_uncovered": pct(uncovered, 0.95),
        "p99_uncovered": p99_unc,
        "worst_case_uncovered": round(max(uncovered), 2),
    }


def _reasoning(o, dest, drivers, max_flight, baseline, cheaper_excluded):
    parts = []
    if max_flight and o["flight_hours"] > max_flight:
        parts.append(f"{o['flight_hours']:.0f}h flight exceeds your {max_flight:.0f}h limit")
    elif cheaper_excluded:
        parts.append(f"cheaper options exist ({cheaper_excluded}) but exceed your flight limit")
    else:
        parts.append(f"{o['flight_hours']:.1f}h from JFK")

    if drivers:
        labels = [RISK_LABELS.get(d["category"], d["category"]) for d in drivers[:2]]
        parts.append(f"premium is above baseline for {' and '.join(labels)}")
    else:
        parts.append("premium is at baseline")

    saved = baseline - o["true_cost"]
    if saved > 0:
        parts.append(f"saves ${saved:,.0f} against your best US expected cost")
    return ". ".join(p[0].upper() + p[1:] for p in parts) + "."


def international_options(procedure, deductible, facts=(), coverage="standard", baseline=None):
    risk_factor, drivers = patient_risk_factor(facts)
    dests = destinations()

    if baseline is None:
        dom, _ = domestic_options(procedure, deductible, coverage, facts, limit=1)
        baseline = dom[0]["expected_cost"] if dom else 0.0

    max_flight = None
    for f in facts:
        if f.get("category") == "max_flight_hours":
            max_flight = float(f["value"])

    raw = []
    for h in intl_hospitals():
        base = h["prices"].get(procedure)
        dest = dests.get(h["state_or_country"])
        if base is None or not dest:
            continue
        # No CMS equivalent abroad — scaled from the national rate by destination risk.
        p_comp = min(0.6, NATIONAL_COMPLICATION_RATE * (dest["risk_multiplier"] / 0.024) * risk_factor)
        travel = float(dest["flight_cost"]) + float(dest["lodging_cost_recovery_stay"])
        premium = warranty_cost(procedure, p_comp)
        raw.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "country": h["state_or_country"],
            "flight_hours": dest["flight_hours_from_jfk"],
            "base_cost": float(base),
            "travel_cost": round(travel, 2),
            "warranty_cost": premium,
            "true_cost": round(base + travel + premium, 2),
            "savings_vs_domestic": round(baseline - (base + travel + premium), 2),
            "complication_rate": round(p_comp, 4),
            "complication_source": "estimated from national rate; no CMS equivalent abroad",
            "accreditation": h["accreditation"],
            "distribution": cost_distribution(base + travel, premium, p_comp, procedure),
            "_dest": dest,
        })

    raw.sort(key=lambda o: o["true_cost"])
    eligible = [o for o in raw if not max_flight or o["flight_hours"] <= max_flight]
    excluded = [o for o in raw if max_flight and o["flight_hours"] > max_flight]

    options = []
    for o in eligible + excluded:
        dest = o.pop("_dest")
        o["excluded_by_constraint"] = bool(max_flight and o["flight_hours"] > max_flight)
        ce = excluded[0]["country"] if (eligible and o is eligible[0] and excluded
                                        and excluded[0]["true_cost"] < o["true_cost"]) else None
        o["reasoning"] = _reasoning(o, dest, drivers, max_flight, baseline, ce)
        options.append(o)

    return options, risk_factor, drivers


def checkout_split(option):
    return [
        {"payee": option["name"], "label": "Procedure", "amount": option["base_cost"]},
        {"payee": "Travel partner", "label": "Round-trip JFK + recovery stay",
         "amount": option["travel_cost"]},
        {"payee": "Complication coverage underwriter", "label": "180-day complication policy",
         "amount": option["warranty_cost"]},
    ]
