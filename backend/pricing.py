import json
import random
from pathlib import Path

import travel

DATA = Path(__file__).parent / "data"

SAFETY_THRESHOLD = 8.6
DRAWS = 10000
POLICY_LIMIT = 150000
DISTRIBUTION_DEPTH = 6
LOAD_FACTOR = 0.30

# CMS national averages, used as the fallback and as the international baseline.
NATIONAL_COMPLICATION_RATE = 0.024
REVISION_COST = {
    "Total Knee Replacement": 75000,
    "Total Hip Replacement": 78000,
}

# No true infinity — it isn't JSON-serializable and the uninsured path is the demo default.
NO_CAP = 10_000_000.0

COVERAGE = {
    "uninsured": {"coinsurance": 1.0, "oop_max": NO_CAP, "self_pay_discount": 0.65},
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


def plans():
    return _load("plans.json")


def _matches(p, q):
    if not q:
        return True
    hay = f"{p.get('issuer') or ''} {p.get('name') or ''} {p.get('metal') or ''}".lower()
    return all(tok in hay for tok in q.split())


def find_plans(state=None, query=None, limit=50):
    """Search real marketplace plans.

    healthcare.gov covers 30 states; NY, CA and other state-run exchanges are absent.
    Rather than return nothing for those, fall back to a national search and say so.
    """
    q = (query or "").lower().strip()
    all_plans = plans()

    in_state = [p for p in all_plans if state and p["state"] == state and _matches(p, q)]
    scope = "state"
    if not in_state:
        in_state = [p for p in all_plans if _matches(p, q)]
        scope = "national" if state else "all"

    # Cheapest deductible first, then a stable name order.
    in_state.sort(key=lambda p: (p["deductible"], p["issuer"] or "", p["name"] or ""))

    seen, out = set(), []
    for p in in_state:
        key = (p["issuer"], p["name"], p["deductible"], p["oop_max"])
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
        if len(out) >= limit:
            break
    return out, scope


def get_plan(plan_id):
    for p in plans():
        if p["plan_id"] == plan_id:
            return p
    return None


def coverage_terms(coverage="standard", plan_id=None):
    """A real ACA plan's own deductible / OOP max / coinsurance when we have one,
    otherwise the generic bucket."""
    if plan_id:
        p = get_plan(plan_id)
        if p:
            return {
                "coinsurance": p["coinsurance"],
                "oop_max": p["oop_max"],
                "deductible": p["deductible"],
                "self_pay_discount": 1.0,
                "source": f"{p['issuer']} — {p['name']} ({p['metal']})",
            }
    c = dict(COVERAGE.get(coverage, COVERAGE["standard"]))
    c["deductible"] = None
    c["capped"] = c["oop_max"] < NO_CAP
    c["source"] = f"{coverage} (generic assumption)"
    return c


def out_of_pocket(base_cost, deductible_remaining, coverage="standard", plan_id=None):
    c = coverage_terms(coverage, plan_id)
    if c.get("deductible") is not None:
        deductible_remaining = min(deductible_remaining, c["deductible"])
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


EXTRA_NIGHTS_ON_COMPLICATION = 14
FLIGHT_CHANGE_FEE = 450.0


def disruption_cost(nightly_rate, flight_cost):
    """A complication abroad isn't only medical. You miss the flight home and the
    recovery stay extends. Priced off the same live hotel and flight numbers."""
    extra_lodging = EXTRA_NIGHTS_ON_COMPLICATION * nightly_rate
    rebooking = FLIGHT_CHANGE_FEE + 0.4 * flight_cost
    return round(extra_lodging + rebooking, 2)


def warranty_cost(procedure, p_comp, disruption=0.0):
    """Actuarial: expected covered loss plus a 30% load.

    Covered loss = medical revision + trip disruption, because both are caused by
    the same event. Not a made-up multiplier.
    """
    medical = REVISION_COST.get(procedure, 75000)
    covered = min(medical + disruption, POLICY_LIMIT)
    return round(p_comp * covered * (1 + LOAD_FACTOR), 2)


def domestic_options(procedure, deductible, coverage="standard", facts=(), state=None,
                     limit=40, plan_id=None):
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
        oop = out_of_pocket(base, deductible, coverage, plan_id)
        revision_oop = out_of_pocket(revision, max(0.0, deductible - base), coverage, plan_id)

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


def cost_distribution(fixed, premium, p_comp, procedure, disruption=0.0, seed=7):
    """Total cost is a random variable. The procedure is certain; the complication isn't.

    A complication draws both a medical loss and the trip disruption it causes.
    """
    mean_loss = REVISION_COST.get(procedure, 75000) + disruption
    rng = random.Random(seed)
    uncovered, covered = [], []

    # mu = -sigma^2/2 makes E[multiplier] exactly 1, so the simulated mean loss
    # matches the mean the premium is priced against. Without this the draw runs
    # ~16% hot and the premium looks under-priced.
    sigma = 0.55
    mu = -(sigma ** 2) / 2

    for _ in range(DRAWS):
        loss = rng.lognormvariate(mu, sigma) * mean_loss if rng.random() < p_comp else 0.0
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
        trip = travel.trip_cost(dest)
        disruption = disruption_cost(trip["nightly_rate"], trip["flight_cost"])
        premium = warranty_cost(procedure, p_comp, disruption)
        total = base + trip["travel_cost"] + premium
        raw.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "country": h["state_or_country"],
            "flight_hours": dest["flight_hours_from_jfk"],
            "base_cost": float(base),
            "flight_cost": trip["flight_cost"],
            "lodging_cost": trip["lodging_cost"],
            "travel_cost": trip["travel_cost"],
            "travel_source": {"flights": trip["flight_source"], "hotels": trip["hotel_source"]},
            "warranty_cost": premium,
            "disruption_exposure": disruption,
            "true_cost": round(total, 2),
            "savings_vs_domestic": round(baseline - total, 2),
            "complication_rate": round(p_comp, 4),
            "complication_source": "estimated from national rate; no CMS equivalent abroad",
            "accreditation": h["accreditation"],
            "_fixed": base + trip["travel_cost"],
            "_disruption": disruption,
            "_dest": dest,
        })

    raw.sort(key=lambda o: o["true_cost"])
    eligible = [o for o in raw if not max_flight or o["flight_hours"] <= max_flight]
    excluded = [o for o in raw if max_flight and o["flight_hours"] > max_flight]

    options = []
    for idx, o in enumerate(eligible + excluded):
        dest = o.pop("_dest")
        fixed, disruption = o.pop("_fixed"), o.pop("_disruption")
        o["excluded_by_constraint"] = bool(max_flight and o["flight_hours"] > max_flight)
        ce = excluded[0]["country"] if (eligible and o is eligible[0] and excluded
                                        and excluded[0]["true_cost"] < o["true_cost"]) else None
        o["reasoning"] = _reasoning(o, dest, drivers, max_flight, baseline, ce)
        # 10k draws per option is the slowest thing here and only the leading few are
        # ever charted. Run the simulation on those; leave the rest null.
        o["distribution"] = (
            cost_distribution(fixed, o["warranty_cost"], o["complication_rate"],
                              procedure, disruption)
            if idx < DISTRIBUTION_DEPTH else None
        )
        options.append(o)

    return options, risk_factor, drivers


