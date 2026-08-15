# MedMap — Build Plan

**Track:** E-commerce (Shopify / Base44 sponsored)
**Team:** 4 — backend, data, frontend ×2
**Clock:** kickoff 10:00 · freeze 17:00 · demos 18:00

---

## 1. What we're building

A marketplace that shows you what a surgery costs in the US, then sells you the same surgery abroad as a single bundled purchase — procedure + flights + lodging + complication insurance, one checkout.

The reason medical tourism hasn't gone mainstream isn't price, it's risk: nobody wants to be 5,000 miles from home when something goes wrong. **Our answer is that the complication coverage is in the cart.** That's the product.

Two screens:

**Zone A — Domestic.** Describe your situation, see US hospitals and your real out-of-pocket. The number is large. A button at the bottom says *Find cheaper options (international)*.

**Zone B — International.** World map with pins. Click one, see the full bundled cost broken into its three parts and the savings vs. domestic. Check out once; the payment visibly splits to the hospital, the airline, and the insurance underwriter.

**Why it's e-commerce:** it's a multi-SKU bundle with a real cart and a real checkout. Bundling is a core commerce problem — ours just has an unusual product.

---

## 2. The two things that make it more than a mockup

Every team can build two screens over a JSON file. These are what we say when a judge asks what was hard.

**Real data, not invented numbers.** Domestic prices come from published CMS/chargemaster data. International prices come from published medical-tourism figures. Flights are pulled live if we get there. We can name every source on stage.

**The warranty premium is computed, not constant.** Intake is free text — the user describes their situation in their own words. We extract each fact and store it with a *confidence*: things stated directly carry full weight, hedged things carry less. The premium is then a function of that confidence-weighted risk profile, and we can explain the number:

> "Your premium is $840 — higher than baseline because you're diabetic, which you stated directly. We weighted the 'I think I had knee trouble before' comment lower because you were unsure."

That runs on an existing deployed memory service (Cloudflare Workers + D1 + Vectorize). It is a separate HTTP service — **if it's down, the backend falls back to the dropdown form and fixed multipliers, and the app still works.**

---

## 3. Repo layout

```
MedMap/
├── API.md                        contract — source of truth
├── PLAN.md                       this file
├── backend/                      OWNER: backend
│   ├── main.py                   FastAPI routes
│   ├── pricing.py                out-of-pocket, bundling, warranty
│   ├── memory.py                 calls to the memory service
│   ├── data/                     OWNER: data  (only file both lanes share)
│   │   ├── hospitals.json
│   │   ├── procedures.json
│   │   └── destinations.json
│   └── requirements.txt
└── frontend/
    ├── src/
    │   ├── api.js                agreed once in hour 1, then frozen
    │   ├── zones/
    │   │   ├── Domestic.jsx      OWNER: frontend-domestic
    │   │   └── International.jsx OWNER: frontend-intl
    │   └── components/           prefix files with your zone: Dom*, Intl*
    └── package.json
```

**One owner per folder. Do not edit someone else's files** — open an issue in the group chat instead. Shared files are `API.md`, `api.js`, and `data/*.json`, and each has exactly one owner too.

Branches: work on `main` directly, commit small and often, pull before you push. With four people and six hours, branch protection costs more than it saves.

---

## 4. API contract

Backend serves these five. Stubs returning correctly-shaped fake data go live by **12:15** so frontend is never blocked. Real implementations swap in behind them.

```
POST /intake
  in   { session_id, text }
  out  { procedure_name, user_deductible, insurance,
         facts: [ { fact, category, confidence } ] }

GET  /procedures
  out  { procedures: [ { procedure_name, category } ] }

POST /quote/domestic
  in   { session_id, procedure_name, user_deductible }
  out  { options: [ { hospital_id, name, city, state,
                      base_cost, out_of_pocket, safety_score } ] }

POST /quote/international
  in   { session_id, procedure_name }
  out  { options: [ { hospital_id, name, city, country, flight_hours,
                      base_cost, travel_cost, warranty_cost,
                      true_cost, savings_vs_domestic,
                      safety_score, reasoning } ] }

POST /checkout
  in   { session_id, hospital_id }
  out  { line_items: [ { payee, label, amount } ],
         total, order_id }
```

Every request carries `session_id`. That's the only coupling between the memory service and the rest of the app.

Errors: backend never returns a 5xx to the frontend. If something fails internally it returns stub data with `"degraded": true`. Frontend can ignore that flag entirely.

---

## 5. Lanes

### Backend — Lohit
- FastAPI skeleton, all five endpoints, **stubs live by 12:15**
- Out-of-pocket math: `min(base_cost, deductible_remaining + coinsurance × (base_cost − deductible_remaining))`
- Bundling engine: base + travel + warranty → true cost, savings vs. cheapest domestic
- Warranty engine: `base_cost × destination_risk × patient_risk_factor`, where patient risk is built from confidence-weighted intake facts
- Memory service wiring: store on `/intake`, retrieve on `/quote/international`
- The `reasoning` string on every international option
- Checkout split into three line items that sum to the total
- Every endpoint wrapped with a stub fallback

