/**
 * The tool surface — which is the security boundary (ADR-0007).
 *
 * There is no general-purpose tool here, and there must never be: no raw SQL, no
 * id-lookup loop, no cursor that can walk the full set. Every tool is a fixed,
 * named question with a bounded answer, so adding one is a decision about
 * exposure rather than a feature increment.
 *
 * Caps: at most MAX_ROWS rows AND MAX_BYTES of payload, whichever binds first.
 * The point is not to make exfiltration impossible — an authenticated caller is
 * indistinguishable from an injected one, and a patient one simply loops. The
 * point is to convert "one quiet call takes everything" into "hundreds of calls,
 * visibly", which is only worth anything if wh_audit is actually read.
 *
 * 16KB, not the 4KB this started at: that number was sized to what a chat turn
 * could hold, and this is a context layer for assistants generally. 16KB returns
 * ~99% of notes whole and still makes a full-corpus pull ~250 logged calls.
 */
import { search } from "./search.js";

export const MAX_ROWS = 20;
export const MAX_BYTES = 16384;

/** Trim to the caps, and say so, so a client never mistakes truncation for exhaustion. */
/**
 * The live URL of a published post, as a spreadable fragment.
 *
 * The permalink is a pure function of the slug, so the link costs no round
 * trip — but the shape of it is an instance fact, not an engine one, and an
 * instance with no blog must produce no url key at all rather than a broken one.
 */
export const postUrl = (env, slug) =>
  env.BLOG_URL_TEMPLATE ? { url: env.BLOG_URL_TEMPLATE.replace("{slug}", slug) } : {};

export function cap(rows) {
  const out = [];
  let bytes = 0;
  let truncated = false;

  for (const r of rows.slice(0, MAX_ROWS)) {
    const size = JSON.stringify(r).length;
    if (bytes + size > MAX_BYTES) { truncated = true; break; }
    out.push(r);
    bytes += size;
  }
  if (rows.length > out.length) truncated = true;

  return {
    rows: out,
    // A single row too large to emit is a different fact from a list too long,
    // and the generic advice is wrong for it: there is nothing to narrow. Say
    // which happened, so the audit log distinguishes a big answer from a bug.
    ...(truncated
      ? {
          note:
            out.length === 0 && rows.length === 1
              ? `the one row matched exceeded the ${MAX_BYTES}-byte cap and was withheld — the tool that built it did not budget for its own envelope`
              : `truncated to ${out.length} of ${rows.length} — ask a narrower question rather than paging`,
        }
      : {}),
  };
}

/**
 * Fill a list field against what the row will ACTUALLY serialise to.
 *
 * Measuring raw text against a fixed allowance does not work: JSON quotes every
 * line, `summary` runs to 890 chars in the store, and the envelope varies per
 * thread. `thread` reserved a flat 1,200 and assembled a 16,665-byte row against
 * a 16,384 cap — whereupon cap() dropped the only row it had and the caller got
 * an empty answer advising them to narrow a question that was already one
 * thread. Budget against the real envelope and the row always fits.
 */
const TURN_NOTE_SAMPLE = "999 of 999 of the owner's turns shown — ask about a narrower part";

function fitInto(row, field, items, notePlaceholder) {
  const envelope = JSON.stringify({ ...row, [field]: [], note: notePlaceholder }).length;
  const budget = MAX_BYTES - envelope;
  const kept = [];
  let used = 0;
  for (const item of items) {
    const cost = JSON.stringify(item).length + 1; // its quoting, and the comma after it
    if (used + cost > budget) break;
    kept.push(item);
    used += cost;
  }
  return kept;
}

/**
 * The one pile that is not here. Letterboxd's watchlist is not in the corpus —
 * only ratings and reviews were ever exported — and t0_tv is watch history, not
 * a queue. Every backlog answer carries this, because an assistant that sees
 * read/resume/make/buy and no watch will otherwise conclude nothing is
 * queued to watch, which is the opposite of true.
 */
const WATCH_GAP =
  "watching is not covered: the Letterboxd watchlist was never exported, so absence of films here is a gap in the data, not an empty queue";

const q = (env, sql, ...binds) =>
  env.DB.prepare(sql).bind(...binds).all().then((r) => r.results ?? []);

/**
 * The full text of ONE row, found by meaning rather than by id.
 *
 * `read_note` and `read_post` were the same forty lines twice — semantic lookup,
 * miss guard, fetch, clip against a hand-rolled byte budget, explain the clip.
 * They were also the only two places in this file that budgeted bytes by hand
 * instead of going through cap(), which is exactly the shape a second copy
 * takes when the first one is copied.
 *
 * Semantic lookup is load-bearing, not incidental (ADR-0007): there is no id to
 * pass and no cursor to walk, so one named thing per call is the hardest shape
 * to bulk-extract with. That property lives here now, in one place, rather than
 * being restated wherever someone needs a whole document.
 */
async function readOne(env, { topic, kind, table, key, select, missing, onClip }) {
  const [hit] = await search(env, topic, { k: 1, kind });
  if (!hit) return { rows: [], note: `no ${missing} matched` };

  const rows = await q(env, `SELECT ${select} FROM ${table} WHERE ${key} = ? LIMIT 1`, hit.ref);
  if (!rows.length) return { rows: [], note: `matched a ${missing} that is not published` };

  const row = rows[0];
  const full = row.body ?? "";
  // Budget against the real envelope, not a guessed constant: the metadata
  // around the body differs per table, and a flat allowance is how a single
  // row ends up over the cap and gets dropped entirely.
  const envelope = JSON.stringify({ ...row, body: "", match_score: 0.999, note: "" }).length;
  const budget = MAX_BYTES - envelope - 200;
  const clipped = full.length > budget;

  return {
    rows: [{ ...row, match_score: hit.score, body: clipped ? full.slice(0, budget) : full }],
    ...(clipped ? { note: onClip(row, budget, full.length) } : {}),
  };
}

