/* ===========================================================================
   AwaazIQ — citizen sign-in page

   A page rather than a modal. The modal it replaces sat inside report.html
   and depended on styles that were not always loaded, so it could render as
   raw unstyled markup sprawled down the page -- with no way for a citizen to
   tell whether the form was broken or they were.

   A page also behaves the way people expect an account screen to behave: it
   has its own URL, the back button works, a password manager can see it, and
   it can be linked to from anywhere. `?next=` carries the citizen back to
   whatever they were doing.
   =========================================================================== */

(function () {
  "use strict";

  const params = new URLSearchParams(location.search);
  const API = (params.get("api") || "http://127.0.0.1:8001").replace(/\/$/, "");
  const TOKEN_KEY = "awaaziq_citizen_token";

  const el = (id) => document.getElementById(id);
  const val = (id) => el(id).value.trim();

  /** Where to go after signing in. Same-origin paths only: an open redirect
   *  on an account page is how a phishing copy of it gets its traffic. */
  function nextPath() {
    const wanted = params.get("next") || "report.html";
    if (/^[a-zA-Z][a-zA-Z0-9+.-]*:|^\/\//.test(wanted)) return "report.html";
    return wanted;
  }

  function goBack() {
    const url = new URL(nextPath(), location.href);
    if (params.get("api")) url.searchParams.set("api", API);
    location.href = url.toString();
  }

  function storeToken(token) {
    try { localStorage.setItem(TOKEN_KEY, token); }
    catch { /* private mode -- the redirect still carries the session below */ }
  }

  function showTab(which) {
    const login = which !== "register";
    el("loginCard").hidden = !login;
    el("registerCard").hidden = login;
    el("tabLoginBtn").classList.toggle("is-on", login);
    el("tabRegisterBtn").classList.toggle("is-on", !login);
    el("pageTitle").textContent = login
      ? "Sign in to file a grievance"
      : "Create your citizen account";
    // Keep the URL honest, so a reload or a shared link lands on the same
    // half of the page rather than silently switching back to sign-in.
    const url = new URL(location.href);
    url.searchParams.set("tab", login ? "login" : "register");
    history.replaceState(null, "", url);
    setTimeout(() => el(login ? "loginEmail" : "regFullName").focus(), 40);
  }

  function showError(boxId, message) {
    const box = el(boxId);
    box.hidden = !message;
    box.textContent = message || "";
  }

  async function post(path, payload, boxId, button, busyLabel) {
    const original = button.textContent;
    button.disabled = true;
    button.textContent = busyLabel;
    showError(boxId, null);
    try {
      const res = await fetch(`${API}${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      storeToken(data.token);
      goBack();
    } catch (err) {
      // The server's own message names the field that is wrong, which is far
      // more use than a generic failure.
      showError(boxId, err.message || "Something went wrong. Please try again.");
      button.disabled = false;
      button.textContent = original;
    }
  }

  el("loginForm").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/auth/login",
      { email: val("loginEmail"), password: val("loginPassword") },
      "loginErrors", el("loginSubmitBtn"), "Signing in…");
  });

  el("registerForm").addEventListener("submit", (e) => {
    e.preventDefault();
    post("/auth/register", {
      full_name: val("regFullName"),
      gender: val("regGender"),
      mobile: val("regMobile"),
      email: val("regEmail"),
      password: val("regPassword"),
      address: val("regAddress"),
      pincode: val("regPincode"),
      state: "Maharashtra",
    }, "regErrors", el("regSubmitBtn"), "Creating account…");
  });

  el("tabLoginBtn").addEventListener("click", () => showTab("login"));
  el("tabRegisterBtn").addEventListener("click", () => showTab("register"));
  el("switchToRegister").addEventListener("click", () => showTab("register"));
  el("switchToLogin").addEventListener("click", () => showTab("login"));

  showTab(params.get("tab") === "register" ? "register" : "login");
})();