### Data — scraping
This lane decides whether we look real. Priority order, top first:

1. **`hospitals.json` — 6 domestic, 6 international, 3 procedures.** Total knee replacement, total hip replacement, cardiac bypass. Domestic from CMS chargemaster / Medicare procedure price lookup; international from published medical-tourism price lists. Ballpark for sanity: US knee $32–50k, Costa Rica ~$12k, India ~$8k, Mexico ~$12k, Thailand ~$14k.
   Fields: `hospital_id, name, city, state_or_country, location_type, procedure_name, base_cost, safety_score, source_url`
2. **`destinations.json`** — flight hours from JFK, typical lodging cost, visa note, one line on why patients go there.
3. **Safety scores must come from something real** — Leapfrog Hospital Safety Grade or CMS star ratings for domestic, JCI accreditation status for international. An invented score is the first thing a judge will poke at.
4. **`source_url` on every row.** This is what lets us say "these are real numbers" out loud.
5. *If time remains:* live flight pricing via Amadeus or SerpApi, JFK → each destination.

Hand off `hospitals.json` by **13:00** — backend is blocked on real math until then. Ship a partial file early rather than a complete one late.

### Frontend A — Domestic
- Full-bleed map background, elevated clinical side panel over it
- Intake: a **free-text box** ("describe your situation") posting to `/intake`, with the dropdown form as a visible fallback
- Results list from `/quote/domestic` — hospital, out-of-pocket, safety score
- Hide any hospital below the safety threshold, and say on screen that we're filtering, with the source named
- The handoff: prominent *Find cheaper options (international)* button → acknowledgement modal → passes `{ session_id, procedure_name, user_deductible }` to Zone B

### Frontend B — International + checkout
- World map, CSS pins on destination countries
- Click a pin → bundle card: the three cost pillars, true cost, savings vs. domestic, **and the `reasoning` line rendered prominently** (this is the demo money-shot, give it real visual weight)
- Checkout: cart → payment → confirmation
- Escrow split visualization — one payment fanning out to hospital / airline / underwriter
- *If time:* run the cart through a free Shopify dev store via the Storefront API instead of mocking it. Three products, real checkout. This is the sponsored track — a real Shopify cart is worth more than any other single feature.

**Both frontend lanes:** deep blues, crisp whites, slate grays. Trust-building and clinical, not playful. Agree the shared component styles once at the start so the two zones don't look like different products.

---

## 6. Schedule

| Time | Milestone |
|---|---|
| 12:15 | **Gate.** Stub endpoints live at a URL everyone can hit. Frontend unblocked. |
| 13:00 | **Gate.** `hospitals.json` handed to backend, even if partial. |
| 14:00 | Zone A renders real domestic results end to end. |
| 15:00 | Zone B renders real bundles with reasoning. Backend feature-complete. |
| 16:00 | **Cut line.** Anything unfinished reverts to its stub. No new work after this. |
| 16:00–17:00 | Integration. All four of us on the full flow, front to back. |
| 17:00 | **Freeze.** No commits. Rehearse the demo out loud, three times. |
| 18:00 | Demos. |

Deploy early and keep it deployed — a laptop that won't screen-share at 18:00 has killed better projects than ours.

---

## 7. Rules

1. **Fall back, never debug live.** After 16:00 a broken feature gets switched off, not fixed.
2. **Nothing fake ships unlabeled.** If a number is a placeholder, the UI says so. Getting caught inventing data in Q&A is worse than admitting it up front.
3. **One owner per folder.** Merge conflicts at 16:00 cost more than the feature would have earned.
4. **Say it out loud before you build it.** If you can't explain why a feature helps the demo, don't build it.

---

## 8. Demo script — 2 minutes

1. **The hook.** "A knee replacement in the US runs about $35,000. Here's what your insurance actually leaves you paying." Show the domestic number.
2. **The intake.** Type the situation in plain English. Show the extracted facts appearing with confidence values — this is the part nobody else has.
3. **The handoff.** Click through the warning into the world map.
4. **The bundle.** Click Costa Rica. Walk the three pillars. Land on the savings number.
5. **The money shot.** Read the reasoning line out loud — why this destination, why this premium, which facts drove it.
6. **The checkout.** One payment, three payees. Confirmation screen.
7. **The close.** "Prices are from published CMS and medical-tourism data, sources in the repo. The premium is computed from the patient's own risk profile. And the insurance is in the cart — which is the only reason anyone would actually book this."

---

## 9. Known weaknesses — own them, don't get caught

- **Flight and lodging costs are estimates** unless the live API lands. Say so.
- **The insurance underwriter is a stand-in.** The pricing logic is ours and it's real; the counterparty is a placeholder. Say that too.
- **Regulatory and licensure questions are out of scope for eight hours.** The honest answer is "a real version needs a licensed insurance partner and a compliance review, and that's the first thing after this."
