"""The invariants that must never regress: provenance, the wall, regen."""
from __future__ import annotations

import pyarrow.parquet as pq
import pytest

from exo import api, config, t2
from exo.provenance import ENVELOPE, Row, stable_id


def test_stable_id_is_deterministic_and_order_sensitive():
    assert stable_id("lastfm", "a", "b") == stable_id("lastfm", "a", "b")
    assert stable_id("lastfm", "ab", "c") != stable_id("lastfm", "a", "bc")


def test_envelope_leads_every_row():
    r = Row(tier="t0", zone="music", source="lastfm", author="external",
            payload={"artist": "Bladee"})
    flat = r.flat()
    for col in ENVELOPE:
        assert col in flat
    assert flat["grounds"] is True and flat["regenerable"] is False


def test_payload_never_clobbers_provenance():
    """A payload key colliding with an envelope column must not overwrite it."""
    r = Row(tier="t0", zone="book", source="goodreads", author="external",
            payload={"author": "Eoin Colfer", "title": "Artemis Fowl"})
    flat = r.flat()
    assert flat["author"] == "external"      # provenance intact
    assert flat["p_author"] == "Eoin Colfer"  # payload preserved, renamed


def test_t2_emit_is_walled_off_by_its_own_envelope():
    r = t2.emit("affinity", {"artist": "Aries", "score": 1})
    assert r.tier == "t2" and r.author == "machine"
    assert r.grounds is False and r.regenerable is True


@pytest.mark.record
def test_source_profile_cannot_see_t2_views():
    """The wall: derivation profile registers only t0_/t1_ views."""
    src_views = api.views("source")
    assert src_views, "expected some source views (run `wh rebuild` first)"
    assert not any(v.startswith("t2_") for v in src_views)
    assert any(v.startswith("t0_") for v in src_views)


def test_read_source_raises_on_t2():
    with pytest.raises(Exception):
        api.read_source("SELECT * FROM t2_affinity")


def test_source_profile_filters_grounds_false(tmp_path):
    """The wall's second half: a grounds=false row in a tier zone is invisible to
    the source profile (derivation) but visible to full (display)."""
    import duckdb
    import pyarrow as pa
    from exo import catalog
    p = tmp_path / "z.parquet"
    pq.write_table(pa.table({"id": ["a", "b"], "grounds": [True, False], "x": [1, 2]}), p)
    con = duckdb.connect(":memory:")
    catalog._create_view(con, "t1_full", p, grounds_only=False)
    catalog._create_view(con, "t1_src", p, grounds_only=True)
    assert con.execute("SELECT count(*) FROM t1_full").fetchone()[0] == 2   # display sees all
    assert con.execute("SELECT count(*) FROM t1_src").fetchone()[0] == 1    # wall drops grounds=false
    assert con.execute("SELECT id FROM t1_src").fetchone()[0] == "a"


@pytest.mark.record
def test_full_profile_sees_all_tiers():
    full = api.views("full")
    assert any(v.startswith("t2_") for v in full)


@pytest.mark.record
def test_chat_zone_is_grounds_false_and_walled():
    """t0_chat is a real tier zone but grounds=false: display sees it, the wall doesn't
    (ADR-0029 made mechanical by the grounds filter). Skips if not yet indexed."""
    from exo.api import read
    if "t0_chat" not in api.views("full"):
        pytest.skip("t0_chat not indexed (run `wh chatindex`)")
    n_full = read("SELECT count(*) FROM t0_chat")[0][0]
    assert n_full > 0
    assert read("SELECT count(*) FROM t0_chat WHERE grounds")[0][0] == 0  # every row grounds=false
    assert api.read_source("SELECT count(*) FROM t0_chat")[0][0] == 0     # invisible to derivation


# The anti-collapse proof (ADR-0001) deliberately does NOT live here. It lived
# here as `test_t2_regenerates_identically`, which called `t2.run_all()` twice —
# two full derivations, embeddings included, on every `pytest tests/`. It also
# derived from scratch both times, ignoring the T2 already on disk, which is the
# exact waste `rebuild.verify()`'s docstring records fixing; the test never got
# the fix.
#
# What it proves is a property of the DERIVATION CODE, not of tonight's data, so
# it belongs on a code-change trigger rather than in the suite you run after
# editing a loader. `wh verify` is that proof, and .github/workflows/verify.yml
# runs it on any push touching t2/embed/loaders/rebuild plus weekly. Everything
# left in this file is cheap and reads what is already written.


