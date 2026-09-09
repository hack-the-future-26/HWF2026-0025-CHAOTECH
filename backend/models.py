from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
)

from database import Base


class CitizenRequest(Base):
    __tablename__ = "citizen_request"

    id = Column(Integer, primary_key=True, autoincrement=True)
    raw_text = Column(Text)
    language_detected = Column(Text)
    issue_category = Column(Text)
    severity = Column(Text)
    location_raw = Column(Text)
    district = Column(Text)
    block = Column(Text)
    village = Column(Text)
    latitude = Column(Float)
    longitude = Column(Float)
    confidence = Column(Float)
    is_synthetic = Column(Boolean, default=False)
    # Which line department the complaint is routed to -- our equivalent of
    # CPGRAMS's Ministry -> Department -> Sub-organisation choice. Not personal
    # data, so it belongs on the analytical record rather than in
    # CitizenIdentity. NULL on the 1,000 seeded reports, which predate routing.
    department = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    # Set by P3's clustering pass (build plan P3 Step 3). NULL means the
    # report has not been clustered, or DBSCAN judged it noise -- a lone
    # report with nothing corroborating it.
    cluster_id = Column(Integer, ForeignKey("demand_cluster.id"), nullable=True)
    # Registered citizen who filed the report, if authenticated.
    user_id = Column(Integer, ForeignKey("citizen_user.id"), nullable=True)


class CitizenRequestRaw(Base):
    __tablename__ = "citizen_request_raw"

    id = Column(Integer, primary_key=True, autoincrement=True)
    original_text = Column(Text)
    linked_request_id = Column(Integer, ForeignKey("citizen_request.id"))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CitizenIdentity(Base):
    """
    Who filed a report -- kept in its own table, on purpose.

    CPGRAMS requires a registered account before a grievance can be lodged:
    name, gender, full postal address, state, district, pincode, e-mail and a
    mobile number. A grievance with no one behind it cannot be answered, sent
    back for more detail, or checked for abuse, so collecting this is what
    makes a complaint system real rather than a suggestion box.

    It lives apart from CitizenRequest because the analytical half of this
    system must not be able to reach it. Clustering embeds `raw_text`;
    scoring reads counts and coordinates; the officials' dashboard serves
    CitizenRequest rows. None of them join to this table, and no endpoint
    returns it. So the dashboard still cannot answer "who reported this" --
    now because the join is absent by design rather than because the fact was
    never recorded.

    The practical difference from CPGRAMS: there is no account and no
    password. A villager filing one complaint in their life should not have
    to invent and remember a credential, so identity is captured per report.
    """

    __tablename__ = "citizen_identity"

    id = Column(Integer, primary_key=True, autoincrement=True)
    linked_request_id = Column(Integer, ForeignKey("citizen_request.id"), index=True)

    full_name = Column(Text)
    gender = Column(Text)              # male | female | transgender
    mobile = Column(Text)
    email = Column(Text)
    address = Column(Text)             # house / street, as written
    pincode = Column(Text)
    state = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ReportAttachment(Base):
    """
    Evidence a citizen attached: a photo of the broken road, the dry
    borewell, the shut clinic -- or a PDF, as CPGRAMS accepts.

    Only metadata lives here; bytes are written under backend/uploads/ and
    named by a generated token, never by the uploader's filename, so a
    crafted name cannot escape the directory or overwrite anything.
    """

    __tablename__ = "report_attachment"

    id = Column(Integer, primary_key=True, autoincrement=True)
    linked_request_id = Column(Integer, ForeignKey("citizen_request.id"), index=True)

    original_filename = Column(Text)   # for display only, never used as a path
    stored_name = Column(Text)         # the on-disk name we generated
    content_type = Column(Text)
    size_bytes = Column(Integer)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Gazetteer(Base):
    __tablename__ = "gazetteer"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(Text)
    admin_level = Column(Text)
    district = Column(Text)
    block = Column(Text)
    population = Column(Integer)
    latitude = Column(Float)
    longitude = Column(Float)


