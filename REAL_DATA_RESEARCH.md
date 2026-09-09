# Real-Data Research — replacing every proxy in the Priority Engine

**Purpose:** the Intelligence Engine (`intelligence/`) currently computes a 9-term priority
score in which **only one input (population) is real government data**. The other eight are
proxies, guesses, or hardcoded lookups — all honestly labelled in
[`intelligence/WEIGHTS.md`](intelligence/WEIGHTS.md), none of them real.

This document records what real, public, verified data exists to replace each one. Everything
below was **tested directly** — files downloaded and their schemas parsed, APIs called and their
responses inspected. Nothing here is taken on trust from a search result or from the research PDF.

Where a finding **contradicts** the original research report
(`From-Complaints-to-Development-Intelligence.pdf`), that is stated explicitly. Three of its
conclusions turned out to be wrong — not through any fault in its method, but because it stopped
at "is there a documented bulk export?" rather than probing the live endpoints.

---

## 1. Summary — what changed

| # | Term | Was (fake) | Now (real) | Status |
|---|---|---|---|---|
| 1 | `infra_deficit` | Citizen's stated severity | Census road/water/health/school status + PMGSY `RoadCategory` | ✅ Solved |
| 2 | `vulnerability` | Settlement-size guess | Census facility-deprivation, power hours, distance, drainage | ✅ Solved (no caste data — deliberate) |
| 3 | `equity` | Hardcoded list of 10 blocks | BharatNet GP fibre status (2022) + Census connectivity (2011) | ✅ Solved |
| 4 | `strategic` | `population ≥ 5000` | Real published norms for **all four** categories (PMGSY / IPHS / RTE / JJM) | ✅ Solved |
| 5 | `unique_reporters` | Distinct report *text* | Proper citizen-ID field | ⚠️ Mechanism fixable; real identities need a live deployment |
| 6 | `block` | Nearest-taluka-HQ geometry | LGD official hierarchy | ✅ Solved |
| 7 | `cost` | Scaled with population | Real per-district ₹/km from PMGSY sanctioned cost ÷ length | ✅ Solved (better than planned) |
| 8 | weights | Equal 25% each | NITI Aayog ADP published sector weights, as cross-category importance | ⚠️ Honest substitute; true outcome-tuning impossible |

**Capability E** (citizen demand scored against government investment data — the doc's
"white space") went from *"mostly unbuildable"* to **two of its three mismatch classes fully
buildable at road level**. See §6.

---

## 2. Verified data sources

### 2.1 PMGSY GeoSadak — bulk geospatial (DataMeet mirror)

Official portal `geosadak-pmgsy.nic.in/OpenData` **failed DNS resolution** from this machine;
the DataMeet GitHub mirror works and is the same data under GODL.

```
https://raw.githubusercontent.com/datameet/pmgsy-geosadak/master/data/<SET>/Maharashtra.zip
SET ∈ { Habitation, Road_DRRP, Proposals, Facilities }
```

| Dataset | Records (MH) | Fields (verified by parsing the .dbf header) |
|---|---|---|
| **Habitation** | **86,003** | `HAB_ID`, `STATE_ID`, `DISTRICT_I`, `BLOCK_ID`, `HAB_NAME`, `TOT_POPULA` |
| **Road_DRRP** | **97,667** | `ER_ID`, `STATE_ID`, `BLOCK_ID`, `DISTRICT_I`, `DRRP_ROAD_`, `RoadCategory`, `RoadName`, `RoadOwner` |
| **Proposals** | **657** | `MRL_ID`, `STATE_ID`, `DISTRICT_I`, `BLOCK_ID`, `CN_CODE`, `PROPOSED_L`, `WORK_NAME`, `IMS_YEAR`, `IMS_BATCH` |
| Facilities | not yet inspected | — |

`RoadCategory` observed values — **this is the road-quality signal**:

```
NH · SH · MDR · RR(ODR) · RR(VR) · RR(TRACK) · OT
```

`RR(TRACK)` = an unpaved track, the worst class. A habitation whose only connection is
`RR(TRACK)` is genuinely unconnected by all-weather road.

`RoadOwner` observed: `PWD`, `MRRDA`, `RD`, `R&B`, `REO`, `RWD`, `RCD`, `OTHERS`.

**Proposals caveat:** only years 2020 (587) and 2021 (70) — PMGSY-III era, not full history,
and it carries no completion status. Superseded for our purposes by §2.2's road-level API.

### 2.2 PMGSY live dashboard API — `pmgsy.dord.gov.in/dbweb` ⭐

**This is the most important find in this document.** Public, no login. `omms.nic.in` is dead;
this is its live replacement.

Session setup: `GET /dbweb` → keep the cookie, scrape `__RequestVerificationToken` from the HTML.

Codes: Maharashtra `stateCode=21` · Kolhapur `306` · Nashik `393`.
(⚠️ JJM uses a *different* state code for Maharashtra — see §2.6.)

