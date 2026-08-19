"""`wh publish` — the standing brief: what an assistant reads before you speak.

Everything else on the read surface is *pull*: a tool answers a question someone
thought to ask. That cannot solve the problem this whole surface exists for —
"I forget things exist unless I'm reminded" — because it requires already knowing
what to ask for. It fails the same way on the model's side: an assistant facing
unlabelled tools over an unknown corpus mostly does not call them.

So the surface leads with a *push*: one small artifact, published as an MCP
resource, that a client loads into context unconditionally. It says who this
person is, what they are currently circling, how stale each zone is, and — the
part that does for the assistant what the assistant is meant to do for the
owner — what it can ask for next.

Two properties are structural, not stylistic:

  composed from the PROJECTION, never the store. Every figure here is read from
  zones/_serve/*.parquet, so held material is unreachable by construction rather
  than by care. This is the most exposed artifact in the system — pushed to every
  client, unconditionally, without anyone asking — so it gets the strongest
  guarantee available, which is physical absence of the alternative.

  an ARRANGEMENT of the owner's own words. Verdicts and questions are quoted
  verbatim; the machine supplies structure and counts, never prose about them.
  Same rule the vault runs on: the machine only ever rearranges your words.

A stale brief is worse than none, because it asserts confidently. Hence generated
every publish, never hand-maintained, and stamped with what it knows.
"""
from __future__ import annotations

import datetime as _dt
import json
import pathlib as _pathlib

import duckdb

from .. import config

MAX_BYTES = 9000  # a brief that does not fit in a system prompt is not a brief
# 6000 held until the repos joined the surface, then 7000 until the brief reached
# 6,799 of it — 201 bytes of headroom, with a blog section still to land. The
# number was always soft (it is sized to a system prompt, not to a protocol
# limit) and the pile is not compressible into slack: naming what the owner is
# building costs a section and a line in the index.
#
# The cap is also no longer a slice off the end. It was, and the end is where the
# freshness stamp and the do-not-infer-from-absence instruction live, so the
# first thing a too-long brief dropped was the part that says how far to trust
# it. Those two are assembled separately below and always survive; the growing
# middle is what gets clipped, and it says so.



def _one(con, sql: str, *params, default=0):
    """One cell, or a default when the zone is empty.

    `_q` already tolerates an empty zone by returning no rows — and then every
    caller wrote `[0][0]` and turned that tolerance into an IndexError. The
    brief must survive an instance that has not ingested beer yet.
    """
    rows = _q(con, sql, *params)
    if not rows or rows[0][0] is None:
        return default
    return rows[0][0]

def _blog_host() -> str:
    """The blog's hostname, for prose. Derived from the instance's URL template
    rather than configured twice — two spellings of one fact drift."""
    t = config.BLOG_URL_TEMPLATE
    return t.split("//", 1)[-1].split("/", 1)[0] if t else "the blog"


def _q(con, sql: str, *params):
    """Query, tolerating zones that are empty.

    A zone with no rows still writes a parquet, but only with the provenance
    columns — no payload columns at all. Naming one then raises a BinderException
    and takes the whole brief down. A source being empty (an upstream not
    mirrored, a loader skipped) is a normal state, not a reason to publish
    nothing, so treat it as "this section has nothing to say".
    """
    try:
        return con.execute(sql, list(params)).fetchall()
    except Exception as e:
        msg = str(e)
        # Two ways a zone has nothing to say, and the guard used to cover one.
        # Empty: the parquet exists with provenance columns only, so naming a
        # payload column is a Binder Error. Absent: there is no parquet at all —
        # the zone was flipped to `hold`, or it is declared but not built yet and
        # publish skipped it. The second raised IO Error straight through here,
        # so retracting a zone in the manifest crashed the publish that was
        # carrying out the retraction, after the projection had been rebuilt.
        if "not found in FROM clause" in msg or "No files found that match the pattern" in msg:
            return []
        raise


