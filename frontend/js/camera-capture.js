/* ===========================================================================
   AwaazIQ — live camera capture (Workstream C1 + C7)

   Why not <input type="file" capture="camera">: that attribute is only a hint,
   and many phones still offer the gallery. getUserMedia opens a live video
   stream with no file picker at all, so the photo must be of what is in front
   of the camera now.

   One press of the shutter records:
     - the photo, at the camera's full resolution (Image Capture API
       grabFrame when the browser has it, a canvas copy of the video if not);
     - a burst of BURST_FRAMES smaller frames ~110 ms apart, so the server can
       check for natural sensor noise and hand shake (C7 — a still image fed
       through a virtual camera has neither);
     - the GPS fix and the time *at the same moment* (C1), not a map pin set
       earlier.

   Needs a secure context (https or localhost), like geolocation.
   =========================================================================== */

(function () {
  "use strict";

  const BURST_FRAMES = 5;
  const BURST_GAP_MS = 110;
  const BURST_WIDTH = 640;

  function h(tag, attrs, ...kids) {
    const n = document.createElement(tag);
    Object.entries(attrs || {}).forEach(([k, v]) => {
      if (v == null) return;
      if (k === "class") n.className = v;
      else if (k.startsWith("on")) n.addEventListener(k.slice(2), v);
      else n.setAttribute(k, v);
    });
    kids.flat().forEach((c) => c != null && n.appendChild(typeof c === "string" ? document.createTextNode(c) : c));
    return n;
  }

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const toBlob = (canvas, q) => new Promise((r) => canvas.toBlob(r, "image/jpeg", q));

  function supported() {
    return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia) && window.isSecureContext !== false;
  }

  /**
   * open({ title, onCapture(shot) })
   * shot = { blob, url, width, height, burst: [Blob], meta: {...} }
   */
  function open(opts) {
    opts = opts || {};
    let stream = null, watchId = null, fix = null, shot = null, closed = false;

    const gpsChip = h("span", { class: "cam__chip" }, "📍 Locating…");
    const video = h("video", { class: "cam__video", autoplay: "", playsinline: "", muted: "" });
    const still = h("img", { class: "cam__still", alt: "Captured photo", hidden: "" });
    const flash = h("div", { class: "cam__flash" });
    const frame = h("div", { class: "cam__frame" });
    const msg = h("div", { class: "cam__msg", hidden: "" });
    const stage = h("div", { class: "cam__stage" }, video, still, frame, flash, msg);
    const shutter = h("button", { type: "button", class: "shutter", "aria-label": "Take photo", disabled: "" });
    const retake = h("button", { type: "button", class: "cam__btn", hidden: "" }, "Retake");
    const use = h("button", { type: "button", class: "cam__btn cam__btn--go", hidden: "" }, "Use this photo");
    const hint = h("div", { class: "cam__hint" }, "Hold steady and point at the problem. One press takes the photo, a short burst and your location together.");
    const root = h("div", { class: "cam", role: "dialog", "aria-modal": "true", "aria-label": "Camera" },
      h("div", { class: "cam__top" },
        h("span", { class: "cam__title" }, opts.title || "Photograph the problem"),
        gpsChip,
        h("button", { type: "button", class: "cam__close", "aria-label": "Close camera", onclick: close }, "×")),
      stage, hint,
      h("div", { class: "cam__bar" }, retake, shutter, use));
    document.body.appendChild(root);
    document.body.style.overflow = "hidden";

    function say(text) { msg.textContent = text; msg.hidden = !text; }

    function close() {
      if (closed) return;
      closed = true;
      if (stream) stream.getTracks().forEach((t) => t.stop());
      if (watchId != null && navigator.geolocation) navigator.geolocation.clearWatch(watchId);
      document.body.style.overflow = "";
      root.remove();
    }
    document.addEventListener("keydown", function onKey(e) {
      if (e.key === "Escape") { close(); document.removeEventListener("keydown", onKey); }
    });

    // GPS runs for the whole time the camera is open, so a fresh fix exists
    // when the shutter is pressed rather than starting a slow lookup then.
    if (navigator.geolocation) {
      watchId = navigator.geolocation.watchPosition(
        (p) => {
          fix = { lat: p.coords.latitude, lon: p.coords.longitude, accuracy_m: p.coords.accuracy, at: new Date(p.timestamp).toISOString() };
          gpsChip.textContent = `📍 GPS ±${Math.round(p.coords.accuracy)} m`;
          gpsChip.className = `cam__chip ${p.coords.accuracy <= 100 ? "cam__chip--ok" : "cam__chip--warn"}`;
        },
        () => { gpsChip.textContent = "📍 GPS unavailable"; gpsChip.className = "cam__chip cam__chip--warn"; },
        { enableHighAccuracy: true, maximumAge: 5000, timeout: 20000 });
    } else {
      gpsChip.textContent = "📍 No GPS on this device";
      gpsChip.className = "cam__chip cam__chip--warn";
    }

    if (!supported()) {
      say("This browser cannot open the camera here. Photos must be taken live in the app — open this page over https (or on this computer at localhost) in Chrome, Edge, Safari or Firefox.");
      return { close };
    }

    navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: "environment" }, width: { ideal: 1920 }, height: { ideal: 1080 } },
      audio: false,
    }).then((s) => {
      if (closed) { s.getTracks().forEach((t) => t.stop()); return; }
      stream = s;
      video.srcObject = s;
      video.onloadedmetadata = () => { video.play(); shutter.disabled = false; };
    }).catch((err) => {
      say(err && err.name === "NotAllowedError"
        ? "Camera permission was refused. Allow camera access for this site to add a photo — or close this and file without one."
        : `The camera could not start (${err && err.name ? err.name : "unknown error"}). Is another app using it?`);
    });

    // Longest side kept under this so a photo stays within the 4 MB upload
    // limit on high-resolution phone cameras.
    const MAX_SIDE = 2560;

    async function grabFull() {
      let source = video, sw = video.videoWidth, sh = video.videoHeight;
      const track = stream.getVideoTracks()[0];
      if ("ImageCapture" in window && track) {
        try {
          const bmp = await new window.ImageCapture(track).grabFrame();
          source = bmp; sw = bmp.width; sh = bmp.height;
        } catch { /* fall back to copying the video element */ }
      }
      const scale = Math.min(1, MAX_SIDE / Math.max(sw, sh));
      const c = h("canvas"); c.width = Math.round(sw * scale); c.height = Math.round(sh * scale);
      c.getContext("2d").drawImage(source, 0, 0, c.width, c.height);
      return c;
    }

    function grabSmall() {
      const w = BURST_WIDTH, hgt = Math.round(BURST_WIDTH * video.videoHeight / video.videoWidth);
      const c = h("canvas"); c.width = w; c.height = hgt;
      c.getContext("2d").drawImage(video, 0, 0, w, hgt);
      return toBlob(c, 0.85);
    }

    shutter.addEventListener("click", async () => {
      if (!stream) return;
      shutter.disabled = true;
      shutter.classList.add("shutter--busy");
      const capturedAt = new Date().toISOString();
      const gps = fix;                                   // the fix at this moment
      flash.classList.remove("cam__flash--on"); void flash.offsetWidth; flash.classList.add("cam__flash--on");

      const full = await grabFull();
      const blob = await toBlob(full, 0.92);
      const burst = [];
      for (let i = 0; i < BURST_FRAMES; i++) {
        burst.push(await grabSmall());
        if (i < BURST_FRAMES - 1) await sleep(BURST_GAP_MS);
      }
      shutter.classList.remove("shutter--busy");

      const track = stream.getVideoTracks()[0];
      const settings = track && track.getSettings ? track.getSettings() : {};
      shot = {
        blob, burst,
        url: URL.createObjectURL(blob),
        width: full.width, height: full.height,
        meta: {
          method: "live_camera",
          captured_at: capturedAt,
          lat: gps ? gps.lat : null,
          lon: gps ? gps.lon : null,
          accuracy_m: gps ? gps.accuracy_m : null,
          gps_at: gps ? gps.at : null,
          burst_count: burst.length,
          width: full.width, height: full.height,
          facing: settings.facingMode || null,
        },
      };
      still.src = shot.url;
      still.hidden = false; video.hidden = true; frame.hidden = true;
      shutter.hidden = true; retake.hidden = false; use.hidden = false;
      hint.textContent = gps
        ? `Captured at ${new Date(capturedAt).toLocaleTimeString()} · GPS ±${Math.round(gps.accuracy_m)} m`
        : `Captured at ${new Date(capturedAt).toLocaleTimeString()} · no GPS fix — the photo will be marked as such`;
    });

    retake.addEventListener("click", () => {
      if (shot) URL.revokeObjectURL(shot.url);
      shot = null;
      still.hidden = true; video.hidden = false; frame.hidden = false;
      shutter.hidden = false; shutter.disabled = false; retake.hidden = true; use.hidden = true;
      hint.textContent = "Hold steady and point at the problem.";
    });

    use.addEventListener("click", () => {
      const s = shot;
      shot = null;
      close();
      if (s && opts.onCapture) opts.onCapture(s);
    });

    return { close };
  }

  window.CameraCapture = { open, supported, BURST_FRAMES };
})();
