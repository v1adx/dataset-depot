from __future__ import annotations

from typing import Callable

from nicegui import ui

from depot import Dataset, Decision

from .catalog import Catalog, render
from .settings import active
from .views import ViewStore
from .widgets.fullscreen_dialog import FullscreenDialog
from .components.meta_card import DatasetMetaCard
from .components.dataset_table import DatasetTable
from .components.views_bar import ViewsBar

NodeEvent = Callable[[str, Decision | None], None]


class DatasetPanel:
    def __init__(self, catalog: Catalog) -> None:
        self._catalog = catalog
        self._dts: Dataset | None = None
        self._node_handlers: list[NodeEvent] = []
        self._decisions: list = []
        # Set only while self._decisions is stale relative to it — i.e. a run
        # failed after a previous run had left decisions behind. Cleared by
        # every successful run. last_log() checks this before self._decisions
        # so a failure is never reported using the previous run's text.
        self._last_error: str | None = None

        with ui.column().classes("w-full h-full gap-3 p-4 overflow-auto") as self.container:
            self._empty_label = ui.label("Select a dataset on the graph").classes(
                "text-gray-400 text-sm m-auto"
            )
            self._spinner = ui.spinner(size="lg").classes("m-auto")

            with ui.column().classes("w-full gap-3 overflow-x-hidden") as self._content:
                self._dialog = FullscreenDialog()
                store = ViewStore(active().state / "views")

                self._meta = DatasetMetaCard(
                    on_force=self.force,
                    on_tabulator=self._open_tabulator,
                    catalog=self._catalog,
                )
                self._views = ViewsBar(self._meta.views_row, self._dialog, store)
                self._table = DatasetTable(store)

        self._set_state("empty")

    # ------------------------------------------------------------------
    # Public interface (called from the page)
    # ------------------------------------------------------------------

    def on_node_event(self, handler: NodeEvent) -> None:
        """Subscribe to run events. The page ties them to the graph.

        Four stages. "run-started" carries None and is sent once, before _run
        so much as calls catalog.run — which gives the handler its chance to
        forget everything it remembers about the previous run before the first
        "started" arrives. That ordering matters: a probe and cache.read_meta
        both reach for a remote source ahead of the first "started", and if one
        of them blows up, a handler without that reset would still be holding
        the state of an earlier, already finished run. "started" and "finished"
        carry the Decision of the node in question. "failed" carries None and
        means the run fell over somewhere inside — a handler must look at stage
        before it touches decision. `FlowGraph.apply` cannot be subscribed
        directly: its first line reads `decision.dataset`, so it would raise on
        "run-started" and on "failed".
        """
        self._node_handlers.append(handler)

    def _emit(self, stage: str, decision: Decision | None) -> None:
        for handler in self._node_handlers:
            handler(stage, decision)

    async def select(self, key: str) -> None:
        try:
            self._dts = self._catalog.dataset(key)
        except KeyError:
            ui.notify(f"No dataset {key}", type="negative")
            return
        try:
            await self._run(key)
        except Exception as exc:
            # The dataset just selected has no valid data to show: fall back
            # to empty rather than leave the previous dataset's tables on
            # screen under the new one's identity.
            ui.notify(f"Pipeline failed: {exc}", type="negative")
            self._emit("failed", None)
            self._decisions = []
            self._last_error = f"{key}: {exc}"
            self._dts = None
            self._set_state("empty")

    async def force(self) -> None:
        if not self._dts:
            return
        key = self._dts.key
        try:
            await self._run(key, force=True)
        except Exception as exc:
            # The dataset on screen is still the one the user is looking at;
            # its previously loaded data is still valid, so stay put.
            ui.notify(f"Pipeline failed: {exc}", type="negative")
            self._emit("failed", None)
            self._decisions = []
            self._last_error = f"{key}: {exc}"
            self._set_state("loaded")

    def current_key(self) -> str | None:
        if not self._dts:
            return None
        return self._dts.key

    def last_log(self) -> str:
        """The last run's log — the same text the CLI prints.

        Failure takes priority over stale decisions: Catalog.run raises
        instead of returning on a failed pipeline, so there is nothing new
        to render, and rendering the previous successful run's decisions
        here would read as if the just-failed run had succeeded.
        """
        if self._last_error:
            return f"run failed — {self._last_error}"
        if not self._decisions:
            return "nothing has run yet"
        return render(self._decisions, target=self._dts.key if self._dts else None)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _run(self, key: str, force: bool = False) -> None:
        """Run, then refresh.

        Pipeline failure propagates — callers want different recovery
        states, so each handles its own exception around this call.
        Rendering failure does not: by the time _refresh_all runs, the
        pipeline has already succeeded, so a bug in a table component is a
        rendering problem, not a pipeline one. It must not be reported as
        "Pipeline failed", must not discard self._decisions (the run log is
        still valid), and must not paint the node red — it gets its own,
        separate notification and otherwise leaves the state alone.
        """
        self._set_state("loading")
        self._emit("run-started", None)
        self._decisions = await self._catalog.run(key, force=force, on_node=self._emit)
        self._last_error = None
        try:
            self._refresh_all()
        except Exception as exc:
            ui.notify(f"Could not render {key}: {exc}", type="negative")
        self._set_state("loaded")

    def _set_state(self, state: str) -> None:
        self._empty_label.set_visibility(state == "empty")
        self._content.set_visibility(state == "loaded")
        self._spinner.set_visibility(state == "loading")

    def _refresh_all(self) -> None:
        dts = self._dts
        self._meta.refresh(dts)
        self._views.refresh(dts)
        self._table.refresh(dts)

    def _open_tabulator(self) -> None:
        if not self._dts:
            return
        ui.navigate.to(f"/dts/{self._dts.key}")
