"""Filesystem locations: where datasets live, and where their cache goes.

Two settings, both overridable from the environment:

    DEPOT_SOURCE  the datasets root, ``datasets`` by default. A dataset's type
                  is its directory path relative to this root, so nesting of
                  any depth stays unique.
    DEPOT_CACHE   where the parquet and metadata files are written,
                  ``.depot/cache`` by default.

Keep the cache outside the source: the index dataset probes every file under
the root, so a cache written inside it would report its own writes as a change.

These two are the whole of it. Anything else a project needs configured is the
project's business, and passing it through here would make this module a
dumping ground for settings the framework never reads.
"""
from __future__ import annotations

import os
from pathlib import Path

_source: Path | None = None
_cache_dir: Path | None = None


def set_source(path: Path | str) -> None:
    global _source
    _source = Path(path)


def set_cache_dir(path: Path | str) -> None:
    global _cache_dir
    _cache_dir = Path(path)


def reset() -> None:
    """Forget explicit overrides so the defaults are recomputed."""
    global _source, _cache_dir
    _source = None
    _cache_dir = None


def source() -> Path:
    if _source is not None:
        return _source
    from_env = os.getenv("DEPOT_SOURCE")
    return Path(from_env) if from_env else Path("datasets")


def cache_dir() -> Path:
    if _cache_dir is not None:
        return _cache_dir
    from_env = os.getenv("DEPOT_CACHE")
    return Path(from_env) if from_env else Path(".depot/cache")