def hotels_for(hospital_id, nights=None):
    """Real hotels near a hospital, nearest first.

    Names, coordinates and distances come from OpenStreetMap. OSM has no pricing,
    so the nightly rate is the destination's published mid-range average and says so.
    """
    nights = nights or travel.RECOVERY_NIGHTS
    try:
        entry = _load("hotels.json").get(hospital_id)
    except Exception:
        entry = None
    if not entry:
        return None
    out = []
    for h in entry["hotels"]:
        out.append({
            **h,
            "nights": nights,
            "total": round(h["nightly_rate"] * nights, 2),
        })
    return {
        "hospital_id": entry["hospital_id"],
        "hospital_name": entry["hospital_name"],
        "hospital_lat": entry["hospital_lat"],
        "hospital_lon": entry["hospital_lon"],
        "nights": nights,
        "hotels": out,
        "source": "OpenStreetMap (names, locations, distances); rates are destination averages",
    }


def checkout_split(option, hotel=None):
    if hotel:
        # A specific hotel was chosen, so bill lodging by name and keep flights separate.
        return [
            {"payee": option["name"], "label": "Procedure", "amount": option["base_cost"]},
            {"payee": "Airline", "label": "Round-trip JFK",
             "amount": option.get("flight_cost", 0)},
            {"payee": hotel["name"],
             "label": f"{hotel['nights']} nights, {hotel['distance_miles']} mi from hospital",
             "amount": hotel["total"]},
            {"payee": "Complication coverage underwriter",
             "label": "180-day complication policy", "amount": option["warranty_cost"]},
        ]
    return [
        {"payee": option["name"], "label": "Procedure", "amount": option["base_cost"]},
        {"payee": "Travel partner", "label": "Round-trip JFK + recovery stay",
         "amount": option["travel_cost"]},
        {"payee": "Complication coverage underwriter", "label": "180-day complication policy",
         "amount": option["warranty_cost"]},
    ]
