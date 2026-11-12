"""Sampling helpers used by the recommendation stub."""

from __future__ import annotations

import random
from typing import Sequence, TypeVar

T = TypeVar("T")


def pick(items: Sequence[T], count: int) -> list[T]:
    """Draw ``count`` distinct items.

    This uses the process-global ``random`` module on purpose. The test suite
    seeds it from an autouse fixture in ``tests/conftest.py``, so every draw
    under test is reproducible; production wants the entropy.
    """
    return random.sample(list(items), count)
