"""Dataset — a node of the graph: its declaration, its state and its data."""
from __future__ import annotations

import functools
import re
import sys
import time
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import pandas as pd

from . import config

_PKG_DIR = Path(__file__).resolve().parent

# What a type and a name may be made of. A type is the folder path kept as it
# is on disk, so it may contain "/"; a name is one segment. Folding the path
# into a single word instead is what once let store/helper/agents.py and
# store_helper/agents.py claim the same identity and the same parquet.
_NAME = re.compile(r"^[\w.-]+$", re.UNICODE)
_TYPE = re.compile(r"^[\w.-]+(/[\w.-]+)*$", re.UNICODE)


def _check_identity(value: str, pattern: re.Pattern, what: str, dataset: str) -> None:
    if not pattern.match(value):
        raise ValueError(
            f"{dataset}: {what} {value!r} may only contain letters, digits, "
            f"'.', '-', '_' — and, for a type, '/' between folders. The key is "
            f"spelled 'type:name' and the cache mirrors it as type/name, so "
            f"anything else would make the two disagree."
        )


def _format_age(seconds: float) -> str:
    """A span of time in one unit, coarsely: ``45s``, ``12m``, ``3h``, ``2w``.

    Shared by info, get_age and the interface's node labels, so "12m ago"
    cannot start meaning different things.
    """
    for limit, unit, size in ((60, "s", 1), (3600, "m", 60), (86400, "h", 3600),
                              (604800, "d", 86400)):
        if seconds < limit:
            return f"{int(seconds // size)}{unit}"
    return f"{int(seconds // 604800)}w"


@functools.cache
def _own_code(cls: type) -> frozenset:
    """The code of every method the dataset's class chain defines.

    A frame running one of them is a template filling itself in — a project's
    own ``Dataset`` subclass as much as depot's — and never the module that
    declared the dataset. Skipping by file instead would lose a module that
    defines its template and declares the dataset in the same breath.
    """
    codes = set()
    for klass in cls.__mro__:
        for attr in vars(klass).values():
            code = getattr(getattr(attr, "__func__", attr), "__code__", None)
            if code is not None:
                codes.add(code)
    return frozenset(codes)


def _caller_file(skip: frozenset = frozenset()) -> Path | None:
    """The file of the first stack frame outside the depot package.

    Three guards. Synthetic frames (``co_filename`` starting with ``<``) are
    skipped because the dataclass-generated ``__init__`` is one of them and
    would otherwise be mistaken for the caller. Package frames are skipped
    because a template factory living inside depot builds datasets on behalf
    of a user module, and it is that user module we want. Frames running code
    in ``skip`` — the dataset's own methods — are skipped for the same reason
    when the template lives in the project: otherwise every instance took the
    identity of the file defining the class, and shared its parquet.
    """
    frame = sys._getframe(1)
    while frame is not None:
        filename = frame.f_code.co_filename
        if not filename.startswith("<") and frame.f_code not in skip:
            path = Path(filename)
            try:
                resolved = path.resolve()
            except OSError:
                resolved = path
            if _PKG_DIR not in resolved.parents:
                return resolved
        frame = frame.f_back
    return None


def _identity_from(path: Path) -> tuple[str, str]:
    """Derive ``(name, type)`` from the file a dataset was defined in.

    The name is the filename; the type is the directory path relative to the
    datasets root, kept as it is — ``source/raw/records.py`` is the type
    ``source/raw``. Keeping the separator is what makes the derivation
    injective: joining the folders into one word would let ``a/b/x.py`` and
    ``a_b/x.py`` arrive at the same identity, and one would overwrite the
    other's cache. A file outside the root (a test writing a module to a
    temporary directory, say) falls back to the name of its immediate
    directory.
    """
    name = path.stem
    try:
        relative = path.parent.resolve().relative_to(config.source().resolve())
    except (ValueError, OSError):
        return name, path.parent.name
    parts = [part for part in relative.parts if part not in (".", "")]
    return name, "/".join(parts) if parts else path.parent.name


