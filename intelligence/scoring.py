"""
P3 Steps 5-7 -- Gap Score, Priority Score, and the explainability breakdown.

The whole thesis of this project lives in this file. Two properties matter
more than raw accuracy, and both are structural rather than aspirational:

1. ADDITION, NOT MULTIPLICATION. Research report SS16.1 shows the brief's
   original all-multiplied formula collapses toward zero as terms are added
   (five average 0.6 factors score 0.078) and that any single missing input
   zeroes a real gap outright. Every term here is added; the only multiplier
   is the confidence gate, which damps and never zeroes.

2. EXACT DECOMPOSABILITY. The nine numbers in `breakdown` sum to
   `priority_score`, to the rounding. That is what lets the dashboard answer
   "why this score" by printing its parts, and what makes the counterfactual
   "why not the runner-up" free to compute (SS10 feature #16). There is
   assertion-backed test coverage for this in test_intelligence.py -- if a
   term is ever added without being surfaced, that test fails.

Honest proxies, all flagged in WEIGHTS.md: infra_deficit is derived from
reported severity rather than PMGSY asset condition (that dataset had no
usable bulk export), and vulnerability is a settlement-size proxy rather
than a published deprivation index.
"""

from __future__ import annotations

import math

from . import config


# ---------------------------------------------------------------------------
# Step 5 -- the four Gap Score terms, each normalised to 0..1
# ---------------------------------------------------------------------------

def demand_term(unique_reporters: int) -> float:
    """
    Evidence strength from citizen reports -- CAPPED.

    Research report SS16.2: report volume is evidence, never demand itself.
    Past DEMAND_SATURATION_REPORTERS distinct reporters the term saturates,
    so a high-connectivity area cannot out-shout a cut-off one simply by
    being able to file more complaints. This single cap is what stops the
    system from re-implementing the popularity contest it exists to replace.
    """
    if unique_reporters <= 0:
        return 0.0
    return min(1.0, unique_reporters / config.DEMAND_SATURATION_REPORTERS)


def population_term(population_affected: int) -> float:
    """
    Log-scaled population, so a city and a hamlet are compared on
    diminishing returns instead of raw headcount -- otherwise dense urban
    clusters would structurally starve every rural one (SS16.2).
    """
    if population_affected <= 0:
        return 0.0
    scaled = math.log1p(population_affected) / math.log1p(config.POPULATION_LOG_CEILING)
    return min(1.0, scaled)


def infra_deficit_term(severities: list[str | None]) -> float:
    """
    PROXY. How far the asset is below an acceptable service level, inferred
    from the severity citizens reported, because PMGSY asset-condition data
    has no confirmed bulk export (research report SS14).

    Swap this for real asset condition before claiming it measures
    infrastructure -- it currently measures what people said about it.
    """
    if not severities:
        return 0.0
    values = [
        config.SEVERITY_DEFICIT.get(s or "", config.DEFAULT_SEVERITY_DEFICIT)
        for s in severities
    ]
    return sum(values) / len(values)


def vulnerability_term(population_affected: int, settlement_count: int) -> float:
    """
    PROXY, AGGREGATE-ONLY. Small, scattered settlements are treated as less
    served than a single large town of the same total population.

    Research report SS16.4 makes this a hard rule, not a preference: this
    term may only ever read published, area-level statistics. Individual
    attributes -- caste above all -- must never enter it, whatever accuracy
    they might buy.
    """
    if settlement_count <= 0 or population_affected <= 0:
        return 0.5  # unknown, not zero: absence of data is not absence of need

    average_settlement = population_affected / settlement_count
    if average_settlement >= config.VULNERABILITY_SMALL_SETTLEMENT_POP:
        return 0.2
    ratio = 1.0 - (average_settlement / config.VULNERABILITY_SMALL_SETTLEMENT_POP)
    return min(1.0, 0.2 + 0.8 * ratio)


def gap_score(demand: float, population: float, infra: float, vulnerability: float) -> float:
    """Stage 1: weighted SUM of the four terms, 0..1."""
    return (
        config.W_DEMAND * demand
        + config.W_POPULATION * population
        + config.W_INFRA_DEFICIT * infra
        + config.W_VULNERABILITY * vulnerability
    )


