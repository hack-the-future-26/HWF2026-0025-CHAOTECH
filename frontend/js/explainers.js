/* ===========================================================================
   AwaazIQ — "How it works" explainers.

   Every panel on this dashboard shows a number. A number without its
   provenance is a claim you have to take on faith, and this project's whole
   argument is that a citizen or an officer should be able to audit the
   ranking rather than trust it. These popups are where that audit starts:
   what the feature computes, which government dataset each input came from,
   and -- said plainly -- which inputs are still proxies rather than real data.

   Content here is checked against the code that actually runs:
   intelligence/config.py for every weight, intelligence/scoring.py for the
   real-vs-proxy fallbacks, models/README.md for the detector's measured
   accuracy. If a constant changes there, change it here too.
   =========================================================================== */
(function (global) {
  "use strict";

  /* Rendered in order. `rows` become a two-column provenance table, `notes`
     become body paragraphs, `list` becomes a bulleted list. */
  const CONTENT = {

    /* ------------------------------------------------- the 9-term score -- */
    "priority-score": {
      title: "How the priority score works",
      lede:
        "A 0-100 score answering one question: of everything reported, what should be " +
        "fixed first? It is built from nine terms that sum exactly to the score - no " +
        "hidden weighting, no black box. Every term below shows where its input came from.",
      sections: [
        {
          heading: "The formula, in two stages",
          notes: [
            "<b>Stage 1 &mdash; Gap score</b> (how bad is this problem, 0&ndash;1): the first " +
            "four terms are weighted equally at 0.25 each, then scaled to 69 points.",
            "<b>Stage 2 &mdash; Offsets:</b> equity, scheme eligibility, urgency and " +
            "feasibility are added, and a cost penalty is subtracted. Maximum possible: " +
            "69 + 10 + 4 + 15 + 2 = 100.",
            "The terms are <b>added, not multiplied</b>. Multiplying collapses a score " +
            "toward zero as terms are added, and lets one missing input erase an otherwise " +
            "real gap. The only multiplier is a confidence gate, which damps a score when " +
            "text extraction was unsure &mdash; it never multiplies by zero.",
          ],
        },
        {
          heading: "The nine terms and where each number comes from",
          rows: [
            ["Citizen demand <span class=\"xp-w\">0.25</span>",
             "Distinct reporters, <b>capped at 25</b>. Past 25 the term saturates &mdash; " +
             "this is what stops a well-connected district out-shouting a cut-off one."],
            ["Population affected <span class=\"xp-w\">0.25</span>",
             "Census 2011 District Census Handbook, plus PMGSY habitation counts. " +
             "Log-scaled so a town does not erase a village."],
            ["Infrastructure deficit <span class=\"xp-w\">0.25</span>",
             "Measured against each scheme's own published norm: Census 2011 village " +
             "amenities, PMGSY works, JJM tap coverage, UDISE+ 2024-25, IPHS norms, " +
             "RTE Act 2009. Falls back to reported severity where no record exists."],
            ["Deprivation <span class=\"xp-w\">0.25</span>",
             "Census 2011 &mdash; power hours, drainage, isolation, access. " +
             "<b>Area-level only; no caste data is used anywhere.</b> Falls back to " +
             "settlement size where the Census row is missing."],
            ["Equity &mdash; silent need <span class=\"xp-w\">+10</span>",
             "BharatNet / BBNL 2022 gram-panchayat fibre status, applied proportionally. " +
             "The load-bearing idea: the same number of reports from a population that " +
             "struggles to report at all is stronger evidence, not weaker."],
            ["Scheme eligibility <span class=\"xp-w\">+4</span>",
             "Published scheme rules where they apply &mdash; PMGSY population floor, IPHS " +
             "sub-centre norm, RTE middle-school duty, JJM coverage threshold. Falls back " +
             "to a population threshold."],
            ["Urgency <span class=\"xp-w\">+15</span>",
             "Emergency only, and graded: crack 1/3, partial damage 2/3, collapse 1.0 " +
             "&mdash; scaled by how many independent signals agree. Inputs: the vision " +
             "damage check, the citizen's own wording, live SACHET/NDMA alerts and live " +
             "CWC river levels."],
            ["Feasibility <span class=\"xp-w\">+2</span>",
             "Census 2011 recorded distance to the taluka headquarters and all-weather " +
             "road share. Falls back to straight-line distance."],
            ["Cost penalty <span class=\"xp-w\">&minus;3</span>",
             "Scaled by catchment population. Honest limit: no per-project repair cost " +
             "data exists for health or education, so this is a population proxy, not a " +
             "quote."],
          ],
        },
        {
          heading: "How to audit a score you don't believe",
          notes: [
            "Each term in the panel carries its own provenance label, so you can see per " +
            "term whether it used a government record or a stand-in &mdash; for example " +
            "<code>government_records</code> versus <code>proxy_reported_severity</code>, " +
            "or <code>bharatnet_2022</code> versus <code>proxy_hardcoded_block_list</code>.",
            "The nine terms are tested to sum exactly to the displayed score. If they ever " +
            "did not, the explanation would be decorative rather than real.",
          ],
        },
      ],
    },

    /* --------------------------------------------- demand vs investment -- */
    "demand-investment": {
      title: "How demand vs investment works",
      lede:
        "Citizen demand placed directly against what the government has already sanctioned " +
        "and spent in the same place. It answers the question a ranking alone cannot: is " +
        "this village unheard, or already funded and still broken?",
      sections: [
        {
          heading: "What is being cross-checked",
          rows: [
            ["Roads",
             "PMGSY sanctioned works &mdash; 552 records carrying sanction year, cost and " +
             "current execution status."],
            ["Schools",
             "UDISE+ 2024-25 &mdash; grant received versus grant actually spent, per school, " +
             "for the current financial year."],
            ["Water",
             "Jal Jeevan Mission &mdash; 2,183 village schemes with cost and status."],
          ],
        },
        {
          heading: "Also on file - evidence only, never moves a score",
          list: [
            "MGNREGA expenditure",
            "PRIASoft 15th Finance Commission receipts",
            "JJM water-quality testing results",
            "eGramSwaraj GPDP district summaries",
            "NDEM flood extents, 2013 and 2021",
          ],
          notes: [
            "These are shown as evidence beside a report but are deliberately kept out of " +
            "the score. Spending is not the same as need, and a dataset that cannot be " +
            "defended as a need signal should not silently move a ranking.",
          ],
        },
      ],
    },

    /* ------------------------------------------------ photo verification -- */
    "photo-verification": {
      title: "How photo verification works",
      lede:
        "Seven checks answering two separate questions: is this photo genuine, and does it " +
        "actually show the problem? No check ever rejects a complaint &mdash; each one " +
        "produces evidence and, at most, a flag for an officer.",
      sections: [
        {
          heading: "Is the photo genuine?",
          rows: [
            ["C1 &middot; Capture provenance",
             "Live camera capture only, with GPS and time recorded at the shutter. Honest " +
             "limit: this raises the cost of faking, it is not proof."],
            ["C3 &middot; Duplicate photo",
             "Perceptual hash compared against every photo already stored, so the same " +
             "image cannot be filed twice from two places."],
            ["C4 &middot; Edit detection",
             "Editing-software traces in EXIF metadata, plus an error-level analysis score " +
             "and heat map as an advisory hint."],
            ["C7 &middot; Screen replay",
             "Moire-pattern spectrum plus a 5-frame burst checked for natural sensor noise " +
             "&mdash; catches a photo of a screen."],
          ],
        },
        {
          heading: "Does the photo show the problem?",
          rows: [
            ["C5 &middot; Road defect",
             "The team's own YOLOv8 crack/pothole model. Measured on 88 held-out real " +
             "photos never used for tuning: <b>81.8% accuracy, 86.5% recall, 0.924 " +
             "ROC-AUC</b>."],
            ["C6 &middot; Structure damage",
             "A vision-language model grades bridge and building damage &mdash; crack, " +
             "partial, or collapse. Used because no trained ground-level collapse " +
             "classifier exists to install, and a coarse scene judgment is exactly what a " +
             "general vision model is genuinely good at."],
          ],
        },
        {
          heading: "The ground rule",
          notes: [
            "A failed or unavailable check never rejects a report and never zeroes its " +
            "score. It lowers confidence and flags the photo for a human. On the hosted " +
            "copy C5's 150 MB weight file is absent and C6's gateway is unreachable, so " +
            "both report themselves as unavailable rather than quietly scoring without " +
            "them.",
          ],
        },
      ],
    },

    /* -------------------------------------------------- budget optimizer -- */
    "budget-optimizer": {
      title: "How the budget optimizer works",
      lede:
        "Given a real budget, which already-costed projects should actually be funded? " +
        "This is a 0/1 knapsack allocation over real project costs &mdash; not a " +
        "re-shuffle of the ranking.",
      sections: [
        {
          heading: "What it does",
          notes: [
            "Each candidate project carries a real sanctioned cost and a real population " +
            "reached. The optimizer selects the combination that fits inside the budget " +
            "and reaches the most people &mdash; which is frequently <b>not</b> the top of " +
            "the priority list, because the top item can be expensive enough to crowd out " +
            "several cheaper ones that together serve more people.",
            "That divergence is the point of the feature: a ranking tells you what matters " +
            "most, an allocation tells you what to buy.",
          ],
        },
        {
          heading: "Honest limits",
          list: [
            "Roads (PMGSY) and water (JJM) only &mdash; these are the two with defensible " +
            "per-project costs.",
            "Health and education are excluded: no honest per-project repair cost exists " +
            "for either, and inventing one would make the whole allocation fiction.",
          ],
        },
      ],
    },

    /* ---------------------------------------------------------- clustering -- */
    "clustering": {
      title: "How clustering works",
      lede:
        "Reports about the same problem in the same area are grouped into one demand " +
        "cluster. A lone report is evidence; a cluster is a demand signal.",
      sections: [
        {
          heading: "How reports are grouped",
          rows: [
            ["1 &middot; Gate",
             "Candidate pairs must share an issue category and sit within 8 km before " +
             "anything expensive runs."],
            ["2 &middot; Meaning",
             "Sentence embeddings compare what the reports actually say, so \"handpump " +
             "dry\" and \"no water in the tap\" can join."],
            ["3 &middot; Cluster",
             "DBSCAN with a 6 km radius and a minimum of 3 reports."],
          ],
        },
        {
          heading: "Why some reports stay in no cluster",
          notes: [
            "Around 29% of reports belong to no cluster, and that is correct behaviour " +
            "rather than a gap. A single uncorroborated report in a sparse area is not yet " +
            "a demand signal, and forcing it into a cluster would claim corroboration that " +
            "does not exist. The 8 km gate was tuned against the real data, not guessed: " +
            "at 12 km, genuinely distinct problems began collapsing into one blob.",
          ],
        },
      ],
    },

    /* ------------------------------------------------------ fix first ---- */
    "fix-first": {
      title: "How \"fix first inside this village\" works",
      lede:
        "A village-level score tells you where to go. This ranks the individual assets " +
        "inside it &mdash; a named school, a specific road stretch, one water point " +
        "&mdash; so the answer is something a department can actually act on.",
      sections: [
        {
          heading: "How assets are identified and ranked",
          notes: [
            "Each report is tied to a named asset from a government register rather than " +
            "to the village centroid: UDISE+ for schools, the health facility register, " +
            "the PMGSY road register for road stretches, and JJM for water schemes.",
            "Assets are then scored with the same nine terms as the village, so the " +
            "ordering inside a village is explainable in exactly the same way as the " +
            "ordering between villages.",
          ],
        },
        {
          heading: "The urgent banner",
          notes: [
            "When an asset carries a photo or report graded as an emergency, it is " +
            "surfaced above the ranking even if it is not the village's top-scoring asset. " +
            "A collapsed classroom should not wait behind a higher-scoring road.",
          ],
        },
      ],
    },

    /* ------------------------------------------------- citizen reports ---- */
    "citizen-reports": {
      title: "How citizen reports are processed",
      lede:
        "A complaint arrives as text or voice, in Hindi, Marathi, English or a mix of " +
        "them. What the state sees is what was reported and from where &mdash; never by " +
        "whom.",
      sections: [
        {
          heading: "The intake pipeline",
          rows: [
            ["Language",
             "Detected per report: hi-Deva, mr-Deva, hi-Latn, mixed or en."],
            ["Voice",
             "faster-whisper transcription with voice-activity detection. Honest limit: " +
             "Hindi and English transcribe well; Marathi is weak even when forced, which " +
             "is a model limitation and is labelled as one."],
            ["Issue &amp; severity",
             "Extracted per language across four categories: road, water, health, " +
             "education."],
            ["Location",
             "Trigger-word windows, then fuzzy-matched against a 1,042-place gazetteer " +
             "built from OpenStreetMap and the Census."],
          ],
        },
        {
          heading: "Privacy, by construction",
          notes: [
            "Phone numbers and self-stated names are stripped at ingestion, and no citizen " +
            "identity is stored at all. The dashboard counts <b>distinct reporters</b> " +
            "without being able to name any of them.",
            "This is also why the demand term saturates at 25 reporters: the system is " +
            "built to measure need, not popularity.",
          ],
        },
      ],
    },

    /* ------------------------------------------------------- what's loaded -- */
    "data-sources": {
      title: "What data is loaded",
      lede:
        "Everything below is real government or open data, loaded by the scripts in this " +
        "repository. Nothing here is mocked.",
      sections: [
        {
          heading: "Coverage",
          rows: [
            ["1,042 villages",
             "Kolhapur and Nashik districts &mdash; OpenStreetMap + Census 2011"],
            ["10,464 named facilities",
             "9,352 UDISE+ schools + 1,112 health facilities"],
            ["8,063 road segments", "PMGSY GeoSadak road geometry"],
            ["2,183 water schemes", "Jal Jeevan Mission village schemes"],
            ["552 sanctioned works", "PMGSY, with cost and execution status"],
            ["819 ranked assets",
             "across 443 ranked villages and 118 demand clusters"],
          ],
        },
        {
          heading: "Live feeds",
          list: [
            "SACHET / NDMA hazard alerts",
            "CWC river levels",
          ],
        },
        {
          heading: "Stated plainly",
          notes: [
            "Two inputs are still stand-ins rather than real data, and the score labels " +
            "them as such per term: the cost penalty is a population proxy because no " +
            "per-project repair cost exists for health or education, and where BharatNet " +
            "fibre status is missing the equity term falls back to a hardcoded list of " +
            "low-connectivity blocks.",
          ],
        },
      ],
    },
  };

  /* ------------------------------------------------------------ rendering -- */

  let overlay = null;
  let lastFocus = null;

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  function build(key) {
    const c = CONTENT[key];
    if (!c) return "";
    let html =
      '<div class="xp__head">' +
        '<h2 class="xp__title">' + esc(c.title) + "</h2>" +
        '<button class="xp__close" aria-label="Close">&times;</button>' +
      "</div>";
    if (c.lede) html += '<p class="xp__lede">' + c.lede + "</p>";

    (c.sections || []).forEach(function (s) {
      html += '<section class="xp__sec">';
      if (s.heading) html += '<div class="eyebrow xp__h">' + esc(s.heading) + "</div>";
      if (s.rows) {
        html += '<div class="xp__rows">';
        s.rows.forEach(function (r) {
          html +=
            '<div class="xp__row">' +
              '<div class="xp__k">' + r[0] + "</div>" +
              '<div class="xp__v">' + r[1] + "</div>" +
            "</div>";
        });
        html += "</div>";
      }
      if (s.list) {
        html += '<ul class="xp__list">';
        s.list.forEach(function (li) { html += "<li>" + li + "</li>"; });
        html += "</ul>";
      }
      (s.notes || []).forEach(function (n) { html += '<p class="xp__p">' + n + "</p>"; });
      html += "</section>";
    });
    return html;
  }

  function close() {
    if (!overlay) return;
    const dying = overlay;
    overlay = null;
    dying.classList.remove("xp--open");
    // Let the fade finish before the node leaves the tree, or it vanishes hard.
    setTimeout(function () {
      if (dying.parentNode) dying.parentNode.removeChild(dying);
    }, 180);
    document.removeEventListener("keydown", onKey);
    if (lastFocus && lastFocus.focus) lastFocus.focus();
  }

  function onKey(e) {
    if (e.key === "Escape") close();
  }

  function open(key) {
    if (!CONTENT[key]) return;
    if (overlay) close();
    lastFocus = document.activeElement;

    const el = document.createElement("div");
    el.className = "xp";
    el.setAttribute("role", "dialog");
    el.setAttribute("aria-modal", "true");
    el.innerHTML = '<div class="xp__card">' + build(key) + "</div>";

    el.addEventListener("click", function (e) {
      // Backdrop only -- a click inside the card must not dismiss it.
      if (e.target === el) close();
    });
    el.querySelector(".xp__close").addEventListener("click", close);
    document.addEventListener("keydown", onKey);

    document.body.appendChild(el);
    overlay = el;
    // Next frame, so the opening transition has two states to run between.
    requestAnimationFrame(function () {
      el.classList.add("xp--open");
      const btn = el.querySelector(".xp__close");
      if (btn) btn.focus();
    });
  }

  /* Returns the button so d3 callers can position it themselves. */
  function makeButton(key, label) {
    const b = document.createElement("button");
    b.className = "xpbtn";
    b.type = "button";
    b.textContent = label || "How it works";
    b.setAttribute("aria-label",
      "How it works: " + (CONTENT[key] ? CONTENT[key].title : key));
    b.addEventListener("click", function (e) {
      e.preventDefault();
      e.stopPropagation();
      open(key);
    });
    return b;
  }

  /* Appends to a host node. Accepts a DOM node or a d3 selection. */
  function attach(host, key, label) {
    if (!host) return null;
    const node = host.node ? host.node() : host;
    if (!node || !CONTENT[key]) return null;
    const b = makeButton(key, label);
    node.appendChild(b);
    return b;
  }

  global.Explain = {
    open: open,
    attach: attach,
    button: makeButton,
    has: function (k) { return !!CONTENT[k]; },
  };
})(window);
