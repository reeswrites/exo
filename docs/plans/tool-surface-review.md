# Tool surface review — after ADR-0028

Scope: every entry in `TOOLS` (`worker/src/tools.js:512-2958`, 32 tools), the
synthesised page parameters (`worker/src/index.js:377-403`), the exposure stamp
(`worker/src/index.js:458`), the brief (`exo/scripts_impl/brief.py`), the
dependency map (`exo/toolzones.py`), the offer resolver (`exo/surface.py`) and
the three skills in `skills/`. Read against ADR-0013, 0015, 0020, 0022, 0023,
0028 and CONTEXT.md.

Conventions below: `tools.js:N` means `worker/src/tools.js:N`. "Base envelope"
means `rows`, `returned_count`, `offset`, `has_more`, optional `note`, all from
`cap()` (`tools.js:88-131`), plus `exposure` stamped by `index.js:458` on every
answer. The table lists only what a tool adds to or omits from that base.

---

## 1. Inventory

| tool (def) | answers | params | reads (`reads`) | envelope beyond base | pages | order / scope |
|---|---|---|---|---|---|---|
| `whats_relevant` `tools.js:513` | semantic hits across atoms, notes, posts | `topic`* | t2_atom, t1_notes, t1_post | rows `{kind,text,score,url?(post),id?(note)}` | yes (search k=probe+skip, `:524`) | none / none |
| `notes_on` `:543` | note titles by meaning, or one note whole | `topic`, `id`, `full` | t1_notes | list rows `{id,title,score}`; single `{id,title,folder,created,source,uuid,body,match_score?}` + `peer?` (`:578`); single path skips `returned_count/has_more` | list yes; single no | none / none |
| `open_threads` `:588` | unclosed self-questions | — | t1_open_thread | rows `{question}` (`state` selected, dropped `:602`) | yes | none / none |
| `verdicts` `:606` | authored opinions across media, no date | `kind` | t1_verdicts | rows `{subject,kind,rating,note}`; `order:"rated"` hardcoded `:629` | yes | fixed axis, stamped anyway / none |
| `taste` `:633` | artists by plays off scrobbles | `artist`, `since`, `until`, `order`, `with_mentions` | t0_music (+t2_affinity via `readsFor`) | rows `{artist,plays,first_played,last_played,mentions?}`; `scope`; empty path sets `returned_count/has_more` `:696` | yes | played/recent/oldest / yes |
| `agenda` `:727` | live items on the spine | `family`, `include_done` | t1_item | rows `{family,title,status,streak?,cadence?,due?,pile?,est_minutes?}`; `shape` (a scope by another name `:775`); standing `note` | yes | none / `shape` |
| `history` `:784` | item status log | `title` | t1_item, t1_item_event | rows `{item,event,from?,to?,when_,ref_kind?}` | yes | none / none |
| `recipes` `:818` | recipes written up; one whole | `topic`, `full` | t1_recipe | list rows `{title,cuisine,time_min,effort,yield_servings,source_url,published}`; `order:"recent"` hardcoded `:866`; full: `LIMIT 1`, no offset `:848` | list yes; full no | fixed axis, stamped / none |
| `drafts` `:869` | longform in progress; one whole | `topic`, `id`(=slug), `stale_days`, `full` | t1_draft | list rows `{title,slug,description,started,modified,words,state,days_since_touched}` (no `id` key); single adds `body`; empty path `{rows:[],note}` only `:918` | list yes; single no | none (modified DESC only) / none |
| `medium` `:930` | one medium's profile: consumed, rated, owned, written | `name` | 10 zones `:932-943` | directory rows `{medium,unit,total}` via cap; named path returns `{rows:[rec],note}` **without** `returned_count/offset/has_more` `:1121` | directory yes; named no (single) | none / none |
| `consumption` `:1125` | counts + last_logged per medium | `medium` | t0_beer/book/film/music/tv (`readsFor`) | rows `{medium,total,last_logged,total_label?,episodes_watched?,distinct_beers?,repeat_count?,to_read_backlog?}` | ≤5 rows, offset applied `:1191` | none / none |
| `posts` `:1195` | blog posts by meaning; one whole | `topic`, `id`(=slug), `kind`, `full` | t1_post | list rows `{slug,title,url,published,kind,words,description,score}`; single via readOne `{id,title,url,published,kind,words,body,match_score?}`; three empty paths return `{rows:[],note}` only `:1224,1227,1250` | list yes (over-fetch `:1226`, bounded to 60 candidates when `kind` set) | none / none |
| `events` `:1255` | upcoming events, series collapsed, round-robin by feed | `topic`, `from`, `to`, `free` | t0_event | rows `{title,start,venue,location,free,url,feed,occurrences?,venues?,price_varies?}` | yes, but over a pool pre-cut to 4 per feed `:1334` | none / none |
| `releases` `:1346` | new records, heard/owned removed | `topic`, `scene`, `since`, `include_heard`, `order` | t0_release, t0_music, t1_collection | rows `{artist,title,release_date,url,label,scenes,mb_status,plays,owned?,scenes_hit?}`; `order`; `note` always (POOL_GAP + removed count) | yes, over a pool pre-cut to 3 per scene when unfiltered `:1383` | recent/unfamiliar/familiar/spread / none (removed count in note) |
| `criticism` `:1460` | press pieces | `topic`, `outlet`, `since`, `order` | t0_criticism | rows `{outlet,byline,title,published,url,tags,summary(320),blurb_chars?}`; `order` | yes, pool pre-cut to 3 per outlet when unfiltered `:1490` | recent/oldest / none |
| `taste_profile` `:1545` | stated preferences | — | t1_taste | rows `{kind,key,value}` | yes | none / none |
| `around_the_time` `:1560` | notes/artists/films/books/tv in a window | `from`*, `to`* | t1_notes, t0_music/book/film/tv | rows `{kind,label,when_?,plays?,rating?,scale?}`; `scope`; `note` | offset walks a ≤25-row interleave (8+5+4+4+4, `:1578-1622`); `limit` cannot widen it; `has_more` is false once 25 are shown | none / yes |
| `ratings` `:1679` | rated items per medium | `medium`, `min_rating`, `order` | t0_anime/beer/book/film, t1_visits (`readsFor`) | rows `{medium,scale,label,rating,url?,when_,+beer meta}`; `order`; `note` (beer) | yes; unfiltered = 5 per medium regardless of `limit` `:1745`, `offset` applied per medium | rated/recent / **none** |
| `facets` `:1762` | beer ratings rolled up by family/style/brewery/venue/beer | `medium`, `by`, `min_n`, `order` | t0_beer | rows `{medium,<by>,n,rated,mean,best,last}`; `scale`; `order`; `note` (coverage) | yes | played/rated/recent / none (coverage in note) |
| `reviews` `:1896` | Letterboxd reviews | `topic`, `min_rating`, `order` | t1_film_review | rows `{title,year,rating,review,url,watched}` — no `scale` key | yes | recent/rated / none |
| `collection` `:1935` | owned things | `kind`, `topic`, `order` | t1_collection, t0_music | rows (nulls dropped) `{kind,title,creator,genre,form,acquired,url,thoughts,cost,retailer,plays}`; `genres`; `order`; `note` | yes | recent/oldest/played / none |
| `watching` `:2038` | shows started, not finished | `status`, `declared`, `stalled_since`, `order` | t0_tv, t0_anime | rows `{title,status,declared?,declared_on?,episodes_watched,episodes_total,no_episode_total?,score?,scale?,last_watched,days_since?,seasons_touched?,url?,mal_url?}`; `scope`; `order`; long `note` | yes | oldest/recent/rated / yes |
| `saves` `:2252` | bookmarks | `topic`, `platform`, `collection`, `kind`, `tag`, `since`, `with_note` | t0_raindrop | rows `{title,url,kind,platform,collection,tags,note,saved}`; `scope`; **`notes`** (plural key `:2332`); no-arg path `{rows:[],total,span,platforms,collections,note}` without `returned_count` | yes | none (created DESC only) / yes (whole zone, not the filtered set) |
| `backlog` `:2338` | queued and not done | `kind`, `topic`, `since`, `order` | t0_book, t0_raindrop (`readsFor`) | shelf rows `{title,author,shelf,queued,avg_rating}`; collection rows `{title,url,platform,collection,note,queued}`; `scope`; `notes`; `gap`; no-kind path `{rows:[],kinds,note,gap}`; unknown kind `{rows:[],error,kinds,gap}` `:2481` | yes | recent/oldest / yes |
| `recent_topics` `:2488` | conversation titles + volume | `topic`, `min_turns` | t0_chat_topic | rows `{title,last_seen,started,turns,his_turns,gist}` — no id | yes | none (last_seen DESC) / none |
| `thread` `:2528` | one conversation | `topic`*, `include` | t0_chat, t0_chat_topic | rows `[{title,when,turns,his_turns,landed?,summary?,summary_is?,his_words?,dialogue?,dialogue_note?,note?}]` | no (single; turns `LIMIT 400/200` `:2586,2610`, `offset` ignored but echoed) | none / none |
| `taste_summary` `:2623` | calibration documents | `kind` | t0_taste_derived, t0_beer (`readsFor`) | rows `{kind,text}` or directory `{kind,chars}` | no (documents) | none / none |
| `places` `:2684` | restaurant visits | `city`, `cuisine`, `order` | t1_visits | rows `{restaurant,city,neighborhood,cuisine_1,rating(TEXT),visited,notes}` — no `scale`, no `topic` | yes | rated/recent / none |
| `projects` `:2735` | repos, heat, shape of the workshop | `topic`, `status`, `group`, `order` | t1_project, t1_project_commit | no-arg `{rows(≤8 own cap :2769),statuses,note}` unstamped; filtered rows `{name,slug,filed_under,github,status,description,languages,commit_count,commits_90d,last_commit,doc_count}`; `order` | yes (no-arg capped at 8) | worked/recent/biggest / `statuses` ≈ scope |
| `project_activity` `:2807` | commit subjects, or per-repo totals | `repo`, `from`, `to` | t1_project_commit | totals rows `{repo,commits,first,last}` or `{repo,date,subject}`; `window` | yes | none / `window` |
| `project_docs` `:2856` | README/CONTEXT/ADR excerpts; one whole | `topic`, `repo`, `kind`, `full` | t1_project_doc | excerpt rows `{repo,path,kind,title,chars,excerpt}`; full `{repo,path,kind,title,body,chars}`, `LIMIT 1 OFFSET 0` `:2885` | excerpts yes; full no | none / none |
| `project_open` `:2916` | TODO/unchecked/uncommitted | `repo`, `kind` | t1_project_open | per-repo `{repo,open_items,markers,unchecked,uncommitted}` or `{repo,kind,path,line,text}` | yes | none / none |

