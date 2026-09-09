/* ===========================================================================
   AwaazIQ — citizen complaint page

   The citizen half of the two-role split. This page can do exactly two
   things: file a report, and look up what happened to one.

   What it deliberately CANNOT do is run clustering. Re-scoring is a
   whole-dataset operation that reorders every district's worklist, and it is
   an official's decision, not a side effect of one villager pressing a
   button. So the citizen gets a tracking view that reports the truth at the
   moment they ask -- including "not yet grouped" -- and the officials'
   dashboard owns the RUN CLUSTERING control.

   The field set matches what CPGRAMS demands of a citizen (name, gender,
   mobile, e-mail, address, pincode, state/district, department, evidence),
   with two deliberate differences:

     - Identity is asked for ONCE, at registration, and never again. CPGRAMS
       makes a citizen restate their name, address and mobile on every
       grievance; here the account carries them and this form posts none of
       them. That is not only less typing: the same person retyping their
       name two ways would be recorded as two reporters, and the demand term
       would read that as corroboration.
     - The place is chosen from the gazetteer, one level deeper than CPGRAMS
       goes, down to the village. Every option offered is a village the
       pipeline can already geocode, so a complaint cannot be stranded by a
       location nobody could match.
   =========================================================================== */

(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || "http://127.0.0.1:8001").replace(/\/$/, "");

  // Carry ?api= across to the dashboard, so pointing this page at a
  // non-default backend doesn't silently send officials to another one.
  if (params.get("api")) {
    document.getElementById("adminLink").href =
      `index.html?api=${encodeURIComponent(API)}`;
  }

  const SAMPLES = [
    "hamare gaon mein sadak bahut kharab hai",
    "पाणी पुरवठा बंद आहे, तीन दिवस झाले",
    "अस्पताल बहुत दूर है, जाना मुश्किल है",
    "school mein shikshak nahi hai",
  ];

  // Each stage is something the backend actually does in process_report, and
  // is marked done only when the response proves that field came back. A
  // stage that could not resolve says so rather than showing a tick.
  const STEPS = [
    ["Reading the language and script", (r) => r.language_detected],
    ["Normalising the text", () => true],
    ["Working out the problem and how urgent it is", (r) => r.issue_category],
    ["Placing it on the map", (r) => r.village],
    ["Routing it to a department", (r) => r.department],
    ["Removing phone numbers and names from the description", () => true],
  ];

  const MAX_FILES = 5;
  const MAX_BYTES = 4 * 1024 * 1024;

  const el = (id) => document.getElementById(id);
  const val = (id) => el(id).value.trim();
  const show = (id, on) => { el(id).hidden = !on; };

  let departments = {};          // category -> [{id, name, note}]
  let chosenFiles = [];

  /* ------------------------------------------------------------- my ids -- */

  // Per-viewer convenience only: the report numbers this browser has filed,
  // so someone can check back without writing the number down. It never
  // leaves the device.
  const STORE = "awaaziq.myreports";

  function myReports() {
    try {
      const raw = localStorage.getItem(STORE);
      return raw ? JSON.parse(raw) : [];
    } catch { return []; }
  }
  function rememberReport(id) {
    try {
      const list = myReports().filter((x) => x !== id);
      list.unshift(id);
      localStorage.setItem(STORE, JSON.stringify(list.slice(0, 8)));
    } catch { /* private mode, blocked storage -- the page still works */ }
  }
  function renderMine() {
    const box = d3.select("#mine");
    box.selectAll("*").remove();
    const list = myReports();
    if (!list.length) return;
    box.append("span")
      .style("font-size", "11px").style("color", "var(--muted)")
      .style("align-self", "center").style("margin-right", "3px")
      .text("From this device:");
    list.forEach((id) => {
      box.append("button").attr("type", "button").attr("class", "mine__chip")
        .text(`#${id}`)
        .on("click", () => { el("trackId").value = id; track(id); });
    });
  }

  /* ------------------------------------------------------ location tree -- */

  function fill(select, items, placeholder) {
    select.innerHTML = "";
    const first = document.createElement("option");
    first.value = "";
    first.textContent = placeholder;
    select.appendChild(first);
    items.forEach((it) => {
      const o = document.createElement("option");
      o.value = it.name;
      o.textContent = it.name;
      select.appendChild(o);
    });
  }

  async function getJSON(path) {
    const res = await fetch(`${API}${path}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return res.json();
  }

  async function loadDistricts() {
    try {
      const rows = await getJSON("/gazetteer/districts");
      fill(el("district"), rows, "Select district…");

      // Say the boundary out loud rather than let a citizen discover it by
      // finding only two options in an otherwise-normal-looking dropdown --
      // that read as a loading glitch, not as "this area isn't covered yet",
      // which is what actually happened before this note existed. Built
      // from the live list so it can never name a district we don't have.
      const names = rows.map((r) => r.name);
      el("coverageDistricts").textContent =
        names.length > 1
          ? `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`
          : (names[0] || "no districts yet");
      el("coverageNote").hidden = false;
    } catch {
      fill(el("district"), [], "Could not load districts — is the API running?");
    }
  }

  async function loadBlocks(district) {
    const block = el("block"), village = el("village");
    block.disabled = true; village.disabled = true;
    fill(village, [], "Select a block first");
    if (!district) { fill(block, [], "Select a district first"); return; }
    fill(block, [], "Loading…");
    const rows = await getJSON(`/gazetteer/blocks?district=${encodeURIComponent(district)}`);
    fill(block, rows, "Select block…");
    block.disabled = false;
  }

  async function loadVillages(district, block) {
    const village = el("village");
    village.disabled = true;
    if (!block) { fill(village, [], "Select a block first"); return; }
    fill(village, [], "Loading…");
    const rows = await getJSON(
      `/gazetteer/villages?district=${encodeURIComponent(district)}` +
      `&block=${encodeURIComponent(block)}`);
    fill(village, rows, `Select village… (${rows.length})`);
    village.disabled = false;
  }

  async function loadDepartments() {
    try { departments = await getJSON("/gazetteer/departments"); }
    catch { departments = {}; }
  }

  function fillDepartments(category) {
    const dept = el("department");
    const list = departments[category] || [];
    dept.innerHTML = "";
    const first = document.createElement("option");
    first.value = "";
    first.textContent = list.length ? "Select department…" : "Select a problem type first";
    dept.appendChild(first);
    list.forEach((d) => {
      const o = document.createElement("option");
      o.value = d.id;
      o.textContent = d.name;
      o.dataset.note = d.note || "";
      dept.appendChild(o);
    });
    dept.disabled = !list.length;
    el("deptHint").textContent = "The office that holds the budget for this";
  }

  /* --------------------------------------------------------------- files -- */

  function renderFiles() {
    const box = d3.select("#fileList");
    box.selectAll("*").remove();
    chosenFiles.forEach((f, i) => {
      const row = box.append("div").attr("class", "file");
      row.append("span").attr("class", "file__name").text(f.name);
      row.append("span").attr("class", "file__size")
        .text(`${(f.size / 1024).toFixed(0)} KB`);
      row.append("button").attr("type", "button").attr("class", "file__x")
        .attr("aria-label", `Remove ${f.name}`).html("&times;")
        .on("click", () => { chosenFiles.splice(i, 1); renderFiles(); });
    });
    el("attachLabel").textContent = chosenFiles.length
      ? `📎 ${chosenFiles.length} of ${MAX_FILES} chosen — add more`
      : "📎 Choose photos or PDFs";
  }

  /* ---------------------------------------------------------- validation -- */

  // Mirrors the server's rules so a citizen is told what is wrong before a
  // round-trip. The server still enforces all of it -- this is a courtesy,
  // never the gate.
  function collect() {
    const errors = [];
    const need = (id, label) => {
      const v = val(id);
      if (!v) errors.push([id, `${label} is required`]);
      return v;
    };

    // Identity is deliberately absent. It is read off the signed-in account
    // by the server, so there is nothing here to validate and nothing a
    // crafted request could override.
    const data = {
      district: need("district", "District"),
      block: need("block", "Block"),
      village: need("village", "Village"),
      department: need("department", "Department"),
      text: val("text"),
    };

    if (!session) {
      errors.push(["text", "Sign in or create an account to file a grievance"]);
    }
    if (!val("category")) errors.push(["category", "Choose the type of problem"]);
    if (!data.text && !el("audio").files[0]) {
      errors.push(["text", "Describe the problem, or upload an audio recording"]);
    }

    return { data, errors };
  }

  function showErrors(errors) {
    document.querySelectorAll(".f--bad").forEach((n) => n.classList.remove("f--bad"));
    const box = d3.select("#errors");
    box.node().hidden = !errors.length;
    box.html("");
    if (!errors.length) return;

    box.append("div").attr("class", "errors__head")
      .text(errors.length === 1
        ? "One thing needs fixing:"
        : `${errors.length} things need fixing:`);
    const ul = box.append("ul");
    errors.forEach(([id, msg]) => {
      ul.append("li").text(msg);
      const field = el(id);
      if (field && field.closest(".f")) field.closest(".f").classList.add("f--bad");
    });

    const firstField = el(errors[0][0]);
    if (firstField) {
      firstField.scrollIntoView({ behavior: "smooth", block: "center" });
      firstField.focus({ preventScroll: true });
    }
  }

  /* -------------------------------------------------------------- steps -- */

  function renderSteps(result) {
    const box = d3.select("#steps");
    box.selectAll("*").remove();
    STEPS.forEach(([label, check]) => {
      const done = result ? !!check(result) : false;
      const row = box.append("div")
        .attr("class", `step${result ? (done ? " step--done" : "") : " step--on"}`);
      row.append("div").attr("class", "step__dot");
      row.append("div").text(label);
      if (result) {
        row.append("div").attr("class", "step__note")
          .text(done ? "done" : "could not resolve");
      }
    });
  }

  /* ------------------------------------------------------------- submit -- */

  async function submit(event) {
    event.preventDefault();
    const { data, errors } = collect();
    if (errors.length) { showErrors(errors); return; }
    showErrors([]);

    // Kept client-side: the type the citizen picked, so the receipt can say
    // when the pipeline read the description differently. The server does
    // not need it -- routing already travels as `department`.
    const declared = val("category");

    el("submit").disabled = true;
    show("form", false);
    show("runCard", true);
    renderSteps(null);
    el("runCard").scrollIntoView({ behavior: "smooth", block: "center" });

    const fd = new FormData();
    Object.entries(data).forEach(([k, v]) => fd.append(k, v));
    const audio = el("audio").files[0];
    if (audio) fd.append("file", audio);
    chosenFiles.forEach((f) => fd.append("attachments", f));

    let saved;
    try {
      const res = await fetch(`${API}/citizen-report`, {
        method: "POST",
        headers: authHeaders(),
        body: fd,
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      saved = await res.json();
    } catch (err) {
      show("runCard", false);
      show("form", true);
      el("submit").disabled = false;
      showErrors([["full_name", `Could not send the report: ${err.message}`]]);
      return;
    }

    renderSteps(saved);
    rememberReport(saved.id);
    renderMine();
    setTimeout(() => {
      show("runCard", false);
      showReceipt(saved, { declared });
    }, 800);
  }

  function departmentName(id) {
    for (const list of Object.values(departments)) {
      const hit = list.find((d) => d.id === id);
      if (hit) return hit.name;
    }
    return id || "—";
  }

  function showReceipt(saved, sent) {
    const box = d3.select("#doneCard");
    box.node().hidden = false;
    box.html("");

    const rc = box.append("div").attr("class", "receipt");
    rc.append("div").attr("class", "receipt__num").text(`#${saved.id}`);
    rc.append("div").attr("class", "receipt__label")
      .html("Your report has been registered.<br>Keep this number to check on it later.");

    box.append("div").attr("class", "eyebrow").style("margin-bottom", "9px")
      .text("What we understood");

    const files = saved.attachments || [];
    const rows = [
      ["Language", saved.language_detected || "—"],
      ["Problem", saved.issue_category || "not recognised"],
      ["Urgency", saved.severity || "—"],
      ["Place", saved.village
        ? `${saved.village}, ${saved.block}, ${saved.district}` : "—"],
      ["Routed to", departmentName(saved.department)],
      ["Evidence", files.length
        ? `${files.length} file${files.length > 1 ? "s" : ""} received`
        : "none attached"],
      ["Stored as", saved.raw_text || ""],
    ];
    const kv = box.append("div").attr("class", "kv");
    rows.forEach(([k, v]) => {
      kv.append("div").attr("class", "kv__k").text(k);
      kv.append("div").attr("class", "kv__v").text(v);
    });

    // The citizen chose a problem type; the pipeline classified the
    // description independently. When the two disagree, say so instead of
    // quietly overriding one with the other. Routing follows what the
    // citizen chose -- they know which office they want -- while grouping
    // follows what the description actually says, because that is what gets
    // compared against other people's descriptions.
    if (sent.declared && saved.issue_category &&
      saved.issue_category !== sent.declared) {
      box.append("div").attr("class", "result__banner result__banner--warn")
        .html(`You chose <b>${sent.declared}</b>, but the description reads as ` +
          `<b>${saved.issue_category}</b>. It still goes to the department ` +
          `you picked; the grouping follows the description.`);
    }

    const next = box.append("div").attr("class", "next");
    next.append("div").attr("class", "next__title").text("What happens now");
    const ol = next.append("ol");
    [
      "Your report goes to <b>" + departmentName(saved.department) + "</b>.",
      "It waits to be <b>matched with others</b> nearby about the same problem. " +
      "One report on its own is not yet enough — that is how the system avoids " +
      "acting on a single voice.",
      "When officials run the analysis, matching reports are grouped into one " +
      "<b>demand cluster</b> for your area, scored against census, road, water " +
      "and connectivity data, and given a <b>rank</b> in the district's list.",
      "Come back and enter <b>#" + saved.id + "</b> below to see where it got to.",
    ].forEach((t) => ol.append("li").html(t));

    const actions = box.append("div").attr("class", "done-actions");
    actions.append("button").attr("type", "button").attr("class", "btn btn--primary")
      .text("REPORT ANOTHER PROBLEM").on("click", reset);
    actions.append("button").attr("type", "button").attr("class", "btn")
      .text("CHECK ITS STATUS")
      .on("click", () => { el("trackId").value = saved.id; track(saved.id); });

    box.node().scrollIntoView({ behavior: "smooth", block: "start" });
  }

  function reset() {
    el("text").value = "";
    el("audio").value = "";
    el("audioLabel").textContent = "🎙 Or record it and upload the audio instead";
    el("attachments").value = "";
    chosenFiles = [];
    renderFiles();
    el("submit").disabled = false;
    showErrors([]);
    show("doneCard", false);
    show("runCard", false);
    show("form", true);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /* -------------------------------------------------------------- track -- */

  async function track(id) {
    const box = d3.select("#trackResult");
    box.html("");
    box.append("div").attr("class", "tstat")
      .append("div").attr("class", "tstat__text").text("Looking up report…");

    let data;
    try {
      const res = await fetch(`${API}/citizen-report/${id}`);
      if (res.status === 404) {
        return paint("bad", "Not found",
          `There is no report numbered <b>#${id}</b>. Check the number and try again.`);
      }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      data = await res.json();
    } catch (err) {
      return paint("bad", "Cannot check", `The server did not answer (${err.message}).`);
    }

    const r = data.report;
    const where = r.village ? `${r.village}, ${r.district}` : "an unmatched place";
    const files = data.attachments || [];
    const evidence = files.length
      ? ` ${files.length} file${files.length > 1 ? "s" : ""} of evidence received.`
      : "";

    if (data.status === "unresolved") {
      return paint("bad", "Needs a place",
        `Report <b>#${id}</b> was stored, but its location could not be matched ` +
        `to a village, so it cannot be grouped with others. File it again and ` +
        `choose the village from the list.`);
    }

    if (data.status === "awaiting_corroboration") {
      return paint("wait", "Waiting for others",
        `Report <b>#${id}</b> about <b>${r.issue_category || "a problem"}</b> in ` +
        `<b>${where}</b> has been received${evidence ? "." + evidence : ""} No ` +
        `matching reports have been grouped with it yet. One report on its own ` +
        `is not treated as a demand signal — if others near you report the same ` +
        `problem, it will join them.`);
    }

    const c = data.cluster;
    paint("ok", "Grouped and ranked",
      `Report <b>#${id}</b> is now part of a <b>${c.issue_category}</b> cluster in ` +
      `<b>${c.block || c.district}</b>, together with <b>${c.report_count} reports</b> ` +
      `from <b>${c.unique_reporters} distinct reporters</b>, affecting about ` +
      `<b>${(c.population_affected || 0).toLocaleString("en-IN")} people</b>.${evidence}`);

    if (c.rank) {
      const rb = d3.select("#trackResult .tstat").append("div").attr("class", "rankbar");
      rb.append("div").attr("class", "rankbar__num").text(`#${c.rank}`);
      rb.append("div").attr("class", "rankbar__of")
        .html(`of ${c.ranked_out_of} clusters in the state, scored ` +
          `<b>${(c.priority_score ?? 0).toFixed(1)}</b> out of 100`);
    }
  }

  function paint(kind, label, html) {
    const box = d3.select("#trackResult");
    box.html("");
    const s = box.append("div").attr("class", "tstat");
    const head = s.append("div").attr("class", "tstat__head");
    head.append("span").attr("class", `tstat__pill tstat__pill--${kind}`).text(label);
    s.append("div").attr("class", "tstat__text").html(html);
    // The track box sits at the bottom of the page, so the answer usually
    // lands below the fold. Bring it to the reader rather than expecting
    // them to notice something appeared off-screen.
    s.node().scrollIntoView({ behavior: "smooth", block: "center" });
  }

  /* --------------------------------------------------------------- auth -- */

  /**
   * Citizen sign-in.
   *
   * The point of an account here is that the "who is reporting" section
   * stops being a form. CPGRAMS asks for name, gender, mobile, e-mail,
   * address and pincode on every single grievance; once those live on an
   * account there is no reason to ask again, and asking again is actively
   * harmful -- the same person retyping their name two ways is recorded as
   * two people, which the demand term would read as corroboration.
   *
   * So: signed in, the section shows the profile read-only and the form
   * posts none of it. The server reads identity off the account (see
   * validate_intake), which also means a hand-crafted POST cannot file a
   * grievance under someone else's name.
   */
  const TOKEN_KEY = "awaaziq_citizen_token";
  let session = null;               // { token, user } once signed in

  function storedToken() {
    try { return localStorage.getItem(TOKEN_KEY); } catch { return null; }
  }
  function storeToken(token) {
    try {
      if (token) localStorage.setItem(TOKEN_KEY, token);
      else localStorage.removeItem(TOKEN_KEY);
    } catch { /* private mode: the session still works for this page load */ }
  }

  /** Authorization header for a signed-in request, or {} when a guest. */
  function authHeaders() {
    return session ? { Authorization: `Bearer ${session.token}` } : {};
  }

  function applyAuthState() {
    const signedIn = !!session;

    // There is no longer a "who is reporting" section. It had nothing for a
    // citizen to do -- signed out it only repeated the header's own Sign In
    // and Register buttons, and signed in it restated details they had
    // already given. The header pill carries who you are; the note by the
    // submit button carries why you cannot file yet.
    el("authGuest").hidden = signedIn;
    el("authLogged").hidden = !signedIn;
    el("signinNote").hidden = signedIn;

    el("submit").disabled = !signedIn;
    el("submit").title = signedIn ? "" : "Sign in to file a grievance";

    if (signedIn) el("userNameLabel").textContent = session.user.full_name;
  }

  async function loadSession() {
    const token = storedToken();
    if (!token) { applyAuthState(); return; }
    try {
      const res = await fetch(`${API}/auth/me`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      session = { token, user: data.user };
    } catch {
      // An expired or rejected token must not leave the page looking signed
      // in; the citizen would fill everything in and only find out at submit.
      storeToken(null);
      session = null;
    }
    applyAuthState();
  }

  async function doLogout() {
    const token = session && session.token;
    session = null;
    storeToken(null);
    applyAuthState();
    if (token) {
      // Best effort: the local session is already gone, so a failure here
      // must not leave the citizen looking signed in.
      fetch(`${API}/auth/logout`, {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
      }).catch(() => {});
    }
  }

  el("btnLogout").addEventListener("click", doLogout);

  /* --------------------------------------------------------------- boot -- */

  SAMPLES.forEach((s) => {
    d3.select("#samples").append("button")
      .attr("type", "button").attr("class", "sample").text(s)
      .on("click", () => { el("text").value = s; el("text").focus(); });
  });

  el("form").addEventListener("submit", submit);

  // Clear a field's error mark as soon as it is touched. Without this the red
  // outline set by a failed submit stayed on every field even after it was
  // filled in correctly, so a completed form still looked entirely wrong.
  el("form").addEventListener("input", clearFieldError, true);
  el("form").addEventListener("change", clearFieldError, true);

  function clearFieldError(event) {
    const holder = event.target.closest(".f");
    if (holder) holder.classList.remove("f--bad");
    if (!document.querySelector(".f--bad")) el("errors").hidden = true;
  }

  el("district").addEventListener("change", (e) => loadBlocks(e.target.value));
  el("block").addEventListener("change", (e) =>
    loadVillages(val("district"), e.target.value));
  el("category").addEventListener("change", (e) => fillDepartments(e.target.value));
  el("department").addEventListener("change", (e) => {
    const opt = e.target.selectedOptions[0];
    el("deptHint").textContent =
      (opt && opt.dataset.note) || "The office that holds the budget for this";
  });

  el("audio").addEventListener("change", function () {
    el("audioLabel").textContent = this.files[0]
      ? `🎙 ${this.files[0].name}`
      : "🎙 Or record it and upload the audio instead";
  });

  el("attachments").addEventListener("change", function () {
    const rejected = [];
    Array.from(this.files).forEach((f) => {
      if (chosenFiles.length >= MAX_FILES) {
        rejected.push(`${f.name} — only ${MAX_FILES} files allowed`);
      } else if (f.size > MAX_BYTES) {
        rejected.push(`${f.name} — larger than 4 MB`);
      } else if (!/^image\//.test(f.type) && f.type !== "application/pdf") {
        rejected.push(`${f.name} — only photos and PDFs`);
      } else {
        chosenFiles.push(f);
      }
    });
    this.value = "";                       // allow re-picking the same file
    renderFiles();
    if (rejected.length) showErrors(rejected.map((m) => ["attachments", m]));
  });

  el("trackGo").addEventListener("click", () => {
    const id = el("trackId").value.trim();
    if (id) track(id);
  });
  el("trackId").addEventListener("keydown", (e) => {
    if (e.key === "Enter") { e.preventDefault(); el("trackGo").click(); }
  });

  loadDistricts();
  loadDepartments();
  // Restores a signed-in citizen on reload, so the profile is already on
  // screen rather than appearing a beat later.
  loadSession();
  renderFiles();
  renderMine();

  // ?track=2014 opens straight onto one report's status.
  const t = params.get("track");
  if (t) { el("trackId").value = t; track(t); }
})();