#### (a) District aggregates — `POST /dbweb/Home/GetPmgsYTabularData`

Form-encoded: `stateCode`, `districtCode`, `scheme`, `fromDate` (`MM/YYYY`), `toDate`,
`mpConstcode`, `__RequestVerificationToken`.

Returns per district: `Sanctioned_Road_No/Length/Cost`, `Completed_Roads_No/Length`,
**`Balance_Roads_No`/`Balanced_Road_Length`**, **`Unwarded_Road_No`**, `EXPENDITURE`.

Verified (all 34 MH districts returned):

| | Kolhapur | Nashik |
|---|---|---|
| Sanctioned roads | 191 | 305 |
| Completed | 191 | 287 |
| **Balance (not completed)** | **0** | **18 · 50.26 km** |
| Unawarded | 0 | 2 · 2.85 km |
| Sanctioned cost | ₹351.19 cr | ₹791.91 cr |
| Expenditure | ₹372.63 cr | ₹734.73 cr |

**Derived real unit cost:** Kolhapur ₹351.19 cr ÷ 851.7 km ≈ **₹0.41 crore/km** — a genuine,
district-specific government figure for the `cost` term.

#### (b) Road-level works — `POST /dbweb/ChiefSecretary/GetRoadTenderDetails` ⭐⭐

JSON body: `{"StateID":21,"DistrictID":393,"SchemeID":0}`

Returns **individual road works**:

```
MAST_DISTRICT_CODE / NAME · IMS_PR_ROAD_CODE · IMS_PACKAGE_ID
IMS_ROAD_NAME · IMS_ROAD_FROM · IMS_ROAD_TO      ← geocodable village names
IMS_YEAR · IMS_BATCH · PMGSY_SCHEME_NAME · IMS_PAV_LENGTH
IMS_SANCTIONED_DATE · TEND_AGREEMENT_NUMBER · TEND_DATE_OF_AGREEMENT
TEND_AGREEMENT_AMOUNT · DAYS_SANCTION_TO_AGREEMENT
WORK_STATUS · TOTAL_SANCTIONED_COST
```

Verified:

| | Kolhapur | Nashik |
|---|---|---|
| Road-level records | 201 | 351 |
| Not Started | 98 | 144 |
| In Progress | 81 | 130 |
| Agreement Cancelled | 22 | 77 |
| **Sanctioned but undelivered** | **₹334.6 cr** | **₹772.3 cr** |
| Sanctioned ≤2010, still not done | 139 | — |
| Sanction years present | 2000 – 2026 | 2000 – 2026 |
| Schemes | PMGSY I/II/III/IV, PM-JANMAN | same |

Real rows:
```
[In Progress] SH 134 → Dabhil Shelap Parpoli Road   | 2010 | ₹246.66 lakh
[In Progress] Belewadi Kalamma → Madhyal            | 2010 | ₹46.26  lakh
[Not Started] MDR-43 (Vani Kh.) → Nanashi           | 2026 | ₹90.16  lakh
```

**This single endpoint solves Capability E mismatch #2 at road level, with cost and date.**

#### (c) Other endpoints discovered (untested unless noted)

```
/Home/GetPendingUnawarded        (tested — "No data found" for Nashik)
/Home/GetUnawardedPopup          (tested — aggregate day-buckets only)
/Home/GetDataForPMGSYChart       /Home/GetMonthlyIncrementalPMGSY
/Home/GetLocationListForMetric   /Home/PMGSYHalfView
/PMGSYIV/GetPhysicalProgressData /PMGSYIV/GetPMGSY4CardsData
/ChiefSecretary/GetStateOverviewData
/ChiefSecretary/GetRoadForestClearanceDetails
/ChiefSecretary/GetCSDashboardNationalData
/Contractor/GetContractorData    /QMSGradingAbstract/GetQMSGradingAbstract
/GreenTechnology/GetGreenTechnologyData
```

> **Contradicts the research PDF (§14, §27):** it concluded PMGSY project data "would need real
> scraping work" and that OMMS is login-gated. The aggregate and road-level data are both public
> JSON. The PDF's caution was reasonable but wrong.

### 2.3 Census 2011 Village Amenities — already downloaded, 95% unused

```
https://raw.githubusercontent.com/bnamita/Village_Mapping_v2/master/data/csvdata/
  census_split_by_district/530_Kolhapur.csv
  census_split_by_district/516_Nasik.csv
```

`backend/load_demographic_data.py` already fetches these but reads **only**
`Total.Population.of.Village`. The file has **280+ columns**. Unused columns that matter:

