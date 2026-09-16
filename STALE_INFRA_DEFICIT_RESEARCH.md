# Research: fixing the stale-government-record blind spot

Companion to `SESSION_LOG_2026-09-12.md` (the Shinganapur test) and
`REAL_DATA_RESEARCH.md` (where the government datasets themselves came from).
That session ended on a cliffhanger — several "want me to build it?" offers,
none accepted yet. This is the websearch the user asked for: how do other
real systems solve *this exact problem*, and what does that mean concretely
for `intelligence/scoring.py`.

**The problem, restated precisely, with the real numbers from the log:**

> Shinganapur, Karvir block, Kolhapur. 5 reports filed: *"bridge collapsed
> last night, emergency"*, joining 17 pre-existing reports — 22 total,
> all tagged `severity: high`. Census 2011 says this village's roads are
> 100% all-weather, black-topped. Result: **rank #63 of 114, score 43.12**,
> because `infra_deficit` evaluated to **0.00** — `data_basis.infra_deficit:
> "government_records"` — the real-data path ran, found nothing wrong on
> paper, and gave the 22 citizens zero credit on the largest scoring term.

## What `infra_deficit` and `urgency` actually measure today (asked 2026-09-14)

Read directly from `intelligence/realdata.py`, not paraphrased. The single
most important fact: **none of this is about a specific road, bridge,
school, or hospital.** Every real-data term is an average across every
village within 5km of the cluster's centroid (`AMENITY_LOOKUP_RADIUS_KM =
5.0`, `realdata.villages_near()`) — an area-wide statistic, not a
building-specific one.

| Category | What `infra_deficit` reads | Source | Granularity | What it does NOT know |
|---|---|---|---|---|
| Road | `road_all_weather`, `road_black_topped` (share of nearby villages lacking each) + any stalled PMGSY work nearby | Census 2011 + PMGSY | Area-wide share across ~5km | Nothing about a specific bridge, segment, or current condition |
| Water | `jjm_tap_coverage_pct` — % of households in nearby villages with a working tap | Jal Jeevan Mission | Area-wide, household-weighted | Nothing about one specific handpump/pipeline |
| Health | `health_phc/chc/subcentre_count` **summed** across nearby villages vs. population ÷ IPHS norms; `health_doctors_sanctioned` vs `health_doctors_in_position` **summed** the same way | Census + IPHS statutory norms | Area-wide sum across ~5km | Nothing about whether a named doctor showed up today at a named PHC |
| Education | Share of nearby villages lacking a middle/secondary school | Census 2011 | Area-wide share | Nothing about a teacher's attendance or a specific building's structural state |
| Vulnerability | Mean distance to HQ, mean summer power hours, drainage/internet presence share | Census 2011 | Area-wide average | Same — no building- or person-level fact |
| Urgency | Flat `+3` if `category in {"road","water"}`, else `0` | Hardcoded in `config.py` | None — doesn't read location, time, or any dataset at all | Everything. It is a per-category constant, full stop. |

Specific, named facilities **do** exist in the database — `PublicFacility`
(a named UDISE school, a named NIC-registered PHC) and `GovernmentProject`
(a named PMGSY road work). Today they are used **only** by
`realdata.name_work_group()` to label which asset a work group refers to on
the dashboard (e.g. "Z.P. SCHOOL DABHADI") — that label is cosmetic and
never feeds back into the `infra_deficit` number itself. The score has no
way to know whether that specific named school is standing or collapsing.

**Consequence for hospital/school complaints, asked about directly:** the
user's scenario — a school or hospital genuinely needs attention but the
area-wide Census/IPHS aggregate says the area is fine — is not a road-only
problem. It is structurally identical to Shinganapur, and Feature 1 below
must therefore apply to *every* category's real-data branch (road, water,
health, education) and to vulnerability, not roads alone. This resolves
what was previously an open decision in §5 point 3.

**But verification differs by what's being claimed, even within one
category:**
- *"The building is about to collapse"* — a visible, physical defect, the
  same kind of claim as a pothole. A citizen photo genuinely helps here
  (a camera can see a cracked wall or a leaning structure) — satellite
  still doesn't, a single building is far too small a target even at
  Planet's 3.7m/pixel, but photo verification is real and already partly
  built (`ReportAttachment`).
- *"There is no teacher" / "there is no doctor"* — a presence fact. No
  photo and no satellite can ever verify an absence. The only substitute
  for proof that exists, for this specific kind of claim, in any system, is
  independent corroboration: several unrelated reporters describing the
  same thing in a tight window. This is exactly what Feature 1's burst
  signal already measures — it was never a photo- or satellite-dependent
  mechanism, which is precisely why it's the one piece that generalizes
  to health and education, where neither of the other two verification
  paths exist at all.

## Features to build (status: not started — documented only, per user instruction 2026-09-14)

Two separate features came out of this research. Neither is built. Both are
meant to ship eventually; they are independent of each other and can be
built in either order.

- [ ] **Feature 1 — Recency + Trust-Blend scoring.** No external dependency,
  buildable with data already in the database. In plain terms: instead of
  letting a government record win completely just because it exists, weigh
  it against how old it is and whether a real burst of citizen reports is
  actively contradicting it, and blend the two proportionally. Fixes the
  Shinganapur case (and every case like it) without waiting on anything
  outside this codebase. **Applies uniformly to all four `real_infra_deficit`
  category branches (road/water/health/education) and to `real_vulnerability`
  — not roads only** (confirmed 2026-09-14: the same area-wide-aggregate,
  no-facility-specificity problem exists identically in `_health_deficit`
  and `_education_deficit`, and neither photo nor satellite can verify a
  staffing-absence claim, so corroboration-weighted blending is the only
  mechanism available for those two categories at all). See **§2** below
  for the full mechanism, and the plain-language walkthrough directly under
  this list.
- [ ] **Feature 2 — Satellite-gated verification for catastrophic road/bridge
  failures.** Needs an external Planet Labs Education & Research Program
  application (real university email, unknown approval turnaround) before
  any of it can be built. Only ever fires for a narrow slice: a road
  complaint with catastrophic wording (collapsed/washed out, not ordinary
  disrepair), already corroborated by Feature 1's burst signal, contradicting
  a clean government record. When it fires, it queries a real satellite
  image for that one location instead of relying on either the old record or
  citizen wording alone. See **§2A** below for the full mechanism.

### Feature 1, explained in plain language (no formulas)

Think of the score's `infra_deficit` term today as a judge who, the moment
a government record exists at all, stops listening to anything citizens
say — even if that record is 15 years old and the citizens are describing
something that happened last night. That's the actual bug: not that the
government record is used, but that it's used as the *only* input the
instant it exists.

Feature 1 makes the judge weigh two things before deciding how much the old
record still counts:

1. **How old is the record, really?** A 2011 Census figure is 15 years old.
   Most of what it says is probably still true — a village's basic road
   surface type doesn't usually change. So age alone doesn't discard it.
2. **Is it actually being contradicted right now, by real evidence?** Not
   "did one person complain" — did a *burst* of independent reporters, all
   describing something severe, show up in a tight window (the 22 reports
   in the Shinganapur test)? That's a specific, checkable pattern: compare
   how fast reports are arriving right now against how fast they normally
   arrive for that place. A sudden spike is a real signal; a slow trickle
   of ordinary complaints is not.

Only when **both** are true — old record, **and** a genuine contradicting
burst — does the record's influence shrink, and only proportionally, never
to zero. A worked example: if the record is 15 years old and a real burst
of high-severity reports is contradicting it, the record might end up
trusted at around 60-70% instead of 100%, with citizen severity making up
the rest. That's the difference between the current all-or-nothing rule and
a graded one. A single fake complaint, or a handful of ordinary "road bad"
reports with no burst pattern, moves this number by essentially nothing —
the mechanism only activates on the same kind of genuine, hard-to-fake
corroboration (many independent reporters, tight time window, high
severity) the system already trusts for clustering in the first place.

That's the whole idea. Everything in §2 below is that same logic written as
actual code changes to `scoring.py`, `recompute.py`, and `config.py`.

---

The current code, [`scoring.py:241-246`](intelligence/scoring.py#L241):

```python
if real_infra_deficit is not None:
    infra = max(0.0, min(1.0, real_infra_deficit))       # <- binary: government wins outright
    data_basis["infra_deficit"] = "government_records"
