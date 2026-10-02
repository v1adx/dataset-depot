"""The depot described as a dataset of its own."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd

from .. import cache, config, registry
from ..dataset import Dataset
from ..runner import run
from .files import _matching, _mtime


def _callable_name(func: Callable) -> str:
    """What to call a pipeline step in a listing.

    The same idiom the runner uses to record which extras ran: a plain
    function answers with its own ``__name__``, a callable object with its
    class's, and neither raises.
    """
    return getattr(func, "__name__", type(func).__name__)


@dataclass(eq=False)
class DatasetIndex(Dataset):
    """One row per dataset in the depot: what it is, and how big it got.

    Built from the metafiles alone, so listing the depot never opens a parquet.
    Two consequences worth knowing: `changed` and `timestamp` are UTC, and
    `size_in_memory` is what a dataset is holding in this process — zero for
    one that nobody has read yet, which is most of them in a fresh run.

    ``pipeline`` lists the callables a run would use, in the order it would
    use them: extractors, then transforms, then validators, then extras. By
    name alone — the order is fixed, so a step's position already says which
    phase it belongs to. Utilities and artifacts are left out: the pipeline
    never calls them.

    This is also the index the access layer reads. Answering "what is in the
    depot" from here costs no imports at all, and it goes stale correctly on
    its own: the probe watches every dataset module, so editing one is what
    rebuilds the table — no invalidation rule written by hand.

    With ``load_all``, every dataset in the depot becomes a ref. Loading this
    then brings the whole depot up to date, in topological order, each node
    once — the framework's own machinery rather than a driver written beside
    it — and the table describes what was just computed instead of what is on
    disk. Without it, listing the depot only describes what is on disk, which
    is what an interface wants: ``run_all`` asks for that same run explicitly,
    and works either way. Both, with ``reload``, are offered as utilities.
    """

    root: Path | None = None
    load_all: bool = False

    COLUMNS = ["type", "name", "class", "module", "doc", "pipeline", "layer", "refs",
               "dependants", "changed", "timestamp", "rows", "size_in_memory",
               "size_of_cache"]

    @staticmethod
    def index_mtime(d: "DatasetIndex") -> float:
        """When the depot last moved: any dataset module, or any other metafile.

        Its own metafile is excluded on purpose. Every run of this dataset
        rewrites it, and a probe that watched it would wake the dataset with
        its own output, for as long as it existed.
        """
        own = cache.meta_path(d)
        watched = _matching(str(Path(d.root) / "**" / "*.py"))
        watched += [str(p) for p in config.cache_dir().rglob("*.meta") if p != own]
        # The root only when there is nothing else: an empty depot watches
        # nothing, and a probe answering 0 would never build the table at all.
        # Not beside the rest — importing the modules writes __pycache__ into
        # it, and the index would wake itself with its own first run.
        return max((_mtime(p) for p in watched), default=_mtime(str(d.root)))

    @staticmethod
    def describe(found: registry.Found, layer: int, dependants: int) -> dict:
        d = found.dataset

        # The metafile, never the parquet: an index that opened every file
        # would pull the whole depot into memory just to report its size.
        meta = cache.read_meta(d)
        data_path = cache.data_path(d)

        # A dataset this process has already run knows more than its metafile.
        d.load_meta()
        in_memory = d._dataframe

        return {
            "type":           d.type,
            "name":           d.name,
            "class":          type(d).__name__,
            "module":         found.module,
            "doc":            found.doc.splitlines()[0] if found.doc else "",
            "pipeline":       [_callable_name(func)
                               for phase in (d.extractors, d.transforms,
                                             d.validators, d.extras)
                               for func in phase],
            "layer":          layer,
            "refs":           [ref.key for ref in d.refs],
            "dependants":     dependants,
            "changed":        pd.to_datetime(d.changed, unit="s") if d.changed else pd.NaT,
            "timestamp":      pd.to_datetime(d.timestamp, unit="s") if d.timestamp else pd.NaT,
            "rows":           len(in_memory) if in_memory is not None else (meta.shape[0] if meta.shape else 0),
            "size_in_memory": int(in_memory.memory_usage(deep=True).sum()) if in_memory is not None else 0,
            "size_of_cache":  data_path.stat().st_size if data_path.is_file() else 0,
        }

    @staticmethod
    def extract(d: "DatasetIndex") -> None:
        scan = registry.discover(d.root)
        for path, why in scan.failures.items():
            print(f"DatasetIndex: skipping {path} — {why}")
        found = scan.found
        pool = [f.dataset for f in found.values()]
        depth, counts = registry.layers(pool), registry.dependants(pool)

        rows = [DatasetIndex.describe(f, depth[key], counts[key]) for key, f in found.items()]
        d.dataframe = pd.DataFrame(rows, columns=DatasetIndex.COLUMNS)

    def _every_other_dataset(self) -> list[Dataset]:
        """The depot minus this dataset: including itself would be a cycle."""
        return [f.dataset for key, f in registry.discover(self.root).found.items()
                if key != self.key]

    def reload(self, _: Dataset | None = None) -> None:
        """Read the depot again: which datasets are there now, and what they need.

        The table looks after itself — the probe watches every module, so an
        edit rebuilds it. The refs do not: they are gathered once, at
        construction, and a dataset written while this process runs is absent
        from them, which is enough for ``run_all`` to skip it.

        The ignored argument is the utility contract — a manual action is
        called with the dataset it belongs to, and here that is this one.
        """
        if self.load_all:
            self.refs = self._every_other_dataset()
        self._dataframe = None

    def run_all(self, _: Dataset | None = None, force: bool = False) -> None:
        """Bring every dataset in the depot up to date, then describe what came out.

        One walk of the graph, not a loop of runs: the runner takes the whole
        depot at once and touches each node once. It keeps going past a
        failure, so one broken source leaves the rest fresh, and raises at the
        end naming whatever failed. A loop would recompute a
        shared source once per dependant, and under ``force`` send it back to
        its API that many times.

        The datasets come from the tree rather than from ``refs``, so this
        works on an index that holds none — which is how an interface builds
        it, so that listing the depot never runs it. Its own table is rebuilt
        afterwards, unforced: the work has just been done, and ``force`` here
        would only do it again.
        """
        decisions = run(*self._every_other_dataset(), force=force, keep_going=True)
        self.reload()
        self.pipeline()
        failures = [f"{d.dataset.key}: {d.error}" for d in decisions if d.error]
        if failures:
            raise RuntimeError("; ".join(failures))

    def run_all_forced(self, _: Dataset | None = None) -> None:
        """``run_all``, recomputing regardless of freshness.

        An entry of its own because a utility is called with the dataset and
        nothing else; there is nowhere to pass a flag.
        """
        self.run_all(force=True)

    def __post_init__(self) -> None:
        super().__post_init__()
        self.root = Path(self.root) if self.root else config.source()
        self.probe = self.index_mtime
        self.extractors.insert(0, self.extract)
        self.utilities += [self.reload, self.run_all, self.run_all_forced]

        if self.load_all:
            # Here rather than in the field: a dataset cannot be excluded from
            # its own refs until it knows its own key, and including itself is
            # a cycle.
            self.refs = self._every_other_dataset()