@dataclass(eq=False)
class Dataset:
    """One node of the graph.

    Identity is ``type:name``, never object identity: two references to the
    same ``type:name`` are the same node, which is what makes a diamond in the
    graph collapse on its own. Both are derived from the defining module when
    not given explicitly — the type is the folder path, the name is the file.

    The colon is the separator because no filesystem this runs on allows one
    in a filename, so a key can always be split back into the two halves it
    was made of, however deeply the folders nest.

    **Callables, in the order the runner uses them.** Each is a list, and each
    is called with the dataset itself:

    - ``extractors`` — go to the source and produce data. The only phase that
      touches the outside world for input.
    - ``transforms`` — compute this dataset's data from its refs' data.
    - ``validators`` — check the result. They run before anything is written,
      so data that fails never reaches the cache; raising is how they fail.
    - ``extras`` — side effects pointing outward: writing files, pushing to
      external systems. They run only when ``changed`` actually moved, so a
      run that found nothing new stays silent.
    - ``utilities`` — manual actions. The pipeline never calls them; they are
      offered to a human or an agent as buttons or commands.
    - ``artifacts`` — manual too, but they return the path of a file they
      generated: an html report, a chart image. ``None`` if there was nothing
      to show.

    **Scheduling.** ``probe`` reads the source and returns its current
    version; the runner calls it once before doing any work, and its answer
    becomes the new ``changed``. ``threshold`` is the fallback for sources
    with no cheap probe: ``None`` means no timer at all, ``N`` means do not
    consult the source more often than once every N seconds, ``0`` means
    consult it every run — on the runner's schedule: a ``cache=False``
    dataset read between runs goes to the source on its own, see Storage.
    A probe and a threshold together make no sense —
    the probe already answers exactly.

    Which of the two: write a probe when learning the version is cheaper than
    fetching the data — a file's ``mtime``, a ``max(updated_at)`` query. Use a
    timer when learning the version *is* fetching the data, which is most
    APIs; there the extractor answers instead, see Storage below.

    **A probe must return a time**, on the same wall clock as everything else.
    Its answer is not only compared with itself: it becomes this dataset's
    ``changed``, which a dependent compares against its own — and a dependent
    with no probe versions itself by ``time.time()``. A probe returning an
    ETag, a revision number or a row count hands out a version so small that
    ``ref.changed > changed`` is never true again, and the dependent silently
    stops recomputing forever. A source whose version is not a time therefore
    gets no probe at all: give it a timer, and let the comparison below decide
    whether anything actually moved.

    **State.** ``timestamp`` records when the source was last consulted and
    drives the timer; loading from the cache does not move it, because
    restoring a cached dataset returns it to a state it was already in.
    ``changed`` is the version of the data, in the source's own scale, and is
    what dependents compare themselves against. Both survive restarts in the
    metafile.

    **Storage.** ``cache=True`` stores the dataframe; ``cache=False`` means the
    dataframe is not this dataset's product — it went outside, to a database
    or an external service — and only metadata is kept. Metadata is always
    kept: without it a stored dataframe is uninterpretable.

    For ``cache=False`` the dataframe is this run's delta, which gives the
    extractor its way to say "I went and nothing had changed": leave the
    dataframe empty and ``changed`` stays put, so no dependent wakes up. The
    runner empties it before every extract, so an empty frame always means
    this run and not the previous one. An extractor that changed something
    without having rows to show for it (deletions, say) sets ``changed``
    itself. Read outside a run, it has nothing stored to open and produces
    its data on the spot — a trip to the source that ``threshold`` does not
    count, so a source that must not be asked more often wants ``cache=True``.

    For ``cache=True`` the same answer comes from the data rather than from
    the extractor: a product identical to what is already stored has not
    changed, however much work went into producing it. The version stays, the
    parquet is not rewritten, and no extra fires. That is what lets a dataset
    with no cheap probe sit on a timer without waking its dependents on every
    tick.

    **Where a version comes from.** A probe asked the source and was told, so
    its answer is the version. A dataset with no extractors fetched nothing —
    its data is its inputs rearranged — so its version is the newest of its
    refs, never the clock; that is what makes the derived half of a graph
    idempotent. Anything else went outside to a source that cannot say how old
    its answer is, and only the moment of fetching is honest.

    ``props`` is free-form configuration for this dataset's own callables.
    """

    name: str | None = None
    type: str | None = None
    refs: list["Dataset"] = field(default_factory=list)

    extractors: list[Callable] = field(default_factory=list, repr=False)
    transforms: list[Callable] = field(default_factory=list, repr=False)
    validators: list[Callable] = field(default_factory=list, repr=False)
    extras: list[Callable] = field(default_factory=list, repr=False)
    utilities: list[Callable] = field(default_factory=list, repr=False)
    artifacts: list[Callable] = field(default_factory=list, repr=False)

    probe: Callable[["Dataset"], float] | None = field(default=None, repr=False)
    threshold: int | None = None
    cache: bool = True
    props: dict = field(default_factory=dict, repr=False)

    timestamp: float = 0.0
    changed: float = 0.0

    source: Path | None = field(default=None, repr=False, init=False)
    _source_digest: str = field(default="", repr=False, init=False)

    _dataframe: pd.DataFrame | None = field(default=None, repr=False, init=False)
    _meta_loaded: bool = field(default=False, repr=False, init=False)
    # (timestamp, changed) as this object last read or wrote them: what tells
    # another process's write apart from this object's own state.
    _seen: tuple[float, float] | None = field(default=None, repr=False, init=False)

    def __post_init__(self) -> None:
        # Always, not only when the identity needs deriving: the module that
        # declares a dataset is an input to it like any other, and the cache
        # has to be able to tell when that input changed.
        self.source = _caller_file(_own_code(type(self)))
        # Fingerprinted now, while the file still holds the code being run.
        from . import cache
        self._source_digest = cache.file_digest(self.source)

        if self.name is None or self.type is None:
            if self.source is None:
                raise ValueError(
                    "a dataset needs an identity: no declaring module was found "
                    "to derive one from, so pass name and type. Without them it "
                    "would cache itself as 'None:None'."
                )
            name, type_ = _identity_from(self.source)
            self.name = self.name if self.name is not None else name
            self.type = self.type if self.type is not None else type_

        _check_identity(self.type, _TYPE, "type", f"{self.type}:{self.name}")
        _check_identity(self.name, _NAME, "name", f"{self.type}:{self.name}")

    @property
    def key(self) -> str:
        return f"{self.type}:{self.name}"

    @property
    def dataframe(self) -> pd.DataFrame:
        """The data, materialised on first read.

        For a cached dataset that means opening its parquet and nothing else:
        the read never runs the pipeline, which is what lets the runner decide
        a whole graph from metadata alone and leave untouched subtrees on disk.

        A ``cache=False`` dataset has no parquet to open. The same read used to
        answer with an empty frame — indistinguishable from a source that had
        nothing — while the timer, restored from the metafile, told the runner
        there was nothing to fetch: the data existed nowhere, and only
        ``force=True`` ever produced any. Its data lives in this process or not
        at all, so when it is not here it is produced, this node alone.

        Only the phases that make the data run: no validators, no extras, and
        the version stays where it is. Nothing was stored and nothing went
        outward — the source was consulted for its rows, not for its version,
        and the timer that schedules those visits is left to the runner.
        """
        if self._dataframe is None:
            if self.cache:
                from . import cache
                self._dataframe = cache.load(self)
            else:
                # Assigned before the phases run, not after: an extractor that
                # reads its own dataframe would otherwise re-enter here for ever.
                self._dataframe = pd.DataFrame()
                for phase in (*self.extractors, *self.transforms):
                    phase(self)
        return self._dataframe

    @dataframe.setter
    def dataframe(self, value: pd.DataFrame) -> None:
        if value is None:
            raise TypeError(
                "Dataset.dataframe cannot be None — assign an empty DataFrame. "
                "None here would re-arm the lazy load from cache."
            )
        self._dataframe = value

    def load_meta(self) -> None:
        """Restore timestamp/changed from the metafile.

        On first use, and again whenever the file no longer says what this
        object last read or wrote — another process has run the dataset since,
        a CLI next to a long-lived interface. Its version is then the current
        one, and the data this object holds is not: it is dropped, so the next
        read opens what that process stored. Otherwise the object's own state
        stands, whatever it has been set to since.
        """
        from . import cache
        meta = cache.read_meta(self)
        disk = (meta.timestamp, meta.changed)
        if self._meta_loaded and disk == self._seen:
            return
        if self._meta_loaded:
            self._dataframe = None
        self.timestamp, self.changed = disk
        self._seen = disk
        self._meta_loaded = True

    def reset(self) -> None:
        """Drop the data, the state and everything stored on disk.

        Use it when the code that produces the data changed: the source has
        not moved, so no probe will notice, and only a clean slate forces a
        recomputation.
        """
        from . import cache
        self._dataframe = None
        self.timestamp = 0.0
        self.changed = 0.0
        self._meta_loaded = True
        self._seen = (0.0, 0.0)
        cache.drop(self)

    def pipeline(self, force: bool = False) -> None:
        """Bring this dataset up to date without materialising its data.

        The counterpart to ``load``: it deliberately never touches
        ``dataframe``. Use it for scheduled refreshes and for datasets whose
        product goes outside — running a whole depot through ``load`` would
        pull every dataframe into memory and defeat the lazy loading the rest
        of the design is built on.
        """
        from .runner import run
        run(self, force=force)

    def load(self, force: bool = False) -> pd.DataFrame:
        """Bring this dataset up to date and return its data.

        The ordinary entry point — this is what a transform calls to read a
        ref, and what a person or an agent calls to look at a dataset. It
        materialises the dataframe by definition; when you only want the
        refresh, use ``pipeline``.
        """
        from .runner import run
        run(self, force=force)
        return self.dataframe

    def info(self, rows: int = 0) -> None:
        """Bring this dataset up to date and describe what came out.

        For the bottom of a module, where running the file should tell you
        something. It runs first — printing the state of a dataset nobody has
        computed would only ever say "empty".
        """
        from . import cache

        self.load(force=False)
        df = self.dataframe
        stored = cache.data_path(self)
        size = stored.stat().st_size / 1024 if stored.is_file() else 0

        when = f"{_format_age(time.time() - self.changed)} ago" if self.changed else "never"
        print("=" * 80)
        print(f"{self.key}")
        print(f"   changed: {when}")
        print(f"   shape: {df.shape[0]} rows × {df.shape[1]} cols")
        print(f"   cache: {size:.0f} KB")
        print(f"   columns:")
        for column, dtype in df.dtypes.items():
            print(f"    - {str(column):<24} {dtype}")

        if rows:
            print("-" * 80)
            print(f"   first {rows} rows:" if rows < df.shape[0] else "   rows:")
            with pd.option_context("display.max_columns", None, "display.width", None,
                                   "display.max_colwidth", 40):
                print()
                print(df.head(rows).to_string())
        print("=" * 80)


    def get_age(self) -> str:
        return _format_age(time.time() - self.changed) if self.changed else "never"

    def get_mtime(self) -> datetime:
        return datetime.fromtimestamp(self.changed)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Dataset):
            return NotImplemented
        return self.type == other.type and self.name == other.name

    def __hash__(self) -> int:
        return hash((self.type, self.name))

    def __repr__(self) -> str:
        return f"Dataset({self.key!r})"