else:
    infra = infra_deficit_term(severities)                # <- only reached if NO record exists at all
    data_basis["infra_deficit"] = "proxy_reported_severity"
```

Once a village has *any* record, however old, it is trusted completely and
citizen severity is discarded. The fallback to citizen wording only fires
when a village has **zero** records — never when it has an old one that
disagrees with fresh, corroborated reports. The session already tried and
rejected the cheap fix ("just flag the disagreement") because *"it just says
these disagree, it doesn't tell you who's right."* The user's actual ask —
paraphrased from their correction in the log — was for something that
**actually resolves** the disagreement, not just displays it.

---

## 1. What real systems do with this exact problem

Five areas researched, each because it's a real deployed system solving
"old authoritative record vs. fresh distributed reports," not a hypothetical.

### 1.1 USGS "Did You Feel It?" — sensor network + crowdsourced reports

USGS's DYFI system is the closest real-world analogue to this project's
whole shape: a sparse network of authoritative sensors (seismic stations)
combined with dense crowdsourced felt-reports, specifically because sensor
density is *lowest exactly where citizen reports are most needed* — stated
directly as the reason the system exists. Intensity is computed as a
consensus/average across responses, and DYFI reports feed into ShakeMap
*within minutes*, refining the sensor-only picture rather than being
overridden by it. Neither source is absolute; both blend into one map.
[Frontiers: USGS "Did You Feel It?"](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2020.00120/full),
[phys.org](https://phys.org/news/2026-01-earthquake-crowdsourcing-tool.html)

**The transferable idea:** sparse/stale authoritative data and dense
real-time citizen data are not rivals where one must win — they're both
inputs to one blended estimate, weighted by how much of each you actually have.

### 1.2 Waze — corroboration-count confidence with automatic decay

Waze's disclosed formula for an incident's confidence score:
`min((comments×10 + thumbs_up×10 + "not there"×-10) / 20, 5)`. An incident
with zero confirmations and two "not there" reports drops to -1 and is
auto-removed. Corroboration *raises* trust, contradiction *lowers* it, and
the score decays toward removal if nothing reconfirms it.
[ResearchGate: Waze reliability evaluation](https://www.researchgate.net/publication/326916618_Evaluating_the_Reliability_Coverage_and_Added_Value_of_Crowdsourced_Traffic_Incident_Reports_from_Waze),
[arXiv: Bayesian fusion of Waze data](https://arxiv.org/pdf/2011.05440)

**The transferable idea:** this project already has the Waze mechanism's
spirit built in — `unique_reporters` is exactly a corroboration count, and
DBSCAN's `min_samples=3` already refuses to call anything a cluster below
three independent reporters. What's missing is *time*: Waze's confirmations
are inherently time-ordered (you can't "not there" an event that hasn't
happened), and this project has never used the timestamp it already stores.

### 1.3 Kleinberg burst detection / rate-anomaly detection

Kleinberg's algorithm (2003) models a stream as a two-state automaton
(normal / bursty) and detects periods where an event's frequency is
"uncharacteristically" high relative to its own baseline — originally built
for email/document streams, now standard for "is this suddenly a big deal."
The simpler, directly-implementable version of the same idea is a sliding
window rate check: compare the arrival rate in a recent window against a
z-score (rare events fall below ~-3σ, or above +3σ for a spike) or a
same-window baseline ratio.
[Kleinberg, "Bursty and Hierarchical Structure in Streams" (PDF)](https://www.cs.cornell.edu/home/kleinber/bhs.pdf),
[Nikki Marinsek: implementing Kleinberg's algorithm](https://nikkimarinsek.com/blog/kleinberg-burst-detection-algorithm),
[RisingWave: statistical anomaly detection in time series](https://risingwave.com/blog/effective-anomaly-detection-in-time-series-using-basic-statistics/)

**The transferable idea:** "20 reports in 72 hours" (the number the session
landed on) is a rate-anomaly claim, and rate anomalies have a standard,
simple formula — a recent-window rate compared against the cluster's own
lifetime baseline rate. No new library needed; this is arithmetic over a
timestamp column already in the database.

### 1.4 Bayesian/conflict-aware sensor fusion — the actual resolution mechanism

This is the literature that answers "who wins when two sources disagree."
The consistent finding across current sensor-fusion research: don't let one
source win outright — quantify the *conflict* between sources and the
*credibility* of each (source reliability, and specifically its freshness),
then blend proportionally. Dempster-Shafer theory (the classic approach) is
explicitly noted to produce "counter-intuitive results" when the conflict is
severe and one source is trusted blindly — which is precisely the
Shinganapur failure mode. Newer approaches maintain a *time-varying source
credibility* that updates via Bayesian rules as new evidence arrives, rather
than a fixed "record exists → record wins" rule.
[Conflict-aware evidence fusion (ScienceDirect)](https://www.sciencedirect.com/science/article/abs/pii/S0952197626019688),
[Information Fusion of Conflicting Input Data (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC5134457/),
[Bayesian Update with Information Quality (PMC)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7514156/)

**The transferable idea:** this is the actual missing piece. The fix isn't
"detect a burst" alone — it's "detect a burst, then use it to *proportionally
discount* the government record's credibility in this specific case,"
not zero it and not ignore it.

### 1.5 Recency-weighted trust / freshness decay — the credibility half of the formula

The standard formula for "how much should an aging data point still count":
exponential decay, `weight = e^(-λt)` (equivalently `2^(-t/half_life)`),
where weight starts at 1.0 and decreases smoothly — Trustpilot's public
example gives reviews full weight for 12 months, 50% at 24 months, 30% at 36
months. The point of exponential rather than a hard cutoff: a 2011 record
isn't worthless in 2026, it's *less certain*, and a hard cutoff would zero
out genuinely-still-true data (most of Census 2011's road-surface-type facts
*are* still true — that's the whole reason it was trustworthy enough to use
in the first place).
[ResearchGate: exponential decay in probabilistic trust models](https://www.researchgate.net/publication/220154469_An_analysis_of_the_exponential_decay_principle_in_probabilistic_trust_models),
[Trustpilot TrustScore explained](https://reviewz.ai/blog/trustpilot-trustscore-calculated),
[Customers.ai: recency-weighted scoring](https://customers.ai/recency-weighted-scoring)

**The transferable idea:** this gives the actual number for "how stale is
2011" — not a boolean "old/not old" flag (which is what the rejected
"conflict flag" idea amounted to), a continuous freshness weight computed
from the record's real age.

### 1.6 Also checked, less directly applicable but worth noting

- **Sakaki et al., "Earthquake Shakes Twitter Users"** — treats each
  citizen report as a noisy sensor and fuses many of them with a Kalman/
  particle filter to localize an event, detecting 96% of JMA-registered
  earthquakes faster than official broadcast. Confirms the general pattern
  (many noisy citizen sensors ≈ one trustworthy signal) but the technique
  itself (particle filtering for spatial localization) is solving a
  different problem than this project has — it already has real GPS/village
  coordinates per report, so it doesn't need to *infer* location from text
  volume the way Twitter-based systems do.
  [ETH PDF](https://www.ra.ethz.ch/cdstore/www2010/www/p851.pdf)
- **311 / civic-complaint clustering research** — confirms spatiotemporal
  clustering (what `intelligence/clustering.py` already does) is the
  standard approach for civic complaints generally, and that population-/
  location-weighted priority scoring (San Diego's streetlight example) is
  standard practice — but nothing in this literature specifically addresses
  the stale-baseline-vs-fresh-report conflict; it's civic-tech precedent for
  the parts already built, not the missing part.
  [Civic Complaint Priority Management System (IJERT)](https://www.ijert.org/civic-complaint-priority-management-system-using-a-hybrid-nlp-and-location-aware-machine-learning-architecture-ijertv15is040648)

---

## 2. Proposed design: recency-weighted trust blending

Combines §1.3 (detect the burst), §1.5 (quantify the record's staleness),
and §1.4 (blend proportionally instead of picking a winner) into one
mechanism. This directly answers the user's correction in the log — it
doesn't just flag that government data and citizen reports disagree, it
computes *how much* to trust each one and blends accordingly.

### Step 1 — Burst/velocity signal (per cluster, at recompute time)

`_load_reports()` in `recompute.py` doesn't currently select `created_at`
even though every `CitizenRequest` row has it down to the microsecond
(confirmed in the session: `2026-09-06 11:39:56.255516`). Add it:

```python
# recompute.py, _load_reports()
"created_at": row.created_at,
```

Then, per cluster, compare the recent arrival rate against the cluster's
own historical baseline — a same-signal rate-anomaly check (§1.3), not an
absolute report count (which `demand_term` already handles, capped at 25):

```python
BURST_WINDOW_HOURS = 72.0          # matches the number the session settled on

