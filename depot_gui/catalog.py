"""The one door between the interface and the framework.

No component imports depot.registry or depot.runner. This file is the whole
surface that has to be checked against depot for compatibility — the package
ships on its own, separately from any project that uses it.

The division of labour inside: the index answers for the shape of the graph and
for cold state, a live Dataset for content and execution. The index is read from
parquet and imports no dataset module, so the application starts instantly; the
import (~0.4 s for forty modules) is paid once per process, on the first request
for a live dataset.
"""
from __future__ import annotations

import asyncio
import queue
from typing import Callable

import pandas as pd

from depot import Dataset, Decision, registry, run as run_pipeline
from depot.dataset import _format_age
from depot.log import render
from depot.templates import DatasetIndex

from .settings import Settings

NodeEvent = Callable[[str, Decision], None]

# Re-exported so flow.py and panel.py — like every other module in this
# package — need only ever import from .catalog, never reach into depot
# directly. Neither name is in depot/__init__.py's __all__, so this module
# is where that surface gets named on this package's behalf.
__all__ = ["Catalog", "_format_age", "render"]


class Catalog:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # The same identity a project's own index module declares: the cache is
        # shared on purpose — `depot show system:index` then reports exactly
        # what the interface is looking at.
        self._index = DatasetIndex(root=settings.datasets, type="system", name="index")
        self._live: dict[str, Dataset] = {}

    # ----------------------------------------------------------------- index

    def rows(self) -> pd.DataFrame:
        """The depot as a table: one row per dataset. Rebuilds itself when it
        needs to.

        The index probe watches the mtime of every module and of every other
        dataset's metafile, so after any run — its own or someone else's — the
        next call hands back a fresh table, and no invalidation rule had to be
        written by hand.
        """
        df = self._index.load().copy()
        df["key"] = df["type"] + ":" + df["name"]
        df["epoch"] = _to_epoch(df["timestamp"])
        if "pipeline" not in df.columns:
            # A table written before the column existed. The index probe
            # watches the dataset modules and other metafiles, not depot's own
            # source, so nothing here would rebuild it on its own — and a
            # missing column is a KeyError in build_nodes rather than a
            # tooltip without a chain. Fills itself in on the next run of
            # anything.
            df["pipeline"] = [[] for _ in range(len(df))]
        return df

    def failures(self) -> dict[str, str]:
        """Modules that will not import: path → reason.

        registry hands these back separately from what it found precisely so
        that a consumer is not made to choose between taking everything down
        and hiding the problem. The interface is the consumer that must show.
        """
        return registry.discover(self._settings.datasets).failures

    @property
    def index_key(self) -> str:
        """The key of the index itself.

        A property rather than a constant: the identity is stated in one place,
        where the `DatasetIndex` is built, so the two values have nowhere to
        drift apart.
        """
        return self._index.key

    # ---------------------------------------------------------- live dataset

    def dataset(self, key: str) -> Dataset:
        """The live object for a key. The first call imports the whole depot.

        The index is the one exception: it describes the depot rather than
        belonging to it, so `registry.discover` does not find it and never
        will. We hand back the same object the table was built from — otherwise
        the interface would show one copy and run another.
        """
        if key == self._index.key:
            return self._index
        if key not in self._live:
            self._live[key] = registry.get(key, self._settings.datasets)
        return self._live[key]

    def doc(self, key: str) -> str:
        """The module's full docstring, for the tooltip.

        The index carries only its first line.
        """
        if key == self._index.key:
            return (type(self._index).__doc__ or "").strip()
        found = registry.discover(self._settings.datasets).found.get(key)
        return found.doc if found else ""

    # ------------------------------------------------------------------- run

    async def run(
        self,
        key: str,
        force: bool = False,
        on_node: NodeEvent | None = None,
    ) -> list[Decision]:
        """Run a dataset with all its dependencies, reporting node by node.

        depot.run calls the callback from a worker thread, and a node can only
        be painted from the event loop. So the events go onto a queue and this
        coroutine drains it — the same device FunctionRunner uses.

        An exception from the pipeline propagates, but the queue is drained
        before it does: the node everything stopped on has to reach the
        interface, or it stays lit up as "running" forever.
        """
        dts = self.dataset(key)
        events: queue.Queue = queue.Queue()

        loop = asyncio.get_running_loop()
        future = loop.run_in_executor(
            None,
            lambda: run_pipeline(dts, force=force, on_event=lambda stage, d: events.put((stage, d))),
        )

        def drain() -> None:
            while not events.empty():
                stage, decision = events.get_nowait()
                if on_node is not None:
                    on_node(stage, decision)

        try:
            while not future.done():
                await asyncio.sleep(0.05)
                drain()
            decisions = await future
        except BaseException:
            drain()
            raise
        drain()
        return decisions


def _to_epoch(timestamps: pd.Series) -> pd.Series:
    """pd.Timestamp in UTC → epoch seconds; NaT → 0.0.

    Through astype rather than .timestamp(): the column is naive, and
    .timestamp() would read UTC as local time — the node's label would be off
    by a timezone.
    """
    seconds = timestamps.astype("int64") / 1e9
    return seconds.where(timestamps.notna(), 0.0)
