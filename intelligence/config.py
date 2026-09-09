"""
Every tunable number P3 uses, in one place.

Build-plan rule (P3 Step 5): weights "must be visible and changeable, never
hardcoded silently inside a function". Nothing in this package may inline a
magic number -- import it from here. The prose justification for each value
lives in WEIGHTS.md next to this file.
"""

# ---------------------------------------------------------------------------
# Step 2/3 -- gating and clustering
# ---------------------------------------------------------------------------

# Candidate pairs are gated to the same issue_category AND this radius before
# any embedding comparison happens (build plan Step 2).
#
# TUNED, not guessed. The build plan's risk table says to tune this against
# the data's real scale rather than trusting a default. Measured on the full
# 1,008-report set (reports spread over 439 villages, ~2.3 per village):
#
#   gate  eps  | clustered | clusters | largest
#   2km   2km  |   25%     |    68    |   11     <- too sparse to demo
#   5km   4km  |   44%     |   113    |   11
#   8km   6km  |   66%     |   113    |   37     <- chosen
#   12km  8km  |   79%     |    75    |  101     <- over-merging
#
# 8km also matches the terrain: villages in Kolhapur/Nashik sit roughly
# 5-10km apart, and a failing road or water main genuinely strands a
# corridor of neighbouring settlements rather than one point. At 12km
# distinct problems start collapsing into one 101-report blob, which is the
# "one giant cluster" failure the build plan warns about.
GATE_RADIUS_KM = 8.0

# Out-of-gate pairs get this pseudo-distance: far beyond any plausible eps,
# so DBSCAN can never link them, without needing a separate masking pass.
GATE_PENALTY_KM = 1000.0

# A fully dissimilar pair of texts (cosine similarity 0) is treated as this
# many extra kilometres apart. Raising it makes clustering more
# semantics-driven; lowering it makes clustering more purely geographic.
SEMANTIC_WEIGHT_KM = 3.0

# DBSCAN parameters, in the same km-ish units as the distance above.
# eps is kept below GATE_RADIUS_KM so the gate is a hard outer bound and eps
# does the actual shaping inside it. min_samples=3 means a cluster needs
# three corroborating reports: two people saying the same thing is not yet a
# demand signal (research report SS10.4).
DBSCAN_EPS_KM = 6.0
DBSCAN_MIN_SAMPLES = 3

# ---------------------------------------------------------------------------
# Step 4 -- population affected
# ---------------------------------------------------------------------------

# Catchment radius per category: who is actually affected by this asset
# failing. A broken road segment strands a wider area than one ward's tap.
CATCHMENT_RADIUS_KM = {
    "road": 5.0,
    "water": 3.0,
    "health": 8.0,
    "education": 4.0,
}
DEFAULT_CATCHMENT_RADIUS_KM = 4.0

# ---------------------------------------------------------------------------
# Step 5 -- Gap Score weights. Must sum to 1.0.
# ---------------------------------------------------------------------------

W_DEMAND = 0.25
W_POPULATION = 0.25
W_INFRA_DEFICIT = 0.25
W_VULNERABILITY = 0.25

# Demand saturates here. Research report SS16.2: report volume is capped
# evidence, never a raw multiplier -- past this many distinct reporters, more
# reports add nothing, so a well-connected area cannot out-shout a cut-off one.
DEMAND_SATURATION_REPORTERS = 25

# log(1 + population) is normalised against log(1 + this), so a city and a
# hamlet compare on diminishing returns instead of raw headcount.
POPULATION_LOG_CEILING = 100_000

# Severity -> infrastructure-deficit proxy. PROXY, NOT REAL DATA: PMGSY road
# condition was not loadable (see research report SS14 / README). Replace with
# real asset-condition data before claiming this measures infrastructure.
SEVERITY_DEFICIT = {"high": 1.0, "medium": 0.5, "low": 0.2}
DEFAULT_SEVERITY_DEFICIT = 0.5

# Vulnerability proxy: village population below this counts as a small,
# less-served settlement. AGGREGATE, AREA-LEVEL ONLY -- research report
# SS16.4 forbids individual-level or caste attributes here, permanently.
VULNERABILITY_SMALL_SETTLEMENT_POP = 2_000

# ---------------------------------------------------------------------------
# Step 6 -- Priority Score
# ---------------------------------------------------------------------------

# Confidence gate: at or above this average confidence the gap score passes
# through untouched; below it the score is damped proportionally. It never
# multiplies by zero -- missing data must not erase a real gap (SS16.1).
CONFIDENCE_THRESHOLD = 0.75

