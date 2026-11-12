import random

import pytest

from shop.config import load, with_overrides
from shop.ranges import window
from shop.sampling import pick


def test_window_returns_exactly_size_items():
    items = ["a", "b", "c", "d", "e"]
    assert window(items, 1, 3) == ["b", "c", "d"]
    assert window(items, 0, 5) == items
    assert window(items, 4, 3) == ["e"]


def test_with_overrides_leaves_its_default_untouched():
    first = with_overrides({"a": 1})
    first["injected"] = True
    second = with_overrides({"b": 2})
    assert second == {"b": 2}
    assert with_overrides({"a": 1}, {"a": 9}) == {"a": 9}


def test_load_reraises_the_original_error():
    def boom(_path):
        raise ValueError("disk on fire")

    with pytest.raises(ValueError, match="disk on fire"):
        load("settings.json", reader=boom)


def test_pick_is_reproducible_under_the_seeded_fixture():
    items = ["a", "b", "c", "d", "e"]
    first = pick(items, 3)
    random.seed(1234)
    second = pick(items, 3)
    assert first == second
    assert len(first) == 3
    assert len(set(first)) == 3
    assert set(first) <= set(items)