`*` = required. `limit`/`offset` are appended to **every** schema by
`index.js:377-403`, including `medium`, `thread`, `taste_summary` and the
`full`/`id` paths, where they do nothing.

---

## 2. Consistency findings

Ranked by how far a calling model would be led astray.

### 2.1 Descriptions that contradict the SQL or the schema

1. **`releases` says the opposite of what it does.** "with what they have NOT
   already heard removed" (`tools.js:1350`); the SQL removes what *has* been
   scrobbled or owned (`:1415`). A model reading the sentence literally
   believes the pool is the familiar half.
2. **`thread.include` schema omits `dialogue`.** Description documents four
   values including `'dialogue'` (`:2532`); the schema says `conclusion | turns
   | both` (`:2537`). A schema-driven client never offers the mode the
   description recommends.
3. **`project_docs` promises a `title` parameter that does not exist.** "ask
   with full=true and a title to read one whole document" (`:2860`); schema has
   `topic/repo/kind/full` (`:2862-2868`), and `full` returns whichever row sorts
   first by kind (`:2881-2893`), not a named one. Also `full` hands the body to
   `cap()` unclipped, so a document over 16KB is **withheld entirely** with the
   "did not budget for its own envelope" note (`:124`) — `readOne`'s budget
   (`:469-480`) is not used here.
4. **`whats_relevant` says notes and atoms; it searches posts too.** Description
   `:517` names "their notes and the verbatim spans"; `search()` is called with
   no `kind` (`:524`) and post hits come back with a URL (`:535`). `reads`
   (`:515`) and `toolzones.py:82-84` both know about posts.
5. **`taste.with_mentions` says it "returns fewer rows"** (`:652`). ROW_CAP is
   gone (ADR-0028 §1); the grade no longer sizes anything. Stale in the one
   place ADR-0028 said the grade must not be read as a size.
6. **`consumption.medium` schema lists four media** (`:1139`); the SQL answers
   five, including `tv` (`:1150`). An unknown medium returns `rows: []` with no
   note (`:1153-1156`), so `medium:'film'` (singular, as `medium` the tool
   spells it) reads as "no films".
7. **`around_the_time` says "these rows are the head of each"** and `has_more`
   says false once the 25-row interleave is exhausted (`:1670`), while `scope`
   says the window held hundreds. Paging exists on the surface and this tool
   cannot page into its own window; nothing tells the caller that `limit` is a
   no-op above 25.
8. **`ratings` unfiltered ignores `limit`** — five per medium (`:1745`) whatever
   the caller asks — and applies `offset` **per medium**, so `offset: 5` skips
   five films *and* five books. Nothing in the description or the page-param
   text (`index.js:389-402`) says so.
9. **`events`, `releases`, `criticism` page over a pre-cut pool.** Round-robin
   `rn <= 4` (`:1334`), `rn <= 3` (`:1430`, `:1515`) runs *before* `LIMIT ?
   OFFSET ?`, so an unfiltered walk with `offset` ends at 4×feeds or 3×scenes
   and `has_more` goes false while most of the pool was never reachable. This
   was an honest design under ADR-0007; under ADR-0028 a caller told it can
   walk the corpus cannot.
10. **`thread` tells the caller to "ask about a narrower part"** (`:2598`,
    `:2616`) and offers no parameter that narrows a thread. `notes_on` clip
    note does the same (`:567`) — there, `id` + no range means the advice is
    "read it again and it will be the same".
