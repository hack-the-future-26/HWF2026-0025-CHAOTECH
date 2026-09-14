"""
MOSDAC GSMaP-ISRO Rain Ingestion (Feature 6).

STATUS: FOUND, NOT CONFIRMED (REQUIRES ISRO SSO LOGIN)
------------------------------------------------------
Investigation conducted on 2026-09-14:
The GSMaP-ISRO Rain product is documented on MOSDAC:
    https://www.mosdac.gov.in/gsmap-isro-rain

However, attempting to access the bulk open data catalog:
    GET https://www.mosdac.gov.in/opendata/GSMaP_SAC_RAIN/
returns HTTP 302 redirecting to ISRO's Keycloak OpenID Connect SSO portal:
    Location: https://mosdac.gov.in/realms/Mosdac/protocol/openid-connect/auth?response_type=code&client_id=mosdac...

MOSDAC data downloads require an authenticated user account (Single Sign-On).
No unauthenticated public CSV or shapefile bulk endpoint exists.

GROUND RULE 4 ENFORCEMENT
-------------------------
"Never fabricate a result."
Per project rules, because the live bulk download cannot be completed without
personal ISRO SSO credentials, we do NOT ship a synthetic placeholder labeled
as real, and write zero fake rows to `mosdac_rainfall`.

Feature 6 is marked "found, not confirmed" (same honest state as the earlier
IMD Pune lead in SESSION_LOG_2026-09-12.md) and deferred until authenticated
MOSDAC API credentials can be provisioned.
"""

from __future__ import annotations

import sys
import requests

MOSDAC_GSMAP_URL = "https://www.mosdac.gov.in/gsmap-isro-rain"
MOSDAC_OPENDATA_URL = "https://www.mosdac.gov.in/opendata/GSMaP_SAC_RAIN/"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


def check_mosdac_access() -> tuple[bool, str]:
    """Verify live access to MOSDAC GSMaP open data endpoint."""
    try:
        session = requests.Session()
        session.headers.update(HEADERS)
        # Check opendata URL with redirect detection
        resp = session.get(MOSDAC_OPENDATA_URL, allow_redirects=False, timeout=10)
        if resp.status_code == 302 and "openid-connect" in resp.headers.get("Location", ""):
            return False, (
                f"MOSDAC endpoint requires ISRO Keycloak Single Sign-On (SSO): "
                f"redirects to {resp.headers.get('Location')[:80]}..."
            )
        if resp.status_code == 200:
            return True, "Open data endpoint accessible without login."
        return False, f"Unexpected response from MOSDAC: HTTP {resp.status_code}"
    except Exception as exc:
        return False, f"Connection to MOSDAC failed: {exc}"


def main() -> int:
    print("=" * 70)
    print("MOSDAC GSMaP-ISRO Rain Ingestion (Feature 6)")
    print("Status: Found, not confirmed (requires ISRO SSO credentials)")
    print("=" * 70)

    print(f"Testing live open data endpoint at {MOSDAC_OPENDATA_URL}...")
    accessible, reason = check_mosdac_access()
    print(f"Result: {reason}\n")

    print(
        "VERDICT (Ground Rule 4 — Never fabricate a result):\n"
        "  MOSDAC GSMaP bulk rain data is gated behind an ISRO user login.\n"
        "  No synthetic or estimated rainfall rows will be written to the database.\n"
        "  Feature 6 is deferred from this pass, marked 'found, not confirmed',\n"
        "  matching the earlier IMD Pune lead.\n"
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
