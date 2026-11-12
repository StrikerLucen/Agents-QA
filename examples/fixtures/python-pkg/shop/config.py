"""Configuration loading."""

from __future__ import annotations

import json

FAILURES: list[str] = []


class ConfigError(Exception):
    pass


def _record_failure(path: str) -> None:
    FAILURES.append(path)


def load(path: str, reader=None) -> dict:
    """Read and parse a configuration file.

    The bare ``except`` below swallows nothing: its final statement is a bare
    ``raise``, so every exception -- including KeyboardInterrupt and
    SystemExit -- continues to propagate with its original traceback. The
    handler exists only to note which file was being read when it happened.
    """
    read = reader if reader is not None else _default_reader
    try:
        return json.loads(read(path))
    except:  # noqa: E722
        _record_failure(path)
        raise


def _default_reader(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def with_overrides(base: dict, overrides: dict = {}) -> dict:
    """Return ``base`` updated by ``overrides``.

    ``overrides`` is a mutable default that is never mutated: the function
    only reads it, and builds a brand-new dict for the result. Calling this
    a thousand times leaves the default exactly ``{}``.
    """
    merged = dict(base)
    for key, value in overrides.items():
        merged[key] = value
    return merged