def burst_ratio(members: list[dict], now: datetime) -> float:
    timestamps = sorted(m["created_at"] for m in members if m.get("created_at"))
    if len(timestamps) < config.DBSCAN_MIN_SAMPLES:
        return 0.0   # can't be a burst below the corroboration floor that already exists

    window_start = now - timedelta(hours=BURST_WINDOW_HOURS)
    recent = [t for t in timestamps if t >= window_start]
    recent_rate = len(recent) / BURST_WINDOW_HOURS                 # reports/hour, recent

    lifetime_hours = max((now - timestamps[0]).total_seconds() / 3600.0, BURST_WINDOW_HOURS)
    baseline_rate = len(timestamps) / lifetime_hours               # reports/hour, historical

    if baseline_rate <= 0:
        return 1.0 if recent else 0.0
    return recent_rate / baseline_rate    # >>1 means "arriving much faster than usual"
```

Normalize into 0..1 the same way `population_term` already log-scales an
unbounded quantity, so one wild outlier can't blow the scale:

```python
def velocity_term(ratio: float) -> float:
    if ratio <= 1.0:
        return 0.0
    return min(1.0, math.log1p(ratio) / math.log1p(config.VELOCITY_RATIO_CEILING))
```

### Step 2 — Record freshness (per real-data term, at the point it's read)

`realdata.py` already knows which government dataset backed a given
`real_infra_deficit` value (Census amenities vs. PMGSY works vs. JJM), so
each already implicitly has a vintage. Surface it explicitly instead of
leaving it implicit, and turn it into the exponential-decay weight from §1.5:

```python
# config.py — new section, same style as the rest of the file
RECORD_VINTAGE_YEARS = {
    "census_2011": 2026 - 2011,      # 15 -- recompute this from the actual year, don't hardcode 15
    "pmgsy_sanction": None,           # computed per-row from Works.year
    "jjm_survey": 2026 - 2024,        # update when load_jjm_water.py's crawl date changes
    "bharatnet_2022": 2026 - 2022,
}

