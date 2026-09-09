# Frontend — AwaazIQ

Two pages, one for each side of the system.

| Page | Role | Does |
|---|---|---|
| `report.html` | **Citizen** | File a complaint in any language; look up what happened to it |
| `index.html` | **Officials** | The demand map, the ranked worklist, the nine-term score, what-if, and RUN CLUSTERING |

They are separate pages rather than one page with a toggle, because the two
roles want opposite things. A citizen does one thing once, on a phone, and
needs plain language and no jargon. An official compares many things at a
glance on a desktop, and needs density. A single layout serving both would
serve neither.

**The split also decides who may do what.** Only the officials' page can run
clustering: a recompute re-embeds every report, re-runs DBSCAN and re-scores
all 113 clusters, reordering every district's worklist. That is an
administrative decision, not a side effect of one villager pressing a button.
The citizen instead gets a tracking view that reports the truth whenever they
ask — including "not grouped yet", which is the normal state for a fresh
complaint.

Each page links to the other in its top bar, and carries `?api=` across, so a
dashboard pointed at a non-default backend never sends citizens to a
different one.

## Run it

Two servers. The API first, from `backend/`:

```bash
cd backend
uvicorn main:app --reload --port 8000
```

Then serve this folder over HTTP — **not** by double-clicking `index.html`,
because browsers block `fetch` on `file://` URLs and the map data will fail
to load:

```bash
cd frontend
python -m http.server 5500
```

Then open whichever role you want:

| | |
|---|---|
| Citizen | <http://127.0.0.1:5500/report.html> |
| Officials | <http://127.0.0.1:5500/index.html> |

If the API is on a different port, pass it: `?api=http://127.0.0.1:8010`.

## What's on screen

| Region | Does |
|---|---|
| **Rail** (left) | Ranked worklist, filtered by category, following whatever the map is showing. Tick two rows to compare them. |
| **Map** (centre) | The zoom ladder — India → Maharashtra → district → cluster |
| **Detail dock** (below the map) | Opens on a cluster, full width, in three columns: the nine terms — each expanding onto the record it was computed from — then the work groups, then the reports |
| **What-if sheet** (bottom) | Adds budget to a district and re-scores; the rail shows deltas and reorders |
| **Compare** (overlay) | Two clusters side by side, term by term, with a verdict generated from their actual numbers |
| **Run clustering** (top bar) | Officials-only. Re-runs the whole P3 pass, then reloads — every cluster id may have changed |

## The citizen loop

`report.html` closes the circle the dashboard only reads from. Its field set
is modelled on CPGRAMS, the Government of India's public grievance portal,
because a complaint system that asks for less than CPGRAMS does is not one a
department could actually act on.

### What CPGRAMS asks for, and what we do

| CPGRAMS | AwaazIQ |
|---|---|
| Register an account first — "grievance can now be lodged only by registered users" | No account, no password. Identity is captured per report |
| Name, gender, address, pincode, mobile, e-mail | Same, all mandatory |
| Mobile verified by OTP | Not implemented — no SMS gateway |
| Country → state → district dropdowns | State → district → **block → village**, one level deeper, from our own gazetteer |
| Ministry → Department → Sub-organisation | Problem type → line department (PWD, ZP Works, ZP Water, MJP, ZP Health, DHO, ZP Education, DEO) |
| Subject + description up to 4,000 chars | Free-text description in any language, or an audio recording |
| Attachments: PDF, 4 MB each, up to 5 | Photos or PDF, 4 MB each, up to 5 |
| Unique registration number, status tracked against it | Same |

The account is the deliberate omission. A villager filing one complaint in
their life should not have to invent and remember a credential, and an
account is not what makes a grievance actionable — a reachable person and a
resolvable place are, and both are collected here.

The village dropdown is the deliberate addition. Every option it offers is a
village the geocoder already knows, so a complaint cannot be stranded by a
place name nobody could match. Reports filed this way arrive with real
coordinates and can always be clustered.

