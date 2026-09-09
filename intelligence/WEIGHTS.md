# WEIGHTS.md — every number the Intelligence Engine uses, and why

Required by the build plan (P3 Step 5): weights "must be visible and
changeable, never hardcoded silently inside a function." Every value below
lives in [`config.py`](config.py); nothing in this package inlines a magic
number.

If you change a value here, change it in `config.py` and re-run
`python -m intelligence.recompute`. Scores are versioned by `computed_at`, so
an old ranking is never silently overwritten without a timestamp.

---

## The formula, in two stages

**Stage 1 — Gap Score** ("how bad is this problem", 0–1):

```
gap_score = w1·demand + w2·population + w3·infra_deficit + w4·vulnerability
```

**Stage 2 — Priority Score** (0–100, what the dashboard ranks on):

```
priority = gap_score · GAP_SCORE_MAX_POINTS · confidence_gate
         + equity + strategic + urgency + feasibility − cost_penalty
```

Addition, not multiplication — research report §16.1 shows the brief's
original all-multiplied formula collapses toward zero as terms are added
(five average 0.6 factors score 0.078) and that one missing input zeroes an
otherwise real gap. The only multiplier is the confidence gate, which damps
and never zeroes.

**The nine breakdown terms sum exactly to `priority_score`.** This is
enforced by `test_breakdown_sums_to_score` in `test_intelligence.py`, checked
across five differently-shaped clusters, and verified against all 113 live
clusters (0 mismatches). Everything the dashboard claims about explainability
depends on this property holding.

---

## Stage 1 weights

| Constant | Value | Why |
|---|---|---|
| `W_DEMAND` | 0.25 | Equal weights, as the build plan's Step 5 instructs. Not tuned — there is no outcome data to tune against, and pretending otherwise would be the exact false precision §16.3 warns about. |
| `W_POPULATION` | 0.25 | " |
| `W_INFRA_DEFICIT` | 0.25 | " |
| `W_VULNERABILITY` | 0.25 | " |
| `GAP_SCORE_MAX_POINTS` | 81.0 | Chosen so the theoretical maximum is exactly 100: 81 + equity 10 + strategic 4 + urgency 3 + feasibility 2. Preferred over clamping at 100, which would break the sum-to-score guarantee. |

### `demand` — capped, on purpose

`DEMAND_SATURATION_REPORTERS = 25`. Past 25 distinct reporters the term
saturates. This is the single most important line in the file: it is what
stops a well-connected district out-shouting a cut-off one, and what makes
this a needs engine rather than the popularity contest it exists to replace
(research report §16.2 — report volume is *evidence*, never demand itself).

Distinct reporters are approximated by **distinct report text**, because this
dataset has no citizen identity. Identical text counts once, which implements
the "500 people forwarding the same WhatsApp message is not 500
corroborations" guard from §10.4. Replace with real per-citizen identity when
it exists.

### `population` — log-scaled

`POPULATION_LOG_CEILING = 100,000`. `log(1+pop)` normalised against
`log(1+100,000)`. Linear population would let dense urban clusters
structurally starve every rural one; log scaling compares them on diminishing
returns (§16.2).

### `infra_deficit` — ⚠️ PROXY, NOT REAL INFRASTRUCTURE DATA

`SEVERITY_DEFICIT = {high: 1.0, medium: 0.5, low: 0.2}`.

PMGSY road-condition data has no confirmed bulk export (research report §14),
so this term is derived from **the severity citizens reported**, not from
measured asset condition. It currently measures *what people said about the
infrastructure*, not the infrastructure. Say so out loud in the demo; replace
with real asset condition before claiming otherwise.

### `vulnerability` — ⚠️ PROXY, AND AGGREGATE-ONLY BY HARD RULE

`VULNERABILITY_SMALL_SETTLEMENT_POP = 2,000`. Small, scattered settlements
are treated as less-served than one large town of the same total population.

**Hard rule, carried from research report §16.4:** this term may only ever
read published, area-level statistics. Individual attributes — caste above
all — must never enter it, whatever accuracy they might buy. Unknown data
returns 0.5, never 0: absence of data is not absence of need.

---

## Stage 2 offsets