| Need | Columns available |
|---|---|
| **Road condition** | `Black.Topped..pucca..Road`, `Gravel..kuchha..Roads`, `Water.Bounded.Macadam`, `All.Weather.Road`, `National.Highway`, `State.Highway`, `Major.District.Road`, `Other.District.Road`, `Foothpath` |
| **Water** | `Tap.Water.Treated` / `Untreated` (+ *Functioning All round the year* / *in Summer months*), `Covered/Uncovered Well`, `Hand.Pump`, `Tube.Wells.Borehole`, `Spring`, `River.Canal`, `Tank.Pond.Lake` |
| **Health** | `Community.Health.Centre`, `Primary.Health.Centre`, `Primary.Heallth.Sub.Centre`, `Maternity.And.Child.Welfare.Centre`, `TB.Clinic`, `Hospital.Allopathic`, `Dispensary`, `Mobile.Health.Clinic` — each with **Numbers**, **Doctors Total Strength**, **Doctors In Position**, **Para Medical strength/in-position** |
| **Education** | Govt/Private Pre-Primary, Primary, Middle, Secondary, Senior Secondary, Degree/Engineering/Medical College, Polytechnic, ITI — status + numbers |
| **Connectivity** | `Mobile.Phone.Coverage`, `Internet.Cafes...Common.Service.Centre..CSC.`, `Telephone..landlines.`, `Public.Call.Office`, `Post.Office`, `Public.Bus.Service`, `Private.Bus.Service`, `Railway.Station` |
| **Isolation** | `Sub.District.Head.Quarter..Distance.in.km.`, `District.Head.Quarter...Distance.in.km.`, `Nearest.Statutory.Town..Distance.in.km.`, `Nearest.Town.Distance.from.Village` |
| **Deprivation** | Power supply hours (domestic/agri/commercial × summer/winter), drainage type, `Total...Households`, ATM/bank/PDS/market presence |

Coding convention: facility status columns are `A.1 / NA.2` (1 = available, 2 = not available).
Where a facility is absent, a companion column gives the distance band of the nearest one:
`a` = <5 km, `b` = 5–10 km, `c` = >10 km.

⚠️ **Caste columns exist** (`Total.Scheduled.Castes.Population.of.Village`,
`...Scheduled.Tribes...`). **Decision: do not use them.** The user explicitly chose to avoid
caste-linked data even in aggregate form. Vulnerability is built from facility deprivation,
isolation and power supply instead.

⚠️ **Currency: 2011.** This is the binding limitation on this source — see §5.

### 2.4 LGD — Local Government Directory (official admin hierarchy)

- Portal: `https://lgdirectory.gov.in`
- Full CSV dump mirror: `https://ramseraph.github.io/opendata/lgd/`
- Also on `data.gov.in/catalog/local-government-directory-lgd`, updated monthly
- Maharashtra-specific: `https://mahavillages.mahabhumi.gov.in/`

Gives the authoritative village → sub-district (taluka/block) → district mapping. Replaces
`repair_data.py`'s nearest-HQ geometric approximation, which the README already flags as
"a geometric approximation, not official boundary data."

### 2.5 BharatNet / BBNL — current connectivity (2022)

```
https://storage.googleapis.com/bbnl_data/parsed.zip     (8.3 MB, no login)
```

| File | Columns |
|---|---|
| `parsed/active_gp_status.csv` | `State Name`, `District Name`, `Block Name`, `GP/ ONT Name`, **`Opn Status`**, `Reason` |
| `parsed/GP_locations.csv` | `STATE`, `DISTRICT`, `BLOCK`, `GP_NAME`, **`LAT`**, **`LONG`** |
| also | `active_gps.csv`, `block_connected_gps.csv`, `FPOI_locations.csv`, `OLT_locations.csv`, `panchayats.csv`, `planned_nofn.csv` |

Maharashtra: **28,133 gram panchayats** — `UP` 12,012 · `DOWN` 8,116 · `#N/A` 6,405 ·
`UNKNOWN PREV UP` 759 · `UNKNOWN PREV DOWN` 709 · `NOT VISIBLE IN NOC` 132.

| | Kolhapur | Nashik |
|---|---|---|
| GPs | **1,035** | **1,397** |
| Fibre UP | 527 | 617 |
| **Fibre DOWN** | **210** | **186** |
| No status | 285 | 530 |

⚠️ **What it does and doesn't measure:** BharatNet is fibre to the *gram panchayat office* —
institutional digital infrastructure. It is **not** household mobile coverage. It is a real,
current digital-access signal, but it answers a slightly different question than "can a citizen
phone in a complaint." Use it **alongside** Census `Mobile.Phone.Coverage`, not instead of it:
two independent signals, one current, one historical. High confidence where both agree.

### 2.6 Jal Jeevan Mission — village/habitation tap-connection data

Citizen Corner page: `https://ejalshakti.gov.in/jjm/citizen_corner/villageinformation.aspx`

ASP.NET WebMethods returning JSON (public):

```
VillageInformation.aspx/BindHabitationInfo          ← the one that matters
VillageInformation.aspx/BindSourceInfo
VillageInformation.aspx/BindSampleTestedInfo
VillageInformation.aspx/BindSource_scheme_wiseInfo
VillageInformation.aspx/LabInfo_List
```

`BindHabitationInfo` body: `{stcode, dtcode, cat:"0", subcat:"0", param:"0", VillageId}`

