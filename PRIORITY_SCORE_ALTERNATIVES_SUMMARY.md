# Summary index: every alternative found for infra_deficit / urgency, by category

Pulls together everything from `STALE_INFRA_DEFICIT_RESEARCH.md`,
`ASSET_LEVEL_PRIORITIZATION_RESEARCH.md`, and `PHOTO_VERIFICATION_RESEARCH.md`
into one table. Nothing new here — this is an index, not new research. Read
the linked doc's section for the full reasoning and verification detail
behind any row.

## The two kinds of alternative, and when each applies

1. **A better/fresher/independent data source** — replaces or supplements
   the government record itself.
2. **A verification mechanism** — doesn't add a data source, but decides
   how much to trust the citizen report when no better data source exists
   or can ever exist (recency+trust-blend, photo/CV verification).

Every category gets mechanism #2. Only some get #1, and never as a full
replacement — as either an added corroboration signal or a fresher input
to the same blend.

## Road

| Alternative | What it does | Verified? | Doc |
|---|---|---|---|
| Recency + trust-blend | Discounts the old Census record when a real, corroborated burst contradicts it | Designed, not built | `STALE_INFRA_DEFICIT_RESEARCH.md` §2 |
| Satellite (Planet Labs) | Confirms a *catastrophic* failure (bridge gone, road washed out) via before/after imagery | Blocked on external application | `STALE_INFRA_DEFICIT_RESEARCH.md` §2A |
| PMGSY GeoSadak road geometry | Real per-road identity + surface/category attributes, replacing village-name-fuzzy-matching | Repo/license verified real; per-district attribute richness not yet checked | `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §3 |
| MOSDAC GSMaP rainfall | Corroborates a washout claim with real regional rainfall | Verified real, free, no login | `STALE_INFRA_DEFICIT_RESEARCH.md` §4.4 |
| Mission Antyodaya | Potentially fresher (annual) village-level road data than Census 2011 | Portal verified real; field quality for Kolhapur/Nashik not yet checked | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.1 |
| Photo + CV model (potholes) | Classifies whether a genuine photo actually shows road damage | RDD2022/YOLOv8 real, ~77% F1 ceiling — a signal, not a verdict | `PHOTO_VERIFICATION_RESEARCH.md` §1.1 |
| Photo authenticity (Layer 2) | Catches an edited, reused, stolen, or screen-replayed photo before the CV model even runs | Techniques real (moiré/ELA/reverse search); nothing built | `PHOTO_VERIFICATION_RESEARCH.md` §2, §2A |

## Water

| Alternative | What it does | Verified? | Doc |
|---|---|---|---|
| Recency + trust-blend | Same mechanism, generalized beyond roads | Designed, not built | `STALE_INFRA_DEFICIT_RESEARCH.md` §5 pt.3 |
| NWDP groundwater/canal telemetry | Regional water-stress corroboration (not per-pump proof) | Verified real, free, no login | `SESSION_LOG_2026-09-12.md`; loader not yet built |
| MOSDAC GSMaP rainfall | Corroborates drought-driven "pani nahi aata" reports | Verified real | `STALE_INFRA_DEFICIT_RESEARCH.md` §4.4 |
| JJM Village Profile / WQMIS | Possibly per-scheme source coordinates, richer than current village-aggregate tap % | **Not confirmed better** than what's already scraped — same login-gated portal family | `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §4.2 |
| Mission Antyodaya | Fresher village-level water data | Portal real; fields unchecked | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.1 |
| Photo (visible only) | A dry/broken visible tap or pipe leak can be photographed | No dedicated water-defect CV model researched yet — open item | — |

## Health

| Alternative | What it does | Verified? | Doc |
|---|---|---|---|
| Recency + trust-blend | **The entire answer for staffing/doctor-absence claims** — no data source or photo can ever verify a person's absence | Designed, not built | `STALE_INFRA_DEFICIT_RESEARCH.md` §5 pt.3 |
| NFHS-5 (2019-21) | Fresher health/nutrition deprivation data than Census 2011 | Verified real | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.3 |
| Mission Antyodaya | Fresher village-level health facility data | Portal real; fields unchecked | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.1 |
| Photo (structural only) | A visibly damaged health-facility building can be photographed like any building | No dedicated model beyond the general crack detector below | `PHOTO_VERIFICATION_RESEARCH.md` §1.2 |
| Satellite | **Does not apply** — a single building is far too small a target even at Planet's resolution | Ruled out | `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §0 (school-roof analysis, same logic) |
| NITI Aspirational Districts dashboard | Real-time, but **doesn't cover Kolhapur/Nashik** | Ruled out for this scope | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.2 |

## Education

| Alternative | What it does | Verified? | Doc |
|---|---|---|---|
| Recency + trust-blend | **The entire answer for "no teacher"** — same presence-fact argument as health | Designed, not built | `STALE_INFRA_DEFICIT_RESEARCH.md` §5 pt.3 |
| Mission Antyodaya | Fresher village-level school data | Portal real; fields unchecked | `STALE_INFRA_DEFICIT_RESEARCH.md` §7.1 |
| SDNET2018-trained crack detector | Verifies "building about to collapse" *if the photo shows a visible crack* — not a general safety assessment | Real, 95%+ accuracy on crack-presence, but answers a narrower question than "is it safe" | `PHOTO_VERIFICATION_RESEARCH.md` §1.2 |
| UDISE+ staffing data | Whether UDISE separately publishes real per-school teacher counts | **Still unresolved** — flagged since the 09-12 session, never checked | `SESSION_LOG_2026-09-12.md` |
| Satellite | Does not apply — same too-small-a-target reasoning as health | Ruled out | `ASSET_LEVEL_PRIORITIZATION_RESEARCH.md` §0 |

## What this means, read straight down the table

- **Every category gets the same base fix first**: recency + trust-blend
  (`STALE_INFRA_DEFICIT_RESEARCH.md` §2), because it's free, needs no new
  data source, and is the *only* thing that works at all for staffing
  claims in health and education.
- **Roads have the richest set of add-ons** — satellite for the
  catastrophic case, real geometry for identity, rainfall for
  corroboration, an actual CV model for potholes — because a road is a
  physical, photographable, satellite-visible thing. Nothing else is.
- **Water is second-richest** — two independently-verified regional
  telemetry sources (NWDP, MOSDAC), though both only prove regional
  stress, never a specific broken pump.
- **Health and education are the hardest, honestly** — most of what
  citizens report there (staffing, attendance) has no possible data-source
  or photo fix, ever, in any system. The trust-blend mechanism carries
  nearly the entire weight for these two categories. The one open thread
  worth chasing is whether UDISE+ actually publishes real staffing
  numbers somewhere this project hasn't checked yet — everything else for
  these two categories is either ruled out (satellite, Aspirational
  Districts) or a freshness upgrade to the same government-record input
  the blend already uses (NFHS-5, Mission Antyodaya).