def test_atomize_cut_is_verbatim_and_boundary_aware():
    """Spans are byte-slices of the body; an authorship flip splits his words
    from a pasted quote so no atom is half-and-half."""
    body = "his own thought here\n\nanother of his lines\n> a pasted quote line"
    blocks = t2._cut_blocks(body)
    assert "his own thought here" in blocks
    assert "another of his lines" in blocks and "> a pasted quote line" in blocks
    # the flip split the second paragraph in two
    assert "another of his lines\n> a pasted quote line" not in blocks


def test_atomize_voice_is_marker_based():
    assert t2._voice("plain sentence of mine") == config.OWNER_VOICE
    assert t2._voice("> a saved quote\n> second quoted line") == "quoted"


def test_atomize_facet_folder_prior_and_first_person():
    # journaling folder biases about-me when there's any first-person presence
    assert t2._facet("i sat with this feeling for a while today", "Journaling") == "about-me"
    # a journaling span with ~no first-person flips to about-world
    assert t2._facet("the market moved today on rate news", "Journaling") == "about-world"
    # heavy first-person flips a world folder to about-me
    assert t2._facet("i feel like i keep circling my own ideas here", "Notes") == "about-me"
    assert t2._facet("the theory of relativity concerns spacetime", "Notes") == "about-world"


def test_atomize_id_is_deterministic_content_hash():
    assert t2._hash("span", "note.md") == t2._hash("span", "note.md")
    assert t2._hash("span", "a.md") != t2._hash("span", "b.md")


@pytest.mark.record
def test_atoms_are_grounded_machine_and_from_raw():
    rows = t2.atomize()
    assert rows, "expected atoms from t1_notes (run `wh rebuild` first)"
    assert all(r.tier == "t2" and r.author == "machine" and r.grounds is False for r in rows)
    # atomize skips refined/: no atom's origin note is a refined note
    assert not any(str(r.origin_ref).startswith("refined/") for r in rows)


def test_embed_is_deterministic_and_right_shape():
    from exo import embed
    a = embed.embed_texts(["a stable sentence"])
    b = embed.embed_texts(["a stable sentence"])
    assert len(a[0]) == embed.DIMS == 384
    assert a[0] == b[0]  # content-addressed cache ⇒ identical


@pytest.mark.record
def test_every_atom_has_exactly_one_vector():
    from exo.api import read
    n_atom = read("SELECT count(*) FROM t2_atom")[0][0]
    n_vec = read("SELECT count(*) FROM t2_atom_vec")[0][0]
    joined = read("SELECT count(*) FROM t2_atom a JOIN t2_atom_vec v USING (id)")[0][0]
    assert n_atom == n_vec == joined and n_atom > 0  # 1:1 by id, no orphans


@pytest.mark.record
def test_nearest_returns_similar_atoms():
    from exo.api import read
    from exo.ask import nearest
    aid = read("SELECT id FROM t2_atom LIMIT 1")[0][0]
    hits = nearest(aid, k=5)
    assert len(hits) == 5
    assert all(h["id"] != aid for h in hits)               # excludes self
    sims = [h["sim"] for h in hits]
    assert sims == sorted(sims, reverse=True)              # ranked
    assert all(-1.01 <= s <= 1.01 for s in sims)


def test_notes_ingester_renders_t1_index_shape():
    """The Apple Notes ingester renders the frontmatter t1_index reads (type: raw, uuid,
    created, folder, title) and collapses U+2028 in a title so the file stays YAML-parseable."""
    import yaml
    from exo import ingest_notes
    from exo.applenotes.extract import NoteRecord
    n = NoteRecord(
        id=1, title="alpha\u2028beta", uuid="ABC-123", pinned=False,
        created="2022-03-23T00:00:00Z", modified="2022-03-23T00:00:00Z", folder="Projects",
        body="plain body", structured_body="# structured\n\nbody text",
    )
    out = ingest_notes._render(n, "2026-08-09")
    fm = yaml.safe_load(out.split("---")[1])  # must parse — the U+2028 collapse is the point
    assert fm["type"] == "raw" and fm["uuid"] == "ABC-123" and str(fm["created"]) == "2022-03-23"
    assert fm["folder"] == "Projects"
    assert fm["title"] == "alpha beta"  # U+2028 collapsed to a space, single line
    assert out.endswith("body text\n")  # structured_body preferred, trailing newline


