/* ===========================================================================
   AwaazIQ dashboard — zoomable demand map
   India → Maharashtra → district → cluster, with citizen demand rendered at
   every level.

   Zoom technique: the projection is fitted to India ONCE and never changes.
   Navigating between levels animates a transform on a single <g>, computed
   from the target feature's projected bounding box. Re-projecting every frame
   would be far more expensive and visibly janky at 726 districts; this is the
   standard "zoom to bounding box" approach and it stays smooth because the
   browser is only interpolating one matrix.

   Because everything scales together, stroke widths and bubble radii are
   divided by the live zoom factor so they keep a constant on-screen size --
   without that, districts get hairline borders and cluster bubbles balloon.
   =========================================================================== */

(() => {
  "use strict";

  // The API port is configurable so the dashboard works whether the backend
  // was started on the README's 8000 or anything else: ?api=http://host:port
  const params = new URLSearchParams(location.search);
  // FastAPI serves this page itself at /app (the ngrok phone demo, and any
  // "uvicorn then open localhost:8000/app" run), and in that case the API is
  // this very origin. Falling through to a fixed 127.0.0.1 port there asks a
  // phone to call its own loopback, which is why the dashboard looked dead on
  // the tunnel while report.html -- which already had this rule -- worked.
  const servedByApi = location.pathname.indexOf("/app/") === 0;
  const defaultApi = (servedByApi || location.protocol === "https:")
    ? location.origin
    : "http://127.0.0.1:8001";
  const API = (params.get("api") || window.AWAAZIQ_API_BASE || defaultApi).replace(/\/$/, "");

  const PILOT_STATE = "Maharashtra";
  const TOPO_URL = "data/india.topo.json";

  const svg = d3.select("#map");
  const stage = document.getElementById("stage");
  const tooltip = d3.select("#tooltip");

  let width = 0;
  let height = 0;

  // Layers, painted back to front.
  const root = svg.append("g").attr("id", "viewport");
  const gStates = root.append("g");
  const gDistricts = root.append("g");
  const gOutline = root.append("g");
  const gReports = root.append("g");
  const gClusters = root.append("g");
  const gVillages = root.append("g");
  const gRoadLines = root.append("g");
  const gAssets = root.append("g");
  const gLabels = root.append("g");

  const projection = d3.geoMercator();
  const path = d3.geoPath(projection);

  let india, statesFC, districtsFC;
  let clusters = [];
  let reports = null;           // lazily fetched -- 1,000+ points
  let mode = "villages";        // "villages" | "clusters"
  let districtVillagesCache = {};
  let currentVillageData = null;
  let currentAssetData = null;
  let vRadiusScale = null;

  let radiusScale, colourScale;

  // Current view: {k, x, y} applied to #viewport.
  let view = { k: 1, x: 0, y: 0 };

  const nav = { level: "india", state: null, district: null, cluster: null, village: null, asset: null };

  /* ------------------------------------------------------------- helpers -- */

  const fmt = d3.format(",");

  function scoreColour(score) {
    if (score == null) return "#d8d3c4";
    return colourScale(score);
  }

  /** Bounding box of a feature in projected pixels, padded. */
  function fitTransform(feature, padding = 0.90) {
    if (!feature) {
      return { k: 1, x: 0, y: 0 };
    }
    const bounds = path.bounds(feature);
    if (!bounds || !Array.isArray(bounds) || bounds.length < 2) {
      return { k: 1, x: 0, y: 0 };
    }
    const [[x0, y0], [x1, y1]] = bounds;
    const dx = x1 - x0;
    const dy = y1 - y0;
    if (!Number.isFinite(dx) || !Number.isFinite(dy) || dx <= 0 || dy <= 0) {
      return { k: 1, x: 0, y: 0 };
    }
    const k = Math.min(width / dx, height / dy) * padding;
    return {
      k: Number.isFinite(k) && k > 0 ? k : 1,
      x: width / 2 - k * (x0 + x1) / 2,
      y: height / 2 - k * (y0 + y1) / 2,
    };
  }

  // On-screen asset pin size in pixels (zoom-compensated like every other mark).
  const ASSET_PIN_R = 14;
  const ASSET_ICON_PX = 15;
  const ASSET_LABEL_PX = 11.5;

  /**
   * Transform that fits a set of [lon, lat] points on screen, clamped to
   * [kMin, kMax]. Used for the village level: a village's assets can sit
   * well away from its centroid (a PHC in the next village), so a fixed zoom
   * either cut them off-screen or zoomed out so far the pins were specks.
   */
  function fitPoints(lonlats, kMin, kMax, padding = 0.55) {
    const pts = lonlats
      .filter((p) => Number.isFinite(p[0]) && Number.isFinite(p[1]))
      .map((p) => projection(p));
    if (!pts.length) return null;
    const xs = pts.map((p) => p[0]);
    const ys = pts.map((p) => p[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs);
    const y0 = Math.min(...ys), y1 = Math.max(...ys);
    const dx = Math.max(x1 - x0, 1e-6);
    const dy = Math.max(y1 - y0, 1e-6);
    let k = Math.min(width / dx, height / dy) * padding;
    k = Math.max(kMin, Math.min(kMax, Number.isFinite(k) ? k : kMax));
    return { k, x: width / 2 - k * (x0 + x1) / 2, y: height / 2 - k * (y0 + y1) / 2 };
  }

  /** Transform for a point (used when diving into a single cluster).
   *  `rightInset` is the width of any panel covering the right edge, so the
   *  point is centred in what the user can actually see. Unused since the
   *  detail moved from a right sidebar to a dock beneath the map, but kept
   *  because a right-hand overlay is a plausible thing to add back. */
  function pointTransform(lon, lat, k, rightInset = 0) {
    const [px, py] = projection([lon, lat]);
    const safeK = Number.isFinite(k) && k > 0 ? k : 1;
    return {
      k: safeK,
      x: (width - rightInset) / 2 - safeK * px,
      y: height / 2 - safeK * py,
    };
  }

  /**
   * Animate #viewport to a target transform.
   *
   * `view` must already be set to `target` by the caller BEFORE it draws, so
   * that radii and stroke widths are computed against the zoom level we are
   * arriving at rather than the one we are leaving. Setting it here instead
   * made every bubble size itself from the previous level's scale, which is
   * what turned district view into a field of giant white blobs.
   */
  function animateTo(target, duration = 900) {
    if (!target || !Number.isFinite(target.k) || !Number.isFinite(target.x) || !Number.isFinite(target.y)) {
      console.warn("Invalid animateTo target:", target);
      return;
    }
    // Stop any in-flight size compensation from a previous zoom. It runs on
    // its own transition (below), so interrupting `root` alone left it alive:
    // opening the village dock triggers an instant refit, and the old 900 ms
    // tween then kept resizing every mark back toward the PREVIOUS zoom --
    // asset pins ended up ~2x too big, overlapping, and hard to click.
    d3.select(document.documentElement).interrupt("zoomcomp");
    const currentTransform = root.attr("transform") || "";
    if (duration === 0 || currentTransform.includes("NaN")) {
      root.interrupt().attr("transform", `translate(${target.x},${target.y}) scale(${target.k})`);
      applyZoomCompensation(target.k);
      return;
    }
    root
      .transition()
      .duration(duration)
      .ease(d3.easeCubicInOut)
      .attr("transform", `translate(${target.x},${target.y}) scale(${target.k})`)
      .on("end", () => applyZoomCompensation(target.k));
    // Compensate continuously so lines don't visibly thicken mid-flight.
    d3.transition("zoomcomp")
      .duration(duration)
      .ease(d3.easeCubicInOut)
      .tween("compensate", () => {
        const kFrom = currentK();
        const i = d3.interpolateNumber(kFrom, target.k);
        return (t) => applyZoomCompensation(i(t));
      });
  }

  function currentK() {
    const t = root.attr("transform") || "";
    const m = t.match(/scale\(([\d.-]+)\)/);
    const val = m ? parseFloat(m[1]) : NaN;
    return (Number.isFinite(val) && val > 0) ? val : ((Number.isFinite(view.k) && view.k > 0) ? view.k : 1);
  }

  /** Keep strokes and bubbles a constant on-screen size as we zoom. */
  function applyZoomCompensation(k) {
    const raw = k || view.k;
    const z = (Number.isFinite(raw) && raw > 0) ? raw : 1;
    gStates.selectAll("path").attr("stroke-width", 0.7 / z);
    gDistricts.selectAll("path").attr("stroke-width", 0.6 / z);
    gOutline.selectAll("path").attr("stroke-width", 1.4 / z);
    gClusters.selectAll("circle")
      .attr("r", (d) => (radiusScale ? radiusScale(d.population_affected || 0) : 4) / z)
      .attr("stroke-width", 1.3 / z);
    gVillages.selectAll("circle")
      .attr("r", (d) => (vRadiusScale ? vRadiusScale(d.report_count || 1) : 5) / z)
      .attr("stroke-width", 1.2 / z);
    gAssets.selectAll(".asset-circle")
      .attr("r", ASSET_PIN_R / z);
    gAssets.selectAll(".asset-icon")
      .attr("font-size", `${ASSET_ICON_PX / z}px`);
    gAssets.selectAll(".asset-label")
      .attr("font-size", `${ASSET_LABEL_PX / z}px`)
      .attr("stroke-width", 3 / z)
      .attr("y", (ASSET_PIN_R + 10) / z);
    gReports.selectAll("circle").attr("r", 1.9 / z);
    gLabels.selectAll("text").attr("font-size", `${11 / z}px`)
      .attr("stroke-width", 3 / z);
  }

  /* ------------------------------------------------------------- tooltip -- */

  function showTip(event, html) {
    tooltip.html(html).classed("tooltip--on", true);
    moveTip(event);
  }
  function moveTip(event) {
    const pad = 16;
    const box = tooltip.node().getBoundingClientRect();
    const rect = stage.getBoundingClientRect();
    let x = event.clientX - rect.left + pad;
    let y = event.clientY - rect.top + pad;
    if (x + box.width > rect.width) x = event.clientX - rect.left - box.width - pad;
    if (y + box.height > rect.height) y = event.clientY - rect.top - box.height - pad;
    tooltip.style("left", `${x}px`).style("top", `${y}px`);
  }
  function hideTip() { tooltip.classed("tooltip--on", false); }

  /* ------------------------------------------------------------ chrome ---- */

  function renderBreadcrumb() {
    const crumbs = [{ label: "India", level: "india" }];
    if (nav.state) crumbs.push({ label: nav.state, level: "state", state: nav.state });
    if (nav.district) crumbs.push({ label: nav.district, level: "district", state: nav.state, district: nav.district });
    if (nav.level === "cluster" && nav.cluster) {
      crumbs.push({
        label: `Cluster ${nav.cluster.id} · ${nav.cluster.block || ""}`.trim(),
        level: "cluster",
        state: nav.state,
        district: nav.district,
        cluster: nav.cluster,
      });
    } else if (nav.village) {
      crumbs.push({
        label: nav.village.name || `Village #${nav.village.gazetteer_id}`,
        level: "village",
        state: nav.state,
        district: nav.district,
        village: nav.village,
      });
      if (nav.level === "asset" && nav.asset) {
        crumbs.push({
          label: nav.asset.name || `Asset #${nav.asset.id}`,
          level: "asset",
          state: nav.state,
          district: nav.district,
          village: nav.village,
          asset: nav.asset,
        });
      }
    }

    const el = d3.select("#breadcrumb");
    el.selectAll("*").remove();
    crumbs.forEach((c, i) => {
      if (i) el.append("span").attr("class", "crumb__sep").text("›");
      el.append("button")
        .attr("class", `crumb${i === crumbs.length - 1 ? " crumb--current" : ""}`)
        .text(c.label)
        .on("click", () => {
          if (i < crumbs.length - 1) {
            navigate(c.level, { state: c.state, district: c.district, cluster: c.cluster, village: c.village, asset: c.asset });
          }
        });
    });
    if (crumbs.length > 1) {
      const prev = crumbs[crumbs.length - 2];
      el.append("button")
        .attr("class", "back-btn")
        .text("← BACK")
        .on("click", () => navigate(prev.level, { state: prev.state, district: prev.district, cluster: prev.cluster, village: prev.village, asset: prev.asset }));
    }
  }

  function renderStatus() {
    const el = d3.select("#status");
    el.selectAll("*").remove();

    let stats = [];
    if (nav.level === "asset" && currentAssetData) {
      stats = [
        { v: currentAssetData.asset_type || "asset", l: "type" },
        { v: fmt(currentAssetData.report_count || 0), l: "reports" },
        { v: (currentAssetData.priority_score ?? 0).toFixed(1), l: "priority score" },
      ];
    } else if (nav.level === "village" && currentVillageData) {
      stats = [
        { v: fmt((currentVillageData.assets || []).length), l: "assets" },
        { v: fmt(currentVillageData.report_count || 0), l: "reports" },
        { v: (currentVillageData.priority_score ?? 0).toFixed(1), l: "top score" },
      ];
    } else if (nav.district && mode === "villages") {
      const vList = districtVillagesCache[nav.district] || [];
      const totalRep = d3.sum(vList, (v) => v.report_count || 0);
      const top = vList.length ? d3.max(vList, (v) => v.priority_score || 0) : null;
      stats = [
        { v: fmt(vList.length), l: "villages" },
        { v: fmt(totalRep), l: "reports filed" },
        { v: top != null ? top.toFixed(1) : "—", l: "top score" },
      ];
    } else {
      const scope = visibleClusters();
      const totalPop = d3.sum(scope, (c) => c.population_affected || 0);
      const top = scope.length ? d3.max(scope, (c) => c.priority_score || 0) : null;
      stats = [
        { v: fmt(scope.length), l: "clusters" },
        { v: fmt(totalPop), l: "people affected" },
        { v: top != null ? top.toFixed(1) : "—", l: "top score" },
      ];
    }

    stats.forEach((s, i) => {
      if (i) el.append("div").attr("class", "stat__divider");
      const d = el.append("div").attr("class", "stat");
      d.append("div").attr("class", "stat__value").text(s.v);
      d.append("div").attr("class", "stat__label").text(s.l);
    });
  }

  /* ---------------------------------------------------------- detail panel -- */

  // Human labels and provenance for the nine scoring terms. The source line
  // matters as much as the number: it is what turns "trust the score" into
  // "audit the score".
  const TERMS = [
    ["demand",        "Citizen demand",         "distinct reporters, capped at 25"],
    ["population",    "Population affected",    "Census + PMGSY habitations, log-scaled"],
    ["infra_deficit", "Infrastructure deficit", "measured against the scheme's own norm"],
    ["vulnerability", "Deprivation",            "isolation, power hours, drainage, access"],
    ["equity",        "Equity — silent need",   "BharatNet 2022 fibre status"],
    ["strategic",     "Scheme eligibility",     "PMGSY / IPHS / RTE / JJM rules"],
    ["urgency",       "Urgency",                "bridge or building damage emergency"],
    ["feasibility",   "Feasibility",            "Census distance to headquarters"],
    ["cost_penalty",  "Cost penalty",           "scaled by catchment population"],
  ];

  // Attaches a "How it works" button to an .eyebrow heading. d3's .call()
  // hands the selection through untouched, so this chains onto the existing
  // .text(...) without restructuring any of the render code below.
  const xp = (key) => (sel) => {
    if (window.Explain) window.Explain.attach(sel, key);
  };

  function rankOf(cluster) {
    const ordered = clusters
      .slice()
      .sort((a, b) => (b.priority_score ?? -1) - (a.priority_score ?? -1));
    return ordered.findIndex((c) => c.id === cluster.id) + 1;
  }

  function closePanel() {
    const dock = d3.select("#dock");
    if (!dock.classed("dock--open")) return;
    dock.classed("dock--open", false);
    // Give the map its space back. Waits out the slide-down so the height
    // read in sizeToStage is the settled one, not a mid-transition value.
    setTimeout(refit, 440);
  }

  /* ------------------------------------------------------------ evidence -- */

  // Which evidence block belongs to which score term. Two of them are named
  // differently on each side: the equity term is computed from reporting
  // capacity, and the strategic term from scheme eligibility.
  // Every asset_source a work group can carry, matched to what recompute.py's
  // name_work_group actually stamps (realdata.py + load_udise_schools.py +
  // load_health_facilities.py). Kept as data, not as branches in the render
  // call, so adding a register only ever means adding one line here.
  const REGISTER_NAMES = {
    udise: "UDISE school register",
    nic_healthgis: "NIC health facility register",
    pmgsy: "PMGSY works register",
    pmgsy_geosadak: "PMGSY GeoSadak road register",
  };

  const EVIDENCE_KEY = {
    demand: "demand",
    population: "population",
    infra_deficit: "infra_deficit",
    vulnerability: "vulnerability",
    equity: "reporting_capacity",
    strategic: "scheme_eligibility",
    feasibility: "feasibility",
  };

  // Readable labels and units for the fields that carry the most weight in an
  // argument. Anything not listed falls back to the generic prettifier, so
  // adding a field to the engine never leaves a blank row here.
  const EV_LABELS = {
    distinct_reporters: ["Distinct reporters", ""],
    total_reports: ["Reports filed", ""],
    saturates_at: ["Counting stops at", " reporters"],
    settlements: ["Settlements", ""],
    people_affected: ["People affected", ""],
    catchment_radius_km: ["Catchment radius", " km"],
    villages_in_catchment: ["Villages counted", ""],
    iphs_subcentre_entitled: ["Sub-centres this population is entitled to", ""],
    iphs_phc_entitled: ["PHCs entitled to", ""],
    share_with_facility_over_5km: ["Villages >5 km from a facility", "%"],
    share_without_all_weather_road: ["Villages with no all-weather road", "%"],
    share_without_black_topped_road: ["Villages with no black-topped road", "%"],
    undelivered_sanctioned_works: ["Sanctioned works not delivered", ""],
    undelivered_sanctioned_cost_lakh: ["Money already sanctioned, unspent", " lakh"],
    oldest_undelivered_sanction_year: ["Oldest undelivered sanction", ""],
    villages_with_records: ["Villages with records", ""],
    jjm_tap_coverage_pct: ["Households with a tap (JJM)", "%"],
    jjm_villages_measured: ["Villages measured", ""],
    share_without_treated_tap: ["Villages without a treated tap", "%"],
    mean_km_to_district_hq: ["Average distance to district HQ", " km"],
    mean_km_to_subdistrict_hq: ["Distance to sub-district HQ", " km"],
    mean_power_hours_summer: ["Power, summer", " hrs/day"],
    share_without_drainage: ["Villages with no drainage", "%"],
    share_without_internet_csc: ["Villages with no internet centre", "%"],
    bharatnet_villages_checked: ["Gram panchayats checked", ""],
    bharatnet_villages_impaired: ["…with fibre down or absent", ""],
    share_digitally_impaired: ["Cannot report online", "%"],
    entitled: ["Entitled to", ""],
    existing: ["Actually exists", ""],
    villages_short: ["Villages falling short", ""],
    villages_below_full_coverage: ["Villages below full coverage", ""],
    lowest_coverage_pct: ["Worst village coverage", "%"],
    median_cost_lakh_per_km: ["Median cost of comparable work", " lakh/km"],
    works_sampled: ["Works averaged", ""],
    dimensions_used: ["Deprivation measures used", ""],
    share_without_middle_school: ["Villages with no middle school", "%"],
    share_without_secondary_school: ["Villages with no secondary school", "%"],
    rte_upper_primary_limit_km: ["RTE limit for upper primary", " km"],
    share_failing_in_summer: ["Villages whose tap fails in summer", "%"],
    iphs_subcentre_shortfall: ["Sub-centre shortfall against norm", "%"],
    doctor_vacancy_rate: ["Sanctioned doctor posts vacant", "%"],
    // Sub-keys of the nested facilities/doctors blocks.
    sub_centre: ["Sub-centres", ""],
    phc: ["Primary health centres", ""],
    chc: ["Community health centres", ""],
    sanctioned: ["Doctor posts sanctioned", ""],
    in_position: ["Doctors actually in post", ""],
  };

  // Fields that are text or explanatory rather than measurements.
  const EV_TEXT = new Set(["scheme", "rule", "source", "measures"]);

  // Years are numbers that must not be thousands-separated: a sanction from
  // 2006 is not "2,006".
  const EV_RAW_NUMBER = new Set(["oldest_undelivered_sanction_year"]);

  function prettyKey(key) {
    return key.replace(/_/g, " ").replace(/^./, (m) => m.toUpperCase());
  }

  function renderEvidence(target, block) {
    if (!block || !Object.keys(block).length) {
      target.append("div").attr("class", "ev__none")
        .text("No separate record behind this term — it follows from the " +
              "category and the population already shown.");
      return;
    }

    Object.entries(block).forEach(([key, value]) => {
      if (value === null || value === undefined) return;

      // Nested blocks -- `facilities: {sub_centre: 6, phc: 0, chc: 1}` and
      // `doctors: {sanctioned: 2, in_position: 0}` -- are flattened into one
      // row each. Falling through to String() rendered them as the literal
      // text "[object Object]", which is exactly the counts an officer most
      // wants from a health cluster.
      if (typeof value === "object" && !Array.isArray(value)) {
        renderEvidence(target, value);
        return;
      }

      if (typeof value === "boolean") {
        const r = target.append("div").attr("class", "ev");
        r.append("span").attr("class", "ev__k")
          .text(key === "eligible" ? "Meets the rule" : prettyKey(key));
        r.append("span").attr("class", "ev__v").text(value ? "Yes" : "No");
        return;
      }

      // Lists of names read better as a sentence than as a table row.
      if (Array.isArray(value)) {
        if (!value.length) return;
        const text = value
          .map((v) => (v && typeof v === "object"
            ? `${v.name} (${fmt(v.population || 0)})` : String(v)))
          .join(", ");
        const r = target.append("div").attr("class", "ev");
        r.append("span").attr("class", "ev__k")
          .text((EV_LABELS[key] || [prettyKey(key)])[0]);
        r.append("span").attr("class", "ev__v").text(text);
        return;
      }

      const [label, unit] = EV_LABELS[key] || [prettyKey(key), ""];
      let shown;
      if (EV_TEXT.has(key)) {
        shown = String(value);
      } else if (EV_RAW_NUMBER.has(key)) {
        shown = String(value);
      } else if (typeof value === "number") {
        // Shares arrive as 0-1 and read as percentages; everything else is a
        // count, a distance or an amount of money.
        shown = unit === "%"
          ? `${(value * 100).toFixed(1)}%`
          : `${fmt(Number(value.toFixed(2)))}${unit}`;
        if (unit.includes("lakh")) shown = `₹${shown}`;
      } else {
        shown = String(value);
      }

      const r = target.append("div").attr("class", "ev");
      r.append("span").attr("class", "ev__k").text(label);
      r.append("span").attr("class", "ev__v").text(shown);
    });
  }

  /**
   * Fetch and render the citizen reports behind one cluster.
   *
   * Note what is deliberately absent: any identity. Phone numbers and
   * name-prefixes are redacted from the text at ingestion and there is no
   * citizen identity column at all, so this can show what was said, from
   * where, in which language and when -- never by whom. The panel states
   * that outright rather than leaving a viewer to assume the names are
   * simply missing.
   */
  function assetIcon(type) {
    switch (type) {
      case "education": return "🏫";
      case "health": return "🏥";
      case "road": return "🛣";
      case "water": return "💧";
      default: return "📍";
    }
  }

  function locationBasisLabel(basis) {
    switch (basis) {
      case "register_coordinates": return "facility register";
      case "citizen_gps_pin": return "citizen GPS pin";
      case "synthetic_seed": return "approximate location";
      case "mixed": return "mixed sources";
      default: return "village centre";
    }
  }

  /**
   * §10.2 #18 -- render one funding-mismatch warning (cluster dock or asset
   * panel share this exact rendering; the data behind it is computed once,
   * server-side, in routes_dashboard._funding_warnings). "already_funded"
   * is the original orange caution (money already committed nearby); the
   * green "unfunded_need" case says the opposite -- a documented deficiency
   * with no funding record found at all -- rather than staying silent.
   */
  function renderFundingWarning(container, insertBeforeSelector, w) {
    const isUnfunded = w.type === "unfunded_need";
    const style = isUnfunded
      ? "margin:0 0 10px;padding:9px 12px;border-radius:8px;font-size:12px;" +
        "line-height:1.5;color:var(--body);background:rgba(34,139,34,.12);" +
        "border:1px solid rgba(34,139,34,.45)"
      : "margin:0 0 10px;padding:9px 12px;border-radius:8px;font-size:12px;" +
        "line-height:1.5;color:var(--body);background:rgba(203,120,20,.12);" +
        "border:1px solid rgba(203,120,20,.45)";
    const label = isUnfunded
      ? "&#10003; Confirmed unfunded need."
      : "&#9888; Already funded here.";
    const node = insertBeforeSelector
      ? container.insert("div", insertBeforeSelector)
      : container.append("div");
    node.attr("class", "dock__warn").attr("style", style)
      .html(`<b>${label}</b> ${w.message}`);
  }

  function nameBasisLabel(basis) {
    switch (basis) {
      case "citizen_selected": return "citizen picked";
      // The facility's name really does come from the government register;
      // only the complaint linked to it is sample data (flagged in is_demo).
      case "demo_assigned": return "facility register";
      case "nearest_register": return "nearest register — not confirmed";
      case "geosadak_segment": return "PMGSY road register";
      case "pmgsy_work": return "PMGSY work";
      case "unnamed_pin": return "location only";
      case "unresolved_village": return "not specified";
      default: return basis || "not specified";
    }
  }

  function renderReportsList(target, list, highlightId = null) {
    target.html("");
    const reportsArr = list || [];
    const distinctTexts = new Set(reportsArr.map((r) => (r.raw_text || "").trim())).size;

    const priv = target.append("div").attr("class", "privacy");
    priv.append("div").attr("class", "privacy__icon").text("🛡");
    priv.append("div").attr("class", "privacy__text")
      .html(`<b>${reportsArr.length} reports · ${distinctTexts} distinct</b>. ` +
            `Phone numbers and self-stated names are stripped at ingestion, and no ` +
            `citizen identity is stored at all — so this is what the state can see: ` +
            `what was reported and from where, never by whom.`);
    // The privacy guarantee is itself a feature worth explaining, so the
    // reports column gets its own explainer rather than relying on the
    // score panel next to it.
    priv.select(".privacy__text").call(xp("citizen-reports"));

    if (!reportsArr.length) {
      target.append("div").attr("class", "ev__none").text("No citizen reports recorded.");
      return;
    }

    reportsArr.forEach((r) => {
      const isNew = highlightId != null && r.id === highlightId;
      const el = target.append("div").attr("class", `rep${isNew ? " rep--new" : ""}`);
      const top = el.append("div").attr("class", "rep__top");
      top.append("span").attr("class", "rep__lang").text(r.language_detected || "?");
      if (r.severity) {
        top.append("span")
          .attr("class", `rep__sev rep__sev--${r.severity}`)
          .text(r.severity);
      }
      if (isNew) {
        top.append("span").attr("class", "rep__sev rep__sev--medium").text("yours");
      }
      top.append("span").attr("class", "rep__where").text(r.village || r.block || "—");
      el.append("div").attr("class", "rep__text").text(r.raw_text || "");

      // Evidence the citizen attached, if any. Metadata-only elsewhere on
      // this row; the photo itself is the one thing worth an extra click
      // rather than always-on, since most reports have none.
      const photos = r.photos || [];
      if (photos.length) {
        const imgs = el.append("div").attr("class", "rep__images").style("display", "none");
        photos.forEach((p) => {
          imgs.append("img")
            .attr("class", "rep__image")
            .attr("src", `${API}${p.url}`)
            .attr("loading", "lazy")
            .attr("alt", "Photo attached to this report")
            .on("click", function () { window.open(this.src, "_blank"); });
        });
        el.insert("button", ".rep__images")
          .attr("type", "button")
          .attr("class", "rep__photobtn")
          .text(`📷 Image${photos.length > 1 ? "s" : ""} (${photos.length})`)
          .on("click", function () {
            const showing = imgs.style("display") !== "none";
            imgs.style("display", showing ? "none" : "flex");
            d3.select(this).text(
              showing
                ? `📷 Image${photos.length > 1 ? "s" : ""} (${photos.length})`
                : "▲ Hide image" + (photos.length > 1 ? "s" : "")
            );
          });
      }
    });
  }

  async function loadClusterReports(clusterId, target, highlightId = null) {
    let data;
    try {
      data = await d3.json(`${API}/clusters/${clusterId}/reports`);
    } catch {
      target.text("Couldn't load the reports for this cluster.");
      return;
    }
    renderReportsList(target, data.reports || [], highlightId);
  }

  function populateEvidence(evTargets, evidence, dataBasis) {
    if (!evidence) return;
    const db = dataBasis || {};
    Object.entries(evTargets).forEach(([term, target]) => {
      target.html("");
      const basis = db[term];
      if (basis) {
        const proxy = /^proxy|straight_line/.test(basis);
        target.append("div")
          .attr("style",
            "font-size:10px;font-weight:700;letter-spacing:.05em;" +
            "text-transform:uppercase;margin-bottom:5px;" +
            `color:${proxy ? "#c47b14" : "#2e7d32"}`)
          .text(proxy
            ? "proxy — no government record for this term"
            : "from a real record");
      }
      renderEvidence(target, evidence[EVIDENCE_KEY[term]]);
      if (term === "strategic" && evidence.cost_benchmark &&
          Object.keys(evidence.cost_benchmark).length) {
        target.append("div").attr("class", "ev__none").style("margin-top", "6px")
          .style("font-style", "normal").text("What comparable work has cost:");
        renderEvidence(target, evidence.cost_benchmark);
      }
    });
  }

  function renderBreakdown(target, breakdown, evidence = null, dataBasis = null) {
    const b = breakdown || {};
    const maxAbs = d3.max(TERMS, ([k]) => Math.abs(b[k] || 0)) || 1;
    const sum = TERMS.reduce((acc, [k]) => acc + (b[k] || 0), 0);

    const evTargets = {};
    TERMS.forEach(([key, label, source]) => {
      const val = b[key] ?? 0;
      const t = target.append("div").attr("class", "term");
      const r = t.append("div").attr("class", "term__row");
      r.append("span").attr("class", "term__name").text(label);
      r.append("span").attr("class", "term__caret").text("▸");
      r.append("span")
        .attr("class", `term__val${val < 0 ? " term__val--neg" : ""}`)
        .text(`${val > 0 ? "+" : ""}${val.toFixed(2)}`);
      const bar = t.append("div").attr("class", "term__track")
        .append("div")
        .attr("class", `term__bar${val < 0 ? " term__bar--neg" : val === 0 ? " term__bar--zero" : ""}`);
      requestAnimationFrame(() => {
        bar.style("width", `${Math.max(Math.abs(val) / maxAbs * 100, val === 0 ? 2 : 4)}%`);
      });
      t.append("div").attr("class", "term__src").text(source);
      evTargets[key] = t.append("div").attr("class", "term__ev")
        .text(evidence ? "" : "Loading the record…");

      t.classed("term--open", true);
      r.select(".term__caret").text("▾");

      r.on("click", () => {
        const open = !t.classed("term--open");
        t.classed("term--open", open);
        r.select(".term__caret").text(open ? "▾" : "▸");
      });
    });

    const sl = target.append("div").attr("class", "sumline");
    sl.append("span").attr("class", "sumline__l").text("Sum of all nine terms");
    sl.append("span").attr("class", "sumline__v").text(sum.toFixed(2));

    if (evidence) {
      populateEvidence(evTargets, evidence, dataBasis);
    }

    return evTargets;
  }

  async function renderPanel(c) {
    const dock = d3.select("#dock");
    dock.html("");

    /* -- head -- */
    const head = dock.append("div").attr("class", "dock__head");
    head.append("span").attr("class", "dock__cat").text(c.issue_category || "—");
    const titles = head.append("div");
    titles.append("div").attr("class", "dock__title")
      .text(c.block || c.district || `Cluster ${c.id}`);
    titles.append("div").attr("class", "dock__sub")
      .text(`${c.district} district · cluster ${c.id}`);
    head.append("div").attr("class", "dock__spacer");
    head.append("div").attr("class", "dock__score")
      .text((c.priority_score ?? 0).toFixed(2));
    head.append("div").attr("class", "dock__scoreof").text("/ 100");
    head.append("div").attr("class", "dock__rank")
      .text(`RANK #${rankOf(c)} OF ${clusters.length}`);
    head.append("button")
      .attr("class", "dock__close")
      .attr("aria-label", "Close detail")
      .html("&times;")
      .on("click", () => navigate("district", { state: nav.state, district: nav.district }));

    const cols = dock.append("div").attr("class", "dock__cols");

    /* -- column 1: the nine terms, each opening onto its evidence -- */
    const sec = cols.append("div").attr("class", "dock__col");
    sec.append("div").attr("class", "eyebrow").text("Why this score").call(xp("priority-score"));
    sec.append("div").attr("class", "dock__hint")
      .text("Nine independently-sourced terms, summing to the score exactly. " +
            "Click any term to see the record it was computed from.");

    const evTargets = renderBreakdown(sec, c.breakdown, null, null);

    /* -- why this ranks above the next cluster (§10.2 #16) -- */
    const runner = c.runner_up_cluster_id != null
      ? clusters.find((x) => x.id === c.runner_up_cluster_id)
      : null;
    if (runner && runner.breakdown) {
      const b = c.breakdown || {};
      const rb = runner.breakdown;
      const ahead = TERMS
        .map(([k, label]) => ({ label, d: (b[k] || 0) - (rb[k] || 0) }))
        .filter((x) => x.d > 0.3)
        .sort((x, y) => y.d - x.d)
        .slice(0, 3);
      const rName = runner.block
        || `${runner.district || "cluster"} #${runner.id}`;
      sec.append("div").attr("class", "cfline")
        .attr("style",
          "margin-top:12px;padding:10px 12px;border-radius:8px;" +
          "background:var(--cream-deep,#efeadd);font-size:11.5px;" +
          "line-height:1.6;color:var(--navy,#1b2a4a)")
        .html(
          `<b>Why this outranks the next cluster.</b> Ahead of <b>${rName}</b> ` +
          `(${runner.issue_category} · ${(runner.priority_score ?? 0).toFixed(2)})` +
          (ahead.length
            ? `, carried by ` + ahead
                .map((x) => `${x.label.toLowerCase()} +${x.d.toFixed(2)}`)
                .join(", ") + `.`
            : ` on the combined terms — no single term decides it.`));
    }

    /* -- column 2: which specific thing is broken -- */
    const mid = cols.append("div").attr("class", "dock__col");
    const facts = mid.append("div").attr("class", "facts");
    [
      [fmt(c.report_count ?? 0), "citizen reports"],
      [fmt(c.unique_reporters ?? 0), "distinct reporters"],
      [fmt(c.population_affected ?? 0), "people affected"],
      [fmt(c.settlement_count ?? 0), "settlements"],
    ].forEach(([v, l]) => {
      const f = facts.append("div").attr("class", "fact");
      f.append("div").attr("class", "fact__v").text(v);
      f.append("div").attr("class", "fact__l").text(l);
    });

    mid.append("div").attr("class", "eyebrow").style("margin-top", "16px")
      .text("Which specific thing is broken");
    mid.append("div").attr("class", "dock__hint")
      .text("The cluster decides where the money goes. These are the places " +
            "inside it a crew would actually be sent.");
    const wgBody = mid.append("div").text("Loading work groups…")
      .style("font-size", "11.5px").style("color", "var(--muted)");

    /* -- column 3: the reports, then the counterfactual -- */
    const right = cols.append("div").attr("class", "dock__col");
    right.append("div").attr("class", "eyebrow").text("Who reported this").call(xp("citizen-reports"));
    right.append("div").attr("class", "dock__hint")
      .text("The individual citizen reports corroborated into this cluster.");
    const repsBody = right.append("div").text("Loading reports…")
      .style("font-size", "11.5px").style("color", "var(--muted)");

    // ?mine=<report id> is how the intake screen hands the citizen back to
    // their own report after clustering runs, so it has to be read here --
    // without it the "yours" badge in loadClusterReports can never fire.
    const mineId = Number(params.get("mine"));
    loadClusterReports(c.id, repsBody, Number.isFinite(mineId) && mineId > 0 ? mineId : null);

    // Only on the closed -> open transition. refit() re-navigates, navigate()
    // re-renders this dock, and an unconditional refit here would schedule
    // another one every 440ms forever -- rebuilding the DOM under the user
    // and silently discarding any term they had expanded.
    const wasOpen = dock.classed("dock--open");
    dock.classed("dock--open", true);
    if (!wasOpen) setTimeout(refit, 440);

    /* -- the evidence and work groups need the detail endpoint -- */
    loadClusterDetail(c.id, evTargets, wgBody);

    /* -- counterfactual -- */
  }

  /**
   * Fetch the working behind the score and the work groups.
   *
   * A second request on purpose. /clusters carries what the rail and the map
   * need for all 114 at once; the evidence is far larger and is only ever
   * read for the one cluster someone opened, so shipping it with the list
   * would make every page load pay for detail nobody asked for.
   */
  async function loadClusterDetail(clusterId, evTargets, wgBody) {
    let detail;
    try {
      detail = await d3.json(`${API}/clusters/${clusterId}`);
    } catch {
      Object.values(evTargets).forEach((t) =>
        t.text("Couldn't load the record behind this term."));
      wgBody.text("Couldn't load the work groups.");
      return;
    }

    const evidence = detail.evidence || {};

    // §10.2 #18 -- a caution surfaced on the recommendation itself when money
    // is already committed where this cluster would be funded again, or an
    // explicit confirmation when a documented need has no funding record at
    // all. Built by the API from evidence it had already computed; empty for
    // most clusters.
    const dockEl = d3.select("#dock");
    dockEl.selectAll(".dock__warn").remove();
    (detail.warnings || []).forEach((w) => renderFundingWarning(dockEl, ".dock__cols", w));

    // §10.2 #3 -- which terms rested on a real government record and which
    // fell back to a proxy. scoring.py computed this; it is now persisted and
    // returned, so a proxy no longer looks identical to a measurement.
    populateEvidence(evTargets, evidence, detail.data_basis || {});

    const groups = detail.work_groups || [];
    wgBody.html("");
    if (!groups.length) {
      wgBody.append("div").attr("class", "ev__none")
        .text("No work groups — every report in this cluster lacks coordinates.");
      return;
    }

    groups.forEach((g) => {
      const cands = g.asset_candidates || [];
      const card = wgBody.append("div").attr("class", "wg")
        .on("click", () => flyToWorkGroup(g));

      const top = card.append("div").attr("class", "wg__top");
      // When a register settles it, the asset's own name is the heading and
      // the village becomes the subtitle. Otherwise the village leads, because
      // that is genuinely all that is established.
      top.append("span").attr("class", "wg__name").text(g.asset_label || g.label);
      if (g.asset_label) {
        top.append("span").attr("class", "wg__spread").text(g.label);
      }
      top.append("span").attr("class", "wg__count")
        .text(`${g.report_count} report${g.report_count === 1 ? "" : "s"}`);

      card.append("div").attr("class", "wg__text").text(g.sample_text || "");

      if (g.asset_label) {
        card.append("div").attr("class", "wg__asset")
          .text(`Named from the ${REGISTER_NAMES[g.asset_source] || `${g.asset_source} register`}` +
                (g.asset_external_id ? ` · ${g.asset_external_id}` : ""));
      } else if (cands.length) {
        const box = card.append("div").attr("class", "wg__cands");
        box.append("div").attr("class", "wg__candhead")
          .text(`${cands.length} possible — the register cannot say which:`);
        cands.forEach((a) => {
          const row = box.append("div").attr("class", "cand");
          row.append("span").attr("class", "cand__n").text(a.name);
          row.append("span").attr("class", "cand__d")
            .text(a.distance_m != null ? `${a.distance_m} m` : (a.detail || ""));
        });
      }
    });

    const anyNamed = groups.some((g) => g.asset_label || (g.asset_candidates || []).length);
    wgBody.append("div").attr("class", "ev__none").style("margin-top", "9px")
      .text(anyNamed
        ? "Candidates come from the government's own registers. A GPS pin on " +
          "the complaint would narrow them to one."
        : "No register of individual facilities exists for this sector yet, so " +
          "a group can only be named by its village.");
  }

  function renderVillagePanel(vData) {
    const dock = d3.select("#dock");
    dock.html("");

    const topAsset = vData.top_asset || {};
    const districtVillages = districtVillagesCache[nav.district] || [];
    const totalVillages = districtVillages.length || (vData.rank_in_district ? vData.rank_in_district : "—");
    const rankText = vData.rank_in_district
      ? `RANK #${vData.rank_in_district} OF ${totalVillages} VILLAGES IN ${vData.district}`
      : "VILLAGE PRIORITY";

    /* -- head -- */
    const head = dock.append("div").attr("class", "dock__head");
    head.append("span").attr("class", "dock__cat").text("Village Priority");
    if (vData.is_demo) {
      head.append("span").attr("class", "badge--demo").text("demo data");
    }
    const titles = head.append("div");
    titles.append("div").attr("class", "dock__title").text(vData.name);
    titles.append("div").attr("class", "dock__sub")
      .text(`${vData.block ? vData.block + " block · " : ""}${vData.district} district · ${vData.asset_count || (vData.assets || []).length} assets`);
    head.append("div").attr("class", "dock__spacer");
    if (vData.has_urgent_asset) {
      head.append("span").attr("class", "badge--urgent").text("⚠ Urgent");
    }
    head.append("div").attr("class", "dock__score")
      .text((vData.priority_score ?? 0).toFixed(2));
    head.append("div").attr("class", "dock__scoreof").text("/ 100");
    head.append("div").attr("class", "dock__rank").text(rankText);
    head.append("button")
      .attr("class", "dock__close")
      .attr("aria-label", "Close detail")
      .html("&times;")
      .on("click", () => navigate("district", { state: nav.state, district: nav.district }));

    // Independent of priority_score/top_asset on purpose: the score above is
    // driven by whichever single asset ranks highest, so a real emergency at
    // a DIFFERENT asset in this village would otherwise never appear here.
    if (vData.has_urgent_asset && vData.urgent_asset) {
      const ua = vData.urgent_asset;
      const warn = dock.insert("div", ".dock__cols")
        .attr("class", "dock__warn dock__warn--urgent")
        .style("cursor", "pointer")
        .on("click", () => navigate("asset", { asset: ua, village: vData, state: nav.state, district: nav.district }));
      warn.append("b").text(`⚠ Urgent: ${ua.grade_label} damage detected. `);
      warn.append("span").text(
        `${ua.name} (${ua.asset_type}) has a real emergency-graded photo or report, ` +
        "even though it isn't this village's top-ranked asset. Click to open it."
      );
    }

    const cols = dock.append("div").attr("class", "dock__cols");

    /* -- Col 1: Why this score (driven by top asset) -- */
    const col1 = cols.append("div").attr("class", "dock__col");
    col1.append("div").attr("class", "eyebrow").text("Why this score").call(xp("priority-score"));
    col1.append("div").attr("class", "dock__hint")
      .html(topAsset.name
        ? `Driven by its highest-need asset: <b>${topAsset.name}</b>`
        : "Nine independently-sourced terms for this village's highest-need asset.");

    renderBreakdown(col1, topAsset.breakdown, topAsset.evidence, topAsset.data_basis);

    /* -- Col 2: Fix first inside this village -- */
    const col2 = cols.append("div").attr("class", "dock__col");
    col2.append("div").attr("class", "eyebrow").text("Fix first inside this village").call(xp("fix-first"));
    col2.append("div").attr("class", "dock__hint")
      .text("Ranked assets in this village. Click any asset to inspect its evidence.");

    const assets = (vData.assets || []).slice().sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
    const listEl = col2.append("div");
    if (!assets.length) {
      listEl.append("div").attr("class", "ev__none").text("No assets identified for this village.");
    }
    assets.forEach((a) => {
      const card = listEl.append("div").attr("class", "wg")
        .on("click", () => navigate("asset", { asset: a, village: vData, state: nav.state, district: nav.district }));
      const top = card.append("div").attr("class", "wg__top");
      top.append("span").style("font-size", "14px").text(assetIcon(a.asset_type));
      top.append("span").attr("class", "wg__name").text(a.name);
      top.append("span").attr("class", "wg__count").text((a.priority_score ?? 0).toFixed(1));

      const sub = card.append("div").style("display", "flex").style("align-items", "center").style("gap", "6px").style("margin-top", "4px").style("flex-wrap", "wrap");
      sub.append("span").style("font-size", "11px").style("color", "var(--body)")
        .text(`${a.report_count} report${a.report_count === 1 ? "" : "s"}`);
      sub.append("span").attr("class", "badge--name-basis").text(nameBasisLabel(a.name_basis));
      if (a.is_demo) {
        sub.append("span").attr("class", "badge--demo").text("demo data");
      }
    });

    /* -- Col 3: Reports -- */
    const col3 = cols.append("div").attr("class", "dock__col");
    col3.append("div").attr("class", "eyebrow").text("Citizen reports").call(xp("citizen-reports"));
    col3.append("div").attr("class", "dock__hint")
      .text(`Individual citizen grievances from ${vData.name}.`);

    renderReportsList(col3, vData.reports || []);

    const wasOpen = dock.classed("dock--open");
    dock.classed("dock--open", true);
    if (!wasOpen) setTimeout(refit, 440);
  }

  function renderAssetPanel(aData) {
    const dock = d3.select("#dock");
    dock.html("");

    /* -- head -- */
    const head = dock.append("div").attr("class", "dock__head");
    head.append("span").attr("class", "dock__cat").text(aData.asset_type || "Asset");
    if (aData.is_demo) {
      head.append("span").attr("class", "badge--demo").text("demo data");
    }
    const titles = head.append("div");
    titles.append("div").attr("class", "dock__title").text(aData.name);
    titles.append("div").attr("class", "dock__sub")
      .text(`${aData.village ? aData.village + " · " : ""}${aData.block ? aData.block + " block · " : ""}${aData.district} district`);
    head.append("div").attr("class", "dock__spacer");
    head.append("div").attr("class", "dock__score")
      .text((aData.priority_score ?? 0).toFixed(2));
    head.append("div").attr("class", "dock__scoreof").text("/ 100");
    const rankLabel = aData.rank_in_village
      ? `RANK #${aData.rank_in_village} IN ${aData.village || "VILLAGE"}`
      : "ASSET PRIORITY";
    head.append("div").attr("class", "dock__rank").text(rankLabel);
    head.append("button")
      .attr("class", "dock__close")
      .attr("aria-label", "Close detail")
      .html("&times;")
      .on("click", () => {
        if (nav.village) {
          navigate("village", { village: nav.village, state: nav.state, district: nav.district });
        } else {
          navigate("district", { state: nav.state, district: nav.district });
        }
      });

    const cols = dock.append("div").attr("class", "dock__cols");

    /* -- Col 1: Nine-term breakdown -- */
    const col1 = cols.append("div").attr("class", "dock__col");
    col1.append("div").attr("class", "eyebrow").text("Why this score").call(xp("priority-score"));
    col1.append("div").attr("class", "dock__hint")
      .text("Nine independently-sourced terms for this specific asset.");

    renderBreakdown(col1, aData.breakdown, aData.evidence, aData.data_basis);

    /* -- Col 2: Facts & provenance -- */
    const col2 = cols.append("div").attr("class", "dock__col");
    col2.append("div").attr("class", "eyebrow").text("Asset facts & provenance").call(xp("priority-score"));
    col2.append("div").attr("class", "dock__hint")
      .text("Registers, location provenance, and scheme records behind this asset.");

    // §10.2 #18 -- same funding-mismatch caution/confirmation as the cluster
    // dock, computed server-side from this asset's own evidence (road, water
    // and school all covered; see routes_dashboard._funding_warnings).
    (aData.warnings || []).forEach((w) => renderFundingWarning(col2, null, w));

    const facts = col2.append("div").attr("class", "facts");
    [
      [fmt(aData.report_count ?? 0), "citizen reports"],
      [fmt(aData.distinct_reporters ?? 0), "distinct reporters"],
      [nameBasisLabel(aData.name_basis), "naming source"],
      [locationBasisLabel(aData.location_basis), "location source"],
    ].forEach(([v, l]) => {
      const f = facts.append("div").attr("class", "fact");
      f.append("div").attr("class", "fact__v").style("font-size", "14px").text(v);
      f.append("div").attr("class", "fact__l").text(l);
    });

    const kvBox = col2.append("div").style("margin-top", "12px");
    if (aData.source) {
      const row = kvBox.append("div").attr("class", "ev");
      row.append("span").attr("class", "ev__k").text("Register source");
      row.append("span").attr("class", "ev__v").text(`${REGISTER_NAMES[aData.source] || aData.source}${aData.external_id ? ` · ${aData.external_id}` : ""}`);
    }
    if (aData.villages_served && aData.villages_served.length) {
      const row = kvBox.append("div").attr("class", "ev");
      row.append("span").attr("class", "ev__k").text("Villages served");
      row.append("span").attr("class", "ev__v").text(aData.villages_served.join(", "));
    }
    const road = (aData.evidence || {}).road_geometry;
    if (road) {
      [
        ["DRRP road code", road.drrp_road_code],
        ["Road category", road.road_category],
        ["Road owner", road.road_owner],
        ["Complaint to road", road.distance_m != null ? `${road.distance_m} m` : null],
      ].filter(([, v]) => v).forEach(([k, v]) => {
        const row = kvBox.append("div").attr("class", "ev");
        row.append("span").attr("class", "ev__k").text(k);
        row.append("span").attr("class", "ev__v").text(v);
      });
    }

    // Groundwater evidence for water assets
    const gwEv = (aData.evidence || {}).groundwater;
    if (gwEv) {
      const gwBox = col2.append("div").style("margin-top", "12px").attr("class", "term__ev").style("display", "block");
      gwBox.append("div").style("font-size", "10px").style("font-weight", "700").style("text-transform", "uppercase").style("color", "#2e7d32").style("margin-bottom", "4px").text("Groundwater telemetry (NWDP)");
      renderEvidence(gwBox, gwEv);
    }

    // Workstream C: photo evidence -- what the checks and the AI model found
    // across this asset's photos, and each photo with its flags.
    const photos = aData.photos || [];
    if (photos.length && window.PhotoChecks) {
      const pBox = col2.append("div").style("margin-top", "14px");
      pBox.append("div").attr("class", "eyebrow").text(`Photo evidence (${photos.length})`);
      const summary = window.PhotoChecks.renderAssetSummary(aData.evidence);
      if (summary) pBox.node().appendChild(summary);
      const list = pBox.append("div").node();
      const cards = photos.map((p) => window.PhotoChecks.render(p, { api: API, compact: true }));
      cards.slice(0, 3).forEach((c) => list.appendChild(c));
      if (cards.length > 3) {
        pBox.append("button").attr("class", "btn").style("width", "100%")
          .text(`SHOW ALL ${cards.length} PHOTOS`)
          .on("click", function () { cards.slice(3).forEach((c) => list.appendChild(c)); this.remove(); });
      }
      // Clicking a compact card opens its full check list.
      cards.forEach((c, i) => {
        c.style.cursor = "pointer";
        c.addEventListener("click", (e) => {
          if (e.target.tagName === "IMG") return;
          const full = window.PhotoChecks.render(photos[i], { api: API });
          const overlay = document.createElement("div");
          overlay.className = "plight";
          overlay.style.cursor = "default";
          const panel = document.createElement("div");
          panel.style.cssText = "width:min(860px,100%);max-height:100%;overflow:auto;";
          panel.appendChild(full);
          overlay.appendChild(panel);
          overlay.addEventListener("click", (ev) => { if (ev.target === overlay) overlay.remove(); });
          document.body.appendChild(overlay);
        });
      });
    }

    // Candidates if unresolved
    const cands = aData.candidates || [];
    if (cands.length) {
      const box = col2.append("div").attr("class", "wg__cands").style("margin-top", "12px");
      box.append("div").attr("class", "wg__candhead")
        .text(`${cands.length} possible facilities in this village — citizen pin narrows to one:`);
      cands.forEach((c) => {
        const row = box.append("div").attr("class", "cand");
        row.append("span").attr("class", "cand__n").text(c.name);
        row.append("span").attr("class", "cand__d")
          .text(c.distance_m != null ? `${c.distance_m} m` : (c.sub_type || ""));
      });
    }

    /* -- Col 3: Reports -- */
    const col3 = cols.append("div").attr("class", "dock__col");
    col3.append("div").attr("class", "eyebrow").text("Reports for this asset").call(xp("citizen-reports"));
    col3.append("div").attr("class", "dock__hint")
      .text(`The individual citizen grievances grouped into this asset.`);

    renderReportsList(col3, aData.reports || []);

    const wasOpen = dock.classed("dock--open");
    dock.classed("dock--open", true);
    if (!wasOpen) setTimeout(refit, 440);
  }

  /** Centre the map on one work group so the officer sees where it is. */
  function flyToWorkGroup(group) {
    if (group.lat == null || group.lon == null) return;
    const [x, y] = projection([group.lon, group.lat]);
    animateTo({ k: Math.max(view.k, 40), x: width / 2 - x * Math.max(view.k, 40),
                y: height / 2 - y * Math.max(view.k, 40) });
  }

  /** Clusters in scope for the current level. */
  function visibleClusters() {
    if (nav.level === "cluster" && nav.cluster) return [nav.cluster];
    if (nav.district) return clusters.filter((c) => c.district === nav.district);
    return clusters;
  }

  /* ------------------------------------------------------------ drawing -- */

  function drawStates() {
    const sel = gStates.selectAll("path").data(statesFC.features, (d) => d.properties.st_nm);
    const enter = sel.enter().append("path").attr("d", path);
    enter.merge(sel)
      .attr("d", path)
      .attr("class", (d) =>
        d.properties.st_nm === PILOT_STATE ? "geo geo--active" : "geo geo--dim")
      .on("click", (e, d) => {
        if (d.properties.st_nm === PILOT_STATE) navigate("state", { state: PILOT_STATE });
      })
      .on("mousemove", (e, d) => {
        const isPilot = d.properties.st_nm === PILOT_STATE;
        showTip(e, `<div class="tooltip__title">${d.properties.st_nm}</div>` +
          (isPilot
            ? `<div class="tooltip__row"><span>Clusters</span><b>${clusters.length}</b></div>
               <div class="tooltip__hint">Click to enter</div>`
            : `<div class="tooltip__row"><span>Coverage</span><b>no pilot data</b></div>`));
      })
      .on("mouseleave", hideTip);
    sel.exit().remove();
  }

  function drawDistricts(stateName) {
    const feats = districtsFC.features.filter((d) => d.properties.st_nm === stateName);
    const withData = new Set(clusters.map((c) => c.district));

    const sel = gDistricts.selectAll("path").data(feats, (d) => d.properties.dt_code);
    const enter = sel.enter().append("path").attr("d", path);
    enter.merge(sel)
      .attr("class", (d) =>
        withData.has(d.properties.district) ? "geo geo--active" : "geo geo--dim")
      .on("click", (e, d) => {
        if (withData.has(d.properties.district)) {
          navigate("district", { state: stateName, district: d.properties.district });
        }
      })
      .on("mousemove", (e, d) => {
        const name = d.properties.district;
        const mine = clusters.filter((c) => c.district === name);
        showTip(e, `<div class="tooltip__title">${name}</div>` +
          (mine.length
            ? `<div class="tooltip__row"><span>Clusters</span><b>${mine.length}</b></div>
               <div class="tooltip__row"><span>People affected</span><b>${fmt(d3.sum(mine, (c) => c.population_affected || 0))}</b></div>
               <div class="tooltip__hint">Click to enter</div>`
            : `<div class="tooltip__row"><span>Coverage</span><b>no pilot data</b></div>`));
      })
      .on("mouseleave", hideTip);
    sel.exit().remove();
  }

  function drawOutline(feature) {
    const sel = gOutline.selectAll("path").data(feature ? [feature] : []);
    sel.enter().append("path").attr("class", "geo geo--outline").merge(sel).attr("d", path);
    sel.exit().remove();
  }

  function drawClusters(list) {
    const sel = gClusters.selectAll("circle").data(list, (d) => d.id);

    const enter = sel.enter().append("circle")
      .attr("class", "cluster")
      .attr("cx", (d) => projection([d.centroid_lon, d.centroid_lat])[0])
      .attr("cy", (d) => projection([d.centroid_lon, d.centroid_lat])[1])
      .attr("r", 0)
      .attr("fill", (d) => scoreColour(d.priority_score))
      .on("click", (e, d) => { e.stopPropagation(); navigate("cluster", { cluster: d, state: nav.state || PILOT_STATE, district: nav.district || d.district }); })
      .on("mousemove", (e, d) => {
        showTip(e, `
          <div class="tooltip__title">${d.block || d.district} · ${d.issue_category}</div>
          <div class="tooltip__row"><span>Priority</span><span class="tooltip__score">${(d.priority_score ?? 0).toFixed(2)}</span></div>
          <div class="tooltip__row"><span>Reports</span><b>${d.report_count}</b></div>
          <div class="tooltip__row"><span>Unique reporters</span><b>${d.unique_reporters}</b></div>
          <div class="tooltip__row"><span>People affected</span><b>${fmt(d.population_affected || 0)}</b></div>
          <div class="tooltip__hint">Click for detail</div>`);
      })
      .on("mouseleave", hideTip);

    // cx/cy are reassigned on the MERGED selection, not only on enter. The
    // projection is refitted whenever the drawing area changes size -- which
    // now happens every time the detail dock opens -- and a circle that keeps
    // the coordinates it was born with is then drawn for a projection that no
    // longer exists. At cluster zoom that put every mark tens of thousands of
    // pixels outside the viewBox, i.e. an empty map.
    enter.merge(sel)
      .attr("cx", (d) => projection([d.centroid_lon, d.centroid_lat])[0])
      .attr("cy", (d) => projection([d.centroid_lon, d.centroid_lat])[1])
      .transition().duration(520).ease(d3.easeCubicOut)
      .attr("r", (d) => radiusScale(d.population_affected || 0) / view.k)
      .attr("fill", (d) => scoreColour(d.priority_score));

    sel.exit().transition().duration(240).attr("r", 0).remove();
  }

  function drawVillages(list) {
    if (!list || !list.length) {
      gVillages.selectAll("circle").remove();
      return;
    }
    const maxRep = d3.max(list, (v) => v.report_count) || 10;
    vRadiusScale = d3.scaleSqrt().domain([1, maxRep]).range([5, 22]);

    const sel = gVillages.selectAll("circle").data(list, (d) => d.gazetteer_id);

    const enter = sel.enter().append("circle")
      .attr("class", "village-bubble")
      .attr("cx", (d) => projection([d.lon, d.lat])[0])
      .attr("cy", (d) => projection([d.lon, d.lat])[1])
      .attr("r", 0)
      .attr("fill", (d) => scoreColour(d.priority_score))
      .on("click", (e, d) => {
        e.stopPropagation();
        navigate("village", { village: d, state: nav.state || PILOT_STATE, district: nav.district || d.district });
      })
      .on("mousemove", (e, d) => {
        const counts = d.counts_by_category || {};
        const cParts = [];
        if (counts.road) cParts.push(`${counts.road} road`);
        if (counts.education) cParts.push(`${counts.education} school`);
        if (counts.health) cParts.push(`${counts.health} health`);
        if (counts.water) cParts.push(`${counts.water} water`);
        const cStr = cParts.length ? ` · ${cParts.join(", ")}` : "";
        const demoStr = d.is_demo ? ` <span class="badge--demo">demo data</span>` : "";
        // Independent of priority_score, which only reflects this village's
        // single highest-scoring asset -- see renderVillagePanel's own note.
        const urgentStr = d.has_urgent_asset ? ` <span class="badge--urgent">⚠ Urgent</span>` : "";

        showTip(e, `
          <div class="tooltip__title">${d.name}${demoStr}${urgentStr}</div>
          <div class="tooltip__row"><span>Priority score</span><span class="tooltip__score">${(d.priority_score ?? 0).toFixed(2)}</span></div>
          <div class="tooltip__row"><span>District rank</span><b>#${d.rank_in_district || "—"}</b></div>
          <div class="tooltip__row"><span>Reports</span><b>${d.report_count}${cStr}</b></div>
          <div class="tooltip__hint">Click to inspect village assets</div>`);
      })
      .on("mouseleave", hideTip);

    enter.merge(sel)
      .attr("cx", (d) => projection([d.lon, d.lat])[0])
      .attr("cy", (d) => projection([d.lon, d.lat])[1])
      .transition().duration(520).ease(d3.easeCubicOut)
      .attr("r", (d) => vRadiusScale(d.report_count || 1) / view.k)
      .attr("fill", (d) => scoreColour(d.priority_score));

    sel.exit().transition().duration(240).attr("r", 0).remove();
  }

  function clearAssets() {
    gAssets.selectAll(".asset-pin").remove();
    gRoadLines.selectAll("path").remove();
  }

  // The real PMGSY road a road asset sits on, drawn under the pins. Points
  // arrive as [lat, lon]; d3 projections take [lon, lat].
  function drawRoadLines(list) {
    const roads = (list || []).filter(
      (d) => d.road_geometry && Array.isArray(d.road_geometry.points) && d.road_geometry.points.length > 1);
    const line = d3.line()
      .x((p) => projection([p[1], p[0]])[0])
      .y((p) => projection([p[1], p[0]])[1]);
    const sel = gRoadLines.selectAll("path").data(roads, (d) => d.id);
    sel.enter().append("path")
      .attr("class", "road-line")
      .merge(sel)
      .attr("d", (d) => line(d.road_geometry.points));
    sel.exit().remove();
  }

  function drawAssets(list, villageName) {
    if (!list || !list.length) {
      clearAssets();
      return;
    }
    drawRoadLines(list);
    // Spread pins that would sit on top of each other on screen. Several
    // assets can share almost the same coordinates (two colleges on one
    // campus, water points around the village centre); stacked pins hid each
    // other and a click landed on the wrong one. Each crowded pin is nudged
    // onto a small ring around the one it collides with -- a display offset
    // only, the stored coordinates are untouched.
    const MIN_GAP_PX = ASSET_PIN_R * 2 + 6;
    const k = view.k || 1;
    const placed = [];
    const offsets = new Map();
    list.slice()
      .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0))
      .forEach((d) => {
        const [px, py] = projection([d.lon, d.lat]);
        let sx = px * k, sy = py * k;
        const hit = placed.find((p) => Math.hypot(p.sx - sx, p.sy - sy) < MIN_GAP_PX);
        if (hit) {
          hit.ring = (hit.ring || 0) + 1;
          const angle = hit.ring * 2.4;             // golden-angle-ish spread
          const radius = MIN_GAP_PX * (1 + Math.floor(hit.ring / 6) * 0.9);
          sx = hit.sx + Math.cos(angle) * radius;
          sy = hit.sy + Math.sin(angle) * radius;
        }
        placed.push({ sx, sy });
        offsets.set(d.id, [sx / k, sy / k]);
      });
    const pinXY = (d) => offsets.get(d.id) || projection([d.lon, d.lat]);

    // Only the three highest-need assets carry a permanent label; the rest
    // show their name on hover. Every asset is also listed, ranked, in the
    // village panel below, so nothing is hidden -- the map just stops being
    // a pile of overlapping text.
    const labelled = new Set(
      list.slice()
        .sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0))
        .slice(0, 3)
        .map((d) => d.id));

    const sel = gAssets.selectAll(".asset-pin").data(list, (d) => d.id);

    const enter = sel.enter().append("g")
      .attr("class", "asset-pin")
      .attr("transform", (d) => {
        const [px, py] = pinXY(d);
        return `translate(${px},${py})`;
      })
      .on("click", (e, d) => {
        e.stopPropagation();
        navigate("asset", { asset: d, village: nav.village, state: nav.state, district: nav.district });
      })
      .on("mousemove", (e, d) => {
        const servesStr = (d.village && villageName && d.village !== villageName) ? ` · serves ${villageName}` : "";
        const demoStr = d.is_demo ? ` <span class="badge--demo">demo data</span>` : "";
        showTip(e, `
          <div class="tooltip__title">${assetIcon(d.asset_type)} ${d.name}${servesStr}${demoStr}</div>
          <div class="tooltip__row"><span>Type</span><b>${d.asset_type}</b></div>
          <div class="tooltip__row"><span>Score</span><span class="tooltip__score">${(d.priority_score ?? 0).toFixed(2)}</span></div>
          <div class="tooltip__row"><span>Reports</span><b>${d.report_count}</b></div>
          <div class="tooltip__hint">Click to inspect asset</div>`);
      })
      .on("mouseleave", hideTip);

    enter.append("circle")
      .attr("class", "asset-circle")
      .attr("r", ASSET_PIN_R / view.k)
      .attr("fill", (d) => scoreColour(d.priority_score));

    enter.append("text")
      .attr("class", "asset-icon")
      .attr("font-size", `${ASSET_ICON_PX / view.k}px`)
      .text((d) => assetIcon(d.asset_type));

    enter.append("text")
      .attr("class", "asset-label")
      .attr("y", (ASSET_PIN_R + 10) / view.k)
      .attr("font-size", `${ASSET_LABEL_PX / view.k}px`)
      .attr("stroke-width", 3 / view.k)
      .text((d) => d.name);

    const merged = enter.merge(sel)
      .attr("transform", (d) => {
        const [px, py] = pinXY(d);
        return `translate(${px},${py})`;
      });
    merged.select(".asset-label")
      .classed("asset-label--hover", (d) => !labelled.has(d.id));
    // Raise pins so the highest-need ones draw on top of any neighbour.
    merged.sort((a, b) => (a.priority_score ?? 0) - (b.priority_score ?? 0));

    sel.exit().remove();
  }

  async function fetchDistrictVillages(district) {
    if (!district) return [];
    if (districtVillagesCache[district]) return districtVillagesCache[district];
    try {
      const data = await d3.json(`${API}/villages?district=${encodeURIComponent(district)}`);
      districtVillagesCache[district] = data || [];
      return districtVillagesCache[district];
    } catch (err) {
      console.error("Failed to fetch villages:", err);
      return [];
    }
  }

  function updateModeToggleUI() {
    const isAtDistrictOrBelow = ["district", "village", "asset"].includes(nav.level) || (nav.level === "cluster" && mode === "clusters");
    d3.select("#modeToggle").attr("hidden", isAtDistrictOrBelow ? null : true);
    d3.select("#btnModeVillages").classed("mode-toggle__btn--on", mode === "villages");
    d3.select("#btnModeClusters").classed("mode-toggle__btn--on", mode === "clusters");
  }

  function setMode(newMode) {
    if (mode === newMode) return;
    mode = newMode;
    updateModeToggleUI();
    if (["district", "village", "asset", "cluster"].includes(nav.level)) {
      navigate("district", { state: nav.state, district: nav.district });
    }
  }

  function drawReports(list) {
    const sel = gReports.selectAll("circle").data(list, (d, i) => i);
    const enter = sel.enter().append("circle")
      .attr("class", "report")
      .attr("r", 0);
    enter.transition().delay((d, i) => Math.min(i * 1.2, 500)).duration(320)
      .attr("r", 1.9 / view.k);
    // Repositioned on every draw, for the same reason as drawClusters.
    enter.merge(sel)
      .attr("cx", (d) => projection(d.geometry.coordinates)[0])
      .attr("cy", (d) => projection(d.geometry.coordinates)[1]);
    sel.exit().remove();
  }

  function drawLabels(feats, key) {
    const sel = gLabels.selectAll("text").data(feats, (d) => d.properties[key]);
    const enter = sel.enter().append("text")
      .attr("class", "label")
      .attr("text-anchor", "middle")
      .text((d) => d.properties[key])
      .attr("opacity", 0);
    enter.transition().duration(400).attr("opacity", 1);
    // Repositioned on every draw, for the same reason as drawClusters.
    enter.merge(sel)
      .attr("x", (d) => path.centroid(d)[0])
      .attr("y", (d) => path.centroid(d)[1]);
    sel.exit().remove();
  }

  /* ---------------------------------------------------------- navigation -- */

  async function navigate(level, opts = {}) {
    hideTip();

    // A resize is not a journey. Re-fitting after the rail collapses or the
    // dock opens lands on a new transform for the SAME place, and animating
    // between two deep-zoom transforms interpolates the translate linearly --
    // at scale 77 the view swoops through thousands of pixels of empty land
    // and the map appears to blank out before coming back. Instant is both
    // less jarring and more truthful: nothing actually moved.
    const dur = opts.instant ? 0 : 900;

    if (level === "india") {
      closePanel();
      Object.assign(nav, { level, state: null, district: null, cluster: null, village: null, asset: null });
      gDistricts.selectAll("path").remove();
      gReports.selectAll("circle").remove();
      gVillages.selectAll("circle").remove();
      clearAssets();
      drawOutline(null);
      view = fitTransform(india.statesMerged, 1.05);
      drawStates();
      drawClusters([]);
      drawLabels([], "st_nm");
      animateTo(view, dur);
      applyZoomCompensation(view.k);
      d3.select("#legendNote").text(
        "Maharashtra is the active pilot. Other states are shown for context only — " +
        "they carry no citizen data yet.");
    }

    if (level === "state") {
      closePanel();
      const targetState = opts.state || nav.state || PILOT_STATE;
      Object.assign(nav, { level, state: targetState, district: null, cluster: null, village: null, asset: null });
      gStates.selectAll("path").remove();
      gReports.selectAll("circle").remove();
      gVillages.selectAll("circle").remove();
      clearAssets();
      const feat = statesFC.features.find((f) => f.properties.st_nm === targetState);
      if (feat) {
        view = fitTransform(feat);
        drawDistricts(targetState);
        drawClusters(clusters);
        drawOutline(feat);
        drawLabels(
          districtsFC.features.filter(
            (d) => d.properties.st_nm === targetState &&
                   clusters.some((c) => c.district === d.properties.district)),
          "district");
        animateTo(view, dur);
        applyZoomCompensation(view.k);
      }
      d3.select("#legendNote").text(
        "Highlighted districts have citizen data. Bubbles are demand clusters, " +
        "sized by population affected.");
    }

    if (level === "district") {
      closePanel();
      const targetState = opts.state || nav.state || PILOT_STATE;
      const targetDistrict = opts.district || nav.district;
      Object.assign(nav, { level, state: targetState, district: targetDistrict, cluster: null, village: null, asset: null });
      gStates.selectAll("path").remove();
      clearAssets();
      const feat = districtsFC.features.find(
        (d) => d.properties.st_nm === targetState && d.properties.district === targetDistrict);
      if (feat) {
        view = fitTransform(feat);
        drawDistricts(targetState);
        drawOutline(feat);
        drawLabels([], "district");

        if (mode === "villages") {
          gClusters.selectAll("circle").remove();
          const vList = await fetchDistrictVillages(targetDistrict);
          drawVillages(vList);
          d3.select("#legendNote").text(
            "Bubbles are villages, sized by report count. Colour is the village's highest-need asset priority score.");
        } else {
          gVillages.selectAll("circle").remove();
          drawClusters(clusters.filter((c) => c.district === targetDistrict));
          d3.select("#legendNote").text(
            "Small dots are individual citizen reports. Bubbles are the clusters they " +
            "were grouped into — corroboration, not repetition.");
        }

        animateTo(view, dur);
        applyZoomCompensation(view.k);
      }

      // Individual report dots only belong to the category-cluster view. In
      // village mode each synthetic report now sits at its own school /
      // hospital / road spot, so drawing them made every village look like
      // three or four separate clusters.
      if (mode === "villages") {
        drawReports([]);
      } else {
        const pts = await ensureReports();
        drawReports(pts.filter((f) => f.properties.district === targetDistrict));
      }
    }

    if (level === "village") {
      const v = opts.village || nav.village;
      if (!v) return;
      const targetState = opts.state || nav.state || PILOT_STATE;
      const targetDistrict = opts.district || nav.district || v.district;
      Object.assign(nav, { level, state: targetState, district: targetDistrict, cluster: null, village: v, asset: null });
      gStates.selectAll("path").remove();
      gClusters.selectAll("circle").remove();
      drawReports([]);
      hideTip();

      const districtFeat = districtsFC.features.find(
        (d) => d.properties.st_nm === targetState && d.properties.district === targetDistrict);
      const base = districtFeat ? fitTransform(districtFeat).k : view.k;

      // Fetch the village's assets BEFORE choosing the zoom, so the view can
      // fit the village and every one of its pins in one move -- one click,
      // one zoom, everything on screen.
      let vData = null;
      try {
        vData = await d3.json(`${API}/villages/${v.gazetteer_id}`);
      } catch (err) {
        console.error("Could not fetch village detail:", err);
      }
      const pts = [[v.lon, v.lat], ...((vData && vData.assets) || []).map((a) => [a.lon, a.lat])];
      // Up to 40x the district fit: a village's own spots sit within ~1 km,
      // which needs that much zoom to separate. A far-off PHC widens the fit
      // automatically, because fitPoints only zooms as far as fits them all.
      view = fitPoints(pts, base * 3, base * 40) || pointTransform(v.lon, v.lat, base * 6);
      drawDistricts(targetState);
      drawOutline(districtFeat);

      const vList = await fetchDistrictVillages(targetDistrict);
      drawVillages(vList);
      gVillages.selectAll("circle")
        .classed("village-bubble--faded", (d) => d.gazetteer_id !== v.gazetteer_id)
        .classed("village-bubble--open", (d) => d.gazetteer_id === v.gazetteer_id);

      animateTo(view, dur);
      applyZoomCompensation(view.k);

      if (vData) {
        currentVillageData = vData;
        drawAssets(vData.assets || [], vData.name);
        // Back from an asset: nothing is selected any more.
        gAssets.selectAll(".asset-pin")
          .classed("asset-pin--faded", false)
          .classed("asset-pin--selected", false);
        gRoadLines.selectAll("path")
          .classed("road-line--faded", false)
          .classed("road-line--selected", false);
        applyZoomCompensation(view.k);
        renderVillagePanel(vData);
        d3.select("#legendNote").text(
          `${vData.name} — ${vData.report_count} citizen reports across ${(vData.assets || []).length} assets.`);
      }
    }

    if (level === "asset") {
      const a = opts.asset || nav.asset;
      if (!a) return;
      const targetState = opts.state || nav.state || PILOT_STATE;
      const targetDistrict = opts.district || nav.district || a.district;
      const parentVillage = opts.village || nav.village;
      Object.assign(nav, { level, state: targetState, district: targetDistrict, cluster: null, village: parentVillage, asset: a });
      gStates.selectAll("path").remove();
      gClusters.selectAll("circle").remove();

      // Highlight selected pin, fade others
      gAssets.selectAll(".asset-pin")
        .classed("asset-pin--faded", (d) => d.id !== a.id)
        .classed("asset-pin--selected", (d) => d.id === a.id);
      gRoadLines.selectAll("path")
        .classed("road-line--faded", (d) => d.id !== a.id)
        .classed("road-line--selected", (d) => d.id === a.id);

      let aData = null;
      try {
        aData = await d3.json(`${API}/assets/${a.id}`);
      } catch (err) {
        console.error("Could not fetch asset detail:", err);
      }
      if (aData) {
        currentAssetData = aData;
        renderAssetPanel(aData);
        d3.select("#legendNote").text(
          `${aData.name} (${aData.asset_type}) — score ${(aData.priority_score ?? 0).toFixed(2)}, rank #${aData.rank_in_village} in ${aData.village}.`);
      }
    }

    if (level === "cluster") {
      const c = opts.cluster || nav.cluster;
      if (!c) return;
      const targetState = opts.state || nav.state || c.state || PILOT_STATE;
      const targetDistrict = opts.district || nav.district || c.district;
      Object.assign(nav, { level, state: targetState, district: targetDistrict, cluster: c, village: null, asset: null });
      gStates.selectAll("path").remove();
      gVillages.selectAll("circle").remove();
      clearAssets();

      const districtFeat = districtsFC.features.find(
        (d) => d.properties.st_nm === targetState &&
               d.properties.district === targetDistrict);
      const base = districtFeat ? fitTransform(districtFeat).k : view.k;
      view = pointTransform(c.centroid_lon, c.centroid_lat, base * 3.2);
      drawDistricts(targetState);
      drawOutline(districtFeat);
      drawClusters(clusters.filter((x) => x.district === targetDistrict));
      gClusters.selectAll("circle").classed("cluster--faded", (d) => d.id !== c.id);
      animateTo(view, dur);
      applyZoomCompensation(view.k);
      renderPanel(c);
      d3.select("#legendNote").text(
        `Cluster ${c.id} — ${c.report_count} reports from ${c.unique_reporters} ` +
        `distinct reporters, affecting ~${fmt(c.population_affected || 0)} people.`);
    }

    if (level !== "cluster") gClusters.selectAll("circle").classed("cluster--faded", false);
    if (level !== "village" && level !== "asset") {
      gVillages.selectAll("circle")
        .classed("village-bubble--faded", false)
        .classed("village-bubble--open", false);
      clearAssets();
    }

    updateModeToggleUI();
    renderBreadcrumb();
    renderStatus();
    (window.AwaazIQ._navFns || []).forEach((fn) => fn(nav));
  }

  async function ensureReports() {
    if (reports) return reports;
    try {
      const fc = await d3.json(`${API}/map-data?layer=reports`);
      reports = fc.features || [];
    } catch {
      reports = [];      // reports are enrichment, not load-bearing
    }
    return reports;
  }

  /* --------------------------------------------------------------- boot -- */

  function sizeToStage() {
    const r = stage.getBoundingClientRect();
    width = r.width;
    const dock = document.getElementById("dock");
    const docked = dock && dock.classList.contains("dock--open")
      ? dock.getBoundingClientRect().height : 0;
    height = Math.max(160, r.height - docked);
    svg.attr("width", width).attr("height", height)
       .style("height", `${height}px`)
       .attr("viewBox", `0 0 ${width} ${height}`);
  }

  /** Re-project and redraw for the current stage size. */
  function refit() {
    if (!india) return;
    sizeToStage();
    projection.fitExtent([[12, 12], [width - 12, height - 12]], india.statesMerged);
    gStates.selectAll("path").attr("d", path);
    gDistricts.selectAll("path").attr("d", path);
    gOutline.selectAll("path").attr("d", path);
    navigate(nav.level, {
      state: nav.state, district: nav.district, cluster: nav.cluster,
      village: nav.village, asset: nav.asset,
      instant: true,
    });
  }

  async function boot() {
    sizeToStage();

    let topo;
    try {
      topo = await d3.json(TOPO_URL);
    } catch (err) {
      return fail(`Couldn't load ${TOPO_URL}. Serve this folder over HTTP rather than ` +
                  `opening the file directly — browsers block local file reads.`);
    }

    statesFC = topojson.feature(topo, topo.objects.states);
    districtsFC = topojson.feature(topo, topo.objects.districts);
    india = { statesMerged: { type: "FeatureCollection", features: statesFC.features } };

    projection.fitExtent([[12, 12], [width - 12, height - 12]], india.statesMerged);

    try {
      clusters = await d3.json(`${API}/clusters`);
    } catch (err) {
      return fail(`No response from <code>${API}</code>.`);
    }
    clusters = (clusters || []).filter((c) => c.centroid_lat != null && c.centroid_lon != null);

    if (!clusters.length) {
      return fail("The API answered but returned no clusters. Run " +
                  "<code>python -m intelligence.recompute</code> from the repo root.");
    }

    const pops = clusters.map((c) => c.population_affected || 0);
    radiusScale = d3.scaleSqrt().domain([0, d3.max(pops) || 1]).range([3, 26]);

    const scores = clusters.map((c) => c.priority_score).filter((s) => s != null);
    colourScale = d3.scaleLinear()
      .domain([d3.min(scores), d3.quantile(scores.slice().sort(d3.ascending), 0.6), d3.max(scores)])
      .range(["#dfe3ea", "#e2c98d", "#c0781e"])
      .clamp(true);

    const districtCount = new Set(clusters.map((c) => c.district)).size;
    document.getElementById("coverageText").textContent =
      `Pilot coverage — ${districtCount} districts · ${clusters.length} clusters`;

    // Mode toggle buttons
    const btnVillages = document.getElementById("btnModeVillages");
    const btnClusters = document.getElementById("btnModeClusters");
    if (btnVillages) btnVillages.addEventListener("click", () => setMode("villages"));
    if (btnClusters) btnClusters.addEventListener("click", () => setMode("clusters"));

    // Deep link, so a view can be shared: ?at=state, ?at=district:Kolhapur,
    // ?at=cluster:12, ?at=village:<id>, ?at=asset:<id>
    const at = params.get("at");
    if (at && at.startsWith("district:")) {
      const name = at.slice(9);
      const known = clusters.some((c) => c.district === name);
      await navigate(known ? "district" : "india",
                     { state: PILOT_STATE, district: name });
    } else if (at && at.startsWith("cluster:")) {
      setMode("clusters");
      const target = clusters.find((c) => String(c.id) === at.slice(8));
      if (target) {
        await navigate("district", { state: PILOT_STATE, district: target.district });
        await navigate("cluster", { cluster: target });
      } else {
        await navigate("india");
      }
    } else if (at && at.startsWith("village:")) {
      setMode("villages");
      const gid = at.slice(8);
      try {
        const v = await d3.json(`${API}/villages/${gid}`);
        if (v && v.district) {
          await navigate("district", { state: PILOT_STATE, district: v.district });
          await navigate("village", { village: v });
        } else {
          await navigate("india");
        }
      } catch {
        await navigate("india");
      }
    } else if (at && at.startsWith("asset:")) {
      setMode("villages");
      const aid = at.slice(6);
      try {
        const a = await d3.json(`${API}/assets/${aid}`);
        if (a && a.district) {
          await navigate("district", { state: PILOT_STATE, district: a.district });
          let v = null;
          if (a.primary_gazetteer_id) {
            v = await d3.json(`${API}/villages/${a.primary_gazetteer_id}`);
          }
          await navigate("village", { village: v || { name: a.village, district: a.district, block: a.block, gazetteer_id: a.primary_gazetteer_id } });
          await navigate("asset", { asset: a, village: v });
        } else {
          await navigate("india");
        }
      } catch {
        await navigate("india");
      }
    } else if (at === "state") {
      await navigate("state", { state: PILOT_STATE });
    } else {
      await navigate("india");
    }

    const loader = document.getElementById("loader");
    loader.classList.add("loader--out");
    setTimeout(() => (loader.hidden = true), 450);

    window.AwaazIQ._fireReady();
  }

  function fail(msg) {
    document.getElementById("loader").hidden = true;
    document.getElementById("errorMsg").innerHTML = msg;
    document.getElementById("error").hidden = false;
  }

  window.addEventListener("error", (e) =>
    fail(`<b>${e.message}</b><br><small>${e.filename || ""}:${e.lineno || "?"}</small>`));
  window.addEventListener("unhandledrejection", (e) =>
    fail(`<b>${(e.reason && e.reason.message) || e.reason}</b>`));

  let resizeTimer = null;
  window.addEventListener("resize", () => {
    if (!india) return;
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(refit, 180);
  });

  const railBtn = document.getElementById("railToggle");
  if (railBtn) {
    railBtn.addEventListener("click", () => {
      const app = document.querySelector(".app");
      const closed = app.classList.toggle("app--railclosed");
      railBtn.setAttribute("aria-expanded", String(!closed));
      railBtn.title = closed ? "Show the priority ranking" : "Hide the priority ranking";
      setTimeout(refit, 340);
    });
  }

  /* --------------------------------------------------------------- api ---- */

  window.AwaazIQ = {
    navigate,
    nav,
    TERMS,
    fmt,
    scoreColour,
    getClusters: () => clusters,
    getMode: () => mode,
    setMode,
    getDistrictVillages: (district) => districtVillagesCache[district] || [],
    fetchDistrictVillages,
    getApi: () => API,
    rankOf,
    onNavigate(fn) {
      this._navFns = (this._navFns || []).concat(fn);
    },
    onReady(fn) {
      if (this._ready) fn();
      else this._readyFns = (this._readyFns || []).concat(fn);
    },
    _fireReady() {
      this._ready = true;
      (this._readyFns || []).forEach((f) => f());
    },
  };

  boot();
})();
