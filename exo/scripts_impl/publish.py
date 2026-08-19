"""`wh publish` — build the serve projection: the only bytes allowed to leave.

The remote read surface (ADR-0005) runs somewhere on the public internet. What
it can expose is decided HERE, by physical omission, not by a WHERE clause in a
tool. A filter applied at query time is a promise; a filter applied at publish
time means the held rows are simply not on the box, so no injected prompt and no
tool bug can reach them.

Two properties do the work:

  fail-closed   Every zone and every note folder must appear in serve-manifest.json.
                An unlisted one FAILS the build. A new `Therapy Notes/` folder can
                therefore never be published by forgetting about it.

  propagation   Holding a note folder is meaningless on its own: `t2_atom` carries
                note text VERBATIM (1,629 atoms trace to the held folders today),
                and the vector tables resolve back to titles. So holding a folder
                removes its notes, its atoms, and both vector tables' rows.

`origin_ref` is the join key throughout, because `t1_notes.id` is NOT unique
(3,444 rows, 1,984 distinct ids — two ids cover 1,462 rows between them).
`t2_note_vec` has no origin_ref and can only be keyed by that non-unique id, so
publish ASSERTS that no id spans both a held and a served note before trusting it.
"""
from __future__ import annotations

import hashlib
import json
import shutil

from .. import catalog, config

# Zones filtered by note provenance rather than copied wholesale.
_NOTE_DERIVED = {"t1_notes", "t2_atom", "t2_atom_vec", "t2_note_vec"}


def _load_manifest() -> dict:
    with open(config.SERVE_MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def _hash_ref(ref: str) -> str:
    """Stable surrogate for origin_ref.

    Filenames leak on their own — `2022-04-15-relationship-standards.md` states
    its subject with no body attached. Hashing keeps notes/atoms/vectors joinable
    downstream while publishing no paths.
    """
    return hashlib.sha256(ref.encode("utf-8")).hexdigest()[:16]


def _check_coverage(con, manifest: dict) -> list[str]:
    """Fail-closed: every live zone and folder must carry an explicit decision."""
    problems: list[str] = []

    live_zones = {
        v for (v,) in con.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_type='VIEW'"
        ).fetchall()
    }
    undeclared = sorted(live_zones - set(manifest["zones"]))
    if undeclared:
        problems.append(
            "zones present in the catalog but absent from serve-manifest.json: "
            + ", ".join(undeclared)
        )

    live_folders = {
        f for (f,) in con.execute("SELECT DISTINCT folder FROM t1_notes").fetchall()
    }
    undeclared_f = sorted(live_folders - set(manifest["note_folders"]))
    if undeclared_f:
        problems.append(
            "note folders with no decision in serve-manifest.json: "
            + ", ".join(repr(x) for x in undeclared_f)
        )

    # Second axis: every note must sit under a declared gradient zone.
    zones = manifest["path_zones"]
    orphan = sorted({
        r for (r,) in con.execute("SELECT DISTINCT origin_ref FROM t1_notes").fetchall()
        if _zone_of(r, zones) is None
    })
    if orphan:
        sample = ", ".join(orphan[:3]) + (f" (+{len(orphan) - 3} more)" if len(orphan) > 3 else "")
        problems.append(
            f"{len(orphan)} note(s) under no declared path_zone: {sample}"
        )
    return problems


def _has_column(con, view: str, col: str) -> bool:
    """An empty zone is written with the envelope only, so its payload columns do
    not exist. A publish rule that names one then dies on a Binder Error takes
    the whole nightly down because an upstream had nothing to say — which is a
    normal Tuesday, not a failure."""
    try:
        cols = {c[1] for c in con.execute(f'PRAGMA table_info("{view}")').fetchall()}
        return col in cols
    except Exception:
        return False


def _zone_of(ref: str, zones: dict) -> str | None:
    """Longest declared prefix wins, so raw/import can serve while raw/daily holds."""
    best = None
    for z in zones:
        if ref.startswith(z + "/") and (best is None or len(z) > len(best)):
            best = z
    return best