| Constant | Value | Meaning |
|---|---|---|
| `CONFIDENCE_THRESHOLD` | 0.75 | At/above this average extraction confidence the gap score passes through untouched; below it, damped proportionally. Never multiplies by zero (§16.1). |
| `EQUITY_BOOST_POINTS` | 10.0 | The Silent Need correction — the load-bearing term of the design. |
| `STRATEGIC_POINTS` | 4.0 | Applied when `population_affected ≥ 5,000`. |
| `URGENCY_POINTS` | 3.0 | Applied to monsoon-sensitive categories (road, water). |
| `FEASIBILITY_POINTS` | 2.0 | Applied within 15 km of a taluka HQ. |
| `COST_PENALTY_POINTS` | 3.0 | Scales with catchment population, subtracted. |

Offsets are deliberately small relative to the 81-point gap score: they break
near-ties, they do not decide rankings.

### `equity` — ⚠️ HARDCODED LOOKUP, sanctioned by the build plan

`LOW_CONNECTIVITY_BLOCKS` lists 10 blocks — the hilly/forested western
talukas of Kolhapur (Chandgad, Ajra, Gaganbawada, Radhanagari, Shahuwadi,
Bhudargad) and the tribal western belt of Nashik (Surgana, Peth,
Trimbakeshwar, Kalwan).

Build plan Step 6 explicitly permits this: *"hardcode a lookup table of 2–3
known low-connectivity blocks in your demo districts if you don't have time
to compute this properly."* These are **not** derived from a published
connectivity or literacy index. Replace with real TRAI circle data and Census
literacy figures before any production claim. The reasoning it encodes: a
given number of reports from a population that struggles to report at all is
proportionally stronger evidence than the same number from a population that
reports easily.

### `strategic` — ⚠️ stand-in, not a scheme-rules engine

