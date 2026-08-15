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

# Cost of treating a complication. Hip/knee are revision surgery; the rest are
# the typical readmission/reoperation cost for that procedure.
REVISION_COST = {
    "Total Knee Replacement": 75000,
    "Total Hip Replacement": 78000,
    "Coronary Artery Bypass": 42000,
    "Spinal Fusion": 68000,
    "Gallbladder Removal": 24000,
    "Hysterectomy": 27000,
    "Bowel Resection": 46000,
    "Cardiac Valve Replacement": 51000,
    "Shoulder Replacement": 58000,
}

# CMS publishes a per-hospital complication rate for hip/knee only. Everything
# else falls back to the published national rate, and the API says so.
PROCEDURE_NATIONAL_RATE = {
    "Coronary Artery Bypass": 0.140,
    "Spinal Fusion": 0.062,
    "Gallbladder Removal": 0.031,
    "Hysterectomy": 0.038,
    "Bowel Resection": 0.115,
    "Cardiac Valve Replacement": 0.121,
    "Shoulder Replacement": 0.041,
}


def has_per_hospital_rate(procedure):
    return "Knee" in procedure or "Hip" in procedure

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

        per_hospital = has_per_hospital_rate(procedure)
        base_rate = (h["complication_rate"] if per_hospital
                     else PROCEDURE_NATIONAL_RATE.get(procedure, NATIONAL_COMPLICATION_RATE))
        p_comp = min(0.6, base_rate * risk_factor)
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
            "complication_ci": ([h["complication_ci_low"], h["complication_ci_high"]]
                                if per_hospital else None),
            "complication_source": ("CMS per-hospital measure" if per_hospital
                                    else "national average — CMS publishes no "
                                         "per-hospital rate for this procedure"),
            "measure_denominator": h["measure_denominator"] if per_hospital else None,
            "compared_to_national": h["compared_to_national"] if per_hospital else None,
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