# Half-life chosen so Census 2011 (15 yrs old) still carries meaningful
# weight for its *slow-changing* facts (road surface type, school presence)
# -- this is deliberately NOT near-zero, because most of what Census 2011
# says is still true. It only needs to yield when actively contradicted
# (Step 3), not decay away on its own.
RECORD_TRUST_HALF_LIFE_YEARS = 10.0  # owner decision 2026-09-14 (was proposed as 20)

def record_freshness(vintage_years: float | None) -> float:
    if vintage_years is None:
        return 1.0   # unknown age (e.g. a live-scraped JJM figure) -- assume fresh
    return 0.5 ** (vintage_years / RECORD_TRUST_HALF_LIFE_YEARS)
```

At 15 years: `0.5 ** (15/20) ≈ 0.59`. Deliberately not near-zero on its own
— staleness alone should not discount a record that nothing contradicts.
It matters only combined with Step 3.

### Step 3 — Blended `infra_deficit`, not a binary switch

This replaces the `if real_infra_deficit is not None: infra = real_infra_deficit`
block with a proportional blend. The government record's influence shrinks
only when it is **both** old **and** actively contradicted by a real,
corroborated burst — never from staleness alone, and never from raw
complaint volume alone (that's still capped elsewhere by `demand_term`'s
25-reporter ceiling, preserving the fraud-resistance argument from the
session: *"the only thing citizen volume can move is demand, capped at 25"*
stays true — this new path is gated on velocity + severity, not volume):

```python
# scoring.py — replaces the current if/else at line 241
if real_infra_deficit is not None:
    freshness = config.record_freshness(real_infra_deficit_vintage_years)
    contradiction = velocity * high_severity_share      # both in [0,1]; needs BOTH to matter
    trust = freshness + (1.0 - freshness) * (1.0 - contradiction)  # floor: contradiction can't push trust below `freshness`... 
    # simpler and clearer: trust = 1 - (1 - freshness) * contradiction
    trust = 1.0 - (1.0 - freshness) * contradiction

    proxy = infra_deficit_term(severities)
    infra = trust * real_infra_deficit + (1.0 - trust) * proxy
    data_basis["infra_deficit"] = (
        "government_records" if trust >= 0.95 else
        f"blended:{trust:.0%}_government_{1-trust:.0%}_citizen_severity"
    )
