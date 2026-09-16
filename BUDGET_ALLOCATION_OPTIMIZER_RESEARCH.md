# Research: Smart Budget Allocation Optimizer

STATUS: DESIGN ONLY — not yet approved for build.

## 1. What this is, in plain terms

The current what-if tool (`intelligence/whatif.py`) answers: *"If Kolhapur got
₹50 crore, how would the rankings shift?"* — a ranking simulation. It does not
decide how to spend the money.

This feature answers a different question: *"Given exactly ₹X, which specific
projects should be funded to reach the most people, without going over
budget?"* — a real spending plan, not a re-sorted wishlist. The project's own
research report already names this as a future step (a knapsack-style
optimizer), not a new idea.

## 2. The real blocker isn't the algorithm

0/1 knapsack (pick the best-fitting combination of items under a budget cap)
is a solved, well-understood problem — genuinely "extremely easy to explain to
judges," and there is real academic precedent for exactly this use: highway
project selection has been formulated as a 0/1 knapsack in the literature, and
the World Bank runs an "Infrastructure Prioritization Framework" specifically
for public-sector cases where project costs are hard to pin down.

**The actual constraint is honest cost data.** A knapsack's entire output
depends on knowing, per project, "how much would this cost" — checked live
against what this project actually has:

| Category | Real, trustworthy per-project cost? |
|---|---|
| A road with a sanctioned-but-undelivered PMGSY work | **Yes** — real sanctioned cost (`undelivered_sanctioned_cost_lakh`) |
| A water village with an ongoing JJM scheme | **Yes** — real remaining cost (`estimated_cost_lakh` − `reported_expenditure_lakh`, from `jjm_scheme_catchment_evidence`) |
| A road/water complaint with no existing scheme yet | No — only a median ₹/km guess applied to a made-up length |
| A health complaint | No real cost data anywhere in this project |
| An education complaint | Real *grant* data exists (UDISE+ `total_grant`/`total_expenditure`), but a grant is not the same number as "cost to fix a specific deficiency" — a school with a small maintenance grant can still have a large repair backlog |

Building the optimizer across every category would mean inventing costs for
most of it — the one thing this project has refused to do anywhere else.

## 3. Scoped design: roads + water only

Restrict the optimizer's universe to assets with a **real** cost figure:
sanctioned-but-undelivered PMGSY road works, and ongoing JJM water schemes
with real unspent estimates. This reframes the pitch slightly but keeps it
just as strong: not *"which brand-new projects should be funded"* but
***"given this budget, which already-approved, half-built real projects
should be finished first."***

### Inputs, all real
- **Cost**: PMGSY `undelivered_sanctioned_cost_lakh` (roads) or JJM
  `unspent_estimate_lakh` (water) — both already computed in this codebase.
- **Value**: real `population_affected` per asset (already scored on).
- **Priority**: the existing real `priority_score`.
- **Exclusions**: assets already fully funded (JJM status "Completed", or a
  PMGSY work not in `UNDELIVERED_STATUSES`) are out of scope by definition —
  there is nothing to allocate to something already finished.

### Constraints (per the original proposal, mapped to what's real here)
- **Budget**: hard cap, the one constraint every version needs.
- **Scheme eligibility**: already computed (`scheme_eligible` / `data_basis:
  published_scheme_rule`) — can be a hard filter or a scoring bonus.
- **Already-funded exclusion**: built in via the undelivered/ongoing filter
  above, not a separate rule.
- **Category limits** (e.g., "at least 30% to water"): optional, a real
  multi-dimensional knapsack constraint, not required for a first version.
- **Minimum equity score**: possible using the real `equity` term already in
  the breakdown — deferred to a second pass, not required to prove the
  mechanism.

### Algorithm
Plain 0/1 knapsack, dynamic programming, maximizing total real
`population_affected` (or a priority-weighted variant) subject to the budget
cap. At roughly 400-500 real road/water assets, this is trivially small for
exact DP — no need for integer-programming tooling or heuristics at this
scale.

## 4. A real worked example, computed from the live database

Not an illustrative table — this is an actual 0/1 knapsack run against 8 real
road/water assets pulled from the live database, budget capped at ₹4 crore
(400 lakh), maximizing population reached:

| Asset | Type | Real cost (₹ lakh) | Population | Priority score | Chosen? |
|---|---|---|---|---|---|
| Water point near Dhekoliwadi | water | 31.86 | 4,371 | 59.71 | ✅ |
| Water point near Kotoli | water | 96.24 | 7,390 | 59.28 | ✅ |
| Water point near Surute | water | 50.84 | 1,560 | 57.90 | ✅ |
| M56 To Kudnur Kitwad... | road | 127.70 | 36,293 | 57.74 | ✅ |
| Chandgad Kokare Navheli... | road | 283.76 | 3,149 | 57.65 | ❌ (too expensive for remaining room) |
| Water point near Khirkade | water | 44.93 | 2,189 | 57.62 | ✅ |
| Road near Ajra | road | 314.95 | 14,855 | 57.16 | ❌ (too expensive) |
| Water point near Madyal | water | 17.12 | 5,979 | 55.49 | ✅ |

**Recommended portfolio**: 6 of 8 real projects.
**Total cost: ₹369 lakh (₹3.69 crore) of ₹400 lakh.**
**Real population reached: 57,782.**
**Remaining budget: ₹31 lakh** — too little left to add either of the two
larger, more expensive roads, correctly left out even though one of them
(M56) reaches far more people per rupee and IS included.

## 5. What's explicitly out of scope for a first build

- Health and education stay out of the optimizer itself (no honest
  per-project repair cost exists for either yet) — they remain visible in the
  regular priority ranking and in the school white-space view, just not in
  this optimizer's budget math.
- Integer programming / multi-dimensional constraints (category quotas,
  equity floors) — real extensions, not required to prove the mechanism.
- MGNREGA and PRIASoft village-panchayat finance are whole-panchayat money,
  not tied to a specific road or water project — they don't have a natural
  place in a per-project knapsack. They're a better fit for a general
  "how much unspent government money exists here at all" signal, a different,
  separate feature from this one.

## 6. Next step

This is a design, not a build prompt. If approved, the build would live
alongside `intelligence/whatif.py` as a new, separate module rather than
modifying it — the existing what-if simulation and this optimizer answer two
different questions and neither replaces the other.