### The flow

1. The citizen fills the form. Client-side validation mirrors the server's
   rules and names each field that needs fixing; the server enforces all of
   it again regardless.
2. `POST /citizen-report` runs the real pipeline over the description. The
   result screen shows each stage's actual output and marks any stage that
   could not resolve, rather than showing a tick.
3. Where the citizen's chosen problem type disagrees with what the pipeline
   read in the description, the receipt says so. Routing follows the citizen's
   choice; grouping follows the description, because that is what gets
   compared against other people's descriptions.
4. `GET /citizen-report/{id}` answers the tracking view with one of three
   honest states:

| State | Means |
|---|---|
| Needs a place | Location never resolved, so it cannot join a cluster as written |
| Waiting for others | Resolved, but no corroborating reports yet. Normal for a fresh complaint, not an error |
| Grouped and ranked | In a cluster, with report count, distinct reporters, people affected, score and rank |

### Identity, and why the dashboard still cannot see it

Identity **is** collected now — it has to be, or nobody can be replied to.
It goes to `citizen_identity`, a table that nothing in the analytics or
dashboard path reads. `clusters`, `clusters/{id}/reports`, `citizen-reports`,
`map-data` and `citizen-report/{id}` were each checked against a stored name,
mobile, e-mail, pincode and address, and none returns any of them.

Phone numbers and self-stated names are additionally stripped from the
description itself before storage (`redact_pii`), because `raw_text` is what
clustering embeds and what the officials' panel renders. The untouched
original stays in `citizen_request_raw`.

So the cluster panel's claim holds exactly as before: it can show what was
reported, from where, in which language and when — never by whom. The
difference is that this is now enforced by which tables the dashboard is
allowed to read, rather than by the fact never having been recorded.

## Reading a score, not just seeing one

Every term in the dock opens onto its own working. The engine already computed
all of it to produce the number and then discarded it; it is now persisted on
`priority_score.evidence` and returned by `GET /clusters/{id}`.

| Term | Opens onto |
|---|---|
| Citizen demand | Distinct reporters, total reports, the 25-reporter cap, settlements |
| Population affected | The headcount, the catchment radius, how many villages were counted, and the largest of them by name |
| Infrastructure deficit | Facility counts against the scheme's own norm; for roads, sanctioned works still undelivered, their rupee value and the oldest sanction year |
| Deprivation | Distance to district HQ, summer power hours, drainage and internet-centre shares |
| Equity — silent need | Gram panchayats checked against BharatNet and how many have fibre down |
| Scheme eligibility | **Which scheme**, its exact rule, entitled-versus-existing, and what comparable work has actually cost |
| Feasibility | The Census-recorded distance to the sub-district HQ |

So "scheme eligibility +4.00" reads as *PMGSY, rule "population ≥ 500 and no
all-weather road", qualifying village Girgaon, comparable work ₹27.19 lakh/km
across 159 sanctioned works.* And "infrastructure deficit +14.17" reads as
*two sanctioned works undelivered, ₹491.3 lakh already committed, the oldest
sanctioned in 2006.*

## Which specific thing is broken

A cluster answers *where* the money should go. It could never answer *which
road*, because every report carries its village's centroid — inside a village
the geographic distance between two reports is exactly zero, so nothing
separated them.

`work_group` is the answer: reports within 250 m of each other, grouped by
single-linkage, rebuilt on every recompute. The cluster stays the funding
unit; a work group is the place a crew is sent.

Today a group resolves to a village, which is already worth showing since a
cluster routinely spans several. When the intake form supplies a GPS pin the
identical code separates two roads inside one village, with no change to the
grouping. The panel says which of those two situations it is looking at
rather than implying precision it does not have.

### Naming the actual school, road or clinic

A village is not an answer to "which one". Two registers now supply names:

