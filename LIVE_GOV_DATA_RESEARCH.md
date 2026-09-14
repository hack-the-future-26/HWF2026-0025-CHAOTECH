# Research: live or fresher government data for infra_deficit and urgency (2026-09-14)

Second search round, asked for because the existing data (Census 2011,
2022 snapshots) is too old to show current condition. Every source below
was **checked live from this machine**, not just found in search results.
Mission Antyodaya showed why that matters: it appeared in search results
but its domain turned out to be dead.

Status words used below:
- **Verified**: returned real data from here.
- **Live, access unconfirmed**: the site loads, but automated or bulk access
  to the useful data was not proven.
- **Unreachable**: couldn't connect from here.

## 1. Verified, buildable now

### NDMA SACHET: live disaster alerts (for emergency urgency)
- **What:** the national Common Alerting Protocol system. IMD, the Central
  Water Commission and state disaster authorities publish warnings through
  it (heavy rain, flood, river level).
- **Checked:** `https://sachet.ndma.gov.in/cap_public_website/rss/rss_maharashtra.xml`
  returned HTTP 200 with **10 current Maharashtra alerts**, in Marathi,
  issued by Mantralaya, naming districts ("in the next 3 hours, in
  Dharashiv, Latur, Nanded, Solapur…"). The all-India feed had 99 items.
  Each item links to a CAP XML message.
- **No login needed.** The feeds have no CORS headers, which doesn't matter
  because the backend fetches them, not the browser.
- **Use:** if a burst of "bridge collapsed" reports arrives while an active
  heavy-rain or flood alert covers that district, that is strong,
  independent corroboration of an emergency.
- **Limits:**
  - Alerts are district-wide, not village-level.
  - Alerts expire. The sample CAP message had already expired ("No Active
    Alert"), so a loader must poll regularly and keep its own history.
  - It confirms that a hazard was happening, not that a specific bridge
    broke.
- Sources: [SACHET (NDMA)](https://sachet.ndma.gov.in/),
  [CAP in the Indian context (Thejesh GN)](https://thejeshgn.com/2025/05/28/common-alerting-protocol-cap-in-the-indian-context/),
  [NDMA IT & Communication projects](https://ndma.gov.in/Capacity_Building/Ops_Comm/IT_Comm_Project)

## 2. Live, and potentially the biggest upgrades, but access is unconfirmed

### UDISE+ Know Your School: current per-school condition and teachers
- **What:** a report card for every registered school, updated every year.
  It includes **classroom condition** (good / needs minor repair / needs
  major repair), **number of teachers**, toilets, drinking water and
  electricity.
- **Why it matters:** this is data about a **specific school**, and
  recent. That is exactly what the village → asset view needs for
  education. It would replace the 2011 fact "this village has a middle
  school" with "this school has 3 classrooms needing major repair and 2
  teachers for 180 pupils." It also partly answers the staffing question
  left open since 2026-09-12 (teacher *count*, not daily attendance).
- **Checked:** `https://kys.udiseplus.gov.in/` returns HTTP 200 and is a
  real app. The data API behind it was not found in its JavaScript. The
  bulk route (the UDISE+ Data Sharing Portal) needs a login and a data
  request.
- **Next step:** open one school's report card in a browser with developer
  tools, find the request that returns the data, and confirm it works
  without a login. If it doesn't, file a bulk data request.
- Sources: [Know Your School](https://kys.udiseplus.gov.in/),
  [UDISE+ report card fields (Schoolites)](https://schoolites.com/udise/report-card),
  [UDISE+ infrastructure module guide](https://udise.net/udise-plus-infrastructure-module-guide-2026-27/)

### JJM WQMIS: water-quality lab tests (current water condition)
- **What:** drinking-water test results from about 2,870 labs, including
  contamination and test dates. JJM itself states "all water quality
  testing data is available in public domain."
- **Checked:** `https://ejalshakti.gov.in/WQMIS/Main/report` and the WQMIS
  dashboard both return HTTP 200.
- **Not yet proven:** that results are available per village or per source
  for Kolhapur and Nashik without going through the ASP.NET form steps.
- **Use:** unsafe water recently found in a village is a real, current
  water deficit. Today's JJM data only says whether a household has a tap.
- Sources: [WQMIS dashboard](https://ejalshakti.gov.in/jjmreport/Quality/WQMIS_Dashboard.aspx),
  [PIB on water-testing labs](https://www.pib.gov.in/PressReleasePage.aspx?PRID=2198887&reg=3&lang=1)

### eMARG: PMGSY's own road maintenance inspections
- **What:** PMGSY roads under their 5-year maintenance contract are
  inspected regularly. Officials submit condition reports with geotagged
  photos, and a Pavement Condition Index (PCI) method exists.
- **Checked:** `https://emarg.gov.in/` returns HTTP 200. **No public report
  or dashboard link was found** on the home page.
- **Use, if accessible:** actual current condition of specific PMGSY
  roads, which is exactly the stale-record problem for roads.
- Sources: [eMARG](https://emarg.gov.in/),
  [eMARG at NRIDA](https://pmgsy.nic.in/emarg-electronic-maintenance-rural-roads-under-pmgsy),
  [PCI guidelines (NRIDA)](https://pmgsy.nic.in/determining-pavement-condition-index-pci-and-guidelines-upgradation-and-routine-maintenance-rural)

### CWC flood forecasting: river levels (for emergency urgency)
- **What:** real-time river levels and flood situation at forecast stations.
- **Checked:** `https://ffs.india-water.gov.in/` returns HTTP 200.
- **Not yet proven:** a data endpoint, and which stations cover Kolhapur
  (Panchganga / Krishna basin) and Nashik (Godavari).
- Sources: [CWC flood forecast](https://ffs.india-water.gov.in/),
  [CWC flood forecasting network (India-WRIS wiki)](https://indiawris.gov.in/wiki/doku.php?id=cwc_national_flood_forecasting_network)

### eGramSwaraj: panchayat works and spending
- **What:** each Gram Panchayat's planned works, progress, spending and
  geotagged photos.
- **Checked:** `https://egramswaraj.gov.in/` returns HTTP 200.
- **Use:** "money approved here but not delivered" at panchayat level,
  alongside the PMGSY check that already exists. This is about spending,
  not physical condition.
- Source: [eGramSwaraj](https://egramswaraj.gov.in/)

## 3. Unreachable or not public

- **OMMAS quality-monitor grades** (`omms.nic.in`): connection failed from
  here. Search results say inspection grades are public.
- **India-WRIS**: connection failed again, as it did on 2026-09-12.
- **Meri Sadak** (PMGSY's own citizen complaint app): no public complaint
  data found.
- **MOSDAC registration:** a sign-up form with an ordinary email; approval
  is sent by email, so it isn't instant. The registration page itself
  returned 403 from here, so the exact fields are unknown.
  Sources: [How to be a registered user of MOSDAC](https://www.mosdac.gov.in/how-be-registered-user-mosdac),
  [MOSDAC FAQ](https://mosdac.gov.in/faq-page)

## 4. Models for emergency detection (bridge and building breakage)

**Bridges:** there is research, but no ready model for "is this bridge
broken."
- Published bridge models detect cracks, corrosion, spalling and exposed
  rebar in close-up inspection or drone photos. The 2026 YOLOv8 bridge
  model reports mAP50 of only 0.445.
- No open, pretrained "collapsed bridge" classifier for citizen photos was
  found.
- Sources: [Structure-aware bridge inspection (PMC)](https://pmc.ncbi.nlm.nih.gov/articles/PMC13364411/),
  [Bridge damage detection, single-stage detector (arXiv)](https://arxiv.org/pdf/1812.10590)

**Buildings:** much closer.
- A published ResNet50 classifier on **ground-level photos** sorts
  buildings into not damaged / damaged / collapsed with 93.5% validation
  accuracy. Whether its code and weights are public was not confirmed.
- GitHub has usable starting points:
  [Earthquake-Building-Damage-Detection](https://github.com/najmulmowla1/Earthquake-Building-Damage-Detection)
  (drone images: cracked / partially damaged / collapsed) and
  [Recognize-damaged-buildings](https://github.com/JinyuanShao/Recognize-damaged-buildings)
  (satellite).
- Sources: [Terrestrial-image building damage classification (PubMed)](https://pubmed.ncbi.nlm.nih.gov/42376797/)

**Honest conclusion:**
- A building-collapse classifier is realistic, via one of these models or
  transfer learning.
- A bridge-collapse classifier would need training on collected examples.
- Either way the model is **one signal**. It gets combined with
  catastrophic wording, several independent reports in a short time, an
  active SACHET or flood alert for that district, and satellite later.

## 4A. Hands-on scraping check (2026-09-14, same day)

The owner asked whether each of the four "live, access unconfirmed"
sources can be scraped. Each was tested by pulling real Kolhapur/Nashik
data, not by loading its home page.

| Source | Scrapable? | What was actually fetched |
|---|---|---|
| **UDISE+ Know Your School** | ✅ **Yes, confirmed.** No login and no CAPTCHA on the per-school endpoints. | `GET https://kys.udiseplus.gov.in/web-app/api/school/report-card?udiseSchCode=27341212203` returned **2024-25** data for *Vidya Mandir, Mhalunge Khalsa (Kolhapur)*: `tchReg: 5` regular teachers, 0 contract, 0 part-time. `school/facility?udiseSchCode=…` returned classrooms by condition (`clsrmsGd: 4`, `clsrmsMin: 1`, `clsrmsMaj: 0`), working toilets, drinking water, electricity and boundary wall. `reportCardPdfByUdiseCode/…` returns the PDF. Our `public_facility` table already holds the UDISE code for all 9,352 schools. (The school *search* is CAPTCHA-protected, but we don't need it.) |
| **CWC flood forecasting** | ✅ **Yes, confirmed.** An unauthenticated JSON API. | Base `https://ffs.india-water.gov.in/iam/api/`. Requests need a `class-name` header (`LayerStationDto`, `NewEntryDataDto`) and a JSON `specification` filter (operators `eq`, `like`, …). `layer-station/specification/count` returned **168,019** stations. `new-entry-data` for Nashik telemetry station `MHSWIWRM026` returned readings stamped **2026-09-14 17:00**, i.e. live. **Caveats:** (1) the station named "KOLHAPUR" is dead (last reading 2017) and its coordinates are in Telangana, so stations must be chosen by recent data and correct coordinates; (2) the reading codes (`GBD`, `GBT`, `GEP`, …) still have to be mapped to "water level" etc.; (3) the live Kolhapur stations aren't identified yet. |
| **JJM WQMIS water quality** | 🟡 **Likely, not proven.** | Public report forms with no login: village-wise field-test-kit results (`/WQMIS/Report/rpt_ftk_testing_village`), contaminant-wise results, lab reports, remedial actions. Maharashtra is `StateId=18`. The forms use cascading dropdowns, an anti-forgery token and an "Export to Excel" POST, the same ASP.NET pattern `load_jjm_water.py` already handles. The district dropdown endpoint wasn't found, so **no Kolhapur rows were pulled yet.** |
| **eMARG road inspections** | ❌ **Not legitimately scrapable.** | The public "Know Your Road" lookup is behind a CAPTCHA (`getCaptchaImageForKnowYourRoad.do`). Bypassing a CAPTCHA isn't acceptable, so this needs a formal data request to NRIDA instead. |

**Scraping etiquette for everything above:**
- Stay under about 1 request per second.
- Cache results and refresh on a schedule (UDISE yearly, CWC hourly).
- Send an honest User-Agent.
- Never bypass a CAPTCHA or a login.

## 5. Verdict

- **Buildable now:** SACHET alerts, for emergency corroboration.
- **Highest value if access works:** UDISE+ per-school condition and
  teacher counts, then JJM water-quality tests, then eMARG road
  inspections. Each needs one hands-on check of how its data can be
  fetched before anyone builds on it.
- **If none of the three can be accessed:** go ahead with what we have:
  1. the stale-record discount (trust-blend);
  2. NWDP groundwater;
  3. SACHET;
  4. NFHS-5 health;
  5. the photo checks.