def _held_refs(con, held_folders: list[str]) -> set[str]:
    if not held_folders:
        return set()
    placeholders = ", ".join("?" for _ in held_folders)
    return {
        r for (r,) in con.execute(
            f"SELECT origin_ref FROM t1_notes WHERE folder IN ({placeholders})",
            held_folders,
        ).fetchall()
    }


def _shingles(text: str, k: int = 12) -> set[str]:
    """Normalized k-word shingles — the unit of 'this passage is the same passage'."""
    w = text.split()
    return {" ".join(w[i:i + k]).lower() for i in range(max(0, len(w) - k + 1))}


def _content_guard(con, held_refs: set[str], served_folders: list[str],
                   overlap: float = 0.30) -> tuple[set[str], set[str]]:
    """Find served rows that reproduce held text.

    Folder is where a note FILED, not where its content went. The vault versions
    and copies across folders (`…ambient-music.md` in a held folder, `…-v2.md` in
    a served one), so a folder filter alone republishes held passages under a
    served note's name. This measures shingle overlap against every held body and
    reports the served refs and atom ids that have to go with them.

    Returns (served note origin_refs to drop, served atom ids to drop).
    """
    # Keyed on the merged held SET, not on held_folders. Holding is now decided
    # by three things — folder, gradient zone, and this guard — and shingling
    # only the folder-held bodies left the other two unguarded: a
    # refined/unshared note (held by path) reproduced inside a served note was
    # invisible here, which is precisely the case the guard was built for.
    held_text = con.execute(
        "SELECT body FROM t1_notes WHERE list_contains(?::VARCHAR[], origin_ref)",
        [sorted(held_refs) or [""]],
    ).fetchall()
    held = set()
    for (b,) in held_text:
        held |= _shingles(b or "")
    if not held:
        return set(), set()

    drop_notes: set[str] = set()
    for ref, body in con.execute(
        "SELECT origin_ref, body FROM t1_notes WHERE list_contains(?::VARCHAR[], folder)",
        [served_folders],
    ).fetchall():
        sh = _shingles(body or "")
        if sh and len(sh & held) / len(sh) >= overlap:
            drop_notes.add(ref)

    drop_atoms: set[str] = set()
    for aid, text in con.execute("SELECT id, text FROM t2_atom").fetchall():
        sh = _shingles(text or "")
        if sh and len(sh & held) / len(sh) >= overlap:
            drop_atoms.add(aid)

    return drop_notes, drop_atoms


