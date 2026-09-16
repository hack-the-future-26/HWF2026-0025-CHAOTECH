/* ===========================================================================
   AwaazIQ — Photo Lab

   Sends each photo to POST /photo-checks/analyze (which stores nothing) and
   renders the result with the same card the citizen receipt and the officer
   panel use. Also shows the model's measured accuracy from
   GET /photo-checks/model, so what the lab shows can be weighed honestly.
   =========================================================================== */

(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || window.AWAAZIQ_API_BASE || "http://127.0.0.1:8001").replace(/\/$/, "");
  if (params.get("api")) {
    const q = `?api=${encodeURIComponent(API)}`;
    ["homeLink", "dashLink"].forEach((id) => { document.getElementById(id).href = `index.html${q}`; });
    document.getElementById("citizenLink").href = `report.html${q}`;
  }

  const el = (id) => document.getElementById(id);
  const pct = (x, d = 0) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
  const tally = { n: 0, defect: 0, review: 0, ms: 0 };
  let queue = Promise.resolve();

  /* ---------------------------------------------------------- model card -- */

  async function loadModel() {
    const body = el("modelBody");
    try {
      const res = await fetch(`${API}/photo-checks/model`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const m = await res.json();
      const ev = m.evaluation;
      const held = ev && ev.image_level && ev.image_level.find((d) => d.key === "heldout");
      const op = held ? held.operating_point : null;

      let html =
        `<div class="mmeta">` +
        `<span class="${m.available ? "ok" : "bad"}">${m.available ? "● model loaded" : "● model file missing"}</span>` +
        `<span>YOLOv8 · crack + pothole</span><span>${m.imgsz}px</span>` +
        `<span>conf ≥ ${m.threshold}</span><span>box ≥ ${m.min_box_area_pct}% of frame</span>` +
        `<span>${m.tta ? "test-time augmentation on" : "TTA off"}</span></div>`;

      if (op) {
        html += `<div class="mstat">` +
          `<div class="mstat__cell"><div class="mstat__v">${pct(op.accuracy, 1)}</div><div class="mstat__l">accuracy on ${held.n} held-out real photos</div></div>` +
          `<div class="mstat__cell"><div class="mstat__v">${pct(op.recall)}</div><div class="mstat__l">of real potholes caught (recall)</div></div>` +
          `<div class="mstat__cell"><div class="mstat__v">${pct(op.precision)}</div><div class="mstat__l">of alerts are real potholes (precision)</div></div>` +
          `<div class="mstat__cell"><div class="mstat__v">${op.roc_auc.toFixed(2)}</div><div class="mstat__l">ROC-AUC (1.0 = perfect ranking)</div></div>` +
          `</div>`;
      }
      if (ev && ev.image_level) {
        html += `<div class="mtable-wrap"><table class="mtable"><thead><tr><th>Test set</th><th>n</th><th>Acc</th><th>Prec</th><th>Recall</th><th>F1</th></tr></thead><tbody>`;
        ev.image_level.forEach((d) => {
          const r = d.operating_point;
          html += `<tr><td>${d.name}</td><td>${d.n}</td><td>${pct(r.accuracy)}</td><td>${pct(r.precision)}</td><td>${pct(r.recall)}</td><td>${r.f1.toFixed(2)}</td></tr>`;
        });
        html += `</tbody></table></div>`;
      }
      if (ev && ev.limits) {
        html += `<div class="eyebrow" style="margin:8px 0 6px">Honest limits</div><ul class="mnote">` +
          ev.limits.map((l) => `<li>${l}</li>`).join("") + `</ul>`;
      }
      body.innerHTML = html;
    } catch (err) {
      body.innerHTML = `<div class="lab__err">Cannot reach the API at ${API} (${err.message}). Start the backend on port 8001.</div>`;
    }
  }

  /* ------------------------------------------------------------ analyse -- */

  function renderTally() {
    el("tally").innerHTML = tally.n
      ? `<span>${tally.n} tested</span><span>🕳️ ${tally.defect} with road damage</span>` +
        `<span>⚑ ${tally.review} flagged</span><span>⏱ ${(tally.ms / tally.n / 1000).toFixed(1)} s avg</span>`
      : "";
  }

  function text() {
    return el("text").value.trim() || el("preset").value;
  }

  function enqueue(file, extra) {
    const pending = document.createElement("div");
    pending.className = "pending";
    const thumb = URL.createObjectURL(file);
    pending.innerHTML = `<img src="${thumb}" alt=""><div class="spinner"></div><span>Checking <b></b> — running the model and forensic checks…</span>`;
    pending.querySelector("b").textContent = file.name || "camera photo";
    el("queue").prepend(pending);
    queue = queue.then(() => analyse(file, extra, pending, thumb));
  }

  async function analyse(file, extra, pending, thumb) {
    const fd = new FormData();
    fd.append("photo", file, file.name || "photo.jpg");
    fd.append("category", el("category").value);
    const t = text();
    if (t) fd.append("text", t);
    if (extra && extra.meta) fd.append("capture_meta", JSON.stringify(extra.meta));
    (extra && extra.burst || []).forEach((b, i) => fd.append("burst", new File([b], `burst-${i}.jpg`, { type: "image/jpeg" })));

    const started = performance.now();
    const results = el("results");
    try {
      const res = await fetch(`${API}/photo-checks/analyze`, { method: "POST", body: fd });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const ms = performance.now() - started;

      const empty = results.querySelector(".lab__empty");
      if (empty) empty.remove();
      const item = document.createElement("div");
      item.className = "lab__item";
      const head = document.createElement("div");
      head.className = "lab__itemHead";
      head.innerHTML = `<b></b><span>${(ms / 1000).toFixed(1)} s · ${data.image.width}×${data.image.height} · ${el("category").value}</span>`;
      head.querySelector("b").textContent = file.name || "Live camera photo";
      item.appendChild(head);
      item.appendChild(window.PhotoChecks.render(data, { annotatedSrc: data.annotated_image, elaSrc: data.ela_image }));
      results.prepend(item);

      tally.n += 1; tally.ms += ms;
      if ((data.defects || {}).defect_seen) tally.defect += 1;
      if (((data.summary || {}).review_flags || []).length) tally.review += 1;
      renderTally();
    } catch (err) {
      const e = document.createElement("div");
      e.className = "lab__err";
      e.textContent = `${file.name || "Photo"}: ${err.message}`;
      results.prepend(e);
    } finally {
      pending.remove();
      URL.revokeObjectURL(thumb);
    }
  }

  function takeFiles(list) {
    Array.from(list).filter((f) => /^image\/(jpeg|png|webp)$/.test(f.type)).forEach((f) => enqueue(f));
  }

  /* --------------------------------------------------------------- wire -- */

  el("files").addEventListener("change", function () { takeFiles(this.files); this.value = ""; });
  const drop = el("drop");
  ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("drop--over"); }));
  ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("drop--over"); }));
  drop.addEventListener("drop", async (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files.length) return takeFiles(e.dataTransfer.files);
    // An image dragged straight from another browser tab arrives as a URL.
    const url = e.dataTransfer.getData("text/uri-list") || e.dataTransfer.getData("text/plain");
    if (!url) return;
    try {
      const r = await fetch(url);
      const blob = await r.blob();
      if (!/^image\//.test(blob.type)) throw new Error("not an image");
      takeFiles([new File([blob], url.split("/").pop().split("?")[0] || "web-image.jpg", { type: blob.type })]);
    } catch {
      const e2 = document.createElement("div");
      e2.className = "lab__err";
      e2.textContent = "That website does not allow its image to be fetched directly — save the image first, then drop the file.";
      el("results").prepend(e2);
    }
  });

  el("btnCam").addEventListener("click", () => {
    window.CameraCapture.open({
      title: "Photo Lab — live capture",
      onCapture: (shot) => enqueue(new File([shot.blob], "live-camera.jpg", { type: "image/jpeg" }), shot),
    });
  });

  loadModel();
})();
