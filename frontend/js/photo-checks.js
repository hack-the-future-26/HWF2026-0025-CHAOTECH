/* ===========================================================================
   AwaazIQ — photo check rendering (Workstream C)

   One renderer for every place a photo check is shown: the citizen receipt,
   the officials' asset panel and the Photo Lab. Plain DOM, no d3, so the lab
   page does not need it.

   Input is the JSON the API returns for a check (routes_photo_checks):
   capture (C1), duplicate (C3), edit_detection (C4), defects (C5),
   damage (C6), screen_replay (C7), summary.
   =========================================================================== */

(function () {
  "use strict";

  const VERDICT = {
    verified: ["✓", "Verified photo"],
    partly_verified: ["◐", "Partly verified"],
    needs_review: ["!", "Needs officer review"],
    not_checked: ["–", "Not checked"],
  };

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v == null || v === false) return;
      if (k === "class") n.className = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    });
    kids.flat().forEach((c) => {
      if (c == null || c === false) return;
      n.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return n;
  }

  const pct = (x) => `${Math.round((x || 0) * 100)}%`;
  const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));

  function trustColour(v) {
    if (v == null) return "var(--muted)";
    if (v >= 0.85) return "var(--green)";
    if (v >= 0.6) return "var(--amber)";
    return "var(--red)";
  }

  function lightbox(src) {
    const box = h("div", { class: "plight", onclick: () => box.remove() }, h("img", { src, alt: "Photo, enlarged" }));
    document.addEventListener("keydown", function esc(e) {
      if (e.key === "Escape") { box.remove(); document.removeEventListener("keydown", esc); }
    });
    document.body.appendChild(box);
  }

  /* ------------------------------------------------ one row per check -- */

  function rows(check) {
    const cap = check.capture || {};
    const flags = new Set((check.summary || {}).flags || []);
    const out = [];

    // C1 — live capture
    if (cap.method === "live_camera") {
      const when = cap.captured_at ? new Date(cap.captured_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : "unknown time";
      const age = cap.minutes_before_upload != null ? ` · ${Math.max(0, cap.minutes_before_upload).toFixed(cap.minutes_before_upload < 10 ? 1 : 0)} min before sending` : "";
      out.push([flags.has("capture_time_mismatch") ? "warn" : "pass", "Live capture", "C1",
        `Taken with the in-app camera at ${when}${age}${flags.has("capture_time_mismatch") ? " — timing does not match the upload" : ""}`]);
    } else {
      out.push(["warn", "Live capture", "C1", "Uploaded as a file, not taken with the in-app camera"]);
    }

    // C1 — location at capture
    if (cap.distance_to_village_km != null) {
      const far = flags.has("captured_far_from_village");
      const acc = cap.accuracy_m != null ? ` (GPS ±${Math.round(cap.accuracy_m)} m)` : "";
      out.push([far ? "fail" : "pass", "Taken here", "C1",
        `${far ? "Captured" : "Captured"} ${cap.distance_to_village_km.toFixed(2)} km from the chosen village${acc}${far ? " — beyond the 5 km limit" : ""}`]);
    } else if (cap.gps_source) {
      out.push(["info", "Taken here", "C1", `GPS recorded (${cap.gps_source === "exif_gps" ? "photo metadata" : "at capture"}); no village to compare with`]);
    } else {
      out.push(["warn", "Taken here", "C1", "No GPS reading at the moment of capture"]);
    }

    // C3 — duplicate
    const dup = check.duplicate;
    if (dup) {
      const reused = flags.has("reused_photo");
      out.push([reused ? "fail" : "warn", "Original photo", "C3",
        reused
          ? `Same photo already used on report #${dup.request_id}${dup.same_village ? "" : " in another village"}${dup.same_reporter ? "" : " by another account"}`
          : `Resubmitted: same photo as your report #${dup.request_id}`]);
    } else {
      out.push(["pass", "Original photo", "C3", "Not a copy of any photo already on file (perceptual hash)"]);
    }

    // C4 — edits
    const ela = check.edit_detection || {};
    const sw = (cap.exif || {}).software;
    if (flags.has("editing_software")) {
      out.push(["fail", "Not edited", "C4", `Image metadata names editing software: “${esc(sw)}”`]);
    } else {
      const hint = ela.score != null ? ` · error-level hint ${ela.score.toFixed(2)}${flags.has("possibly_edited") ? " (one region differs — advisory)" : ""}` : "";
      out.push([flags.has("possibly_edited") ? "info" : "pass", "Not edited", "C4", `No editing software in metadata${hint}`]);
    }

    // C7 — screen replay
    const sr = check.screen_replay || {};
    const burst = sr.burst || {};
    const moire = sr.moire || {};
    if (flags.has("static_burst")) {
      out.push(["fail", "Real scene", "C7", `All ${burst.frames} camera frames are identical — no sensor noise, likely a still image fed to the camera`]);
    } else if (flags.has("screen_replay_suspected")) {
      out.push(["fail", "Real scene", "C7", `Moiré pattern found (score ${(moire.score || 0).toFixed(2)}) — looks like a photo of a screen`]);
    } else {
      const b = burst.frames >= 2 ? ` · ${burst.frames} burst frames with natural sensor noise` : "";
      out.push(["pass", "Real scene", "C7", `No moiré pattern from a screen${b}`]);
    }

    // C5 — model. The detector is road-domain-trained, and summarise()
    // (photo_checks.py) only fills in `summary.support` when the report's
    // own category is "road" -- null/undefined here means this report was
    // never about a road, so "found nothing" would be a meaningless result,
    // not a clean check.
    const d = check.defects || {};
    const support = (check.summary || {}).support;
    const roadApplicable = support !== null && support !== undefined;
    if (!d.available) {
      out.push(["info", "Road damage", "C5", "Detection model not available on this server"]);
    } else if (d.defect_seen) {
      const parts = [];
      if (d.potholes) parts.push(`${d.potholes} pothole${d.potholes > 1 ? "s" : ""} (${pct(d.pothole_confidence)})`);
      if (d.cracks) parts.push(`${d.cracks} crack${d.cracks > 1 ? "s" : ""} (${pct(d.crack_confidence)})`);
      out.push(["pass", "Road damage", "C5", `AI model detected ${parts.join(" and ")}, covering ${d.damage_area_pct}% of the photo`]);
    } else if (!roadApplicable) {
      out.push(["info", "Road damage", "C5", "Not applicable — this report isn't about a road"]);
    } else {
      out.push([flags.has("no_road_damage_seen") ? "warn" : "info", "Road damage", "C5", "No pothole or crack detected by the AI model"]);
    }

    // C6 — damage grade. `emergency_candidate` means the check found real
    // building/bridge damage worth an officer's attention -- the opposite of
    // a failed/suspect check, so it gets its own "hit" state rather than the
    // same ✕ used for authenticity problems (a real find was being read as
    // "nothing detected" because it looked identical to a failure).
    const g = check.damage || {};
    if (g.emergency_candidate) {
      const src = [g.photo_grade && "photo", g.wording_grade && "description"].filter(Boolean).join(" + ");
      out.push(["hit", "Structure damage", "C6",
        `${g.structure === "bridge" ? "Bridge" : "Building"} damage <b>detected</b> — grade "${g.grade}" (from ${src}), confidence ${pct(g.confidence)}. Emergency candidate, needs officer verification.`]);
    } else if (g.grade) {
      out.push(["info", "Structure damage", "C6", `Damage grade “${g.grade}”, but no bridge or building named — road condition, not an emergency`]);
    } else {
      out.push(["info", "Structure damage", "C6", "No bridge or building damage indicated"]);
    }
    return out;
  }

  function checkList(check) {
    const icons = { pass: "✓", warn: "!", fail: "✕", info: "i", hit: "⚠" };
    return h("div", { class: "checks" },
      rows(check).map(([state, name, code, detail]) =>
        h("div", { class: `chk chk--${state}` },
          h("span", { class: "chk__icon" }, icons[state]),
          h("span", { class: "chk__name" }, name, h("small", {}, code)),
          h("span", { class: "chk__detail", html: detail }))));
  }

  function aiBox(check) {
    const d = check.defects || {};
    const g = check.damage || {};
    const support = (check.summary || {}).support;
    const roadApplicable = support !== null && support !== undefined;

    // A real structure hit leads -- it is the strongest, most actionable
    // signal on the card, and this box should never claim "no damage" while
    // C6 is showing a genuine building/bridge finding right below it.
    if (g.emergency_candidate) {
      return h("div", { class: "ai ai--hit" },
        h("div", { class: "ai__num", html: `${Math.round((g.confidence || 0) * 100)}<small>%</small>` }),
        h("div", { class: "ai__txt", html:
          `<b>${g.structure === "bridge" ? "Bridge" : "Building"} damage detected</b> (grade “${g.grade}”) by the vision model. ` +
          `Flagged as an emergency candidate for officer review.` }));
    }
    if (!d.available) return null;
    // Nothing to report and this was never a road photo -- showing "0, no
    // road damage" here would read as a finding when the check simply does
    // not apply, so the box is omitted rather than left misleading.
    if (!d.defect_seen && !roadApplicable) return null;
    const best = Math.max(d.pothole_confidence || 0, d.crack_confidence || 0);
    const label = (d.pothole_confidence || 0) >= (d.crack_confidence || 0) ? "pothole" : "crack";
    return h("div", { class: `ai${d.defect_seen ? " ai--hit" : ""}` },
      h("div", { class: "ai__num", html: d.defect_seen ? `${Math.round(best * 100)}<small>%</small>` : "0" }),
      h("div", { class: "ai__txt", html: d.defect_seen
        ? `<b>${label[0].toUpperCase() + label.slice(1)} detected</b> by the team's YOLOv8 model. ` +
          `Boxes on the photo show where.`
        : `<b>No road damage detected.</b> The model found nothing above its ${pct(d.threshold)} confidence threshold.` }));
  }

  /* ---------------------------------------------------------- the card -- */

  /**
   * render(check, {api, annotatedSrc, elaSrc, compact})
   * `api` prefixes the relative image URLs the backend returns.
   */
  function render(check, opts) {
    opts = opts || {};
    const api = opts.api || "";
    const s = check.summary || {};
    const verdict = s.verdict || "not_checked";
    const [vIcon, vText] = VERDICT[verdict] || VERDICT.not_checked;

    const photo = check.photo_url ? api + check.photo_url : null;
    const annotated = opts.annotatedSrc || (check.annotated_url ? api + check.annotated_url : null) || check.annotated_image || photo;
    const ela = opts.elaSrc || check.ela_image || (check.ela_url ? api + check.ela_url : null);

    const img = h("img", { class: "pcard__img", src: annotated, alt: "Checked photo with detected damage boxed", loading: "lazy" });
    img.addEventListener("click", () => lightbox(img.src));

    if (opts.compact) {
      const flags = (s.flags || []).filter((f) => f !== "no_capture_gps");
      const d = check.defects || {};
      return h("div", { class: "pcard pcard--compact" },
        h("div", { class: "pcard__media" }, img),
        h("div", { class: "pcard__body" },
          h("div", { class: "pcard__top" },
            h("span", { class: `verdict verdict--${verdict}` }, vIcon, " ", vText),
            h("span", { class: "pmini__line" }, "report #", String(check.request_id || "—"))),
          h("div", { class: "pmini__line", html:
            (d.defect_seen
              ? `<b>AI: ${d.potholes ? "pothole" : "crack"} ${pct(Math.max(d.pothole_confidence, d.crack_confidence))}</b>`
              : "<b>AI: no damage seen</b>") +
            ` · authenticity <b>${s.authenticity != null ? pct(s.authenticity) : "—"}</b>` +
            ` · ${(check.capture || {}).method === "live_camera" ? "live capture" : "file upload"}` }),
          flags.length ? h("div", { class: "pmini__flags" },
            flags.map((f) => h("span", { class: `pflag${(s.review_flags || []).includes(f) ? "" : " pflag--soft"}` }, (s.flag_text || {})[f] || f))) : null));
    }

    const media = h("div", { class: "pcard__media" }, img);
    if (ela && annotated) {
      const tabs = h("div", { class: "pcard__imgtabs" });
      const mk = (label, src, on) => {
        const b = h("button", { type: "button", class: `pcard__tab${on ? " pcard__tab--on" : ""}` }, label);
        b.addEventListener("click", () => {
          img.src = src;
          tabs.querySelectorAll(".pcard__tab").forEach((t) => t.classList.remove("pcard__tab--on"));
          b.classList.add("pcard__tab--on");
        });
        return b;
      };
      tabs.append(mk("AI boxes", annotated, true));
      if (photo && photo !== annotated) tabs.append(mk("Original", photo, false));
      tabs.append(mk("Error level (C4)", ela, false));
      media.append(tabs);
    }

    const auth = s.authenticity;
    const body = h("div", { class: "pcard__body" },
      h("div", { class: "pcard__top" },
        h("span", { class: `verdict verdict--${verdict}` }, vIcon, " ", vText),
        h("div", { class: "trust" },
          h("div", { class: "trust__row" }, h("span", {}, "Photo authenticity"), h("b", {}, auth != null ? pct(auth) : "—")),
          h("div", { class: "trust__bar" }, h("div", { class: "trust__fill", style: `width:${Math.round((auth || 0) * 100)}%;background:${trustColour(auth)}` })))),
      aiBox(check),
      check.error ? h("div", { class: "pcard__note" }, check.error) : checkList(check),
      h("div", { class: "pcard__note" },
        "Photo checks never reject a complaint. They raise or lower confidence and flag doubtful photos for an officer."));

    return h("div", { class: "pcard" }, media, body);
  }

  /** Officer-panel summary from the asset's aggregated evidence keys. */
  function renderAssetSummary(evidence) {
    const pv = (evidence || {}).photo_verification;
    const pd = (evidence || {}).photo_defect;
    const em = (evidence || {}).emergency_damage;
    if (!pv) return null;
    const wrap = h("div", {});
    if (em) {
      wrap.append(h("div", { class: "pemerg", html:
        `<b>&#9888; Possible ${esc(em.structure)} damage — ${esc(em.grade)}.</b> ` +
        `Confidence ${pct(em.confidence)} from photo and description checks (C6). ` +
        `Verify on site before treating as an emergency.` }));
    }
    const cell = (v, l) => h("div", { class: "pstrip__cell" }, h("div", { class: "pstrip__v" }, String(v)), h("div", { class: "pstrip__l" }, l));
    wrap.append(h("div", { class: "pstrip" },
      cell(`${pv.verified}/${pv.photos}`, "photos verified"),
      cell(pd ? `${pd.trusted_photos_showing_defect}` : "0", "trusted photos showing damage"),
      cell(pv.needs_review, "need review")));
    return wrap;
  }

  window.PhotoChecks = { render, renderAssetSummary, lightbox };
})();
