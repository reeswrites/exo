/**
 * The tool surface — fixed, named questions with bounded answers.
 *
 * There is no general-purpose tool here: no raw SQL, no free-form query. Every
 * tool is a named question whose answer has a known shape, so a caller learns
 * the vocabulary once and an answer never needs parsing to be trusted. What is
 * reachable at all was decided upstream by `exo publish` (ADR-0005): held
 * material is absent from this database, not filtered here.
 *
 * Every list answer is a PAGE: `limit` rows (DEFAULT_ROWS unless the caller
 * says otherwise, never more than MAX_ROWS) starting `offset` rows in, and
 * never more than MAX_BYTES of payload. The byte cap is a context budget — it
 * bounds how much of the caller's window one answer consumes — and it usually
 * binds first on wide rows. Every answer says whether there is more
 * (`has_more`) and where it started (`offset`), so a caller can page or narrow
 * and never has to guess whether it saw everything (ADR-0028).
 *
 * 16KB, not the 4KB this started at: that number was sized to what a chat turn
 * could hold, and this is a context layer for assistants generally. 16KB
 * returns ~99% of notes whole.
 */
import { search } from "./search.js";
import { peerFor } from "./surface.js";

/**
 * The page. DEFAULT_ROWS is what a caller gets when it says nothing about
 * size — enough to answer most questions and small enough not to crowd the
 * context that asked. MAX_ROWS is a sanity bound on `limit`, not a policy:
 * past it the byte cap is what binds anyway, and a page of five hundred is a
 * caller that should have narrowed the question or paged with `offset`.
 *
 * The BYTE cap does not move for anything. It bounds how much of the caller's
 * context one answer eats, which is true whatever the rows are and whoever may
 * read them.
 */
export const DEFAULT_ROWS = 20;
export const MAX_ROWS = 500;
export const MAX_BYTES = 16384;

/**
 * How many rows this call may return: what the caller asked for, or the
 * default page, lowered by whatever the tool caps itself at and by MAX_ROWS.
 *
 * `limit` can raise the page above the default as well as lower it — the
 * parameter exists so a caller can size the answer to the question. A tool's
 * `own` ceiling is for tools that spread one answer over several piles
 * (`ratings` shows a few per medium) and detects THEIR overflow.
 */
export function pageSize(ctx, own) {
  const asked = Number.isInteger(ctx?.limit) && ctx.limit > 0 ? ctx.limit : DEFAULT_ROWS;
  return Math.max(1, Math.min(own ?? MAX_ROWS, asked, MAX_ROWS));
}

/** Rows to skip before the first returned: the caller's `offset`, or none. */
export const skip = (ctx) =>
  Number.isInteger(ctx?.offset) && ctx.offset > 0 ? ctx.offset : 0;

/**
 * Ask a query for one row PAST the page.
 *
 * Without it a tool cannot tell "that is all of them" from "there are four
 * hundred more": every SQL tool here bound its LIMIT to the page and then handed
 * the result to `cap`, so `cap` never saw a row it had to drop and the count it
 * reported was the count it was given. `truncated to 20 of 20` is not a fact
 * about the corpus, and an assistant reading it concluded the shelf was twenty
 * books long.
 *
 * One extra row, thrown away after it has been counted. `cap` recognises it by
 * arithmetic — more rows arrived than fit — so nothing has to be flagged.
 *
 * Every `LIMIT ?` on this surface is bound to this and followed by `OFFSET ?`
 * bound to `skip(ctx)`, and every ORDER BY ends on a unique key — the two
 * facts that make a page neither repeat nor drop a row between calls.
 */
export const probe = (ctx, own) => pageSize(ctx, own) + 1;

/**
 * The live URL of a published post, as a spreadable fragment.
 *
 * The permalink is a pure function of the slug, so the link costs no round
 * trip — but the shape of it is an instance fact, not an engine one, and an
 * instance with no blog must produce no url key at all rather than a broken one.
 */
export const postUrl = (env, slug) =>
  env.BLOG_URL_TEMPLATE ? { url: env.BLOG_URL_TEMPLATE.replace("{slug}", slug) } : {};

/** Trim to the page and the byte budget, and say so, so a client never mistakes truncation for exhaustion. */
export function cap(rows, ctx, own) {
  // `own` is a tool's private ceiling — ratings shows five per medium — so it
  // detects ITS overflow rather than the global one.
  const limit = pageSize(ctx, own);
  const offset = skip(ctx);
  const out = [];
  let bytes = 0;
  let byteBound = false;

  for (const r of rows.slice(0, limit)) {
    const size = JSON.stringify(r).length;
    if (bytes + size > MAX_BYTES) { byteBound = true; break; }
    out.push(r);
    bytes += size;
  }
  // More arrived than fit. Whether that is the probe row or the twenty-first of
  // four hundred, the fact a caller needs is the same: this is not all of it.
  const has_more = rows.length > out.length;
  const next = offset + out.length;

  return {
    rows: out,
    // Structured, not only prose. The old note carried both numbers inside a
    // sentence, which meant a model had to parse English to learn whether it had
    // seen everything — and parse it correctly, every time, to avoid answering a
    // question about four hundred books from twenty.
    returned_count: out.length,
    offset,
    has_more,
    // A single row too large to emit is a different fact from a list too long,
    // and the generic advice is wrong for it: there is nothing to page. Say
    // which happened, so the audit log distinguishes a big answer from a bug.
    ...(has_more
      ? {
          note:
            out.length === 0 && rows.length === 1
              ? `the one row matched exceeded the ${MAX_BYTES}-byte cap and was withheld — the tool that built it did not budget for its own envelope`
              : byteBound
                ? `${out.length} rows fit the ${MAX_BYTES}-byte budget and more matched — pass offset:${next} for the next page, or ask a narrower question`
                : `showing ${out.length} from offset ${offset}; more matched — pass offset:${next} for the next page, or ask a narrower question`,
        }
      : {}),
  };
}

/**
 * The envelope of an answer with nothing in it.
 *
 * Every empty path used to hand back `{ rows: [], note }` and nothing else, so
 * a client keyed on `has_more` read `undefined` on half the surface and a page
 * walker could not tell "the list ended" from "this tool never says". The
 * three fields `cap()` stamps are stamped here too, whatever else the tool
 * wants to add beside them.
 */
export const empty = (ctx, extra = {}) => ({
  rows: [], returned_count: 0, offset: skip(ctx), has_more: false, ...extra,
});

/**
 * The envelope of a single-row answer that was never a page: one note, one
 * draft, one thread, one document. `offset` is 0 because nothing was skipped,
 * and `has_more` is false because there is no list to be more of — stamped
 * anyway, so a client reads the same keys off every answer.
 */
export const single = (row, extra = {}) => ({
  rows: [row], returned_count: 1, offset: 0, has_more: false, ...extra,
});

/** Instance facts published beside the tool list (ADR-0014), or nothing. */
export const instanceOf = (ctx) => ctx?.surface?.instance ?? {};

/**
 * One vocabulary for "which medium", accepted everywhere a tool takes one.
 *
 * Three tools spelt the same medium three ways — `medium(name:'film')`,
 * `ratings(medium:'films')`, `verdicts(kind:'films')` — and a caller that
 * learned one spelling got an empty answer from the next tool, which read as
 * "no films". Each tool keeps its own canonical key (the one its SQL is written
 * against); this map is how a caller's word finds it. Singular, plural and the
 * common synonyms all resolve; an unknown word resolves to nothing, and the tool
 * says which words it knows.
 */
const MEDIA = {
  film: ["film", "films", "movie", "movies", "cinema"],
  book: ["book", "books", "reading"],
  tv: ["tv", "television", "show", "shows", "series"],
  anime: ["anime"],
  music: ["music", "scrobbles", "listening", "records"],
  beer: ["beer", "beers"],
  restaurant: ["restaurant", "restaurants", "dining", "places", "meals"],
};

const conceptOf = (word) => {
  const w = typeof word === "string" ? word.trim().toLowerCase() : "";
  if (!w) return null;
  for (const [concept, spellings] of Object.entries(MEDIA)) {
    if (spellings.includes(w)) return concept;
  }
  return null;
};

/**
 * The key of `table` that names the medium `asked` refers to, or undefined.
 * `table` is any object keyed by a tool's own medium spellings.
 */
export function mediumKey(asked, table) {
  if (asked === undefined || asked === null || asked === "") return undefined;
  const keys = Object.keys(table);
  const w = String(asked).trim().toLowerCase();
  if (keys.includes(w)) return w;
  const concept = conceptOf(w);
  if (!concept) return undefined;
  return keys.find((k) => conceptOf(k) === concept);
}

/**
 * Which axis a list came back on — the caller's choice, out of what the tool holds.
 *
 * Every ORDER BY on this surface is over a measured fact and none of them rank
 * by preference (ADR-0013 §1). That invariant says nothing about WHICH measured
 * fact, and a tool that hardcodes one of the two it holds answers half its
 * questions: `ratings` sorted by rating and could not say what was watched
 * lately, `reviews` sorted by recency with no way to reach the best-argued ones,
 * `places` sorted by rating and did not return the visit date at all. In every
 * case the other axis was already sitting in the row.
 *
 * One vocabulary across the surface, so a caller learns it once:
 *
 *   recent   newest first — the default wherever the record carries a date
 *   oldest   the same axis reversed, for what has been sitting
 *   rated    the owner's own rating, highest first
 *   played   how often they actually reached for it
 *
 * A tool advertises only the axes its rows can answer, and the FIRST key of the
 * map is its default. The chosen name rides back on the answer, because a
 * page means something different on each axis: a page of films by rating is
 * the best of the whole shelf, a page by recency is the latest, and `has_more`
 * cannot tell those apart. An assistant should not have to infer it
 * from the shape of the rows.
 *
 * The SQL never comes from the caller. `asked` only ever picks a key out of a
 * map written here, and an unrecognised one falls back to the default and says
 * so rather than failing the call — a misspelled sort is not worth an error, but
 * it is worth not lying about.
 */
export function ordering(asked, axes) {
  const names = Object.keys(axes);
  const want = typeof asked === "string" ? asked.trim().toLowerCase() : "";
  const chosen = names.includes(want) ? want : names[0];
  return {
    order: chosen,
    sql: axes[chosen],
    ...(want && want !== chosen
      ? { note: `no order called '${want}' — sorted by ${chosen}; this tool offers ${names.join(", ")}` }
      : {}),
  };
}

/** Stamp the axis onto an answer, folding any complaint into the note already there. */
export const ordered = (out, by) => ({
  ...out,
  order: by.order,
  ...(by.note ? { note: [out.note, by.note].filter(Boolean).join(" · ") } : {}),
});


/**
 * Fill a list field against what the row will ACTUALLY serialise to.
 *
 * Measuring raw text against a fixed allowance does not work: JSON quotes every
 * line, `summary` runs to 890 chars in the store, and the envelope varies per
 * thread. `thread` reserved a flat 1,200 and assembled a 16,665-byte row against
 * a 16,384 cap — whereupon cap(, ctx) dropped the only row it had and the caller got
 * an empty answer advising them to narrow a question that was already one
 * thread. Budget against the real envelope and the row always fits.
 */
const TURN_NOTE_SAMPLE = "999 of 999 of the owner's turns shown, from turn 999 — pass from_turn:999 for the rest";

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
/**
 * The release pool is taste-blind by construction, and saying so is not a
 * disclaimer. It is a crawl of a handful of scene tags chosen by the instance,
 * so a record outside those scenes was never looked at — its absence is a fact
 * about the crawl and says nothing about whether the owner would like it. Every
 * answer carries this for the same reason WATCH_GAP is carried: a coverage gap
 * the caller cannot see from the rows is one they will read as a judgement.
 *
 * How MANY scenes is deliberately not stated. Which ones are crawled is an
 * instance fact (ADR-0014) and the engine does not get to know it; the answer
 * carries the scenes it actually matched, which is the part a caller can use.
 * What the crawl structurally cannot reach is an instance fact too, so the
 * instance may append a sentence of its own through `[surface] release_pool`
 * in exo.toml; the engine's sentence says only what is true of any crawl.
 */
const POOL_GAP =
  "this pool is a crawl of the scene sources this instance follows, not a survey of everything released \u2014 a record absent from it was not judged and not rejected, it was never looked at. Ask `criticism` for what is being written about outside those sources";

const poolGap = (ctx) => {
  const own = instanceOf(ctx).release_pool;
  return typeof own === "string" && own.trim() ? `${POOL_GAP}. ${own.trim()}` : POOL_GAP;
};

const WATCH_GAP =
  "watching is not covered: the Letterboxd watchlist was never exported, so absence of films here is a gap in the data, not an empty queue";

/**
 * The other half of the television record, and why only one half can be
 * measured.
 *
 * Trakt records episodes watched and nothing about how many there were, so
 * "did they finish it" is not a hard question over t0_tv, it is an unanswerable
 * one. MAL carries a total per entry, which is what makes a fraction possible —
 * and MAL only covers anime. Every answer that reports on progress says so,
 * because a reader who sees the anime measured and no drama measured will read
 * the silence as "they finish everything else".
 */
const COUNT_GAP =
  "only the anime shelf carries episode totals \u2014 the rest of the television record is Trakt, which counts what was watched and never how much there was to watch, so no other show here can be called finished or unfinished";

/**
 * What a beer check-in IS, beyond its name.
 *
 * The Untappd export carries all of this and the loader has kept all of it since
 * the zone was written; the surface returned the name and the score and nothing
 * else, so the top of the scale read as a list of product names. A reader can
 * guess New Zealand hops off "TDH All NZ Everything" — and a guess off a product
 * name is not a finding, which is exactly the shape of answer this omission
 * produced. The style is the field that makes the question answerable at all.
 *
 * `abv` is CAST because the column is TEXT off a CSV, like every other number in
 * this store; the rest are names and stay text.
 */
/**
 * How many of an unfiltered page one source may take. `events` spreads by
 * feed, `releases` by scene and `criticism` by outlet, and the cut runs before
 * the page, so every one of them states it in `scope` rather than letting an
 * offset walk pretend it reached the pool.
 */
const FEED_SHARE = 4;
const SCENE_SHARE = 3;

/** The media `consumption` and `ratings` answer for, keyed as their SQL spells them. */
const CONSUMPTION_MEDIA = { music: 1, books: 1, films: 1, tv: 1, beer: 1 };
const RATINGS_MEDIA = { films: 1, books: 1, beer: 1, restaurants: 1, anime: 1 };

const BEER_META = [
  ["brewery", "brewery_name"],
  ["style", "beer_type"],
  ["abv", "CAST(nullif(beer_abv,'') AS REAL)"],
  ["venue", "venue_name"],
  ["venue_city", "venue_city"],
  ["serving", "serving_type"],
];

/** `, expr AS key` for each pair, or nothing — so a tool with no metadata is unchanged. */
const selectMeta = (meta) =>
  meta ? meta.map(([k, expr]) => `, ${expr} AS ${k}`).join("") : "";

/**
 * Drop the empty ones rather than returning "".
 *
 * The RSS feed carries a title, a comment, a timestamp and a link — the export
 * columns are export-only, so every check-in newer than the last CSV export has
 * a blank style and a blank brewery. An empty string in a `style` key reads as
 * "this beer has no style"; an absent key reads as "this row does not say",
 * which is the true one.
 */
function withMeta(row, meta) {
  if (!meta) return row;
  const out = { ...row };
  for (const [k] of meta) {
    if (out[k] === null || out[k] === undefined || out[k] === "") delete out[k];
  }
  return out;
}

/**
 * The dining problem, on the other scale.
 *
 * `taste_summary(kind:'dining')` exists because a median of 8.1 makes an 8 look
 * like praise. Beer averages 3.78 of 5 and had no equivalent, so a 4.0 read as
 * mild approval when it may sit near the top of the distribution — and
 * `taste_profile`'s `outing.breweryMinRating` is literally 4.0, a threshold
 * nobody could interpret without the distribution behind it.
 */
const BEER_CALIBRATION_POINTER =
  "the 0-5 beer scale is not read off its face \u2014 call taste_summary(kind:'beer') for the median and the deciles before calling any of these numbers high";

const BEER_META_GAP =
  "brewery, style, abv, venue and serving come from the CSV export only \u2014 a check-in pulled from the RSS feed since the last export carries the name and the score alone, and those keys are absent rather than empty";

/**
 * The beer scale, calibrated from the beer log.
 *
 * `taste_summary(kind:'dining')` is a document taste-engine writes and this
 * surface mirrors. There is no beer equivalent and nothing upstream is going to
 * write one — taste-engine has never seen the check-in log — so this is
 * computed, from the distribution it is a statement about.
 *
 * The whole histogram comes back in one query and everything else is arithmetic
 * here. Untappd rates in quarter steps, so a 0-5 scale has twenty populated
 * buckets at most: cheap to fetch whole, and exact, where a SQL percentile would
 * have to be approximated or leaned on a window function D1's SQLite may or may
 * not have compiled in.
 *
 * Returns null when nothing is rated, which is a served-but-empty zone rather
 * than an error.
 */
async function beerCalibration(env) {
  const score = "nullif(CAST(nullif(rating_score,'') AS REAL), 0)";
  const hist = await q(
    env,
    `SELECT ${score} AS v, count(*) AS n
     FROM t0_beer WHERE ${score} IS NOT NULL
     GROUP BY v ORDER BY v`
  );
  if (!hist.length) return null;

  const [totals] = await q(
    env,
    `SELECT count(*) AS checkins,
            sum(CASE WHEN ${score} IS NULL THEN 1 ELSE 0 END) AS unrated,
            max(substr(created,1,10)) AS last
     FROM t0_beer`
  );

  const rated = hist.reduce((a, r) => a + r.n, 0);
  const mean = hist.reduce((a, r) => a + r.v * r.n, 0) / rated;

  // The value at or below which p of the ratings fall. Walks the cumulative
  // counts, so it is the real order statistic and not an interpolation between
  // buckets a quarter-step apart.
  const at = (p) => {
    const target = p * rated;
    let seen = 0;
    for (const r of hist) {
      seen += r.n;
      if (seen >= target) return r.v;
    }
    return hist[hist.length - 1].v;
  };
  const median = at(0.5);
  const deciles = Array.from({ length: 9 }, (_, i) => at((i + 1) / 10));

  // Where a 4.0 actually sits. This is the number the reader came for: the
  // threshold in taste_profile is 4.0, and nobody can read it without knowing
  // what share of the record clears it.
  const shareAtOrAbove = (v) =>
    hist.filter((r) => r.v >= v).reduce((a, r) => a + r.n, 0) / rated;
  const pct = (x) => `${(x * 100).toFixed(0)}%`;
  const num = (x) => (Math.round(x * 100) / 100).toFixed(2).replace(/\.?0+$/, "");
  // Thousands separators without Intl. The runtime has it, but a document whose
  // digits depend on which ICU build the isolate was compiled with is a document
  // that reads differently in test and in production for no reason.
  const n = (x) => String(x ?? 0).replace(/\B(?=(\d{3})+(?!\d))/g, ",");

  const text = [
    "# The beer scale, calibrated",
    "",
    `Computed from the check-in log itself rather than written by hand: ${n(rated)} rated`,
    `check-ins out of ${n(totals?.checkins ?? rated)}, last logged ${totals?.last ?? "unknown"}.`,
    "",
    "## Read this before reading a number",
    "",
    `The scale runs 0-5. The mean is ${num(mean)} — the number every other tool on this`,
    `surface hands back — and the median is ${num(median)}. ${pct(shareAtOrAbove(4))} of rated check-ins`,
    `are at 4.0 or above, ${pct(shareAtOrAbove(4.5))} are at 4.5 or above, and the ninetieth`,
    `percentile is ${num(at(0.9))}.`,
    "",
    `A 4.0 therefore sits above ${pct(1 - shareAtOrAbove(4))} of what they drank and level with or`,
    "below the rest. That is the fact to carry into a sentence about it, and it is",
    "not the fact that 4 out of 5 suggests on its face.",
    "",
    "`taste_profile` carries `outing.breweryMinRating`, a threshold stated on this",
    "same scale. Read it against the table below before treating it as a bar for a",
    "favourite: on this distribution a threshold at 4.0 admits",
    `${pct(shareAtOrAbove(4))} of what they drink.`,
    "",
    "## Deciles",
    "",
    "| decile | at or below |",
    "|---|---|",
    ...deciles.map((v, i) => `| ${(i + 1) * 10}% | ${num(v)} |`),
    "",
    "## Every step that occurs",
    "",
    "Untappd rates in quarter steps, so these are the buckets the record actually",
    "uses rather than a binning chosen here.",
    "",
    "| rating | check-ins | share |",
    "|---|---|---|",
    ...hist.map((r) => `| ${num(r.v)} | ${n(r.n)} | ${pct(r.n / rated)} |`),
    "",
    "## What this scale does not carry",
    "",
    ...(totals?.unrated
      ? [`${n(totals.unrated)} check-ins carry no rating at all. They are absent from every`,
         "number above, and they are not zeroes.",
         ""]
      : []),
    "Rating happens at the point of drinking, so this is a judgement on nearly",
    "everything consumed rather than on a selected few — which is the opposite of",
    "the music record, where the play count is the only judgement there is.",
  ].join("\n");

  return { kind: "beer", text };
}