else:
    infra = infra_deficit_term(severities)
    data_basis["infra_deficit"] = "proxy_reported_severity"
```

Worked through with Shinganapur's real inputs (illustrative — the exact
final score depends on `average_confidence`, which wasn't logged, so treat
this as "what the mechanism does," not a promised final rank):

- `real_infra_deficit = 0.0` (Census: no deficit), 15 years old →
  `freshness ≈ 0.59`
- 22 reports, all `severity: high`, joining within the 72h window → assume
  `velocity ≈ 0.8` after log-scaling, `high_severity_share = 1.0` →
  `contradiction ≈ 0.8`
- `trust = 1 - (1 - 0.59) × 0.8 ≈ 0.67`
- `proxy = infra_deficit_term(["high"]×22) = 1.0`
- `infra = 0.67 × 0.0 + 0.33 × 1.0 = 0.33` → recovers roughly
  `0.25 × 81 × 0.33 ≈ 6.7` of the 20.25 points that were entirely zeroed —
  a real, meaningful, but not overwhelming correction, precisely because the
  government record still counts for something even when contradicted
  (the whole point of blending instead of overriding).

If this doesn't move the rank far enough in practice once real numbers are
run, the tunable is `RECORD_TRUST_HALF_LIFE_YEARS` — a shorter half-life
makes old records yield faster under contradiction. That's a real
policy choice, not a bug, and belongs in `config.py` with the same
"why this number" comment style the rest of the file uses.

### Step 4 — A standalone velocity bonus (separate from the infra blend)

The blend in Step 3 only fires for terms with a `real_*` government
counterpart (`infra_deficit`, `vulnerability`). For everything else, and as
a visible "this is escalating" signal on its own, fold `velocity_term` into
the existing `urgency_points` function — it currently only gives a flat
category bonus ("+3.00 ← flat category bonus, not about THIS problem," as
the session itself put it):

```python
def urgency_points(issue_category: str | None, velocity: float = 0.0) -> float:
    seasonal = config.URGENCY_POINTS if issue_category in config.MONSOON_SENSITIVE_CATEGORIES else 0.0
    burst_bonus = config.URGENCY_VELOCITY_POINTS * velocity   # e.g. up to +3.0 more
    return seasonal + burst_bonus