# Point offsets on the 0-100 scale. Deliberately small: the four Gap Score
# terms should dominate the ranking, these only break near-ties.
EQUITY_BOOST_POINTS = 10.0
STRATEGIC_POINTS = 4.0
URGENCY_POINTS = 3.0
FEASIBILITY_POINTS = 2.0
COST_PENALTY_POINTS = 3.0

# The four Gap Score terms are scaled into this many points so that the
# theoretical maximum total lands exactly on 100:
#     81 (gap) + 10 (equity) + 4 (strategic) + 3 (urgency) + 2 (feasibility)
#   = 100, before any cost penalty.
# Chosen over clamping at 100, which would have broken the property that the
# nine breakdown terms sum to the score -- the one guarantee the whole
# explainability story rests on.
GAP_SCORE_MAX_POINTS = 81.0

# Equity: blocks with below-median connectivity/literacy get the boost.
# Build plan Step 6 explicitly sanctions a small hardcoded lookup for MVP.
# These are the hilly/forested western talukas of Kolhapur and the tribal
# western belt of Nashik -- the low-reporting-capacity areas the Silent Need
# Detector exists for. NOT derived from a published index yet; replace with
# real TRAI/Census literacy figures before production use.
LOW_CONNECTIVITY_BLOCKS = {
    "Chandgad",
    "Ajra",
    "Gaganbawada",
    "Radhanagari",
    "Shahuwadi",
    "Bhudargad",
    "Surgana",
    "Peth",
    "Trimbakeshwar",
    "Kalwan",
}

# Categories whose failure is monsoon-sensitive -> urgency offset applies.
MONSOON_SENSITIVE_CATEGORIES = {"road", "water"}

# Strategic offset applies when a cluster plausibly clears an existing
# scheme's population threshold (stand-in for the Scheme-Eligibility
# Auto-Matcher, research report SS10 feature #12 -- not a real scheme-rules
# engine, just a population gate).
STRATEGIC_POPULATION_THRESHOLD = 5_000

# Feasibility: clusters nearer a taluka headquarters are cheaper to reach.
FEASIBILITY_NEAR_HQ_KM = 15.0

# Cost penalty scales with catchment size: a bigger, more spread-out fix
# costs more per unit of benefit.
COST_PENALTY_POPULATION_SCALE = 50_000

# ===========================================================================
# REAL DATA -- published government norms and thresholds
#
# Everything below replaces a proxy that used to live above it. Each value is
# a PUBLISHED GOVERNMENT RULE, not a tuned parameter, and is sourced in
# REAL_DATA_RESEARCH.md §3. They must not be "optimised" -- changing them
# means asserting the government's own standard is something other than what
# it published.
# ===========================================================================

# --- PMGSY road eligibility (NRIDA programme guidelines) --------------------
# An unconnected habitation qualifies for a road at these population floors.
# The scheme's own rule is stated against Census 2001; our population data is
# Census 2011 / PMGSY habitation counts, so this is applied with that
# mismatch documented rather than hidden.
PMGSY_MIN_POPULATION_PLAIN = 500
PMGSY_MIN_POPULATION_HILLY_TRIBAL = 250

# --- IPHS health facility norms (National Health Mission) -------------------
# One facility per this many people. Plain-area figures; hilly/tribal norms
# are stricter (3,000 / 20,000 / 80,000) and would apply in the tribal belts.
IPHS_POPULATION_PER_SUBCENTRE = 5_000
IPHS_POPULATION_PER_PHC = 30_000
IPHS_POPULATION_PER_CHC = 120_000

# --- RTE Act 2009 neighbourhood-school limits -------------------------------
# Statutory, not policy: Section 6 obliges the state to provide a school
# within these walking distances.
RTE_PRIMARY_MAX_KM = 1.0
RTE_UPPER_PRIMARY_MAX_KM = 3.0

# Census records the nearest facility only as a band, so an RTE breach can be
# proven only for band "b" (5-10km) and "c" (>10km). A village in band "a"
# (<5km) may still be in breach at 2km and we cannot tell -- so band "a" is
# NOT counted as a violation. This understates deficit rather than inventing it.
CENSUS_DISTANCE_BANDS_KM = {"a": 5.0, "b": 10.0, "c": 15.0}
CENSUS_BANDS_PROVING_RTE_BREACH = {"b", "c"}

# --- Jal Jeevan Mission -----------------------------------------------------
# JJM's service standard is 55 lpcd via a Functional Household Tap Connection.
# We cannot measure litres, but we can measure the connection: a village below
# full household tap coverage is short of the standard.
JJM_FULL_COVERAGE_PCT = 100.0

