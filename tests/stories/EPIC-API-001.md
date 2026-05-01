# EPIC-API-001 — api/server.py Endpoint Contract Validation

```
Story ID:     EPIC-API-001
Module:       api/server.py
Title:        Validate /portfolio/execute-swap returns 401 (not 404) when
              unauthenticated, and /healthz returns expected shape

AS A:         mobile client developer
I WANT:       contract tests for the two most critical endpoint behaviors —
              auth rejection and health signaling — that run without a live
              server, launchd, or Cloudflare tunnel
SO THAT:      a refactor that accidentally removes auth middleware or renames
              a route is caught in CI before the mobile app sees a 404

GIVEN:        FastAPI TestClient wraps the app object imported from api/server.py
              no Authorization header is set on the request
              launchd is NOT involved — TestClient handles the ASGI lifecycle
WHEN:         POST /portfolio/execute-swap with body {"close_symbol":"X","open_symbol":"Y"}
THEN:         response status code is 401
AND:          response is NOT 404 (route exists; auth rejects before handler fires)
AND:          response body contains a machine-readable error key ({"detail": "..."})

WHEN:         GET /healthz (no auth required)
THEN:         response status code is 200
AND:          response body is JSON with key "pid" (int) and key "started" (str)
AND:          /healthz does not require Authorization header

ACCEPTANCE CRITERIA:
  AC1: unauthenticated POST /portfolio/execute-swap → 401, not 404, not 200
  AC2: 401 response body is valid JSON (not an HTML error page)
  AC3: GET /healthz → 200 with {"pid": <int>, "started": <str>}
  AC4: /healthz requires no Authorization header
  AC5: TestClient does not bind port 8000 — tests pass even if port is in use

DEFINITION OF DONE:
  □ Test written and passing
  □ Edge cases: malformed JSON body on execute-swap (still 401 — auth before body parse);
    /healthz with Accept: text/plain header (still returns JSON 200)
  □ No launchd, no Cloudflare, no kill $(lsof -ti:8000) in test setup
  □ Added to CI pipeline
  □ Product Owner signed off
```

## Architect's Notes

**NEW ENDPOINT REQUIRED:** `/healthz` does not currently exist. This story doubles
as a feature request. Add to `api/server.py`:
```python
import os
from datetime import datetime
_startup_time = datetime.utcnow().isoformat()

@app.get("/healthz")
async def healthz():
    return {"pid": os.getpid(), "started": _startup_time}
```
Cost: ~5 lines. Benefit: the launchd restart verification script can probe
`/healthz` instead of the auth-gated `/portfolio/execute-swap`, decoupling
health checking from business logic.

**Meta-test recommendation (AC6 follow-on):**
Enumerate all POST/PUT/DELETE routes via `app.routes` and assert each returns 401
when unauthenticated. Catches the class of bug where a developer adds a route and
forgets the auth dependency:
```python
def test_all_mutating_routes_require_auth(client):
    mutating_routes = [r for r in app.routes if hasattr(r, 'methods')
                       and r.methods & {'POST', 'PUT', 'DELETE'}
                       and r.path != '/healthz']
    for route in mutating_routes:
        method = next(iter(route.methods & {'POST', 'PUT', 'DELETE'}))
        resp = getattr(client, method.lower())(route.path, json={})
        assert resp.status_code == 401, f"{route.path} returned {resp.status_code}"
```

## Sprint Assignment

**Sprint 3** — Operational Excellence (API infrastructure)
