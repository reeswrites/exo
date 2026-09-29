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
#
# And then the middle turned out to be the wrong thing to clip too. The index of
# what a caller can ask for sits below the prose sections, so a slice off the
# bottom of the middle took the index first — at 9,000 bytes on the real
# instance it stopped after `events`, and `releases`, the music press and the
# film pool never reached a caller. ADR-0013 §2: publishing is not offering; a
# capability the brief does not name is unshipped, and this one was unshipped
# by arithmetic rather than by anyone's decision. Worse, an entry near the clip
# line flickered in and out as the sections above it grew and shrank — `albums`
# had to be hidden inside the scrobbles line to stay visible at all.
#
# So the budget is split by what each part is FOR, not by where it sits:
#
#   protected — the title and hard constraints, the capability index, and the
#     guarded tail. These are the brief's contract: what must never be violated,
#     what exists, and how far to trust the rest. None of them is recoverable by
#     asking, because each is what tells a caller there is something to ask.
#   yielding  — the prose sections: open questions, projects, listening,
#     verbatim verdicts, rhythm, recently-added. Every one is a PREVIEW of a tool
#     the index names, so dropping one costs a caller one call, not a capability.
#
# Yielding sections leave whole, in _YIELD_ORDER, and the brief says which went
# and which tool holds them. Whole, never sliced: a byte cut through the
# verdicts section ends a verbatim quote mid-sentence and still reads as a
# quote, which is the one thing an arrangement of the owner's own words must
# not do. The old byte clip survives only as a last resort, for an instance
# whose protected parts alone overrun — and tests/test_brief.py fails if the
# fixture or the live instance ever gets there.
#
# The index was also made to fit rather than just protected. Its entries had
# grown to 400-700 bytes each of rationale — why beer returns matter, why the
# workshop has no code — that the tool's own description already carries, and
# every client already has those descriptions from tools/list. The brief's job
# is to NAME each capability with enough of a hook to be reached for; the
# rationale lives with the tool. Where an entry keeps a sentence of instruction,
# it is one a caller would get wrong without it (quote the press as the
# outlet's, answer the blog with a link, never read an assistant turn as the
# owner's words).

# Which prose section leaves first when the brief is over budget, and which tool
# a caller asks to get it back. Rhythm goes last: the freshness stamp in the
# guarded tail points at its per-source dates as the authoritative ones.
# Recently-added goes late because it is the only thing that tells a client
# with a stale tool list that its list is stale.
_YIELD_ORDER: tuple[tuple[str, str, str], ...] = (
    ("listening", "top artists", "`taste`"),
    ("verdicts", "verbatim verdicts", "`verdicts`"),
    ("building", "what is being built", "`projects`"),
    ("questions", "open questions", "`open_threads`"),
    ("recent", "recently added", "your tool list"),
    ("rhythm", "rhythm", "`consumption`"),
)

