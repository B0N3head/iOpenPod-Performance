from __future__ import annotations

from scripts.generate_fake_music_library import _build_track_recipe


def test_track_recipes_are_unique_by_design():
    recipes = {
        tuple(_build_track_recipe(track_serial, 8))
        for track_serial in range(200)
    }
    assert len(recipes) == 200