A crude population gate (`STRATEGIC_POPULATION_THRESHOLD = 5,000`) standing
in for the Scheme-Eligibility Auto-Matcher (research report §10, feature
#12). It answers "would this plausibly clear an existing scheme's threshold",
nothing more. A real implementation would match against actual published
PMGSY/JJM eligibility criteria.

---

## Clustering parameters

| Constant | Value | Why |
|---|---|---|
| `GATE_RADIUS_KM` | 8.0 | **Tuned against the real data, not guessed** — see the table below. |
| `DBSCAN_EPS_KM` | 6.0 | Kept below the gate so the gate is a hard outer bound and eps shapes clusters inside it. |
| `DBSCAN_MIN_SAMPLES` | 3 | A cluster needs three corroborating reports. Two people saying the same thing is not yet a demand signal (§10.4). |
| `SEMANTIC_WEIGHT_KM` | 3.0 | A fully dissimilar pair of texts is treated as 3 km further apart, so "the school roof leaks" never merges into "the borewell is dry" just because both came from one village. |
| `GATE_PENALTY_KM` | 1000.0 | Effectively infinite; keeps gating and clustering in one matrix. |

Measured on the full 1,008-report set (spread over 439 villages, ~2.3 reports
each), as the build plan's risk table instructs:

| gate | eps | reports clustered | clusters | largest cluster |
|---|---|---|---|---|
| 2 km | 2 km | 25% | 68 | 11 |
| 5 km | 4 km | 44% | 113 | 11 |
| **8 km** | **6 km** | **66%** | **113** | **37** ← chosen |
| 12 km | 8 km | 79% | 75 | 101 ← over-merging |

8 km also matches the terrain: villages in Kolhapur/Nashik sit roughly 5–10 km
apart, and a failing road or water main strands a corridor of neighbouring
settlements rather than one point. At 12 km distinct problems collapse into a
single 101-report blob — the "one giant cluster" failure the build plan warns
about.

Reports DBSCAN labels as noise are deliberately **not** turned into clusters:
a lone uncorroborated report is not yet a demand signal.

## Catchment radius (population affected)

| Category | Radius | Why |
|---|---|---|
| health | 8 km | People travel furthest for a clinic. |
| road | 5 km | A broken segment strands a corridor. |
| education | 4 km | School catchments are local. |
| water | 3 km | Supply failures are the most local of the four. |

Settlements with no Census population figure contribute **nothing** rather
than an estimate — 948 of 1,042 gazetteer rows carry a real number, and
inventing the rest would fabricate the most load-bearing input to the score.

---

## What-if re-ranking

`WHATIF_POINTS_PER_CRORE = 0.4`, capped at `WHATIF_MAX_POINTS = 12.0`.

Extra budget raises the `strategic` term for clusters in the funded district.
Deliberately crude, and it does **not** re-cluster — what is broken does not
change because money moved, only what is fundable does. The cap matters:
uncapped, a large enough number would swamp every other term and the ranking
would become "whoever got the most money", the opposite of the point.

This is **not** a capital-budgeting optimiser. Research report §16.3 puts
real constrained optimisation (knapsack under a hard budget cap) at Phase 3+,
once trustworthy per-project cost data exists — which §14 shows it does not.

---

## Summary of every simplification — now mostly RESOLVED

Six of the eight proxies below have been replaced with real government data.
Sources, schemas and verification are in
[`../REAL_DATA_RESEARCH.md`](../REAL_DATA_RESEARCH.md); the scoring code reads
them via [`realdata.py`](realdata.py).

| # | Was | Now | Status |
|---|---|---|---|
| 1 | `infra_deficit` from reported severity | Census facility records + PMGSY works, **measured against each scheme's own norm** (PMGSY / IPHS / RTE / JJM) | ✅ real |
| 2 | `vulnerability` from settlement size | Census deprivation: distance to district HQ, summer power hours, drainage, digital access. **No caste data, by standing decision** | ✅ real |
| 3 | `equity` from a 10-block hardcoded list | **BharatNet 2022** gram-panchayat fibre status, applied proportionally rather than as a binary boost | ✅ real |
| 4 | `strategic` from a population threshold | Real published eligibility rules for all four categories | ✅ real |
| 5 | Unique reporters ≈ distinct report text | unchanged | ❌ needs real citizens |
| 6 | Gazetteer `block` by nearest-HQ geometry | unchanged (LGD loader not yet written) | ⬜ outstanding |
| 7 | What-if as a strategic-points nudge | unchanged | ⬜ Phase 3+ |
| 8 | Equal Gap Score weights | NITI ADP cross-sector weighting **implemented but default OFF** — see below | ⚠️ available, not applied |

### Term-by-term provenance is now reported, not assumed

`score_cluster()` returns a `data_basis` map naming the source each term
actually used on that cluster (`government_records` vs
`proxy_reported_severity`, and so on), plus an `evidence` block carrying the
underlying figures — IPHS entitlement vs facilities present, doctor vacancy
rate, JJM coverage percentage, undelivered sanctioned works and their oldest
sanction year. The dashboard can therefore show provenance **per term**, not
per score.

Where no government record covers a cluster, the old proxy still runs and the
confidence gate is multiplied by `NO_REAL_DATA_CONFIDENCE_FACTOR` (0.85), so a
guessed score is visibly less certain than a recorded one. It dampens and never
zeroes: a village with no recorded data is not a village with no needs (§16.1).

### Why NITI's weights are implemented but switched off

Applying NITI Aayog's published sector weights (Health 30 / Education 30 /
Agriculture & Water 20 / Basic Infrastructure 10) as a cross-category
multiplier was tried against the live 113-cluster set:

| Category | Best score | Rank |
|---|---|---|
| health | 72.56 | #1 |
| education | 60.77 | #10 |
| water | 39.34 | #44 |
| **road** | **31.48** | **#64 of 113** |

No road cluster reached the top 50. A system built to surface citizen
infrastructure demand would have become structurally unable to recommend a
road. The weights are real; **the mapping is the weak link** — NITI's "Basic
Infrastructure" bucket covers electrification, housing and sanitation as well
as roads, and its "Agriculture & Water" bucket is largely irrigation, not
drinking water. Compressing four complaint categories onto those buckets
asserts something NITI never published.

So it remains available and versioned (`APPLY_CATEGORY_IMPORTANCE` in
config.py) as a lever the deploying government body can pull — which is what
§16.4 asks weights to be — rather than one switched on for them.

With it off, the ranking carries all four categories (top 30: 18 health,
9 road, 2 education, 1 water). Health leads on a real finding, not an
artefact: 491 of 942 villages have neither a sub-centre nor a PHC.

Stating all of this plainly is the point, not an apology for it — research
report §19.2 and §20.3: a judge who catches one overclaim discounts everything
else shown, including the parts that are genuinely real.