11. **`places.rating` is returned as TEXT** (`:2713`, uncast in SELECT) while
    `ratings(medium:'restaurants')` returns REAL (`:1738`). Same meal, `"9.5"`
    on one tool and `9.5` on the other; `places` carries no `scale` key though
    it is `kind: judgement`. `reviews` likewise returns `rating` with no
    `scale` (`:1921`).
12. **`drafts.id` is a slug, and the listing returns `slug`, not `id`**
    (`:894`, `:912`). `notes_on` returns `id`; `posts` returns `slug` and takes
    `id`; `readOne` stamps `id` onto the single row for notes and posts
    (`:475`) but `drafts` does not. Three tools, three shapes of "the key you
    hand back".

### 2.2 Descriptions that still speak the capped surface

- `taste` `:644`: "a truncated answer is the TOP of that list".
- `ratings` `:1690`: "so a truncated answer is the top of the list, not a sample".
- `facets` `:1766`: "`ratings` returns a page of 1,906 rows and so cannot
  answer 'which styles do they rate highest'" — garbled, and `ratings` can now
  page.
- `taste` `:652`: "returns fewer rows" (above).
- `cap()` note `:126-127` "or ask a narrower question" is fine — it names the
  page first. The tool-local notes at `:567`, `:2598`, `:2616`, `:2911` lead
  with "narrower" and offer no page.

### 2.3 One concept, several parameter names

| concept | names in use | where |
|---|---|---|
| the medium | `medium` (plural: films/books/restaurants) · `name` (singular: film/book/restaurant) · `kind` (plural: books/films/tv/music) | `ratings:1694`, `consumption:1139`, `facets:1771` · `medium:949` · `verdicts:613` |
| window start | `since` · `from` · `stalled_since` (which is an *until*) · `stale_days` | `taste:649`, `releases:1356`, `criticism:1470`, `saves:2265`, `backlog:2357` · `events:1264`, `around_the_time:1568`, `project_activity:2816` · `watching:2056` · `drafts:879` |
| window end | `until` · `to` · (absent) | `taste:650` · `events:1265`, `around_the_time:1569`, `project_activity:2817` · every other `since` tool |
| free-text match | `topic` = vector search · `topic` = `LIKE` substring | `whats_relevant`, `notes_on`, `posts` · 13 other tools. Same word, two mechanisms, and the param text ("A topic, question, or phrase" vs "Match against …") is the only tell. A sentence works on the first group and fails on the second. |
| "one whole" | `full` · `include` | `notes_on`, `posts`, `recipes`, `drafts`, `project_docs` · `thread:2537` |
| booleans | `include_done`, `include_heard` · `with_mentions`, `with_note` | `agenda:736`, `releases:1357` · `taste:652`, `saves:2266` |
| a floor | `min_rating`, `min_n`, `min_turns` | consistent prefix; fine |
| `kind` | post type · verdict medium · collection object type · backlog pile · save link type · summary document · doc type · open-item type | `posts:1205`, `verdicts:613`, `collection:1943`, `backlog:2355`, `saves:2263`, `taste_summary:2637`, `project_docs:2866`, `project_open:2925`. Eight vocabularies under one name. |
| filed-under | `group` in, `filed_under` out | `projects:2745` vs `:2789` |
| `collection` | a tool (owned things) · a param on `saves` (a raindrop bucket) | `:1935` vs `:2262` |
| `thread` | a conversation (`thread`) · an open question (`open_threads`) | `:2528` vs `:588` |

### 2.4 Order vocabulary

