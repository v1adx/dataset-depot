"""Datasets that come from files on disk.

A file's mtime is a time on the wall clock, which is exactly what a probe owes
(see the Dataset docstring). So a file-backed dataset knows precisely when it
needs rereading, and a run that changes nothing costs one stat() per file.

Identity comes from the module the template is instantiated in, the same as a
dataset declared by hand — so a file-backed dataset belongs in its own module,
next to the ones that read it. Pass ``name``/``type`` only to override that.

They differ in what they point at, and therefore in what "nothing there"
means. ``DatasetFromFile`` names one file that has to exist, so a missing path
is a configuration error and says so. The glob-based ones describe a set that
is allowed to be empty: no matches means no data yet, not a mistake.

One thing to know when choosing where the cache goes: the probe watches the
containing directories as well as the files, because that is the only way a
deletion shows up. The cost is that anything else changing in those folders
also counts as a change — so never point DEPOT_CACHE inside a watched folder,
or the dataset's own writes will keep waking it.
"""
from __future__ import annotations

import glob as _glob
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from ..dataset import Dataset


def _matching(pattern: str | list[str]) -> list[str]:
    patterns = [pattern] if isinstance(pattern, (str, Path)) else list(pattern)
    # recursive, so that ** means what it looks like it means
    return sorted({found for p in patterns for found in _glob.glob(str(p), recursive=True)})


def _mtime(path: str) -> float:
    return Path(path).stat().st_mtime


def _base_dirs(pattern: str | list[str]) -> set[str]:
    """The existing directories a pattern reaches into, before any wildcard."""
    patterns = [pattern] if isinstance(pattern, (str, Path)) else list(pattern)
    found = set()
    for p in patterns:
        head = str(p).split("*")[0]
        base = Path(head) if head.endswith(("/", "\\")) else Path(head).parent
        if base.is_dir():
            found.add(str(base))
    return found


@dataclass(eq=False)
class DatasetFileBacked(Dataset):
    """What every file-backed template shares: a path, and how to read it.

    Subclasses say which of the matching files they want and what they make of
    them. The probe is common to all of them, because "when did this set of
    files last change" has one answer.
    """

    path: str | list[str] = ""
    reader: Callable | None = None
    reader_kwargs: dict = field(default_factory=dict)
    processor: Callable | None = None

    @staticmethod
    def newest_mtime(d: "DatasetFileBacked") -> float:
        """The version of a set of files: when any of it last changed.

        The directories count, not only the files. A deleted file does not move
        the newest mtime — it can move it backwards — so watching the files
        alone means a deletion is never noticed and the dataset serves a stale
        list forever. A directory's mtime moves when an entry appears or
        disappears, which is the one cheap signal that catches it.
        """
        paths = _matching(d.path)
        watched = set(paths) | {str(Path(p).parent) for p in paths} | _base_dirs(d.path)
        return max((_mtime(p) for p in watched), default=0.0)

    def read(self, paths: list[str]) -> pd.DataFrame:
        frames = [self.reader(p, **self.reader_kwargs) for p in paths]
        if self.processor:
            frames = [self.processor(f) for f in frames]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def __post_init__(self) -> None:
        super().__post_init__()
        self.probe = self.newest_mtime


@dataclass(eq=False)
class DatasetFromFile(DatasetFileBacked):
    """One named file. A missing path is an error, not an empty dataset."""

    reader: Callable | None = pd.read_csv

    @staticmethod
    def required_mtime(d: "DatasetFromFile") -> float:
        path = Path(d.path)
        if not path.is_file():
            raise FileNotFoundError(f"{d.key}: no file at {path}")
        return _mtime(str(path))

    @staticmethod
    def extract(d: "DatasetFromFile") -> None:
        d.dataframe = d.read([str(d.path)])

    def __post_init__(self) -> None:
        super().__post_init__()
        self.probe = self.required_mtime
        self.extractors.insert(0, self.extract)


@dataclass(eq=False)
class DatasetFromLastFile(DatasetFileBacked):
    """The most recently modified file matching the glob.

    For sources that arrive as dated exports, where only the latest one counts
    and the older ones are kept as history.
    """

    reader: Callable | None = pd.read_excel

    @staticmethod
    def extract(d: "DatasetFromLastFile") -> None:
        paths = _matching(d.path)
        latest = max(paths, key=_mtime) if paths else None
        d.dataframe = d.read([latest] if latest else [])

    def __post_init__(self) -> None:
        super().__post_init__()
        self.extractors.insert(0, self.extract)


@dataclass(eq=False)
class DatasetFromSetOfFiles(DatasetFileBacked):
    """Every file matching the glob, read and concatenated.

    ``processor`` runs on each file's frame before they are joined, for sources
    that need reshaping one file at a time. ``reader_kwargs`` go to the reader.
    """

    reader: Callable | None = pd.read_csv

    @staticmethod
    def extract(d: "DatasetFromSetOfFiles") -> None:
        d.dataframe = d.read(_matching(d.path))

    def __post_init__(self) -> None:
        super().__post_init__()
        self.extractors.insert(0, self.extract)


@dataclass(eq=False)
class DatasetListOfFiles(DatasetFileBacked):
    """The files themselves, not their contents: path, name, extension, mtime, size.

    For folders that are an inventory rather than a data source — documents to
    be matched against records, exports waiting to be processed.
    """

    COLUMNS = ["path", "filename", "extension", "mtime", "size"]

    @staticmethod
    def extract(d: "DatasetListOfFiles") -> None:
        paths = _matching(d.path)
        d.dataframe = pd.DataFrame({
            "path":      paths,
            "filename":  [Path(p).stem for p in paths],
            "extension": [Path(p).suffix.lstrip(".") for p in paths],
            "mtime":     [_mtime(p) for p in paths],
            "size":      [Path(p).stat().st_size for p in paths],
        }, columns=DatasetListOfFiles.COLUMNS)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.extractors.insert(0, self.extract)
