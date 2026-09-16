# Research: from village-level scoring to asset-level scoring

Companion to `STALE_INFRA_DEFICIT_RESEARCH.md` (that doc is about *when* a
government record should be trusted; this one is about *what unit of the
map* gets a score at all). Different problem, same root cause: everything
the Priority Engine currently knows is an area-wide average, never a single
road, school, hospital, or water source.

**The problem, in the user's own framing:** a village can be high-priority
because its school is bad, its road is bad, *and* its hospital is bad — but
today the system cannot say which of those three needs attention first
inside that one village. Zooming into a village on the map should reveal
which specific asset is worst, not just that the village as a whole scored
high.

## Features to build (status: not started — documented only)

- [ ] **Feature 3 — Precise report location capture.** The single blocker
  everything else depends on. See §1.
- [ ] **Feature 4 — Work-group-level scoring** (asset-level priority inside
  a village, not just a label). See §2.
- [ ] **Feature 5 — Real road geometry via PMGSY GeoSadak**, replacing
  village-name-matched road works with actual per-road identity and
  condition attributes. See §3.
- [ ] **Feature 6 — Swap the rainfall source** from the dead-end IMD lead to
  the verified-open MOSDAC GSMaP dataset. See §4.4.

---

## 0. What already exists — read directly from the code, not assumed

Before designing anything, it's worth being precise about what's already
built, because it's more than either of us assumed, and it's currently
disconnected from scoring rather than missing outright.

**`WorkGroup` already does exactly the spatial separation being asked
for**, at 250m single-linkage clustering (`recompute.py`, `_work_groups()`).
Its own docstring states the actual blocker outright:

> "Today, reports carry their village's centroid, so a work group resolves
> to a village — already useful, since a cluster routinely spans several.
> **Once reports carry a GPS pin from the intake form, the same grouping
> separates two roads inside one village without any change here.**"

That is: the mechanism to tell two roads inside one village apart already
exists and requires **zero code changes** to activate — it's blocked purely
by reports not carrying a precise coordinate today. This is Blocker A, and
it's the most important finding in this doc.

**`PublicFacility` already gives specific-facility identity for schools and
health**, and it's already matched to reports via `realdata.name_work_group()`
— a named UDISE school or NIC-registered PHC, not a generic "education
problem in Dabhadi." Its own docstring: *"the department needs a name to
put on a work order."* This exists today. What doesn't exist is that name
carrying its *own* score — it's rendered as a label only, per the earlier
research doc's finding.

**Roads are the weak link, and it's worse than "aggregate."**
`load_pmgsy_works.py`'s own docstring: *"every record carries `IMS_ROAD_FROM`
and `IMS_ROAD_TO` — real place names — works can be matched to villages by
name, **with no geometry and no spatial join**."* So today, a road complaint
isn't matched to a specific road at all — it's matched to "whichever PMGSY
work happens to share a fuzzy-matched village name," which breaks down
completely if a village has two sanctioned works, or if a road runs through
a village with no fuzzy-name-matchable endpoint. This is Blocker B, and
it's exactly where your PMGSY GeoSadak source (§3) genuinely helps.

**Nothing computes a work-group's own severity or priority score.** Only
the parent `DemandCluster` gets a `PriorityScore` row today. A work group
has a label, a report count, a spread — never a score of its own.

## 1. Blocker A — precise report location (Feature 3)

Nothing below matters until this is fixed, because `_work_groups()` cannot
separate two roads that both resolve to the same village centroid, no
matter what data is layered on top.

**What's needed:** a real coordinate per report, not the village's
centroid. Two independent paths, either sufficient on its own:

1. **A map pin in the citizen intake form** — "tap or drag to your exact
   location" — the most reliable, and the one the `WorkGroup` docstring
   itself names as the fix.