# ---------------------------------------------------------------------------
# Step 6 -- Priority Score
# ---------------------------------------------------------------------------

def confidence_gate(average_confidence: float) -> float:
    """
    Damps -- never zeroes -- a cluster built on shaky extractions.

    At or above CONFIDENCE_THRESHOLD the gap score passes through untouched.
    Below it, the score is scaled down proportionally. Research report
    SS16.1: a data-completeness artifact must never silently discard a real
    infrastructure gap, which is exactly what a zeroing gate would do.
    """
    if average_confidence <= 0:
        return 0.0
    return min(1.0, average_confidence / config.CONFIDENCE_THRESHOLD)


def equity_points(block: str | None) -> float:
    """
    The Silent Need correction, and the load-bearing term of the entire
    design (research report SS10.4, SS16.2).

    Clusters in low-connectivity/low-literacy blocks get a boost, because a
    given number of reports from a population that struggles to report at
    all is proportionally stronger evidence than the same number from a
    population that reports easily.
    """
    if block and block in config.LOW_CONNECTIVITY_BLOCKS:
        return config.EQUITY_BOOST_POINTS
    return 0.0


def strategic_points(population_affected: int) -> float:
    """
    Stand-in for the Scheme-Eligibility Auto-Matcher (SS10 feature #12): a
    crude population gate, not a real scheme-rules engine. It answers "would
    this plausibly clear an existing scheme's threshold", nothing more.
    """
    if population_affected >= config.STRATEGIC_POPULATION_THRESHOLD:
        return config.STRATEGIC_POINTS
    return 0.0


def urgency_points(issue_category: str | None) -> float:
    """Seasonality: roads and water fail hardest in monsoon (SS16.2)."""
    if issue_category in config.MONSOON_SENSITIVE_CATEGORIES:
        return config.URGENCY_POINTS
    return 0.0


def feasibility_points(distance_to_hq_km: float | None) -> float:
    """Nearer a taluka headquarters is cheaper to actually reach and build."""
    if distance_to_hq_km is None:
        return 0.0
    if distance_to_hq_km <= config.FEASIBILITY_NEAR_HQ_KM:
        return config.FEASIBILITY_POINTS
    return 0.0


def cost_penalty_points(population_affected: int) -> float:
    """
    Bigger, more spread-out interventions cost more. Subtracted, so that a
    cheap high-impact fix can outrank an expensive marginal one -- the
    cost-per-capita framing from SS16.2.
    """
    if population_affected <= 0:
        return 0.0
    scale = min(1.0, population_affected / config.COST_PENALTY_POPULATION_SCALE)
    return config.COST_PENALTY_POINTS * scale


# ---------------------------------------------------------------------------
# Step 7 -- assemble score + breakdown
# ---------------------------------------------------------------------------

