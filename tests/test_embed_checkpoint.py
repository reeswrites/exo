"""An interrupted embed keeps what it finished.

The cache used to be written once, after every miss was embedded. A CI job
killed by its timeout partway through a large batch of new text therefore kept
nothing, and the next run started from zero and was killed at the same place.
"""
from __future__ import annotations

import pytest

from exo import config, embed


class _Killed(Exception):
    pass


class _FakeModel:
    """Embeds `allowed` texts, then dies the way a timed-out job does."""

    def __init__(self, allowed: int):
        self.left = allowed

    def embed(self, texts, batch_size, parallel):
        for t in texts:
            if self.left == 0:
                raise _Killed
            self.left -= 1
            yield [float(len(t))] * embed.DIMS


def test_an_interrupted_embed_keeps_its_finished_chunks(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EMBED_CACHE", tmp_path / "embed.parquet")
    monkeypatch.setattr(config, "ensure_dirs", lambda: None)
    monkeypatch.setattr(embed, "CHECKPOINT", 3)
    monkeypatch.setattr(embed, "_model", _FakeModel(allowed=7))
    texts = [f"text {i}" for i in range(10)]

    with pytest.raises(_Killed):
        embed.embed_texts(texts)

    # Two whole chunks (6 texts) were written before the kill; the seventh,
    # mid-chunk, was not. The rerun only has to embed what is left.
    assert len(embed._load_cache()) == 6
    monkeypatch.setattr(embed, "_model", _FakeModel(allowed=4))
    assert len(embed.embed_texts(texts)) == 10
