"""`wh` — drive the store.

    wh ingest [source...]   T0 loaders -> zones/t0/*.parquet   (default: all)
    wh ingest-notes [src]   a note source -> notes/raw/<src>/ (apple|files|notion; ADR-0017)
    wh index                T1: index the note trees + authored records -> zones/t1
    wh derive               T2: run registered derivations (reads via the wall)
    wh build                (re)build the DuckDB catalog views
    wh query "SQL"          run SQL across all zones (full profile)
    wh source "SQL"         run SQL across grounding zones only (the wall)
    wh search "text"        semantic note search (JSON) — embed query, cosine t2_note_vec
    wh sync-raw             pull MUST-STAY upstreams (blog _posts, second-brain vault) -> raw/
    wh publish [--cf]       build zones/_serve/ — the filtered projection that may leave (ADR-0005)
                                --cf also reshapes it into a Cloudflare bundle (D1 + R2 vectors)
    wh chatimport           file chat exports from ~/Downloads -> vault -> normalize -> t0_chat
    wh chatindex            project second-brain chat-logs -> t0_chat (grounds=false, walled)
    wh chatsearch "text"    semantic chat search (JSON) — locators, channel his|world|both
    wh views [--source]     list registered views
    wh item create "T"      Type A task spine (ADR-0002): create|status|done|list|glance
    wh rebuild              nuke derived + re-ingest + re-index + re-derive + build
    wh verify               prove T2 regenerates deterministically (byte-compare)
    exo backup              snapshot the record
"""
from __future__ import annotations

import argparse
import sys

from . import catalog, config, io, plugins
from .api import read, read_source, views as list_views


def _ingest(sources: list[str]) -> None:
    from .loaders import t0_loaders
    reg = t0_loaders()
    targets = sources or list(reg)
    for name in targets:
        contributors = reg.get(name)
        if not contributors:
            print(f"  ? unknown source {name}", file=sys.stderr)
            continue
        rows = []
        for who, loader in contributors:
            add = loader()
            rows += add
            # Per-contributor counts, always: a zone with five feeders that
            # reports one total cannot show you which feeder went to zero.
            if len(contributors) > 1:
                print(f"  t0/{name:10s} + {len(add):>5,} from {who}")
        path = io.write_zone("t0", name, rows)
        print(f"  t0/{name:10s} {len(rows):>7,} rows -> {path.name}")


def _index() -> None:
    from .loaders import t1_index
    t1_index.run()


def _derive() -> None:
    from . import t2
    t2.run_all()