# Printed when the protected parts alone overrun and the index itself has to
# be sliced. tests/test_brief.py asserts it never appears in a built brief.
CLIPPED_MARKER = ("…(this brief was clipped in the middle to fit; the sections above "
                  "are complete, the index of what you can ask for may not be)")



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
          src: "_pathlib.Path | None" = None,
          offered: dict | None = None) -> str:
    # `src` because publish now builds into a staging directory and swaps it in:
    # reading config.SERVE during a staged build would compose the brief from the
    # PREVIOUS projection, stamping today's date on yesterday's counts. Defaults
    # to the live projection for anyone calling this directly.
    srv = src or config.SERVE
    con = duckdb.connect(":memory:")

    def P(name: str) -> str:
        return f"read_parquet('{srv}/{name}.parquet')"

    # The body is a list of named parts rather than one list of lines, so the
    # budget can drop a whole section by name (see _YIELD_ORDER) instead of
    # slicing bytes off wherever the end happens to fall.
    parts: list[tuple[str, list[str]]] = []

    def section(key: str) -> None:
        parts.append((key, []))

    def A(line: str) -> None:
        parts[-1][1].append(line)

    section("head")
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
        section("questions")
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
        section("building")
        A(f"## What {config.OWNER} is building")
        A(f"{total} repos on disk. Most-worked in the last 90 days, by commit volume:")
        for name, n, desc in live:
            A(f"- **{name}** ({n} commits) — {_clip(desc, 110)}")
        A("")

    # ── taste, with evidence ──────────────────────────────────────────────────
    art = _q(con, f"SELECT artist, plays FROM {P('t2_affinity')} ORDER BY plays DESC LIMIT 6")
    if art:
        section("listening")
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
        section("verdicts")
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
    section("rhythm")
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
    # Protected (see the MAX_BYTES note): this part is never yielded, so every
    # entry here reaches every caller. The corollary is a discipline on the
    # entries themselves — one line each, the capability, its count, and the
    # tool that serves it in backticks. worker/test/run.mjs checks that every
    # tool the instance offers is named here; a tool nobody is told about is a
    # tool nobody calls (ADR-0013 §2).
    #
    # The long-form reasons each entry used to carry now live in the comments
    # beside it and in the tool descriptions, which every client already holds.
    counts = served_counts or {}
    # t0_book is the whole library; only the read shelf is consumption.
    read_n = _one(con, f"SELECT count(*) FROM {P('t0_book')} WHERE shelf = 'read'")
    toread_n = _one(con, f"SELECT count(*) FROM {P('t0_book')} WHERE shelf = 'to-read'")

    section("index")
    A("## What you can ask this surface for")
    A("Ask by meaning, not by table. Each tool's own description says more. Available:")
    A(f"- **{config.OWNER_POSSESSIVE} notes** — {counts.get('t1_notes', 0):,} of them plus "
      f"{counts.get('t2_atom', 0):,} quotable spans; `whats_relevant` searches both by "
      "idea, `notes_on` maps what exists on a topic")
    # Beer rows are check-ins, not beers: 1,952 visits across 1,935 distinct
    # beers. Films are one row per film now that the merge keys on title+year
    # (it keyed on the permalink, and Letterboxd issues a different one per
    # context, so one watch counted three times).
    beers_distinct = _one(con, f"SELECT count(DISTINCT lower(beer_name)) FROM {P('t0_beer')}")
    # The gap between those two numbers, named rather than left to be subtracted.
    # Almost every check-in is a beer never drunk before, so a return is the
    # rarest judgement in this record and the one worth the most. Beer rows carry
    # brewery, style, abv and venue, which is what `facets` rolls up by.
    beers_repeat = _one(con, f"""SELECT count(*) FROM (
        SELECT count(*) AS c FROM {P('t0_beer')} GROUP BY lower(beer_name)) WHERE c > 1""")
    reviews_n = _one(con, f"SELECT count(*) FROM {P('t1_film_review')}")
    episodes = _one(con, f"SELECT sum(episodes_watched) FROM {P('t0_tv')}")
    # `taste` is the scrobble stream as revealed preference; `albums` is the
    # same plays by record, with Last.fm crowd tags, because the scrobbles say
    # what was played and never what it sounds like — a mood question
    # ("something high-energy") had nothing to match against. It used to hide
    # as a clause on this line to stay above the clip; the index is protected
    # now, so it is named like everything else.
    albums = (" (`albums` by record, with crowd tags — ask by sound)"
              if counts.get("t0_music_tag", 0) else "")
    A(f"- **what has been consumed** — {counts.get('t0_music', 0):,} scrobbles"
      f"{albums}, {read_n:,} books read and {toread_n:,} shelved to-read, "
      f"{counts.get('t0_film', 0):,} films, {counts.get('t0_tv', 0):,} tv shows "
      f"({episodes:,} episodes), {beers_distinct:,} beers across "
      f"{counts.get('t0_beer', 0):,} check-ins (only {beers_repeat:,} drunk twice — "
      "a return says more than a score). `consumption` for shape, `taste` for "
      "plays, `ratings` for scores, `facets` to roll ratings up by style, brewery "
      "or venue, `watching` for what was started and not finished.")
    # Television is the one medium whose consumption line cannot say how far
    # anything got: Trakt counts episodes watched and never episodes existing.
    # The anime list is where the denominator is (ADR-0024), and Trakt carries
    # no ratings, so this is also the only rating television has.
    anime_n = _one(con, f"SELECT count(*) FROM {P('t0_anime')} WHERE status <> 'plan_to_watch'")
    if anime_n:
        anime_rated = _one(con, f"SELECT count(*) FROM {P('t0_anime')} WHERE score > 0")
        anime_queued = _one(con, f"SELECT count(*) FROM {P('t0_anime')} WHERE status = 'plan_to_watch'")
        A(f"- **the anime shelf** — {anime_n:,} watched or watching, {anime_queued:,} "
          f"queued, {anime_rated:,} scored 1-10; the only record with episode totals "
          "and the only television ratings (`ratings(medium:'anime')`)")
    # Verbatim, which is what makes them worth more than the ratings.
    A(f"- **{config.OWNER_POSSESSIVE} own criticism** — {reviews_n:,} written film reviews "
      f"(`reviews`) and {counts.get('t1_verdicts', 0):,} longer verdicts (`verdicts`), "
      f"verbatim — {config.OWNER_POSSESSIVE} sentences, not a summary")
    A(f"- **open threads** — {counts.get('t1_open_thread', 0):,} questions raised and "
      "not closed (`open_threads`)")
    # The blog is the only pile here that is already public, and the only one
    # where the right answer is a LINK rather than a quote. Say so explicitly:
    # an assistant that treats it like the notes will paraphrase a finished
    # essay back at the person who wrote and published it. The same idea may
    # sit in both piles at different stages — the posts are the finished
    # public version of what the notes hold in draft.
    post_n = counts.get("t1_post", 0)
    if post_n:
        # `kind` breaks ties so the brief is a function of the record: two
        # publishes of the same projection must produce the same bytes, or a
        # diff of the brief reports news that is only DuckDB's hash order.
        kinds = _q(con, f"""SELECT kind, count(*) FROM {P('t1_post')}
                            GROUP BY kind ORDER BY count(*) DESC, kind LIMIT 4""")
        shape = ", ".join(f"{n:,} {k}" for k, n in kinds)
        A(f"- **{config.OWNER_POSSESSIVE} published blog** — {post_n:,} posts at "
          f"{_blog_host()} ({shape}), searchable by meaning (`posts`). Public and "
          "finished: answer with the live URL each hit carries, not a paraphrase.")
    # Prose and metadata only — no source code is in this store (the workshop
    # zones, ADR-0013). Four tools because the questions differ: what is live,
    # what was done when, what a project argued, what is left unfinished.
    proj_n = counts.get("t1_project", 0)
    if proj_n:
        A(f"- **the workshop** — {proj_n:,} repos (`projects`), "
          f"{counts.get('t1_project_commit', 0):,} dated commits (`project_activity`), "
          f"{counts.get('t1_project_doc', 0):,} README/CONTEXT/ADR documents "
          "(`project_docs`), and what is left unfinished (`project_open`). No source "
          "code.")
    A(f"- **places** — {counts.get('t1_visits', 0):,} restaurant visits with notes "
      "(`places`)")
    # The item spine was in production, reachable by nothing, for days after it
    # was published. An assistant asked what the owner should be doing answered
    # from the NOTES about those todos, because that is what this list said
    # existed. The write path stays on the owner's machine — say so, or a
    # caller offers to tick things off.
    items = counts.get("t1_item", 0)
    if items:
        A(f"- **what is committed to** — {items:,} tasks, habits, slots and "
          f"constraints (`agenda`) and {counts.get('t1_item_event', 0):,} status "
          f"changes behind them (`history`). Marking done happens on "
          f"{config.OWNER_POSSESSIVE} machine, not here.")
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
          "progress, between the notes and the blog (`drafts`; stale_days finds the "
          "cold ones)")
    else:
        A("- **what is being written right now** — `drafts` covers longform pieces in "
          "progress. None are open; that is the actual state, not a gap in what you "
          "can see.")
    # Small and real rather than a recipe database: ones they made and posted.
    recipes_n = counts.get("t1_recipe", 0)
    if recipes_n:
        A(f"- **what they cook** — {recipes_n:,} recipes they wrote up and published "
          "(`recipes`)")
    # The lenses: no rows of their own, each answers across zones in one call,
    # so a caller need not know which four tools to combine.
    A("- **across the record** — `medium` for everything about one medium in one "
      "call; `backlog` for what was queued and not done; `around_the_time` for a "
      "period rather than a topic")
    # Ownership is invisible unless advertised: an assistant will not guess that
    # a personal-context server knows what is on the owner's shelves. Owning is a
    # stronger signal than playing once.
    coll = _q(con, f"""SELECT kind, count(*) FROM {P('t1_collection')}
                       GROUP BY kind ORDER BY count(*) DESC, kind""")
    if coll:
        A("- **what they own** — " + ", ".join(f"{n:,} {k.replace('_',' ')}" for k, n in coll)
          + " (`collection`)")
    # Conversations, saves, events and the taste verticals were published for
    # days and never once asked for — the audit log shows recent_topics, reviews,
    # ratings and taste_summary at zero calls while whats_relevant ran 21 times.
    # An assistant asked about the owner's LLM conversations answered from the
    # NOTES about them, because this list is what it believes exists.
    #
    # Turn count is the signal (300 turns is a preoccupation, 3 a passing
    # look) — the tool description says so. Two things stay here: that the
    # longer threads carry a machine distillation beside the owner's closing
    # words (nothing else tells a caller the distillations exist, and it should
    # check one against the other), and the one instruction whose absence
    # misattributes — an assistant line in a thread is another model's output.
    topics_n = counts.get("t0_chat_topic", 0)
    if topics_n:
        newest = _one(con, f"SELECT max(last_seen) FROM {P('t0_chat_topic')}", default=None)
        A(f"- **what they have been working through in conversation** — {topics_n:,} "
          f"threads, newest {newest} (`recent_topics`); one in full with `thread`. "
          "Longer ones carry a machine distillation beside "
          f"{config.OWNER_POSSESSIVE} closing words — check one against the other. "
          f"Assistant lines are another model's output, never {config.OWNER_POSSESSIVE} words.")
    saves_n = counts.get("t0_raindrop", 0)
    if saves_n:
        A(f"- **saved links** — {saves_n:,} bookmarks, by platform or tag (`saves`)")
    events_n = counts.get("t0_event", 0)
    if events_n:
        A(f"- **events they could go to** — {events_n:,} upcoming in DC (`events`). "
          f"Judge fit against {config.OWNER_POSSESSIVE} taste rather than listing them.")
    # The discovery zones. All pure gain for an assistant and pure invisibility
    # without this: nothing about a personal-context server suggests it knows
    # what came out last week, so an assistant asked "anything new I'd like"
    # will answer from the scrobbles — the one question the scrobbles
    # structurally cannot answer. The record pool is crawled by SCENE, not by
    # similarity, so it can name an artist nobody has scrobbled; it is unranked
    # by design, and absence from it means the crawl never looked, not that
    # anything was rejected — the tool description carries all of that.
    releases_n = counts.get("t0_release", 0)
    if releases_n:
        A(f"- **records that just came out** — {releases_n:,} candidates, crawled by "
          "scene, already-heard and already-owned removed, unranked (`releases`)")
    # Not new, just missed: "an album I haven't heard" at any age. The scrobbles
    # cannot answer it and `releases` only reaches the last few months.
    pool_n = counts.get("t0_album_pool", 0)
    if pool_n:
        A(f"- **records not yet heard** — {pool_n:,} from the catalogs of the most-played "
          "acts and the densest scenes, heard and owned removed, with crowd tags (`unheard`)")
    # The film half. Without it an assistant asked "what should I watch
    # tonight" answers from the ratings, and cannot say whether any of it is
    # actually streamable.
    offer_n = counts.get("t0_film_offer", 0)
    if offer_n:
        A(f"- **films streamable right now** — {offer_n:,} titles on a service "
          f"{config.OWNER} subscribes to, already-watched removed, with days until "
          "each leaves (`streaming`)")
    # SOMEBODY ELSE'S writing — the only thing here that says what anyone other
    # than the owner thinks. The attribution instruction stays in the brief
    # because a caller that quotes it as the owner's has invented an opinion.
    crit_n = counts.get("t0_criticism", 0)
    if crit_n:
        outlets = _one(con, f"SELECT count(DISTINCT outlet) FROM {P('t0_criticism')}", default=0)
        A(f"- **what the music press is publishing** — {crit_n:,} pieces from {outlets} "
          f"outlet{'' if outlets == 1 else 's'} (`criticism`). Somebody else's writing, "
          f"never {config.OWNER_POSSESSIVE}: quote it as the outlet's, with the link.")
    # Stated, distinct from revealed. Where the two disagree is usually the
    # interesting part.
    taste_n = counts.get("t1_taste", 0)
    if taste_n:
        A(f"- **what they SAY they like** — {taste_n:,} stated preferences across "
          "events, outings, travel and dining (`taste_profile`), distinct from what "
          "they do")
    # Gated on EITHER half, because the two kinds now come from different places:
    # the dining and cluster documents are taste-engine's, mirrored; the beer one
    # is computed by the surface from the check-in log. An instance with beer and
    # no taste-engine still has a calibration to advertise.
    if counts.get("t0_taste_derived") or counts.get("t0_beer"):
        cal = [f"- **how to read {config.OWNER_POSSESSIVE} ratings** — per-medium scale "
               "calibration (`taste_summary`); read it before interpreting any number."]
        if counts.get("t0_taste_derived"):
            # Sentence-initial, and the possessive is configured per instance —
            # `capitalize()` would lowercase the tail of a name-shaped one.
            poss = config.OWNER_POSSESSIVE
            cal.append(f" {poss[:1].upper()}{poss[1:]} restaurant scale is "
                       "0-10 with a median of 8.1, so an 8 is average, not a rave.")
        if counts.get("t0_beer"):
            # Computed, never stated. A median written into this file is a number
            # that stops being true the next time they drink something, and the
            # claim it supports ("a 4 is not high") is only true of some
            # distributions — so the numbers go in and the adjective does not.
            beer_cal = _q(con, f"""SELECT median(r),
                                          sum(CASE WHEN r >= 4 THEN 1 ELSE 0 END) * 1.0 / count(*)
                                   FROM (SELECT CAST(rating_score AS DOUBLE) AS r
                                         FROM {P('t0_beer')}
                                         WHERE nullif(rating_score, '') IS NOT NULL
                                           AND CAST(rating_score AS DOUBLE) > 0)""")
            if beer_cal and beer_cal[0][0] is not None:
                med, at4 = beer_cal[0]
                cal.append(f" Beer is 0-5 with a median of {med:g}, and {at4:.0%} of "
                           "rated check-ins are 4.0 or above.")
        A("".join(cal))

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
            section("recent")
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

    # Deliberately in the guarded tail rather than the body. A caller that has
    # been handed a shorter tool list than the engine defines needs to know the
    # difference between "this record cannot answer that" and "something else
    # you are holding answers it better" — and that is exactly the distinction
    # that goes missing when a section gets clipped.
    if offered:
        switched_off = sorted(t for t, why in (offered.get("withheld") or {}).items()
                              if why != "zones")
        peer_map = offered.get("peers") or {}
        if switched_off:
            T("")
            T(f"Some tools this engine defines are switched off here: "
              f"{', '.join('`' + t + '`' for t in switched_off)}. That is a choice, not "
              f"a gap in the record — do not report the underlying material as missing.")
        if peer_map:
            T("")
            T("Rows tagged with one of these sources are also live in a system you may "
              "already be connected to: "
              + ", ".join(f"`{src_}` → {srv}" for src_, srv in sorted(peer_map.items()))
              + ". Where you hold both, this surface is the filed and indexed copy and "
              "the peer is the current one. Reconcile them rather than reporting the "
              "same material twice.")
    T("")
    T("## Freshness")
    try:
        state = json.loads((config.STATE / "sync-state.json").read_text())
        T(f"Last rebuilt {state.get('last_run_finished', 'unknown')} "
          f"({state.get('status', '?')}).")
    except Exception:
        T(f"Published {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}.")
    T("Each source lags differently — the per-source last-logged dates under "
      "Rhythm are authoritative, not this timestamp. If recency matters to the "
      "answer, check them rather than assuming the whole store is current.")

    con.close()

    tail_text = "\n".join(tail).rstrip() + "\n"
    return _fit(parts, tail_text)