def test_notes_ingester_new_path_is_deterministic():
    from exo import ingest_notes
    from exo.applenotes.extract import NoteRecord
    from pathlib import Path
    mk = lambda u, t: NoteRecord(id=1, title=t, uuid=u, pinned=False, created="2022-03-23",
                                 modified="2022-03-23", folder="F", body="", structured_body="x")
    taken = set()
    p1 = ingest_notes._new_path(Path("/out"), mk("U1", "same title"), taken)
    p2 = ingest_notes._new_path(Path("/out"), mk("U2", "same title"), taken)  # collision
    assert p1.name == "2022-03-23-same-title.md"
    assert p2.name == "2022-03-23-same-title-u2.md"  # disambiguated by uuid tail


def test_every_t2_row_declares_not_grounds():
    from exo import config
    import glob
    for p in glob.glob(str(config.T2 / "*.parquet")):
        table = pq.read_table(p)
        if table.num_rows == 0:
            continue
        assert set(table.column("grounds").to_pylist()) == {False}
        assert set(table.column("regenerable").to_pylist()) == {True}


def test_blog_posts_carry_resolvable_links():
    """The blog zone exists to hand back links, so the link is the invariant.

    Jekyll resolves `permalink: /posts/:title/` against the frontmatter slug, not
    the filename — the two differ on most posts here (2024-01-04-How-Do-I-Decide-
    My-Favorite-Breweries.md → /posts/favorite-brewery-deciding/). Deriving the
    URL from the wrong one produces links that are individually plausible and
    uniformly 404, which is worse than publishing no link at all.
    """
    from exo import config
    import json as _json
    p = config.T1 / "post.parquet"
    if not p.exists():
        return
    t = pq.read_table(p)
    if t.num_rows == 0:
        return
    slugs = t.column("slug").to_pylist()
    urls = t.column("url").to_pylist()
    assert all(u == config.post_url(s) for s, u in zip(slugs, urls))
    # t2_post_vec is keyed by id, so THAT is what must be unique or the vector
    # blob amplifies on the join (the t1_notes lesson). The id hashes the
    # FILENAME, which Jekyll guarantees unique; the slug it does not — this blog
    # has a colliding pair (two Listening At A Distance editions), which the
    # loader warns about at build time because the damage is on the live site.
    assert len(set(t.column("id").to_pylist())) == t.num_rows
    assert all(x for x in t.column("title").to_pylist())
    # tags is JSON on the wire — D1 has no list type, so a non-string here
    # becomes an unparseable column on the far side.
    assert all(isinstance(_json.loads(x), list) for x in t.column("tags").to_pylist())


def test_blog_posts_are_authored_and_ground():
    """t1, human, grounds=true. The blog is his writing — published, edited, and
    the strongest form of it. A derivation that could read his notes but not his
    essays would be reading the draft and ignoring the finished argument."""
    from exo import config
    p = config.T1 / "post.parquet"
    if not p.exists():
        return
    t = pq.read_table(p)
    if t.num_rows == 0:
        return
    assert set(t.column("author").to_pylist()) == {"human"}
    assert set(t.column("tier").to_pylist()) == {"t1"}
    assert set(t.column("grounds").to_pylist()) == {True}


def test_no_preference_weight_reaches_the_surface():
    """ADR-0013: the read surface ranks by relevance, never by preference.

    `t2_affinity.score` is plays + mentions * 500. That 500 is a judgement about
    what matters, and it was the only one that ever reached served data — no
    tool read it, so it sat there as a tripwire rather than a feature. The
    column stays in the store, where affinity is a worked cross-zone example;
    it must not stay in the projection.

    Asserted at the projection rather than in the Worker because the Worker is
    not the boundary here: a column that is published can be selected by any
    tool anyone adds later, including one written without reading the ADR.
    """
    from exo import config
    p = config.SERVE / "t2_affinity.parquet"
    if not p.exists():
        return
    cols = set(pq.read_table(p).schema.names)
    assert "score" not in cols
    assert {"artist", "plays", "mentions"} <= cols  # the facts stay