export const TOOLS = {
  whats_relevant: {
    class: "derived", domain: "mind", kind: "vector",
    description:
      "What has the owner written that bears on a topic? Searches their notes and the verbatim spans lifted from them, by meaning. Use this first when you want to know their thinking on something.",
    schema: {
      type: "object",
      properties: { topic: { type: "string", description: "A topic, question, or phrase." } },
      required: ["topic"],
    },
    async run(env, { topic }) {
      const hits = await search(env, topic, { k: MAX_ROWS });
      // A post hit is the one kind that has somewhere to send a reader. Its ref is
      // the slug, and the permalink is a pure function of it, so the link costs
      // no round trip — and an answer with a link beats one that paraphrases
      // an essay back at its own author.
      return cap(
        hits.map((h) => ({
          kind: h.kind,
          text: h.label,
          score: h.score,
          ...(h.kind === "post" ? postUrl(env, h.ref) : {}),
        }))
      );
    },
  },

  notes_on: {
    class: "authored", domain: "mind", kind: "text",
    description:
      "The owner's notes about a topic. Returns titles by default — a map of what exists rather than contents. Pass full:true for the entire text of the single best match.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string" },
        full: { type: "boolean", description: "Return one whole note instead of a list of titles." },
      },
      required: ["topic"],
    },
    async run(env, { topic, full }) {
      if (full) {
        return readOne(env, {
          topic, kind: "note", table: "t1_notes", key: "origin_ref", missing: "note",
          select: "title, folder, created, body",
          onClip: (_r, kept, total) =>
            `body truncated to ${kept} of ${total} chars — this is one of the owner's longer notes; ask about a specific part of it rather than requesting it again`,
        });
      }
      const hits = await search(env, topic, { k: MAX_ROWS, kind: "note" });
      return cap(hits.map((h) => ({ title: h.label, score: h.score })));
    },
  },

  open_threads: {
    class: "intent", domain: "mind", kind: "pointer",
    description:
      "Questions the owner has asked themselves and not closed. The best single source of what they are currently chewing on.",
    schema: { type: "object", properties: {} },
    async run(env) {
      const rows = await q(
        env,
        `SELECT question, state FROM t1_open_thread
         WHERE state IS NULL OR lower(state) NOT IN ('closed','done','dismissed')
         ORDER BY created DESC LIMIT ?`,
        MAX_ROWS
      );
      return cap(rows.map((r) => ({ question: r.question })));
    },
  },

  verdicts: {
    class: "authored", domain: "culture", kind: "text",
    description:
      "The owner's written opinions on books, films, tv and music — in their own words, with reasoning. The highest-signal material here for judging how they think.",
    schema: {
      type: "object",
      properties: { kind: { type: "string", description: "books | films | tv | music" } },
    },
    async run(env, { kind }) {
      const rows = kind
        ? await q(env, `SELECT subject, kind, rating, note FROM t1_verdicts WHERE kind = ? LIMIT ?`, kind, MAX_ROWS)
        : await q(env, `SELECT subject, kind, rating, note FROM t1_verdicts LIMIT ?`, MAX_ROWS);
      return cap(rows);
    },
  },

  taste: {
    class: "revealed", domain: "culture", kind: "event",
    description:
      "What the owner actually listens to, by play count — the revealed preference, as distinct from what they say.",
    schema: { type: "object", properties: {} },
    async run(env) {
      const rows = await q(
        env,
        `SELECT artist, plays, mentions FROM t2_affinity ORDER BY plays DESC LIMIT ?`,
        MAX_ROWS
      );
      return cap(rows);
    },
  },

  agenda: {
    class: "intent", domain: "commitments", kind: "pointer",
    description:
      "What the owner has committed to and where it stands \u2014 the item spine Kairos schedules from. Four families with different lifecycles: tasks are todo/done, habits carry a streak and a cadence, slots are open or filled, constraints are standing rules. Ask with no arguments for what is live right now.",
    schema: {
      type: "object",
      properties: {
        family: { type: "string", description: "task | habit | slot | constraint" },
        include_done: { type: "boolean", description: "Include finished tasks (default false)." },
      },
    },
    async run(env, { family, include_done }) {
      // "Live" is family-specific, which is the point of the spine: a habit is
      // never 'todo' and a slot is never 'done'. Filtering on one status
      // vocabulary would silently empty three of the four families.
      const LIVE = "(status IN ('todo','active','open') OR status IS NULL)";
      const where = [];
      const binds = [];
      if (family) { where.push("family = ?"); binds.push(family); }
      if (!include_done) where.push(LIVE);

      const rows = await q(
        env,
        `SELECT family, title, status, streak, cadence, due, pile, est_minutes
         FROM t1_item
         ${where.length ? "WHERE " + where.join(" AND ") : ""}
         ORDER BY family, due IS NULL, due, created DESC
         LIMIT ?`,
        ...binds, MAX_ROWS
      );

      // Counts alongside, so "3 rows" is never read as "3 items exist".
      const shape = await q(
        env,
        `SELECT family, status, count(*) AS n FROM t1_item GROUP BY family, status ORDER BY family`
      );
      const out = rows.map((r) => {
        const item = { family: r.family, title: r.title, status: r.status };
        if (r.streak) item.streak = r.streak;
        if (r.cadence) item.cadence = r.cadence;
        if (r.due) item.due = r.due;
        if (r.pile) item.pile = r.pile;
        if (r.est_minutes) item.est_minutes = r.est_minutes;
        return item;
      });
      return {
        ...cap(out),
        shape: shape.map((r) => `${r.family}/${r.status}: ${r.n}`).join(" \u00b7 "),
        // Completion is not available here and never will be \u2014 this surface is
        // read-only (ADR-0006). Saying so stops an assistant offering to tick
        // something off and then silently failing to.
        note: "read-only: items are marked done on the owner's own machine, not through this surface",
      };
    },
  },

  history: {
    class: "revealed", domain: "commitments", kind: "event",
    description:
      "What actually happened to the owner's commitments \u2014 the append-only log behind the item spine. Status changes with dates, so you can see when a habit lapsed or how long a task sat before it moved, rather than only where it stands now.",
    schema: {
      type: "object",
      properties: { title: { type: "string", description: "Match against the item's title." } },
    },
    async run(env, { title }) {
      const like = title ? `%${title.toLowerCase()}%` : null;
      const rows = await q(
        env,
        `SELECT e.event, e.to_status, e.date, e.ts, e.ref_kind, e.from_,
                COALESCE(i.title, e.title) AS item
         FROM t1_item_event e
         LEFT JOIN t1_item i ON i.origin_ref = e.item_id OR i.id = e.item_id
         WHERE (? IS NULL OR lower(COALESCE(i.title, e.title)) LIKE ?)
         ORDER BY COALESCE(e.date, e.ts) DESC
         LIMIT ?`,
        like, like, MAX_ROWS
      );
      return cap(rows.map((r) => ({
        item: r.item, event: r.event,
        // from_ -> to_status is the fold: "todo -> done" says more than "done",
        // and it is the only place the previous state survives at all.
        ...(r.from_ ? { from: r.from_ } : {}),
        ...(r.to_status ? { to: r.to_status } : {}),
        when_: r.date || (r.ts ?? "").slice(0, 10),
        ...(r.ref_kind ? { ref_kind: r.ref_kind } : {}),
      })));
    },
  },

  recipes: {
    class: "authored", domain: "table", kind: "text",
    description:
      "What the owner actually cooks \u2014 recipes they wrote up and published, with their source links. Small and real: these are ones they made and posted, not a recipe database. Pass full:true for the ingredients and steps of the best match.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, cuisine, or tags." },
        full: { type: "boolean", description: "Return ingredients and steps for one recipe." },
      },
    },
    async run(env, { topic, full }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const match = "(? IS NULL OR lower(title) LIKE ? OR lower(cuisine) LIKE ? OR lower(tags) LIKE ?)";
      const binds = [like, like, like, like];

      if (full) {
        const rows = await q(
          env,
          `SELECT title, cuisine, time_min, effort, yield_servings, source_url,
                  ingredients, steps
           FROM t1_recipe WHERE ${match} ORDER BY title LIMIT 1`,
          ...binds
        );
        if (!rows.length) return { rows: [], note: "no recipe matched" };
        const r = rows[0];
        // Both columns are JSON on the wire \u2014 D1 has no list type \u2014 so parse
        // here rather than handing a reader a string that only looks like data.
        const parse = (v) => { try { return JSON.parse(v || "[]"); } catch { return []; } };
        return cap([{ ...r, ingredients: parse(r.ingredients), steps: parse(r.steps) }]);
      }

      const rows = await q(
        env,
        `SELECT title, cuisine, time_min, effort, yield_servings, source_url
         FROM t1_recipe WHERE ${match} ORDER BY title LIMIT ?`,
        ...binds, MAX_ROWS
      );
      return cap(rows);
    },
  },
  drafts: {
    class: "authored", domain: "mind", kind: "text",
    description:
      "Longform pieces the owner is in the middle of writing \u2014 the state between a private note and a published post. Ask with no arguments for everything open, oldest-touched last. `stale_days` finds the ones that have gone cold, which is the question a writer cannot answer about themselves. Pass full:true for the whole text of one.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title or text." },
        stale_days: { type: "number", description: "Only drafts untouched for at least this many days." },
        full: { type: "boolean", description: "Return the entire text of the best match." },
      },
    },
    async run(env, { topic, stale_days, full }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const match = "(? IS NULL OR lower(title) LIKE ? OR lower(body) LIKE ?)";
      const stale = "(? IS NULL OR julianday('now') - julianday(modified) >= ?)";
      const binds = [like, like, like, stale_days ?? null, stale_days ?? 0];

      if (full) {
        const rows = await q(
          env,
          `SELECT title, slug, description, started, modified, words, state, body
           FROM t1_draft WHERE ${match} AND ${stale} ORDER BY modified DESC LIMIT 1`,
          ...binds
        );
        if (!rows.length) return { rows: [], note: "no draft matched" };
        const d = rows[0];
        const envelope = JSON.stringify({ ...d, body: "", note: "" }).length;
        const budget = MAX_BYTES - envelope - 200;
        const clipped = (d.body ?? "").length > budget;
        return {
          rows: [{ ...d, body: clipped ? d.body.slice(0, budget) : d.body }],
          ...(clipped ? { note: `body truncated to ${budget} of ${d.body.length} chars` } : {}),
        };
      }

      const rows = await q(
        env,
        `SELECT title, slug, description, started, modified, words, state,
                CAST(julianday('now') - julianday(modified) AS INTEGER) AS days_since_touched
         FROM t1_draft WHERE ${match} AND ${stale} ORDER BY modified DESC LIMIT ?`,
        ...binds, MAX_ROWS
      );
      if (!rows.length) {
        return {
          rows: [],
          // An empty answer here has two very different meanings and the caller
          // cannot tell them apart, so say which one it is.
          note: topic || stale_days
            ? "nothing matched — there may still be other drafts open"
            : "no drafts are in progress right now",
        };
      }
      return cap(rows);
    },
  },
  medium: {
    class: "lens", domain: "*", kind: "mixed",
    description:
      "Everything about one medium in a single call: how much of it the owner consumes, how they rate it ON ITS OWN SCALE, what they own, and what they have written about it. Answering 'what are they like about film' otherwise takes four calls to four tools, and an assistant has to know all four exist.",
    schema: {
      type: "object",
      properties: {
        name: { type: "string", description: "film | book | tv | music | beer | restaurant" },
      },
    },
    async run(env, { name }) {
      // Shelf-aware, like `consumption`: t0_book is the whole Goodreads library
      // and 436 of its rows are to-read, so an unfiltered count reports 920
      // books read when the number is 443.
      const M = {
        film:  { table: "t0_film",   unit: "films",      verdicts: "films",
                 rate: { col: "rating",       scale: "0-5",  label: "title",      url: "origin_ref" },
                 owns: "dvd", reviews: true },
        book:  { table: "t0_book",   unit: "books read", verdicts: "books", where: "WHERE shelf = 'read'",
                 rate: { col: "my_rating",    scale: "0-5",  label: "title",
                         where: "AND shelf IN ('read','partly-read')" } },
        tv:    { table: "t0_tv",     unit: "shows",      verdicts: "tv" },
        music: { table: "t0_music",  unit: "scrobbles",  verdicts: "music", owns: "vinyl" },
        beer:  { table: "t0_beer",   unit: "check-ins",
                 rate: { col: "rating_score", scale: "0-5",  label: "beer_name" } },
        restaurant: { table: "t1_visits", unit: "visits",
                 rate: { col: "rating",       scale: "0-10", label: "restaurant" }, calibrated: true },
      };

      // No name: the directory, so an assistant can pick one rather than guess a
      // label. Cheaper than returning six full profiles and blowing the cap.
      if (!name || !M[name]) {
        const out = [];
        for (const [k, d] of Object.entries(M)) {
          const [r] = await q(env, `SELECT count(*) AS n FROM ${d.table} ${d.where ?? ""}`);
          out.push({ medium: k, unit: d.unit, total: r?.n ?? 0 });
        }
        return {
          ...cap(out),
          note: name
            ? `no medium called '${name}' — ask for one of these`
            : "ask for one by name to get ratings, what is owned, and what was written about it",
        };
      }

      const d = M[name];
      const [base] = await q(
        env,
        `SELECT count(*) AS n, max(substr(created,1,10)) AS last_logged
         FROM ${d.table} ${d.where ?? ""}`
      );
      const rec = { medium: name, consumed: base?.n ?? 0, unit: d.unit,
                    last_logged: base?.last_logged ?? null };

      if (name === "tv") {
        const [e] = await q(env, `SELECT sum(episodes_watched) AS n FROM t0_tv`);
        rec.episodes_watched = e?.n ?? null;
      }
      if (name === "beer") {
        const [b] = await q(env, `SELECT count(DISTINCT lower(beer_name)) AS n FROM t0_beer`);
        rec.distinct_beers = b?.n ?? null;
      }
      if (name === "book") {
        const [b] = await q(env, `SELECT count(*) AS n FROM t0_book WHERE shelf = 'to-read'`);
        rec.shelved_to_read = b?.n ?? 0;
      }

      if (d.rate) {
        const r = d.rate;
        const cast = `CAST(${r.col} AS REAL)`;
        const filter = `WHERE ${r.col} IS NOT NULL AND ${cast} > 0 ${r.where ?? ""}`;
        const [agg] = await q(
          env,
          `SELECT count(*) AS n, round(avg(${cast}), 2) AS average, max(${cast}) AS highest
           FROM ${d.table} ${filter}`
        );
        const urlCol = r.url ? `${r.url} AS url,` : "";
        const top = await q(
          env,
          `SELECT ${r.label} AS label, ${cast} AS rating, ${urlCol}
                  substr(created,1,10) AS when_
           FROM ${d.table} ${filter}
           ORDER BY ${cast} DESC, created DESC LIMIT 3`
        );
        // The scale rides with the numbers, always. A bare 9.5 next to a bare
        // 4.5 invites the reader to rank them, and they are not on the same axis.
        rec.rated = { n: agg?.n ?? 0, scale: r.scale, average: agg?.average ?? null,
                      highest: agg?.highest ?? null, top };
      }

      const written = {};
      if (d.verdicts) {
        const [v] = await q(env, `SELECT count(*) AS n FROM t1_verdicts WHERE kind = ?`, d.verdicts);
        written.verdicts = v?.n ?? 0;
      }
      if (d.reviews) {
        const [rv] = await q(env, `SELECT count(*) AS n FROM t1_film_review`);
        written.reviews = rv?.n ?? 0;
      }
      if (Object.keys(written).length) rec.written = written;

      if (d.owns) {
        const [o] = await q(env, `SELECT count(*) AS n FROM t1_collection WHERE kind = ?`, d.owns);
        rec.owns = { [d.owns]: o?.n ?? 0 };
      }

      const notes = [];
      // Calibration is not decoration here: the dining median is 8.1, so an 8
      // read against a 0-10 scale looks like praise and is not.
      if (d.calibrated) notes.push("the owner's 0-10 dining scale runs high — call taste_summary(kind:'dining') before reading any of these numbers as praise");
      if (name === "film") notes.push(WATCH_GAP);
      if (!d.rate) notes.push(`the owner does not rate ${name} item by item — the counts are the record`);

      return { rows: [rec], ...(notes.length ? { note: notes.join(" · ") } : {}) };
    },
  },

  consumption: {
    class: "revealed", domain: "*", kind: "event",
    description:
      "Shape and recency of what the owner consumes, per medium: how much, and how current the record is. Returns aggregates, not a list of titles.",
    schema: {
      type: "object",
      properties: { medium: { type: "string", description: "music | books | films | beer" } },
    },
    async run(env, { medium }) {
      // t0_book is the whole Goodreads library, not a reading log: 436 of its
      // rows are to-read and 38 partly-read, and `created` falls back to
      // date_added, so a shelved book looks consumed. Reporting one total said
      // 920 books were read when the number is 443.
      const T = {
        music: { table: "t0_music", where: "" },
        books: { table: "t0_book",  where: "WHERE shelf = 'read'" },
        films: { table: "t0_film",  where: "" },
        tv:    { table: "t0_tv",    where: "" },
        beer:  { table: "t0_beer",  where: "" },
      };
      const picked = medium ? { [medium]: T[medium] } : T;
      const out = [];
      for (const [name, d] of Object.entries(picked)) {
        if (!d) continue;
        const [row] = await q(
          env,
          `SELECT count(*) AS n, max(substr(created,1,10)) AS last_logged
           FROM ${d.table} ${d.where}`
        );
        const rec = { medium: name, total: row?.n ?? 0, last_logged: row?.last_logged ?? null };
        // Beer rows are check-ins, not distinct beers.
        if (name === "tv") {
          // Shows and episodes are different questions: 358 shows is what is
          // watched, 7,719 episodes is how much.
          const [e] = await q(env, `SELECT sum(episodes_watched) AS n FROM t0_tv`);
          rec.total_label = "shows";
          rec.episodes_watched = e?.n ?? null;
        }
        if (name === "beer") {
          const [b] = await q(env, `SELECT count(DISTINCT lower(beer_name)) AS n FROM t0_beer`);
          rec.distinct_beers = b?.n ?? null;
          rec.total_label = "check-ins";
        }
        if (name === "books") {
          const [b] = await q(env, `SELECT count(*) AS n FROM t0_book WHERE shelf = 'to-read'`);
          rec.total_label = "finished";
          rec.to_read_backlog = b?.n ?? 0;
        }
        out.push(rec);
      }
      return cap(out);
    },
  },

  posts: {
    class: "authored", domain: "mind", kind: "text",
    description:
      "The owner's published blog — articles, essays, lists, project write-ups — as opposed to the private notes behind them. Searches by meaning and returns each post's live URL. Pass full:true for the entire text of the best match.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "A topic, question, or phrase." },
        kind: {
          type: "string",
          description: "article | essay | notes | list | project | stub | recipe",
        },
        full: { type: "boolean", description: "Return one whole post instead of a list." },
      },
      required: ["topic"],
    },
    async run(env, { topic, kind, full }) {
      if (full) {
        return readOne(env, {
          topic, kind: "post", table: "t1_post", key: "slug", missing: "post",
          select: "title, url, published, kind, words, body",
          onClip: (r, kept, total) =>
            `body truncated to ${kept} of ${total} chars — the whole post is public at ${r.url}`,
        });
      }
      // Over-fetch, then filter: the vector index carries no post kind, so a
      // kind filter applied after the search would return 3 rows out of 20 and
      // look like the blog is empty on that kind.
      const hits = await search(env, topic, { k: kind ? 60 : MAX_ROWS, kind: "post" });
      if (!hits.length) return { rows: [], note: "nothing on the blog matched" };

      const slugs = hits.map((h) => h.ref).filter(Boolean);
      if (!slugs.length) return { rows: [], note: "nothing on the blog matched" };

      const meta = new Map();
      for (const r of await q(
        env,
        `SELECT slug, title, description, kind, published, url, words
         FROM t1_post WHERE slug IN (${slugs.map(() => "?").join(",")})`,
        ...slugs
      )) meta.set(r.slug, r);

      const rows = [];
      for (const h of hits) {
        const m = meta.get(h.ref);
        if (!m) continue;
        if (kind && (m.kind || "").toLowerCase() !== kind.toLowerCase()) continue;
        rows.push({
          title: m.title, url: m.url, published: m.published,
          kind: m.kind, words: m.words, description: m.description, score: h.score,
        });
      }
      if (!rows.length) return { rows: [], note: `matched posts, but none of kind '${kind}'` };
      return cap(rows);
    },
  },

  events: {
    class: "world", domain: "world", kind: "entity",
    description:
      "Upcoming DC events the owner could actually go to \u2014 a live pool merged from eight sources (library, theatre, improv, cinema, parties, music venues). Recurring series are collapsed to their next date, and no single source is allowed to dominate the answer. Filter by a topic word or free-only; pair with `taste_profile` for the venues and orgs they rate.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, venue or description." },
        from: { type: "string", description: "ISO date. Defaults to today; never volunteers the past." },
        to: { type: "string", description: "ISO date, inclusive." },
        free: { type: "boolean", description: "Only free events." },
      },
    },
    async run(env, { topic, free, from, to }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      // Two corrections to "ORDER BY start LIMIT 20":
      //
      // Recurring programming swamps everything. The library alone contributes
      // 500 of ~1,040 rows, and they are repeats: Family Story Time appears 39
      // times, Baby Lap Time 16. Collapsing a series to its next occurrence
      // (with a count) says the same thing in one row.
      //
      // Even collapsed, ordering purely by date let the largest feed take half
      // the answer regardless of relevance. Round-robin by source so a week of
      // library sessions cannot crowd out the cinema and the venues \u2014 pool
      // composition should not decide what gets shown.
      const rows = await q(
        env,
        `WITH f AS (
           SELECT title, feed, venue, location, free, url, start
           FROM t0_event
           -- Window, not just a floor. from- defaults to today so the tool
           -- never volunteers the past unasked, but an explicit past window is
           -- honoured literally and simply returns nothing rather than
           -- silently answering about the future instead.
           WHERE substr(start, 1, 10) >= COALESCE(?, date('now'))
             AND (? IS NULL OR substr(start, 1, 10) <= ?)
             AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(venue,'')) LIKE ?
                            OR lower(COALESCE(description,'')) LIKE ?)
             AND (? IS NULL OR free = 1)
         ), agg AS (
           -- Grouped by title alone, not (title, venue). Once branches became
           -- distinct venues, "Family Story Time" ran at six of them and took
           -- six of twenty slots. One row per distinct programme.
           SELECT title, feed, min(start) AS soonest, count(*) AS occurrences,
                  count(DISTINCT venue) AS venues,
                  min(free) AS free_min, max(free) AS free_max
           FROM f GROUP BY title, feed
         ), instance AS (
           -- The soonest OCCURRENCE, carried whole. min(venue)/min(url) per
           -- column looked equivalent and is not: min of a string is
           -- lexicographic, so a collapsed row showed one occurrence's date
           -- beside another occurrence's link — 16 of 117 upcoming series, and
           -- the link is the half a reader acts on.
           SELECT f.title, f.feed, f.venue, f.location, f.url, f.free, f.start,
                  row_number() OVER (PARTITION BY f.title, f.feed
                                     ORDER BY f.start, f.url) AS pick
           FROM f JOIN agg a ON a.title = f.title AND a.feed = f.feed
                            AND f.start = a.soonest
         ), series AS (
           SELECT i.title, i.feed, i.start, i.venue, i.location, i.url, i.free,
                  a.occurrences, a.venues, a.free_min, a.free_max
           FROM instance i JOIN agg a ON a.title = i.title AND a.feed = i.feed
           WHERE i.pick = 1
         ), ranked AS (
           -- Even collapsed, ordering purely by date let the largest feed take
           -- half the answer regardless of relevance. Round-robin by source so
           -- a week of library sessions cannot crowd out the cinema.
           SELECT *, row_number() OVER (PARTITION BY feed ORDER BY start) AS rn
           FROM series
         )
         SELECT title, start, venue, location, free, url, feed,
                CASE WHEN occurrences > 1 THEN occurrences END AS occurrences,
                CASE WHEN venues > 1 THEN venues END AS venues,
                -- free describes the instance shown, so say when the rest of
                -- the series disagrees rather than reporting max(free) and
                -- sending the reader to a paid night expecting a free one.
                CASE WHEN free_min <> free_max THEN 1 END AS price_varies
         FROM ranked WHERE rn <= 4
         ORDER BY start
         LIMIT ?`,
        from ?? null, to ?? null, to ?? null,
        like, like, like, like, free ? 1 : null, MAX_ROWS
      );
      return cap(rows);
    },
  },

  taste_profile: {
    class: "authored", domain: "world", kind: "judgement",
    description:
      "What the owner SAYS they like — stated preferences: venues and orgs they rate, things they seek out, things they avoid. Distinct from `taste`, which is revealed behaviour (play counts). When the two disagree, that gap is usually the interesting part.",
    schema: { type: "object", properties: {} },
    async run(env) {
      const rows = await q(env, `SELECT kind, key, value FROM t1_taste ORDER BY kind, key LIMIT ?`, MAX_ROWS);
      return cap(rows);
    },
  },

  // The one tool that justifies one record rather than seven APIs: every other
  // tool reads a single table, but ADR-0001 exists so `taste ⋈ notes ⋈
  // scrobbles` can be asked as one question.
  around_the_time: {
    class: "lens", domain: "*", kind: "mixed",
    description:
      "What was going on around a period: what the owner wrote, listened to, read or watched. Answers 'what were they thinking about in March' — a window, not a topic. Periods are forgotten more easily than subjects, so this is often the useful lens.",
    schema: {
      type: "object",
      properties: {
        from: { type: "string", description: "ISO date, e.g. 2026-03-01" },
        to: { type: "string", description: "ISO date, e.g. 2026-03-31" },
      },
      required: ["from", "to"],
    },
    async run(env, { from, to }) {
      const notes = await q(
        env,
        `SELECT 'note' AS kind, title AS label, substr(created,1,10) AS when_
         FROM t1_notes WHERE substr(created,1,10) BETWEEN ? AND ?
         ORDER BY created LIMIT 8`,
        from, to
      );
      const music = await q(
        env,
        `SELECT 'artist' AS kind, artist AS label, count(*) AS plays
         FROM t0_music WHERE substr(created,1,10) BETWEEN ? AND ?
         GROUP BY artist ORDER BY plays DESC LIMIT 5`,
        from, to
      );
      // Ratings ride along: "X was watched" and "X was watched and rated 5"
      // are different facts, and the second is the one worth having. Each
      // branch is limited separately — a single LIMIT over the UNION let a
      // heavy film month discard the books entirely.
      const films = await q(
        env,
        `SELECT 'film' AS kind, title AS label, substr(created,1,10) AS when_,
                nullif(CAST(rating AS TEXT),'') AS rating, '0-5' AS scale
         FROM t0_film WHERE substr(created,1,10) BETWEEN ? AND ?
         ORDER BY created LIMIT 4`,
        from, to
      );
      const books = await q(
        env,
        `SELECT 'book' AS kind, title AS label, substr(created,1,10) AS when_,
                nullif(CAST(my_rating AS TEXT),'0') AS rating, '0-5' AS scale
         FROM t0_book WHERE substr(created,1,10) BETWEEN ? AND ?
           AND shelf = 'read'
         ORDER BY created LIMIT 4`,
        from, to
      );
      const tv = await q(
        env,
        `SELECT 'tv' AS kind, title AS label, substr(created,1,10) AS when_,
                CAST(episodes_watched AS TEXT) AS rating, 'episodes' AS scale
         FROM t0_tv WHERE substr(created,1,10) BETWEEN ? AND ?
         ORDER BY created LIMIT 4`,
        from, to
      );
      // Interleaved, not concatenated. The per-branch LIMITs above exist so a
      // heavy film month cannot discard the books — but 8+5+4+4+4 is 25 against
      // a 20-row cap, so concatenating handed the cap the same starvation from
      // the other end: in a full month (March 2026 fills every branch) books
      // lost one row and television lost all four, every time. Round-robin
      // means the cap trims the tail of each source rather than the last source
      // whole.
      const branches = [notes, music, films, books, tv];
      const all = [];
      for (let i = 0; branches.some((b) => i < b.length); i++) {
        for (const b of branches) if (i < b.length) all.push(b[i]);
      }
      return all.length
        ? cap(all)
        : { rows: [], note: `nothing recorded between ${from} and ${to} — check the last_logged dates from consumption, the exports lag` };
    },
  },

  ratings: {
    class: "revealed", domain: "*", kind: "judgement",
    description:
      "What the owner rated and how highly, per medium. Use this to judge taste from behaviour rather than prose \u2014 `verdicts` has only 10 written opinions, while they have rated 720 films, 409 books, 1,906 beers and 93 restaurants. Scales differ per medium and are returned with each row; do not compare a 9.5 restaurant to a 5 film.",
    schema: {
      type: "object",
      properties: {
        medium: { type: "string", description: "films | books | beer | restaurants" },
        min_rating: { type: "number", description: "Only at or above this, on that medium's own scale." },
      },
    },
    async run(env, { medium, min_rating }) {
      // scale is carried per row because the mediums disagree: Letterboxd and
      // Goodreads are 0-5, the restaurant log is 0-10. Returning a bare 9.5
      // invites an assistant to read it as "off the charts" on a five-point
      // scale, or a 4.5 film as mediocre.
      const SRC = {
        films:       { table: "t0_film",   label: "title",      col: "rating",       scale: "0-5", url: "origin_ref" },
        books:       { table: "t0_book",   label: "title",      col: "my_rating",    scale: "0-5", where: "AND shelf IN ('read','partly-read')" },
        beer:        { table: "t0_beer",   label: "beer_name",  col: "rating_score", scale: "0-5"  },
        restaurants: { table: "t1_visits", label: "restaurant", col: "rating",       scale: "0-10" },
      };
      const picked = medium ? { [medium]: SRC[medium] } : SRC;
      const out = [];
      for (const [name, d] of Object.entries(picked)) {
        if (!d) continue;
        const floor = min_rating ?? 0;
        // origin_ref on t0_film is the Letterboxd permalink — it was published
        // all along and no tool returned it, so an assistant could name a film
        // but never point at it.
        const urlCol = d.url ? `${d.url} AS url,` : "";
        const rows = await q(
          env,
          `SELECT ${d.label} AS label, CAST(${d.col} AS REAL) AS rating, ${urlCol}
                  substr(created,1,10) AS when_
           FROM ${d.table}
           WHERE ${d.col} IS NOT NULL AND CAST(${d.col} AS REAL) > 0
             AND CAST(${d.col} AS REAL) >= ? ${d.where ?? ""}
           ORDER BY CAST(${d.col} AS REAL) DESC, created DESC
           LIMIT ?`,
          floor, medium ? MAX_ROWS : 5
        );
        for (const r of rows) out.push({ medium: name, scale: d.scale, ...r });
      }
      return cap(out);
    },
  },

  reviews: {
    class: "authored", domain: "culture", kind: "text",
    description:
      "The owner's written film reviews from Letterboxd \u2014 115 of them, in their own words, each with a link. Far more of their actual criticism than `verdicts` (10). Search by topic or filter to a minimum rating; quote them rather than paraphrasing.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against film title or review text." },
        min_rating: { type: "number", description: "Only films rated at least this, 0-5." },
      },
    },
    async run(env, { topic, min_rating }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const rows = await q(
        env,
        `SELECT title, year, rating, review, url, substr(created,1,10) AS watched
         FROM t1_film_review
         WHERE (? IS NULL OR lower(title) LIKE ? OR lower(review) LIKE ?)
           AND (? IS NULL OR CAST(nullif(rating,'') AS REAL) >= ?)
         ORDER BY created DESC
         LIMIT ?`,
        like, like, like,
        min_rating ?? null, min_rating ?? 0,
        MAX_ROWS
      );
      return cap(rows);
    },
  },

  collection: {
    class: "possession", domain: "culture", kind: "entity",
    description:
      "What the owner OWNS, which is not what they consumed: 89 vinyl records, 66 DVDs, 24 board games, 7 fragrances. Buying and keeping a thing is a stronger signal than playing it once \u2014 use this when the question is about taste they committed to. Fragrances carry the owner's own written notes.",
    schema: {
      type: "object",
      properties: {
        kind: { type: "string", description: "vinyl | dvd | board_game | fragrance" },
        topic: { type: "string", description: "Match against title, creator or genre." },
      },
    },
    async run(env, { kind, topic }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const rows = await q(
        env,
        // plays is computed, not stored. The sheet records what is owned and the
        // store holds 40,561 scrobbles, so the count is a join rather than a
        // field to keep in sync, and fresher than the enrichment it replaces.
        // Matched on artist AND album (74 of 89): artist alone reported the
        // total Bladee plays against every Bladee record.
        `SELECT c.kind, c.title, c.creator, c.genre, c.form, c.acquired,
                c.url, c.thoughts, c.cost, c.retailer, p.plays
         FROM t1_collection c
         LEFT JOIN (
           SELECT lower(artist) AS a, lower(album) AS al, count(*) AS plays
           FROM t0_music GROUP BY 1, 2
         ) p ON c.kind = 'vinyl'
            AND lower(c.creator) = p.a AND lower(c.title) = p.al
         WHERE (? IS NULL OR c.kind = ?)
           AND (? IS NULL OR lower(c.title) LIKE ? OR lower(COALESCE(c.creator,'')) LIKE ?
                          OR lower(COALESCE(c.genre,'')) LIKE ?)
         ORDER BY COALESCE(c.acquired,'') DESC, c.title
         LIMIT ?`,
        kind ?? null, kind ?? null,
        like, like, like, like,
        MAX_ROWS
      );
      // Drop the columns a given kind does not use rather than emitting a wall
      // of nulls — a board game has no genre and a DVD has no thoughts.
      return cap(rows.map((r) => Object.fromEntries(
        Object.entries(r).filter(([, v]) => v !== null && v !== "")
      )));
    },
  },

  saves: {
    class: "intent", domain: "*", kind: "pointer",
    description:
      "Links the owner bookmarked \u2014 2,188 of them across nine years. Filter by platform (youtube, substack, x, pinterest, tiktok, soundcloud...), by collection (the owner's own buckets: Gift Ideas, thought-provoking, aesthetically-pleasing, want-to-think-about, Tattoo Inspiration, yummy...), by kind, by tag, or by topic. Call with no arguments to see which platforms and collections exist before narrowing. A save is intent, not consumption: it caught their attention, they did not necessarily finish it.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, tags or collection." },
        platform: { type: "string", description: "youtube | substack | x | pinterest | tiktok | soundcloud | etsy ..." },
        collection: { type: "string", description: "One of the owner's collections, e.g. 'thought-provoking'." },
        kind: { type: "string", description: "link | article | video | image | audio | document" },
        tag: { type: "string", description: "A single tag." },
        since: { type: "string", description: "ISO date; only saves on or after." },
        with_note: { type: "boolean", description: "Only saves with a note written on them." },
      },
    },
    async run(env, { topic, platform, collection, kind, tag, since, with_note }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;

      // With nothing to narrow by, return the AXES rather than 20 arbitrary
      // links. A flat sample of a 2,188-item library teaches an assistant
      // nothing about what is in it; the facets tell it what it can ask next,
      // which is the same job the standing brief does one level up.
      if (!topic && !platform && !collection && !kind && !tag && !since && !with_note) {
        const plats = await q(env,
          `SELECT platform AS name, count(*) AS n FROM t0_raindrop
           GROUP BY 1 ORDER BY n DESC LIMIT 10`);
        const cols = await q(env,
          `SELECT collection AS name, count(*) AS n FROM t0_raindrop
           WHERE collection <> '' GROUP BY 1 ORDER BY n DESC LIMIT 10`);
        const [tot] = await q(env,
          `SELECT count(*) AS n, min(substr(created,1,10)) AS oldest,
                  max(substr(created,1,10)) AS newest FROM t0_raindrop`);
        return {
          rows: [],
          total: tot?.n ?? 0,
          span: `${tot?.oldest ?? "?"} .. ${tot?.newest ?? "?"}`,
          platforms: plats,
          collections: cols,
          note: "no filter given — these are the axes; call again with one to get links",
        };
      }

      const rows = await q(
        env,
        `SELECT title, url, kind, platform, collection, tags,
                nullif(note,'') AS note, substr(created,1,10) AS saved
         FROM t0_raindrop
         WHERE (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(tags,'')) LIKE ?
                          OR lower(COALESCE(collection,'')) LIKE ?)
           AND (? IS NULL OR lower(platform) = lower(?))
           AND (? IS NULL OR lower(collection) = lower(?))
           AND (? IS NULL OR kind = ?)
           AND (? IS NULL OR lower(COALESCE(tags,'')) LIKE ?)
           AND (? IS NULL OR substr(created,1,10) >= ?)
           AND (? IS NULL OR note <> '')
         ORDER BY created DESC
         LIMIT ?`,
        like, like, like, like,
        platform ?? null, platform ?? "",
        collection ?? null, collection ?? "",
        kind ?? null, kind ?? "",
        tag ?? null, tag ? `%${tag.toLowerCase()}%` : "",
        since ?? null, since ?? "",
        with_note ? 1 : null,
        MAX_ROWS
      );
      const [tot] = await q(env,
        `SELECT count(*) AS n, max(substr(created,1,10)) AS newest,
                sum(CASE WHEN note <> '' THEN 1 ELSE 0 END) AS noted FROM t0_raindrop`);
      // Say when the commentary is absent rather than letting silence read as
      // "these were saved without comment". 1,906 of these carry a note in
      // raindrop; none are here until a token-based sync pulls them, because the
      // MCP that seeded this snapshot does not return the field.
      const noted = tot?.noted ?? 0;
      return {
        ...cap(rows),
        scope: `searched ${tot?.n ?? 0} saves, newest ${tot?.newest ?? "?"}`,
        ...(noted === 0
          ? { notes: "the owner's own notes on these saves are not synced yet — absence here is not evidence none were written" }
          : { notes: `${noted} of these carry a note the owner wrote` }),
      };
    },
  },

  backlog: {
    class: "intent", domain: "*", kind: "pointer",
    description:
      "What the owner has queued but not done — things they decided they wanted and have not gotten to. Shelving a book or filing a link into a bucket is a deliberate act, which is what separates this from `saves` (attention) and from `collection` (already owned). Kinds: 'read' (436 shelved to-read), 'resume' (41 books started and abandoned mid-way — the strongest candidates, since they already began), 'make' (things they want to build or cook), 'buy' (gift and shopping ideas). Call with no kind to see the sizes. Default order is newest-first; pass order='oldest' to dig up what has been sitting, which is usually the point of asking.",
    schema: {
      type: "object",
      properties: {
        kind: { type: "string", description: "read | resume | make | buy" },
        topic: { type: "string", description: "Match against title or author." },
        since: { type: "string", description: "ISO date; only things queued on or after." },
        order: { type: "string", description: "recent (default) | oldest" },
      },
    },
    async run(env, { kind, topic, since, order }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const dir = order === "oldest" ? "ASC" : "DESC";

      // The kinds are not one table. Books carry their queue state in a shelf
      // column; the making and buying queues are raindrop collections created
      // by hand. Keeping them as named kinds rather than one union
      // means each can say what it actually knows.
      const SHELVES = {
        read: ["to-read"],
        resume: ["partly-read", "currently-reading"],
      };
      const COLLECTIONS = {
        make: ["want-to-make"],
        buy: ["Gift Ideas", "Shopping"],
      };

      // No kind: return the sizes rather than an arbitrary 20 rows off one pile.
      // Same reasoning as `saves` — the shape of the backlog is the useful
      // answer when nothing has been narrowed.
      if (!kind) {
        const [b] = await q(env,
          `SELECT sum(CASE WHEN lower(shelf) = 'to-read' THEN 1 ELSE 0 END) AS to_read,
                  sum(CASE WHEN lower(shelf) IN ('partly-read','currently-reading') THEN 1 ELSE 0 END) AS resume
           FROM t0_book`);
        const [m] = await q(env,
          `SELECT sum(CASE WHEN lower(collection) = 'want-to-make' THEN 1 ELSE 0 END) AS make,
                  sum(CASE WHEN collection IN ('Gift Ideas','Shopping') THEN 1 ELSE 0 END) AS buy
           FROM t0_raindrop`);
        return {
          rows: [],
          kinds: [
            { kind: "read", n: b?.to_read ?? 0, source: "goodreads shelf" },
            { kind: "resume", n: b?.resume ?? 0, source: "goodreads shelf" },
            { kind: "make", n: m?.make ?? 0, source: "raindrop collection" },
            { kind: "buy", n: m?.buy ?? 0, source: "raindrop collection" },
          ],
          note: "no kind given — these are the piles; call again with one",
          gap: WATCH_GAP,
        };
      }

      if (SHELVES[kind]) {
        const shelves = SHELVES[kind];
        const rows = await q(
          env,
          // date_added arrives in two shapes from Goodreads exports
          // ('2026-08-16' and '2025-10-24T00:00:00'), so every comparison and
          // every emitted date goes through substr — an unnormalised >= would
          // silently drop the T-suffixed rows.
          `SELECT title, book_author AS author, shelf,
                  substr(date_added,1,10) AS queued, avg_rating
           FROM t0_book
           WHERE lower(shelf) IN (${shelves.map(() => "?").join(",")})
             AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(book_author,'')) LIKE ?)
             AND (? IS NULL OR substr(date_added,1,10) >= ?)
           ORDER BY substr(date_added,1,10) ${dir}, title
           LIMIT ?`,
          ...shelves,
          like, like, like,
          since ?? null, since ?? "",
          MAX_ROWS
        );
        const [tot] = await q(env,
          `SELECT count(*) AS n, min(substr(date_added,1,10)) AS oldest
           FROM t0_book WHERE lower(shelf) IN (${shelves.map(() => "?").join(",")})`,
          ...shelves);
        return {
          ...cap(rows),
          scope: `${tot?.n ?? 0} on this shelf, oldest queued ${tot?.oldest ?? "?"}`,
          // my_rating is 0 across every unread row, so it is omitted rather
          // than emitted as a zero an assistant would read as a verdict.
          notes: "avg_rating is Goodreads' crowd score, not the owner's — they have not read these",
          gap: WATCH_GAP,
        };
      }

      if (COLLECTIONS[kind]) {
        const cols = COLLECTIONS[kind];
        const rows = await q(
          env,
          `SELECT title, url, platform, collection, nullif(note,'') AS note,
                  substr(created,1,10) AS queued
           FROM t0_raindrop
           WHERE collection IN (${cols.map(() => "?").join(",")})
             AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(tags,'')) LIKE ?)
             AND (? IS NULL OR substr(created,1,10) >= ?)
           ORDER BY substr(created,1,10) ${dir}, title
           LIMIT ?`,
          ...cols,
          like, like, like,
          since ?? null, since ?? "",
          MAX_ROWS
        );
        const [tot] = await q(env,
          `SELECT count(*) AS n FROM t0_raindrop WHERE collection IN (${cols.map(() => "?").join(",")})`,
          ...cols);
        return {
          ...cap(rows),
          scope: `${tot?.n ?? 0} in ${cols.join(" + ")}`,
          // 'want-to-make' holds 9 items against 285 untagged Pinterest saves
          // and a wall of TikToks. Reporting 9 as the making backlog without
          // this would understate it by an order of magnitude.
          ...(kind === "make"
            ? { notes: "only what was filed into want-to-make; most making references sit unfiled in the pinterest and tiktok saves — try saves(platform:'pinterest')" }
            : {}),
          gap: WATCH_GAP,
        };
      }

      return {
        rows: [],
        error: `unknown kind '${kind}'`,
        kinds: ["read", "resume", "make", "buy"],
        gap: WATCH_GAP,
      };
    },
  },

  recent_topics: {
    class: "dialogue", domain: "mind", kind: "event",
    description:
      "What the owner has been working through in conversation lately \u2014 titles and volume, not transcripts. Fresher than their notes, which lag a deliberate act of capture. Turn count is the signal a title cannot carry: 40 turns is a preoccupation, 3 is a passing look. Use this to know what is live for them right now.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against the conversation title." },
        min_turns: { type: "number", description: "Only conversations at least this long." },
      },
    },
    async run(env, { topic, min_turns }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const rows = await q(
        env,
        `SELECT title, last_seen, started, turns, his_turns,
                -- Clipped here, whole in the thread tool. Listing threads with
                -- no distillation meant the only route to one was already
                -- knowing which thread to ask about, which is the opposite of
                -- what a list is for.
                substr(COALESCE(summary,''), 1, 200) AS gist
         FROM t0_chat_topic
         WHERE (? IS NULL OR lower(title) LIKE ?)
           AND (? IS NULL OR turns >= ?)
         ORDER BY last_seen DESC, turns DESC
         LIMIT ?`,
        like, like, min_turns ?? null, min_turns ?? 0, MAX_ROWS
      );
      return cap(rows);
    },
  },

  thread: {
    class: "dialogue", domain: "mind", kind: "text",
    description:
      "One conversation. `include` chooses what comes back: 'conclusion' (where the owner landed, plus the machine distillation), 'turns' (only what THEY typed), 'dialogue' (both sides interleaved, speaker-tagged), or 'both' (default: conclusion + owner turns). Prefer 'dialogue' when those turns read as questions or fragments \u2014 45% of them are under 80 chars and answer something you cannot otherwise see. Lines tagged assistant are another model's output: context for reading the owner, not verified fact, and never quote them as the owner's.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Thread title, or what it was about." },
        include: { type: "string", description: "conclusion | turns | both (default both)" },
      },
      required: ["topic"],
    },
    async run(env, { topic, include }) {
      const want = include || "both";
      const [hit] = await q(
        env,
        `SELECT title, landed, turns, his_turns, last_seen,
                COALESCE(summary, '') AS summary, COALESCE(summary_by, '') AS summary_by
         FROM t0_chat_topic
         WHERE lower(title) LIKE ?
         ORDER BY turns DESC LIMIT 1`,
        `%${(topic || "").toLowerCase()}%`
      );
      if (!hit) return { rows: [], note: "no thread matched — try recent_topics to see what exists" };

      const out = {
        title: hit.title,
        when: hit.last_seen,
        turns: hit.turns,
        his_turns: hit.his_turns,
      };
      if (want !== "turns") {
        out.landed = hit.landed || null;
        // Attributed, and never presented as authored. The verbatim `landed` sits
        // beside it deliberately: a summary a reader can check against the
        // source is a different object from one they must believe.
        if (hit.summary) {
          out.summary = hit.summary;
          out.summary_is = `machine-written by ${hit.summary_by}, not the owner's words`;
        }
      }

      if (want === "dialogue") {
        // Interleaved and speaker-tagged. Owner turns are often questions —
        // returning them alone reads as a list of half-thoughts and invites a
        // reader to reconstruct the missing half by guessing.
        const turns = await q(
          env,
          `SELECT turn, channel, text FROM t0_chat
           WHERE title = ? ORDER BY CAST(turn AS INTEGER) LIMIT 400`,
          hit.title
        );
        const lines = turns.map(
          (t) => `${t.channel === "his" ? (env.OWNER_LABEL || "owner") : "assistant"}: ${t.text || ""}`
        );
        // Set before budgeting: the note is part of the row it has to fit in.
        out.dialogue_note =
          "lines tagged assistant are another model's output — context, not fact, and not the owner's words";
        const kept = fitInto(out, "dialogue", lines, TURN_NOTE_SAMPLE);
        out.dialogue = kept;
        if (kept.length < turns.length) {
          out.note = `${kept.length} of ${turns.length} turns shown \u2014 ask about a narrower part`;
        }
        return cap([out]);
      }

      if (want !== "conclusion") {
        // Owner turns are short (176 chars average), so a thread's whole side
        // usually fits. Ordered, and capped by the same budget as everything
        // else rather than by a turn count, because thread lengths vary 100x.
        const turns = await q(
          env,
          `SELECT turn, text FROM t0_chat
           WHERE title = ? ORDER BY CAST(turn AS INTEGER) LIMIT 200`,
          hit.title
        );
        const kept = fitInto(out, "his_words", turns.map((t) => t.text || ""), TURN_NOTE_SAMPLE);
        out.his_words = kept;
        if (kept.length < turns.length) {
          out.note = `${kept.length} of ${turns.length} of the owner's turns shown — ask about a narrower part`;
        }
      }
      return cap([out]);
    },
  },

  taste_summary: {
    class: "derived", domain: "*", kind: "judgement",
    description:
      "Derived summaries of the owner's taste: how their rating scales actually behave, and the clusters their loved items fall into. Read the dining one BEFORE interpreting any restaurant rating \u2014 that 0-10 scale has a median of 8.1, so an 8 is average, not a rave.",
    schema: {
      type: "object",
      properties: { kind: { type: "string", description: "dining | clusters" } },
    },
    async run(env, { kind }) {
      const rows = kind
        ? await q(env, `SELECT kind, text FROM t0_taste_derived WHERE kind = ?`, kind)
        : await q(env, `SELECT kind, text FROM t0_taste_derived`);
      // These are documents, not row sets: one is ~6KB and truncating it mid
      // table would drop exactly the calibration it exists to convey. Return one
      // at a time rather than clipping both.
      if (!kind && rows.length > 1) {
        return {
          rows: rows.map((r) => ({ kind: r.kind, chars: r.text.length })),
          note: "ask for one by kind — these are documents, and clipping them loses the point",
        };
      }
      return cap(rows);
    },
  },

  places: {
    class: "authored", domain: "table", kind: "entity",
    description:
      "Restaurants the owner has been to, with their own notes and ratings. Filter by city or cuisine.",
    schema: {
      type: "object",
      properties: { city: { type: "string" }, cuisine: { type: "string" } },
    },
    async run(env, { city, cuisine }) {
      const rows = await q(
        env,
        `SELECT restaurant, city, neighborhood, cuisine_1, rating, notes
         FROM t1_visits
         WHERE (? IS NULL OR lower(city) = lower(?))
           AND (? IS NULL OR lower(cuisine_1) = lower(?))
         ORDER BY rating DESC LIMIT ?`,
        city ?? null, city ?? null, cuisine ?? null, cuisine ?? null, MAX_ROWS
      );
      return cap(rows);
    },
  },
  /**
   * The four project tools. The workshop was the one large pile this surface
   * could not see: an assistant could quote the notes ABOUT a project and not
   * know whether it had been touched since spring, or that it existed at all.
   *
   * The store holds prose and metadata only — READMEs, ADRs, commit subjects,
   * TODO markers. No source code is captured at any layer, so a reader that
   * offers to review the code from here is offering something that does not
   * exist. Every description below says so, because the tool list is the only
   * thing a client reads before deciding what this server can do.
   */
  projects: {
    class: "possession", domain: "workshop", kind: "entity",
    description:
      "The owner's repos — what they are building, what they set down, and what each one claims to be. `status` is heat, not judgement: active (commit in 21 days), warm (90), stalled (a year), dormant (older, which includes everything that simply shipped). Repos filed under a group like 'hiatus' were deliberately set aside — that is deliberate shelving, not a guess. Call with no arguments for the shape of the workshop: the status counts plus what has actually been worked on, ranked by commits in the last 90 days rather than by last-commit date, because half these repos were git-init'd the same week and recency alone floats a one-commit import above a year of work. Prose and metadata only — no source code is in this store.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against name, description or language." },
        status: { type: "string", description: "active | warm | stalled | dormant" },
        group: { type: "string", description: "The folder it is filed under, e.g. 'hiatus'." },
        order: { type: "string", description: "worked (default, commits in 90d) | recent | biggest" },
      },
    },
    async run(env, { topic, status, group, order }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const since = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);

      // Bound, not interpolated. `since` is derived from the clock rather than
      // from the caller, so this is not injectable today — but it was the only
      // value in this file spliced into SQL as text, and the header above says
      // the tool surface is the security boundary. The placeholder is first in
      // both statements below because SQLite binds by position in the SQL text
      // and this subquery sits in the FROM clause, ahead of every filter.
      const RECENT = `(SELECT repo, count(*) AS n FROM t1_project_commit
                       WHERE substr(committed_at,1,10) >= ? GROUP BY repo)`;

      if (!topic && !status && !group) {
        const bands = await q(env,
          `SELECT status, count(*) AS n FROM t1_project GROUP BY status ORDER BY n DESC`);
        const hot = await q(env,
          `SELECT p.name, p.slug, p.status, c.n AS commits_90d, p.description
           FROM t1_project p JOIN ${RECENT} c ON c.repo = p.slug
           ORDER BY c.n DESC LIMIT 8`,
          since);
        return {
          ...cap(hot),
          statuses: bands,
          note: "no filter given — these are the status bands and the repos with the most commits in the last 90 days; call again with topic, status or group",
        };
      }

      const sort = order === "recent" ? "p.last_commit DESC"
        : order === "biggest" ? "p.commit_count DESC"
        : "coalesce(c.n, 0) DESC, p.last_commit DESC";

      const rows = await q(
        env,
        `SELECT p.name, p.slug, p.grouping AS filed_under, p.github, p.status,
                p.description, p.languages, p.commit_count,
                coalesce(c.n, 0) AS commits_90d,
                substr(p.last_commit,1,10) AS last_commit, p.doc_count
         FROM t1_project p LEFT JOIN ${RECENT} c ON c.repo = p.slug
         WHERE (? IS NULL OR lower(p.name) LIKE ? OR lower(p.description) LIKE ?
                            OR lower(p.languages) LIKE ?)
           AND (? IS NULL OR p.status = ?)
           AND (? IS NULL OR lower(p.grouping) = lower(?))
         ORDER BY ${sort} LIMIT ?`,
        since,
        like, like, like, like, status ?? null, status ?? null,
        group ?? null, group ?? null, MAX_ROWS
      );
      return cap(rows);
    },
  },

  project_activity: {
    class: "revealed", domain: "workshop", kind: "event",
    description:
      "What the owner actually worked on, dated — commit subjects from their own repos. This is the closest thing in the store to a work log, and unlike their notes it cannot be stale: a commit is written at the moment of the work. Use it to answer what they have been doing lately, when a project came alive or died, or what was happening around a date. With no repo it returns per-repo totals for the window; with a repo it returns the subjects themselves. Subjects only — never a diff, and no source code is in this store.",
    schema: {
      type: "object",
      properties: {
        repo: { type: "string", description: "Repo name or slug, e.g. 'warehouse' or 'hiatus/mosaic'." },
        from: { type: "string", description: "ISO date. Defaults to 30 days ago." },
        to: { type: "string", description: "ISO date, inclusive. Defaults to today." },
      },
    },
    async run(env, { repo, from, to }) {
      const start = from ?? new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10);
      const end = to ?? new Date().toISOString().slice(0, 10);

      if (!repo) {
        const rows = await q(
          env,
          `SELECT c.repo, count(*) AS commits,
                  min(substr(c.committed_at,1,10)) AS first,
                  max(substr(c.committed_at,1,10)) AS last
           FROM t1_project_commit c
           WHERE substr(c.committed_at,1,10) BETWEEN ? AND ?
           GROUP BY c.repo ORDER BY commits DESC LIMIT ?`,
          start, end, MAX_ROWS
        );
        return {
          ...cap(rows),
          window: { from: start, to: end },
          note: rows.length ? "per-repo totals — ask again with a repo for the subjects" : "no commits in this window",
        };
      }

      const like = `%${repo.toLowerCase()}%`;
      const rows = await q(
        env,
        `SELECT c.repo, substr(c.committed_at,1,10) AS date, c.subject
         FROM t1_project_commit c
         WHERE (lower(c.repo) = lower(?) OR lower(c.repo) LIKE ?)
           AND substr(c.committed_at,1,10) BETWEEN ? AND ?
         ORDER BY c.committed_at DESC LIMIT ?`,
        repo, like, start, end, MAX_ROWS
      );
      return { ...cap(rows), window: { from: start, to: end } };
    },
  },

  project_docs: {
    class: "authored", domain: "workshop", kind: "text",
    description:
      "The prose those repos carry: READMEs, CONTEXT glossaries, architecture decision records and plan documents. This is where a project states what it is FOR and why it was built the way it was — an ADR is the owner arguing with themselves and recording who won, which is the same category of writing as their notes and usually more decided. Search by topic to find which project already settled a question, or pass a repo to read what it says about itself. Returns matching excerpts; ask with full=true and a title to read one whole document.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against document title or body." },
        repo: { type: "string", description: "Repo name or slug." },
        kind: { type: "string", description: "readme | context | adr | plan | claude | doc" },
        full: { type: "boolean", description: "Return one whole document rather than excerpts." },
      },
    },
    async run(env, { topic, repo, kind, full }) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const rlike = repo ? `%${repo.toLowerCase()}%` : null;

      const rows = await q(
        env,
        `SELECT repo, path, kind, title, body, chars
         FROM t1_project_doc
         WHERE (? IS NULL OR lower(title) LIKE ? OR lower(body) LIKE ?)
           AND (? IS NULL OR lower(repo) = lower(?) OR lower(repo) LIKE ?)
           AND (? IS NULL OR kind = ?)
         ORDER BY CASE kind WHEN 'context' THEN 0 WHEN 'readme' THEN 1
                            WHEN 'adr' THEN 2 ELSE 3 END, repo, path
         LIMIT ?`,
        like, like, like, rlike ? repo : null, repo ?? null, rlike,
        kind ?? null, kind ?? null, full ? 1 : MAX_ROWS
      );

      if (full) {
        // One document whole. Excerpting an ADR is how you end up quoting the
        // option that was rejected: the decision is at the bottom, the alternatives
        // are in the middle, and a keyword hit lands anywhere.
        if (!rows.length) return { rows: [], note: "no document matched" };
        return cap([rows[0]]);
      }

      // Excerpt around the hit rather than shipping bodies. A CONTEXT.md is
      // 8KB and the byte cap would otherwise spend the whole answer on one file.
      const out = rows.map((r) => {
        const body = r.body ?? "";
        const at = like ? body.toLowerCase().indexOf(topic.toLowerCase()) : 0;
        const from = Math.max(0, (at < 0 ? 0 : at) - 200);
        const excerpt = body.slice(from, from + 700).replace(/\s+/g, " ").trim();
        return {
          repo: r.repo, path: r.path, kind: r.kind, title: r.title,
          chars: r.chars,
          excerpt: (from > 0 ? "…" : "") + excerpt + (from + 700 < body.length ? "…" : ""),
        };
      });
      return {
        ...cap(out),
        note: "excerpts — ask again with full=true and a narrower repo/kind to read one whole",
      };
    },
  },

  project_open: {
    class: "intent", domain: "workshop", kind: "pointer",
    description:
      "What is visibly unfinished in those repos: TODO and FIXME markers left in code, unchecked items in plan documents, and files sitting uncommitted. Useful for 'what could I pick up' and for reading intent — a marker is a note written to oneself at the moment of choosing not to do something. Treat it as a trail, not a backlog: nobody prunes these, so an old marker may name work that was done another way. With no repo it returns where the unfinished work is concentrated. Paths and marker text only, never file contents.",
    schema: {
      type: "object",
      properties: {
        repo: { type: "string", description: "Repo name or slug." },
        kind: { type: "string", description: "marker | unchecked | uncommitted" },
      },
    },
    async run(env, { repo, kind }) {
      if (!repo) {
        const rows = await q(
          env,
          `SELECT o.repo, count(*) AS open_items,
                  sum(CASE WHEN o.kind = 'marker' THEN 1 ELSE 0 END) AS markers,
                  sum(CASE WHEN o.kind = 'unchecked' THEN 1 ELSE 0 END) AS unchecked,
                  sum(CASE WHEN o.kind = 'uncommitted' THEN 1 ELSE 0 END) AS uncommitted
           FROM t1_project_open o
           WHERE (? IS NULL OR o.kind = ?)
           GROUP BY o.repo ORDER BY open_items DESC LIMIT ?`,
          kind ?? null, kind ?? null, MAX_ROWS
        );
        return { ...cap(rows), note: "where unfinished work sits — ask again with a repo for the items" };
      }

      const like = `%${repo.toLowerCase()}%`;
      const rows = await q(
        env,
        `SELECT repo, kind, path, line, text
         FROM t1_project_open
         WHERE (lower(repo) = lower(?) OR lower(repo) LIKE ?)
           AND (? IS NULL OR kind = ?)
         ORDER BY CASE kind WHEN 'unchecked' THEN 0 WHEN 'marker' THEN 1 ELSE 2 END, path, line
         LIMIT ?`,
        repo, like, kind ?? null, kind ?? null, MAX_ROWS
      );
      return cap(rows);
    },
  },
};
