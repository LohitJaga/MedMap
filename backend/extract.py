"""Free-text intake -> structured facts.

Deterministic keyword parse, no LLM. Runs offline, never times out mid-demo,
and the scripted demo sentence always produces the same result.
"""

import re

PROCEDURES = [
    (r"\bknee\b", "Total Knee Replacement"),
    (r"\bhip\b", "Total Hip Replacement"),
]

CONDITIONS = [
    (r"\bdiabet", "diabetes", "diabetic"),
    (r"\b(heart|cardiac|bypass|stent|afib|a-fib)\b", "heart_condition", "cardiac history"),
    (r"\b(smoke|smoker|smoking)\b", "smoker", "smoker"),
    (r"\b(obese|obesity|overweight|bmi)\b", "obesity", "elevated BMI"),
    (r"\b(blood thinner|warfarin|eliquis|coumadin|xarelto)\b", "blood_thinners", "blood thinners"),
]

COVERAGE_PATTERNS = [
    (r"\b(uninsured|no insurance|don'?t have insurance|without insurance|self.?pay)\b", "uninsured"),
    (r"\b(high.?deductible|hdhp|catastrophic|bronze)\b", "high_deductible"),
]

STATES = {
    "new york": "NY", "nyc": "NY", "california": "CA", "texas": "TX", "florida": "FL",
    "new jersey": "NJ", "connecticut": "CT", "massachusetts": "MA", "maryland": "MD",
    "pennsylvania": "PA", "illinois": "IL", "ohio": "OH", "georgia": "GA",
}


def _money(text, *keywords):
    for kw in keywords:
        m = re.search(rf"{kw}[^.$]*\$?\s*([\d,]+)\s*(k\b)?", text, re.I)
        if m:
            v = float(m.group(1).replace(",", ""))
            return v * 1000 if m.group(2) else v
        m = re.search(rf"\$?\s*([\d,]+)\s*(k\b)?[^.$]*{kw}", text, re.I)
        if m:
            v = float(m.group(1).replace(",", ""))
            return v * 1000 if m.group(2) else v
    return None


def parse(text):
    t = text.lower()
    facts = []

    procedure = "Total Knee Replacement"
    for pat, name in PROCEDURES:
        if re.search(pat, t):
            procedure = name
            break

    for pat, category, label in CONDITIONS:
        if re.search(pat, t):
            facts.append({"fact": label, "category": category})

    age = None
    for pat in (r"\b(\d{2})[\s-]*(?:years?[\s-]*old|yrs?[\s-]*old|yo)\b",
                r"\b(?:i'?m|i am|aged?)\s*(?:a\s+)?(\d{2})\b",
                # bare age in a list: "I'm uninsured, 62, diabetic"
                r"[,;]\s*(\d{2})\s*[,;]"):
        m = re.search(pat, t)
        if m and 18 <= int(m.group(1)) <= 99:
            break
        m = None
    if m:
        age = int(m.group(1))
        if age >= 70:
            facts.append({"fact": f"age {age}", "category": "age_over_70"})
        elif age >= 60:
            facts.append({"fact": f"age {age}", "category": "age_over_60"})

    m = re.search(r"(\d{1,2})\s*(?:hour|hr)s?\s*(?:flight|flying|in the air)?", t)
    if not m:
        m = re.search(r"flight[^.]*?(\d{1,2})\s*(?:hour|hr)", t)
    if m:
        facts.append({
            "fact": f"max {m.group(1)} hour flight",
            "category": "max_flight_hours",
            "value": int(m.group(1)),
        })

    coverage = "standard"
    for pat, kind in COVERAGE_PATTERNS:
        if re.search(pat, t):
            coverage = kind
            break

    deductible = _money(t, "deductible") or 5000.0
    budget = _money(t, "budget", "afford", "spend")

    state = None
    for name, abbr in STATES.items():
        if name in t:
            state = abbr
            break

    insurance = None
    m = re.search(r"\b(bcbs|blue cross|aetna|cigna|united ?health(care)?|humana|kaiser)\b", t)
    if m:
        insurance = m.group(1).upper()
    elif coverage == "uninsured":
        insurance = "Uninsured"

    return {
        "procedure_name": procedure,
        "user_deductible": deductible,
        "coverage": coverage,
        "insurance": insurance,
        "state": state,
        "budget": budget,
        "facts": facts,
    }