def _clip(text: str, n: int) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def build(served_counts: dict[str, int] | None = None,
          history: list[dict] | None = None,
          src: "_pathlib.Path | None" = None) -> str:
    # `src` because publish now builds into a staging directory and swaps it in:
    # reading config.SERVE during a staged build would compose the brief from the
    # PREVIOUS projection, stamping today's date on yesterday's counts. Defaults
    # to the live projection for anyone calling this directly.
    srv = src or config.SERVE
    con = duckdb.connect(":memory:")

    def P(name: str) -> str:
        return f"read_parquet('{srv}/{name}.parquet')"

    out: list[str] = []
    A = out.append

    A(f"# {config.OWNER} — standing context")
    A("")
    A(f"Generated from {config.OWNER_POSSESSIVE} own records. Quoted lines are "
      f"{config.OWNER_POSSESSIVE} words verbatim; counts and ordering are "
      f"mechanical. Nothing here is written *about* {config.OWNER}.")
    A("")

    # ── hard constraints, before anything else ────────────────────────────────
    # Above taste deliberately, and never clipped. Everything else in this brief
    # is preference — things the owner leans toward, which an assistant may weigh
    # against each other. These are not that. Clipping a severe allergy mid-list
    # would drop an avoided cuisine while still reading as complete, and the cost
    # of that error is not symmetrical with the cost of a dull suggestion.
    cons = _q(con, f"""
        SELECT key, value FROM {P('t1_taste')}
        WHERE kind = 'constraint'
        ORDER BY CASE WHEN key = 'allergies' THEN 0 ELSE 1 END, key
    """)
    if cons:
        A("## Hard constraints — not preferences")
        A("Conditions any suggestion must satisfy to be worth making. Do not "
          "weigh these against anything below.")
        for key, value in cons:
            try:
                parsed = json.loads(value) if isinstance(value, str) else value
            except Exception:
                A(f"- {key}: {value}")
                continue

            if key == "allergies":
                for a in parsed if isinstance(parsed, list) else [parsed]:
                    if not isinstance(a, dict):
                        A(f"- **Allergy**: {a}")
                        continue
                    sev = a.get("severity", "")
                    warn = " · always warn" if a.get("alwaysWarn") else ""
                    A(f"- **Allergy — {a.get('allergen', 'unknown')}** "
                      f"({sev}{warn})")
                    if a.get("_note"):
                        A(f"  - {a['_note']}")
                    if a.get("avoidCuisines"):
                        A(f"  - Avoid cuisines: {', '.join(a['avoidCuisines'])}.")
                        if a.get("_avoidCuisinesNote"):
                            A(f"    {a['_avoidCuisinesNote']}")
                    if a.get("highRisk"):
                        A(f"  - High-risk dishes and preparations: "
                          f"{', '.join(a['highRisk'])}.")
            elif key == "avoidIngredients":
                items = parsed if isinstance(parsed, list) else [parsed]
                A(f"- **Never serve or suggest**: {', '.join(str(i) for i in items)}. "
                  "A standing dislike, not an allergy — but treat it as settled.")
            else:
                A(f"- **{key}**: {parsed}")
        A("")

    # ── what the owner is circling now ────────────────────────────────────────
    threads = _q(con, f"""
        SELECT question FROM {P('t1_open_thread')}
        WHERE (state IS NULL OR lower(state) NOT IN ('closed','done','dismissed'))
          AND length(question) BETWEEN 35 AND 160
          AND question LIKE '%?%'
          AND regexp_matches(question, '^[A-Z]')
        ORDER BY created DESC NULLS LAST LIMIT 5
    """)
    if threads:
        A("## Currently open questions")
        for (question,) in threads:
            A(f"- {_clip(question, 150)}")
        A("")

    # ── what the owner is building ────────────────────────────────────────────
    # The half of the life the surface used to be blind to. An assistant that
    # knows what the owner reads and never that they are mid-build on a personal
    # OS will answer "what are they working on" from the notes about the work.
    #
    # Ranked by commits in the window, NOT by last commit date: half the repos on
    # disk were `git init`-ed the same week, so recency alone floats a one-commit
    # import above a project pushed on for months. Volume is the signal.
    live = _q(con, f"""
        SELECT p.name, c.n, p.description
        FROM {P('t1_project')} p
        JOIN (SELECT repo, count(*) AS n FROM {P('t1_project_commit')}
              WHERE substr(committed_at, 1, 10) >= strftime(current_date - INTERVAL 90 DAY, '%Y-%m-%d')
              GROUP BY repo) c ON c.repo = p.slug
        WHERE p.description <> ''
        ORDER BY c.n DESC, p.last_commit DESC LIMIT 5
    """)
    if live:
        total = _one(con, f"SELECT count(*) FROM {P('t1_project')}")
        A(f"## What {config.OWNER} is building")
        A(f"{total} repos on disk. Most-worked in the last 90 days, by commit volume:")
        for name, n, desc in live:
            A(f"- **{name}** ({n} commits) — {_clip(desc, 110)}")
        A("")

    # ── taste, with evidence ──────────────────────────────────────────────────
    art = _q(con, f"SELECT artist, plays FROM {P('t2_affinity')} ORDER BY plays DESC LIMIT 6")
    if art:
        A("## Listening")
        A(", ".join(f"{a} ({p:,})" for a, p in art) + " — plays, all-time.")
        A("")

    verdicts = _q(con, f"""
        SELECT subject, kind, rating, note FROM (
            SELECT *, row_number() OVER (PARTITION BY kind ORDER BY length(note) DESC) AS rn
            FROM {P('t1_verdicts')} WHERE note IS NOT NULL AND length(note) > 80
        ) WHERE rn = 1 ORDER BY length(note) DESC LIMIT 3
    """)
    if verdicts:
        A(f"## How {config.OWNER} judges things (verbatim)")
        for subj, kind, rating, note in verdicts:
            star = f" · {rating}/5" if rating else ""
            A(f"- **{subj}** ({kind}{star}) — \"{_clip(note, 260)}\"")
        A("")

    # ── rhythm, not episodes ──────────────────────────────────────────────────
    # Titles are pull-only. A specific book on a specific date is an episode —
    # something that merely happened — and promoting episodes into an artifact
    # every client loads unconditionally is a different exposure than leaving
    # them findable. Shape carries the useful signal without broadcasting acts.
    # Rates are quoted against each source's OWN last-logged date, because the
    # exports lag by months and "per month" measured from today would read as
    # "they stopped".
    A("## Rhythm")
    # `where` narrows a medium to what was actually consumed. t0_book carries
    # the whole Goodreads library — 436 of its rows are to-read and 38
    # partly-read — and `created` falls back to date_added, so shelving a book
    # you never opened otherwise reads as having finished it. Counting the shelf
    # produced "~27 books/month", which would make anyone a superhuman reader.
    for label, table, unit, where in (
        ("films", "t0_film", "films", ""),
        ("books", "t0_book", "books finished",
         " AND shelf = 'read' AND date_read IS NOT NULL AND date_read <> ''"),
        ("tv", "t0_tv", "shows touched", ""),
        ("music", "t0_music", "scrobbles", ""),
    ):
        edge = _q(con, f"""SELECT max(substr(created, 1, 10)) FROM {P(table)}
                           WHERE created IS NOT NULL AND created <> ''""")
        last = edge[0][0] if edge else None
        if not last:
            continue
        n90 = _one(con, f"""SELECT count(*) FROM {P(table)}
                            WHERE substr(created, 1, 10)
                                  BETWEEN strftime(CAST(? AS DATE) - INTERVAL 90 DAY, '%Y-%m-%d') AND ?
                                  {where}""",
                   last, last)
        # A rate is only worth quoting when the window supports one. "~0/month"
        # off a single row reads as "they stopped", when it means "they stopped
        # recording finish dates" — a fact about the log, not about the person.
        rate = f" (~{round(n90 / 3):,}/month)" if n90 >= 6 else ""
        A(f"- **{label}** — {n90:,} {unit} in the 90 days to {last}{rate}, "
          f"last logged {last}")
    # Only 108 of 448 read books carry a finish date, so the pace above is
    # computed from those and undercounts. Saying so beats implying nothing is
    # being read, and beats the earlier version that counted to-read additions and
    # implied 27 a month.
    dated = _one(con, f"""SELECT count(*) FROM {P('t0_book')}
                          WHERE shelf='read' AND date_read IS NOT NULL AND date_read <> ''""")
    undated = _one(con, f"""SELECT count(*) FROM {P('t0_book')}
                            WHERE shelf='read' AND (date_read IS NULL OR date_read = '')""")
    if undated:
        A(f"  ...book pace counts only the {dated} finished books that carry a finish "
          f"date; {undated} more were read without one, so the real rate is higher.")
    A("")

    # ── what the assistant may ask for ────────────────────────────────────────
    counts = served_counts or {}
    # t0_book is the whole library; only the read shelf is consumption.
    read_n = _one(con, f"SELECT count(*) FROM {P('t0_book')} WHERE shelf = 'read'")
    toread_n = _one(con, f"SELECT count(*) FROM {P('t0_book')} WHERE shelf = 'to-read'")

    A("## What you can ask this surface for")
    A("Ask by meaning, not by table. Available:")
    A(f"- **{config.OWNER_POSSESSIVE} notes** — {counts.get('t1_notes', 0):,} of them, "
      f"searchable by idea, plus {counts.get('t2_atom', 0):,} quotable spans lifted "
      "from them")
    # Beer rows are check-ins, not beers: 1,952 visits across 1,935 distinct
    # beers. Films are one row per film now that the merge keys on title+year
    # (it keyed on the permalink, and Letterboxd issues a different one per
    # context, so one watch counted three times).
    beers_distinct = _one(con, f"SELECT count(DISTINCT lower(beer_name)) FROM {P('t0_beer')}")
    reviews_n = _one(con, f"SELECT count(*) FROM {P('t1_film_review')}")

    A(f"- **what has been consumed** — {counts.get('t0_music', 0):,} scrobbles, "
      f"{read_n:,} books read and {toread_n:,} shelved to-read, "
      f"{counts.get('t0_film', 0):,} films, "
      f"{counts.get('t0_tv', 0):,} tv shows ({_one(con, f'SELECT sum(episodes_watched) FROM {P("t0_tv")}'):,} episodes), "
      f"{beers_distinct:,} beers across {counts.get('t0_beer', 0):,} check-ins")
    A(f"- **{config.OWNER_POSSESSIVE} own criticism** — {reviews_n:,} written film "
      f"reviews with links, plus {counts.get('t1_verdicts', 0):,} longer verdicts "
      f"across media. These are verbatim \u2014 {config.OWNER_POSSESSIVE} sentences, "
      "not a summary of them, which is what makes them worth more than the "
      "ratings.")
    A(f"- **open threads** — {counts.get('t1_open_thread', 0):,} questions raised and "
      "not closed")
    # The blog is the only pile here that is already public, and the only one
    # where the right answer is a LINK rather than a quote. Say so explicitly:
    # an assistant that treats it like the notes will paraphrase a finished
    # essay back at the person who wrote and published it.
    post_n = counts.get("t1_post", 0)
    if post_n:
        kinds = _q(con, f"""SELECT kind, count(*) FROM {P('t1_post')}
                            GROUP BY kind ORDER BY count(*) DESC LIMIT 4""")
        shape = ", ".join(f"{n:,} {k}" for k, n in kinds)
        A(f"- **{config.OWNER_POSSESSIVE} published blog** — {post_n:,} posts "
          f"at {_blog_host()} ({shape}), "
          "full text, searchable by meaning. Every hit carries its live URL. This is "
          "the finished public version of thinking the notes hold in draft \u2014 the "
          "posts are public and the notes are not, so the same idea may appear in "
          "both at different stages.")
    proj_n = counts.get("t1_project", 0)
    if proj_n:
        A(f"- **the workshop** — {proj_n:,} repos with "
          f"{counts.get('t1_project_commit', 0):,} dated commits and "
          f"{counts.get('t1_project_doc', 0):,} README/CONTEXT/ADR documents. Ask what is "
          "live, what stalled, which project argued for a decision, or what is left "
          "unfinished. Prose and metadata only — no source code is in this store.")
    A(f"- **places** — {counts.get('t1_visits', 0):,} restaurant visits with notes")
    # The item spine has been in production, reachable by nothing, since it was
    # published. An assistant asked what the owner should be doing answered from
    # the NOTES about those todos, because that is what this list said existed.
    items = counts.get("t1_item", 0)
    if items:
        A(f"- **what is committed to** — {items:,} items on the spine "
          f"{config.OWNER_POSSESSIVE} scheduler reads: tasks with due dates, habits "
          f"with streaks, open time slots, and standing constraints, plus "
          f"{counts.get('t1_item_event', 0):,} logged status changes behind them. Ask "
          "`agenda` for what is live and `history` for how it got there. Marking "
          f"anything done happens on {config.OWNER_POSSESSIVE} machine, not here.")
    # The middle of the writing axis. Notes are thinking, posts are the finished
    # argument, and this is the state between — the one the owner is most likely
    # to forget starting, which is the whole reason this surface exists.
    # Advertised even at zero, unlike the other optional sections. "Nothing is in
    # progress" and "I cannot see what is being written" are different
    # facts, and only this surface can tell them apart — an assistant that reads
    # silence as absence will answer the second as if it were the first.
    drafts_n = counts.get("t1_draft", 0)
    if drafts_n:
        A(f"- **what is being written right now** — {drafts_n:,} longform drafts in "
          "progress, with word counts and when each was last touched. Between "
          f"{config.OWNER_POSSESSIVE} private notes and the published blog. `drafts` "
          "lists them; ask with stale_days to find the ones that have gone cold.")
    else:
        A(f"- **what is being written right now** — `drafts` covers longform pieces in "
          f"progress, between {config.OWNER_POSSESSIVE} private notes and the published "
          "blog. None are open at the moment; that is the actual state, not a gap in "
          "what you can see.")
    recipes_n = counts.get("t1_recipe", 0)
    if recipes_n:
        A(f"- **what they cook** — {recipes_n:,} recipes they wrote up and published, "
          "with ingredients, steps and the post each came from. Small and real rather "
          "than a recipe database.")
    A("- **one medium at a time** — `medium` answers 'what is liked about film' in "
      "a single call: how much they have consumed, how they rate it on that medium's "
      "own scale, what they own, and what they have written about it. Four tools' worth "
      "of answer without needing to know the four tools.")
    A("- **what they queued and have not done** — `backlog` spans to-read, half-read, "
      "want-to-make and want-to-buy. `around_the_time` asks the same corpus by period "
      "instead of by topic: what they wrote, played, read and watched in a given "
      "window.")
    # Ownership is invisible unless advertised: an assistant will not guess that
    # a personal-context server knows what is on the owner's shelves.
    coll = _q(con, f"""SELECT kind, count(*) FROM {P('t1_collection')}
                       GROUP BY kind ORDER BY count(*) DESC""")
    if coll:
        A("- **what they own** — " + ", ".join(f"{n:,} {k.replace('_',' ')}" for k, n in coll)
          + " (owning is a stronger signal than playing once)")
    # Conversations, saves, events and the taste verticals were published for
    # days and never once asked for — the audit log shows recent_topics, reviews,
    # ratings and taste_summary at zero calls while whats_relevant ran 21 times.
    # An assistant asked about the owner's LLM conversations answered from the
    # NOTES about them, because this list is what it believes exists. A tool nobody is
    # told about is a tool nobody calls; publishing is not the same as offering.
    topics_n = counts.get("t0_chat_topic", 0)
    if topics_n:
        newest = _one(con, f"SELECT max(last_seen) FROM {P('t0_chat_topic')}", default=None)
        A(f"- **what they have been working through in conversation** — {topics_n:,} "
          f"threads with titles and turn counts, most recent {newest}. Fresher than "
          f"{config.OWNER_POSSESSIVE} notes, which lag a deliberate act of capture. "
          "Turn count is the signal: 300 turns is a preoccupation, 3 is a passing "
          "look. The longer ones carry a machine-written distillation of where "
          f"{config.OWNER_POSSESSIVE} thinking landed, alongside "
          f"{config.OWNER_POSSESSIVE} own closing words verbatim — check one against "
          "the other rather than trusting the summary. Full dialogue for one thread "
          "is available on request, speaker-tagged — lines from the assistant are "
          f"another model's output, context for reading {config.OWNER} rather than "
          f"fact, and never {config.OWNER_POSSESSIVE} words.")
    saves_n = counts.get("t0_raindrop", 0)
    if saves_n:
        A(f"- **saved links** — {saves_n:,} bookmarks, filterable by platform or tag")
    events_n = counts.get("t0_event", 0)
    if events_n:
        A(f"- **events they could go to** — {events_n:,} upcoming in DC, merged from "
          f"eight sources. Judge fit against {config.OWNER_POSSESSIVE} taste rather "
          "than listing them.")
    taste_n = counts.get("t1_taste", 0)
    if taste_n:
        A(f"- **what they SAY they like** — {taste_n:,} stated preferences across "
          "events, outings, travel and dining, distinct from what they actually do. "
          "Where the two disagree is usually the interesting part.")
    if counts.get("t0_taste_derived"):
        A(f"- **how to read {config.OWNER_POSSESSIVE} ratings** — scale calibration "
          "per medium. Read it before interpreting any number: "
          f"{config.OWNER_POSSESSIVE} restaurant scale is 0-10 with a median of 8.1, "
          "so an 8 is average, not a rave.")

    A("")

    # ── what changed here recently ─────────────────────────────────────────────
    # A client caches the tool list when it connects, so anything added later is
    # invisible to it until it reconnects. This section is the workaround: the
    # brief is a resource and gets re-read, so news arrives without a reconnect.
    # If something listed here has no matching tool, the tool list is stale —
    # say so rather than reporting the data as missing.
    if history:
        import datetime as _d
        cutoff = (_d.date.today() - _d.timedelta(days=30)).isoformat()
        fresh = [h for h in history if h["since"] >= cutoff]
        if fresh:
            A("## Recently added to this surface")
            for h in fresh[:8]:
                A(f"- `{h['zone']}` — since {h['since']}")
            A("If one of these has no tool you can call, your tool list predates it. "
              "Say that plainly instead of reporting the data as unavailable.")
            A("")

    # ── the guarded tail ──────────────────────────────────────────────────────
    # Everything from here down is reserved out of the budget before the body is
    # measured, so it cannot be clipped. Both items are about how far to trust
    # the rest: what is deliberately missing, and how old the whole thing is. A
    # brief that loses them still reads as complete and current, which is the
    # one failure a self-describing artifact must not have.
    tail: list[str] = []
    T = tail.append
    T("Not everything in the record is here. Private material is absent from this "
      "surface by construction, not filtered on request — do not ask for it, and "
      "do not infer its contents from its absence.")
    T("")
    T("## Freshness")
    try:
        state = json.loads((_pathlib.Path.home() / ".local/state/warehouse/sync-state.json").read_text())
        T(f"Last rebuilt {state.get('last_run_finished', 'unknown')} "
          f"({state.get('status', '?')}).")
    except Exception:
        T(f"Published {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}.")
    T("Each source lags differently — the per-source last-logged dates under "
      "Rhythm are authoritative, not this timestamp. If recency matters to the "
      "answer, check them rather than assuming the whole store is current.")

    con.close()

    tail_text = "\n".join(tail).rstrip() + "\n"
    body = "\n".join(out).rstrip() + "\n"
    clipped = "\n…(this brief was clipped in the middle to fit; the sections above "
    clipped += "are complete, the index of what you can ask for may not be)\n\n"
    budget = MAX_BYTES - len(tail_text.encode()) - len(clipped.encode())
    if len(body.encode()) > budget:
        body = body.encode()[:budget].decode("utf-8", "ignore").rstrip() + clipped
    else:
        body = body + "\n"
    return body + tail_text