def _fit(parts: list[tuple[str, list[str]]], tail_text: str) -> str:
    """Assemble the brief within MAX_BYTES, yielding prose before capability.

    Whole prose sections leave in _YIELD_ORDER until the rest fits, and a line
    above the index says which went and where to ask for them — a brief that
    drops a section silently reads as complete, which is the failure the
    guarded tail exists to prevent. Only if the protected parts alone overrun
    does the old byte clip run, and then it says so in CLIPPED_MARKER.
    """
    def render(ps: list[tuple[str, list[str]]]) -> str:
        return "\n".join(line for _, lines in ps for line in lines).rstrip() + "\n"

    def over(ps: list[tuple[str, list[str]]]) -> bool:
        return len((render(ps) + "\n" + tail_text).encode()) > MAX_BYTES

    def with_note(kept: list[tuple[str, list[str]]], gone: list[tuple[str, str]]):
        if not gone:
            return kept
        note = ("…(left out to fit: "
                + "; ".join(f"{label} — ask {where}" for label, where in gone)
                + ". The index below is complete.)")
        at = next(i for i, (k, _) in enumerate(kept) if k == "index")
        return kept[:at] + [("note", [note, ""])] + kept[at:]

    kept = list(parts)
    gone: list[tuple[str, str]] = []
    for key, label, where in _YIELD_ORDER:
        if not over(with_note(kept, gone)):
            break
        if any(k == key for k, _ in kept):
            kept = [(k, ls) for k, ls in kept if k != key]
            gone.append((label, where))

    body = render(with_note(kept, gone))
    clipped = "\n" + CLIPPED_MARKER + "\n\n"
    budget = MAX_BYTES - len(tail_text.encode()) - len(clipped.encode())
    if len(body.encode()) > budget:
        body = body.encode()[:budget].decode("utf-8", "ignore").rstrip() + clipped
    else:
        body = body + "\n"
    return body + tail_text