def test_drafts_are_read_from_the_directory_not_from_git(tmp_path, monkeypatch):
    """t1_draft must count a draft that has never been committed.

    The project scanner reads `git ls-files`, so it only sees committed files —
    and drafts are committed by the nightly, not as they are written. Sourcing them from git
    would hide the draft being written right now, which is the one most worth
    surfacing precisely because it is the one he might abandon.

    Asserted behaviourally, against a directory that is not a repo at all: if
    the loader ever starts consulting git, this returns nothing.
    """
    from exo import config
    from exo.loaders import drafts
    (tmp_path / "2026-08-19-never-committed.md").write_text(
        "---\ntitle: 'Never Committed'\n---\n\n# Never Committed\n\n" + "word " * 40,
        encoding="utf-8")
    (tmp_path / "README.md").write_text("# drafts\n\nrepo furniture", encoding="utf-8")
    monkeypatch.setattr(config, "DRAFTS", tmp_path)

    rows = drafts.load()
    assert len(rows) == 1                      # the README is furniture, not a draft
    r = rows[0].payload
    assert r["title"] == "Never Committed"
    assert r["started"] == "2026-08-19"        # from the filename
    assert r["words"] > 5 and r["state"] == "drafting"
    assert rows[0].author == "human" and rows[0].grounds is True


def test_a_draft_with_only_a_title_is_kept_and_labelled(tmp_path, monkeypatch):
    """A title someone typed and walked away from is an intention, and forgetting
    it is the problem this store exists to solve. Kept, but marked `empty`, so a
    reader does not present a bare heading as work in progress."""
    from exo import config
    from exo.loaders import drafts
    (tmp_path / "2026-08-19-just-a-title.md").write_text(
        "---\ntitle: 'Just A Title'\n---\n\n# Just A Title\n", encoding="utf-8")
    monkeypatch.setattr(config, "DRAFTS", tmp_path)
    rows = drafts.load()
    assert len(rows) == 1 and rows[0].payload["state"] == "empty"


def test_draft_projection_keeps_its_shape_when_empty():
    """Zero drafts is the normal state of a writer between pieces, not an edge
    case — and an empty zone writes a parquet carrying only the provenance
    columns, so every reader naming `title` dies with "no such column". t0_event
    learned this once already; the projection pins the shape either way."""
    from exo import config
    p = config.SERVE / "t1_draft.parquet"
    if not p.exists():
        return
    t = pq.read_table(p)
    cols = set(t.schema.names)
    # `modified` earns its place: "which drafts went cold" is the question a
    # writer cannot answer about themselves.
    assert {"slug", "title", "words", "modified", "state", "body"} <= cols
    if t.num_rows == 0:
        return
    assert set(t.column("author").to_pylist()) == {"human"}
    assert set(t.column("grounds").to_pylist()) == {True}
    assert len(set(t.column("id").to_pylist())) == t.num_rows


def test_markdown_listing_skips_apple_double_sidecars(tmp_path):
    """`._name.md` is a macOS xattr sidecar, not writing.

    A listing on macOS hides them; opening the same archive on Linux
    materialises 163-byte binary files that match *.md. That is how one draft
    became three in CI, and how a table ended up empty in production with every
    step green.
    """
    from exo import io as exo_io
    (tmp_path / "real.md").write_text("# real\n\nprose")
    (tmp_path / "._real.md").write_bytes(b"\x00\x05\x16\x07")
    (tmp_path / "README.md").write_text("furniture")
    found = [p.name for p in exo_io.markdown(tmp_path, exclude={"README.md"})]
    assert found == ["real.md"]


def test_sql_literal_refuses_a_value_d1_would_drop():
    """A NUL byte is not escapable — D1 takes the batch and loses it."""
    from exo.scripts_impl import publish_cf
    assert publish_cf._sql_literal("it's fine") == "'it''s fine'"
    with pytest.raises(publish_cf.BinaryValue):
        publish_cf._sql_literal("bad\x00value")