const q = (env, sql, ...binds) =>
  env.DB.prepare(sql).bind(...binds).all().then((r) => r.results ?? []);

/**
 * The full text of ONE row, found by meaning rather than by id.
 *
 * `read_note` and `read_post` were the same forty lines twice — semantic lookup,
 * miss guard, fetch, clip against a hand-rolled byte budget, explain the clip.
 * They were also the only two places in this file that budgeted bytes by hand
 * instead of going through cap(, ctx), which is exactly the shape a second copy
 * takes when the first one is copied.
 *
 * Two ways in. By `topic`, the default: a vector search picks the best match
 * and its ref is the row's key. By `id`: the same key column, handed back by a
 * title listing from the same tool, so a caller that has already seen the map
 * can pick a document off it without a second guess at wording. Either way
 * the shape is one row, budgeted against its own envelope.
 */
async function readOne(env, { topic, id, kind, table, key, select, missing, onClip }, ctx) {
  let ref = id;
  let score = null;
  if (ref === undefined || ref === null || ref === "") {
    if (!topic) return empty(ctx, { note: `pass a topic to search for a ${missing}, or the id a listing returned` });
    const [hit] = await search(env, topic, { k: 1, kind });
    if (!hit) return empty(ctx, { note: `no ${missing} matched` });
    ref = hit.ref;
    score = hit.score;
  }

  const rows = await q(env, `SELECT ${select} FROM ${table} WHERE ${key} = ? LIMIT 1`, String(ref));
  if (!rows.length) {
    return empty(ctx, {
      note: score === null
        ? `no ${missing} with id '${ref}' — ids come from this tool's own listing, and a held one is absent rather than hidden`
        : `matched a ${missing} that is not published`,
    });
  }

  const row = rows[0];
  const full = row.body ?? "";
  // Budget against the real envelope, not a guessed constant: the metadata
  // around the body differs per table, and a flat allowance is how a single
  // row ends up over the cap and gets dropped entirely.
  const envelope = JSON.stringify({ ...row, id: ref, body: "", match_score: 0.999, note: "" }).length;
  const budget = MAX_BYTES - envelope - 200;
  const clipped = full.length > budget;

  return single({
    id: ref,
    ...row,
    ...(score === null ? {} : { match_score: score }),
    body: clipped ? full.slice(0, budget) : full,
  }, clipped ? { note: onClip(row, budget, full.length) } : {});
}

/**
 * Three orthogonal facets on every tool (ADR-0015). They are not decoration:
 * `class` is what would make an answer a lie, `domain` is what the rows are
 * about, `kind` is what one row is. The README table is generated from them and
 * test/run.mjs fails on an unknown value, so a new tool cannot ship unclassified.
 *
 * `class` — the act that produced the row, which is what a caller gets wrong.
 *   revealed    they did it; no intent was stated (plays, ratings, commits)
 *   authored    their words, deliberately written
 *   intent      declared want, not consummated — and nobody prunes these
 *   possession  they paid for it and kept it
 *   dialogue    co-authored; half of it is another model and is not them
 *   derived     a machine concluded it (T2) — never quote it as theirs
 *   world       not about them at all; a candidate pool
 *   lens        owns no rows; joins across the classes above
 *
 * `domain` — what the rows are about. "*" means the caller chooses: the tool
 * takes the domain as an argument rather than being fixed to one. class:lens
 * implies domain:"*"; the reverse does not hold.
 *
 * `kind` — the shape of one row, which predicts the failure mode. A judgement
 * is unreadable without its scale, a pointer goes stale silently, a vector is
 * never quotable, and an event is the only kind that cannot lie about recency.
 */
export const CLASSES = ["revealed", "authored", "intent", "possession", "dialogue", "derived", "world", "lens"];
export const DOMAINS = ["culture", "table", "mind", "workshop", "commitments", "world", "*"];
export const KINDS = ["event", "judgement", "text", "entity", "pointer", "vector", "mixed"];