ADR-0022 fixes one vocabulary (recent/oldest/rated/played, `tools.js:146-149`).
Off-vocabulary axes: `releases` `unfamiliar | familiar | spread` (`:1363`,
`familiar` ≈ `played`), `projects` `worked | biggest` (`:2746`, `worked` ≈
`played`, `biggest` ≈ count). `verdicts` (`:629`) and `recipes` (`:866`) stamp
`order` with a single fixed axis, which ADR-0022 says not to do ("a tool with
one axis returns no `order` at all"). Tools whose rows carry two measured facts
and offer no `order`: `recent_topics` (last_seen, turns `:2520`), `saves`
(created only; ADR-0022 names it a recency tool and it has no `oldest`),
`drafts` (modified; `stale_days` is an oldest-proxy), `agenda`, `history`,
`project_activity`, `events` (`start` only — fine), `open_threads`.

### 2.5 Envelope fields present on some tools and absent on others

- **`scope`** (ADR-0023): on `taste`, `around_the_time`, `watching`, `saves`,
  `backlog`. Absent on `ratings` — the tool ADR-0022 opened with, drawn from a
  population per medium the caller never sees — and on `collection`,
  `reviews`, `verdicts`, `places`, `recent_topics`, `criticism`, `releases`,
  `facets` (population is in prose `:1875`), `events`. `agenda.shape`
  (`:775`), `projects.statuses` (`:2772`), `project_activity.window` (`:2837`)
  are scopes under other names. `saves.scope` counts the whole zone, not the
  filtered set (`:2321`, `:2330`) — "searched 2,188 saves" after
  `platform:'youtube'` is the wrong denominator.
- **`returned_count`/`has_more`/`offset` on empty answers**: set explicitly by
  `taste:696`, `releases:1447`, `criticism:1533`, `around_the_time:1637`,
  `collection:2005`; omitted by `notes_on:580`, `posts:1224/1227/1250`,
  `recipes:851`, `drafts:899/918`, `medium:1121` (non-empty!), `facets:1815`,
  `taste_summary:2647/2669/2675`, `thread:2560`, `project_docs:2892`,
  `saves:2286` (no-arg), `backlog:2396/2479`. A client keyed on `has_more`
  reads `undefined` on half the surface.
- **`note` vs `notes`**: `saves:2332` and `backlog:2440/2473` use `notes` for a
  second caveat, everywhere else it is `note`; `places` rows carry `notes`
  meaning the owner's visit notes (`:2714`), and `saves` rows carry `note`
  meaning the owner's bookmark note (`:2299`) — the same word for an envelope
  caveat and a row payload, swapped between two tools.
- **`score` vs `match_score`**: list hits carry `score` (`:534`, `:584`,
  `:1247`); the `readOne` single row carries `match_score` (`:477`).
- **`gap`** only on `backlog` (`:2405`); the same fact is a `note` on `medium`
  (`:1107`) and `watching` (`:2225`). **`error`** only on `backlog:2481`; every
  other bad argument is a `note`. **`kinds`**, **`genres`**, **`statuses`**,
  **`platforms`/`collections`**, **`shape`**, **`window`**, **`peer`**,
  **`scale`** (envelope-level on `facets:1890`, row-level on `ratings:1747`,
  absent on `reviews`/`places`) — each a one-off.
- **`url`**: rows carry it on `posts`, `whats_relevant` (posts only), `reviews`,
  `collection`, `saves`, `backlog(make/buy)`, `events`, `releases`,
  `criticism`, `watching` (`url`+`mal_url`), `ratings` (films, anime), `medium`
  top-3; `projects` returns `github` (`:2789`) rather than `url`;
  `recipes` returns `source_url`; `notes_on` carries none (correct) but
  `posts` list rows carry `url` while `whats_relevant` note hits carry `id` and
  post hits `url` — two keys for "how to get the rest" on one row shape.
- **Date keys**: `when_` (`history:812`, `ratings:1739`, `around_the_time`),
  `when` (`thread:2564`), then `watched`, `visited`, `saved`, `queued`,
  `published`, `date`, `start`, `last_seen`, `last_watched`, `last_played`,
  `acquired`, `modified`, `created`. Per-tool names are the house style and
  defensible; `when_` is a reserved-word workaround leaking to the wire, and
  `when` beside it is the same field spelled twice.

### 2.6 Instance facts in engine strings (ADR-0014) — and they drift

`worker/test/run.mjs:1002` asserts the *brief* no longer claims "720 films";
`ratings` still does (`tools.js:1690`: "720 films, 409 books, 1,906 beers and 93
restaurants", "`verdicts` has only 10"). Also: `reviews:1900` "115 of them",
`collection:1939` "89 vinyl … 7 fragrances", `saves:2256` "2,188 … nine years"
plus named collections ("Gift Ideas, Tattoo Inspiration"), `backlog:2351` "436
… 41", `thread:2532` "45% of them are under 80 chars", `events:1259` "DC",
"eight sources", `watching:2042` "abandoned in August 2023" and the literal
`declared_on: "2023-08-31"` at `:2211`, `taste_summary:2634` "median of 8.1"
(and `brief.py:481`), `POOL_GAP:230` "seven Bandcamp label rosters … rage and
digicore" directly under a comment (`:225-228`) saying how many scenes is
"deliberately not stated", `backlog:2375-2382` hardcoded shelf and collection
names, `agenda:731` "Kairos". `skills/README.md` rule 4 ("No counts. A row
count is an instance fact") applies to skills and not, so far, to the tool
table. `criticism:1464` uses "his"/"he" where every other description says
"the owner"/"they"; `recent_topics` returns `his_turns` (`:2504`) while
`thread` labels speaker lines with `env.OWNER_LABEL` (`:2590`).

### 2.7 Naming style

Nouns: `posts`, `drafts`, `recipes`, `places`, `events`, `releases`,
`reviews`, `verdicts`, `saves`, `facets`, `collection`, `agenda`, `history`,
`backlog`, `medium`, `thread`, `projects`, `criticism`, `taste`,
`taste_profile`, `taste_summary`, `consumption`, `watching` (a gerund).
Questions/phrases: `whats_relevant`, `notes_on`, `around_the_time`,
`open_threads`, `recent_topics`. Prefixed families: `project_*`, `taste*`.
Two styles, no rule; a caller guessing a name has no pattern to guess from.

### 2.8 Defaults

Windows: `project_activity` defaults to 30 days (`:2821`), `projects` heat is
90 days (`:2751`), `events` defaults `from` to today (`:1291`),
`around_the_time` requires both bounds, `taste` is a lifetime. Sort defaults
follow ADR-0022 and are stated per tool — fine. Directory-on-empty: `saves`,
`backlog`, `medium`, `projects`, `project_activity`, `project_open`,
`taste_summary` return a directory when unfiltered; `ratings`, `collection`,
`taste`, `criticism`, `releases`, `events`, `places` return a page. Both are
reasonable; the description is the only place a caller learns which, and
`ratings` (`:1690`) does not say it returns five per medium.

### 2.9 Page params advertised where they do nothing

`index.js:377-403` appends `limit`/`offset` to every schema. On `medium`
(named), `thread`, `taste_summary`, and the `full`/`id` paths of `notes_on`,
`posts`, `drafts`, `recipes`, `project_docs` they are inert; `thread` and
`taste_summary` still echo `offset` because `cap()` reports whatever it was
given (`:92`, `:116`) without slicing.

---

## 3. Merge candidates

### 3.1 `ratings` / `reviews` / `verdicts` / `criticism`
- **Classes differ**: `ratings` revealed, `reviews`+`verdicts` authored,
  `criticism` world (`:1680`, `:1897`, `:607`, `:1461`). ADR-0015 §1 says the
  class is what stops a false statement; one tool over three classes would
  need a per-row `class` key, which nothing else on the surface carries.
- **`reviews` + `verdicts`** are the real pair: both authored text with a rating
  and a `topic`/`min_rating` filter; `verdicts` has no date (`:616-622`) and
  `reviews` has one. Merge → `verdicts(medium?, topic?, min_rating?, order)`
  with rows carrying `source` (`letterboxd` / `verdicts file`) and `watched?`.
  Gained: one answer to "what did they write about X"; the film half stops
  being invisible to a caller who found `verdicts` first. Lost: `verdicts`'
  honest "no `recent` axis" becomes a per-source caveat; `reads` becomes two
  zones and the grade is the tighter of the two unless `readsFor` splits on
  `medium`. Changes: `toolzones.py:96-97`, `TOOL_DOMAINS`, README table,
  `recommend-media` Needs (`skills/recommend-media/SKILL.md` "verdicts,
  reviews" row) and its closing paragraph, `trace-an-idea` provenance list,
  `brief.py:333-337` ("written film reviews … plus longer verdicts"),
  `run.mjs` (`TOOLS.reviews` ×5, `TOOLS.verdicts` ×3). **Recommend: merge**,
  behind an alias for `reviews` (see §4 on ADR-0020).
- **`ratings`, `criticism`: keep.**

### 3.2 `taste` / `taste_profile` / `taste_summary`
Three classes (revealed / authored / derived), which ADR-0015 §Context used as
the motivating example of why class matters. **Keep three; rename two** (§4).
A merge would put "what they play" and "what a machine says about their
scale" under one name and hand the caller the misquote ADR-0015 predicts.

### 3.3 `projects` / `project_activity` / `project_docs` / `project_open`
Four classes again (possession / revealed / authored / intent, `:2736`,
`:2808`, `:2857`, `:2917`). The prefix already groups them and
`pick-up-a-project` hardcodes all four in its Needs table and its ordered
walk. **Keep**; harmonise instead: `repo` on all four (it is), `kind` on
`project_docs`/`project_open` are different vocabularies (fine, both are
"kind of row"), `from/to` on `project_activity` should also be offered on
`project_open`? no — but `project_docs` needs an `id` (= `path`) and a clipped
`full` (§2.1 item 3).

### 3.4 `recent_topics` / `thread`
List + item split across two tools, while `notes_on`, `posts`, `drafts`,
`recipes`, `project_docs` fold list and item into one tool with `full`/`id`.
ADR-0028 §3 says `id` lookup belongs on "whatever else is built on `readOne`
later"; `thread` is not built on it and `recent_topics` returns no key
(`:2504`). Merge → `conversations(topic?, id?, min_turns?, include?, order?)`:
list rows gain `id` (title or `origin_ref`); `id` returns one thread with
`include` as now. Gained: one pattern for list+item across the mind domain,
an id path, `order: recent | longest`. Lost: `thread` is a good name and
`recent_topics` a bad one, so the rename is a gain. Changes:
`toolzones.py:91-94`, `trace-an-idea` (Needs row, sweep steps 7, the
"two notes on the conversation surfaces" paragraph, the `class: dialogue`
bullet), `brief.py:421-431` names no tool (fine), `run.mjs` (`TOOLS.thread`
×11, `TOOLS.recent_topics` ×7). **Recommend: merge**, alias both old names.

### 3.5 `notes_on` / `whats_relevant`
`notes_on(topic)` list mode is literally `search(topic, kind:'note')`
(`:583`) and `whats_relevant` is `search(topic)` with no kind (`:524`) — the
same call, one filter apart, returning `{id,title,score}` vs
`{kind,text,score,id}`. Gained by folding: one semantic entry point
`whats_relevant(topic, kind?: note|post|atom)` and `notes_on`/`posts` reduced
to readers. Lost: `notes_on` titles-first is the map `trace-an-idea` step 2
leans on, and the two tools are graded differently (atoms are derived).
**Recommend: keep both; add `kind` to `whats_relevant` now** (additive) and
say in both descriptions which is which. Revisit the fold when `thread` and
`project_docs` are on `readOne`.

### 3.6 `consumption` / `medium`
`medium(name)` returns every number `consumption(medium)` returns, from
duplicated SQL (`:1157-1189` vs `:1018-1054`), plus ratings, ownership and
what was written. `consumption`'s one distinct answer — all media in one call
with `last_logged` — is `medium`'s directory (`:991-1003`) minus `last_logged`
(`countOf` already fetches it, `:986`, and the directory drops it, `:995`).
Gained by merging: one medium vocabulary (today `film` vs `films`), one set of
numbers that cannot disagree, one tool fewer. Lost: `consumption`'s
`readsFor` grade per medium (the directory already grades on the union).
Changes: `toolzones.py:139-140`, `TOOL_DOMAINS`, README, `recommend-media`
Needs row and step 1 (`consumption(medium)` → `medium()`), the two notes that
say "check last_logged from consumption" (`:699`, `:1638`), `run.mjs`
`TOOLS.consumption` ×5. **Recommend: merge** — put `last_logged` (and the
tv/beer/book extras) on the directory rows, alias `consumption` to
`medium` with no name.

### 3.7 `ratings` / `facets`
Same class and kind; `facets` is `ratings` grouped. A `group_by` on `ratings`
would be the tidy shape, but the row is a different thing (a rollup with `n`,
`mean`, `best`) and ADR-0015 §3 says the row shape is what predicts the
failure. **Keep; rename** (§4).

### 3.8 `saves` / `backlog(make|buy)`
`backlog`'s two raindrop piles are `saves(collection:)` over instance-named
collections hardcoded in the engine (`:2379-2382`). Not a merge question — an
ADR-0014 question: those names belong in `exo.toml`, and until they do
`backlog` is an instance tool wearing engine clothes. **Keep; move the names
to config.**

---

## 4. Rename candidates

ADR-0020 makes a name a contract in four places: `[tools] disable` and
procedure `needs.exo` (`exo/surface.py:99-103`, `exo/scripts_impl/publish.py:123-135`),
`TOOL_ZONES`/`TOOL_DOMAINS` (`exo/toolzones.py:78,170`), the README table
(`run.mjs:1804`) and the skills (every table in `skills/*/SKILL.md`). A renamed
tool with no alias turns a served procedure into a build failure and a stale
client into `unknown tool`. **Add an `aliases: []` field on the tool def**,
resolved in `tools/call` (`worker/src/index.js:407-409`) and accepted by `surface.resolve`,
before any rename ships; drop aliases one release later.

| now | proposed | why |
|---|---|---|
| `whats_relevant` | `search` (or `relevant`) | the only semantic entry; a model asked "search their notes" guesses `search`. Hardcoded: `trace-an-idea` (5×), `pick-up-a-project`, ADR-0013 prose, `toolzones.py:82` |
| `medium` | `medium_profile` | the tool shares its name with the parameter three siblings take; `medium(name:'film')` beside `ratings(medium:'films')` is the worst pair on the surface. Hardcoded: brief `:397-400`, `run.mjs` SHOULD_ADVERTISE `:1829`, `recommend-media`, notes at `:1117`, `:1103` |
| `facets` | `rating_breakdown` (or `ratings_by`) | "facets" is this codebase's word for ADR-0015 metadata; a caller sees `class/domain/kind` called facets in the README and a beer rollup called `facets` on the wire. Hardcoded: brief `:316`, `medium` note `:1103-1104`, `recommend-media` |
| `saves` | `bookmarks` | reads as a verb; `class: intent` is clearer as the thing it is. Hardcoded: brief `:434`, `backlog` note `:2473`, `trace-an-idea`, `recommend-media`, `exo-me/procedures/README.md:25` (`needs.exo: [saves, verdicts]`) |
| `around_the_time` | `period` (or `in_window`) | a model with a date range guesses `timeline`/`period`, not a phrase. Hardcoded: brief `:402`, SHOULD_ADVERTISE, `trace-an-idea` |
| `open_threads` | `open_questions` | `thread` on the same surface means a conversation; the rows are questions (`:597`). Hardcoded: `trace-an-idea`, `pick-up-a-project`, brief `:338` (prose "open threads") |
| `recent_topics` (+`thread`) | `conversations` | see §3.4 |
| `taste_profile` | `preferences` (or `stated_preferences`) | "profile" reads as a summary; the rows are declared likes and constraints (`brief.py:134-138` reads `kind='constraint'` from the same zone). Hardcoded: `events:1259` description, `BEER` text `:390`, brief `:465` (prose) |
| `taste_summary` | `calibration` (or `scales`) | it is not a summary of taste; it is how to read a number. Every pointer to it says "before reading a number" (`:303`, `:1098`, `:2688`, brief `:473`). Hardcoded: those four, `recommend-media` |
| `history` | `commitment_log` (or `item_history`) | bare `history` could be any zone. Hardcoded: brief `:372` |
| `places` | `restaurants` | it reads `t1_visits` only (`:2686`); "places" invites venue/city questions the zone cannot answer. Hardcoded: brief `:362` (prose) |
| `collection` | `owned` (or `shelf`) | collides with `saves.collection`; `class: possession` is the name. Hardcoded: `recommend-media`, brief `:410` (prose) |
| `watching` | `unfinished_shows` | gerund, and the tool is about what is *not* finished (`:2042`). Hardcoded: brief `:331`, `run.mjs:1729`, `medium` note `:1117`, `recommend-media` |
| `project_open` | `project_todos` | "open" beside `open_threads` and `open_items` means three things. Hardcoded: `pick-up-a-project` |
| `criticism` | `press` | the owner's own criticism is `reviews`/`verdicts`; this is somebody else's (`:1464`). Hardcoded: `POOL_GAP:230`, brief `:457` (prose), both media skills |
| `releases` | `new_releases` | fine as is; only if `press` lands, for symmetry |

Keep as they are: `posts`, `drafts`, `recipes`, `events`, `verdicts`,
`ratings`, `taste`, `agenda`, `backlog`, `projects`, `project_activity`,
`project_docs`, `notes_on`, `thread` (if it survives §3.4).

---

## 5. Description rewrites

Copy-ready. House voice: what question, what comes back, what is not here,
which sibling for the rest. No instance counts — `scope` carries numbers, and
`run.mjs:1002` already treats a count in prose as a bug. Where the schema is
wrong, the corrected param text follows the description. Tools not listed
(`agenda`, `history`, `project_activity`, `project_open`) are sound as they
stand; `watching` needs one edit, given last.

**`whats_relevant`** (`:517`)
> What has the owner written that bears on a topic? One semantic search over three corpora at once: quotable spans lifted from their notes, the notes themselves, and their published posts. Each hit says which it is — a post hit carries its live URL, a note hit carries the `id` that `notes_on` reads by. The selection is a machine's; the text inside a span is theirs. Use this first for their thinking on something; use `notes_on` or `posts` to read a whole document once you have its key. Does not search conversations, drafts or repo documents — those match on words, not meaning, through `recent_topics`, `drafts` and `project_docs`.

Schema: add `kind: { type: "string", description: "note | post | atom — search one corpus instead of all three." }`.

**`notes_on`** (`:547`)
> The owner's private notes on a topic, found by meaning. Returns titles by default — a map of what exists, each with the `id` to read it by — and one whole note with `full:true` (the best match) or `id` (that exact note, no search). Notes are draft thinking, not finished positions; `posts` holds what was argued into shape and published, and `whats_relevant` searches both at once. A note also live in a peer system is said so on the row.

Param `topic`: "What the note is about — a phrase, a question, a claim. Matched by meaning, so a sentence works better than a keyword."

**`open_threads`** (`:592`)
> Questions the owner has asked themselves in writing and not closed — the best single source of what they are currently chewing on. Newest first. Each row is an unfinished question, not a position: do not read one as something they concluded. Takes no filter; the whole list pages. For conversations rather than written questions, see `recent_topics`.

**`verdicts`** (`:610`)
> The owner's written opinions on books, films, television and music — their own words, with the reasoning, and the rating they gave. Highest-rated first, and that is the only axis: this zone carries no date, so it cannot say what they judged lately. For dated film writing see `reviews`; for the numbers without prose see `ratings`; for what the press thinks see `criticism`.

Param `kind`: "books | films | tv | music — one medium, or all."

**`taste`** (`:644`)
> What the owner actually listens to, straight off the scrobble stream — revealed preference, as distinct from what they say they like (`taste_profile`). One row per artist with plays and first/last play dates. The tail is long and quiet: every artist ever scrobbled is counted and most were played a handful of times, so a page is the head of a list whose size `scope` states — never read a short page as a narrow taste. `artist` asks whether one act is in the record at all; `since`/`until` make "lately" a different question from "ever"; `order:'recent'` is what they have been reaching for, which is not the all-time list. `with_mentions` adds how many of their notes name each artist, which reads the notes and grades the answer accordingly.

Param `with_mentions`: "Also count how many of the owner's notes name each artist. Reads their notes, so the answer is graded as the notes are."

**`recipes`** (`:822`)
> Recipes the owner wrote up and published, with the post each came from. Small and real — what they cooked and posted, not a database. Newest publication first; undated seed templates sort last. `full:true` returns ingredients and steps for the best match. For what they eat out, see `places`.

**`drafts`** (`:873`)
> Longform pieces the owner is in the middle of writing — between a private note and a published post. With no arguments, everything open, most recently touched first, each with its word count and days since touched. `stale_days` finds what has gone cold, which is the question a writer cannot answer about themselves. `full:true` returns the whole text of the best match; `id` is a draft's `slug` as a listing returned it. A draft absent here was never started or is already published (`posts`); it is not evidence the idea does not exist in `notes_on`.

Param `id`: "A draft's `slug`, exactly as the listing returned it. Returns that draft whole."

**`medium`** (`:945`)
> Everything about one medium in a single call: how much of it the owner has consumed and how current that record is, how they rate it on its own scale with the top few, what they own of it, and how much they have written about it. With no `name`, a directory of the media this record holds with a count for each. One row, not a list — `ratings`, `collection`, `verdicts` and `consumption` page the same facts separately. A medium not in the directory is not held here, which says nothing about whether the owner consumes it.

Param `name`: "film | book | tv | anime | music | beer | restaurant. Singular. Omit for the directory."

**`consumption`** (`:1136`) — if kept (§3.6)
> How much of each medium the owner has consumed and how current each record is — totals and `last_logged` per medium, never titles. Read `last_logged` before trusting any answer about "lately": the exports lag by months and a stale source reads as someone who stopped. `medium` returns the same numbers for one medium with its ratings and ownership beside them.

Param `medium`: "music | books | films | tv | beer. Plural. Omit for all."

**`posts`** (`:1199`)
> The owner's published blog — essays, articles, lists, project write-ups — found by meaning, as opposed to the private notes behind them. Each hit carries its live URL, its `slug`, and a one-line description. `full:true` returns the whole text of the best match; `id` is a slug from a listing. These are public and finished; the same idea may exist earlier and rougher in `notes_on` or `drafts`. `kind` narrows to one post type after the search.

**`events`** (`:1259`)
> Upcoming events the owner could go to — a pool merged from several local feeds, soonest first, from today unless `from` says otherwise. Recurring programmes are collapsed to their next date with an occurrence count, and no single feed may fill the answer, so a page is a spread across sources rather than a slice of one. `topic` matches title, venue or description; `free` keeps only free ones; `from`/`to` bound the window and a past window returns the past. This is a candidate pool, not a recommendation: pair with `taste_profile` for the venues and organisers they say they rate.

**`releases`** (`:1350`)
> Records that came out recently in the scenes the owner follows, with anything they have already scrobbled or own removed — the count removed is stated. The pool is crawled by scene, not by similarity to what they play, so it can surface an artist with no listeners yet; a record absent from it was never looked at, not rejected. Each row carries the measured facts — lifetime plays of that artist, whether anything by them is owned, how many scenes surfaced it — and no judgement. `order:'unfamiliar'` is the discovery axis; `'familiar'` is new work by artists already in rotation; `'spread'` is what the most scenes agree on. `include_heard:true` keeps the removed ones.

**`criticism`** (`:1464`)
> What the music press is publishing — headline, byline, date, the outlet's own blurb and the link — from the outlets this record follows. Somebody else's writing, never the owner's: attribute it to the outlet. Use it for what is being said about a scene or an artist now, which nothing in the owner's own record can answer. No outlet may fill an unfiltered page; a `topic` or `outlet` search returns every match. Pair with `taste` for whether they already play what is being written about.

**`taste_profile`** (`:1549`)
> What the owner says they like and will not have — stated preferences and standing constraints, as key/value rows grouped by kind: venues and organisers they rate, things they seek out, things they avoid, allergies and rules. Distinct from `taste`, which is what they measurably play; where the two disagree is usually the interesting part. Constraints are not preferences to weigh — a suggestion that breaks one is not worth making.

Schema: add `kind: { type: "string", description: "One kind of preference, e.g. constraint, dining, outing, event. Omit for all." }` (column exists, `:1552`).

**`around_the_time`** (`:1564`)
> What was going on in a window of dates: what the owner wrote, played, watched and read between `from` and `to`. A period, not a topic — the lens for "what was I thinking about in March". Rows interleave the media so a heavy month in one cannot crowd out the others, and `scope` counts what the window actually held; the rows are the head of each medium, and `limit` cannot widen them past the interleave. For one medium in depth over the same window, use that medium's own tool with `since`/`until`.

**`ratings`** (`:1690`)
> What the owner rated and how highly, one medium at a time, from the services that recorded it. Behaviour rather than prose: the numbers behind `verdicts` and `reviews`, and far more of them. Every row carries its `scale` because the media disagree — never compare a number from one medium with a number from another, and read `taste_summary` before calling a dining or beer score high. Highest-rated first by default, so a page is the top of that medium; `order:'recent'` is what they rated lately, a different list. With no `medium`, a few rows from each medium as a sampler, and `limit` does not apply. `min_rating` is on that medium's own scale.

Param `medium`: "films | books | beer | restaurants | anime. Plural. Omit for a sampler across all."

**`facets`** (`:1766`)
> The owner's ratings for one medium rolled up by a property of the thing rated — for beer: style family, full style, brewery, venue, or the beer itself. One row per group with how many, how many were rated, the mean, the best and the most recent. A rollup answers "which styles do they rate highest" where a page of individual ratings cannot. Ordered by how often they reached for the group, because a mean over two check-ins is not a preference; `order:'rated'` puts the mean first and says so. `min_n:2` with `by:'beer'` is the list of beers they went back to. Covers beer only; other media carry no facet columns.

**`reviews`** (`:1900`)
> The owner's written film reviews, in their own words, each with its rating, its watch date and a link. The larger part of their film criticism; `verdicts` holds the cross-media opinions. `topic` matches the title or the review text; `min_rating` is on the 0-5 scale; newest watch first, or `order:'rated'` for the films they thought most of. Their sentences, not a summary of them.

**`collection`** (`:1939`)
> What the owner owns, which is not what they consumed — vinyl, DVDs, board games, fragrances, whatever the inventory holds, with what they paid and where. Buying and keeping something is a stronger signal than playing it once. Most recently acquired first; `order:'oldest'` reaches what has been on the shelf longest; `order:'played'` ranks vinyl by scrobbles, since owning a record and wearing it out are different claims. `genre` is a few coarse buckets the owner typed into their own inventory, returned as `genres` on every answer — a word outside them cannot match, so a miss is a gap in the vocabulary, not in the shelf.

**`saves`** (`:2256`)
> Links the owner bookmarked. A save is attention, not consumption: it caught their eye, and nothing here says they finished it or agreed with it. Filter by `platform`, by `collection` (their own buckets), by `kind` of link, by `tag`, by `topic` (title, tags or collection), by `since`, or to those they wrote a note on. With no filter, the axes — which platforms and collections exist and how many — rather than an arbitrary page. Newest first. For what they own see `collection`; for what they queued on purpose see `backlog`.

**`backlog`** (`:2351`)
> What the owner queued and has not done — a deliberate act of shelving or filing, which separates it from `saves` (attention) and `collection` (already owned). Four piles: `read` (shelved to-read), `resume` (books started and set down), `make` (things to build or cook), `buy` (gift and shopping ideas). With no `kind`, the size of each pile. Newest first; `order:'oldest'` digs up what has been sitting, which is usually the question. Nobody prunes these, so an old row is a decision made once, not a live intention. Every answer names the pile this record cannot see.

**`recent_topics`** (`:2492`)
> What the owner has been working through in conversation — titles, dates and turn counts, with a clipped machine-written gist, never transcripts. Fresher than their notes, which lag a deliberate act of capture. Turn count is the signal a title cannot carry: a long thread is a preoccupation, a short one a glance. `topic` matches the title, the gist or where the thread landed, so a short word works better than a sentence; `min_turns` alone lists the long ones. Read one thread with `thread`. Half of every thread is another model's; only the owner's turns are theirs.

**`thread`** (`:2532`)
> One conversation, matched by title first and by gist or landing second. `include` chooses what comes back: `conclusion` (where the owner landed, verbatim, beside the machine distillation, which is marked as such), `turns` (only what the owner typed), `dialogue` (both sides interleaved and speaker-tagged), or `both` (default: conclusion plus the owner's turns). Prefer `dialogue` when the owner's turns read as fragments — they are often questions, and the answer is the half you cannot otherwise see. Lines tagged as the assistant are another model's output: context for reading the owner, never their words. Long threads are clipped to the byte budget and the answer says how many turns were shown.

Schema `include`: "conclusion | turns | dialogue | both (default)".

**`taste_summary`** (`:2634`)
> How to read the owner's rating scales — calibration documents, one per kind, to be read before calling any number high or low. `dining` and `clusters` are documents mirrored from the taste engine; `beer` is computed here from the check-in log and is always current: mean, median, deciles and the count at every step the record uses. With no `kind`, the list of documents and their sizes. Only the kinds listed exist; film, book and anime scales have no calibration and should be reported flat with their scale.

**`places`** (`:2688`)
> Restaurants the owner has visited, with their own notes, the rating they gave and the date of the visit. Best-rated first on their own 0-10 scale, which runs high — read `taste_summary(kind:'dining')` before calling an 8 praise. `order:'recent'` is where they have been eating lately. Filter by `city` or `cuisine`, or `topic` for a restaurant by name. Restaurants only; not venues, trips or events.

Schema: add `topic: { type: "string", description: "Match against the restaurant name or the owner's notes." }` and return `rating` cast (`:2713`) with a `scale` key, as `ratings` does.

**`projects`** (`:2739`)
> The owner's repos — what each claims to be, how hot it is, and what they have actually been working on. `status` is heat, not judgement: active, warm, stalled, dormant by time since the last commit, and dormant includes everything that simply shipped. A repo filed under a group like a hiatus folder was set aside deliberately. With no arguments, the status counts plus the repos with the most commits in the last 90 days — volume, not last-commit date, because recency floats a one-commit import above a year of work. Prose and metadata only; no source code is in this store. `project_activity` has the dated commits, `project_docs` the READMEs and decision records, `project_open` what is left unfinished.

Param `order`: "worked (default: commits in the last 90 days) | recent (last commit) | biggest (all-time commits)".

**`project_docs`** (`:2860`)
> The prose the owner's repos carry: READMEs, CONTEXT glossaries, architecture decision records, plan documents. Where a project states what it is for and why it was built that way — an ADR is the owner arguing with themselves and recording who won. `topic` finds which project already settled a question; `repo` reads what one says about itself; `kind` narrows to one document type. Returns excerpts around the match; `full:true` returns the first matching document whole, clipped to the byte budget, so narrow with `repo` and `kind` first. No source code is in this store.

(And fix `full` to clip through the `readOne` budget rather than `cap()` — §2.1 item 3.)

**`watching`** (`:2042`) — one edit
Replace "the list was abandoned in August 2023, so the word they last filed against a title is returned as `declared` and clearly dated" with "the list stopped being kept, so the word they last filed against a title is returned as `declared` with the date the list froze". The date itself should come from the zone (`max(last_updated)` on `t0_anime`) rather than the literal at `:2211`.

---

## 6. Gaps

Questions the brief or CONTEXT promises, or a served zone can answer, that no
tool answers now that paging and `id` exist. Five recommended.

1. **List notes without a topic — by folder, by date.** `t1_notes` carries
   `folder`, `created`, `note_type`, `voice`, `facet` (`fixtures/instance/zones/_serve/cf/schema.sql:32`),
   with an index on `folder`; `notes_on` refuses without a topic (`:580`).
   "What did I write last week" and "what is in the reading folder" are
   unanswerable except through `around_the_time`'s eight-note head. *Param on
   existing*: make `notes_on.topic` optional and add `folder`, `since`,
   `until`, `order: recent | oldest`; no topic → a dated listing with `id`s.
   Zones: t1_notes. Safe and additive.
2. **A rating or review for one named title.** `ratings` has no text filter
   (`:1691-1697`), `places` has none, and `recommend-media` documents the hole
   ("film: nothing … neither takes a title"). *Param on existing*: `topic` on
   `ratings` matched against the label (and `year` where the zone has it), and
   on `places` (§5). Zones: t0_film/book/beer/anime, t1_visits. Closest today:
   `reviews(topic)`, `taste(artist)`.
3. **What changed since a date, across zones.** `around_the_time` is the only
   cross-zone window and it has fixed per-branch heads, no `limit` effect, no
   notes/posts/drafts/commits/saves, and requires `to`. *Param on existing*:
   make `to` default to today, honour `limit` by scaling the per-branch share
   (`ceil(limit / branches)`), add `kinds` to choose branches, and add posts,
   drafts, commits and saves as branches (`t1_post.published`,
   `t1_draft.modified`, `t1_project_commit.committed_at`,
   `t0_raindrop.created`). Zones as listed. Closest today: `around_the_time`,
   `project_activity`.
4. **`id` on `thread` and `project_docs`** (ADR-0028 §3 "whatever else is
   built on `readOne` later"). `recent_topics` returns no key and `thread`
   re-matches by `LIKE` (`:2554`), so a listing cannot be followed exactly;
   `project_docs` excerpts return `path` and `full` cannot ask for it
   (`:2885`). *Param on existing*: `id` on both (title or `origin_ref` for
   threads; `repo/path` for docs), listings carry it. Zones: t0_chat_topic,
   t0_chat, t1_project_doc. If §3.4 merges, this lands there.
5. **One search over notes, posts and conversations.** `whats_relevant` covers
   notes, atoms and posts by meaning; conversations, drafts and repo documents
   are `LIKE` only. The `kind` param (§5) is the cheap half. The other half —
   embedding `t0_chat_topic` summaries and `t1_draft` bodies — is publish-side
   (`exo/scripts_impl/publish_cf.py`) and its own decision. *New capability,
   not a new tool*: extend `whats_relevant` when the vectors exist.

Noted, not recommended now: `t0_meal_event` / `t0_meal_rating` are in the
served schema (`fixtures/instance/zones/_serve/cf/schema.sql:12,14`) and no tool reads them — "what did I cook
and how did it go" has no answer; closest is `recipes`. `taste_profile` has
no `kind` filter though the brief filters that zone by kind (`brief.py:134`).
`open_threads` has no `topic`. `collection` has no `id`/`url` for a single
row beyond what the listing shows — fine, rows are small.

---

## 7. Recommended order of work

**Safe now — no contract change, no owner decision.**
1. Fix the three outright contradictions: `releases` inversion (`:1350`),
   `thread.include` enum (`:2537`), `project_docs` description and `full`
   clipping (`:2860`, `:2893`). Add `tv` to `consumption`'s param text.
2. Apply §5 descriptions; strip instance counts, "truncated", "fewer rows",
   "his". Add the `run.mjs:1002` style check to tool descriptions: no digits
   followed by a medium noun.
3. Envelope consistency: every empty path returns `returned_count: 0,
   has_more: false, offset`; `medium` named path goes through `cap()`;
   `notes` → fold into `note` (or rename the row payloads on `places`/`saves`
   to `owner_note`); `score` → `match_score` on list hits, or the reverse; drop
   the hardcoded `order` on `verdicts` and `recipes` per ADR-0022; add
   `scope` to `ratings`, `collection`, `reviews`, `places`, `recent_topics`,
   `criticism`, `releases`, `facets`, `events`; make `saves.scope` count the
   filtered set.
4. Additive params: `kind` on `whats_relevant`; `topic` on `ratings`/`places`;
   `until` beside every `since`; `order: oldest` on `saves`, `recent_topics`,
   `drafts`; `kind` on `taste_profile`; `folder`/dates on `notes_on`; `id` on
   `thread` and `project_docs`; accept both singular and plural medium names on
   `medium`, `consumption`, `ratings`, `facets`, `verdicts` (alias map, one
   place).
5. Paging honesty: `around_the_time` scales branch heads to `limit` and sets
   `has_more` from `scope`; `ratings` unfiltered honours `limit` as a
   per-medium share and applies `offset` once; `events`/`releases`/`criticism`
   lift the round-robin cut when `offset > 0` or state it in `scope`; a
   `pages: false` flag on `medium`, `thread`, `taste_summary` so
   `index.js:377` stops advertising `limit`/`offset` there; `thread` gains a
   turn range (`from_turn`) so its "narrower part" note names a real move.
6. Move instance strings out of the engine: `backlog` shelf/collection names,
   `declared_on`, POOL_GAP's roster count, "DC"/"eight sources" — to
   `exo.toml` or to the zone.

**Contract changes — the owner's decision, behind aliases.**
7. Add `aliases` to the tool def and resolver (§4 preamble).
8. Merges, in order of payoff: `consumption` → `medium` directory (§3.6);
   `recent_topics` + `thread` → `conversations` (§3.4); `reviews` →
   `verdicts` (§3.1).
9. Renames (§4), starting with the collisions: `medium` → `medium_profile`,
   `facets` → `rating_breakdown`, `open_threads` → `open_questions`,
   `collection` → `owned`; then the guessability set (`whats_relevant` →
   `search`, `saves` → `bookmarks`, `around_the_time` → `period`,
   `taste_summary` → `calibration`, `taste_profile` → `preferences`).
10. Order vocabulary: fold `familiar`/`worked` into `played`, `biggest` into a
    new shared `count` axis, keep `unfamiliar`/`spread` as documented
    exceptions in `ordering()`'s comment (`:144-149`).
11. Update in step with 8-9: `toolzones.py`, README table (generated),
    `brief.py` tool mentions (`:316,331,332,372,386,397-404,434,473,497`),
    `run.mjs` SHOULD_ADVERTISE (`:1829`), the three skills' Needs tables and
    walks, `exo-me/procedures` `needs.exo` lists.