def score_cluster(
    *,
    unique_reporters: int,
    population_affected: int,
    settlement_count: int,
    severities: list[str | None],
    average_confidence: float,
    issue_category: str | None,
    block: str | None,
    distance_to_hq_km: float | None,
    extra_strategic_points: float = 0.0,
    # --- real government data, all optional ---------------------------------
    # Every one of these replaces a proxy above. They are optional so a caller
    # with no loaded data still gets a score, and so the fallback path stays
    # explicit rather than hidden behind a silent default.
    real_infra_deficit: float | None = None,
    real_vulnerability: float | None = None,
    real_reporting_deficit: float | None = None,
    scheme_eligible: bool | None = None,
    real_hq_distance_km: float | None = None,
) -> dict:
    """
    Full two-stage score for one cluster.

    Returns priority_score plus the nine-term breakdown whose values sum to
    it. `extra_strategic_points` is how the what-if re-ranking injects a
    budget effect without duplicating any of this logic.

    REAL DATA VS PROXY
    ------------------
    Each `real_*` argument, when supplied, replaces the proxy that used to
    stand in for it:

        real_infra_deficit     <- severity a citizen asserted
        real_vulnerability     <- settlement-size guess
        real_reporting_deficit <- hardcoded list of ten block names
        scheme_eligible        <- bare population threshold
        real_hq_distance_km    <- our own straight-line distance

    When a value is absent the proxy still runs, but the cluster's confidence
    gate is multiplied by NO_REAL_DATA_CONFIDENCE_FACTOR, so a score built on
    guesses is visibly less certain than one built on government records. It
    dampens and never zeroes -- a village with no recorded data must not be
    scored as a village with no needs (research report §16.1).

    `data_basis` in the returned dict names which source each term used, so
    the dashboard can show provenance per term rather than per score.
    """
    demand = demand_term(unique_reporters)
    population = population_term(population_affected)

    data_basis: dict[str, str] = {"demand": "citizen_reports", "population": "census"}

    if real_infra_deficit is not None:
        infra = max(0.0, min(1.0, real_infra_deficit))
        data_basis["infra_deficit"] = "government_records"
    else:
        infra = infra_deficit_term(severities)
        data_basis["infra_deficit"] = "proxy_reported_severity"

    if real_vulnerability is not None:
        vulnerability = max(0.0, min(1.0, real_vulnerability))
        data_basis["vulnerability"] = "census_deprivation"
    else:
        vulnerability = vulnerability_term(population_affected, settlement_count)
        data_basis["vulnerability"] = "proxy_settlement_size"

    gate = confidence_gate(average_confidence)
    if real_infra_deficit is None or real_vulnerability is None:
        gate *= config.NO_REAL_DATA_CONFIDENCE_FACTOR

    # NITI Aayog's published cross-sector weighting, applied to the Gap Score
    # only. Always in (0, 1], so the 0-100 ceiling holds (see config).
    importance = config.category_importance(issue_category)
    data_basis["category_importance"] = (
        f"niti_aspirational_districts:{importance:.3f}"
        if config.APPLY_CATEGORY_IMPORTANCE
        else "disabled"
    )

    # Each Gap Score term's contribution in points, already gated, so the
    # breakdown reflects what actually reached the final number.
    scale = config.GAP_SCORE_MAX_POINTS * importance
    demand_pts = config.W_DEMAND * demand * scale * gate
    population_pts = config.W_POPULATION * population * scale * gate
    infra_pts = config.W_INFRA_DEFICIT * infra * scale * gate
    vulnerability_pts = config.W_VULNERABILITY * vulnerability * scale * gate

    if real_reporting_deficit is not None:
        # Proportional, not binary: a cluster where every gram panchayat's
        # fibre is down has a harder time reporting than one where a third do,
        # and the boost should say so.
        equity = config.EQUITY_BOOST_POINTS * max(0.0, min(1.0, real_reporting_deficit))
        data_basis["equity"] = "bharatnet_2022"
    else:
        equity = equity_points(block)
        data_basis["equity"] = "proxy_hardcoded_block_list"

    if scheme_eligible is not None:
        strategic = config.STRATEGIC_POINTS if scheme_eligible else 0.0
        data_basis["strategic"] = "published_scheme_rule"
    else:
        strategic = strategic_points(population_affected)
        data_basis["strategic"] = "proxy_population_threshold"
    strategic += extra_strategic_points

    urgency = urgency_points(issue_category)

    if real_hq_distance_km is not None:
        feasibility = feasibility_points(real_hq_distance_km)
        data_basis["feasibility"] = "census_recorded_distance"
    else:
        feasibility = feasibility_points(distance_to_hq_km)
        data_basis["feasibility"] = "computed_straight_line"

    cost_penalty = cost_penalty_points(population_affected)

    breakdown = {
        "demand": round(demand_pts, 2),
        "population": round(population_pts, 2),
        "infra_deficit": round(infra_pts, 2),
        "vulnerability": round(vulnerability_pts, 2),
        "equity": round(equity, 2),
        "strategic": round(strategic, 2),
        "urgency": round(urgency, 2),
        "feasibility": round(feasibility, 2),
        "cost_penalty": round(-cost_penalty, 2),  # negative: it is a penalty
    }

    priority = round(sum(breakdown.values()), 2)

    return {
        "priority_score": priority,
        "breakdown": breakdown,
        "gap_score": round(gap_score(demand, population, infra, vulnerability), 4),
        "confidence_gate": round(gate, 4),
        # Which source each term actually used. Kept out of `breakdown` on
        # purpose: breakdown's values must stay numeric and sum to the score,
        # and that invariant is asserted in the tests.
        "data_basis": data_basis,
    }