export const TOOLS = {
  whats_relevant: {
    class: "derived", domain: "mind", kind: "vector",
    reads: ["t2_atom", "t1_notes", "t1_post"],
    description:
      "What has the owner written that bears on a topic? One semantic search over three corpora at once: quotable spans lifted from their notes, the notes themselves, and their published posts. Each hit says which it is — a post hit carries its live URL, a note hit carries the `id` that `notes_on` reads by. The selection is a machine's; the text inside a span is theirs. Use this first for their thinking on something; use `notes_on` or `posts` to read a whole document once you have its key. Does not search conversations, drafts or repo documents — those match on words, not meaning, through `recent_topics`, `drafts` and `project_docs`.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "A topic, question, or phrase. Matched by meaning, so a sentence works better than a keyword." },
        kind: { type: "string", description: "note | post | atom — search one corpus instead of all three." },
      },
      required: ["topic"],
    },
    async run(env, { topic, kind }, ctx) {
      const KINDS = ["note", "post", "atom"];
      const want = typeof kind === "string" ? kind.trim().toLowerCase() : "";
      const only = KINDS.includes(want) ? want : null;
      const hits = await search(env, topic, { k: probe(ctx) + skip(ctx), kind: only });
      // A post hit is the one kind that has somewhere to send a reader. Its ref is
      // the slug, and the permalink is a pure function of it, so the link costs
      // no round trip — and an answer with a link beats one that paraphrases
      // an essay back at its own author. A note hit carries its id, so
      // `notes_on` can be asked for it directly.
      const out = cap(
        hits.slice(skip(ctx)).map((h) => ({
          kind: h.kind,
          text: h.label,
          match_score: h.score,
          ...(h.kind === "post" ? postUrl(env, h.ref) : {}),
          ...(h.kind === "note" ? { id: h.ref } : {}),
        })),
        ctx
      );
      const complaint = want && !only
        ? `no corpus called '${want}' — searched all three; this tool knows ${KINDS.join(", ")}`
        : null;
      return complaint ? { ...out, note: [out.note, complaint].filter(Boolean).join(" · ") } : out;
    },
  },

  notes_on: {
    class: "authored", domain: "mind", kind: "text",
    reads: ["t1_notes"],
    description:
      "The owner's private notes on a topic, found by meaning. Returns titles by default — a map of what exists, each with the `id` to read it by — and one whole note with `full:true` (the best match) or `id` (that exact note, no search). With no `topic`, a dated listing instead: every note, newest first, narrowed by `folder`, `since` and `until`, which is how to ask what they wrote last week or what a folder holds. Notes are draft thinking, not finished positions; `posts` holds what was argued into shape and published, and `whats_relevant` searches both at once. A note also live in a peer system is said so on the row.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "What the note is about — a phrase, a question, a claim. Matched by meaning, so a sentence works better than a keyword. Omit to list notes by date instead." },
        id: { type: "string", description: "The `id` a listing returned. Fetches that one note whole, no search — the follow-up to a listing." },
        full: { type: "boolean", description: "Return one whole note instead of a list of titles." },
        folder: { type: "string", description: "Only notes filed in this folder, by name." },
        since: { type: "string", description: "ISO date; only notes created on or after it." },
        until: { type: "string", description: "ISO date; only notes created on or before it." },
        order: { type: "string", description: "recent (default, newest first) | oldest. Applies to a dated listing; a topic search is ranked by match instead." },
      },
    },
    async run(env, { topic, id, full, folder, since, until, order }, ctx) {
      if (full || id) {
        const one = await readOne(env, {
          topic, id, kind: "note", table: "t1_notes", key: "origin_ref", missing: "note",
          // `source` and `uuid` are the join key back to wherever this note came
          // out of (ADR-0020). A caller holding that system's own MCP server can
          // match this row to the live page instead of treating the two as two
          // notes — which is the whole difference between a second source and a
          // second copy.
          select: "title, folder, created, source, uuid, body",
          onClip: (_r, kept, total) =>
            `body clipped to ${kept} of ${total} chars — one answer holds this much of the note, and the rest is not reachable by asking again`,
        }, ctx);
        const peer = peerFor(ctx?.surface, one.rows?.[0]?.source);
        if (!peer) return one;
        // Stated once, on the row it applies to, and it is a fact rather than an
        // instruction: this note is also live over there. Whether that means
        // re-read it, cite it, or ignore the duplication is the agent's call —
        // it knows what is already in its context and we do not (ADR-0013 §2).
        const where = one.rows[0].uuid ? `\`${one.rows[0].uuid}\`` : "an untracked page";
        const said = `also live in ${peer.server} as ${where} — this copy is the filed `
          + `and indexed one, that copy is the current one`;
        return { ...one, peer, note: [one.note, said].filter(Boolean).join("; ") };
      }

      const narrowed = folder || since || until;
      const folderLike = folder ? folder.trim().toLowerCase() : null;
      const inWindow = (r) =>
        (!folderLike || (r.folder ?? "").toLowerCase() === folderLike)
        && (!since || (r.created ?? "").slice(0, 10) >= since)
        && (!until || (r.created ?? "").slice(0, 10) <= until);

      if (!topic) {
        // No topic: a listing by date, off the folder index rather than the
        // vector index. "What did I write last week" and "what is in the
        // reading folder" are questions about the record, not about meaning,
        // and a search cannot answer them without a subject to search for.
        const by = ordering(order, {
          recent: "created DESC",
          oldest: "created ASC",
        });
        const rows = await q(
          env,
          `SELECT origin_ref AS id, title, folder, substr(created,1,10) AS created
           FROM t1_notes
           WHERE (? IS NULL OR lower(COALESCE(folder,'')) = ?)
             AND (? IS NULL OR substr(created,1,10) >= ?)
             AND (? IS NULL OR substr(created,1,10) <= ?)
           ORDER BY ${by.sql}, id
           LIMIT ? OFFSET ?`,
          folderLike, folderLike, since ?? null, since ?? null, until ?? null, until ?? null,
          probe(ctx), skip(ctx)
        );
        const [held] = await q(
          env,
          `SELECT count(*) AS n, count(DISTINCT folder) AS folders FROM t1_notes
           WHERE (? IS NULL OR lower(COALESCE(folder,'')) = ?)
             AND (? IS NULL OR substr(created,1,10) >= ?)
             AND (? IS NULL OR substr(created,1,10) <= ?)`,
          folderLike, folderLike, since ?? null, since ?? null, until ?? null, until ?? null
        );
        const scope = `${held?.n ?? 0} notes${narrowed ? " matched" : ""}` +
          (folderLike ? "" : ` across ${held?.folders ?? 0} folders`);
        if (!rows.length) {
          return empty(ctx, {
            scope,
            note: narrowed
              ? "no notes in that folder or window — pass a topic to search by meaning instead, or widen the dates"
              : "no notes are published here",
          });
        }
        return ordered({ ...cap(rows, ctx), scope }, by);
      }

      // The index is ranked, so a page of it is the next `limit` hits past the
      // ones already shown: ask for that many and drop the head. A folder or
      // a window on top of a topic filters the hits afterwards, over-fetching
      // the way `posts` does for `kind`, because the vector index carries no
      // folder and no date.
      const want = probe(ctx) + skip(ctx);
      const hits = await search(env, topic, { k: narrowed ? Math.max(60, want * 3) : want, kind: "note" });
      let rows = hits.map((h) => ({ id: h.ref, title: h.label, match_score: h.score }));
      if (narrowed && rows.length) {
        const refs = rows.map((r) => r.id);
        const meta = new Map();
        for (const r of await q(
          env,
          `SELECT origin_ref, folder, created FROM t1_notes
           WHERE origin_ref IN (${refs.map(() => "?").join(",")})`,
          ...refs
        )) meta.set(r.origin_ref, r);
        rows = rows.filter((r) => meta.has(r.id) && inWindow(meta.get(r.id)))
          .map((r) => ({ ...r, folder: meta.get(r.id).folder, created: (meta.get(r.id).created ?? "").slice(0, 10) }));
      }
      if (!rows.length) return empty(ctx, { note: narrowed ? "nothing matched inside that folder or window" : "no note matched" });
      return cap(rows.slice(skip(ctx)), ctx);
    },
  },

  open_threads: {
    class: "intent", domain: "mind", kind: "pointer",
    reads: ["t1_open_thread"],
    description:
      "Questions the owner has asked themselves in writing and not closed — the best single source of what they are currently chewing on. Newest first. Each row is an unfinished question, not a position: do not read one as something they concluded. Takes no filter; the whole list pages. For conversations rather than written questions, see `recent_topics`.",
    schema: { type: "object", properties: {} },
    async run(env, _args, ctx) {
      const rows = await q(
        env,
        `SELECT question, state FROM t1_open_thread
         WHERE state IS NULL OR lower(state) NOT IN ('closed','done','dismissed')
         ORDER BY created DESC, id LIMIT ? OFFSET ?`,
        probe(ctx), skip(ctx)
      );
      return cap(rows.map((r) => ({ question: r.question })), ctx);
    },
  },

  verdicts: {
    class: "authored", domain: "culture", kind: "text",
    reads: ["t1_verdicts"],
    description:
      "The owner's written opinions on books, films, television and music — their own words, with the reasoning, and the rating they gave. Highest-rated first, and that is the only axis: this zone carries no date, so it cannot say what they judged lately. For dated film writing see `reviews`; for the numbers without prose see `ratings`; for what the press thinks see `criticism`.",
    schema: {
      type: "object",
      properties: { kind: { type: "string", description: "books | films | tv | music — one medium, or all. Singular spellings are accepted." } },
    },
    async run(env, { kind }, ctx) {
      // `ORDER BY created DESC` here was a no-op that read like a promise.
      // media-verdicts.json carries no date and the loader never invents one, so
      // every row's `created` is NULL, the sort fell straight through to `id` —
      // a content hash — and the opinions came back in hash order under a
      // heading that said newest first. The rating is the fact these rows do
      // hold, and it arrives as TEXT off a JSON file, so it is cast rather than
      // compared as a string ('9.5' sorts above '10').
      //
      // No `order` on the answer: one axis is not a choice (ADR-0022), and a
      // stamped axis reads as one the caller could have picked otherwise.
      const KINDS = { books: 1, films: 1, tv: 1, music: 1 };
      const picked = mediumKey(kind, KINDS);
      if (kind && !picked) {
        return empty(ctx, { note: `no medium called '${kind}' here — this tool knows ${Object.keys(KINDS).join(", ")}` });
      }
      const sort = "CAST(nullif(rating,'') AS REAL) DESC, subject";
      const rows = picked
        ? await q(env, `SELECT subject, kind, rating, note FROM t1_verdicts WHERE kind = ?
            ORDER BY ${sort}, id LIMIT ? OFFSET ?`, picked, probe(ctx), skip(ctx))
        : await q(env, `SELECT subject, kind, rating, note FROM t1_verdicts
            ORDER BY ${sort}, id LIMIT ? OFFSET ?`, probe(ctx), skip(ctx));
      const [held] = await q(
        env,
        `SELECT count(*) AS n FROM t1_verdicts WHERE (? IS NULL OR kind = ?)`,
        picked ?? null, picked ?? null
      );
      return { ...cap(rows, ctx), scope: `${held?.n ?? 0} verdicts${picked ? ` on ${picked}` : ""}, undated` };
    },
  },

  taste: {
    class: "revealed", domain: "culture", kind: "event",
    reads: ["t0_music", "t2_affinity"],
    // The scrobble stream is what this tool is ABOUT; `mentions` is colour on it
    // (ADR-0023 §2). t2_affinity joins t0_music to t1_notes, so a tool that
    // always returned that one integer was always as private as the notes. The
    // grade follows the subject, and the colour costs its own call, so the
    // exposure stamp on a plain scrobble answer is the scrobbles' own.
    readsFor: ({ with_mentions }) =>
      with_mentions ? ["t0_music", "t2_affinity"] : ["t0_music"],
    description:
      "What the owner actually listens to, straight off the scrobble stream — revealed preference, as distinct from what they say they like (`taste_profile`). One row per artist with plays and first/last play dates. The tail is long and quiet: every artist ever scrobbled is counted and most were played a handful of times, so a page is the head of a list whose size `scope` states — never read a short page as a narrow taste. `artist` asks whether one act is in the record at all; `since`/`until` make \"lately\" a different question from \"ever\"; `order:'recent'` is what they have been reaching for, which is not the all-time list. `with_mentions` adds how many of their notes name each artist, which reads the notes and grades the answer accordingly.",
    schema: {
      type: "object",
      properties: {
        artist: { type: "string", description: "One act by name, matched anywhere in the string. The way to ask 'do they listen to this at all' rather than 'is this at the top of the list'." },
        since: { type: "string", description: "ISO date; only plays on or after it." },
        until: { type: "string", description: "ISO date; only plays on or before it." },
        order: { type: "string", description: "played (default, most plays first) | recent (last reached for) | oldest (fell out of rotation longest ago)" },
        with_mentions: { type: "boolean", description: "Also count how many of the owner's notes name each artist. Reads their notes, so the answer is graded as the notes are." },
      },
    },
    async run(env, { artist, since, until, order, with_mentions }, ctx) {
      const like = artist ? `%${artist.toLowerCase()}%` : null;
      // Three facts per artist, so all three axes come off one scan. `plays`
      // alone could not say what fell OUT of rotation, and that is most of what
      // "lately" means against a decade of scrobbles.
      const by = ordering(order, {
        played: "plays DESC, last_played DESC",
        recent: "last_played DESC, plays DESC",
        oldest: "last_played ASC, plays DESC",
      });
      const where = `WHERE (? IS NULL OR lower(artist) LIKE ?)
           AND (? IS NULL OR substr(created,1,10) >= ?)
           AND (? IS NULL OR substr(created,1,10) <= ?)`;
      const binds = [like, like, since ?? null, since ?? null, until ?? null, until ?? null];

      const rows = await q(
        env,
        `SELECT artist, count(*) AS plays,
                substr(min(created),1,10) AS first_played,
                substr(max(created),1,10) AS last_played
         FROM t0_music
         ${where}
         GROUP BY artist
         ORDER BY ${by.sql}, artist
         LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );

      // What the rows were drawn FROM, counted rather than implied (ADR-0023
      // §1). "Twenty artists" is a fact about the cap; "twenty of 2,314" is the
      // fact a reader needs before saying anything about the shape of a taste.
      const [scope] = await q(
        env,
        `SELECT count(*) AS plays, count(DISTINCT artist) AS artists,
                substr(min(created),1,10) AS first, substr(max(created),1,10) AS last
         FROM t0_music ${where}`,
        ...binds
      );

      if (!scope?.plays) {
        return empty(ctx, {
          scope: "0 plays matched",
          note: artist
            ? `nothing scrobbled by anything matching '${artist}' \u2014 this record is one service's stream, so absence means it was never scrobbled, not that they have never heard it`
            : "nothing scrobbled in that window \u2014 check last_logged from consumption, the exports lag",
        });
      }

      const out = rows.map((r) => ({ ...r }));
      const extra = [];
      if (with_mentions) {
        // A LEFT JOIN would be one query, but the affinity zone has a plays
        // floor, so a missing row there is ambiguous in a way a coalesced zero
        // would hide. Attach what exists and say what a gap means.
        const seen = await q(env, `SELECT artist, mentions FROM t2_affinity WHERE mentions > 0`);
        const m = new Map(seen.map((r) => [r.artist, r.mentions]));
        for (const r of out) if (m.has(r.artist)) r.mentions = m.get(r.artist);
        extra.push("mentions comes from the affinity zone, which holds only artists over a hundred lifetime plays \u2014 a row with no count is a quiet artist, not an unmentioned one");
      }

      const capped = cap(out, ctx);
      const note = [capped.note, ...extra].filter(Boolean).join(" \u00b7 ");
      const span = scope.first === scope.last ? scope.first : `${scope.first} \u2192 ${scope.last}`;
      const plural = (n, one) => `${n} ${n === 1 ? one : one + "s"}`;
      return ordered({
        ...capped,
        scope: `${plural(scope.plays, "play")} by ${plural(scope.artists, "artist")}, ${span}`,
        ...(note ? { note } : {}),
      }, by);
    },
  },

  agenda: {
    class: "intent", domain: "commitments", kind: "pointer",
    reads: ["t1_item"],
    description:
      "What the owner has committed to and where it stands \u2014 the item spine their scheduler reads from. Four families with different lifecycles: tasks are todo/done, habits carry a streak and a cadence, slots are open or filled, constraints are standing rules. Ask with no arguments for what is live right now.",
    schema: {
      type: "object",
      properties: {
        family: { type: "string", description: "task | habit | slot | constraint" },
        include_done: { type: "boolean", description: "Include finished tasks (default false)." },
      },
    },
    async run(env, { family, include_done }, ctx) {
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
         ORDER BY family, due IS NULL, due, created DESC, id
         LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
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
        ...cap(out, ctx),
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
    reads: ["t1_item", "t1_item_event"],
    description:
      "What actually happened to the owner's commitments \u2014 the append-only log behind the item spine. Status changes with dates, so you can see when a habit lapsed or how long a task sat before it moved, rather than only where it stands now.",
    schema: {
      type: "object",
      properties: { title: { type: "string", description: "Match against the item's title." } },
    },
    async run(env, { title }, ctx) {
      const like = title ? `%${title.toLowerCase()}%` : null;
      const rows = await q(
        env,
        `SELECT e.event, e.to_status, e.date, e.ts, e.ref_kind, e.from_,
                COALESCE(i.title, e.title) AS item
         FROM t1_item_event e
         LEFT JOIN t1_item i ON i.origin_ref = e.item_id OR i.id = e.item_id
         WHERE (? IS NULL OR lower(COALESCE(i.title, e.title)) LIKE ?)
         ORDER BY COALESCE(e.date, e.ts) DESC, e.id
         LIMIT ? OFFSET ?`,
        like, like, probe(ctx), skip(ctx)
      );
      return cap(rows.map((r) => ({
        item: r.item, event: r.event,
        // from_ -> to_status is the fold: "todo -> done" says more than "done",
        // and it is the only place the previous state survives at all.
        ...(r.from_ ? { from: r.from_ } : {}),
        ...(r.to_status ? { to: r.to_status } : {}),
        when_: r.date || (r.ts ?? "").slice(0, 10),
        ...(r.ref_kind ? { ref_kind: r.ref_kind } : {}),
      })), ctx);
    },
  },

  recipes: {
    class: "authored", domain: "table", kind: "text",
    reads: ["t1_recipe"],
    description:
      "Recipes the owner wrote up and published, with the post each came from. Small and real — what they cooked and posted, not a database. Newest publication first; undated seed templates sort last. `full:true` returns ingredients and steps for the best match. For what they eat out, see `places`.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, cuisine, or tags." },
        full: { type: "boolean", description: "Return ingredients and steps for one recipe." },
      },
    },
    async run(env, { topic, full }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const match = "(? IS NULL OR lower(title) LIKE ? OR lower(cuisine) LIKE ? OR lower(tags) LIKE ?)";
      // Alphabetical was the one ORDER BY on this surface that ranked by nothing
      // measured, and the zone holds two shapes of row: recipes written up and
      // posted, which carry a publication date, and the synthetic seed templates
      // of ADR-0003, which carry neither a date nor a title. Sorting by title put
      // every untitled template first \u2014 so `full:true` with no topic answered
      // "give me a recipe" with an empty shell. Published first, newest first,
      // seeds after: a date is a fact, and the alphabet is not.
      const RECIPE_ORDER = "created IS NULL, created DESC, title";
      const binds = [like, like, like, like];

      if (full) {
        const rows = await q(
          env,
          `SELECT title, cuisine, time_min, effort, yield_servings, source_url,
                  ingredients, steps
           FROM t1_recipe WHERE ${match} ORDER BY ${RECIPE_ORDER} LIMIT 1`,
          ...binds
        );
        if (!rows.length) return empty(ctx, { note: "no recipe matched" });
        const r = rows[0];
        // Both columns are JSON on the wire \u2014 D1 has no list type \u2014 so parse
        // here rather than handing a reader a string that only looks like data.
        const parse = (v) => { try { return JSON.parse(v || "[]"); } catch { return []; } };
        return single({ ...r, ingredients: parse(r.ingredients), steps: parse(r.steps) });
      }

      const rows = await q(
        env,
        `SELECT title, cuisine, time_min, effort, yield_servings, source_url,
                substr(created,1,10) AS published
         FROM t1_recipe WHERE ${match} ORDER BY ${RECIPE_ORDER}, id LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );
      // One axis, so no `order` on the answer (ADR-0022): newest publication
      // first is what the description says, and stamping it would read as a
      // choice the caller could have made otherwise.
      const [held] = await q(env, `SELECT count(*) AS n FROM t1_recipe WHERE ${match}`, ...binds);
      return { ...cap(rows, ctx), scope: `${held?.n ?? 0} recipes${like ? " matched" : ""}` };
    },
  },
  drafts: {
    class: "authored", domain: "mind", kind: "text",
    reads: ["t1_draft"],
    description:
      "Longform pieces the owner is in the middle of writing — between a private note and a published post. With no arguments, everything open, most recently touched first, each with its word count and days since touched. `stale_days` finds what has gone cold, which is the question a writer cannot answer about themselves; `order:'oldest'` is the same question as a sort. `full:true` returns the whole text of the best match; `id` is a draft's `slug` as a listing returned it. A draft absent here was never started or is already published (`posts`); it is not evidence the idea does not exist in `notes_on`.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title or text." },
        id: { type: "string", description: "A draft's `slug`, exactly as the listing returned it. Returns that draft whole." },
        stale_days: { type: "number", description: "Only drafts untouched for at least this many days." },
        full: { type: "boolean", description: "Return the entire text of the best match." },
        order: { type: "string", description: "recent (default, last touched first) | oldest (longest untouched first)" },
      },
    },
    async run(env, { topic, id, stale_days, full, order }, ctx) {
      const by = ordering(order, {
        recent: "modified DESC",
        oldest: "modified ASC",
      });
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const match = "(? IS NULL OR lower(title) LIKE ? OR lower(body) LIKE ?)";
      const stale = "(? IS NULL OR julianday('now') - julianday(modified) >= ?)";
      const binds = [like, like, like, stale_days ?? null, stale_days ?? 0];

      if (full || id) {
        // By slug when one was handed back by a listing; by best match otherwise.
        // `modified DESC, id` so two drafts touched in the same second still
        // resolve to the same one on every call.
        const rows = id
          ? await q(env, `SELECT title, slug, description, started, modified, words, state, body
                          FROM t1_draft WHERE slug = ? LIMIT 1`, String(id))
          : await q(env, `SELECT title, slug, description, started, modified, words, state, body
                          FROM t1_draft WHERE ${match} AND ${stale} ORDER BY modified DESC, id LIMIT 1`,
                    ...binds);
        if (!rows.length) return empty(ctx, { note: id ? `no draft with slug '${id}'` : "no draft matched" });
        const d = rows[0];
        const envelope = JSON.stringify({ ...d, body: "", note: "" }).length;
        const budget = MAX_BYTES - envelope - 200;
        const clipped = (d.body ?? "").length > budget;
        return single(
          { ...d, body: clipped ? d.body.slice(0, budget) : d.body },
          clipped ? { note: `body clipped to ${budget} of ${d.body.length} chars — one answer holds this much of the draft` } : {}
        );
      }

      const rows = await q(
        env,
        `SELECT title, slug, description, started, modified, words, state,
                CAST(julianday('now') - julianday(modified) AS INTEGER) AS days_since_touched
         FROM t1_draft WHERE ${match} AND ${stale} ORDER BY ${by.sql}, id LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );
      if (!rows.length) {
        return empty(ctx, {
          // An empty answer here has two very different meanings and the caller
          // cannot tell them apart, so say which one it is.
          note: topic || stale_days
            ? "nothing matched — there may still be other drafts open"
            : "no drafts are in progress right now",
        });
      }
      return ordered(cap(rows, ctx), by);
    },
  },
  medium: {
    class: "lens", domain: "*", kind: "mixed",
    // One row, or a directory of at most a handful: nothing here is a list a
    // caller could page, so tools/list does not advertise limit/offset on it.
    pages: false,
    reads: [
      "t0_anime",
      "t0_beer",
      "t0_book",
      "t0_film",
      "t0_music",
      "t0_tv",
      "t1_collection",
      "t1_film_review",
      "t1_verdicts",
      "t1_visits",
    ],
    description:
      "Everything about one medium in a single call: how much of it the owner has consumed and how current that record is, how they rate it on its own scale with the top few, what they own of it, and how much they have written about it. With no `name`, a directory of the media this record holds with a count for each. One row, not a list — `ratings`, `collection`, `verdicts` and `consumption` page the same facts separately. A medium not in the directory is not held here, which says nothing about whether the owner consumes it.",
    schema: {
      type: "object",
      properties: {
        name: { type: "string", description: "film | book | tv | anime | music | beer | restaurant. Singular; plurals are accepted. Omit for the directory." },
      },
    },
    async run(env, { name: asked }, ctx) {
      // Shelf-aware, like `consumption`: t0_book is the whole Goodreads library
      // and a shelved book has been read exactly as much as a queued one, so an
      // unfiltered count reports the library as the reading.
      const M = {
        film:  { table: "t0_film",   unit: "films",      verdicts: "films",
                 rate: { col: "rating",       scale: "0-5",  label: "title",      url: "origin_ref" },
                 owns: "dvd", reviews: true },
        book:  { table: "t0_book",   unit: "books read", verdicts: "books", where: "WHERE shelf = 'read'",
                 rate: { col: "my_rating",    scale: "0-5",  label: "title",
                         where: "AND shelf IN ('read','partly-read')" } },
        tv:    { table: "t0_tv",     unit: "shows",      verdicts: "tv" },
        // Shelf-aware for the same reason `book` is: a MAL list is a queue as
        // well as a history, and plan-to-watch entries have been watched
        // exactly as much as a to-read book has been read.
        anime: { table: "t0_anime",  unit: "titles watched or watching",
                 where: "WHERE status <> 'plan_to_watch'",
                 rate: { col: "score", scale: "1-10", label: "title", url: "url" } },
        music: { table: "t0_music",  unit: "scrobbles",  verdicts: "music", owns: "vinyl" },
        beer:  { table: "t0_beer",   unit: "check-ins",
                 rate: { col: "rating_score", scale: "0-5",  label: "beer_name", extra: BEER_META } },
        restaurant: { table: "t1_visits", unit: "visits",
                 rate: { col: "rating",       scale: "0-10", label: "restaurant" }, calibrated: true },
      };

      // How many rows a medium has, or null if this instance does not hold it.
      //
      // Not defensive garnish: the map above is the ENGINE's list of media and
      // an instance holds whichever zones it has ingested. A medium nobody has
      // a zone for used to take the whole tool down inside D1 — including the
      // directory call, which is the one an assistant makes first — and "no
      // such table" is the only error a bare count over a fixed name can raise.
      // A medium this record does not hold is simply not in the directory.
      const countOf = async (d) => {
        const rows = await q(env, `SELECT count(*) AS n, max(substr(created,1,10)) AS last_logged
                                   FROM ${d.table} ${d.where ?? ""}`).catch(() => null);
        return rows?.[0] ?? null;
      };

      const directory = async (missing) => {
        const out = [];
        for (const [k, d] of Object.entries(M)) {
          const base = await countOf(d);
          if (base) out.push({ medium: k, unit: d.unit, total: base.n ?? 0 });
        }
        return {
          ...cap(out.slice(skip(ctx)), ctx),
          note: missing
            ? `no medium called '${missing}' here — ask for one of these`
            : "ask for one by name to get ratings, what is owned, and what was written about it",
        };
      };

      // No name: the directory, so an assistant can pick one rather than guess a
      // label. Cheaper than returning six full profiles and blowing the cap.
      const name = mediumKey(asked, M);
      if (!name) return directory(asked);

      const d = M[name];
      const base = await countOf(d);
      // Named, known to the engine, absent from this record. Same answer as an
      // unknown name, because from the caller's side it is the same fact.
      if (!base) return directory(name);
      const rec = { medium: name, consumed: base.n ?? 0, unit: d.unit,
                    last_logged: base.last_logged ?? null };

      if (name === "tv") {
        const [e] = await q(env, `SELECT sum(episodes_watched) AS n FROM t0_tv`);
        rec.episodes_watched = e?.n ?? null;
      }
      if (name === "anime") {
        // The shape of a MAL list is its status column — how much is finished,
        // how much was abandoned, how much is queued. Returning a bare total
        // for a zone that holds all four states says almost nothing.
        const st = await q(
          env,
          `SELECT status AS status, count(*) AS n FROM t0_anime
           WHERE status <> '' GROUP BY status ORDER BY n DESC, status`
        );
        // by_status counts the WHOLE list, queue included; `consumed` above
        // does not. Both are wanted and they are different numbers, so the
        // queue is also stated on its own, the way `book` states its to-read
        // shelf — read off the breakdown rather than counted a second time.
        if (st.length) rec.by_status = Object.fromEntries(st.map((r) => [r.status, r.n]));
        rec.plan_to_watch = rec.by_status?.plan_to_watch ?? 0;
      }
      if (name === "beer") {
        // Two counts and the gap between them, because the gap is the fact. The
        // owner drinks 1,947 distinct beers across 1,964 check-ins: novelty is
        // the point, and a return is rare enough to say more than a single 5.
        // It was derivable by subtracting one number from another, and so it was
        // never derived.
        const [b] = await q(
          env,
          `SELECT count(*) AS distinct_beers, sum(CASE WHEN n > 1 THEN 1 ELSE 0 END) AS repeated
           FROM (SELECT count(*) AS n FROM t0_beer GROUP BY lower(beer_name))`
        );
        rec.distinct_beers = b?.distinct_beers ?? null;
        rec.repeat_count = b?.repeated ?? 0;
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
        const top = (await q(
          env,
          `SELECT ${r.label} AS label, ${cast} AS rating, ${urlCol}
                  substr(created,1,10) AS when_${selectMeta(r.extra)}
           FROM ${d.table} ${filter}
           ORDER BY ${cast} DESC, created DESC, id LIMIT 3`
        )).map((row) => withMeta(row, r.extra));
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
      if (name === "beer") {
        notes.push(BEER_CALIBRATION_POINTER);
        notes.push(
          `${rec.repeat_count} of those beers were drunk more than once — `
          + `facets(medium:'beer', by:'beer', min_n:2) is that list, and `
          + `facets(medium:'beer', by:'family') is what the taste is made of`
        );
      }
      if (name === "film") notes.push(WATCH_GAP);
      if (name === "anime") notes.push(COUNT_GAP);
      if (!d.rate) {
        // The one place this sentence was wrong. Trakt carries no ratings, so
        // "they do not rate television" was true of the SOURCE and false of
        // them — the anime half of the same medium is rated item by item, and an
        // assistant told otherwise stopped looking.
        const rated = name === "tv" ? await countOf(M.anime) : null;
        notes.push(
          rated?.n
            ? "Trakt carries no ratings, so the television counts are the whole of what it says \u2014 but the anime half of the same medium is scored title by title: ask medium(name:'anime') or ratings(medium:'anime'), and `watching` for how far through each show they got"
            : `the owner does not rate ${name} item by item — the counts are the record`);
      }

      // Through cap() like every other answer, so the one row carries the same
      // envelope a page does and a client keyed on has_more reads false, not
      // undefined.
      const out = cap([rec], ctx);
      return { ...out, ...(notes.length ? { note: [out.note, ...notes].filter(Boolean).join(" · ") } : {}) };
    },
  },

  consumption: {
    class: "revealed", domain: "*", kind: "event",
    reads: ["t0_beer", "t0_book", "t0_film", "t0_music", "t0_tv"],
    // Named medium, single zone. Unnamed, it aggregates across all of them and
    // is as private as the least public — which is the correct answer for a row
    // that mixes them.
    readsFor: ({ medium }) => ({
      music: ["t0_music"], books: ["t0_book"], films: ["t0_film"],
      beer: ["t0_beer"], tv: ["t0_tv"],
    })[mediumKey(medium, CONSUMPTION_MEDIA)] ?? ["t0_beer", "t0_book", "t0_film", "t0_music", "t0_tv"],
    description:
      "How much of each medium the owner has consumed and how current each record is — totals and `last_logged` per medium, never titles. Read `last_logged` before trusting any answer about \"lately\": the exports lag by months and a stale source reads as someone who stopped. `medium` returns the same numbers for one medium with its ratings and ownership beside them.",
    schema: {
      type: "object",
      properties: { medium: { type: "string", description: "music | books | films | tv | beer. Plural; singular spellings are accepted. Omit for all." } },
    },
    async run(env, { medium }, ctx) {
      // t0_book is the whole Goodreads library, not a reading log: most of
      // its rows are shelved rather than read, and `created` falls back to
      // date_added, so a shelved book looks consumed. One total over the
      // library reports the queue as the reading.
      const T = {
        music: { table: "t0_music", where: "" },
        books: { table: "t0_book",  where: "WHERE shelf = 'read'" },
        films: { table: "t0_film",  where: "" },
        tv:    { table: "t0_tv",    where: "" },
        beer:  { table: "t0_beer",  where: "" },
      };
      const key = mediumKey(medium, T);
      if (medium && !key) {
        return empty(ctx, { note: `no medium called '${medium}' here — this tool knows ${Object.keys(T).join(", ")}` });
      }
      const picked = key ? { [key]: T[key] } : T;
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
          // Shows and episodes are different questions: the show count is what
          // is watched, the episode count is how much.
          const [e] = await q(env, `SELECT sum(episodes_watched) AS n FROM t0_tv`);
          rec.total_label = "shows";
          rec.episodes_watched = e?.n ?? null;
        }
        if (name === "beer") {
          // The same two numbers `medium` returns, from the same query, because
          // two tools that report the beer shelf and disagree about it is worse
          // than either of them being absent.
          const [b] = await q(
            env,
            `SELECT count(*) AS distinct_beers, sum(CASE WHEN c > 1 THEN 1 ELSE 0 END) AS repeated
             FROM (SELECT count(*) AS c FROM t0_beer GROUP BY lower(beer_name))`
          );
          rec.distinct_beers = b?.distinct_beers ?? null;
          rec.repeat_count = b?.repeated ?? 0;
          rec.total_label = "check-ins";
        }
        if (name === "books") {
          const [b] = await q(env, `SELECT count(*) AS n FROM t0_book WHERE shelf = 'to-read'`);
          rec.total_label = "finished";
          rec.to_read_backlog = b?.n ?? 0;
        }
        out.push(rec);
      }
      return cap(out.slice(skip(ctx)), ctx);
    },
  },

  posts: {
    class: "authored", domain: "mind", kind: "text",
    reads: ["t1_post"],
    description:
      "The owner's published blog — essays, articles, lists, project write-ups — found by meaning, as opposed to the private notes behind them. Each hit carries its live URL, its `slug`, and a one-line description. `full:true` returns the whole text of the best match; `id` is a slug from a listing. These are public and finished; the same idea may exist earlier and rougher in `notes_on` or `drafts`. `kind` narrows to one post type after the search.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "A topic, question, or phrase. The default way in." },
        id: { type: "string", description: "A post's `slug`, as a listing returned it. Fetches that one post whole, no search." },
        kind: {
          type: "string",
          description: "article | essay | notes | list | project | stub | recipe",
        },
        full: { type: "boolean", description: "Return one whole post instead of a list." },
      },
    },
    async run(env, { topic, id, kind, full }, ctx) {
      if (full || id) {
        return readOne(env, {
          topic, id, kind: "post", table: "t1_post", key: "slug", missing: "post",
          select: "title, url, published, kind, words, body",
          onClip: (r, kept, total) =>
            `body clipped to ${kept} of ${total} chars — the whole post is public at ${r.url}`,
        }, ctx);
      }
      // Over-fetch, then filter: the vector index carries no post kind, so a
      // kind filter applied after the search would return a few rows out of a
      // page and look like the blog is empty on that kind.
      if (!topic) return empty(ctx, { note: "pass a topic to search by, or an id (slug) from an earlier listing" });
      const want = probe(ctx) + skip(ctx);
      const hits = await search(env, topic, { k: kind ? Math.max(60, want * 3) : want, kind: "post" });
      if (!hits.length) return empty(ctx, { note: "nothing on the blog matched" });

      const slugs = hits.map((h) => h.ref).filter(Boolean);
      if (!slugs.length) return empty(ctx, { note: "nothing on the blog matched" });

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
          slug: m.slug, title: m.title, url: m.url, published: m.published,
          kind: m.kind, words: m.words, description: m.description, match_score: h.score,
        });
      }
      if (!rows.length) return empty(ctx, { note: `matched posts, but none of kind '${kind}'` });
      return cap(rows.slice(skip(ctx)), ctx);
    },
  },

  events: {
    class: "world", domain: "world", kind: "entity",
    reads: ["t0_event"],
    description:
      "Upcoming events the owner could go to — a pool merged from the local feeds this instance follows, soonest first, from today unless `from` says otherwise. Recurring programmes are collapsed to their next date with an occurrence count, and no single feed may fill the answer, so a page is a spread across sources rather than a slice of one — `scope` states the spread. `topic` matches title, venue or description; `free` keeps only free ones; `from`/`to` bound the window and a past window returns the past. This is a candidate pool, not a recommendation: pair with `taste_profile` for the venues and organisers they say they rate.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, venue or description." },
        from: { type: "string", description: "ISO date. Defaults to today; never volunteers the past." },
        to: { type: "string", description: "ISO date, inclusive." },
        free: { type: "boolean", description: "Only free events." },
      },
    },
    async run(env, { topic, free, from, to }, ctx) {
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
         FROM ranked WHERE rn <= ?
         -- One row per (title, feed) after the collapse, so those two make the
         -- order total; url last so the lint that reads this clause can see it.
         ORDER BY start, title, feed, url
         LIMIT ? OFFSET ?`,
        from ?? null, to ?? null, to ?? null,
        like, like, like, like, free ? 1 : null, FEED_SHARE, probe(ctx), skip(ctx)
      );
      // The population, and the shape of the cut (ADR-0023). The round-robin
      // runs BEFORE the page, so `offset` walks the spread of FEED_SHARE per
      // feed and never the whole pool — stated here rather than lifted, because
      // lifting it past page one would make page two a slice of a different
      // list from page one, which is the one thing a page walk must not be.
      const [held] = await q(
        env,
        `SELECT count(*) AS programmes, count(DISTINCT feed) AS feeds
         FROM (SELECT title, feed FROM t0_event
               WHERE substr(start, 1, 10) >= COALESCE(?, date('now'))
                 AND (? IS NULL OR substr(start, 1, 10) <= ?)
                 AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(venue,'')) LIKE ?
                                OR lower(COALESCE(description,'')) LIKE ?)
                 AND (? IS NULL OR free = 1)
               GROUP BY title, feed)`,
        from ?? null, to ?? null, to ?? null, like, like, like, like, free ? 1 : null
      );
      return {
        ...cap(rows, ctx),
        scope: `${held?.programmes ?? 0} upcoming programmes across ${held?.feeds ?? 0} feeds` +
          `${from ? ` from ${from}` : ""}${to ? ` to ${to}` : ""}; a page holds at most ${FEED_SHARE} per feed, ` +
          "so offset walks that spread rather than the pool — narrow with topic or from/to to reach the rest",
      };
    },
  },

  releases: {
    class: "world", domain: "culture", kind: "entity",
    reads: ["t0_release", "t0_music", "t1_collection"],
    description:
      "Records that came out recently in the scenes the owner follows, with anything they have already scrobbled or own removed — the count removed is stated. The pool is crawled by scene, not by similarity to what they play, so it can surface an artist with no listeners yet; a record absent from it was never looked at, not rejected. Each row carries the measured facts — lifetime plays of that artist, whether anything by them is owned, how many scenes surfaced it — and no judgement. `order:'unfamiliar'` is the discovery axis; `'familiar'` is new work by artists already in rotation; `'spread'` is what the most scenes agree on. `include_heard:true` keeps the removed ones. An unfiltered page is spread across scenes, which `scope` states.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against artist, title or label." },
        scene: { type: "string", description: "One scene tag, as the crawl labels them — every row carries its `scenes`, so an unfiltered call shows the vocabulary." },
        since: { type: "string", description: "ISO date. Only records released on or after it." },
        until: { type: "string", description: "ISO date. Only records released on or before it." },
        include_heard: {
          type: "boolean",
          description: "Keep records they have already scrobbled or own. Off by default; the count removed is reported either way.",
        },
        order: {
          type: "string",
          description: "recent (default) | unfamiliar (fewest plays of that artist first) | familiar (most) | spread (surfaced under the most scenes)",
        },
      },
    },
    async run(env, { topic, scene, since, until, include_heard, order }, ctx) {
      const by = ordering(order, {
        // Every axis ends on `url`, which is the only unique column in the
        // pool: two labels can announce the same artist and title on the same
        // day, and without a final key those tie and SQLite is free to return
        // them in either order. import.sh DROPs and re-INSERTs this table on
        // every publish, so "either order" changes under a caller who asked
        // the same question twice. criticism has ended its axes this way from
        // the start; this one had not.
        recent: "release_date DESC, artist, title, url",
        unfamiliar: "plays ASC, release_date DESC, artist, title, url",
        familiar: "plays DESC, release_date DESC, artist, title, url",
        spread: "scene_count DESC, listings DESC, release_date DESC, artist, title, url",
      });
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const inScene = scene ? `%${scene.toLowerCase()}%` : null;
      const share = topic || scene ? 1000 : SCENE_SHARE;

      // Aggregated once and joined, never correlated per row: t0_music is 40k
      // scrobbles and the pool is thousands of releases, so a subquery per
      // candidate is a table scan per candidate.
      const POOL = `
        WITH plays AS (
          SELECT lower(artist) AS a, count(*) AS n FROM t0_music GROUP BY 1
        ), heard AS (
          SELECT DISTINCT lower(artist) AS a, lower(COALESCE(album,'')) AS t FROM t0_music
        ), owns AS (
          SELECT DISTINCT lower(COALESCE(creator,'')) AS a, lower(COALESCE(title,'')) AS t
          FROM t1_collection
        ), pool AS (
          SELECT r.artist, r.title, r.release_date, r.url, r.label, r.scenes,
                 r.scene_count, r.listings, r.mb_status,
                 COALESCE(p.n, 0) AS plays,
                 (h.t IS NOT NULL) AS scrobbled, (o.t IS NOT NULL) AS owned
          FROM t0_release r
          LEFT JOIN plays p ON p.a = lower(r.artist)
          LEFT JOIN heard h ON h.a = lower(r.artist) AND h.t = lower(r.title)
          LEFT JOIN owns  o ON o.a = lower(r.artist) AND o.t = lower(r.title)
          WHERE (? IS NULL OR r.release_date >= ?)
            AND (? IS NULL OR r.release_date <= ?)
            AND (? IS NULL OR lower(r.scenes) LIKE ?)
            AND (? IS NULL OR lower(r.artist) LIKE ? OR lower(r.title) LIKE ?
                           OR lower(COALESCE(r.label,'')) LIKE ?)
        )`;
      const binds = [since ?? null, since ?? null, until ?? null, until ?? null, inScene, inScene, like, like, like, like];

      const rows = await q(
        env,
        `${POOL}, kept AS (
           SELECT * FROM pool WHERE ? = 1 OR (scrobbled = 0 AND owned = 0)
         ), ranked AS (
           -- Round-robin on the FIRST scene that surfaced a record, so one busy
           -- tag cannot take the whole answer. Lifted for a filtered query, where
           -- capping per scene would hide matches for the sole reason that they
           -- share a tag.
           SELECT *, row_number() OVER (
             PARTITION BY substr(scenes, 1, instr(scenes || ',', ',') - 1)
             ORDER BY ${by.sql}) AS rn
           FROM kept
         )
         SELECT artist, title, release_date, url, label, scenes, mb_status,
                plays,
                CASE WHEN owned = 1 THEN 1 END AS owned,
                CASE WHEN scene_count > 1 THEN scene_count END AS scenes_hit
         FROM ranked WHERE rn <= ?
         ORDER BY ${by.sql}
         LIMIT ? OFFSET ?`,
        ...binds, include_heard ? 1 : 0, share, probe(ctx), skip(ctx)
      );

      // The exclusion is the point of this tool, so it is stated rather than
      // performed silently: an answer that dropped forty records they have
      // already worn out is a different answer from one that had forty fewer
      // candidates. Counted with the population it was cut from (ADR-0023).
      const [held] = await q(
        env,
        `${POOL} SELECT count(*) AS matched,
                        sum(CASE WHEN scrobbled = 1 OR owned = 1 THEN 1 ELSE 0 END) AS heard
                 FROM pool`,
        ...binds);
      const matched = held?.matched ?? 0;
      const removed = held?.heard ?? 0;
      const kept = include_heard ? matched : matched - removed;

      const notes = [poolGap(ctx)];
      if (!include_heard && removed) notes.push(`${removed} already heard or owned, removed`);
      const scope = `${kept} candidates${topic || scene || since || until ? " matched" : ""}` +
        (share === SCENE_SHARE
          ? `; an unfiltered page holds at most ${SCENE_SHARE} per scene, so offset walks that spread rather than the pool — narrow with topic or scene to reach the rest`
          : "");
      if (!rows.length) {
        const [{ n }] = await q(env, `SELECT count(*) AS n FROM t0_release`);
        return empty(ctx, {
          order: by.order,
          scope,
          note: [n ? `nothing in ${n} candidates matched` : "the release pool is empty — no crawl has landed yet",
                 ...notes].join(" \u00b7 "),
        });
      }
      const out = ordered({ ...cap(rows, ctx), scope }, by);
      return { ...out, note: [out.note, ...notes].filter(Boolean).join(" \u00b7 ") };
    },
  },

  // The empty cell ADR-0015's grid left in `culture`: every culture tool reads
  // something the owner did or owns, and nothing said what the world was saying
  // about any of it. `events` was the only `class: "world"` tool on the surface.
  criticism: {
    class: "world", domain: "culture", kind: "text",
    reads: ["t0_criticism"],
    description:
      "What the music press is publishing — headline, byline, date, the outlet's own blurb and the link — from the outlets this record follows. Somebody else's writing, never the owner's: attribute it to the outlet. Use it for what is being said about a scene or an artist now, which nothing in the owner's own record can answer. No outlet may fill an unfiltered page, which `scope` states; a `topic` or `outlet` search returns every match. Pair with `taste` for whether they already play what is being written about.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against the headline, blurb, tags or byline." },
        outlet: { type: "string", description: "One outlet, by name or slug (e.g. 'no-bells')." },
        since: { type: "string", description: "ISO date. Only pieces published on or after it." },
        until: { type: "string", description: "ISO date. Only pieces published on or before it." },
        order: { type: "string", description: "recent (default) | oldest." },
      },
    },
    async run(env, { topic, outlet, since, until, order }, ctx) {
      const by = ordering(order, {
        recent: "published DESC, url",
        oldest: "published ASC, url",
      });
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const who = outlet ? outlet.toLowerCase() : null;

      // Round-robin by outlet, so a daily reviewer cannot fill an answer a
      // weekly essayist should be in \u2014 the same rule `events` applies to
      // its nine scrapers, and the reason this zone stores an outlet at all.
      //
      // Only when nothing was ASKED for, though. Dominance is a problem for a
      // browse; on a topic search the caller wants the matches, and capping
      // them per outlet would hide four of five pieces about one record because
      // they ran in the same place.
      const share = topic || who ? 1000 : SCENE_SHARE;

      const rows = await q(
        env,
        `WITH f AS (
           SELECT outlet, outlet_slug, byline, title, url, published, summary, chars, tags
           FROM t0_criticism
           WHERE (? IS NULL OR published >= ?)
             AND (? IS NULL OR published <= ?)
             AND (? IS NULL OR lower(outlet_slug) = ? OR lower(outlet) = ?)
             AND (? IS NULL OR lower(title) LIKE ?
                            OR lower(COALESCE(summary,'')) LIKE ?
                            OR lower(COALESCE(tags,'')) LIKE ?
                            OR lower(COALESCE(byline,'')) LIKE ?)
         ), ranked AS (
           SELECT *, row_number() OVER (PARTITION BY outlet_slug ORDER BY ${by.sql}) AS rn
           FROM f
         )
         SELECT outlet, byline, title, published, url, tags,
                -- Clipped again here, and deliberately. The stored blurb runs to
                -- 700 chars, which across a default page is the whole byte
                -- budget and returns eleven links instead of a page. A dek is
                -- enough to decide whether to follow one, and the count says
                -- there is more behind it.
                substr(COALESCE(summary,''), 1, 320) AS summary,
                CASE WHEN chars > 320 THEN chars END AS blurb_chars
         FROM ranked WHERE rn <= ?
         ORDER BY ${by.sql}
         LIMIT ? OFFSET ?`,
        since ?? null, since ?? null,
        until ?? null, until ?? null,
        who, who, who,
        like, like, like, like, like,
        share, probe(ctx), skip(ctx)
      );

      const [held] = await q(
        env,
        `SELECT count(*) AS n, count(DISTINCT outlet_slug) AS outlets FROM t0_criticism
         WHERE (? IS NULL OR published >= ?)
           AND (? IS NULL OR published <= ?)
           AND (? IS NULL OR lower(outlet_slug) = ? OR lower(outlet) = ?)
           AND (? IS NULL OR lower(title) LIKE ?
                          OR lower(COALESCE(summary,'')) LIKE ?
                          OR lower(COALESCE(tags,'')) LIKE ?
                          OR lower(COALESCE(byline,'')) LIKE ?)`,
        since ?? null, since ?? null, until ?? null, until ?? null,
        who, who, who, like, like, like, like, like
      );
      const scope = `${held?.n ?? 0} pieces${topic || who || since || until ? " matched" : ""} across ${held?.outlets ?? 0} outlets` +
        (share === SCENE_SHARE
          ? `; an unfiltered page holds at most ${SCENE_SHARE} per outlet, so offset walks that spread rather than the pool — narrow with topic or outlet to reach the rest`
          : "");

      // An empty answer is explained, and the two reasons are not the same
      // answer. "Nothing matched" is a fact about the question; "the press has
      // not been read yet" is a fact about the pipeline, and a caller told the
      // first when the second is true will report that nobody has written about
      // an artist all year.
      if (!rows.length) {
        const [{ n }] = await q(env, `SELECT count(*) AS n FROM t0_criticism`);
        return empty(ctx, {
          scope,
          note: n
            ? `nothing in ${n} collected pieces matched`
            : "no press has been collected yet — this is an empty zone, not an empty week",
          order: by.order,
        });
      }
      return ordered({ ...cap(rows, ctx), scope }, by);
    },
  },

  taste_profile: {
    class: "authored", domain: "world", kind: "judgement",
    reads: ["t1_taste"],
    description:
      "What the owner says they like and will not have — stated preferences and standing constraints, as key/value rows grouped by kind: venues and organisers they rate, things they seek out, things they avoid, allergies and rules. Distinct from `taste`, which is what they measurably play; where the two disagree is usually the interesting part. Constraints are not preferences to weigh — a suggestion that breaks one is not worth making.",
    schema: {
      type: "object",
      properties: {
        kind: { type: "string", description: "One kind of preference, e.g. constraint, dining, outing, event. Omit for all." },
      },
    },
    async run(env, { kind }, ctx) {
      const want = typeof kind === "string" && kind.trim() ? kind.trim().toLowerCase() : null;
      const rows = await q(
        env,
        `SELECT kind, key, value FROM t1_taste
         WHERE (? IS NULL OR lower(kind) = ?)
         ORDER BY kind, key, id LIMIT ? OFFSET ?`,
        want, want, probe(ctx), skip(ctx)
      );
      if (!rows.length && want) {
        const kinds = await q(env, `SELECT DISTINCT kind FROM t1_taste ORDER BY kind`);
        return empty(ctx, {
          note: `no preferences of kind '${kind}' — this record holds ${kinds.map((r) => r.kind).join(", ") || "none"}`,
        });
      }
      return cap(rows, ctx);
    },
  },

  // The one tool that justifies one record rather than seven APIs: every other
  // tool reads a single table, but ADR-0001 exists so `taste ⋈ notes ⋈
  // scrobbles` can be asked as one question.
  around_the_time: {
    class: "lens", domain: "*", kind: "mixed",
    reads: ["t0_book", "t0_film", "t0_music", "t0_tv", "t1_notes"],
    description:
      "What was going on in a window of dates: what the owner wrote, played, watched and read between `from` and `to`. A period, not a topic — the lens for \"what was I thinking about in March\". Rows interleave the media so a heavy month in one cannot crowd out the others, and `scope` counts what the window actually held; `limit` and `offset` page through the whole interleave. For one medium in depth over the same window, use that medium's own tool with `since`/`until`.",
    schema: {
      type: "object",
      properties: {
        from: { type: "string", description: "ISO date, e.g. 2026-03-01" },
        to: { type: "string", description: "ISO date, e.g. 2026-03-31" },
      },
      required: ["from", "to"],
    },
    async run(env, { from, to }, ctx) {
      // Each branch fetches as far into its window as the page could reach.
      // The per-branch head used to be a hand-sized constant (8, 5, 4, 4, 4),
      // which made `limit` a no-op above the sum and `has_more` false while the
      // window held hundreds. Fetching `offset + limit` per branch, plus the
      // probe row, means the interleave is exact for every row the page can
      // show, and a walk by offset reaches the whole window. OFFSET is bound
      // to 0 on every branch: the page is over the interleave, not over any
      // one branch, so the skip happens after the merge.
      const head = Math.min(skip(ctx) + pageSize(ctx), MAX_ROWS) + 1;
      const notes = await q(
        env,
        `SELECT 'note' AS kind, title AS label, substr(created,1,10) AS when_
         FROM t1_notes WHERE substr(created,1,10) BETWEEN ? AND ?
         ORDER BY created, id LIMIT ? OFFSET ?`,
        from, to, head, 0
      );
      const music = await q(
        env,
        `SELECT 'artist' AS kind, artist AS label, count(*) AS plays
         FROM t0_music WHERE substr(created,1,10) BETWEEN ? AND ?
         GROUP BY artist ORDER BY plays DESC, artist LIMIT ? OFFSET ?`,
        from, to, head, 0
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
         ORDER BY created, id LIMIT ? OFFSET ?`,
        from, to, head, 0
      );
      const books = await q(
        env,
        `SELECT 'book' AS kind, title AS label, substr(created,1,10) AS when_,
                nullif(CAST(my_rating AS TEXT),'0') AS rating, '0-5' AS scale
         FROM t0_book WHERE substr(created,1,10) BETWEEN ? AND ?
           AND shelf = 'read'
         ORDER BY created, id LIMIT ? OFFSET ?`,
        from, to, head, 0
      );
      // The scale string is doing real work on this branch. A show is IN the
      // window because its last episode was, and the number beside it is every
      // episode ever watched of it — so `5` on a June row means "five in total,
      // the last of them in June", not "five during June". Labelled 'episodes'
      // it read as the second, and there is no column here that could mean it:
      // t0_tv is one row per show and holds no per-episode dates at all.
      const tv = await q(
        env,
        `SELECT 'tv' AS kind, title AS label, substr(created,1,10) AS when_,
                CAST(episodes_watched AS TEXT) AS rating,
                'episodes watched in total, not in this window' AS scale
         FROM t0_tv WHERE substr(created,1,10) BETWEEN ? AND ?
         ORDER BY created, id LIMIT ? OFFSET ?`,
        from, to, head, 0
      );
      // Interleaved, not concatenated. The branches are fetched separately so
      // a heavy film month cannot discard the books — but concatenating handed
      // the page the same starvation from the other end: the last branch lost
      // every row. Round-robin means the page trims the tail of each source
      // rather than the last source whole, and the order is exact for every
      // round the branches were fetched deep enough to fill.
      const branches = [notes, music, films, books, tv];
      const all = [];
      for (let i = 0; branches.some((b) => i < b.length); i++) {
        for (const b of branches) if (i < b.length) all.push(b[i]);
      }
      if (!all.length) {
        return empty(ctx, {
          scope: `${from} → ${to} held nothing`,
          note: `nothing recorded between ${from} and ${to} — check the last_logged dates from consumption, the exports lag`,
        });
      }
      // What the window actually held, beside what fit in it (ADR-0023 §1).
      // A reader handed five artists and no denominator concluded the month
      // WAS five artists, which is the one thing this tool must not let them
      // do. One statement, six subqueries, and the window bound six times
      // over — rather than `?1`/`?2` bound once, which SQLite understands and
      // every other statement in this file does not use. Consistency is worth
      // two lines here: the binds are generated, so they cannot fall out of
      // step with the placeholders by hand.
      const IN_WINDOW = "substr(created,1,10) BETWEEN ? AND ?";
      const [held] = await q(
        env,
        `SELECT (SELECT count(DISTINCT artist) FROM t0_music WHERE ${IN_WINDOW}) AS artists,
                (SELECT count(*) FROM t0_music WHERE ${IN_WINDOW}) AS plays,
                (SELECT count(*) FROM t1_notes WHERE ${IN_WINDOW}) AS notes,
                (SELECT count(*) FROM t0_film  WHERE ${IN_WINDOW}) AS films,
                (SELECT count(*) FROM t0_book  WHERE ${IN_WINDOW} AND shelf = 'read') AS books,
                (SELECT count(*) FROM t0_tv    WHERE ${IN_WINDOW}) AS tv`,
        ...Array.from({ length: 6 }, () => [from, to]).flat()
      );
      const counted = [
        held?.notes ? `${held.notes} notes` : null,
        held?.artists ? `${held.artists} artists over ${held.plays} plays` : null,
        held?.films ? `${held.films} films` : null,
        held?.books ? `${held.books} books` : null,
        held?.tv ? `${held.tv} shows last watched` : null,
      ].filter(Boolean).join(" · ");
      const total = (held?.notes ?? 0) + (held?.artists ?? 0) + (held?.films ?? 0)
        + (held?.books ?? 0) + (held?.tv ?? 0);
      // The page is over the interleaved list; `offset` walks that list, and
      // `has_more` is read off the window's own count rather than off what
      // the branches happened to fetch, so it cannot go false with rows left.
      const capped = cap(all.slice(skip(ctx)), ctx);
      const has_more = capped.has_more || skip(ctx) + capped.rows.length < total;
      const next = skip(ctx) + capped.rows.length;
      return {
        ...capped,
        has_more,
        scope: `${from} → ${to} held ${counted || "nothing"} (${total} rows); this page interleaves the media from offset ${skip(ctx)}`,
        note: capped.note
          ?? (has_more
            ? `more of the window remains — pass offset:${next} for the next page`
            : "each medium is fetched separately so a heavy month cannot starve the others — read `scope` for the window's size"),
      };
    },
  },

  ratings: {
    class: "revealed", domain: "*", kind: "judgement",
    reads: ["t0_anime", "t0_beer", "t0_book", "t0_film", "t1_visits"],
    // Restaurant visits are the owner's own record and never public; films,
    // books and beer are profiles on services that may be. One medium per call,
    // so one grade per call.
    readsFor: ({ medium }) => ({
      films: ["t0_film"], books: ["t0_book"],
      beer: ["t0_beer"], restaurants: ["t1_visits"], anime: ["t0_anime"],
    })[mediumKey(medium, RATINGS_MEDIA)] ?? ["t0_anime", "t0_beer", "t0_book", "t0_film", "t1_visits"],
    description:
      "What the owner rated and how highly, one medium at a time, from the services that recorded it. Behaviour rather than prose: the numbers behind `verdicts` and `reviews`, and far more of them. Every row carries its `scale` because the media disagree — never compare a number from one medium with a number from another, and read `taste_summary` before calling a dining or beer score high. Highest-rated first by default, so a page is the top of that medium; `order:'recent'` is what they rated lately, a different list. `topic` finds one title by name. With no `medium`, the media are interleaved so each takes a share of the page, and `scope` counts every medium. `min_rating` is on that medium's own scale.",
    schema: {
      type: "object",
      properties: {
        medium: { type: "string", description: "films | books | beer | restaurants | anime. Plural; singular spellings are accepted. Omit for a share of each." },
        topic: { type: "string", description: "Match against the title or name of the thing rated." },
        min_rating: { type: "number", description: "Only at or above this, on that medium's own scale." },
        order: { type: "string", description: "rated (default, highest first) | recent" },
      },
    },
    async run(env, { medium, topic, min_rating, order }, ctx) {
      // scale is carried per row because the mediums disagree: Letterboxd and
      // Goodreads are 0-5, the restaurant log is 0-10. Returning a bare 9.5
      // invites an assistant to read it as "off the charts" on a five-point
      // scale, or a 4.5 film as mediocre.
      const SRC = {
        films:       { table: "t0_film",   label: "title",      col: "rating",       scale: "0-5", url: "origin_ref" },
        books:       { table: "t0_book",   label: "title",      col: "my_rating",    scale: "0-5", where: "AND shelf IN ('read','partly-read')" },
        beer:        { table: "t0_beer",   label: "beer_name",  col: "rating_score", scale: "0-5", extra: BEER_META },
        restaurants: { table: "t1_visits", label: "restaurant", col: "rating",       scale: "0-10" },
        // Television's only ratings. Trakt has none, so before the MAL list
        // landed the honest answer for the whole medium was "they do not rate
        // it" — true of Trakt and false of them. MAL's 0 means unrated
        // rather than terrible, which the shared `> 0` filter already reads
        // correctly.
        anime:       { table: "t0_anime",  label: "title",      col: "score",        scale: "1-10", url: "url" },
      };
      const key = mediumKey(medium, SRC);
      if (medium && !key) {
        return empty(ctx, { note: `no medium called '${medium}' here — this tool knows ${Object.keys(SRC).join(", ")}` });
      }
      const picked = key ? { [key]: SRC[key] } : SRC;
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const floor = min_rating ?? 0;
      // One medium: a page straight off its list. Several: every medium is
      // fetched as deep as the page could reach and the lists are interleaved,
      // so `limit` is honoured as a share per medium, `offset` is applied once
      // over the interleave, and `has_more` is exact for the rows the page can
      // show — the same shape `around_the_time` pages by.
      const head = key ? probe(ctx) : Math.min(skip(ctx) + pageSize(ctx), MAX_ROWS) + 1;
      const branches = [];
      const counts = [];
      // One axis for the whole answer, but its SQL is per medium: each keeps its
      // rating in a different column. The two facts swap places rather than one
      // replacing the other, so whichever leads, ties break on the other one
      // before they fall through to `id`.
      let axis = "rated", complaint = null;
      for (const [name, d] of Object.entries(picked)) {
        if (!d) continue;
        const by = ordering(order, {
          rated:  `CAST(${d.col} AS REAL) DESC, created DESC`,
          recent: `created DESC, CAST(${d.col} AS REAL) DESC`,
        });
        axis = by.order;
        complaint = by.note ?? null;
        // origin_ref on t0_film is the Letterboxd permalink — it was published
        // all along and no tool returned it, so an assistant could name a film
        // but never point at it.
        const urlCol = d.url ? `${d.url} AS url,` : "";
        const where = `WHERE ${d.col} IS NOT NULL AND CAST(${d.col} AS REAL) > 0
             AND CAST(${d.col} AS REAL) >= ? ${d.where ?? ""}
             AND (? IS NULL OR lower(${d.label}) LIKE ?)`;
        const rows = await q(
          env,
          `SELECT ${d.label} AS label, CAST(${d.col} AS REAL) AS rating, ${urlCol}
                  substr(created,1,10) AS when_${selectMeta(d.extra)}
           FROM ${d.table}
           ${where}
           ORDER BY ${by.sql}, id
           LIMIT ? OFFSET ?`,
          floor, like, like, head, key ? skip(ctx) : 0
        );
        branches.push(rows.map((r) => ({ medium: name, scale: d.scale, ...withMeta(r, d.extra) })));
        // The population per medium (ADR-0023): a page of five films and five
        // books says nothing about which shelf is the deep one.
        const [held] = await q(env, `SELECT count(*) AS n FROM ${d.table} ${where}`, floor, like, like);
        counts.push([name, held?.n ?? 0]);
      }
      let out;
      if (key) {
        out = branches[0] ?? [];
      } else {
        out = [];
        for (let i = 0; branches.some((b) => i < b.length); i++) {
          for (const b of branches) if (i < b.length) out.push(b[i]);
        }
        out = out.slice(skip(ctx));
      }
      const capped = cap(out, ctx);
      const total = counts.reduce((a, [, n]) => a + n, 0);
      const scope = key
        ? `${total} ${key} rated${topic ? ` matching '${topic}'` : ""}${floor ? ` at or above ${floor}` : ""}`
        : `${total} rated across ${counts.length} media (${counts.map(([n, c]) => `${n} ${c}`).join(" · ")}); the page interleaves them`;
      // Only when a beer row actually survived the cap. An unfiltered call
      // interleaves every medium, and a calibration note about a medium the
      // caller cannot see in the answer is noise they have to rule out.
      const notes = [complaint];
      if (capped.rows.some((r) => r.medium === "beer")) {
        notes.push(BEER_CALIBRATION_POINTER, BEER_META_GAP);
      }
      const note = notes.filter(Boolean).join(" · ");
      return ordered({ ...capped, scope }, { order: axis, ...(note ? { note } : {}) });
    },
  },

  facets: {
    class: "revealed", domain: "*", kind: "judgement",
    reads: ["t0_beer"],
    description:
      "The owner's ratings for one medium rolled up by a property of the thing rated — for beer: style family, full style, brewery, venue, or the beer itself. One row per group with how many, how many were rated, the mean, the best and the most recent. A rollup answers \"which styles do they rate highest\" where a page of individual ratings cannot. Ordered by how often they reached for the group, because a mean over two check-ins is not a preference; `order:'rated'` puts the mean first and says so. `min_n:2` with `by:'beer'` is the list of beers they went back to. Covers beer only; other media carry no facet columns.",
    schema: {
      type: "object",
      properties: {
        medium: { type: "string", description: "beer — the one medium whose rows carry facets. Singular or plural." },
        by: { type: "string", description: "family | style | brewery | venue | beer" },
        min_n: { type: "number", description: "Only groups with at least this many check-ins. Default 1." },
        order: { type: "string", description: "played (default, most check-ins first) | rated | recent" },
      },
    },
    async run(env, { medium: asked, by, min_n, order }, ctx) {
      // Beer alone, because beer alone has facets in the store. Film carries a
      // year and books an author, and either could be added here the day
      // somebody checks what the column actually holds \u2014 the registry is the
      // extension point, and a medium listed before that check is a claim
      // nobody verified.
      const M = {
        beer: {
          table: "t0_beer",
          unit: "check-ins",
          scale: "0-5",
          // Every one of these is NULL or "" on a check-in that came from the
          // RSS feed rather than the CSV export. Grouping would give them a
          // bucket of their own named "", so they are excluded and counted
          // separately \u2014 an unknown style is a gap in the export, not a style.
          by: {
            // Untappd styles are "Family - Substyle" ("IPA - Hazy", "Stout -
            // Irish Dry"). The family is what a reader means by "what do they
            // drink"; the full string is what they mean by "which exact
            // corner". Both, because twenty rows of substyle is a sample of the
            // vocabulary and twenty rows of family is most of it.
            family: { show: `trim(CASE WHEN instr(beer_type, ' - ') > 0
                                       THEN substr(beer_type, 1, instr(beer_type, ' - ') - 1)
                                       ELSE beer_type END)` },
            style: { show: "beer_type" },
            brewery: { show: "brewery_name" },
            venue: { show: "venue_name" },
            // The one facet with a published counterpart: `medium` counts
            // distinct beers and repeats on lower(beer_name), so grouping this
            // on the raw string would let `min_n:2` return a different set from
            // the `repeat_count` sitting beside it. Group on the same key and
            // show a real name off it.
            beer: { show: "min(beer_name)", group: "lower(beer_name)" },
          },
          score: "nullif(CAST(nullif(rating_score,'') AS REAL), 0)",
        },
      };
      const medium = mediumKey(asked, M);
      const d = M[medium];
      if (!d) {
        return empty(ctx, {
          note: asked
            ? `no facets for '${asked}' \u2014 this tool covers: ${Object.keys(M).join(", ")}`
            : `name a medium \u2014 this tool covers: ${Object.keys(M).join(", ")}`,
        });
      }
      const names = Object.keys(d.by);
      const key = names.includes(by) ? by : names[0];
      const { show, group = d.by[key].show } = d.by[key];

      const floor = Number.isFinite(min_n) && min_n > 1 ? Math.floor(min_n) : 1;
      // `played` first, and deliberately. A mean is the number a reader wants
      // and the number that misleads: one check-in of one saison at 4.5 outranks
      // forty hazy IPAs averaging 4.0 on any rating axis, and reads as a
      // favourite. Sorting by how often they actually reached for it puts the
      // groups that carry weight at the top, and `rated` is one argument away.
      const by_ = ordering(order, {
        played: "n DESC, mean DESC",
        rated:  "mean DESC, n DESC",
        recent: "last DESC, n DESC",
      });

      const rows = await q(
        env,
        // GROUP BY the alias, so the family expression is written once. `n`
        // counts check-ins and `rated` counts the ones that carried a score:
        // 58 of 1,964 beer rows have no rating, and a mean whose base is not
        // stated is a mean a reader will attach to the wrong number.
        `SELECT ${show} AS facet,
                ${group} AS grp,
                count(*) AS n,
                count(${d.score}) AS rated,
                round(avg(${d.score}), 2) AS mean,
                max(${d.score}) AS best,
                max(substr(created,1,10)) AS last
         FROM ${d.table}
         WHERE ${group} IS NOT NULL AND trim(${group}) <> ''
         GROUP BY grp
         HAVING count(*) >= ?
         ORDER BY ${by_.sql}, grp
         LIMIT ? OFFSET ?`,
        floor, probe(ctx), skip(ctx)
      );

      // How much of the medium this breakdown actually covers. Without it a
      // reader takes the top row as the top of everything, when the facet may
      // be blank on a fifth of the record. And how many groups there are at
      // all (ADR-0023), so a page of twenty families is not read as twenty.
      const [cov] = await q(
        env,
        `SELECT count(*) AS total,
                sum(CASE WHEN ${group} IS NULL OR trim(${group}) = '' THEN 1 ELSE 0 END) AS blank,
                (SELECT count(*) FROM (SELECT 1 FROM ${d.table}
                   WHERE ${group} IS NOT NULL AND trim(${group}) <> ''
                   GROUP BY ${group} HAVING count(*) >= ?)) AS groups
         FROM ${d.table}`,
        floor
      );

      const notes = [];
      if (!names.includes(by) && by) {
        notes.push(`no facet called '${by}' \u2014 grouped by ${key}; this medium offers ${names.join(", ")}`);
      }
      if (cov?.blank) {
        notes.push(`${cov.blank} of ${cov.total} ${d.unit} carry no ${key} and are not in any group below \u2014 that field is in the CSV export only`);
      }
      if (by_.order === "rated") {
        notes.push("sorted by mean, so a group of one sits beside a group of forty \u2014 read `n` before reading the order");
      }
      if (floor > 1) {
        notes.push(`only groups with at least ${floor} ${d.unit}`);
      }
      notes.push(BEER_CALIBRATION_POINTER);

      // The group key is named for what it IS \u2014 `brewery`, `family` \u2014 rather
      // than sitting under a generic `facet`, so a row read on its own still
      // says what it is a row about.
      const shaped = rows.map(({ facet, grp, ...rest }) => ({ medium, [key]: facet, ...rest }));
      return ordered(
        { ...cap(shaped, ctx), scale: d.scale,
          scope: `${cov?.groups ?? 0} ${key} groups over ${(cov?.total ?? 0) - (cov?.blank ?? 0)} of ${cov?.total ?? 0} ${d.unit}` },
        { order: by_.order, note: notes.join(" \u00b7 ") }
      );
    },
  },

  reviews: {
    class: "authored", domain: "culture", kind: "text",
    reads: ["t1_film_review"],
    description:
      "The owner's written film reviews, in their own words, each with its rating, its watch date and a link. The larger part of their film criticism; `verdicts` holds the cross-media opinions. `topic` matches the title or the review text; `min_rating` is on the 0-5 scale; newest watch first, or `order:'rated'` for the films they thought most of. Their sentences, not a summary of them.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against film title or review text." },
        min_rating: { type: "number", description: "Only films rated at least this, 0-5." },
        order: { type: "string", description: "recent (default, newest watch first) | rated" },
      },
    },
    async run(env, { topic, min_rating, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      // Same cast the min_rating filter already used: the column is TEXT off a
      // Letterboxd export, so an uncast DESC would rank '5' above '4.5' by luck
      // and '10' below both if the scale ever moved.
      const stars = "CAST(nullif(rating,'') AS REAL)";
      const by = ordering(order, {
        recent: `created DESC, ${stars} DESC`,
        rated:  `${stars} DESC, created DESC`,
      });
      const rows = await q(
        env,
        `SELECT title, year, rating, review, url, substr(created,1,10) AS watched
         FROM t1_film_review
         WHERE (? IS NULL OR lower(title) LIKE ? OR lower(review) LIKE ?)
           AND (? IS NULL OR CAST(nullif(rating,'') AS REAL) >= ?)
         ORDER BY ${by.sql}, id
         LIMIT ? OFFSET ?`,
        like, like, like,
        min_rating ?? null, min_rating ?? 0,
        probe(ctx), skip(ctx)
      );
      const [held] = await q(
        env,
        `SELECT count(*) AS n FROM t1_film_review
         WHERE (? IS NULL OR lower(title) LIKE ? OR lower(review) LIKE ?)
           AND (? IS NULL OR CAST(nullif(rating,'') AS REAL) >= ?)`,
        like, like, like, min_rating ?? null, min_rating ?? 0
      );
      return ordered({
        ...cap(rows.map((r) => ({ ...r, scale: "0-5" })), ctx),
        scope: `${held?.n ?? 0} reviews${topic || min_rating ? " matched" : ""}`,
      }, by);
    },
  },

  collection: {
    class: "possession", domain: "culture", kind: "entity",
    reads: ["t0_music", "t1_collection"],
    description:
      "What the owner owns, which is not what they consumed — vinyl, DVDs, board games, fragrances, whatever the inventory holds, with what they paid and where. Buying and keeping something is a stronger signal than playing it once. Most recently acquired first; `order:'oldest'` reaches what has been on the shelf longest; `order:'played'` ranks vinyl by scrobbles, since owning a record and wearing it out are different claims. `genre` is a few coarse buckets the owner typed into their own inventory, returned as `genres` on every answer — a word outside them cannot match, so a miss is a gap in the vocabulary, not in the shelf.",
    schema: {
      type: "object",
      properties: {
        kind: { type: "string", description: "vinyl | dvd | board_game | fragrance" },
        topic: { type: "string", description: "Match against title, creator or genre. Genre is a closed handful of hand-kept buckets, returned as `genres` on every answer — a word outside them cannot match, so a miss is a gap in the vocabulary and not in the shelf." },
        order: { type: "string", description: "recent (default, newest acquisition) | oldest | played (vinyl scrobbles)" },
      },
    },
    async run(env, { kind, topic, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      // Undated rows go last on BOTH date axes. `acquired` is empty for a good
      // part of the shelf, and an ASC sort that took '' at face value would open
      // "what has been sitting longest" with everything nobody dated.
      const by = ordering(order, {
        recent: "COALESCE(c.acquired,'') DESC, c.title",
        oldest: "nullif(c.acquired,'') IS NULL, nullif(c.acquired,'') ASC, c.title",
        played: "p.plays IS NULL, p.plays DESC, c.title",
      });
      const rows = await q(
        env,
        // plays is computed, not stored. The sheet records what is owned and the
        // store holds the scrobbles, so the count is a join rather than a
        // field to keep in sync, and fresher than the enrichment it replaces.
        // Matched on artist AND album: artist alone reported an artist's total
        // plays against every one of their records.
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
         ORDER BY ${by.sql}, c.id
         LIMIT ? OFFSET ?`,
        kind ?? null, kind ?? null,
        like, like, like, like,
        probe(ctx), skip(ctx)
      );
      // Drop the columns a given kind does not use rather than emitting a wall
      // of nulls — a board game has no genre and a DVD has no thoughts.
      const out = rows.map((r) => Object.fromEntries(
        Object.entries(r).filter(([, v]) => v !== null && v !== "")
      ));

      // A topic that matched nothing is the dangerous answer here, because
      // `topic` is matched against `genre` and `genre` is a hand-kept sheet
      // column with a closed handful of values in it — not a taxonomy anyone
      // agreed to. An empty answer to topic:'ambient' means the sheet has no
      // bucket by that name; it does not mean the shelf holds none, and a
      // reader with no way to tell the two apart will say the wrong one
      // (ADR-0023 §3). So a miss returns the vocabulary rather than nothing.
      if (!out.length && topic) {
        const vocab = await q(
          env,
          `SELECT genre AS name, count(*) AS n FROM t1_collection
           WHERE genre IS NOT NULL AND genre != ''
             AND (? IS NULL OR kind = ?)
           GROUP BY 1 ORDER BY n DESC LIMIT 20`,
          kind ?? null, kind ?? null
        );
        return empty(ctx, {
          scope: `0 of the shelf matched`,
          ...(vocab.length ? { genres: vocab.map((r) => `${r.name} (${r.n})`) } : {}),
          note: vocab.length
            ? `nothing matched '${topic}'. Genre here is a free-text column on the owner's own inventory, and the genres listed above are the whole of it — a word that is not one of those buckets cannot match, whatever is actually on the shelf. Ask by title or creator instead, or use taste for what they play.`
            : `nothing matched '${topic}', and nothing on this shelf carries a genre at all — so a miss says nothing either way. Ask by title or creator instead, or use taste for what they play.`,
        });
      }

      // The vocabulary rides along on every genre-bearing answer too, so a
      // caller learns the buckets exist before it reasons about a gap in them
      // rather than after.
      const shelf = await q(
        env,
        `SELECT genre AS name, count(*) AS n FROM t1_collection
         WHERE genre IS NOT NULL AND genre != '' AND (? IS NULL OR kind = ?)
         GROUP BY 1 ORDER BY n DESC LIMIT 20`,
        kind ?? null, kind ?? null
      );
      const [held] = await q(
        env,
        `SELECT count(*) AS n FROM t1_collection c
         WHERE (? IS NULL OR c.kind = ?)
           AND (? IS NULL OR lower(c.title) LIKE ? OR lower(COALESCE(c.creator,'')) LIKE ?
                          OR lower(COALESCE(c.genre,'')) LIKE ?)`,
        kind ?? null, kind ?? null, like, like, like, like
      );
      const capped = cap(out, ctx);
      const note = [
        capped.note,
        shelf.length
          ? `genre is ${shelf.length} hand-assigned buckets on the owner's own inventory — coarse by construction, and never a claim that nothing outside them is on the shelf`
          : null,
      ].filter(Boolean).join(" · ");
      return ordered({
        ...capped,
        scope: `${held?.n ?? 0} owned${kind ? ` (${kind})` : ""}${topic ? ` matching '${topic}'` : ""}`,
        ...(shelf.length ? { genres: shelf.map((r) => `${r.name} (${r.n})`) } : {}),
        ...(note ? { note } : {}),
      }, by);
    },
  },

  watching: {
    class: "revealed", domain: "culture", kind: "entity",
    reads: ["t0_tv", "t0_anime"],
    description:
      "What the owner started and has not finished, per show: episodes watched, how long since the last one, and an episode total where one is known. Trakt is the record of what is actually watched and is what every status here is derived from. MyAnimeList supplies denominators only — the list stopped being kept, so the word they last filed against a title is returned as `declared` with the date the list froze (`declared_on`), never as the current state. Filter status='stalled' for what quietly stopped, or declared='dropped' for what they abandoned on purpose back when they were still keeping the list.",
    schema: {
      type: "object",
      properties: {
        status: {
          type: "string",
          description:
            "watching | stalled | completed | unknown. All four are DERIVED from Trakt activity and none is something the owner said. `completed` and `stalled` need an episode total, so a show without one is `unknown` however plainly it ended.",
        },
        declared: {
          type: "string",
          description:
            "watching | completed | dropped | on_hold | plan_to_watch — the owner's own MyAnimeList word, frozen on the day the list was last updated (`declared_on` on every row that carries one). A historical fact about the list, not a claim about now.",
        },
        stalled_since: {
          type: "string",
          description:
            "ISO date. Only shows whose last episode was on or before it, and which are not already finished.",
        },
        order: { type: "string", description: "oldest (default, longest untouched first) | recent | rated" },
      },
    },
    async run(env, { status, declared, stalled_since, order }, ctx) {
      // Today, bound rather than read inside SQL, so the same call against the
      // same bundle is reproducible from outside and the day boundary is the
      // worker's rather than D1's.
      const today = new Date().toISOString().slice(0, 10);
      // What counts as stalled, in one place. 180 days is a season and a half:
      // long enough that a gap is not a break between cours, short enough that
      // a show abandoned last winter shows up before it is a year gone.
      const STALL_DAYS = 180;

      // Two vocabularies now, because there are two sources and they no longer
      // mean the same kind of thing. Everything in STATUSES is worked out here
      // from Trakt; everything in DECLARED is a word the owner typed into a
      // list they stopped keeping. Collapsing them is what made a 2023 edit
      // read as "currently watching" for three years.
      const STATUSES = ["watching", "stalled", "completed", "unknown"];
      const DECLARED = ["watching", "completed", "dropped", "on_hold", "plan_to_watch"];
      const norm = (v) =>
        typeof v === "string" ? v.trim().toLowerCase().replace(/[\s-]+/g, "_") : "";

      const askedStatus = norm(status);
      const askedDeclared = norm(declared);
      // `dropped`, `on_hold` and `plan_to_watch` used to be status values and
      // the old schema advertised them, so a caller still asking that way is
      // answered rather than silently given everything. They are declarations,
      // so the ask is routed to the column that still holds them.
      const rerouted = STATUSES.includes(askedStatus) ? null
        : DECLARED.includes(askedStatus) ? askedStatus : null;
      const wantStatus = STATUSES.includes(askedStatus) ? askedStatus : null;
      const wantDeclared = DECLARED.includes(askedDeclared) ? askedDeclared
        : rerouted;

      const by = ordering(order, {
        oldest: "(last_watched IS NULL), last_watched ASC",
        recent: "(last_watched IS NULL), last_watched DESC",
        rated:  "score DESC, last_watched DESC",
      });

      // The spine is Trakt, because that is where watching leaves a trace
      // (ADR-0025). MAL is joined in twice and for two different reasons:
      //
      //   `shelf`, on show_key, sums episode totals across every season entry —
      //   Trakt counts a show and MAL files a season each, so the denominator
      //   only measures the same thing as the numerator once it is summed.
      //
      //   the scalar subqueries, on match_key, read the exact entry for that
      //   title. `declared` and `score` are annotations on one list row and
      //   must not be smeared across a rollup.
      const rows = await q(
        env,
        `WITH shelf AS (
           SELECT show_key AS k,
                  sum(CAST(episodes_total AS INTEGER)) AS listed_total,
                  count(*) AS seasons_listed
             FROM t0_anime
            WHERE show_key <> ''
            GROUP BY show_key
         ),
         seen AS (
           SELECT t.id AS id, t.title AS title, t.url AS url,
                  t.imdb AS imdb, t.year AS year,
                  CAST(t.episodes_watched AS INTEGER) AS watched,
                  CAST(t.seasons_touched AS INTEGER) AS seasons_touched,
                  nullif(substr(t.created, 1, 10), '') AS last_watched,
                  shelf.listed_total AS listed_total,
                  shelf.seasons_listed AS seasons_listed,
                  (SELECT a.status FROM t0_anime a
                    WHERE a.match_key = t.match_key AND t.match_key <> ''
                    LIMIT 1) AS declared,
                  (SELECT CAST(a.score AS INTEGER) FROM t0_anime a
                    WHERE a.match_key = t.match_key AND t.match_key <> ''
                    LIMIT 1) AS score,
                  (SELECT a.url FROM t0_anime a
                    WHERE a.match_key = t.match_key AND t.match_key <> ''
                    LIMIT 1) AS mal_url
             FROM t0_tv t
             LEFT JOIN shelf ON shelf.k = t.show_key AND t.show_key <> ''
         ),
         graded AS (
           SELECT seen.*,
                  -- A denominator is believed only if it can still be true. MAL
                  -- closed while Trakt kept counting, so a show whose later
                  -- seasons were never listed rolls up SHORT of what has
                  -- already been watched. That is not 110% of a series; it is a
                  -- total that stopped being the total, and a ratio built on it
                  -- would read as finished-and-then-some.
                  CASE WHEN listed_total > 0 AND watched <= listed_total
                       THEN listed_total END AS total,
                  CASE WHEN listed_total IS NULL THEN 'never on the anime list'
                       WHEN listed_total > 0 AND watched > listed_total
                       THEN 'the list stops short of what Trakt has counted'
                       WHEN listed_total = 0
                       THEN 'the list carries no episode total for it'
                  END AS no_total_because
             FROM seen
         ),
         judged AS (
           SELECT graded.*,
                  CASE WHEN last_watched IS NULL THEN NULL
                       ELSE CAST(julianday(?) - julianday(last_watched) AS INTEGER)
                  END AS idle_days,
                  CASE
                    WHEN last_watched IS NULL THEN 'unknown'
                    WHEN total > 0 AND watched >= total THEN 'completed'
                    WHEN total > 0 AND watched < total
                         AND julianday(?) - julianday(last_watched) >= ? THEN 'stalled'
                    WHEN julianday(?) - julianday(last_watched) < ? THEN 'watching'
                    ELSE 'unknown'
                  END AS status
             FROM graded
         )
         SELECT * FROM judged
          WHERE (? IS NULL OR status = ?)
            AND (? IS NULL OR declared = ?)
            AND (? IS NULL OR (last_watched IS NOT NULL AND last_watched <= ?
                               AND status <> 'completed'))
          ORDER BY ${by.sql}, id
          LIMIT ? OFFSET ?`,
        today,
        today, STALL_DAYS,
        today, STALL_DAYS,
        wantStatus, wantStatus,
        wantDeclared, wantDeclared,
        stalled_since ?? null, stalled_since ?? null,
        probe(ctx), skip(ctx)
      );

      // The population, and the part of it this tool cannot measure (ADR-0023).
      // Both numbers are about the DENOMINATOR, which is the only thing MAL is
      // still trusted for: how many shows Trakt knows, and how many of those
      // the list can put a total against.
      const [held] = await q(
        env,
        `SELECT (SELECT count(*) FROM t0_tv) AS shows,
                (SELECT count(*) FROM t0_tv t
                  WHERE t.show_key <> '' AND EXISTS (
                    SELECT 1 FROM t0_anime a
                     WHERE a.show_key = t.show_key
                       AND CAST(a.episodes_total AS INTEGER) > 0)) AS denominated,
                -- The day the list froze, read off the list itself rather than
                -- written here: the last edit anyone made to it is the date
                -- every declared word is true as of (ADR-0014).
                (SELECT max(substr(last_updated,1,10)) FROM t0_anime
                  WHERE last_updated IS NOT NULL AND last_updated <> '') AS froze`
      );
      const frozeOn = held?.froze ?? null;

      const out = rows.map((r) => ({
        title: r.title,
        status: r.status,
        // The owner's own word, kept beside the derived one and DATED. Without
        // the date this is the bug it replaced: a years-old list edit reading
        // as a present-tense claim.
        ...(r.declared ? { declared: r.declared, declared_on: frozeOn } : {}),
        episodes_watched: r.watched,
        episodes_total: r.total ?? null,
        ...(r.total == null && r.no_total_because
          ? { no_episode_total: r.no_total_because } : {}),
        ...(r.score > 0 ? { score: r.score, scale: "1-10" } : {}),
        last_watched: r.last_watched ?? null,
        ...(r.last_watched ? { days_since: r.idle_days } : {}),
        ...(r.seasons_touched > 0 ? { seasons_touched: r.seasons_touched } : {}),
        ...(r.url ? { url: r.url } : {}),
        ...(r.mal_url ? { mal_url: r.mal_url } : {}),
      }));

      const notes = [
        COUNT_GAP,
        `every status here is derived from Trakt activity; \`declared\` is the owner's MyAnimeList word and the list was last updated ${frozeOn ?? "on a date the export does not carry"}`,
        `stalled means no episode in ${STALL_DAYS} days with episodes still to watch — derived here, never declared`,
        held?.shows
          ? `${held.denominated} of ${held.shows} shows can be given an episode total; the rest can be called neither finished nor unfinished`
          : null,
        rerouted
          ? `'${rerouted}' is a declaration rather than a derived status, so it was read as declared='${rerouted}'`
          : null,
        status && !wantStatus && !rerouted
          ? `no status called '${status}' — unfiltered; this tool knows ${STATUSES.join(", ")}`
          : null,
        declared && !DECLARED.includes(askedDeclared)
          ? `no declaration called '${declared}' — unfiltered; the list knows ${DECLARED.join(", ")}`
          : null,
      ].filter(Boolean);

      const capped = cap(out, ctx);
      return ordered({
        ...capped,
        scope: `${held?.shows ?? 0} shows in the television record; these are the head of it`,
        note: [capped.note, ...notes].filter(Boolean).join(" · "),
      }, by);
    },
  },


  saves: {
    class: "intent", domain: "*", kind: "pointer",
    reads: ["t0_raindrop"],
    description:
      "Links the owner bookmarked. A save is attention, not consumption: it caught their eye, and nothing here says they finished it or agreed with it. Filter by `platform`, by `collection` (their own buckets), by `kind` of link, by `tag`, by `topic` (title, tags or collection), by `since`/`until`, or to those they wrote a note on. With no filter, the axes — which platforms and collections exist and how many — rather than an arbitrary page. Newest first; `order:'oldest'` reaches what has been sitting longest. For what they own see `collection`; for what they queued on purpose see `backlog`.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against title, tags or collection." },
        platform: { type: "string", description: "One platform by name, e.g. youtube, substack, pinterest — the unfiltered answer lists the ones that exist." },
        collection: { type: "string", description: "One of the owner's own collections, by name — the unfiltered answer lists them." },
        kind: { type: "string", description: "link | article | video | image | audio | document" },
        tag: { type: "string", description: "A single tag." },
        since: { type: "string", description: "ISO date; only saves on or after." },
        until: { type: "string", description: "ISO date; only saves on or before." },
        with_note: { type: "boolean", description: "Only saves with a note written on them." },
        order: { type: "string", description: "recent (default, newest save first) | oldest" },
      },
    },
    async run(env, { topic, platform, collection, kind, tag, since, until, with_note, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const by = ordering(order, {
        recent: "created DESC",
        oldest: "created ASC",
      });

      // With nothing to narrow by, return the AXES rather than an arbitrary
      // page of links. A flat sample of a large library teaches an assistant
      // nothing about what is in it; the facets tell it what it can ask next,
      // which is the same job the standing brief does one level up.
      if (!topic && !platform && !collection && !kind && !tag && !since && !until && !with_note) {
        const plats = await q(env,
          `SELECT platform AS name, count(*) AS n FROM t0_raindrop
           GROUP BY 1 ORDER BY n DESC LIMIT 10`);
        const cols = await q(env,
          `SELECT collection AS name, count(*) AS n FROM t0_raindrop
           WHERE collection <> '' GROUP BY 1 ORDER BY n DESC LIMIT 10`);
        const [tot] = await q(env,
          `SELECT count(*) AS n, min(substr(created,1,10)) AS oldest,
                  max(substr(created,1,10)) AS newest FROM t0_raindrop`);
        return empty(ctx, {
          total: tot?.n ?? 0,
          span: `${tot?.oldest ?? "?"} .. ${tot?.newest ?? "?"}`,
          platforms: plats,
          collections: cols,
          scope: `${tot?.n ?? 0} saves, ${tot?.oldest ?? "?"} → ${tot?.newest ?? "?"}`,
          note: "no filter given — these are the axes; call again with one to get links",
        });
      }

      const WHERE = `WHERE (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(tags,'')) LIKE ?
                          OR lower(COALESCE(collection,'')) LIKE ?)
           AND (? IS NULL OR lower(platform) = lower(?))
           AND (? IS NULL OR lower(collection) = lower(?))
           AND (? IS NULL OR kind = ?)
           AND (? IS NULL OR lower(COALESCE(tags,'')) LIKE ?)
           AND (? IS NULL OR substr(created,1,10) >= ?)
           AND (? IS NULL OR substr(created,1,10) <= ?)
           AND (? IS NULL OR note <> '')`;
      const binds = [
        like, like, like, like,
        platform ?? null, platform ?? "",
        collection ?? null, collection ?? "",
        kind ?? null, kind ?? "",
        tag ?? null, tag ? `%${tag.toLowerCase()}%` : "",
        since ?? null, since ?? "",
        until ?? null, until ?? "",
        with_note ? 1 : null,
      ];
      const rows = await q(
        env,
        `SELECT title, url, kind, platform, collection, tags,
                nullif(note,'') AS note, substr(created,1,10) AS saved
         FROM t0_raindrop
         ${WHERE}
         ORDER BY ${by.sql}, id
         LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );
      // The FILTERED set, not the zone (ADR-0023): "searched 2,000 saves"
      // after platform:'youtube' is the wrong denominator for the page it
      // sits beside.
      const [tot] = await q(env,
        `SELECT count(*) AS n, max(substr(created,1,10)) AS newest,
                sum(CASE WHEN note <> '' THEN 1 ELSE 0 END) AS noted
         FROM t0_raindrop ${WHERE}`, ...binds);
      // Say when the commentary is absent rather than letting silence read as
      // "these were saved without comment": a snapshot seeded through an MCP
      // that does not return the field carries no notes at all.
      const noted = tot?.noted ?? 0;
      const capped = cap(rows, ctx);
      const said = noted === 0
        ? "the owner's own notes on these saves are not synced — absence here is not evidence none were written"
        : `${noted} of these carry a note the owner wrote`;
      return ordered({
        ...capped,
        scope: `${tot?.n ?? 0} saves matched, newest ${tot?.newest ?? "?"}`,
        note: [capped.note, said].filter(Boolean).join(" · "),
      }, by);
    },
  },

  backlog: {
    class: "intent", domain: "*", kind: "pointer",
    reads: ["t0_book", "t0_raindrop"],
    // The kinds are not one table, and they are not one publicity either: the
    // shelves come off a Goodreads profile and the collections out of a private
    // Raindrop account. Graded as one tool, the gift list decided for the
    // reading list, and a shelf off a public profile was stamped private
    // because a folder of present ideas shared the door (ADR-0019 §2).
    readsFor: ({ kind }) =>
      kind === "read" || kind === "resume" ? ["t0_book"]
      : kind === "make" || kind === "buy" ? ["t0_raindrop"]
      : ["t0_book", "t0_raindrop"],   // no kind: the summary counts both piles
    description:
      "What the owner queued and has not done — a deliberate act of shelving or filing, which separates it from `saves` (attention) and `collection` (already owned). Four piles: `read` (shelved to-read), `resume` (books started and set down), `make` (things to build or cook), `buy` (gift and shopping ideas). With no `kind`, the size of each pile. Newest first; `order:'oldest'` digs up what has been sitting, which is usually the question. Nobody prunes these, so an old row is a decision made once, not a live intention. Every answer names the pile this record cannot see.",
    schema: {
      type: "object",
      properties: {
        kind: { type: "string", description: "read | resume | make | buy" },
        topic: { type: "string", description: "Match against title or author." },
        since: { type: "string", description: "ISO date; only things queued on or after." },
        until: { type: "string", description: "ISO date; only things queued on or before." },
        order: { type: "string", description: "recent (default) | oldest" },
      },
    },
    async run(env, { kind, topic, since, until, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      // Two piles, two date columns, one vocabulary: the shelves date from
      // Goodreads' date_added and the collections from raindrop's created, and
      // a caller should not have to know which pile it is asking.
      const dated = (col) => ordering(order, {
        recent: `substr(${col},1,10) DESC`,
        oldest: `substr(${col},1,10) ASC`,
      });

      // The kinds are not one table. Books carry their queue state in a shelf
      // column; the making and buying queues are raindrop collections created
      // by hand. Keeping them as named kinds rather than one union means each
      // can say what it actually knows.
      //
      // WHICH shelves and collections is an instance fact (ADR-0014). The
      // shelf defaults are Goodreads' own vocabulary, which every export
      // carries; the collections have no default at all, because a bucket
      // named by hand in one account is not a name the engine gets to know.
      // Both come from `[surface]` in exo.toml, published beside the tool
      // list, and are matched case-insensitively.
      const inst = instanceOf(ctx);
      const names = (v, dflt) =>
        (Array.isArray(v) ? v : dflt).map((x) => String(x).trim().toLowerCase()).filter(Boolean);
      const SHELVES = {
        read: names(inst.backlog_read, ["to-read"]),
        resume: names(inst.backlog_resume, ["currently-reading"]),
      };
      const COLLECTIONS = {
        make: names(inst.backlog_make, []),
        buy: names(inst.backlog_buy, []),
      };
      const marks = (list) => list.map(() => "?").join(",") || "''";
      const unnamed = (k) =>
        `no collection is named for the '${k}' pile — this instance sets [surface] backlog_${k} in exo.toml to the raindrop collections that hold it, and until then this pile cannot be counted`;

      // No kind: return the sizes rather than an arbitrary page off one pile.
      // Same reasoning as `saves` — the shape of the backlog is the useful
      // answer when nothing has been narrowed.
      if (!kind) {
        const [b] = await q(env,
          `SELECT sum(CASE WHEN lower(shelf) IN (${marks(SHELVES.read)}) THEN 1 ELSE 0 END) AS to_read,
                  sum(CASE WHEN lower(shelf) IN (${marks(SHELVES.resume)}) THEN 1 ELSE 0 END) AS resume
           FROM t0_book`, ...SHELVES.read, ...SHELVES.resume);
        const [m] = await q(env,
          `SELECT sum(CASE WHEN lower(collection) IN (${marks(COLLECTIONS.make)}) THEN 1 ELSE 0 END) AS make,
                  sum(CASE WHEN lower(collection) IN (${marks(COLLECTIONS.buy)}) THEN 1 ELSE 0 END) AS buy
           FROM t0_raindrop`, ...COLLECTIONS.make, ...COLLECTIONS.buy);
        const missing = ["make", "buy"].filter((k) => !COLLECTIONS[k].length);
        return empty(ctx, {
          kinds: [
            { kind: "read", n: b?.to_read ?? 0, source: "goodreads shelf" },
            { kind: "resume", n: b?.resume ?? 0, source: "goodreads shelf" },
            { kind: "make", n: m?.make ?? 0, source: "raindrop collection" },
            { kind: "buy", n: m?.buy ?? 0, source: "raindrop collection" },
          ],
          note: ["no kind given — these are the piles; call again with one",
                 ...missing.map(unnamed)].join(" · "),
          gap: WATCH_GAP,
        });
      }

      if (SHELVES[kind]) {
        const shelves = SHELVES[kind];
        const by = dated("date_added");
        const rows = await q(
          env,
          // date_added arrives in two shapes from Goodreads exports
          // ('2026-08-16' and '2025-10-24T00:00:00'), so every comparison and
          // every emitted date goes through substr — an unnormalised >= would
          // silently drop the T-suffixed rows.
          `SELECT title, book_author AS author, shelf,
                  substr(date_added,1,10) AS queued, avg_rating
           FROM t0_book
           WHERE lower(shelf) IN (${marks(shelves)})
             AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(book_author,'')) LIKE ?)
             AND (? IS NULL OR substr(date_added,1,10) >= ?)
             AND (? IS NULL OR substr(date_added,1,10) <= ?)
           ORDER BY ${by.sql}, title, id
           LIMIT ? OFFSET ?`,
          ...shelves,
          like, like, like,
          since ?? null, since ?? "",
          until ?? null, until ?? "",
          probe(ctx), skip(ctx)
        );
        const [tot] = await q(env,
          `SELECT count(*) AS n, min(substr(date_added,1,10)) AS oldest
           FROM t0_book WHERE lower(shelf) IN (${marks(shelves)})`,
          ...shelves);
        const capped = cap(rows, ctx);
        return {
          ...ordered(capped, by),
          scope: `${tot?.n ?? 0} on this shelf, oldest queued ${tot?.oldest ?? "?"}`,
          // my_rating is 0 across every unread row, so it is omitted rather
          // than emitted as a zero an assistant would read as a verdict.
          note: [capped.note, "avg_rating is Goodreads' crowd score, not the owner's — they have not read these"]
            .filter(Boolean).join(" · "),
          gap: WATCH_GAP,
        };
      }

      if (COLLECTIONS[kind]) {
        const cols = COLLECTIONS[kind];
        if (!cols.length) return empty(ctx, { note: unnamed(kind), gap: WATCH_GAP });
        const by = dated("created");
        const rows = await q(
          env,
          `SELECT title, url, platform, collection, nullif(note,'') AS note,
                  substr(created,1,10) AS queued
           FROM t0_raindrop
           WHERE lower(collection) IN (${marks(cols)})
             AND (? IS NULL OR lower(title) LIKE ? OR lower(COALESCE(tags,'')) LIKE ?)
             AND (? IS NULL OR substr(created,1,10) >= ?)
             AND (? IS NULL OR substr(created,1,10) <= ?)
           ORDER BY ${by.sql}, title, id
           LIMIT ? OFFSET ?`,
          ...cols,
          like, like, like,
          since ?? null, since ?? "",
          until ?? null, until ?? "",
          probe(ctx), skip(ctx)
        );
        const [tot] = await q(env,
          `SELECT count(*) AS n FROM t0_raindrop WHERE lower(collection) IN (${marks(cols)})`,
          ...cols);
        const capped = cap(rows, ctx);
        // A hand-filed collection undercounts by construction: most making
        // references sit unfiled in the platform saves, and reporting the
        // filed few as the whole pile understates it.
        const said = [capped.note,
                      kind === "make"
                        ? "only what was filed into the making collection; most making references sit unfiled in the platform saves — try saves(platform:'pinterest') or saves(kind:'video')"
                        : null].filter(Boolean).join(" · ");
        return {
          ...ordered(capped, by),
          scope: `${tot?.n ?? 0} in ${cols.join(" + ")}`,
          ...(said ? { note: said } : {}),
          gap: WATCH_GAP,
        };
      }

      return empty(ctx, {
        note: `no pile called '${kind}' — this tool knows read, resume, make, buy`,
        kinds: ["read", "resume", "make", "buy"],
        gap: WATCH_GAP,
      });
    },
  },

  recent_topics: {
    class: "dialogue", domain: "mind", kind: "event",
    reads: ["t0_chat_topic"],
    description:
      "What the owner has been working through in conversation — titles, dates and turn counts, with a clipped machine-written gist, never transcripts. Fresher than their notes, which lag a deliberate act of capture. Turn count is the signal a title cannot carry: a long thread is a preoccupation, a short one a glance. `topic` matches the title, the gist or where the thread landed, so a short word works better than a sentence; `min_turns` alone lists the long ones; `order:'oldest'` reaches the earliest. Each row carries the `id` that `thread` reads by. Half of every thread is another model's; only the owner's turns are theirs.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against the conversation title, its gist, or where it landed." },
        min_turns: { type: "number", description: "Only conversations at least this long." },
        order: { type: "string", description: "recent (default, last active first) | oldest" },
      },
    },
    async run(env, { topic, min_turns, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const by = ordering(order, {
        recent: "last_seen DESC, turns DESC",
        oldest: "last_seen ASC, turns DESC",
      });
      const WHERE = `WHERE (? IS NULL OR lower(title) LIKE ?
                          OR lower(COALESCE(summary, '')) LIKE ?
                          OR lower(COALESCE(landed, '')) LIKE ?)
           AND (? IS NULL OR turns >= ?)`;
      const binds = [like, like, like, like, min_turns ?? null, min_turns ?? 0];
      const rows = await q(
        env,
        `SELECT id, title, last_seen, started, turns, his_turns,
                -- Clipped here, whole in the thread tool. Listing threads with
                -- no distillation meant the only route to one was already
                -- knowing which thread to ask about, which is the opposite of
                -- what a list is for.
                substr(COALESCE(summary,''), 1, 200) AS gist
         FROM t0_chat_topic
         -- Title, gist and landing, not title alone. Titles here are topical
         -- and the gist is thematic: a thread about theory of change is titled
         -- after healthcare, so a title-only match answers "no such thread" to
         -- a question the corpus does contain. The gist was already SELECTed
         -- and shown to the caller; it was simply never searched.
         ${WHERE}
         ORDER BY ${by.sql}, id
         LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );
      const [held] = await q(env, `SELECT count(*) AS n FROM t0_chat_topic ${WHERE}`, ...binds);
      return ordered({
        ...cap(rows, ctx),
        scope: `${held?.n ?? 0} conversations${topic || min_turns ? " matched" : ""}`,
      }, by);
    },
  },

  thread: {
    class: "dialogue", domain: "mind", kind: "text",
    // One conversation is never a page of anything; the turns inside it are
    // walked with `from_turn`, so tools/list does not advertise limit/offset.
    pages: false,
    reads: ["t0_chat", "t0_chat_topic"],
    description:
      "One conversation, by `id` from a `recent_topics` listing, or matched by title first and by gist or landing second. `include` chooses what comes back: `conclusion` (where the owner landed, verbatim, beside the machine distillation, which is marked as such), `turns` (only what the owner typed), `dialogue` (both sides interleaved and speaker-tagged), or `both` (default: conclusion plus the owner's turns). Prefer `dialogue` when the owner's turns read as fragments — they are often questions, and the answer is the half you cannot otherwise see. Lines tagged as the assistant are another model's output: context for reading the owner, never their words. Long threads are clipped to the byte budget; the answer says how many turns were shown and `from_turn` continues from there.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Thread title, or what it was about. Not needed when `id` is given." },
        id: { type: "string", description: "The `id` a recent_topics listing returned. Reads that exact thread, no matching." },
        include: { type: "string", description: "conclusion | turns | dialogue | both (default)" },
        from_turn: { type: "integer", minimum: 0, description: "Start the turns at this turn number. A clipped answer names the next one to pass." },
      },
    },
    async run(env, { topic, id, include, from_turn }, ctx) {
      const want = include || "both";
      const start = Number.isInteger(from_turn) && from_turn > 0 ? from_turn : 0;
      if (!id && !topic) return empty(ctx, { note: "pass a topic to match a thread by, or the id a recent_topics listing returned" });
      const like = `%${(topic || "").toLowerCase()}%`;
      const [hit] = id
        ? await q(
            env,
            `SELECT id, title, landed, turns, his_turns, last_seen,
                    COALESCE(summary, '') AS summary, COALESCE(summary_by, '') AS summary_by
             FROM t0_chat_topic WHERE id = ? LIMIT 1`,
            String(id)
          )
        : await q(
            env,
            `SELECT id, title, landed, turns, his_turns, last_seen,
                    COALESCE(summary, '') AS summary, COALESCE(summary_by, '') AS summary_by
             FROM t0_chat_topic
             -- Widened with recent_topics, and for the same reason. This param has
             -- always been documented as "thread title, or what it was about", and
             -- until now only the first half was true. Title matches still win —
             -- the ORDER BY puts them first — so naming a thread outright returns
             -- that thread, not one that merely mentions it in passing.
             WHERE lower(title) LIKE ?
                OR lower(COALESCE(summary, '')) LIKE ?
                OR lower(COALESCE(landed, '')) LIKE ?
             ORDER BY (lower(title) LIKE ?) DESC, turns DESC LIMIT 1`,
            like, like, like, like
          );
      if (!hit) {
        return empty(ctx, {
          note: id
            ? `no thread with id '${id}' — ids come from recent_topics, and a held one is absent rather than hidden`
            : "no thread matched — try recent_topics to see what exists",
        });
      }

      const out = {
        id: hit.id,
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

      // The clip note names a real move: the turn to continue from. Without
      // it the advice was "ask about a narrower part", and nothing on this
      // tool could narrow a thread.
      const clipNote = (kept, turns, whose) => {
        const next = turns[kept.length]?.turn;
        const shown = `${kept.length} of ${turns.length}${whose} turns shown`;
        return next !== undefined
          ? `${shown}, from turn ${turns[0]?.turn ?? start} — pass from_turn:${next} for the rest`
          : `${shown}, from turn ${turns[0]?.turn ?? start}`;
      };

      if (want === "dialogue") {
        // Interleaved and speaker-tagged. Owner turns are often questions —
        // returning them alone reads as a list of half-thoughts and invites a
        // reader to reconstruct the missing half by guessing.
        const turns = await q(
          env,
          `SELECT turn, channel, text FROM t0_chat
           WHERE title = ? AND CAST(turn AS INTEGER) >= ?
           ORDER BY CAST(turn AS INTEGER), id LIMIT ? OFFSET ?`,
          hit.title, start, 400, 0
        );
        const lines = turns.map(
          (t) => `${t.channel === "his" ? (env.OWNER_LABEL || "owner") : "assistant"}: ${t.text || ""}`
        );
        // Set before budgeting: the note is part of the row it has to fit in.
        out.dialogue_note =
          "lines tagged assistant are another model's output — context, not fact, and not the owner's words";
        const kept = fitInto(out, "dialogue", lines, TURN_NOTE_SAMPLE);
        out.dialogue = kept;
        if (kept.length < turns.length) out.note = clipNote(kept, turns, "");
        return single(out);
      }

      if (want !== "conclusion") {
        // Owner turns are short, so a thread's whole side usually fits.
        // Ordered, and capped by the same budget as everything else rather
        // than by a turn count, because thread lengths vary a hundredfold.
        const turns = await q(
          env,
          `SELECT turn, text FROM t0_chat
           WHERE title = ? AND CAST(turn AS INTEGER) >= ?
           ORDER BY CAST(turn AS INTEGER), id LIMIT ? OFFSET ?`,
          hit.title, start, 200, 0
        );
        const kept = fitInto(out, "his_words", turns.map((t) => t.text || ""), TURN_NOTE_SAMPLE);
        out.his_words = kept;
        if (kept.length < turns.length) out.note = clipNote(kept, turns, " of the owner's");
      }
      return single(out);
    },
  },

  taste_summary: {
    class: "derived", domain: "*", kind: "judgement",
    // Documents, one at a time, or a directory of a few: nothing to page.
    pages: false,
    reads: ["t0_taste_derived", "t0_beer"],
    // `beer` is computed here and reads nothing else; the two stored documents
    // read nothing else either. An unrecognised kind grades on both, which is
    // the tightest reading and the right one for a typo.
    readsFor: ({ kind }) =>
      kind === "beer" ? ["t0_beer"]
      : kind === "dining" || kind === "clusters" ? ["t0_taste_derived"]
      : ["t0_taste_derived", "t0_beer"],
    description:
      "How to read the owner's rating scales — calibration documents, one per kind, to be read before calling any number high or low. `dining` and `clusters` are documents mirrored from the taste engine; `beer` is computed here from the check-in log and is always current: mean, median, deciles and the count at every step the record uses. With no `kind`, the list of documents and their sizes. Only the kinds listed exist; film, book and anime scales have no calibration and should be reported flat with their scale.",
    schema: {
      type: "object",
      properties: { kind: { type: "string", description: "dining | clusters | beer" } },
    },
    async run(env, { kind }, ctx) {
      // `beer` is the one kind that is not a stored document. Nothing upstream
      // writes it: `t0_taste_derived` is a mirror of a foreign system's output,
      // and that system has never seen the beer log. The distribution it would
      // need is sitting in D1, so the calibration is computed from the record
      // it calibrates rather than waiting on a file nobody is writing.
      if (kind === "beer") {
        const doc = await beerCalibration(env);
        return doc ? single(doc) : empty(ctx, { note: "no beer ratings are published here" });
      }

      // The two halves are now independently held-able: ADR-0020 offers this
      // tool when EITHER zone is served, so the stored documents may simply not
      // be in the bundle. That is an absent table rather than an empty one, and
      // it is the only failure swallowed here — a computed answer must not be
      // taken down by a mirror this instance chose not to publish.
      const derived = await q(
        env,
        kind
          ? `SELECT kind, text FROM t0_taste_derived WHERE kind = ?`
          : `SELECT kind, text FROM t0_taste_derived`,
        ...(kind ? [kind] : [])
      ).catch(() => []);

      const rows = [...derived];
      if (!kind) {
        const doc = await beerCalibration(env);
        if (doc) rows.push(doc);
      }
      if (!rows.length) {
        return empty(ctx, { note: kind ? `no taste summary called '${kind}'` : "no taste summaries are published here" });
      }
      // These are documents, not row sets: one is several KB and clipping it
      // mid table would drop exactly the calibration it exists to convey.
      // Return one at a time rather than clipping both.
      if (!kind && rows.length > 1) {
        return {
          ...cap(rows.map((r) => ({ kind: r.kind, chars: r.text.length })), ctx),
          note: "ask for one by kind — these are documents, and clipping them loses the point",
        };
      }
      return single(rows[0]);
    },
  },

  places: {
    class: "authored", domain: "table", kind: "entity",
    reads: ["t1_visits"],
    description:
      "Restaurants the owner has visited, with their own notes, the rating they gave and the date of the visit. Best-rated first on their own 0-10 scale, which runs high — read `taste_summary(kind:'dining')` before calling an 8 praise; every row carries its `scale`. `order:'recent'` is where they have been eating lately. Filter by `city` or `cuisine`, or `topic` for a restaurant by name or by what they wrote about it. Restaurants only; not venues, trips or events.",
    schema: {
      type: "object",
      properties: {
        city: { type: "string", description: "One city, by name." },
        cuisine: { type: "string", description: "One cuisine, by name, as the owner filed it." },
        topic: { type: "string", description: "Match against the restaurant name or the owner's notes." },
        order: { type: "string", description: "rated (default, highest first) | recent" },
      },
    },
    async run(env, { city, cuisine, topic, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      // The rating is TEXT \u2014 it comes off a CSV column and lands in D1 as one \u2014
      // so `ORDER BY rating DESC` was a lexical sort: '9.5' above '9' above
      // '8.5' above '10'. Every perfect score sat near the bottom of a list
      // sold as best-first, and `ratings(medium:'restaurants')`, which does cast,
      // disagreed with this tool about the same meal.
      const score = "CAST(nullif(rating,'') AS REAL)";
      const by = ordering(order, {
        rated:  `${score} DESC, created DESC`,
        recent: `created DESC, ${score} DESC`,
      });
      const rows = await q(
        env,
        // The visit date was in the row and never returned, so an assistant
        // could not tell a place they loved last month from one they loved in
        // 2019 \u2014 which is most of what "where should we eat" turns on.
        // `rating` is CAST on the way out too, so the same meal reads as the
        // same number here and on `ratings(medium:'restaurants')`, and the
        // scale rides with it as it does on every judgement row.
        `SELECT restaurant, city, neighborhood, cuisine_1, ${score} AS rating, '0-10' AS scale,
                substr(created,1,10) AS visited, notes
         FROM t1_visits
         WHERE (? IS NULL OR lower(city) = lower(?))
           AND (? IS NULL OR lower(cuisine_1) = lower(?))
           AND (? IS NULL OR lower(restaurant) LIKE ? OR lower(COALESCE(notes,'')) LIKE ?)
         ORDER BY ${by.sql}, id LIMIT ? OFFSET ?`,
        city ?? null, city ?? null, cuisine ?? null, cuisine ?? null, like, like, like,
        probe(ctx), skip(ctx)
      );
      const [held] = await q(
        env,
        `SELECT count(*) AS n FROM t1_visits
         WHERE (? IS NULL OR lower(city) = lower(?))
           AND (? IS NULL OR lower(cuisine_1) = lower(?))
           AND (? IS NULL OR lower(restaurant) LIKE ? OR lower(COALESCE(notes,'')) LIKE ?)`,
        city ?? null, city ?? null, cuisine ?? null, cuisine ?? null, like, like, like
      );
      return ordered({
        ...cap(rows, ctx),
        scope: `${held?.n ?? 0} visits${city || cuisine || topic ? " matched" : ""}`,
      }, by);
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
    reads: ["t1_project", "t1_project_commit"],
    description:
      "The owner's repos — what each claims to be, how hot it is, and what they have actually been working on. `status` is heat, not judgement: active, warm, stalled, dormant by time since the last commit, and dormant includes everything that simply shipped. A repo filed under a group like a hiatus folder was set aside deliberately. With no arguments, the status counts plus the repos with the most commits in the last 90 days — volume, not last-commit date, because recency floats a one-commit import above a year of work. Prose and metadata only; no source code is in this store. `project_activity` has the dated commits, `project_docs` the READMEs and decision records, `project_open` what is left unfinished.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against name, description or language." },
        status: { type: "string", description: "active | warm | stalled | dormant" },
        group: { type: "string", description: "The folder it is filed under, e.g. 'hiatus'." },
        order: { type: "string", description: "worked (default: commits in the last 90 days) | recent (last commit) | biggest (all-time commits)" },
      },
    },
    async run(env, { topic, status, group, order }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const since = new Date(Date.now() - 90 * 86400000).toISOString().slice(0, 10);

      // Bound, not interpolated. `since` is derived from the clock rather than
      // from the caller, so this is not injectable today — but it was the only
      // value in this file spliced into SQL as text, and nothing a caller sends
      // ever reaches SQL as text. The placeholder is first in both statements
      // below because SQLite binds by position in the SQL text and this
      // subquery sits in the FROM clause, ahead of every filter.
      const RECENT = `(SELECT repo, count(*) AS n FROM t1_project_commit
                       WHERE substr(committed_at,1,10) >= ? GROUP BY repo)`;

      if (!topic && !status && !group) {
        const bands = await q(env,
          `SELECT status, count(*) AS n FROM t1_project GROUP BY status ORDER BY n DESC`);
        const hot = await q(env,
          `SELECT p.name, p.slug, p.status, c.n AS commits_90d, p.description
           FROM t1_project p JOIN ${RECENT} c ON c.repo = p.slug
           ORDER BY c.n DESC, p.slug LIMIT ? OFFSET ?`,
          since, probe(ctx, 8), skip(ctx));
        return {
          ...cap(hot, ctx),
          statuses: bands,
          note: "no filter given — these are the status bands and the repos with the most commits in the last 90 days; call again with topic, status or group",
        };
      }

      // Named `worked` because that is what the schema advertises. It was the
      // fall-through of a chain that accepted anything and silently meant
      // commits-in-90d, so order='newest' \u2014 a reasonable guess, and not a name
      // this tool has \u2014 came back ranked by heat with nothing saying so.
      const by = ordering(order, {
        worked:  "coalesce(c.n, 0) DESC, p.last_commit DESC",
        recent:  "p.last_commit DESC",
        biggest: "p.commit_count DESC",
      });

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
         ORDER BY ${by.sql}, p.id LIMIT ? OFFSET ?`,
        since,
        like, like, like, like, status ?? null, status ?? null,
        group ?? null, group ?? null, probe(ctx), skip(ctx)
      );
      return ordered(cap(rows, ctx), by);
    },
  },

  project_activity: {
    class: "revealed", domain: "workshop", kind: "event",
    reads: ["t1_project_commit"],
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
    async run(env, { repo, from, to }, ctx) {
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
           GROUP BY c.repo ORDER BY commits DESC, c.repo LIMIT ? OFFSET ?`,
          start, end, probe(ctx), skip(ctx)
        );
        return {
          ...cap(rows, ctx),
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
         ORDER BY c.committed_at DESC, c.id LIMIT ? OFFSET ?`,
        repo, like, start, end, probe(ctx), skip(ctx)
      );
      return { ...cap(rows, ctx), window: { from: start, to: end } };
    },
  },

  project_docs: {
    class: "authored", domain: "workshop", kind: "text",
    reads: ["t1_project_doc"],
    description:
      "The prose the owner's repos carry: READMEs, CONTEXT glossaries, architecture decision records, plan documents. Where a project states what it is for and why it was built that way — an ADR is the owner arguing with themselves and recording who won. `topic` finds which project already settled a question; `repo` reads what one says about itself; `kind` narrows to one document type. Returns excerpts around the match, each with the `id` to read it by; `full:true` returns the first matching document whole, or `id` returns that exact one, clipped to the byte budget either way. No source code is in this store.",
    schema: {
      type: "object",
      properties: {
        topic: { type: "string", description: "Match against document title or body." },
        repo: { type: "string", description: "Repo name or slug." },
        kind: { type: "string", description: "readme | context | adr | plan | claude | doc" },
        id: { type: "string", description: "A document's `id` as an excerpt listing returned it (its repo and path). Reads that one document whole." },
        full: { type: "boolean", description: "Return one whole document rather than excerpts — the first match, or the one `id` names." },
      },
    },
    async run(env, { topic, repo, kind, id, full }, ctx) {
      const like = topic ? `%${topic.toLowerCase()}%` : null;
      const rlike = repo ? `%${repo.toLowerCase()}%` : null;
      const docId = (r) => `${r.repo}/${r.path}`;

      if (full || id) {
        // One document whole. Excerpting an ADR is how you end up quoting the
        // option that was rejected: the decision is at the bottom, the
        // alternatives are in the middle, and a keyword hit lands anywhere.
        //
        // Matched on the id as one string rather than split on a slash: a
        // repo slug can itself carry one (a group folder), so the join is the
        // only safe way to take it apart.
        const rows = id
          ? await q(env, `SELECT repo, path, kind, title, body, chars FROM t1_project_doc
                          WHERE repo || '/' || path = ? LIMIT 1`, String(id))
          : await q(
              env,
              `SELECT repo, path, kind, title, body, chars
               FROM t1_project_doc
               WHERE (? IS NULL OR lower(title) LIKE ? OR lower(body) LIKE ?)
                 AND (? IS NULL OR lower(repo) = lower(?) OR lower(repo) LIKE ?)
                 AND (? IS NULL OR kind = ?)
               ORDER BY CASE kind WHEN 'context' THEN 0 WHEN 'readme' THEN 1
                                  WHEN 'adr' THEN 2 ELSE 3 END, repo, path, id
               LIMIT 1`,
              like, like, like, rlike ? repo : null, repo ?? null, rlike, kind ?? null, kind ?? null
            );
        if (!rows.length) {
          return empty(ctx, {
            note: id
              ? `no document with id '${id}' — ids come from this tool's own excerpt listing`
              : "no document matched",
          });
        }
        const d = { id: docId(rows[0]), ...rows[0] };
        // Budgeted against its own envelope, the way readOne does, rather than
        // handed to cap() unclipped — which withheld any document over the
        // byte budget entirely and told the caller the tool had a bug.
        const envelope = JSON.stringify({ ...d, body: "", note: "" }).length;
        const budget = MAX_BYTES - envelope - 200;
        const body = d.body ?? "";
        const clipped = body.length > budget;
        return single(
          { ...d, body: clipped ? body.slice(0, budget) : body },
          clipped
            ? { note: `body clipped to ${budget} of ${body.length} chars — one answer holds this much of the document` }
            : {}
        );
      }

      const WHERE = `WHERE (? IS NULL OR lower(title) LIKE ? OR lower(body) LIKE ?)
           AND (? IS NULL OR lower(repo) = lower(?) OR lower(repo) LIKE ?)
           AND (? IS NULL OR kind = ?)`;
      const binds = [like, like, like, rlike ? repo : null, repo ?? null, rlike, kind ?? null, kind ?? null];
      const rows = await q(
        env,
        `SELECT repo, path, kind, title, body, chars
         FROM t1_project_doc
         ${WHERE}
         ORDER BY CASE kind WHEN 'context' THEN 0 WHEN 'readme' THEN 1
                            WHEN 'adr' THEN 2 ELSE 3 END, repo, path, id
         LIMIT ? OFFSET ?`,
        ...binds, probe(ctx), skip(ctx)
      );
      const [held] = await q(env, `SELECT count(*) AS n FROM t1_project_doc ${WHERE}`, ...binds);

      // Excerpt around the hit rather than shipping bodies. A CONTEXT.md can
      // be most of the byte cap on its own, and the answer would otherwise be
      // spent on one file.
      const out = rows.map((r) => {
        const body = r.body ?? "";
        const at = like ? body.toLowerCase().indexOf(topic.toLowerCase()) : 0;
        const from = Math.max(0, (at < 0 ? 0 : at) - 200);
        const excerpt = body.slice(from, from + 700).replace(/\s+/g, " ").trim();
        return {
          id: docId(r),
          repo: r.repo, path: r.path, kind: r.kind, title: r.title,
          chars: r.chars,
          excerpt: (from > 0 ? "…" : "") + excerpt + (from + 700 < body.length ? "…" : ""),
        };
      });
      const capped = cap(out, ctx);
      return {
        ...capped,
        scope: `${held?.n ?? 0} documents${topic || repo || kind ? " matched" : ""}`,
        note: [capped.note, "excerpts — pass a row's id with full:true to read that document whole"]
          .filter(Boolean).join(" · "),
      };
    },
  },

  project_open: {
    class: "intent", domain: "workshop", kind: "pointer",
    reads: ["t1_project_open"],
    description:
      "What is visibly unfinished in those repos: TODO and FIXME markers left in code, unchecked items in plan documents, and files sitting uncommitted. Useful for 'what could I pick up' and for reading intent — a marker is a note written to oneself at the moment of choosing not to do something. Treat it as a trail, not a backlog: nobody prunes these, so an old marker may name work that was done another way. With no repo it returns where the unfinished work is concentrated. Paths and marker text only, never file contents.",
    schema: {
      type: "object",
      properties: {
        repo: { type: "string", description: "Repo name or slug." },
        kind: { type: "string", description: "marker | unchecked | uncommitted" },
      },
    },
    async run(env, { repo, kind }, ctx) {
      if (!repo) {
        const rows = await q(
          env,
          `SELECT o.repo, count(*) AS open_items,
                  sum(CASE WHEN o.kind = 'marker' THEN 1 ELSE 0 END) AS markers,
                  sum(CASE WHEN o.kind = 'unchecked' THEN 1 ELSE 0 END) AS unchecked,
                  sum(CASE WHEN o.kind = 'uncommitted' THEN 1 ELSE 0 END) AS uncommitted
           FROM t1_project_open o
           WHERE (? IS NULL OR o.kind = ?)
           GROUP BY o.repo ORDER BY open_items DESC, o.repo LIMIT ? OFFSET ?`,
          kind ?? null, kind ?? null, probe(ctx), skip(ctx)
        );
        return { ...cap(rows, ctx), note: "where unfinished work sits — ask again with a repo for the items" };
      }

      const like = `%${repo.toLowerCase()}%`;
      const rows = await q(
        env,
        `SELECT repo, kind, path, line, text
         FROM t1_project_open
         WHERE (lower(repo) = lower(?) OR lower(repo) LIKE ?)
           AND (? IS NULL OR kind = ?)
         ORDER BY CASE kind WHEN 'unchecked' THEN 0 WHEN 'marker' THEN 1 ELSE 2 END, path, line, id
         LIMIT ? OFFSET ?`,
        repo, like, kind ?? null, kind ?? null, probe(ctx), skip(ctx)
      );
      return cap(rows, ctx);
    },
  },
};
