"""Board games carry the owner's rating, and adding it moved no row's identity."""
from exo.loaders import collection


def _load(monkeypatch, game):
    monkeypatch.setattr(collection, "_json", lambda name: [game] if name == "games.json" else [])
    return [r for r in collection.load() if r.payload["kind"] == "board_game"]


def test_a_rated_game_keeps_its_rating_and_note(monkeypatch):
    [g] = _load(monkeypatch, {"name": "Codenames", "type": "party", "rating": 3,
                              "rating_note": "fun with the right crowd"})
    assert g.payload["rating"] == "3" and g.payload["rating_note"] == "fun with the right crowd"


def test_rating_does_not_change_the_id(monkeypatch):
    [bare] = _load(monkeypatch, {"name": "Codenames", "type": "party"})
    [rated] = _load(monkeypatch, {"name": "Codenames", "type": "party", "rating": 5})
    assert bare.id == rated.id
    assert bare.payload["rating"] == ""
