"""Saves are searched by meaning from an index of their own.

`saves.f32` stays apart from `vectors.f32`: bookmarks are other people's pages
and must not rank in `whats_relevant`, and the main blob is all-or-nothing on
scoped runs, where the notes lane never loads Raindrop."""
import json

import duckdb

from exo import t2
from exo.scripts_impl import publish_cf


def _serve(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_cf.config, "SERVE", tmp_path)
    con = duckdb.connect(":memory:")
    con.execute(f"""COPY (SELECT * FROM (VALUES ('s1', [1.0, 0.0]::FLOAT[]), ('s2', [0.0, 1.0]::FLOAT[]))
                    t(id, vec)) TO '{tmp_path}/t2_save_vec.parquet' (FORMAT parquet)""")
    con.execute(f"""COPY (SELECT * FROM (VALUES ('s1', 'One trick'), ('s2', 'Glazes')) t(id, title))
                    TO '{tmp_path}/t0_raindrop.parquet' (FORMAT parquet)""")
    out = tmp_path / "cf"
    out.mkdir()
    return con, out


def test_a_full_run_writes_the_saves_index_apart_from_the_main_blob(tmp_path, monkeypatch):
    con, out = _serve(tmp_path, monkeypatch)
    info = publish_cf._emit_save_vectors(con, out, partial=False)
    assert info["written"] and info["count"] == 2
    meta = json.loads((out / "saves.json").read_text())
    assert [r["id"] for r in meta["rows"]] == ["s1", "s2"]
    assert {r["kind"] for r in meta["rows"]} == {"save"}
    assert (out / "saves.f32").stat().st_size == 2 * 2 * 4
    publish_cf._emit_vectors(con, out, partial=False)
    main = json.loads((out / "vectors.json").read_text())["rows"]
    assert not any(r["kind"] == "save" for r in main), "whats_relevant must never rank a bookmark"


def test_a_scoped_run_leaves_the_published_saves_index_alone(tmp_path, monkeypatch):
    con, out = _serve(tmp_path, monkeypatch)
    assert publish_cf._emit_save_vectors(con, out, partial=True)["written"] is False
    assert not (out / "saves.f32").exists(), (
        "the instance uploads saves.f32 only when it exists; a scoped run did not "
        "rebuild the saves and must not replace the copy in R2")


def test_no_saves_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.setattr(publish_cf.config, "SERVE", tmp_path)
    out = tmp_path / "cf"
    out.mkdir()
    assert publish_cf._emit_save_vectors(duckdb.connect(), out, partial=False)["written"] is False


def test_the_owners_note_is_embedded_after_the_title_and_before_the_excerpt(monkeypatch):
    monkeypatch.setattr(t2, "read_source", lambda q: [
        ("s1", "You won't believe", "on loneliness in cities", "A page about tricks"),
        ("s2", "Bare title", "", ""),
    ])
    seen = {}
    monkeypatch.setattr(t2.embed, "embed_texts", lambda texts: seen.setdefault("t", texts) and [[0.0]] * len(texts))
    rows = t2.embed_saves()
    assert seen["t"] == ["You won't believe. on loneliness in cities. A page about tricks", "Bare title"]
    assert [r.id for r in rows] == ["s1", "s2"] and rows[0].zone == "save_vec"
