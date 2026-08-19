"""`wh fetch-collections` — pull the collection inventories from their Sheets.

The collections are maintained in Google Sheets, not in files. Exo was
reading the blog repo's `_data/inventory/*.json`, which is another repo's *output* of
this same fetch — so the store sat one hop downstream of the truth and went stale
whenever that repo did not run. Coupling points into Exo; the fetch
belongs here.

Reads the sheet IDs from the environment and authenticates with the service
account already used for this (GOOGLE_SERVICE_ACCOUNT_FILE, relocated to
~/.config/exo/secrets/). Read-only scope.

Emits list-of-dicts keyed by the header row, matching the shape
`loaders/collection.py` already reads, so the loader is unchanged and the JSON in
raw/ stays the interchange format — the difference is who writes it.

Laptop-side, like fetch-goodreads: the result ships in the CI tarball, so the
nightly rebuild stays offline and CI needs no Google credentials.
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from .. import config

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]
API = "https://sheets.googleapis.com/v4/spreadsheets"

# env var holding the sheet id -> output file, and which worksheet to read
SHEETS = (
    ("RECORDS_SHEET_ID",   "records.json",   None),
    ("GAMES_SHEET_ID",     "games.json",     None),
    ("FRAGRANCE_SHEET_ID", "fragrance.json", "Own"),
)

# Sheet headers are Title Case; the loader reads snake_case. A couple do not
# survive mechanical conversion, so they are renamed explicitly rather than the
# loader learning two vocabularies.
RENAME = {
    "primary_genre": "genre",
    "year_released": "release_date",
}


def _creds():
    key = os.environ.get("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    if not key or not os.path.exists(key):
        raise SystemExit(
            "fetch-collections: set GOOGLE_SERVICE_ACCOUNT_FILE to the service "
            "account json (it lives in ~/.config/exo/secrets/)"
        )
    c = service_account.Credentials.from_service_account_file(key, scopes=SCOPES)
    c.refresh(Request())
    return c


def _values(token: str, sheet_id: str, worksheet: str | None) -> list[list]:
    rng = urllib.parse.quote(worksheet or "A:ZZ", safe="")
    url = f"{API}/{sheet_id}/values/{rng}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read()).get("values", [])


def _snake(h: str) -> str:
    out = "".join(c.lower() if c.isalnum() else "_" for c in h.strip())
    while "__" in out:
        out = out.replace("__", "_")
    return RENAME.get(out.strip("_"), out.strip("_"))


def _rows(values: list[list]) -> list[dict]:
    """Header row -> list of dicts in the vocabulary the loader reads.

    The sheet is richer than the JSON that used to stand in for it: records carry
    cost, retailer and location that the downstream copy dropped. Everything is
    kept — the loader picks what it needs and the rest stays available.
    """
    if not values:
        return []
    header = [_snake(str(h)) for h in values[0]]
    out = []
    for row in values[1:]:
        rec = {h: (row[i].strip() if i < len(row) and isinstance(row[i], str) else
                   (row[i] if i < len(row) else ""))
               for i, h in enumerate(header) if h}
        if any(v for v in rec.values()):
            out.append(rec)
    return out


def run() -> int:
    creds = _creds()
    config.INVENTORY.mkdir(parents=True, exist_ok=True)
    total = 0
    for env_var, filename, worksheet in SHEETS:
        sheet_id = os.environ.get(env_var, "").strip()
        if not sheet_id:
            print(f"  {filename:<16} skipped — {env_var} not set")
            continue
        try:
            rows = _rows(_values(creds.token, sheet_id, worksheet))
        except Exception as exc:
            # Never overwrite a good file with a failed fetch: the mirror in
            # raw/ is what CI rebuilds from, and an empty write would silently
            # empty a collection on the next nightly.
            print(f"  {filename:<16} FAILED ({exc}) — keeping the existing file")
            continue
        if not rows:
            print(f"  {filename:<16} returned nothing — keeping the existing file")
            continue
        (config.INVENTORY / filename).write_text(
            json.dumps(rows, indent=1), encoding="utf-8")
        print(f"  {filename:<16} {len(rows):>4} rows")
        total += len(rows)
    return 0 if total else 1