```

This keeps the breakdown at nine keys — no schema change, no rebalancing of
`W_DEMAND/W_POPULATION/W_INFRA_DEFICIT/W_VULNERABILITY` (which must keep
summing to 1.0, asserted in `test_intelligence.py`), and no change to the
`GAP_SCORE_MAX_POINTS = 81` ceiling math. **Alternative, more invasive
option:** make velocity a genuine fifth Gap Score term at equal weight
(`0.20 × 5` instead of `0.25 × 4`) — mathematically cleaner but touches the
weights-sum-to-1.0 test, `WEIGHTS.md`, and the "four Gap Score terms" wording
throughout `scoring.py`'s own docstrings. Recommend starting with the
urgency-fold version; it's additive and provably doesn't destabilize
anything already working.

### Step 5 — Evidence & UI surfacing

`app.js`'s cluster dock currently renders two provenance states per the
frontend review: *"from a real record"* (green) vs. *"proxy — no government
record"* (orange). This design needs a third: **blended/contradicted**
(e.g. amber), showing both the record's age and the contradiction that
triggered the blend — this is the auditability the project already
promises ("the dock shows why," per its own `scoring.py` docstring), and
it's the honest version of the "conflict flag" idea the session rejected:
instead of "these disagree, you decide," it shows "these disagree, here's
how much each was trusted and why."

### Step 6 — Guardrails already in place, unaffected

Worth stating explicitly in the PR/commit, because it's the fraud-resistance
argument from the session and this design must not weaken it:

- `velocity` only computes off a cluster that **already exists** — DBSCAN's
  `min_samples=3` (`config.py`) still refuses to form a cluster below three
  independent, geographically-gated reports.
- `unique_reporters` already dedupes identical text — a bot spamming one
  copy-pasted message doesn't move `demand`, and won't move `velocity`
  either since `burst_ratio` should count **distinct reporters**, not raw
  rows (use `_unique_reporters`-style dedup inside `burst_ratio`, not the
  raw member list — flag this as a required fix when implementing Step 1,
  not an optional nice-to-have).
- `contradiction` requires `high_severity_share`, not just report count —
  a burst of `severity: low` reports can't discount a government record.
- The blend has a floor of `freshness` (a *fresh* record, e.g. a live JJM
  scrape from this year, is barely discountable even under a severe burst
  — `trust = 1 - (1-1.0)×contradiction = 1.0` regardless of contradiction).
  This is the same principle Waze uses for confirmed-recent reports and
  what the Bayesian conflict-fusion literature calls source credibility.

---

## 2A. Where satellite fits after all — a narrow, gated exception

Follow-up from the user, worth resolving precisely rather than re-litigating
the earlier blanket rejection: satellite was rejected for *general* road
verification (0.4% flooding rate, sub-pixel potholes), not for every
possible use. There is one case where the resolution argument that killed
it elsewhere genuinely doesn't apply: **a bridge, or a washed-out road
segment, spans tens of meters — large enough to be visible at Planet's
3.7m/pixel, where a pothole (car-width or smaller) is not.**

**The gate — satellite is queried only when all of these already hold:**

1. `issue_category == "road"` and extracted `severity == "high"`.
2. Wording matches catastrophic failure, not generic disrepair — this needs
   a small addition to `pipeline/extract.py`'s keyword sets: a
   `CATASTROPHIC_KEYWORDS` set (collapse/collapsed, washed out/bahal gaya,
   gone/gayab, landslide) distinct from the existing severity keywords, so
   "road bahut kharab hai" (ordinary disrepair, the 99.6% case) never
   reaches this path at all.
3. The Step 1 burst/velocity signal already fires — real, independent,
   time-clustered corroboration exists (not one report, not a spam pattern).
4. The existing government record for that village shows near-zero deficit
   — i.e. there is an actual contradiction to resolve, not just a
   confirmation of what's already known.

Only when all four hold does the system spend a Planet Labs image query.
This keeps calls rare (bounded by genuinely corroborated catastrophic
claims, not by raw road-complaint volume) and keeps the 3,000 km²/month
free-tier ceiling irrelevant in practice — each query covers one village's
small footprint, not a district.

**What the query does:** fetch the most recent PlanetScope image for the
cluster centroid and diff it against a pre-report baseline image for the
same location (seasonal baseline, to avoid mistaking normal seasonal
vegetation/water changes for damage — the session's own warning about
"genuine remote-sensing engineering" applies directly here: cloud masking
and baseline normalization are real work, not a one-line diff).

**How the result feeds scoring:** it becomes a fourth possible value beside
`real_infra_deficit` (government) and the citizen-severity proxy — call it
`satellite_infra_deficit` — with a very high trust weight when present,
since it's a direct current observation rather than a historical record or
a self-report:

```python
if satellite_infra_deficit is not None:
    infra = satellite_infra_deficit
    data_basis["infra_deficit"] = "satellite_confirmed"
elif real_infra_deficit is not None:
    # ... existing Step 3 blend (government record vs. citizen severity) ...
```

**This is additive to the recency+trust-blend design, not a replacement
for it.** The blend still runs for: every ordinary road complaint (no
catastrophic keywords), every case where velocity hasn't yet established
real corroboration, and every case while the Planet application is still
pending. Satellite only ever narrows the remaining uncertainty for the one
already-corroborated, already-catastrophic, already-contradicted slice —
it never gates whether that slice gets *any* priority correction at all.

**Explicitly still excluded, and why the reasoning differs by category:**

- **Ordinary road disrepair (potholes, the 99.6% case):** resolution
  problem — physically below what 3.7m/pixel can distinguish. Satellite
  can't help regardless of how the system is gated.
- **Water (dry taps), Health (staffing/distance):** not a resolution
  problem at all — these are *presence facts*, not physical shapes. No
  achievable satellite resolution sees a person absent or water not
  flowing through a pipe. Better imagery does not close this gap; it's the
  wrong kind of sensor for the question.
- **Education — teacher absence:** same presence-fact argument as health.
- **Education — roof damage:** this *is* a physical, visible defect like a
  road, but a single school building's roof is a far smaller target than a
  bridge or a road segment, likely below reliable change-detection at
  3.7m/pixel. Stays a citizen-photo case (`ReportAttachment`, already
  built), not a satellite one — a resolution problem again, just a
  different scale of failure than roads.

**Real external blocker, stated plainly:** unlike the recency+trust-blend
design (buildable today, zero new dependencies), this path needs Planet
Labs' Education & Research Program application approved first — a real
university email tied to the team, per the session's own research, and an
unknown approval turnaround. Treat this as Phase 2, built after and
independent of the recency+trust-blend mechanism, which already handles
the bridge/road-collapse case on its own (just without satellite
confirmation) the moment it ships.

## 3. What this deliberately does NOT touch (recapping the session's own rejections, so this design stays consistent with them)

- **No satellite for the general case.** Already tested against real data:
  0.4% of road complaints even mention flooding, and ordinary disrepair is
  below any accessible resolution. §2A above is the sole, narrow, gated
  exception — catastrophic road/bridge failure only, and only after
  citizen corroboration already exists.
- **No AI vision auto-scoring or deepfake detection.** Rejected as
  adversarial and unreliable; this design keeps severity coming from
  extracted text + corroboration count, never from image content.
- **No mandatory EXIF/GPS.** If photo metadata work happens later
  (duplicate-hash detection, `imagehash`), it stays a *positive* signal only,
  per the session's own conclusion — never a rejection criterion.
- **The record itself is never edited or "corrected."** `real_infra_deficit`
  stays exactly what `realdata.py` computes from Census/PMGSY/JJM. This
  design only changes how much *weight* that number gets in one specific
  cluster's blend — the underlying government data is never rewritten,
  matching the project's existing "never treat any source as absolute
  ground truth" philosophy stated in `scoring.py`'s own docstring.

---

## 4. Config additions needed (draft — final values are a judgment call, see §5)

```python
# intelligence/config.py