| Sector | Register | Coverage |
|---|---|---|
| Education | **UDISE+** school directory (DataMeet's GeoJSON mirror), 9,352 schools across Kolhapur and Nashik with official school codes and coordinates | 106 of 106 work groups get named candidates |
| Road | **PMGSY** sanctioned works, already loaded, matched to a village by endpoint name | 13 of 108 groups name a real work |
| Health, water | none loaded | falls back to the village, and says so |

**A name is asserted only when it is unambiguous.** Where one facility is in
range, the group is titled with it and credited to the register that supplied
it. Where six schools sit within 2 km of a village centroid, the panel lists
all six with their distances under "6 possible — the register cannot say
which", because measuring from a village centroid genuinely cannot decide
between them, and naming the nearest would be inventing a fact an officer
would then act on.

A GPS pin on the complaint collapses that shortlist to one. That is the
single highest-value thing the intake form could add next.

## Deep links

Every view is addressable, so a specific finding can be sent to someone
rather than described to them:

| URL | Opens |
|---|---|
| `?at=state` | Maharashtra |
| `?at=district:Kolhapur` | Kolhapur district |
| `?at=cluster:12` | Cluster 12, zoomed in, panel open |
| `?compare=18,1` | Ajra vs Shirol, side by side |
| `?whatif=Kolhapur:120` | ₹120 crore to Kolhapur, simulated |
| `?at=cluster:92&mine=2014` | A cluster with one report flagged "yours" |

On the citizen page:

| URL | Opens |
|---|---|
| `report.html` | A blank complaint form |
| `report.html?track=2014` | That report's status, looked up on load |

Backing endpoints added for the form:

| Endpoint | Returns |
|---|---|
| `GET /gazetteer/districts` | Districts that have data |
| `GET /gazetteer/blocks?district=` | Blocks in a district |
| `GET /gazetteer/villages?district=&block=` | Villages, with coordinates |
| `GET /gazetteer/departments[?category=]` | Line departments per issue type |
| `GET /report-attachment/{id}` | One evidence file |

`?compare=18,1` is the one worth bookmarking for a demo: Ajra wins on 7
reports against Shirol's 18, and the verdict line explaining why is generated
from the live scores, not written into the page.

## The zoom ladder

| Level | Shows | Source |
|---|---|---|
| India | 36 states, Maharashtra active | `data/india.topo.json` |
| Maharashtra | 35 districts, those with data highlighted | same file, filtered by `st_nm` |
| District | Demand clusters + individual reports | `/clusters`, `/map-data?layer=reports` |
| Cluster | One cluster isolated, siblings faded | `/clusters` |

## Map data

`data/india.topo.json` — 866 KB TopoJSON, 36 states + 726 districts, Census
2011 boundaries, from [udit-001/india-maps-data](https://github.com/udit-001/india-maps-data).

Its `dt_code` values are the same district codes our Census loaders already
use (Kolhapur `530`, Nashik `516`), so map and database join without a
translation table.

## Six things worth knowing before changing this

**1. Do not set `stroke-width` or `font-size` in CSS for map elements.**
The whole map lives in one scaled `<g>`, so those values are divided by the
live zoom factor in JS to hold a constant on-screen size. A CSS declaration
beats the SVG presentation attribute and silently defeats that — borders
disappear when zoomed out, labels become enormous when zoomed in.

**2. Set `view` to the target transform *before* drawing.**
Bubble radii and stroke widths are computed from `view.k`. Drawing first and
animating after sizes everything against the zoom level you are *leaving*,
which renders district view as a field of giant overlapping blobs.

**3. Match the CSS selector to the class the JS actually emits.**
`loadClusterReports` writes `class="rep rep--new"`; the stylesheet had
`.rep__new`, an element where a modifier was meant. It matched nothing, and
the failure was invisible — the citizen's own report simply came back looking
like every other row. Same failure mode as (1): a selector that silently
matches nothing looks identical to a feature that was never wired up.

**4. Set the map's height as an inline style, not only as the attribute.**
The stylesheet says `#map { height: 100% }`, which outranks the `height`
attribute. When the dock started reducing the map's drawing area, the element
kept rendering at the full stage height while the viewBox claimed the shorter
one, and the browser stretched the difference — putting all 780 marks
thousands of pixels off-screen. Third instance of the same trap as (1) and
(3): CSS quietly outranking what the JS thought it had set.

**5. Reassign `cx`/`cy` on the merged selection, never only on enter.**
`drawClusters`, `drawReports` and `drawLabels` set their coordinates once, at
enter. That was survivable while the projection never changed after boot. The
dock refits the projection every time it opens, so marks born under the old
projection stayed where they were and the map came up empty. Any value derived
from the projection belongs on `enter.merge(sel)`.

**6. Cluster zoom must be absolute, not `view.k * 3.2`.**
Scaling the *current* view is not idempotent, and navigating to the cluster
that is already open is routine — a resize does it, and so does opening the
dock. Twice through gave scale 196. It is now derived from the district's own
`fitTransform`, so it can be recomputed any number of times and land in the
same place.

## Honest scope

Only Kolhapur and Nashik carry citizen data — 2 of 726 districts. The India
view says so rather than implying national coverage, and districts without
data are rendered inactive and are not clickable.

**This is a rural pilot by design, not a temporary gap.** Five of the nine
priority-score terms (demand, population, urgency, feasibility, cost penalty)
are already location-general -- they would work for a city today. The other
four (infra deficit, vulnerability, equity, scheme eligibility) are built
from schemes that only exist in rural governance: PMGSY roads, JJM village
water, BharatNet-to-Gram-Panchayat connectivity, Census Village Amenities.
A municipal corporation has none of those -- it has NUHM health centres,
AMRUT water coverage, and ward-level administration instead, none of which
is loaded.

The confidence-gate in `scoring.py` was already built to degrade gracefully
when a term's real data is missing (falls back to a proxy, applies a 0.85x
confidence penalty) -- so extending to a city is architecturally a data-
loading exercise, not a redesign. It has deliberately not been done for this
pilot: `report.html` states the two-district boundary explicitly on the form
itself (built from the live district list, not hardcoded), rather than
leaving a citizen outside it to discover an empty dropdown and guess why.

## File layout

| File | Owns |
|---|---|
| `index.html` | Officials' dashboard |
| `js/app.js` | Map, projection, navigation, the cluster detail panel |
| `js/panels.js` | Rail, comparison, what-if, run-clustering — talks to the map only through `window.AwaazIQ` |
| `report.html` | Citizen complaint page |
| `js/report.js` | Filing and tracking. Shares no code with the dashboard; it needs neither the map nor D3's heavier machinery |
| `css/app.css` | Design tokens plus everything the dashboard renders, and the few components both roles share (`.btn`, `.steps`, `.kv`, `.sample`, `.role`) |
| `css/report.css` | Only what is specific to the citizen page. Loaded after `app.css` |

`app.js` exposes a deliberately small surface (`navigate`, `onNavigate`,
`getClusters`, `TERMS`, `rankOf`, …). `panels.js` never reaches into the
map's internals. Subscribe with `onNavigate` rather than wrapping
`navigate` — the map triggers navigations itself (clicking a bubble, the
breadcrumb, Back), and a wrapper only sees the ones you call.

## What-if, and why the numbers cap

The slider speaks crore because that is the unit a budget officer uses; the
API takes rupees, so the value is multiplied by 1e7 on the way out.

Deltas cap at **+12.00** by design. Extra budget adds 0.4 points per crore up
to a ceiling of 12, so ₹120 crore and ₹500 crore both land on +12. Without
that cap a large enough number would swamp every other term and the ranking
would become "whoever got the most money" — the opposite of the point.
Clusters are re-scored but never re-clustered: what is broken does not change
because money moved, only what is fundable does.

## Not built yet

Nothing on the P4 list. Remaining work is elsewhere: real voice testing,
deployment, the LGD loader, and finishing the JJM crawl.