2. **Photo EXIF GPS**, when present (already researched in
   `SESSION_LOG_2026-09-12.md`: real when a citizen uploads through the web
   form directly, stripped by WhatsApp's default send). A secondary signal,
   not a replacement for the map pin, since presence/absence proves nothing
   either way.

Nothing here needs a new external data source — it's an intake-form and
pipeline change (`frontend/js/report.js`, `pipeline/location.py`,
`routes_citizen_report.py`), independent of everything in this doc's
remaining sections.

## 2. Blocker B — giving a work group its own score (Feature 4)

Once reports carry precise coordinates and `_work_groups()` starts
producing genuinely separate groups (a road here, a school there), each
group needs a severity of its own — otherwise the map still can't say
which one to fix first.

**The mechanism already exists and generalizes cleanly**: apply the
recency+trust-blend design from `STALE_INFRA_DEFICIT_RESEARCH.md`
**at work-group scope** instead of cluster scope — i.e. run it against only
the reports inside that one work group, blended against whatever
asset-specific real data is available for the thing it's matched to:

- **Roads matched to a real PMGSY GeoSadak segment (§3):** that segment's
  own surface/road-category attributes become the work group's
  `real_infra_deficit`, replacing the village-wide share.
- **Schools/health matched to a named `PublicFacility`:** until UDISE/NIC
  expose per-facility staffing or condition data (still an open research
  item from the 09-12 session — never confirmed either way), the work
  group's severity comes from the trust-blend of its own local reports
  alone. That is already a real improvement over today, where one number
  is shared across every school in a 5km radius regardless of which one is
  actually failing.
- **Water,** pending §4.2's verification: same interim treatment as
  schools/health until a genuine per-source signal is confirmed.

**Two-level display, matching what was asked for exactly:** the cluster
(village) keeps its existing role as the *funding* unit — the number that
answers "does this village deserve budget at all." Work groups become the
*dispatch* unit underneath it — sorted by their own score, answering "given
the village gets attention, which specific thing first." On the map: zoom
past the village-level cluster marker and its work-group pins appear, each
independently colored/scored, instead of one undifferentiated blob. This
doesn't replace `DemandCluster`/`PriorityScore` — it adds a `PriorityScore`
row (or an equivalent) scoped to `WorkGroup` alongside it.

## 3. Real road geometry — PMGSY GeoSadak (Feature 5)

**Verified real**, not just claimed: [`datameet/pmgsy-geosadak`](https://github.com/datameet/pmgsy-geosadak)
is a real, active GitHub mirror providing shapefiles by state — `Bound_Block`,
`Habitations`, `Facilities`, **`Road_DRRP`** (the road geometry layer), and
`Proposals` — covering 29 states with road type, surface, and connectivity
attributes, under the Government Open Data License (free reuse, attribution
required). Primary official source confirmed live at
`geosadak-pmgsy.nic.in/opendata/`.
[GitHub: datameet/pmgsy-geosadak](https://github.com/datameet/pmgsy-geosadak),
[India Geodata: open geospatial data index](https://yashveeeeeeer.github.io/india-geodata/)

**What this actually fixes:** `Road_DRRP` gives real line geometry per
road, with its own attributes — this is the difference between "the works
matched to this village by fuzzy name" (today) and "the nearest actual road
segment to this report's coordinate" (with Feature 3's precise coordinates
in place). A road work group can then be matched by real spatial
proximity to a line, not by hoping a village name fuzzy-matches an
`IMS_ROAD_FROM`/`IMS_ROAD_TO` free-text field.

**Not yet verified, and worth checking before building on it:** whether the
`Road_DRRP` attributes for Kolhapur/Nashik specifically include a
*condition* field (e.g. surface type per segment) as rich as the pack's
table implies, or mainly identity/classification fields. Recommend a
concrete check — download the Maharashtra shapefile and inspect its actual
attribute table for these two districts — before committing to it as an
asset-condition source rather than just an asset-identity source.

## 4. The rest of the pack, verified one by one

### 4.1 Bhuvan (ISRO/NRSC) — real portal, but the resolution claim needs a correction

The pack's table says "50cm and 1m HR layers." Checked directly: Bhuvan
has deployed **2.5m resolution nationally**, and **1m natural-colour
imagery for only around 200 cities** — no confirmation of 50cm coverage,
and no confirmation that Kolhapur/Nashik's rural villages fall inside the
1m city list rather than the 2.5m national layer. The archive also sits
behind a login. [Bhuvan Geoportal (data.gov.in catalog)](https://www.data.gov.in/catalog/bhuvan-geoportal-nrscisro),
[NRSC Open EO Data Archive](https://bhuvan-app3.nrsc.gov.in/data/download/index.php)

**Correction to the pack's framing:** at 2.5m (the resolution that actually
applies to rural Maharashtra villages, most likely), Bhuvan is *worse* than
Planet Labs' 3.7m-but-genuinely-applicable-everywhere free research tier,
not better — despite being the domestic, no-foreign-application option.
Don't treat Bhuvan as a drop-in replacement for the Planet Labs plan in
`STALE_INFRA_DEFICIT_RESEARCH.md` §2A without first confirming actual
1m-tier coverage for these two specific districts.

### 4.2 JJM Village Profile / WQMIS — same portal family already being scraped, not confirmed richer

Checked directly: both `VillageProfile.aspx` and `WQMIS` live under the same
`ejalshakti.gov.in` domain already targeted by `load_jjm_water.py`.
**Correction from a live check (2026-09-14)**: the WQMIS *root*
(`ejalshakti.gov.in/WQMIS/`) loads fine, HTTP 200, no forced login — only
one specific deep link (`/Home/login_register`) is a login page, so login
likely gates specific actions rather than all browsing. `VillageProfile.aspx`
also loads (HTTP 200) but lands on an ASP.NET report page identified by
encrypted session-style query parameters, not a raw guessable URL —
consistent with the ASP.NET-postback pattern `load_jjm_water.py` already
handles. Nothing found still confirms these specific pages expose
per-scheme *source coordinates* richer than what's already scraped.
[eJalShakti Village Profile](https://ejalshakti.gov.in/JJM/JJM/Public/Profile/VillageProfile.aspx),
[JJM-WQMIS login](https://ejalshakti.gov.in/WQMIS/Home/login_register?role_id=6AkqcVKF8EGNeDnztoywG9U%3D)

**Verdict: not independently confirmed to be better than what's already
built.** Treat as "worth a follow-up probe with the same technique
`load_jjm_water.py` already has working," per the 09-12 session's own
conclusion about the IoT pilot — not as a ready-to-use new source.

### 4.3 PMGSY GIS documentation — confirms field/layer names, no new capability

The official documentation page is real and explains layer names (`Road_DRRP`
and siblings) and field meanings — useful as a reference once someone is
actually parsing the shapefile from §3, not a capability on its own.

### 4.4 MOSDAC GSMaP rainfall — verified real, and better than the earlier IMD dead-end (Feature 6)

**Verified real, with one caveat found on a live check (2026-09-14)**:
the GSMaP-ISRO Rain dataset itself is real — 0.1°×0.1° grid (~11km),
hourly, IMD-gauge-corrected, covering India from March 2000 onward — and
its product page loads fine (HTTP 200), listing the dataset by name.
**The open-data index page itself returned HTTP 403 to a direct
automated request, even with a real browser user-agent** — response
headers show a Drupal site with visible anti-automation protection,
consistent with bot-blocking rather than the source being genuinely
unavailable. **This needs real request/session handling tested at build
time**, the same way `load_pmgsy_works.py` already fights PMGSY's own
anti-forgery-token dance — it should not be assumed to "just work with no
login" the way this section originally claimed.
[MOSDAC Open Data](https://www.mosdac.gov.in/open-data),
[GSMaP ISRO Rain product page](https://www.mosdac.gov.in/gsmap-isro-rain)

This is a real upgrade over the earlier session's IMD lead, which hit a
connection failure to `imdpune.gov.in` and an API-key requirement at
`data.gov.in` — "found, not confirmed" was the honest verdict then.
**MOSDAC GSMaP should replace that as the rainfall-corroboration source**
for the road-washout signal discussed in the 09-12 session, and for
corroborating water-stress reports alongside the already-verified NWDP
groundwater/canal telemetry.

### 4.5 Sentinel-1/2, Copernicus Data Space — no change from the earlier verdict

Same resolution ceiling already established (10m) — Copernicus Data Space
is a legitimate free, programmatic access route, but doesn't change what
10m can and can't see. No update to the earlier conclusion in
`SESSION_LOG_2026-09-12.md`: not useful for the potholes/dry-taps/staffing
majority of complaints, and PMGSY GeoSadak (§3) is a better fit for roads
specifically than a satellite pass would be anyway, since it's the
government's own as-built road network rather than an inferred image.

### 4.6 UDISE+ (Know Your School, bulk portal), National Hospital Directory — the pack's own table already deprioritizes these

The pack itself notes UDISE+'s bulk portal requires a login/data request,
"Know Your School" is lookup-only (not bulk), and the National Hospital
Directory is explicitly marked "your existing 1,112 named-facility dataset
is preferable." No action needed — what's already loaded via the DataMeet
UDISE+ mirror and NIC HealthGIS remains the best available option for
these two categories' facility identity.

---

## 5. Summary — what actually needs to happen, in order

1. **Feature 3** (precise report coordinates) — the true prerequisite;
   nothing else in this doc functions without it.
2. **Feature 5** (PMGSY GeoSadak `Road_DRRP`) — gives roads the same
   named-asset identity schools/health already have, and potentially a
   real per-segment condition attribute (needs the attribute-table check
   flagged in §3).
3. **Feature 4** (work-group-level scoring) — the actual scoring change;
   depends on both of the above to have something meaningful to score
   against for roads, though it can start improving schools/health
   immediately once Feature 3 lands, using the trust-blend mechanism
   already designed.
4. **Feature 6** (swap IMD → MOSDAC GSMaP) — independent, small, can happen
   any time; not blocked by anything else here.

Everything in `STALE_INFRA_DEFICIT_RESEARCH.md` (recency+trust-blend,
satellite-gated verification) still applies — it now applies at whichever
scope is being scored, cluster or work group, once Feature 4 exists.

## 6. UI hierarchy — confirmed against the actual frontend, not a new nav level

Asked directly (2026-09-14): does this add a new click-through zoom level —
district → village cluster → click → a *new* road/hospital/school zoom
level? Checked against `app.js`'s real navigation, which already has four
levels (India → Maharashtra → District → village cluster).

**Answer: no new level. This enriches the existing village-cluster view.**
Clicking into a village cluster already opens a detail panel whose Column 2
lists work groups by name, and `flyToWorkGroup()` already pans the map to
one. What's missing is that work groups aren't drawn as their own pins on
the map — they're sidebar text you can fly to. The fix is a new map layer
(alongside the existing `gClusters`, `gReports` layers) that draws each
work group as its own scored pin at the same zoom level a village cluster
already opens to, instead of only listing them in the panel.

**Two things gate this being useful in practice, not just implemented:**

1. **Multiple separate pins per village require Feature 3.** Today every
   report in a village collapses to that village's centroid, so a village
   cluster still produces exactly one combined work group — not a separate
   road pin, hospital pin, and school pin. This is the same Feature 3
   dependency as §1, restated for the UI specifically: build the pin layer
   whenever convenient, but it will show one undifferentiated pin per
   village until reports carry a precise coordinate each.
2. **Naming and pinning are separate, and pinning doesn't need a name**
   (clarified 2026-09-14). Schools/health can already be *named* correctly
   today (matched to one real, named `PublicFacility` when unambiguous).
   Roads can't yet — a road complaint is matched to a PMGSY work by fuzzy
   village-name text, not a real per-road link. **But a road pin doesn't
   need a name to exist or be useful**: once Feature 3 gives a report its
   own precise coordinate, that coordinate alone places an exact pin on
   the map — labeled with the complaint text/severity/corroboration count
   instead of an official road name. An officer can act on an exact
   location with no name attached, the same way a pothole report works in
   any city 311 system. Feature 5 (GeoSadak) only *upgrades* an
   already-pinned, already-useful road spot from "unnamed coordinate" to
   "identified as this specific PMGSY road" — it is not a precondition for
   the pin existing at all. So road pins go live the moment Feature 3
   ships; Feature 5 adds a name to them later, independently.

## Sources

- [GitHub: datameet/pmgsy-geosadak — PMGSY National GIS Open Data](https://github.com/datameet/pmgsy-geosadak)
- [India Geodata: open geospatial data index](https://yashveeeeeeer.github.io/india-geodata/)
- [Bhuvan Geoportal (data.gov.in catalog entry)](https://www.data.gov.in/catalog/bhuvan-geoportal-nrscisro)
- [NRSC Open EO Data Archive / download portal](https://bhuvan-app3.nrsc.gov.in/data/download/index.php)
- [eJalShakti JJM Village Profile](https://ejalshakti.gov.in/JJM/JJM/Public/Profile/VillageProfile.aspx)
- [JJM-WQMIS login page](https://ejalshakti.gov.in/WQMIS/Home/login_register?role_id=6AkqcVKF8EGNeDnztoywG9U%3D)
- [MOSDAC Open Data](https://www.mosdac.gov.in/open-data)
- [MOSDAC GSMaP ISRO Rain product page](https://www.mosdac.gov.in/gsmap-isro-rain)
