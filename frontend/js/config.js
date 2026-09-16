/* ===========================================================================
   AwaazIQ — deployed backend URL, one place to set it.

   Every page resolves its API origin the same three ways, in order:
     1. an explicit ?api=... query param (always wins, e.g. for testing)
     2. window.AWAAZIQ_API_BASE, set below
     3. each page's own local-dev/same-origin fallback (unaffected by this
        file -- see app.js/auth.js/report.js/photo-lab.js)

   Local dev and the single-process ngrok phone demo (frontend + backend
   served from the same FastAPI process, see backend/main.py's /app mount)
   both already resolve correctly without this file doing anything, so it
   ships empty. Fill this in only when the frontend and backend are two
   separate deployments (e.g. frontend on Vercel, backend on Railway) --
   the one case where "same origin as the page" is no longer the backend.
   =========================================================================== */
window.AWAAZIQ_API_BASE = "";