def _build() -> None:
    names = catalog.build()
    print(f"  catalog: {len(names)} views -> {config.CATALOG}")


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    p = argparse.ArgumentParser(prog="exo", description="Exo — one record, write-scoped zones")
    sub = p.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("ingest"); sp.add_argument("source", nargs="*")
    sn = sub.add_parser("ingest-notes")
    sn.add_argument("source", nargs="?", default=None,
                    help="apple|files|notion|<plugin>; omit to run every [notes.sources] entry")
    sn.add_argument("--from", dest="src", default=None,
                    help="what to read: an export, a directory, a file, or - for stdin")
    sn.add_argument("--full", action="store_true",
                    help="re-read every note, ignoring what is already landed")
    sub.add_parser("index")
    sub.add_parser("derive")
    sub.add_parser("build")
    sq = sub.add_parser("query"); sq.add_argument("sql")
    sj = sub.add_parser("queryjson"); sj.add_argument("sql")
    ss = sub.add_parser("source"); ss.add_argument("sql")
    sv = sub.add_parser("views"); sv.add_argument("--source", action="store_true")
    snn = sub.add_parser("nn"); snn.add_argument("atom_id"); snn.add_argument("--k", type=int, default=6)
    ssr = sub.add_parser("search"); ssr.add_argument("query"); ssr.add_argument("--k", type=int, default=10)
    sub.add_parser("fetch-goodreads")
    sub.add_parser("fetch-lastfm")
    sub.add_parser("fetch-letterboxd")
    sub.add_parser("fetch-untappd")
    sub.add_parser("summarize-threads")
    sub.add_parser("fetch-collections")
    sub.add_parser("fetch-trakt")
    sub.add_parser("trakt-auth")
    # Whatever this instance's plugins add (ADR-0014 §3). Registered here so
    # `--help` lists them beside the built-ins rather than hiding them.
    for _name, (_fn, _help) in sorted(plugins.commands().items()):
        sub.add_parser(_name, help=_help)
    sin = sub.add_parser("init", help="scaffold a new instance")
    sin.add_argument("dest")
    sub.add_parser("scan-projects")
    ssy = sub.add_parser("sync-raw")
    spb = sub.add_parser("publish"); spb.add_argument("--dry-run", action="store_true"); spb.add_argument("--cf", action="store_true")
    ssy.add_argument("--posts", action="store_true"); ssy.add_argument("--vault", action="store_true")
    sci = sub.add_parser("chatimport"); sci.add_argument("--dry-run", action="store_true")
    sub.add_parser("chatindex")
    sub.add_parser("gardenindex")
    scap = sub.add_parser("capture"); scap.add_argument("title"); scap.add_argument("body")
    scap.add_argument("--now", default=None)
    # item spine (ADR-0002) — Type A tasks
    sit = sub.add_parser("item"); isub = sit.add_subparsers(dest="iaction", required=True)
    ic = isub.add_parser("create"); ic.add_argument("title")
    ic.add_argument("--done-when"); ic.add_argument("--due"); ic.add_argument("--parent")
    ic.add_argument("--est", type=int); ic.add_argument("--tag", action="append", default=[])
    ic.add_argument("--link", action="append", default=[]); ic.add_argument("--now", default=None)
    ist = isub.add_parser("status"); ist.add_argument("id"); ist.add_argument("to")
    idn = isub.add_parser("done"); idn.add_argument("id")
    il = isub.add_parser("list"); il.add_argument("--status", default=None)
    isub.add_parser("glance")
    # Type B habits
    ih = isub.add_parser("habit"); ih.add_argument("title"); ih.add_argument("--cadence", required=True)
    ih.add_argument("--block", type=int); ih.add_argument("--day-part", action="append", default=[])
    ih.add_argument("--tag", action="append", default=[]); ih.add_argument("--link", action="append", default=[])
    ih.add_argument("--seed", type=int); ih.add_argument("--now", default=None)
    icp = isub.add_parser("complete"); icp.add_argument("id"); icp.add_argument("--date", default=None)
    isub.add_parser("habits")
    isub.add_parser("migrate-habits")
    # Type C slot-fill
    isl = isub.add_parser("slot"); isl.add_argument("title"); isl.add_argument("--pile", required=True)
    isl.add_argument("--date", default=None); isl.add_argument("--constraint", action="append", default=[])
    isl.add_argument("--block", type=int); isl.add_argument("--day-part", action="append", default=[])
    isl.add_argument("--tag", action="append", default=[]); isl.add_argument("--now", default=None)
    icn = isub.add_parser("constraint"); icn.add_argument("rule")
    icn.add_argument("--severity", default="hard"); icn.add_argument("--scope", default="*")
    ifl = isub.add_parser("fill"); ifl.add_argument("id"); ifl.add_argument("ref")
    ifl.add_argument("--kind", default="recipe"); ifl.add_argument("--date", default=None)
    isl2 = isub.add_parser("slots"); isl2.add_argument("--status", default=None)
    isub.add_parser("constraints")
    sc = sub.add_parser("chatsearch"); sc.add_argument("query"); sc.add_argument("--k", type=int, default=10)
    sc.add_argument("--channel", choices=("his", "world", "both"), default="both")
    sub.add_parser("rebuild")
    sub.add_parser("verify")
    sub.add_parser("backup")
    args = p.parse_args(argv)

    if args.cmd == "ingest":
        _ingest(args.source); _build()
    elif args.cmd == "ingest-notes":
        from . import notes as notes_mod
        try:
            notes_mod.run(args.source, args.src, full=args.full)
        except PermissionError as e:
            # Apple Notes is the only source that can fail this way, and the
            # cause is never the path — it is that the process was not granted
            # Full Disk Access. Saying so beats reprinting errno.
            print(f"  cannot read the note database ({e}).\n  Apple Notes needs Full Disk "
                  "Access — run this from a terminal that has it.", file=sys.stderr)
            return 1
        except (notes_mod.SourceError, ValueError, FileNotFoundError) as e:
            # A source that cannot be read is the owner's problem to fix, not a
            # bug in the engine — one line, exit 1, no traceback.
            print(f"  {e}", file=sys.stderr)
            return 1
    elif args.cmd == "index":
        _index(); _build()
    elif args.cmd == "derive":
        _derive(); _build()
    elif args.cmd == "build":
        _build()
    elif args.cmd == "query":
        for row in read(args.sql):
            print(row)
    elif args.cmd == "queryjson":
        import json
        con = catalog.connect("full", read_only=True)
        try:
            cur = con.execute(args.sql)
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            con.close()
        print(json.dumps(rows, default=str))
    elif args.cmd == "source":
        for row in read_source(args.sql):
            print(row)
    elif args.cmd == "views":
        for v in list_views("source" if args.source else "full"):
            print(v)
    elif args.cmd == "nn":
        from .ask import nearest
        for h in nearest(args.atom_id, args.k):
            print(f"  {h['sim']:.4f}  {h['id']}  [{h['voice']}/{h['facet']}]  {h['text'][:70]}")
    elif args.cmd == "search":
        import json
        from .ask import search_notes
        print(json.dumps(search_notes(args.query, args.k), default=str))
    elif args.cmd == "trakt-auth":
        from .scripts_impl import fetch_trakt
        return fetch_trakt.authorize()
    elif args.cmd == "fetch-trakt":
        from .scripts_impl import fetch_trakt
        return fetch_trakt.run()
    elif args.cmd == "fetch-collections":
        from .scripts_impl import fetch_collections
        return fetch_collections.run()
    elif args.cmd == "fetch-goodreads":
        from .scripts_impl import fetch_goodreads
        return fetch_goodreads.run()
    elif args.cmd == "fetch-lastfm":
        from .scripts_impl import fetch_lastfm
        return fetch_lastfm.run()
    elif args.cmd == "fetch-letterboxd":
        from .scripts_impl import fetch_letterboxd
        return fetch_letterboxd.run()
    elif args.cmd == "fetch-untappd":
        from .scripts_impl import fetch_untappd
        return fetch_untappd.run()
    elif args.cmd == "summarize-threads":
        from .scripts_impl import summarize_threads
        return summarize_threads.run()
    elif args.cmd == "scan-projects":
        from .scripts_impl import scan_projects
        return scan_projects.run()
    elif args.cmd == "publish":
        from .scripts_impl import publish
        rc = publish.run(dry_run=args.dry_run)
        if rc == 0 and args.cf and not args.dry_run:
            from .scripts_impl import publish_cf
            rc = publish_cf.run()
        return rc
    elif args.cmd == "sync-raw":
        from .scripts_impl import sync_raw
        # bare `sync-raw` does both; a flag narrows to just that one
        both = not (args.posts or args.vault)
        return sync_raw.run(posts=both or args.posts, vault=both or args.vault)
    elif args.cmd == "chatimport":
        from .scripts_impl import chatimport
        return chatimport.run(dry=args.dry_run)
    elif args.cmd == "chatindex":
        from .loaders import chat
        chat.run(); _build()
    elif args.cmd == "gardenindex":
        from .loaders import garden
        garden.run()  # builds the catalog itself
    elif args.cmd == "capture":
        import json
        from .capture import write_capture
        print(json.dumps(write_capture(args.title, args.body, args.now)))
    elif args.cmd == "item":
        import json
        from . import item as it
        if args.iaction == "create":
            print(json.dumps(it.create_task(
                args.title, done_when=args.done_when, due=args.due, parent=args.parent,
                est_minutes=args.est, tags=args.tag, links=args.link, iso_now=args.now)))
        elif args.iaction == "status":
            print(json.dumps(it.set_status(args.id, args.to)))
        elif args.iaction == "done":
            print(json.dumps(it.done(args.id)))
        elif args.iaction == "list":
            print(json.dumps(it.list_tasks(args.status), default=str))
        elif args.iaction == "glance":
            print("\n".join(str(p) for p in (it.build_glance(), it.build_habit_glance(), it.build_slot_glance())))
        elif args.iaction == "habit":
            cadence = args.cadence.split(",") if "," in args.cadence else args.cadence
            print(json.dumps(it.create_habit(
                args.title, cadence, block_minutes=args.block, day_parts=args.day_part,
                tags=args.tag, links=args.link, seed_streak=args.seed, iso_now=args.now)))
        elif args.iaction == "complete":
            print(json.dumps(it.complete_habit(args.id, args.date)))
        elif args.iaction == "habits":
            print(json.dumps(it.list_habits(), default=str))
        elif args.iaction == "migrate-habits":
            from .scripts_impl import migrate_habits
            print(json.dumps(migrate_habits.run()))
        elif args.iaction == "slot":
            print(json.dumps(it.create_slot(
                args.title, args.pile, slot_date=args.date, constraints=args.constraint,
                block_minutes=args.block, day_parts=args.day_part, tags=args.tag, iso_now=args.now)))
        elif args.iaction == "constraint":
            print(json.dumps(it.create_constraint(args.rule, severity=args.severity, scope=args.scope)))
        elif args.iaction == "fill":
            print(json.dumps(it.fill_slot(args.id, args.ref, ref_kind=args.kind, date=args.date)))
        elif args.iaction == "slots":
            print(json.dumps(it.list_slots(args.status), default=str))
        elif args.iaction == "constraints":
            print(json.dumps(it.active_constraints(), default=str))
    elif args.cmd == "chatsearch":
        import json
        from .ask import search_chat
        print(json.dumps(search_chat(args.query, args.k, args.channel), default=str))
    elif args.cmd == "rebuild":
        from .scripts_impl import rebuild
        rebuild.run()
    elif args.cmd == "verify":
        from .scripts_impl import rebuild
        rebuild.verify()
    elif args.cmd == "init":
        from .scripts_impl import init
        return init.run(args.dest)
    elif args.cmd == "backup":
        from .scripts_impl import backup
        backup.run()
    elif args.cmd in plugins.commands():
        fn, _ = plugins.commands()[args.cmd]
        return fn() or 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
