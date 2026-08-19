"""`wh fetch-trakt` — pull watched shows from the Trakt API into raw/.

The trakt cache was two months stale (2026-06-21) for the same reason every other
consumption source was stale this morning: the data was fetched once by another
system and nothing refreshed it. Trakt has a real API and the tokens are already
on disk, so this is a wiring gap, not a source limitation.

Writes the same `{watched_at, shows}` shape the cache already had, so
`loaders/tv.py` needs no change and the file keeps working if this never runs.

OAuth here is not decorative: the stored access token expired 2026-08-01, so the
refresh path is the first thing that executes, not a rare branch. Refreshed
tokens are written back with mode 600 — they are credentials, and the file lives
in ~/.config/warehouse/secrets/, never in raw/ where the tarball would carry them
to CI.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

API = "https://api.trakt.tv"


def _tokens_path() -> Path:
    env = os.environ.get("TRAKT_TOKENS_FILE")
    if env:
        return Path(env)
    return Path.home() / ".config" / "warehouse" / "secrets" / "trakt-tokens.json"


def _load_tokens() -> dict:
    p = _tokens_path()
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save_tokens(tok: dict) -> None:
    p = _tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(tok, indent=4), encoding="utf-8")
    os.chmod(p, 0o600)


def _request(path: str, client_id: str, access_token: str) -> tuple[int, object]:
    req = urllib.request.Request(
        f"{API}{path}",
        headers={
            "Content-Type": "application/json",
            "trakt-api-version": "2",
            "trakt-api-key": client_id,
            "Authorization": f"Bearer {access_token}",
            "User-Agent": "exo/1.0",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # Read the body. A bare status code cannot tell Trakt's answer from
        # Cloudflare's: without a User-Agent every request comes back 403
        # "error code: 1010" (bot signature block) having never reached Trakt,
        # which reads exactly like a permissions problem with the account.
        detail = ""
        try:
            detail = exc.read().decode()[:200].strip()
        except Exception:
            pass
        if detail:
            print(f"  HTTP {exc.code}: {detail}")
        return exc.code, None


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    body = json.dumps({
        "refresh_token": refresh_token,
        "client_id": client_id,
        "client_secret": client_secret,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "grant_type": "refresh_token",
    }).encode()
    req = urllib.request.Request(
        f"{API}/oauth/token", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "exo/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        print(f"  refresh failed ({exc.code}) — re-authorise Trakt by hand")
        return None


def run() -> int:
    client_id = os.environ.get("TRAKT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET", "").strip()
    if not client_id:
        print("fetch-trakt: set TRAKT_CLIENT_ID")
        return 1

    tokens = _load_tokens()
    access = tokens.get("access_token", "")

    status, data = _request("/sync/watched/shows", client_id, access)
    if status == 401:
        print("  access token rejected — refreshing")
        if not (client_secret and tokens.get("refresh_token")):
            print("  no refresh_token or client secret available")
            return 1
        new = _refresh(client_id, client_secret, tokens["refresh_token"])
        if not new:
            return 1
        _save_tokens(new)
        access = new["access_token"]
        status, data = _request("/sync/watched/shows", client_id, access)

    if status != 200 or not isinstance(data, list):
        print(f"  fetch failed: HTTP {status}")
        return 1

    # last_activities gives the true watermark; fall back to the newest row.
    _, act = _request("/sync/last_activities", client_id, access)
    watched_at = ""
    if isinstance(act, dict):
        watched_at = (act.get("episodes") or {}).get("watched_at", "") or ""
    if not watched_at:
        watched_at = max((s.get("last_watched_at") or "") for s in data) if data else ""

    out = config.EXPORTS / "trakt-cache.json"
    # Refuse to replace a good cache with an empty answer: a 200 carrying nothing
    # is far more likely to be an auth or scope problem than a wiped history.
    if not data:
        print("  API returned zero shows — keeping the existing cache")
        return 1
    out.write_text(json.dumps({"watched_at": watched_at, "shows": data}, indent=1),
                   encoding="utf-8")
    eps = sum(len(se.get("episodes") or []) for s in data for se in (s.get("seasons") or []))
    print(f"  {len(data):,} shows · {eps:,} episodes · watched_at {watched_at[:10]} -> {out.name}")
    return 0


def authorize() -> int:
    """`wh trakt-auth` — device-code flow, stdlib only.

    Needed because the stored refresh token came back `invalid_grant: session not
    found`, which is revocation rather than expiry: no refresh recovers it, the
    session has to be established again by a human approving in a browser.

    Implemented here rather than borrowed from the blog repo because ingestion is
    Exo's job, and that repo's env no longer runs (pipenv on 3.13, this
    machine on 3.14, `requests` absent). This needs nothing but urllib.

    Prints the code and waits. The approval happens on trakt.tv, in the owner's
    own browser and account — this only polls for the result.
    """
    import time

    client_id = os.environ.get("TRAKT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET", "").strip()
    if not (client_id and client_secret):
        print("trakt-auth: TRAKT_CLIENT_ID and TRAKT_CLIENT_SECRET must be set")
        return 1

    def post(path: str, payload: dict):
        # User-Agent is not optional: Trakt sits behind Cloudflare, which
        # rejects UA-less requests with 403 "error code: 1010" before they ever
        # reach the API.
        req = urllib.request.Request(
            f"{API}{path}", data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "User-Agent": "exo/1.0"},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:200].strip()
            except Exception:
                pass
            if body and e.code not in (400, 409):   # 400/409 are normal polling states
                print(f"  HTTP {e.code}: {body}")
            return e.code, None

    status, code = post("/oauth/device/code", {"client_id": client_id})
    if status != 200 or not code:
        print(f"trakt-auth: could not get a device code (HTTP {status})")
        if status == 401:
            print("  Trakt says the client does not exist. The application itself is")
            print("  gone, so no re-authorisation can help — create one at")
            print("  https://trakt.tv/oauth/applications/new and replace")
            print("  TRAKT_CLIENT_ID / TRAKT_CLIENT_SECRET.")
        elif status == 403:
            print("  403 with no JSON body is usually Cloudflare (error code 1010),")
            print("  not Trakt — check the User-Agent header is being sent.")
        return 1

    print()
    print("  ┌──────────────────────────────────────────────┐")
    print(f"  │  Go to:  {code['verification_url']:<35} │")
    print(f"  │  Enter:  {code['user_code']:<35} │")
    print("  └──────────────────────────────────────────────┘")
    print(f"  waiting up to {code['expires_in'] // 60} minutes...")

    interval = max(int(code.get("interval", 5)), 5)
    deadline = time.time() + int(code["expires_in"])
    while time.time() < deadline:
        time.sleep(interval)
        st, tok = post("/oauth/device/token", {
            "code": code["device_code"],
            "client_id": client_id,
            "client_secret": client_secret,
        })
        if st == 200 and tok:
            _save_tokens(tok)
            print(f"  authorized — tokens saved to {_tokens_path()} (mode 600)")
            return 0
        if st == 400:      # pending, keep polling
            continue
        if st == 409:
            print("  already approved")
            return 0
        if st in (404, 410, 418):
            print(f"  device code rejected or expired (HTTP {st}) — re-run")
            return 1
    print("  timed out waiting for approval")
    return 1