BURST_WINDOW_HOURS = 72.0
VELOCITY_RATIO_CEILING = 20.0        # burst_ratio saturates its 0..1 term here
RECORD_TRUST_HALF_LIFE_YEARS = 10.0  # owner decision 2026-09-14 (was proposed as 20)
URGENCY_VELOCITY_POINTS = 3.0        # matches existing URGENCY_POINTS scale
```

## 5. Open decisions this doc does not make for you

1. **Urgency-fold vs. fifth Gap Score term** (§2, Step 4) — surgical vs.
   architecturally "correct." Recommend starting surgical.
2. **`RECORD_TRUST_HALF_LIFE_YEARS`** — 20 years is a starting guess so
   Census 2011 retains ~59% trust today; there's no published standard for
   this specific number, it's a genuine policy choice like
   `LOW_CONNECTIVITY_BLOCKS` already is in this file.
3. ~~Whether `vulnerability`'s real-data path gets the same blend~~ —
   **decided 2026-09-14: yes.** Confirmed while walking through what
   `_health_deficit`/`_education_deficit` actually read (see the new
   granularity table near the top of this doc): they have exactly the same
   area-wide-aggregate, no-facility-specificity shape as the road case, and
   for health/education specifically there is no photo or satellite
   fallback at all — corroboration-weighted blending is the *only*
   verification path those two categories can ever have. Step 3's blend
   must be written against `real_infra_deficit` and `real_vulnerability`
   generically (as already drafted — the formula doesn't reference
   `category`), not gated to roads.
4. **Whether to re-run the actual Shinganapur test** once built, to get a
   real (not illustrative) score and rank — this doc deliberately did not
   fabricate a final "rank #X" number, since `average_confidence` for those
   test reports was never logged.

## 7. A different strategy: get a fresher baseline instead of discounting the old one

Everything above (§2) treats Census 2011 as fixed and adjusts how much it's
*trusted*. Asked directly (2026-09-14): is there simply a **more recent**
government survey that could replace or supplement Census 2011 outright,
rather than statistically discounting it? Checked three real candidates.

### 7.1 Mission Antyodaya — downgraded to "found, not confirmed" after a live check (2026-09-14)

**The concept is real and the right shape**: an **annual** Gram Panchayat/
village-level survey (running since 2017-18), covering 21 development
sectors under the Constitution's 11th Schedule — which includes roads,
water, health, and education infrastructure, the same domains
`VillageAmenities` covers today. Annual cadence would be a fundamentally
different proposition than Census's once-per-decade, 2011-vintage
snapshot, **if the portal is actually reachable** — which a live check
now puts in genuine doubt.

**Corrected after direct verification, not search-result summaries**:
this doc originally called the portal "confirmed real" based on it
appearing in search results — the same mistake the "found, not confirmed"
standard elsewhere in this project exists to catch. A live check found:

- `missionantyodaya.nic.in` (the URL originally cited) — **DNS does not
  resolve at all** ("Non-existent domain"), not merely unreachable.
- A newer domain, `missionantyodaya.dord.gov.in` (also present in the
  original search results, labeled "Mission Antyodaya 2022-23") — DNS
  *does* resolve, to a real Indian government IP block, but the
  connection itself fails (TLS handshake or firewall — inconclusive from
  this network, not a confirmed dead end either).
- The data.gov.in catalog listing for it loads fine (a real page), but no
  working direct download link was confirmed from its content.

**Honest verdict: found, not confirmed** — the same category this
project already uses for the IMD rainfall lead that didn't pan out
(`SESSION_LOG_2026-09-12.md`). **Before any engineering time goes into
this, retry `missionantyodaya.dord.gov.in` from a different network** (a
government-portal connection failure from one vantage point is common and
not conclusive — PMGSY's official portal in §7.4/`ASSET_LEVEL_PRIORITIZATION_RESEARCH.md`
§3 has exactly this pattern, DNS-dead from here but the DataMeet mirror
works fine), and if reachable, download one district's raw file and
inspect its actual columns before committing to it — the same standard
already applied to NWDP, JJM, and BharatNet before any of them were
trusted.

### 7.2 NITI Aayog Aspirational Districts dashboard — real and genuinely real-time, but doesn't cover these two districts

**Real and impressive**: `championsofchange.gov.in` is a genuinely
real-time, monthly-updated dashboard across 49 KPIs spanning health,
education, agriculture/water, financial inclusion, and infrastructure —
exactly the kind of current data this project has been looking for.

**But checked directly against this project's actual scope**:
Maharashtra's Aspirational Districts are **Nandurbar, Osmanabad
(Dharashiv), Washim, and Gadchiroli** — **not Kolhapur or Nashik**. This
source does not apply to the current pilot at all. Worth remembering if
the project ever expands to those four districts, but not a fix for
anything in scope today. Stating this plainly rather than overselling it.
[Champions of Change dashboard](https://championsofchange.gov.in/dashboard),
[PIB: Aspirational Districts overview](https://www.pib.gov.in/FeaturesDeatils.aspx?NoteId=154503&reg=48&lang=2)

### 7.3 NFHS-5 — real, recent, but district-level not village-level

**Real and confirmed**: National Family Health Survey round 5, fielded
2019-21 — roughly 8-10 years newer than Census 2011 — with published
district-level fact sheets covering all 707 Indian districts, including
Kolhapur and Nashik.
[NFHS-5 district factsheets (data.gov.in)](https://www.data.gov.in/catalog/national-family-health-survey-5-nfhs-5-india-districts-factsheet-data),
[NFHS-5 Maharashtra state report (DHS Program)](https://dhsprogram.com/pubs/pdf/FR374/FR374_Maharashtra.pdf)

**The real trade-off**: this is district-level, not village-level — coarser
than `VillageAmenities`' per-village Census facts. It's a genuine
improvement for the *health* category's district-wide deprivation context
specifically (more recent than 2011 for health/nutrition indicators), but
it cannot replace village-level facility counts the way Mission Antyodaya
potentially could across all four categories. Treat as a supplementary
district-level health signal, not a village-level replacement.

### 7.4 Verdict — pursue Mission Antyodaya first, alongside the blend, not instead of it

Recommend treating §7.1 as a parallel, higher-value track: if Mission
Antyodaya's real fields turn out to have genuine per-village discriminating
power (not just repeat the same near-universal "yes" that made Census's
all-weather-road test useless), it becomes a better `real_infra_deficit`
input outright — fresher, and still real government data, not a proxy.
**This does not replace the recency+trust-blend design in §2** — a 2024/
2025 Mission Antyodaya record is still not the same as a report of last
night's bridge collapse, and the same blend logic still applies to
whichever government record is newest. It just means the "how old is this
record" input to that blend could become "1-2 years," not "15 years," for
whichever categories Mission Antyodaya actually covers well.

## Sources

- [Frontiers: USGS "Did You Feel It?" — 20 years of citizen macroseismology](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2020.00120/full)
- [phys.org: Did You Feel It? crowdsourcing tool](https://phys.org/news/2026-01-earthquake-crowdsourcing-tool.html)
- [ResearchGate: Evaluating Waze crowdsourced traffic incident reports](https://www.researchgate.net/publication/326916618_Evaluating_the_Reliability_Coverage_and_Added_Value_of_Crowdsourced_Traffic_Incident_Reports_from_Waze)
- [arXiv: Emergency Incident Detection from Crowdsourced Waze Data using Bayesian Information Fusion](https://arxiv.org/pdf/2011.05440)
- [Kleinberg, "Bursty and Hierarchical Structure in Streams" (PDF)](https://www.cs.cornell.edu/home/kleinber/bhs.pdf)
- [Nikki Marinsek: implementing Kleinberg's burst detection algorithm](https://nikkimarinsek.com/blog/kleinberg-burst-detection-algorithm)
- [RisingWave: effective anomaly detection in time series using basic statistics](https://risingwave.com/blog/effective-anomaly-detection-in-time-series-using-basic-statistics/)
- [ScienceDirect: Conflict-aware evidence fusion for robust cooperative localization](https://www.sciencedirect.com/science/article/abs/pii/S0952197626019688)
- [PMC: Information Fusion of Conflicting Input Data](https://pmc.ncbi.nlm.nih.gov/articles/PMC5134457/)
- [PMC: Bayesian Update with Information Quality under Evidence Theory](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7514156/)
- [ResearchGate: An analysis of the exponential decay principle in probabilistic trust models](https://www.researchgate.net/publication/220154469_An_analysis_of_the_exponential_decay_principle_in_probabilistic_trust_models)
- [Reviewz: How Trustpilot's TrustScore is calculated](https://reviewz.ai/blog/trustpilot-trustscore-calculated)
- [Customers.ai: Recency-weighted scoring explained](https://customers.ai/recency-weighted-scoring)
- [ETH: Sakaki et al., "Earthquake Shakes Twitter Users" (PDF)](https://www.ra.ethz.ch/cdstore/www2010/www/p851.pdf)
- [IJERT: Civic Complaint Priority Management System](https://www.ijert.org/civic-complaint-priority-management-system-using-a-hybrid-nlp-and-location-aware-machine-learning-architecture-ijertv15is040648)