def run(dry_run: bool = False) -> int:
    manifest = _load_manifest()
    zones = manifest["zones"]
    folders = manifest["note_folders"]
    path_zones = manifest["path_zones"]
    served_zones = [z for z, d in zones.items() if d == "serve"]
    held_folders = [f for f, d in folders.items() if d == "hold"]
    served_folders = [f for f, d in folders.items() if d == "serve"]

    con = catalog.connect("full", read_only=True)
    try:
        problems = _check_coverage(con, manifest)
        if problems:
            print("publish: REFUSING — the manifest does not cover everything:")
            for p in problems:
                print(f"  - {p}")
            print("  (fail-closed: classify each as 'serve' or 'hold', then re-run)")
            return 1

        held = _held_refs(con, held_folders)

        # Axis two: gradient position. A note publishes only if BOTH axes agree.
        # Folder is an organisational accident — notes move between folders and
        # their content migrates across them. Path zone is the semantic claim the
        # vault's CONTEXT.md makes promises about ("refined/unshared never
        # leaves"), so it gets its own independent veto rather than relying on a
        # folder decision that happens to coincide.
        zone_held = {
            r for (r,) in con.execute("SELECT origin_ref FROM t1_notes").fetchall()
            if path_zones.get(_zone_of(r, path_zones) or "", "hold") == "hold"
        }
        by_zone_only = zone_held - held
        held |= zone_held

        dup_notes, dup_atoms = _content_guard(con, held, served_folders)
        held |= dup_notes

        # t2_note_vec can only be keyed by the non-unique note id, and this must
        # be checked against the FINAL held set. Checking only held_folders left
        # the path axis and this guard unprotected: a held note sharing an id
        # with a served one still satisfied the served-side subquery, so its
        # embedding published. The assertion is the last thing before writing,
        # so every source of holding is already merged into `held`.
        spanning = con.execute(
            """
            SELECT count(*) FROM (
              SELECT id FROM t1_notes GROUP BY id
              HAVING count(DISTINCT CASE WHEN list_contains(?::VARCHAR[], origin_ref)
                                          THEN 1 ELSE 0 END) > 1
            )
            """,
            [sorted(held) or [""]],
        ).fetchone()[0]
        if spanning:
            print(
                f"publish: REFUSING — {spanning} note id(s) span both held and served "
                "notes. t2_note_vec is keyed by that id, so publishing would leak."
            )
            return 1

        # A zone can be declared serve before it has ever been built — a new
        # loader lands, the manifest names it, and the t2 pass that fills it has
        # not run yet. Without this, `SELECT * FROM <view>` throws and the whole
        # projection dies over a zone that is merely early. Skipping publishes
        # LESS, which is the safe direction, but say so: a silently absent zone
        # looks identical to one that was never wanted.
        existing = {
            v for (v,) in con.execute(
                "SELECT view_name FROM duckdb_views() WHERE NOT internal"
            ).fetchall()
        }
        missing = [z for z in served_zones if z not in existing]
        if missing:
            print(f"  not built yet, skipping: {', '.join(sorted(missing))}")
            served_zones = [z for z in served_zones if z in existing]

        print(f"publish: {len(served_zones)} zones serve · {len(held_folders)} folders held "
              f"· {len(held)} notes held")
        if by_zone_only:
            print(f"  path axis: {len(by_zone_only)} note(s) held by gradient zone "
                  "that the folder axis would have published")
        if dup_notes or dup_atoms:
            print(f"  content guard: +{len(dup_notes)} notes, {len(dup_atoms)} atoms "
                  "reproduce held text under a served folder")

        if dry_run:
            print("  (dry run — nothing written)")
            return 0

        # Built aside, then swapped in. Writing straight into SERVE meant the
        # projection was destroyed before its replacement existed, so anything
        # that failed downstream — a brief naming a zone the manifest had just
        # retracted, a loader that had not run — left no projection at all
        # rather than yesterday's. The failure window was the whole build.
        #
        # It also took `cf/` with it. The Cloudflare bundle lives INSIDE this
        # directory, so a plain `wh publish` deleted schema.sql, data/, the
        # vectors and import.sh — every input the D1 load and the worker's test
        # harness need — without mentioning it.
        final = config.SERVE
        out = final.parent / (final.name + ".building")
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True, exist_ok=True)

        held_list = sorted(held)
        summary: list[tuple[str, int]] = []

        for zone in served_zones:
            dest = out / f"{zone}.parquet"

            if zone == "t1_notes":
                sql = f"""
                    SELECT * EXCLUDE (origin_ref),
                           {_hash_expr('origin_ref')} AS origin_ref
                    FROM t1_notes
                    WHERE list_contains(?::VARCHAR[], folder)
                      AND NOT list_contains(?::VARCHAR[], origin_ref)
                """
                params = [served_folders, sorted(held) or [""]]
            elif zone in ("t2_atom",):
                sql = f"""
                    SELECT * EXCLUDE (origin_ref),
                           {_hash_expr('origin_ref')} AS origin_ref
                    FROM t2_atom
                    WHERE NOT list_contains(?::VARCHAR[], origin_ref)
                      AND NOT list_contains(?::VARCHAR[], id)
                """
                params = [held_list or [""], sorted(dup_atoms) or [""]]
            elif zone == "t1_draft" and not _has_column(con, "t1_draft", "title"):
                # An empty zone writes a parquet carrying ONLY the provenance
                # columns, so every payload column vanishes and any reader
                # naming one dies with "no such column". t0_event learned this
                # already; drafts hit it immediately, because zero drafts is the
                # normal state of a writer between pieces rather than an edge
                # case. Emit the shape explicitly so the schema is the same on
                # the day writing starts and the day it has finished.
                sql = """
                    SELECT *,
                           NULL::VARCHAR AS slug,    NULL::VARCHAR AS title,
                           NULL::VARCHAR AS description, NULL::VARCHAR AS started,
                           NULL::VARCHAR AS modified, NULL::BIGINT AS words,
                           NULL::VARCHAR AS state,   NULL::VARCHAR AS body
                    FROM t1_draft
                """
                params = []
            elif zone == "t2_affinity" and _has_column(con, "t2_affinity", "score"):
                # `score` is plays + mentions * 500. That 500 is a preference
                # weight, and it is the only one that ever reached served data
                # (ADR-0013: this surface ranks by relevance, never by
                # preference). Nothing read it — `taste` orders by plays — so it
                # was a tripwire rather than a feature. The column stays in the
                # store, where affinity remains a worked cross-zone example.
                sql = "SELECT * EXCLUDE (score) FROM t2_affinity"
                params = []
            elif zone == "t1_taste":
                # Row-level hold inside a served zone: the allergy and
                # Constraint rows (allergies, never-ingredients) carry
                # sensitive=true and WERE held. Decision reversed 2026-08-18:
                # they publish. An assistant that recommends a restaurant or a
                # recipe without them is not safer for the omission — it is
                # confidently wrong in the one direction that can hurt. The flag
                # stays as the semantic marker; publication is now the explicit
                # decision the loader asked for, not an inherited default.
                sql = "SELECT * FROM t1_taste"
                params = []
            elif zone == "t2_atom_vec":
                sql = """
                    SELECT v.* FROM t2_atom_vec v
                    JOIN t2_atom a ON a.id = v.id
                    WHERE NOT list_contains(?::VARCHAR[], a.origin_ref)
                      AND NOT list_contains(?::VARCHAR[], v.id)
                """
                params = [held_list or [""], sorted(dup_atoms) or [""]]
            elif zone == "t2_note_vec":
                sql = """
                    SELECT v.* FROM t2_note_vec v
                    WHERE v.id IN (
                        SELECT id FROM t1_notes
                        WHERE list_contains(?::VARCHAR[], folder)
                          AND NOT list_contains(?::VARCHAR[], origin_ref)
                    )
                """
                params = [served_folders, sorted(held) or [""]]
            elif zone == "t1_recipe" and _has_column(con, "t1_recipe", "is_seed"):
                # The seed pool is synthetic — machine-generated combinations
                # with no title, carrying is_seed=true. It exists so the eating
                # vertical has something to rank against; it is not real cooking.
                # Published, it was 50 invented recipes against 10 real ones, and
                # an assistant asked what the owner cooks would mostly have been told
                # about food that does not exist. The store keeps them; the
                # surface does not.
                sql = "SELECT * FROM t1_recipe WHERE NOT COALESCE(is_seed, false)"
                params = []
            elif zone == "t0_chat":
                # BOTH sides now. One side alone was a distorted record: 957 of
                # 2,136 are under 80 chars, and most of those are questions or
                # redirects — "so not the f'(x) kind of derivative?" means
                # nothing without the turn it answers. A reader cannot derive the
                # other half; it can only guess, which is worse than showing it.
                #
                # Still excluded: `vec` (10k embeddings, no reader, would triple
                # the bundle) and the held thread titles, which stay held here
                # exactly as in t0_chat_topic.
                #
                # Never embedded. The assistant's prose is retrievable as context
                # inside one thread, but it does not enter any vector space, so
                # search keeps returning the owner's words rather than a model's.
                ct = manifest.get("chat_topics", {})
                # Windowed with t0_chat_topic, not just held-title filtered. The
                # window is a publication decision and it was applied to one of
                # the two chat tables: 34 turns across 4 threads shipped whose
                # topic row had aged out, three of them on keeping secrets. No
                # tool reached them — `thread` resolves through t0_chat_topic
                # first — but that is the tool layer protecting a row, which is
                # exactly the trust this file exists to avoid needing. Worse,
                # they were invisible to review, because what is served is read
                # off t0_chat_topic.
                window = int(ct.get("window_days", 90))
                sql = f"""
                    SELECT * EXCLUDE (vec)
                    FROM t0_chat
                    WHERE NOT list_contains(?::VARCHAR[], title)
                      AND title IN (
                          SELECT title FROM t0_chat_topic
                          WHERE last_seen >= strftime(current_date - INTERVAL {window} DAY, '%Y-%m-%d')
                      )
                """
                params = [list(ct.get("hold_titles", [])) or [""]]
            elif zone == "t0_chat_topic" and _has_column(con, "t0_chat_topic", "last_seen"):
                # Attach the machine distillation. The columns are emitted
                # ALWAYS, null when no cache exists — a schema that changes
                # depending on whether a model has run yet is how "no such
                # column: summary" reaches production the first night a token is
                # missing. Same failure the empty-zone payload columns caused.
                ct = manifest.get("chat_topics", {})
                window = int(ct.get("window_days", 90))
                hold_titles = list(ct.get("hold_titles", [])) or [""]
                # exists() is not enough. A failed R2 fetch leaves a zero-byte
                # file that passes exists() and then dies inside DuckDB with
                # "Invalid Input Error" partway through publishing — a missing
                # optional input taking down the whole nightly.
                cache = config.CACHE / "thread_summary.parquet"
                usable = cache.exists() and cache.stat().st_size > 0
                if usable:
                    try:
                        con.execute(f"SELECT 1 FROM read_parquet('{cache}') LIMIT 1")
                    except Exception as exc:
                        print(f"  thread summaries unreadable ({str(exc)[:60]}) — publishing without")
                        usable = False
                if usable:
                    src = f"""
                        SELECT t.*, s.summary AS summary, s.model AS summary_by
                        FROM t0_chat_topic t
                        LEFT JOIN read_parquet('{cache}') s ON s.title = t.title
                    """
                else:
                    src = """
                        SELECT t.*, NULL::VARCHAR AS summary, NULL::VARCHAR AS summary_by
                        FROM t0_chat_topic t
                    """
                sql = f"""
                    SELECT * FROM ({src})
                    WHERE last_seen >= strftime(current_date - INTERVAL {window} DAY, '%Y-%m-%d')
                      AND NOT list_contains(?::VARCHAR[], title)
                """
                params = [hold_titles]
            elif zone == "t0_event" and _has_column(con, "t0_event", "start"):
                # Candidate events are a utility, not a record. They describe the
                # world rather than the owner, they age out on a fixed date, and the
                # pool refreshes daily — so a past event is pure payload with no
                # reader. The store keeps whatever the sources still list; the
                # projection carries only what is still actionable.
                # An empty zone still writes a parquet, but only with the
                # provenance columns — naming `start` then throws and takes the
                # whole publish down. An upstream that was not mirrored is a
                # normal state, not a reason to publish nothing.
                # Deduped on (title, start, venue): the library feed lists the
                # same session twice — two identical 'Family Story Time' rows at
                # 14:00, same venue — so the pool carried 12 phantom options.
                sql = """
                    SELECT * FROM (
                        SELECT *, row_number() OVER (
                            PARTITION BY lower(COALESCE(title,'')), COALESCE(start,''),
                                         lower(COALESCE(venue,''))
                            ORDER BY id
                        ) AS _rn
                        FROM t0_event
                        WHERE substr(start, 1, 10) >= strftime(current_date, '%Y-%m-%d')
                    ) WHERE _rn = 1
                """
                # EXCLUDE the helper: a window column left in the projection
                # ships a meaningless "_rn: 1" to every consumer and becomes a
                # D1 column nobody can explain.
                sql = sql.replace("SELECT * FROM (", "SELECT * EXCLUDE (_rn) FROM (", 1)
                params = []
            else:
                sql = f"SELECT * FROM {zone}"
                params = []

            con.execute(
                f"COPY ({sql}) TO '{dest}' (FORMAT PARQUET)", params
            )
            n = con.execute(
                f"SELECT count(*) FROM read_parquet('{dest}')"
            ).fetchone()[0]
            summary.append((zone, n))
            marker = "  (filtered)" if zone in _NOTE_DERIVED else ""
            print(f"  {zone:<18} {n:>8,}{marker}")

        history = _update_surface_log(summary)

        # The brief reads the projection we just wrote — never the store — so it
        # cannot carry held material even by mistake.
        from . import brief
        text = brief.build(dict(summary), history=history, src=out)
        (out / "brief.md").write_text(text, encoding="utf-8")
        print(f"  brief             {len(text.encode()):>8,} bytes")

        _write_receipt(out, manifest, held_folders, summary)

        # Carried across rather than rebuilt: `cf/` is derived from the parquets
        # this run just replaced, so it survives as a working bundle but a STALE
        # one. Say so — a stale bundle that imports quietly is worse than an
        # absent one, and `import.sh` verifies against counts shipped inside the
        # bundle, so it cannot notice that the projection moved underneath it.
        old_cf = final / "cf"
        if old_cf.is_dir():
            shutil.move(str(old_cf), str(out / "cf"))
            print("  cf/ bundle kept, and is now STALE — run `wh publish --cf` before importing")

        # Swap. rename is atomic per step, so the worst interruption leaves the
        # previous projection under .previous rather than nothing under either.
        previous = final.parent / (final.name + ".previous")
        if previous.exists():
            shutil.rmtree(previous)
        if final.exists():
            final.rename(previous)
        out.rename(final)
        shutil.rmtree(previous, ignore_errors=True)
        return 0
    finally:
        con.close()