class VillageAmenities(Base):
    """
    Real per-village infrastructure, connectivity and deprivation facts, from
    the Census 2011 District Census Handbook (Village Amenities schedule).

    This table is what lets the Priority Engine stop inferring infrastructure
    condition from what a citizen *said* and start reading what the government
    itself recorded. See REAL_DATA_RESEARCH.md §2.3.

    Status columns follow the Census encoding, normalised on load:
        1 -> available, 2 -> not available, "NA"/blank -> NULL (unknown).
    NULL genuinely means "not recorded", and must never be treated as 0 --
    absence of data is not absence of the facility (research report §16.1).

    CURRENCY WARNING: this is 2011 data. A facility built since then still
    reads as absent. Cross-check road fields against the PMGSY 2022 network
    (pmgsy_road_category) before asserting a road does not exist.

    Deliberately NOT loaded: the Scheduled Caste / Scheduled Tribe population
    columns present in the same source. Excluded by explicit decision -- the
    vulnerability term is built from facility deprivation and isolation only.
    """

    __tablename__ = "village_amenities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    gazetteer_id = Column(Integer, ForeignKey("gazetteer.id"), unique=True)

    # provenance of the fuzzy name match back to the census row
    census_village_name = Column(Text)
    match_score = Column(Float)

    households = Column(Integer)

    # --- road (feeds infra_deficit for category "road") ---
    road_black_topped = Column(Integer)
    road_all_weather = Column(Integer)
    road_gravel = Column(Integer)
    road_national_highway = Column(Integer)
    road_state_highway = Column(Integer)
    # from the PMGSY 2022 network, not the census -- the freshness cross-check
    pmgsy_road_category = Column(Text)

    # --- water (feeds infra_deficit for category "water") ---
    water_tap_treated = Column(Integer)
    water_tap_treated_all_year = Column(Integer)
    water_tap_treated_summer = Column(Integer)
    water_tap_untreated = Column(Integer)
    water_handpump = Column(Integer)
    water_well_covered = Column(Integer)

    # Jal Jeevan Mission, live from the JJM Citizen Corner -- CURRENT data,
    # unlike everything above it in this block, which is Census 2011 and so
    # predates JJM (launched 2019) entirely.
    # jjm_tap_coverage_pct is the metric JJM's own 55-lpcd Functional
    # Household Tap Connection norm is assessed against.
    jjm_households = Column(Integer)
    jjm_households_with_tap = Column(Integer)
    jjm_tap_coverage_pct = Column(Float)
    jjm_habitation_count = Column(Integer)
    jjm_quality_status = Column(Text)

    # --- health (feeds infra_deficit for category "health", tested vs IPHS) ---
    health_phc_count = Column(Integer)
    health_chc_count = Column(Integer)
    health_subcentre_count = Column(Integer)
    health_doctors_sanctioned = Column(Integer)
    health_doctors_in_position = Column(Integer)

    # --- education (feeds infra_deficit for "education", tested vs RTE Act) ---
    school_primary = Column(Integer)
    school_middle = Column(Integer)
    school_secondary = Column(Integer)
    # census distance band to the nearest facility when absent in-village:
    # "a" = <5km, "b" = 5-10km, "c" = >10km
    school_primary_nearest_band = Column(Text)
    health_nearest_band = Column(Text)

    # --- connectivity (feeds equity / reporting capacity) ---
    # Census 2011 fields. Measured after loading: mobile coverage is almost
    # flat in these two districts (9 of 942 villages lack it), so it carries
    # almost no ranking information and must not be leaned on. Kept for
    # corroboration only.
    conn_mobile_coverage = Column(Integer)
    conn_internet_csc = Column(Integer)
    conn_telephone = Column(Integer)
    conn_bus_public = Column(Integer)

    # BharatNet / BBNL 2022 -- 11 years fresher than the Census, and the
    # signal the equity term should actually lead on. Nearest gram-panchayat
    # fibre node to this village: "UP", "DOWN", or an unknown-status marker.
    # NOTE: this is fibre to the GP office (institutional connectivity), not
    # household mobile coverage. Real and current, but measuring a slightly
    # different thing -- say so wherever it surfaces.
    conn_bharatnet_status = Column(Text)
    conn_bharatnet_gp = Column(Text)
    conn_bharatnet_distance_km = Column(Float)

    # --- isolation and deprivation (feeds vulnerability) ---
    dist_subdistrict_hq_km = Column(Float)
    dist_district_hq_km = Column(Float)
    dist_nearest_town_km = Column(Float)
    power_domestic_summer_hrs = Column(Float)
    power_domestic_winter_hrs = Column(Float)
    drainage_none = Column(Integer)

    source = Column(Text, default="census_2011")


