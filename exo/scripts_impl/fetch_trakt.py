"""`exo fetch-trakt` — pull watched shows from the Trakt API into raw/.

The trakt cache was stale (2026-06-21) for the same reason every other
consumption source went stale: the data was fetched by the blog repo's ETL and
nothing in Exo refreshed it. Trakt has a real API and the tokens are already on
disk, so this is a wiring gap, not a source limitation. `daily-sync.sh` runs it
on the laptop beside `fetch-goodreads`; the result rides to CI in the raw/
tarball like every other export, so the nightly needs no Trakt secret.

Writes the same `{watched_at, shows}` file the blog fetcher wrote — the raw
`/sync/watched/shows?extended=full` answer, indent 4, unescaped — so
`loaders/tv.py` and the anime match in `loaders/titles.py` need no change and
the file is interchangeable with one either side wrote.

OAuth here is not decorative: access tokens last a week, so the refresh path
runs on most days, not as a rare branch. Refreshed tokens are written back with
mode 600 — they are credentials, and the file lives in the secrets dir
(`~/.config/exo/secrets/trakt-tokens.json`, or `TRAKT_TOKENS_FILE`), never in
raw/ where the tarball would carry them to CI. A refresh token Trakt no longer
honours (`invalid_grant`) cannot be recovered by code: run `exo trakt-auth`.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from .. import config

API = "https://api.trakt.tv"
CACHE = "trakt-cache.json"
PAGE_LIMIT = 100


class _Unauthorised(Exception):
    """Trakt refused the token and no refresh could fix it."""


def _tokens_path() -> Path:
    env = os.environ.get("TRAKT_TOKENS_FILE", "").strip()
    if env:
        return Path(os.path.expandvars(env)).expanduser()
    path = config.SECRETS / "trakt-tokens.json"
    # Read the pre-rename location until the file has moved, the same way
    # config reads the legacy env file.
    legacy = Path.home() / ".config" / "warehouse" / "secrets" / "trakt-tokens.json"
    return legacy if not path.exists() and legacy.exists() else path


def _load_tokens() -> dict:
    p = _tokens_path()
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save_tokens(tok: dict) -> None:
    p = _tokens_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Created 600 rather than chmod-ed after: the window between write and
    # chmod is a world-readable credential.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(tok, f, indent=4)
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
        if detail and exc.code != 401:
            print(f"  HTTP {exc.code}: {detail}")
        return exc.code, None


def _refresh(client_id: str, client_secret: str, refresh_token: str) -> dict | None:
    payload = {
        "refresh_token": refresh_token,
        "client_id": client_id,
        "redirect_uri": "urn:ietf:wg:oauth:2.0:oob",
        "grant_type": "refresh_token",
    }
    # Apps made on developer.trakt.tv (late 2026) are public PKCE clients and
    # are issued no secret at all; only legacy apps send one.
    if client_secret:
        payload["client_secret"] = client_secret
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{API}/oauth/token", data=body,
        headers={"Content-Type": "application/json", "User-Agent": "exo/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # The error body names the reason (`invalid_grant` etc.) and carries no
        # credential, so it is safe to show and saves a guess.
        reason = ""
        try:
            reason = (json.loads(exc.read() or b"{}") or {}).get("error", "")
        except Exception:
            pass
        print(f"  refresh failed (HTTP {exc.code}{', ' + reason if reason else ''})"
              " — re-authorise with `exo trakt-auth`")
        return None


class _Session:
    """One access token, refreshed at most once on the first 401."""

    def __init__(self, client_id: str, client_secret: str, tokens: dict):
        self.client_id = client_id
        self.client_secret = client_secret
        self.tokens = tokens
        self.refreshed = False

    def get(self, path: str) -> tuple[int, object]:
        status, data = _request(path, self.client_id, self.tokens.get("access_token", ""))
        if status != 401 or self.refreshed:
            return status, data
        self.refreshed = True
        print("  access token rejected — refreshing")
        if not self.tokens.get("refresh_token"):
            raise _Unauthorised("no refresh_token available")
        new = _refresh(self.client_id, self.client_secret, self.tokens["refresh_token"])
        if not new or not new.get("access_token"):
            raise _Unauthorised("refresh refused")
        _save_tokens(new)
        self.tokens = new
        return _request(path, self.client_id, new["access_token"])


def _read_cache(path: Path) -> tuple[list[dict], str, bool]:
    """`(shows, watched_at, readable)` for the existing cache.

    `readable` is False for a file that exists but will not parse: that must
    block the write just as a shrink does, since `had` would otherwise read 0
    and any answer at all would pass the guard.
    """
    if not path.exists():
        return [], "", True
    try:
        blob = json.loads(path.read_text(encoding="utf-8"))
        shows = blob.get("shows")
        return (shows if isinstance(shows, list) else []), str(blob.get("watched_at") or ""), True
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
        return [], "", False


def write_cache(path: Path, watched_at: str, shows: list[dict], *, had: int) -> None:
    """Write the cache, or raise ValueError.

    `/sync/watched/shows` is the whole history, so a full answer never has
    fewer shows than the last one: un-watching a show on trakt.tv is the only
    way to shrink it, and that is rare enough to re-run by hand. A smaller (or
    empty) 200 is a truncated response, a scope problem or a different account
    — and writing it would replace the record with it.
    """
    if not shows:
        raise ValueError("nothing fetched — refusing to overwrite the cache")
    if len(shows) < had:
        raise ValueError(f"{len(shows):,} shows, but the cache held {had:,} — "
                         "refusing to overwrite the cache with a shrunken fetch")
    tmp = path.with_suffix(path.suffix + ".tmp")
    # indent 4, unescaped: byte-for-byte the blog fetcher's json.dump, so the
    # file diffs cleanly whichever side last wrote it.
    tmp.write_text(json.dumps({"watched_at": watched_at, "shows": shows},
                              indent=4, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def seasons_from_history(plays: list[dict]) -> dict:
    """`{trakt show id: [{number, episodes: [{number, plays, last_watched_at}]}]}`
    from /sync/history/episodes rows — the shape /sync/watched/shows used to
    carry. Each history row is one play."""
    shows: dict = {}
    for h in plays:
        tid = ((h.get("show") or {}).get("ids") or {}).get("trakt")
        ep = h.get("episode") or {}
        if tid is None or ep.get("season") is None or ep.get("number") is None:
            continue
        e = shows.setdefault(tid, {}).setdefault(ep["season"], {}).setdefault(
            ep["number"], {"number": ep["number"], "plays": 0, "last_watched_at": ""})
        e["plays"] += 1
        e["last_watched_at"] = max(e["last_watched_at"], h.get("watched_at") or "")
    return {
        tid: [{"number": sn, "episodes": [eps[n] for n in sorted(eps)]}
              for sn, eps in sorted(seasons.items())]
        for tid, seasons in shows.items()
    }


def _history_seasons(session: "_Session") -> dict | None:
    rows: list = []
    for page in range(1, 401):      # 250/page: 100k plays before the cap
        status, chunk = session.get(f"/sync/history/episodes?page={page}&limit=250")
        if status != 200 or not isinstance(chunk, list):
            return None
        rows.extend(chunk)
        if len(chunk) < 250:
            return seasons_from_history(rows)
    return None


def run() -> int:
    client_id = os.environ.get("TRAKT_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TRAKT_CLIENT_SECRET", "").strip()
    if not client_id:
        print("fetch-trakt: set TRAKT_CLIENT_ID (TRAKT_CLIENT_SECRET only for legacy apps)")
        return 1
    tokens = _load_tokens()
    if not tokens.get("access_token"):
        print(f"fetch-trakt: no tokens at {_tokens_path()} — run `exo trakt-auth`")
        return 1

    out = config.EXPORTS / CACHE
    cached, cached_at, readable = _read_cache(out)
    if not readable:
        print(f"fetch-trakt: {out.name} exists but will not parse — refusing to overwrite it")
        return 1

    session = _Session(client_id, client_secret, tokens)
    try:
        # The cheap watermark first: one small call, and nothing else when
        # nothing new was watched — which is most days.
        status, act = session.get("/sync/last_activities")
        watched_at = ""
        if status == 200 and isinstance(act, dict):
            watched_at = (act.get("episodes") or {}).get("watched_at", "") or ""
        if watched_at and watched_at == cached_at and cached:
            print(f"  nothing new since {cached_at[:10]} — {len(cached):,} shows kept")
            return 0
        # Paged, always. Since late 2026 an unpaginated /sync/watched/shows
        # answers with page 1 only (100 shows) and no error, which the shrink
        # guard below caught as 358 -> 100 on the first run after the change.
        # Walk pages until one comes back short; the cap only stops a runaway.
        data: list = []
        status = 200
        for page in range(1, 51):
            status, chunk = session.get(
                f"/sync/watched/shows?extended=full&page={page}&limit={PAGE_LIMIT}")
            if status != 200 or not isinstance(chunk, list):
                break
            data.extend(chunk)
            if len(chunk) < PAGE_LIMIT:
                break
        # Seasons are gone from /sync/watched/shows (late 2026: no `seasons`
        # key, paged or not, `extended=full` or not), and the loader's episode
        # counts read them. Rebuild the old {number, episodes:[{number, plays,
        # last_watched_at}]} shape from the per-play history instead.
        if status == 200 and data and not any(s.get("seasons") for s in data):
            seasons = _history_seasons(session)
            if seasons is None:
                print("fetch-trakt: episode history failed — keeping the existing cache")
                return 1
            for s in data:
                tid = ((s.get("show") or {}).get("ids") or {}).get("trakt")
                s["seasons"] = seasons.get(tid, [])
    except _Unauthorised as exc:
        print(f"fetch-trakt: {exc} — run `exo trakt-auth`")
        return 1

    if status != 200 or not isinstance(data, list):
        print(f"fetch-trakt: fetch failed (HTTP {status}) — keeping the existing cache")
        return 1
    if not watched_at:
        # last_activities gives the true watermark; fall back to the newest row.
        watched_at = max((s.get("last_watched_at") or "") for s in data) if data else ""

    try:
        write_cache(out, watched_at, data, had=len(cached))
    except ValueError as exc:
        print(f"fetch-trakt: {exc}")
        return 1
    eps = sum(len(se.get("episodes") or []) for s in data for se in (s.get("seasons") or []))
    newest = max((s.get("last_watched_at") or "") for s in data)
    print(f"  {len(data):,} shows · {eps:,} episodes · newest {newest[:10]} -> {out.name}")
    return 0


def authorize() -> int:
    """`exo trakt-auth` — device-code flow, stdlib only.

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
    if not client_id:
        print("trakt-auth: TRAKT_CLIENT_ID must be set "
              "(TRAKT_CLIENT_SECRET too, for a legacy app that has one)")
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
            print("  https://developer.trakt.tv/apps/new and replace TRAKT_CLIENT_ID")
            print("  (new apps issue no secret; clear TRAKT_CLIENT_SECRET).")
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
        poll = {"code": code["device_code"], "client_id": client_id}
        if client_secret:
            poll["client_secret"] = client_secret
        st, tok = post("/oauth/device/token", poll)
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