def _median(xs):
    s = sorted(xs)
    return s[len(s) // 2] if s else None


def _domestic_ratio(procedure):
    """How this procedure costs relative to a knee replacement, from real CMS
    medians. Used to scale international pricing where none is published."""
    if procedure == "Total Knee Replacement":
        return 1.0
    hs = us_hospitals()
    target = _median([h["prices"][procedure] for h in hs if procedure in h["prices"]])
    knee = _median([h["prices"]["Total Knee Replacement"] for h in hs
                    if "Total Knee Replacement" in h["prices"]])
    if not target or not knee:
        return None
    return round(target / knee, 4)


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

    ratio = _domestic_ratio(procedure)
    raw = []
    for h in intl_hospitals():
        base = h["prices"].get(procedure)
        scaled = False
        if base is None and ratio:
            # No published international price for this procedure, so scale that
            # hospital's own knee price by the domestic cost ratio between them.
            knee = h["prices"].get("Total Knee Replacement")
            if knee:
                base = round(knee * ratio, 2)
                scaled = True
        dest = dests.get(h["state_or_country"])
        if base is None or not dest:
            continue
        # No CMS equivalent abroad — the procedure's national rate scaled by
        # destination risk and the patient's own comorbidities.
        national = PROCEDURE_NATIONAL_RATE.get(procedure, NATIONAL_COMPLICATION_RATE)
        p_comp = min(0.6, national * (dest["risk_multiplier"] / 0.024) * risk_factor)
        trip = travel.trip_cost(dest)
        disruption = disruption_cost(trip["nightly_rate"], trip["flight_cost"])
        premium = warranty_cost(procedure, p_comp, disruption)
        total = base + trip["travel_cost"] + premium
        raw.append({
            "hospital_id": h["hospital_id"],
            "name": h["name"],
            "city": h["city"],
            "country": h["state_or_country"],
            "lat": dest.get("lat"),
            "lon": dest.get("lon"),
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
            "price_basis": ("scaled from this hospital's published knee pricing"
                            if scaled else "published international price"),
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


def _dest_for_hospital(hospital_id):
    for h in intl_hospitals():
        if h["hospital_id"] == hospital_id:
            return destinations().get(h["state_or_country"]), h
    return None, None


def flights_for(hospital_id):
    dest, h = _dest_for_hospital(hospital_id)
    if not dest:
        return None
    out = travel.flight_options(dest)
    out["hospital_id"] = hospital_id
    out["hospital_name"] = h["name"]
    out["country"] = h["state_or_country"]
    return out


def get_flight(hospital_id, flight_id):
    found = flights_for(hospital_id)
    if not found:
        return None
    return next((f for f in found["options"] if f["flight_id"] == flight_id), None)


# OSM carries no prices, but it does carry the hotel's name and sometimes a star
# rating. Brand tier is real information, so the destination's average nightly
# rate is scaled by it rather than applied flat to every property.
BRAND_TIERS = [
    (2.4, ["ritz", "four seasons", "st. regis", "peninsula", "mandarin oriental",
           "shangri", "conrad", "waldorf", "park hyatt", "raffles"]),
    (1.8, ["sheraton", "hilton", "marriott", "hyatt", "intercontinental", "westin",
           "sofitel", "radisson blu", "pullman", "le meridien", "kempinski",
           "crowne plaza", "taj ", "oberoi", "swissotel", "grand hyatt"]),
    (1.35, ["novotel", "courtyard", "doubletree", "holiday inn", "radisson",
            "mercure", "four points", "aloft", "park inn", "residency", "suites"]),
    (0.75, ["hostel", "guesthouse", "guest house", "inn ", "lodge", "motel",
            "backpack", "budget", "super ", "ibis", "oyo", "b&b"]),
]


def _hotel_tier(name, stars):
    n = (name or "").lower()
    for mult, keys in BRAND_TIERS:
        if any(k in n for k in keys):
            return mult
    if stars:
        try:
            s = float(str(stars).split("-")[0])
            return {1: 0.7, 2: 0.85, 3: 1.1, 4: 1.6, 5: 2.3}.get(int(s), 1.0)
        except (TypeError, ValueError):
            pass
    return 1.0


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
    # OSM tagging is crowd-sourced; restaurants and shops occasionally carry
    # tourism=hotel. Drop the obvious ones rather than offer them as lodging.
    NOT_LODGING = ("restaurant", "snacks", "sweet", "cafe", "coffee", "bakery",
                   "bar &", "pharmacy", "clinic", "hospital", "spa &")

    out = []
    for h in entry["hotels"]:
        if any(w in (h.get("name") or "").lower() for w in NOT_LODGING):
            continue
        tier = _hotel_tier(h.get("name"), h.get("stars"))
        # Being within walking distance of a major hospital carries a small
        # premium; beyond a couple of miles it comes back off.
        prox = 1.0 + max(-0.12, min(0.10, (1.2 - float(h["distance_miles"])) * 0.08))
        nightly = round(h["nightly_rate"] * tier * prox, 2)
        out.append({
            **h,
            "nightly_rate": nightly,
            "tier": ("upscale" if tier >= 1.8 else
                     "midscale" if tier >= 1.3 else
                     "budget" if tier < 0.9 else "standard"),
            "nights": nights,
            "total": round(nightly * nights, 2),
        })
    out.sort(key=lambda h: h["distance_miles"])
    return {
        "hospital_id": entry["hospital_id"],
        "hospital_name": entry["hospital_name"],
        "hospital_lat": entry["hospital_lat"],
        "hospital_lon": entry["hospital_lon"],
        "nights": nights,
        "hotels": out,
        "source": ("OpenStreetMap for names, locations and distances; nightly rate is "
                   "the destination average scaled by the hotel's brand tier and "
                   "distance to the hospital"),
    }


def checkout_split(option, hotel=None, flight=None):
    if hotel or flight:
        # Anything explicitly chosen is billed by name; the rest falls back to
        # the bundled estimate for that leg.
        return [
            {"payee": option["name"], "label": "Procedure", "amount": option["base_cost"]},
            {"payee": flight["carrier"] if flight else "Airline",
             "label": (f"{flight['origin']}→{flight['destination']} round trip, "
                       f"{flight['duration']}, "
                       f"{'non-stop' if flight['stops'] == 0 else str(flight['stops']) + ' stop'}"
                       if flight else "Round-trip JFK"),
             "amount": flight["price"] if flight else option.get("flight_cost", 0)},
            {"payee": hotel["name"] if hotel else "Lodging partner",
             "label": (f"{hotel['nights']} nights, {hotel['distance_miles']} mi from hospital"
                       if hotel else "Recovery stay"),
             "amount": hotel["total"] if hotel else option.get("lodging_cost", 0)},
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