class GovernmentProject(Base):
    """
    A real, individually-sanctioned government work.

    This is the research report's GOVERNMENT_PROJECT entity (§15.1), which had
    no implementation at all until now -- and without it the report's whole
    "Capability E" (citizen demand scored against government investment data,
    §9) cannot exist, because there was nothing to score demand *against*.

    Source: PMGSY's live public dashboard API, per-district, no login
    (REAL_DATA_RESEARCH.md §2.2b). Each row is one road work with its
    sanction date, sanctioned cost, tender/agreement state and current
    execution status.

    IMPORTANT -- what work_status does and does not mean. The endpoint returns
    only works still in the tender/execution pipeline; no row ever carries
    "Completed". So this table is the *backlog*, not the full programme, and
    its row count must never be compared against, or added to, the district
    aggregate's Balance figure -- the two count different universes
    (REAL_DATA_RESEARCH.md §5.3). Phrase findings as "recorded in PMGSY's
    tender/execution pipeline", never as a certified "undelivered" total.
    """

    __tablename__ = "government_project"

    id = Column(Integer, primary_key=True, autoincrement=True)

    source = Column(Text, default="pmgsy_dashboard")
    external_id = Column(Integer)          # IMS_PR_ROAD_CODE
    package_id = Column(Text)              # IMS_PACKAGE_ID
    scheme_name = Column(Text)             # PMGSY I/II/III/IV, PM-JANMAN
    work_name = Column(Text)               # IMS_ROAD_NAME
    road_from = Column(Text)
    road_to = Column(Text)

    district = Column(Text)
    sanctioned_year = Column(Integer)
    sanctioned_cost_lakh = Column(Float)
    length_km = Column(Float)

    agreement_number = Column(Text)
    agreement_amount_lakh = Column(Float)
    days_sanction_to_agreement = Column(Integer)

    # "Not Started" / "In Progress" / "Agreement Cancelled"
    work_status = Column(Text)

    # Where we managed to pin this work to a place we know about. Nullable on
    # purpose: an unmatched work is still a real sanctioned work, and dropping
    # it would understate the backlog.
    matched_gazetteer_id = Column(Integer, ForeignKey("gazetteer.id"), nullable=True)
    match_score = Column(Float)
    match_field = Column(Text)             # which of road_from/road_to matched


class DemandCluster(Base):
    __tablename__ = "demand_cluster"

    id = Column(Integer, primary_key=True, autoincrement=True)
    issue_category = Column(Text)
    centroid_lat = Column(Float)
    centroid_lon = Column(Float)
    report_count = Column(Integer)
    unique_reporters = Column(Integer)
    population_affected = Column(Integer)
    # Added for P3: district drives what-if re-ranking (which district got the
    # budget), block drives the equity lookup (is this a low-connectivity
    # block). Both are the modal value across the cluster's reports.
    district = Column(Text)
    block = Column(Text)
    avg_confidence = Column(Float)
    settlement_count = Column(Integer)


