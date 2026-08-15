# Base44 build prompt

Paste the block below into Base44. Replace `API_BASE` with the current backend URL
before pasting — it changes when the tunnel restarts, and becomes permanent once
Render is up.

---

Build a two-screen web app called **MedMap** that helps someone facing a knee or hip
replacement find the lowest *expected* cost, including the risk of complications.

All data comes from an existing API. **Never compute, estimate, or invent any number —
display only what the API returns.**

API_BASE = `https://PASTE-URL-HERE`

## Screen 1 — Your situation

A single centered card on a clean clinical background. Deep blues, white, slate grey.
Serious and trustworthy, not playful.

Fields:
- Large textarea, placeholder: *"Describe your situation — what surgery you need, your
  insurance, your age, any conditions, how far you're willing to travel."*
- A row of three selects underneath: **Procedure** (`GET {API_BASE}/procedures`),
  **Coverage** (Uninsured / High deductible / Standard employer plan), **State** (optional)
- An optional **Insurance plan** searchable dropdown populated from
  `GET {API_BASE}/plans?state={state}&q={typed}` — show `name`, `metal`, and
  `Deductible $X · Out-of-pocket max $Y`. Store the selected `plan_id`.
- Primary button: **Analyze my options**

On submit:
1. `POST {API_BASE}/intake` with `{ session_id, text }` where `session_id` is a random
   string generated once per visit and reused for every later call.
2. Show the returned `facts` as small chips — one per condition.
3. `POST {API_BASE}/quote/domestic` with
   `{ session_id, procedure_name, user_deductible, coverage, state, plan_id }`

## Screen 2 — What it actually costs

### Top banner
From `price_spread`: **"The same procedure costs $20,699 to $344,847 across
{hospital_count} US hospitals — a {multiple}x difference."** Use the returned values.

### The finding (make this the most prominent element on the page)
If `rank_inversion` is not null, show `rank_inversion.note` verbatim in a bordered
callout with a heading like *"Cheaper isn't cheaper."* This is the single most important
thing on the screen — give it real visual weight.

### US hospitals table
From `options`. Columns: Hospital, State, **Sticker price**, **Your cost**,
**Complication rate**, **Expected total**. Sort order is already correct — do not re-sort.

Render complication rate as the value plus its `complication_ci` range in smaller grey
text beneath, e.g. `5.4%` over `range 3.1–8.2%`. Highlight the **Expected total** column —
it is the one that matters and it is not the same order as sticker price.

### Going abroad
Button: **Compare international options** →
`POST {API_BASE}/quote/international` with `{ session_id, procedure_name }`

Render each option as a card on a world map or in a grid. Each card shows:
- Country, hospital, `flight_hours` from JFK
- A stacked bar broken into **Procedure / Travel / Complication coverage**
- **Total** and **savings vs best US option** — colour green when positive, red when negative
- The `reasoning` sentence, prominently
- If `excluded_by_constraint` is true, dim the card and badge it
  *"Outside your travel limit"* — still show it, don't hide it

### Risk panel
For the top eligible option, from `distribution`:
- **"Worst case without coverage: ${p99_uncovered}"**
- **"Worst case with coverage: ${p99}"**
- **"The premium removes ${tail_protection} of downside and costs ${cost_of_certainty}
  in expectation."**

Render as two horizontal bars at the same scale so the difference is visually obvious.

### Explanation
Call `POST {API_BASE}/explain` with `{ session_id, procedure_name }`. It returns
`{ facts, instruction }`. Pass **both** to the LLM — use `instruction` as the system
prompt and `facts` as the content. Display the result in a bordered panel headed
*"What this means for you."*

**Critical: the LLM only writes prose. It must never calculate, re-rank, or introduce a
number that isn't in `facts`. Every figure on this page comes from the API.**

### Checkout
Button **Book this** → `POST {API_BASE}/checkout` with `{ session_id, hospital_id }`.
Show `line_items` as a payment splitting three ways — hospital, travel partner,
complication underwriter — with the `order_id` on confirmation.

## Rules

- If any response contains `"degraded": true`, show a small grey note
  *"showing cached estimates"*. Never show an error screen.
- Format money as `$12,345` with no decimals. Percentages to one decimal.
- Never round or recompute an API value.
- Mobile-responsive; tables scroll horizontally rather than the page.