Returns **per habitation**: Habitation Name · **Rural population** · **Households** ·
**Nos. of tap connections provided** — i.e. the FHTC coverage JJM's 55-lpcd norm is measured
against. The page also exposes water-quality testing (samples tested / safe / unsafe /
remedial action) and school & anganwadi tap connections.

**Maharashtra `stcode` = 18** on JJM (≠ PMGSY's 21 — do not mix the code systems up).

⚠️ **Needs a crawler, not a single call.** `VillageId` comes from ASP.NET postback dropdowns
(`ddState` → `ddDistrict` → `ddList`, with `__EVENTVALIDATION`/ViewState). So: district →
village list → per-village call. Entirely public, just multi-step.

> **Contradicts the research PDF (§14):** it recorded JJM as "browse-only drill-down MIS
> dashboard, no confirmed bulk export." The habitation data is reachable as JSON.

### 2.7 Maharashtra BEAMS — district treasury expenditure

`https://beams.mahakosh.gov.in/Beams5/BudgetMVC/MISRPT/DistrictWiseExpenditure.jsp`

Public, no login. Columns: District Name · Budget Received · Expenditure. Fiscal years
2018-19 → 2026-27. 35 districts + admin units.

**Verdict: dashboard context only, never a scoring input.** It is a single number per district
covering *all departments mixed* (salaries, pensions, police, everything), with no category
breakdown and no export button. A term that is identical for every cluster inside a district
adds **zero ranking information** — it cannot separate cluster #50 from cluster #88. Its one
genuinely interesting use is the received-vs-spent gap, as an "absorption failure" note.

> Worth recording that this *does* exist, because the research PDF concluded district-level
> budget data "does not exist in structured form on the primary government site — full stop."
> That was true of the **Union** budget; it is not true of Maharashtra's **state** treasury.

---

## 3. Scheme eligibility norms — all four categories, verified

These are **published policy rules**, not downloadable datasets. They turn a vague "this place
needs help" into a checkable statement of non-compliance with a government's own standard.

| Category | Scheme / law | The norm |
|---|---|---|
| **Roads** | PMGSY (NRIDA guidelines) | Habitation **≥500** (plain) / **≥250** (NE, Himalayan, tribal Schedule-V, desert) / **≥100** (LWE-affected blocks) that is **unconnected by an all-weather road**. Population basis: Census 2001. Habitations within 500 m (1.5 km path distance in hills) may be clubbed. |
| **Health** | IPHS (National Health Mission) | **1 Sub-Centre per 5,000** (3,000 hilly/tribal/desert) · **1 PHC per 30,000** (20,000) · **1 CHC per 120,000** (80,000) |
| **Education** | **RTE Act 2009** — statutory, not just policy | Primary school (cl. 1–5) within **1 km** walking distance · Upper primary (cl. 6–8) within **3 km**. Section 6 obliges government to establish one where absent. |
| **Water** | Jal Jeevan Mission | **55 litres per capita per day** via a Functional Household Tap Connection, quality per **BIS:10500** |

How well each maps to data we hold:

| Category | Measurable? | Notes |
|---|---|---|
| Health | ✅ Strong | Census gives facility counts **and** population → direct IPHS ratio test. Bonus: *doctors in position vs sanctioned* catches "clinic exists, no doctor." |
| Roads | ✅ Strong | Census all-weather/black-topped status + PMGSY `RoadCategory` + habitation population |
| Education | ✅ Good | School presence + distance band. Limit: bands are `<5 / 5–10 / >10 km`, so a 2 km gap is a real RTE violation we cannot isolate — we can only prove clear ones (>5 km when the law says 1 km). |
| Water | ✅ Good | Needs the §2.6 crawler for FHTC. Census alone predates JJM (2019) so it can only show source type and summer failure, not lpcd. |

### NITI Aayog weights (for the `weights` fix)

Aspirational Districts Programme published composite: **Health & Nutrition 30% · Education 30%
· Agriculture & Water Resources 20% · Financial Inclusion & Skill Development 10% · Basic
Infrastructure 10%.**

⚠️ **These are *sector* weights, answering a different question than our *factor* weights.**
NITI weights "how much does health matter vs education when ranking a district." Ours weight
"how much does population matter vs infrastructure deficit when ranking one broken road."
They cannot be copied across. **Legitimate use:** apply them as **cross-category importance**
— a health cluster carries 30%, education 30%, water 20%, roads 10% relative to each other.
That is like-for-like. The four Gap-Score factor weights stay an openly published, versioned,
adjustable governance setting, exactly as research report §16.4 prescribes.

---

## 4. Capability E — demand vs investment

The doc's white-space claim (§9): no system anywhere joins citizen demand to government
investment data. Its Demand-to-Investment Alignment Engine (§10.2 feature #2) names three
mismatch classes.

| Mismatch | Verdict | How |
|---|---|---|
| **Demanded but unfunded** | ✅ **Real, per-village** | PMGSY population threshold + Census all-weather-road status → "eligible and uncovered" |
| **Funded but undelivered** | ✅ **Real, per-road** | §2.2(b) — 552 road works across the two districts with `WORK_STATUS`, sanction date and cost. ₹334.6 cr (Kolhapur) + ₹772.3 cr (Nashik) sanctioned and not delivered; 139 Kolhapur roads sanctioned ≤2010 still incomplete |
| **Funded where not demanded** | ⚠️ **Mechanism real, finding not yet** | `IMS_ROAD_FROM`/`IMS_ROAD_TO` are geocodable against our gazetteer, so sanctioned works *can* be matched against complaint density. But our complaints are synthetic, so any result reflects our seeding, not reality. Real complaints make it a real finding. |

---

## 5. What is still not fixed

### 5.1 Hard blockers — no dataset can solve these

| # | Blocker | Why no data fixes it |
|---|---|---|
| 1 | **Real citizen identity** (`unique_reporters`) | Requires actual people using the system. The counting *mechanism* can be made correct (a real `citizen_id` instead of comparing report text), but the identities stay synthetic until deployment. |
| 2 | **Outcome-tuned weights** | Requires knowing *"did building this thing improve lives?"* — i.e. before/after welfare measurement at village level. Census 2011 is India's only village-level census and 2021 was delayed, so **there is no "after."** It does not exist for anyone. |

On #2 there is a legitimate alternative now available: compare our ranking against **25 years of
real sanction decisions** (§2.2b). That does not tune to outcomes, but it supports a sharper
claim — *"here is where our score disagrees with what government actually funded."* Note that
learning weights *from* historical funding would bake in the participation bias this project
exists to correct, so this must stay a comparison, not a training target.

### 5.2 Soft limitations — real data, real caveats

| Limitation | Impact | Mitigation |
|---|---|---|
| **Census is 2011** | A road built in 2015 still reads as missing | Cross-check against PMGSY 2022 road network; disagreement between the two is itself a signal |
| **2011 connectivity is the worst-aged field** | Mobile coverage exploded after 2011; a village marked "no coverage" almost certainly has it now. This matters most because **`equity` is the load-bearing term** — if it is wrong, the headline ranking is wrong | Lead with BharatNet 2022 (§2.5); use Census mobile coverage only as a corroborating second signal |
| **BharatNet ≠ household coverage** | Measures fibre to the GP office, not citizen phone access | State it plainly; combine with Census |
| **RTE distance bands are coarse** | Cannot isolate a 2 km violation inside the `<5 km` bucket | Only claim clear violations (>5 km) |
| **JJM needs a crawler** | Multi-step, slower, more fragile than a single API | Build it as a separate loader with caching; fall back to Census water source + summer-failure if it breaks |
| **"Funded where not demanded" needs real complaints** | Mechanism demonstrable, finding not truthful yet | Label as mechanism-demo until a pilot |
| **PMGSY population basis is Census 2001** | The scheme's own rule uses 2001, our data is 2011 | Use 2001 basis where testing eligibility, document the mismatch |

### 5.3 ✅ RESOLVED — the two PMGSY endpoints measure different things

The apparent contradiction (Kolhapur: aggregate said "191 sanctioned / 191 completed / balance 0"
while the road-level endpoint listed 201 incomplete works) is **not a data error**. It was a
misreading of the aggregate's columns. Resolved empirically:

**Test — Nashik (393), varying only the date window:**

| Window | Sanctioned | Completed | Balance | Unawarded |
|---|---|---|---|---|
| 2000 → 2026 | 305 | 287 | 18 | 2 |
| 2020 → 2026 | 73 | 55 | 18 | 2 |
| **2024 → 2026** | **37** | **42** ⚠️ | 18 | 2 |

**In the 2024–26 window, `Completed` (42) exceeds `Sanctioned` (37).** That is impossible for a
single set of roads, and it proves the columns are filtered on **two different date fields**:

- `Sanctioned_Road_No` — roads **sanctioned** inside the window
- `Completed_Roads_No` — roads **completed** inside the window (mostly *different* roads,
  sanctioned in earlier years)
- `Balance_Roads_No` / `Unwarded_Road_No` — **a current snapshot; they ignore the date filter
  entirely** (constant 18 / 2 across every window tested)

So **"191 sanctioned, 191 completed" never meant "all 191 were finished."** It means 191 were
sanctioned in that window and 191 were completed in that window — two overlapping-but-different
sets. The scheme split confirms the aggregate is sane: PMGSY-I 140 + PMGSY-III 39 + PMGSY-II 12
= 191 (`scheme=0` genuinely means All Schemes).

**Residual gap, characterised but not fully closed:** the road-level endpoint returns a *larger*
universe than `Balance` (Kolhapur: 201 pipeline rows vs Balance = 0). Its rows are entirely
`Not Started` / `In Progress` / `Agreement Cancelled` — **no `Completed` value appears at all** —
and 97 of the 98 Kolhapur "Not Started" rows carry a signed agreement number and amount. It is
therefore a **tender/agreement-execution pipeline report** (a Chief-Secretary watchlist),
historical and cumulative, not the same accounting universe as `Balance`.

**Engineering rules that follow — apply these when building:**

1. Use **`Balance_Roads_No` / `Balanced_Road_Length`** as *the* official outstanding-works figure.
   It is PMGSY's own snapshot and is date-filter-independent.
2. Use **`GetRoadTenderDetails`** for **named, located, dated, costed examples** — its value is
   the `ROAD_FROM → ROAD_TO` geocodable detail, not its count.
3. **Never present the two as the same metric and never sum them.**
4. Phrase road-level findings as *"N road works are recorded in PMGSY's tender/execution pipeline,
   totalling ₹X of sanctioned cost, none marked complete in this report"* — **not** as
   *"₹X undelivered"*, which overstates what the endpoint certifies.
5. When quoting the aggregate, always state the date window, because both headline columns move
   with it.

### 5.4 Not yet investigated

- `Facilities/Maharashtra.zip` from GeoSadak (~800k rural POIs nationally) — schema uninspected
- Health/education scheme *funding* data (NHM, Samagra Shiksha) at district level — only norms
  researched so far, not spend
- Whether `GetRoadTenderDetails` returns *completed* works too under different parameters
  (it appears to return only the non-completed backlog)

---

## 5.5 BUILT — what now exists, and what the real data revealed

All five items are implemented and run against live sources. Existing tests
still pass (32/32 intelligence, 46/46 pipeline).

| Loader | What it loads | Result |
|---|---|---|
| `backend/load_village_amenities.py` | Census 2011 amenities → `village_amenities` | **942 / 1,042 villages** (90%) |
| `backend/load_pmgsy_works.py` | Real sanctioned road works → `government_project` | **552 works**, 184 pinned to a village |
| `backend/load_bharatnet.py` | BharatNet 2022 GP fibre status | **831 villages** given a current status |
| `backend/load_jjm_water.py` | JJM tap-connection coverage (crawler, resumable) | 80 villages in a bounded run, **0 failures** |
| `intelligence/investment.py` | Capability E — demand vs investment | all three mismatch classes computing |

### Three findings that only appeared once real data was loaded

**1. The Census road test is nearly useless in these districts.** 930 of 942
villages already report an all-weather road; only **12 fail**. A field where
99% of rows share a value cannot rank anything. The road deficit signal must
come from PMGSY works and road class instead, not from Census road status.
Same for `school_primary` (**all 942** have one → the RTE primary-school test
finds zero violations here; education deficit has to come from middle/secondary,
where 115 and 464 villages respectively fail).

**2. Census mobile coverage is equally flat** — 9 of 942 lack it. This
retrospectively justifies the BharatNet work: it is not merely fresher, it is
the only connectivity field with usable variation (Kolhapur: 102 DOWN vs 360 UP).

**3. Jal Jeevan Mission has largely fixed the 2011 water deficits.** Census says
265 villages lack treated tap water and 323 have supply that fails in summer.
Current JJM data says median household tap coverage is now **100%**, with only
5 of 80 sampled villages below it (worst: Ambewadi, 39% — 113 of 290
households). **Scoring water need from Census alone would have badly overstated
it.** This is the clearest vindication of replacing proxies with current data,
and the clearest warning against trusting 2011 for anything that a scheme has
since targeted.

### Which deficit tests actually discriminate (measured, n=942)

| Test | Villages failing | Usable? |
|---|---|---|
| No internet/CSC | 823 (87%) | ✅ |
| >30 km from district HQ | 687 (73%) | ✅ |
| No secondary school | 464 (49%) | ✅ |
| No sub-centre and no PHC | 491 (52%) | ✅ |
| Tap fails in summer (2011) | 323 (34%) | ⚠️ superseded by JJM |
| No treated tap water (2011) | 265 (28%) | ⚠️ superseded by JJM |
| Health facility >5 km | 289 (31%) | ✅ |
| ≤10 hrs power in summer | 233 (25%) | ✅ |
| No middle school | 115 (12%) | ✅ |
| **No all-weather road** | **12 (1.3%)** | ❌ too flat |
| **No mobile coverage (2011)** | **9 (1%)** | ❌ too flat |
| **No primary school** | **0** | ❌ no variation |

### Capability E output on live data

| Class | Villages | Example |
|---|---|---|
| Funded but undelivered | **98** | Mangaon (Kolhapur): 3 works undelivered, ₹1,239 lakh, oldest sanctioned **2006** |
| Demanded but unfunded | 762 | Narsoba Vadi (Kolhapur): 35 reports, no sanctioned work found |
| Funded where not demanded | 37 | Nandgaon (Nashik): 0 reports, 10 undelivered works, ₹2,041 lakh |

⚠️ The investment half of all three is real. The demand half is synthetic, so
**"funded where not demanded" is a mechanism demonstration, not a finding** —
"nobody complained here" only means something when the complaints are real.

### Still outstanding after this pass

- **`pmgsy_road_category` is not populated.** PMGSY's road network `.dbf` carries
  `BLOCK_ID`, but PMGSY block codes cannot be joined to our gazetteer's block
  *names*, and `MasterData.xls` (which holds the code lookup) is a legacy OLE2
  `.xls` with no reader installed. Options: install `xlrd`, or load LGD first
  and join on official codes. Left NULL rather than guessed.
- **Roads have no per-village spatial join** — no shapefile reader available
  (only `shapely`; no `pyshp`/`geopandas`/`fiona`).
- **JJM crawl is bounded** — 80 of ~940 matched villages fetched. Remove
  `--limit` for the full run; the cache makes it resumable.
- **The scoring engine has not been rewired yet.** All of this data is loaded
  and queryable, but `intelligence/scoring.py` still reads the old proxies.
  That is the next task, and it is deliberately separate: loading data and
  changing how scores are computed are different risks and should not land in
  the same change.

---

## 6. Build order

1. **Reconcile §5.3** — decide which PMGSY figure means what
2. **LGD loader** — official block hierarchy first; everything joins on it
3. **Census wide loader** — extend `load_demographic_data.py` from 1 column to the ~40 that matter
4. **PMGSY loaders** — habitation population, road network, road-level works API
5. **BharatNet loader** — GP fibre status + coordinates
6. **JJM crawler** — habitation tap connections (last; most fragile)
7. **Rewire `intelligence/scoring.py` + `config.py`** — one term at a time, tests after each
8. **Rewrite `WEIGHTS.md`** — every entry in its "simplifications" table now has a real source

---

## 7. Source list

**PMGSY** — `pmgsy.dord.gov.in/dbweb` · `github.com/datameet/pmgsy-geosadak` ·
`geosadak-pmgsy.nic.in/OpenData` (DNS-blocked here) · `pmgsy.nic.in` ·
NRIDA PMGSY guidelines (eligibility thresholds)
**Census 2011** — `github.com/bnamita/Village_Mapping_v2` (District Census Handbook mirror);
equivalent to data.gov.in resource `2e03171c-d6df-436d-b40c-38e0078b0c66` (needs a personal key)
**LGD** — `lgdirectory.gov.in` · `ramseraph.github.io/opendata/lgd/` ·
`data.gov.in/catalog/local-government-directory-lgd` · `mahavillages.mahabhumi.gov.in`
**BharatNet** — `storage.googleapis.com/bbnl_data/parsed.zip` · `bbnl.nic.in`
**JJM** — `ejalshakti.gov.in/jjm/citizen_corner/villageinformation.aspx` · `jaljeevanmission.gov.in`
**IPHS** — `nhm.gov.in` IPHS 2022 guidelines (Sub-Centre / PHC / CHC population norms)
**RTE** — Right to Education Act 2009, Section 6 + Central RTE Rules (neighbourhood limits)
**NITI Aayog** — Aspirational Districts Programme composite methodology
**Maharashtra BEAMS** — `beams.mahakosh.gov.in`

---

*Every claim in this file was verified by direct download or live API call. Where something was
not tested, it says so.*


---

## §7. Named facility registers (asset-level identity)

Added to answer "which school / which road", which village-level amenity
counts can never answer.

### 7.1 Schools — UDISE+ via DataMeet · LOADED

`https://github.com/datameet/udise_schools` — the Ministry of Education's
UDISE+ school directory as GeoJSON, one file per district. Each feature
carries `schcd` (the national school code), `schname`, `school_cat`,
`management`, `vilname` and a point geometry.

District file codes were **verified by fetching each 27xx file and reading
its `dtname`**, not taken from a published code list:

| District | UDISE file code | Pages | Schools loaded |
|---|---|---|---|
| Kolhapur | 2734 | 4 | 3,717 |
| Nashik | 2720 | 6 | 5,635 |

Loader: `backend/load_udise_schools.py` → `public_facility`.

### 7.2 What was tried first and rejected

| Source | Result |
|---|---|
| OpenStreetMap / Overpass | 2 schools within 6 km of Bidri. Rural POI coverage far too sparse to name an asset |
| `kys.udiseplus.gov.in` | Single-page app, no documented public API; guessed endpoints 404 |
| `data.gov.in` | Carries UDISE *aggregates* (enrolment by category, infrastructure counts), not the school directory |
| `facility.abdm.gov.in` | Redirects; not resolved |

### 7.3 Health facilities — NIC HealthGIS · LOADED

`github.com/yashveeeeeeer/india-geodata`, GitHub release
`healthcare/facilities` → `INDIA_HEALTH_FACILITIES_NIC.geojson` (~50 MB,
147,957 features nationally). Originally **NIC HealthGIS** (healthgis.in)
under the National Health Mission, India Open Government Licence.

Properties: `name`, `type`, `place`, `district`, `state`, `source_id`, point
geometry.

| District | Facilities |
|---|---|
| Kolhapur | 396 |
| Nashik | 716 |

By type: 897 sub-centres, 169 PHCs, 38 CHCs, 7 taluka health offices, 1
district health office.

**The file spells Nashik "Nasik".** Filtering on our own spelling returns
zero rows for the larger pilot district — a failure that looks exactly like
"no data exists". The loader maps the spelling on the way in.

Loader: `backend/load_health_facilities.py` → `public_facility`.

Rejected first: DataMeet has no health repository (it mirrors schools only);
`facility.abdm.gov.in` redirects with no documented bulk export; OSM has the
same sparse rural coverage that ruled it out for schools.

### 7.4 Still missing: water

Water is the one sector with no facility register, and the reason is real
rather than a gap in searching: there is no national register of individual
water assets. A borewell, a standpost or a village pipeline is not
nationally enumerated the way a school has a UDISE code or a health facility
an NIC id.

What does exist is **scheme-level**: Jal Jeevan Mission records coverage per
village, which is already loaded and already feeds the water deficit. So for
water the honest unit of work IS the village scheme, and the panel says so
rather than implying an asset it cannot name.

Coverage after §7.1–7.3, measured over work groups:

| Sector | With named candidates | Unambiguously named |
|---|---|---|
| Education | 106 / 106 | 1 |
| Health | 68 / 126 | 30 |
| Road | 13 / 108 | 11 |
| Water | 0 / 120 | 0 (no register exists) |


---

## §8. LGD administrative hierarchy — LOADED

The item that sat at the top of §6's build order ("LGD loader — official block
hierarchy first; everything joins on it") and was never built. Now is.

### 8.1 Source

`github.com/ramSeraph/opendata`, release `lgd-latest` →
`villages_by_blocks.<DDMonYYYY>.csv.7z` (~10 MB compressed, 90 MB CSV) — a
**daily** mirror of LGD's own bulk exports. Chosen over `lgdirectory.gov.in`,
which serves these only through an interactive session, and over
`data.gov.in`, which catalogues LGD but refreshes monthly.

`villages_by_blocks` is the one file worth having: state → district →
sub-district → development block → village **plus the Census 2011 code at
every level**, on a single row.

The filename carries a date and the release keeps ~156 of them, so the loader
resolves the newest from the GitHub API at run time. A pinned date would rot
within weeks. Loader: `backend/load_lgd_hierarchy.py` → `lgd_village`.

Two practical traps, both hit during this pass: GitHub throttles release
downloads that send no `User-Agent` (it times out rather than failing fast),
and `.7z` needs `py7zr` — no 7-Zip CLI is present on this machine.

### 8.2 What it settles: `gazetteer.block` was never official

`block` came from a nearest-taluka-HQ geometric guess, which the README has
always flagged as "a geometric approximation, not official boundary data."
Measured against LGD, it is not reliably *either* administrative unit:

| Our `block` agrees with | Share of matched villages |
|---|---|
| LGD **sub-district** (taluka) | 549 / 939 (58%) |
| LGD **development block** | 447 / 939 (48%) |

Taluka and development block are **different divisions** in India that often
but not always coincide. `lgd_village` stores them separately; collapsing them
is what produced the ambiguity.

### 8.3 The matching problem, and why the honest number is smaller

First attempt matched LGD → gazetteer by name and reported **703 corrections**.
That number was wrong and was thrown away.

**Village names repeat relentlessly.** "Pimpalgaon" occurs in *nine different
talukas of Nashik district alone*, every one scoring 100 on the name. Matching
in that direction attached whichever copy the scorer returned first, then
published its taluka as the official correction — worse than the geometric
guess it replaced, because the guess at least used coordinates.

LGD carries no lat/lon, and neither `gazetteer` nor `village_amenities` stores
a Census village code, so nothing remains to disambiguate a repeated name.
A link is therefore asserted **only when unambiguous, in both directions** —
one taluka tops the score, and no other gazetteer village claims that same LGD
row. Same rule as work-group asset naming.

| Result | Count |
|---|---|
| LGD villages loaded (complete hierarchy, both districts) | **3,174** |
| Unambiguously linked to a gazetteer village | **656** |
| Left unlinked — name matches several talukas | 283 |
| **Of the linked, our geometric block disagrees with the official taluka** | **197 (30%)** |

Roughly one in three of our block labels was wrong, established only over
villages where the identity is certain.

### 8.4 Not yet applied

`gazetteer.block` is **not** overwritten. Doing so relabels 197 villages and
shifts the block shown on every affected cluster — a visible behavioural
change that deserves to be a deliberate decision, not a side effect of a
loader run. The corrected hierarchy is available in `lgd_village` for whoever
takes that step.

Still open: whether PMGSY's `BLOCK_ID` joins to `lgd_block_code`. If it does,
that is the missing key for `pmgsy_road_category` (still 0/942, §5.5).