class PriorityScore(Base):
    __tablename__ = "priority_score"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id = Column(Integer, ForeignKey("demand_cluster.id"))
    priority_score = Column(Float)
    breakdown = Column(Text)
    # The working behind each term, as JSON: which scheme and which rule, how
    # many people and in which villages, which sanctioned works are still
    # undelivered and for how much money. recompute.py already computed all of
    # this to produce the numbers and then dropped it on the floor, so a score
    # could be read but never checked. A number an official cannot interrogate
    # is a number they are right not to trust.
    evidence = Column(Text)
    runner_up_cluster_id = Column(Integer, nullable=True)
    computed_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class PublicFacility(Base):
    """
    A named, located public asset: this school, that health centre.

    The gap this fills: village_amenities records that a village HAS two
    primary schools, never WHICH ones. So a cluster could say "education
    problem in Dabhadi" and stop there, when the department needs a name to
    put on a work order.

    Deliberately generic rather than a `school` table. Health and water assets
    belong here too as their registries are loaded, and the matching code
    should not care which sector it is resolving.

    Naming a facility from a report is a claim about the real world, so
    `source` and `external_id` are mandatory in practice -- every row must be
    traceable to the register it came from (a UDISE code, for schools).
    """

    __tablename__ = "public_facility"

    id = Column(Integer, primary_key=True, autoincrement=True)

    source = Column(Text, index=True)        # "udise_2022", "pmgsy", ...
    external_id = Column(Text, index=True)   # UDISE school code, work id
    name = Column(Text)
    category = Column(Text, index=True)      # matches DemandCluster.issue_category
    sub_type = Column(Text)                  # "Primary with Upper Primary", ...
    management = Column(Text)                # government / private / aided

    village = Column(Text)
    block = Column(Text)
    district = Column(Text, index=True)
    latitude = Column(Float)
    longitude = Column(Float)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WorkGroup(Base):
    """
    A unit of work inside a demand cluster: the specific place a crew is sent.

    The cluster stays the funding unit -- it is what carries a priority score
    and what a district compares against other clusters. But "road problem in
    Pimpalgaon Basvant, 19 reports" does not tell PWD which road, and the
    priority engine was never meant to answer that.

    A work group is the answer: reports inside one cluster that sit close
    enough together to be the same broken thing. Today, reports carry their
    village's centroid, so a work group resolves to a village -- already
    useful, since a cluster routinely spans several. Once reports carry a GPS
    pin from the intake form, the same grouping separates two roads inside one
    village without any change here.

    Rebuilt from scratch on every recompute, exactly like clusters.
    """

    __tablename__ = "work_group"

    id = Column(Integer, primary_key=True, autoincrement=True)
    cluster_id = Column(Integer, ForeignKey("demand_cluster.id"), index=True)

    label = Column(Text)               # village, or the landmark people named
    centroid_lat = Column(Float)
    centroid_lon = Column(Float)
    report_count = Column(Integer)
    distinct_reporters = Column(Integer)
    spread_m = Column(Float)           # how far apart its reports actually are
    village = Column(Text)
    block = Column(Text)
    sample_text = Column(Text)         # one real complaint, for the officer

    # The named asset this group is about, where it can be established.
    #
    # asset_label is set ONLY when the answer is unambiguous -- exactly one
    # candidate facility in range. Where a village holds 29 schools, the one
    # nearest its centroid is not the one being complained about, and saying
    # so would be inventing a fact. In that case asset_label stays null and
    # asset_candidates carries the shortlist for a human to choose from.
    asset_label = Column(Text)
    asset_source = Column(Text)              # "udise", "pmgsy"
    asset_external_id = Column(Text)         # UDISE code, PMGSY work id
    asset_candidates = Column(Text)          # JSON: the ranked shortlist
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class CitizenUser(Base):
    """
    Registered citizen account for filing and tracking grievances.
    Stores CPGRAMS-compliant citizen identity details and authentication credentials.
    """
    __tablename__ = "citizen_user"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(Text, unique=True, index=True, nullable=False)
    password_hash = Column(Text, nullable=False)
    full_name = Column(Text, nullable=False)
    gender = Column(Text)              # male | female | transgender
    mobile = Column(Text)              # 10 digits
    address = Column(Text)             # house / street
    pincode = Column(Text)             # 6 digits
    state = Column(Text, default="Maharashtra")
    district = Column(Text, nullable=True)
    block = Column(Text, nullable=True)
    village = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class UserSession(Base):
    """
    Active citizen login session.
    """
    __tablename__ = "user_session"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token = Column(Text, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("citizen_user.id"), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at = Column(DateTime, nullable=True)


class LgdVillage(Base):
    """
    The official administrative hierarchy for one village, from the Local
    Government Directory (Ministry of Panchayati Raj).

    WHY THIS TABLE EXISTS
    ---------------------
    `gazetteer.block` was never an official fact. It came from a nearest-
    taluka-headquarters geometric guess, which the README has always flagged
    as "a geometric approximation, not official boundary data". Measured
    against LGD, that guess agrees with the official taluka for 58% of our
    villages and with the official development block for 48% -- it sits
    between two genuinely different administrative units without being either.

    That matters because block is not cosmetic: it labels clusters, groups
    the worklist, and is what an official reads as the unit of responsibility.
    A wrong block sends a work order to the wrong office.

    LGD settles it. Taluka (subdistrict) and development block are recorded
    separately here, because in India they are separate divisions that often
    but not always coincide -- collapsing them is the mistake that produced
    the ambiguity in the first place.

    CENSUS CODES ARE THE POINT
    --------------------------
    Every level carries its Census 2011 code. That is the join key this
    project has never had: `village_amenities` came from the Census, PMGSY
    carries block ids, and until now the only thing connecting any of them to
    our gazetteer was fuzzy name matching. A code is not a guess.

    gazetteer_id is nullable on purpose: LGD holds every village in the two
    pilot districts, including ones our gazetteer does not, and dropping them
    would throw away the hierarchy a future gazetteer row could join to.
    """

    __tablename__ = "lgd_village"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Our village, where a match was established. NULL means LGD knows this
    # village but our gazetteer does not (yet).
    gazetteer_id = Column(Integer, ForeignKey("gazetteer.id"), index=True)
    match_score = Column(Float)        # fuzzy score, same scale as village_amenities

    # village
    lgd_village_code = Column(Text, index=True)
    village_name = Column(Text)
    census_2011_village_code = Column(Text, index=True)

    # taluka / tehsil / sub-district
    lgd_subdistrict_code = Column(Text)
    subdistrict_name = Column(Text)
    census_2011_subdistrict_code = Column(Text)

    # development block -- a DIFFERENT unit from the taluka above
    lgd_block_code = Column(Text, index=True)
    block_name = Column(Text)

    # district / state
    lgd_district_code = Column(Text)
    district_name = Column(Text)
    census_2011_district_code = Column(Text)
    lgd_state_code = Column(Text)
    state_name = Column(Text)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
