"""Quality checks. Run before demoing: python qa.py

Fails loudly on anything a judge could catch — bad data, broken math, unstable
Monte Carlo, endpoints that 500 on garbage input.
"""

import json
import statistics as st
import sys

import pricing

PASS, FAIL, WARN = [], [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(f"{name}{' — ' + detail if detail else ''}")


def warn(name, detail=""):
    WARN.append(f"{name}{' — ' + detail if detail else ''}")


# ---------- data integrity ----------

def data_checks():
    hs = pricing.us_hospitals()
    check("hospitals loaded", len(hs) > 1000, f"{len(hs)}")

    missing = [h for h in hs if not h.get("name") or not h.get("state_or_country")]
    check("no hospitals missing name/state", not missing, f"{len(missing)} bad")

    bad_rate = [h for h in hs if not (0 < h["complication_rate"] < 0.5)]
    check("complication rates in (0, 50%)", not bad_rate, f"{len(bad_rate)} out of range")

    bad_ci = [h for h in hs
              if not (h["complication_ci_low"] <= h["complication_rate"] <= h["complication_ci_high"])]
    check("rate sits inside its own CI", not bad_ci, f"{len(bad_ci)} inverted")

    prices = [h["prices"]["Total Knee Replacement"] for h in hs]
    check("no zero or negative prices", min(prices) > 0, f"min {min(prices):,.0f}")
    check("no absurd prices (>$1M)", max(prices) < 1_000_000, f"max {max(prices):,.0f}")

    med = st.median(prices)
    warn("price distribution",
         f"min {min(prices):,.0f} / median {med:,.0f} / max {max(prices):,.0f}")

    tiny = [h for h in hs if h["measure_denominator"] < 25]
    if tiny:
        warn("low-volume hospitals", f"{len(tiny)} with <25 discharges — wide CIs, expected")

    dupes = len(hs) - len({h["hospital_id"] for h in hs})
    check("no duplicate hospital ids", dupes == 0, f"{dupes} dupes")

    ps = pricing.plans()
    check("plans loaded", len(ps) > 1000, f"{len(ps)}")
    bad_plan = [p for p in ps if p["deductible"] > p["oop_max"]]
    check("deductible never exceeds OOP max", not bad_plan, f"{len(bad_plan)} bad")
    bad_coins = [p for p in ps if not (0 <= p["coinsurance"] <= 1)]
    check("coinsurance in [0,1]", not bad_coins, f"{len(bad_coins)} bad")
    warn("plan states", f"{len({p['state'] for p in ps})} states — no NY/CA (state exchanges)")


# ---------- model behaviour ----------

def model_checks():
    proc = "Total Knee Replacement"

    # out-of-pocket must never exceed the plan's cap
    for cov in ("standard", "high_deductible"):
        cap = pricing.COVERAGE[cov]["oop_max"]
        worst = pricing.out_of_pocket(500_000, 5000, cov)
        check(f"{cov} OOP respects cap", worst <= cap, f"{worst:,.0f} vs cap {cap:,.0f}")

    # uninsured must always cost more than insured for the same bill
    unins = pricing.out_of_pocket(60_000, 5000, "uninsured")
    std = pricing.out_of_pocket(60_000, 5000, "standard")
    check("uninsured pays more than insured", unins > std, f"{unins:,.0f} vs {std:,.0f}")

    opts, total = pricing.domestic_options(proc, 5000, "uninsured")
    check("domestic returns options", len(opts) > 0, f"{len(opts)} of {total}")

    # expected cost must be sorted, and always >= out of pocket
    exp = [o["expected_cost"] for o in opts]
    check("sorted by expected cost", exp == sorted(exp))
    check("expected >= out-of-pocket", all(o["expected_cost"] >= o["out_of_pocket"] for o in opts))

    # sicker patient must never be cheaper
    facts = [{"fact": "diabetic", "category": "diabetes"},
             {"fact": "age 71", "category": "age_over_70"}]
    sick, _ = pricing.domestic_options(proc, 5000, "uninsured", facts)
    check("comorbidities raise expected cost",
          sick[0]["expected_cost"] > opts[0]["expected_cost"],
          f"{sick[0]['expected_cost']:,.0f} vs {opts[0]['expected_cost']:,.0f}")

    inv = pricing.rank_inversion(opts)
    if inv:
        a, b = inv["cheaper_sticker"], inv["better_value"]
        check("inversion is real", a["base_cost"] < b["base_cost"]
              and a["expected_cost"] > b["expected_cost"], inv["note"][:90])
    else:
        warn("no rank inversion found in default slice")

    sp = pricing.price_spread(proc)
    check("price spread computed", sp and sp["multiple"] > 1, f"{sp['multiple']}x")

    # premium must scale with risk
    lo = pricing.warranty_cost(proc, 0.02)
    hi = pricing.warranty_cost(proc, 0.06)
    check("premium rises with risk", hi > lo, f"{lo:,.0f} -> {hi:,.0f}")

    # premium must exceed expected loss (that is what a load means)
    p = 0.04
    expected_loss = p * pricing.REVISION_COST[proc]
    prem = pricing.warranty_cost(proc, p)
    check("premium exceeds expected loss (load)", prem > expected_loss,
          f"{prem:,.0f} vs {expected_loss:,.0f}")


# ---------- monte carlo stability ----------

def monte_carlo_checks():
    proc = "Total Knee Replacement"
    p_comp = 0.045
    # use the actuarially-priced premium, not an arbitrary one
    args = dict(fixed=13000, premium=pricing.warranty_cost(proc, p_comp),
                p_comp=p_comp, procedure=proc)

    a = pricing.cost_distribution(**args, seed=1)
    b = pricing.cost_distribution(**args, seed=2)
    c = pricing.cost_distribution(**args, seed=1)

    check("deterministic for a fixed seed",
          a["expected_cost_uncovered"] == c["expected_cost_uncovered"])

    drift = abs(a["expected_cost_uncovered"] - b["expected_cost_uncovered"])
    rel = drift / a["expected_cost_uncovered"]
    check("stable across seeds (<2% drift)", rel < 0.02, f"{rel:.2%} at {pricing.DRAWS:,} draws")

    check("p50 <= p95 <= p99", a["p50"] <= a["p95"] <= a["p99"])
    check("coverage removes the tail", a["tail_protection"] > 0, f"{a['tail_protection']:,.0f}")
    check("insurance is negative EV (the load)", a["cost_of_certainty"] > 0,
          f"{a['cost_of_certainty']:,.0f}")


# ---------- endpoint robustness ----------

def endpoint_checks():
    from fastapi.testclient import TestClient
    import main

    c = TestClient(main.app)

    check("health", c.get("/health").json().get("ok") is True)

    garbage = [
        ("unknown procedure", {"session_id": "x", "procedure_name": "Brain Transplant",
                               "user_deductible": 5000}),
        ("negative deductible", {"session_id": "x", "procedure_name": "Total Knee Replacement",
                                 "user_deductible": -900}),
        ("absurd deductible", {"session_id": "x", "procedure_name": "Total Knee Replacement",
                               "user_deductible": 9e9}),
        ("bad plan id", {"session_id": "x", "procedure_name": "Total Knee Replacement",
                         "user_deductible": 5000, "plan_id": "NOPE"}),
        ("bad state", {"session_id": "x", "procedure_name": "Total Knee Replacement",
                       "user_deductible": 5000, "state": "ZZ"}),
    ]
    for name, body in garbage:
        r = c.post("/quote/domestic", json=body)
        check(f"domestic survives {name}", r.status_code == 200, f"got {r.status_code}")

    for name, text in [("empty", ""), ("emoji", "🦴🦴🦴"), ("very long", "knee " * 3000)]:
        r = c.post("/intake", json={"session_id": "x", "text": text})
        check(f"intake survives {name} input", r.status_code == 200, f"got {r.status_code}")

    r = c.post("/quote/international", json={"session_id": "never-seen", "procedure_name":
                                             "Total Knee Replacement"})
    check("international survives unknown session", r.status_code == 200, f"got {r.status_code}")

    r = c.post("/checkout", json={"session_id": "never-seen", "hospital_id": "NOPE"})
    check("checkout survives unknown ids", r.status_code == 200, f"got {r.status_code}")

    # checkout line items must sum to the total
    c.post("/intake", json={"session_id": "s1", "text": "knee replacement, uninsured, 62, diabetic"})
    c.post("/quote/domestic", json={"session_id": "s1", "procedure_name": "Total Knee Replacement",
                                    "user_deductible": 5000, "coverage": "uninsured"})
    intl = c.post("/quote/international", json={"session_id": "s1",
                                                "procedure_name": "Total Knee Replacement"}).json()
    hid = intl["options"][0]["hospital_id"]
    co = c.post("/checkout", json={"session_id": "s1", "hospital_id": hid}).json()
    s = round(sum(i["amount"] for i in co["line_items"]), 2)
    check("checkout line items sum to total", abs(s - co["total"]) < 0.01,
          f"{s:,.2f} vs {co['total']:,.2f}")

    # every international option must carry a reasoning string
    check("every option has reasoning",
          all(o.get("reasoning") for o in intl["options"]))
    # Only the leading options get the 10k-draw simulation; the rest would never be charted.
    with_dist = [o for o in intl["options"] if o.get("distribution")]
    check("leading options carry a distribution",
          len(with_dist) == min(pricing.DISTRIBUTION_DEPTH, len(intl["options"])),
          f"{len(with_dist)} of {len(intl['options'])}")
    check("the displayed option has a distribution",
          bool(next(o for o in intl["options"] if not o["excluded_by_constraint"])["distribution"]))


def main_run():
    for fn in (data_checks, model_checks, monte_carlo_checks, endpoint_checks):
        try:
            fn()
        except Exception as e:
            FAIL.append(f"{fn.__name__} crashed: {type(e).__name__}: {e}")

    for p in PASS:
        print(f"  PASS  {p}")
    for w in WARN:
        print(f"  NOTE  {w}")
    for f in FAIL:
        print(f"  FAIL  {f}")
    print(f"\n{len(PASS)} passed, {len(FAIL)} failed, {len(WARN)} notes")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main_run())
