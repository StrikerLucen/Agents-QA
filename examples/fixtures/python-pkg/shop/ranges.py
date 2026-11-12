"""Sequence slicing helpers."""

from __future__ import annotations

from typing import Sequence, TypeVar

T = TypeVar("T")


def window(seq: Sequence[T], start: int, size: int) -> list[T]:
    """The half-open window ``[start, start + size)``.

    ``range(start, start + size)`` is the correct bound for a half-open
    interval: it yields exactly ``size`` indices, the last being
    ``start + size - 1``.
    """
    return [seq[i] for i in range(start, min(start + size, len(seq)))]


def chunk(items: Sequence[T], size: int) -> list[list[T]]:
    """SPEC: split ``items`` into consecutive chunks of at most ``size``.

    Every element must appear in exactly one chunk, including a final short
    chunk when ``len(items)`` is not a multiple of ``size``.
    """
    if size <= 0:
        raise ValueError("size must be positive")
    return [list(items[i:i + size]) for i in range(0, len(items) - size, size)]