def _hash_expr(col: str) -> str:
    """SHA-256 the ref in-database, truncated to 16 hex chars (matches _hash_ref)."""
    return f"substr(sha256({col}), 1, 16)"


def _update_surface_log(summary: list[tuple[str, int]]) -> list[dict]:
    """Record when each zone first appeared on the surface.

    A client caches tools/list at connect, so a tool added afterwards is
    invisible to it until it reconnects — which is a thing to avoid asking for.
    The brief is a resource and is re-read, so it is where news belongs. Derived
    rather than hand-written: a changelog someone has to remember to update is a
    changelog that is wrong by the second week.

    Append-only and outside the projection, so a rebuild cannot forget history.
    """
    import datetime as _dt
    path = config.LEDGER / "surface-log.json"
    log = {}
    if path.exists():
        try:
            log = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            log = {}
    today = _dt.date.today().isoformat()

    # First run establishes a baseline instead of announcing that all 22 zones
    # are new. "Everything arrived today" is true of the log and false of the
    # surface, and a changelog that cries new about everything is ignored by the
    # second read.
    first_run = "_baseline" not in log
    if first_run:
        log["_baseline"] = today
    for zone, _ in summary:
        log.setdefault(zone, today)

    config.LEDGER.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=1, sort_keys=True), encoding="utf-8")

    baseline = log["_baseline"]
    return [
        {"zone": z, "since": d}
        for z, d in sorted(log.items(), key=lambda kv: kv[1], reverse=True)
        if z != "_baseline" and d > baseline
    ]


def _write_receipt(out, manifest, held_folders, summary) -> None:
    receipt = {
        "content_guard": "served rows reproducing held text are dropped (>=30% 12-word shingle overlap)",
        "held_folders": sorted(held_folders),
        "held_zones": sorted(z for z, d in manifest["zones"].items() if d == "hold"),
        "published": {z: n for z, n in summary},
        "origin_ref": "sha256[:16] — no filenames published",
    }
    with open(out / "_receipt.json", "w", encoding="utf-8") as f:
        json.dump(receipt, f, indent=2)
    print(f"  receipt -> {out / '_receipt.json'}")