# --- BharatNet reporting capacity (equity) ----------------------------------
# GP fibre states that mean "this place is digitally underserved", and so its
# citizens face a higher cost to report anything at all.
BHARATNET_IMPAIRED_STATUSES = {
    "DOWN",
    "UNKNOWN PREV DOWN",
    "NO_STATUS_RECORDED",
    "#N/A",
    "NOT VISIBLE IN NOC",
}

# --- NITI Aayog cross-category importance ------------------------------------
# The Aspirational Districts Programme's published composite weights:
#   Health & Nutrition 30% · Education 30% · Agriculture & Water 20% ·
#   Financial Inclusion 10% · Basic Infrastructure 10%
#
# READ THIS BEFORE CHANGING IT. These are SECTOR weights for ranking districts
# on development. Ours are FACTOR weights for ranking one failing asset. They
# are not the same question, so they are used only for the one thing that IS
# like-for-like: how much a health problem matters relative to a road problem.
#
# Normalised to average 1.0 so switching this on does not deflate every score
# -- it redistributes emphasis rather than shrinking the scale.
#
# CONSEQUENCE, stated plainly: this demotes road clusters relative to health
# and education ones, because NITI weights basic infrastructure at 10% against
# health's 30%. That is a real policy position inherited from a real
# government instrument, not an accident. Set APPLY_CATEGORY_IMPORTANCE =
# False to rank on need alone with no cross-sector view.
NITI_SECTOR_WEIGHTS = {
    "health": 0.30,
    "education": 0.30,
    "water": 0.20,
    "road": 0.10,
}

# DEFAULT OFF -- and the reason is measured, not theoretical.
#
# Switching this on was tried against the live 113-cluster set. The result:
#
#     best health cluster    72.56  rank #1
#     best education cluster 60.77  rank #10
#     best water cluster     39.34  rank #44
#     best road cluster      31.48  rank #64      <- of 113
#
# No road cluster reached the top 50. A system built to surface citizen
# infrastructure demand would have become structurally incapable of ever
# recommending a road, which is not a defensible outcome and is not what the
# NITI weights were built to say.
#
# The mapping is the weak link, not the weights. NITI's "Basic Infrastructure"
# 10% covers electrification, housing and sanitation as well as roads, and its
# "Agriculture & Water Resources" 20% is largely about irrigation rather than
# drinking water. Compressing our four categories onto those buckets asserts
# something NITI never published.
#
# So it stays available, versioned and documented -- a lever a deploying
# government body can pull, exactly as research report §16.4 requires weights
# to be -- but it is not switched on by us on their behalf.
APPLY_CATEGORY_IMPORTANCE = False


def category_importance(category: str | None) -> float:
    """
    NITI sector weight for a category, normalised so the highest-weighted
    sector scores 1.0 and the others are discounted relative to it.

    Normalised on the MAX rather than the mean deliberately: it keeps every
    importance value in (0, 1], so the Gap Score can never exceed
    GAP_SCORE_MAX_POINTS and the 0-100 scale and the sum-to-score guarantee
    both survive. Mean-normalisation would push health clusters to 133% of the
    gap ceiling and break both.
    """
    if not APPLY_CATEGORY_IMPORTANCE:
        return 1.0
    weights = NITI_SECTOR_WEIGHTS
    highest = max(weights.values())
    unknown_default = sum(weights.values()) / len(weights)
    return weights.get(category or "", unknown_default) / highest


# --- Real-data blending ------------------------------------------------------
# How far from a cluster centroid to gather villages whose recorded amenities
# describe that cluster's surroundings.
AMENITY_LOOKUP_RADIUS_KM = 5.0

# When no village near a cluster carries real data, the engine falls back to
# the old severity/settlement-size proxies. Falling back is not free: the
# cluster's confidence is multiplied by this, so a score built on proxies is
# visibly less certain than one built on government records. It dampens, it
# never zeroes (§16.1).
NO_REAL_DATA_CONFIDENCE_FACTOR = 0.85

# ---------------------------------------------------------------------------
# Step 9 -- what-if
# ---------------------------------------------------------------------------

# Extra strategic points granted to clusters in the district receiving a
# positive budget delta, per this many rupees. Deliberately crude: this
# models "more money for this district raises what is fundable there", not a
# real capital-budgeting optimiser (research report SS16.3 puts constrained
# optimisation at Phase 3+).
WHATIF_POINTS_PER_CRORE = 0.4
WHATIF_MAX_POINTS = 12.0
RUPEES_PER_CRORE = 10_000_000
