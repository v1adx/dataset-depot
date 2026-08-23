"""Three pages and the entry point.

Everything that knows about routes is gathered here: the package hands out one
function, start(), and the components have no idea the pages exist.
"""
from __future__ import annotations

import asyncio
from typing import Callable

from nicegui import Client, app, ui

from depot import Decision

from . import theme
from .catalog import Catalog
from .errors import describe
from .components.tabulator_filter import TabulatorFilter
from .components.tabulator_settings import TabulatorPageSettings
from .components.tabulator_table import TabulatorTable
from .components.views_bar import KINDS, save_config
from .flow import FlowGraph, build_edges, build_nodes
from .panel import DatasetPanel
from .settings import Settings, configure
from .state import StateFile
from .views import ViewStore
from .widgets.function_runner import mount_artifacts


def node_painter(flow: FlowGraph) -> Callable[[str, Decision | None], None]:
    """A run event → how the node looks, a failure included.

    The runner sends "finished" from a finally block — before the exception
    reaches Catalog.run — so a node that failed gets the same
    "started" → "finished" a successful one does, and only then a "failed" with
    no decision. That is why the tracker of the last "started" is NOT cleared
    on "finished": it has to outlive it, so that on "failed" we still know
    which node actually fell over. Datasets are visited in topological order,
    so the last one started is that node.

    But _run_one calls probe and cache.read_meta BEFORE the first "started",
    and both may reach out to a remote source. If a probe fails ahead of the
    first "started" of a run, the tracker is still holding the last node of the
    PREVIOUS run — for a node in the middle of the graph, a node that finished
    successfully back then. Without a reset, "failed" would redden an unrelated
    node that had already done its work. So DatasetPanel sends "run-started"
    before every call to catalog.run, and here that wipes the tracker: the
    worst outcome becomes "no node is marked" rather than "the wrong node is".
    """
    last_started: dict = {"key": None}

    def on_node(stage: str, decision: Decision | None) -> None:
        if stage == "run-started":
            last_started["key"] = None
            return
        if stage == "failed":
            if last_started["key"]:
                flow.mark(last_started["key"], "error")
                last_started["key"] = None
            return
        if stage == "started":
            last_started["key"] = decision.dataset.key
        flow.apply(stage, decision)

    return on_node


def start(settings: Settings) -> None:
    configure(settings)
    mount_artifacts()
    catalog = Catalog(settings)
    positions = StateFile(settings.state / "layout.json")

    @ui.page("/clear-data")
    def clear_data() -> None:
        app.storage.clear()
        ui.label("Storage cache cleared.")

    @ui.page("/")
    async def index(client: Client) -> None:
        await _index_page(client, catalog, positions, settings)

    views = ViewStore(settings.state / "views")

    @ui.page("/dts/{key:path}")
    async def dataset_page(key: str, view: str = "") -> None:
        """One route, two pages. `view` is a query parameter rather than a path
        segment because the key is matched greedily as `{key:path}`."""
        if view:
            await _view_page(key, catalog, views, view)
        else:
            await _dataset_page(key, catalog)

    ui.run(title=settings.title, port=settings.port, reload=False)


async def _index_page(client, catalog, positions, settings) -> None:
    ui.query("body").style("margin:0;overflow:hidden;")

    splitter = ui.splitter(value=theme.SPLITTER_DEFAULT).classes("w-full h-screen")
    expanded: dict = {"side": None}

    def expand(side: str) -> None:
        if expanded["side"] == side:
            splitter.set_value(theme.SPLITTER_DEFAULT)
            expanded["side"] = None
        else:
            splitter.set_value(0 if side == "right" else 100)
            expanded["side"] = side

    failures = catalog.failures()
    if failures:
        with ui.row().classes("w-full bg-red-2 text-red-10 text-xs px-3 py-1"):
            ui.label(f"{len(failures)} module(s) could not be imported")
            with ui.expansion("details").classes("text-xs"):
                for path, why in failures.items():
                    ui.label(f"{path} — {why}")

    with splitter.before:
        with ui.column().classes("relative w-full h-full overflow-hidden"):
            flow = FlowGraph()
            # The index describes the depot rather than belonging to it: drawing
            # it as a node would misrepresent what it is, and it has no edges at
            # all. So it is a static button — but one that goes down the same
            # selection path a node click does, so the panel, the run and the
            # log behave exactly as they do everywhere else.
            ui.button(
                icon="inventory_2",
                on_click=lambda: asyncio.create_task(
                    on_node_selected({"id": catalog.index_key})
                ),
            ).props("flat round size=sm").classes(
                "absolute top-2 left-2 z-10 bg-white/70"
            ).tooltip(f"{catalog.index_key} — what the depot holds")
            ui.button(icon="open_in_full", on_click=lambda: expand("left")).props(
                "flat round size=sm"
            ).classes("absolute top-2 right-2 z-10 bg-white/70")

    with splitter.after:
        with ui.column().classes("relative w-full h-full"):
            panel = DatasetPanel(catalog)
            ui.button(icon="open_in_full", on_click=lambda: expand("right")).props(
                "flat round size=sm"
            ).classes("absolute top-2 right-2 z-10 bg-white/70")

    with ui.right_drawer(value=False).props("overlay width=560") as log_drawer:
        log_view = ui.code("nothing has run yet").classes("w-full text-xs")

    def show_log() -> None:
        log_view.set_content(panel.last_log())
        log_drawer.toggle()

    ui.button(icon="receipt_long", on_click=show_log).props(
        "flat round size=sm"
    ).classes("absolute bottom-2 right-2 z-10 bg-white/70")

    panel.on_node_event(node_painter(flow))
    ui.timer(5.0, lambda: flow.refresh_labels(catalog.rows()))

    async def on_node_selected(data: dict) -> None:
        with client:
            await panel.select(data.get("id", ""))

    flow.on_node_select(lambda d: asyncio.create_task(on_node_selected(d)))
    flow.on_positions_changed(lambda pos: positions.write(pos))

    def on_orientation(e) -> None:
        data = e.args if not isinstance(e.args, list) else e.args[0]
        if data.get("portrait", False):
            splitter.props("horizontal")
        else:
            splitter.props(remove="horizontal")
        splitter.update()

    ui.on("orientation_change", on_orientation)

    saved = positions.read()
    rows = catalog.rows()
    nodes = build_nodes(rows, saved, settings)
    edges = build_edges(rows)

    async def delayed_init() -> None:
        await client.connected()
        with client:
            flow.init(nodes, edges, bool(saved))
            ui.run_javascript("""
                function _updateOrientation() {
                    window.emitEvent('orientation_change', { portrait: window.innerHeight > window.innerWidth });
                }
                window.addEventListener('resize', _updateOrientation);
                setTimeout(_updateOrientation, 200);
            """)

    asyncio.create_task(delayed_init())


def _run_button(key: str, catalog) -> None:
    """Recompute the dataset without going back to the graph.

    Forced, like the card's Pipeline button: the page has already run the
    pipeline plainly on its way in, so an unforced run here would almost always
    decide there is nothing to do.

    The page reloads rather than rebuilding its contents in place. Both pages
    are a pure function of the key, the view and the stored state, so a reload
    is the honest way to show new data — and it sidesteps the stale references
    a rebuild would leave behind: TabulatorPageSettings and TabulatorFilter each
    hold the table and the dataset they were built with. Perspective's layout
    survives it, being in the token by then.
    """
    async def run() -> None:
        button.props("loading")
        try:
            await catalog.run(key, force=True)
        except Exception as exc:
            ui.notify(
                f"Pipeline failed — {key}: {describe(exc, f'pipeline failed: {key}')}",
                type="negative",
            )
            button.props(remove="loading")
            return
        ui.navigate.reload()

    button = ui.button(icon="refresh", on_click=run).props(
        "flat round dense size=sm"
    ).tooltip("Run pipeline")


def _page_header(title: str, on_back: Callable[[], None] | None = None) -> ui.row:
    """The thin bar both pages wear.

    Title on the left, then a spacer — so whatever the caller adds inside the
    returned row lands on the right, which is where a page's service buttons go.

    Back only where there is somewhere to go back to. Tabulator replaces the
    graph in the tab it was opened from, so back means the graph. A view opens
    in a tab of its own, where back has no history behind it and would have to
    invent a destination — the dataset's table, which is not where the reader
    came from. The tab's own close button is the honest way out.
    """
    with ui.row().classes("items-center gap-2 px-3 w-full").style(
        f"border-bottom:1px solid #e0e0e0;height:{theme.HEADER_HEIGHT}px;"
    ) as bar:
        if on_back is not None:
            ui.button(icon="arrow_back", on_click=on_back).props(
                "flat round dense size=sm"
            )
        ui.label(title).classes("text-sm font-bold")
        ui.space()
    return bar


async def _view_page(key: str, catalog, store: ViewStore, view_id: str) -> None:
    """A single view, filling a tab of its own.

    Opened from the graph's views row through a plain anchor, so this is a
    separate client from the one that lists the views. That is why the handler
    writing layouts back is registered here: the component emits
    `view_config_changed` into this page, and nowhere else.
    """
    ui.query("body").style("margin:0;overflow:hidden;")
    # nicegui.css pads .nicegui-content by 1rem and gaps its children by
    # another, so the column below — which asks for the viewport minus the
    # header — ends up 48px past the bottom of a page that has just been told
    # not to scroll, and the last rows of every table went with it. A view
    # fills its tab; the inset belongs to pages that have something to inset
    # from. AgGrid used to subtract those 48px from its own height by hand.
    ui.query(".nicegui-content").classes("p-0 gap-0")

    view = next((v for v in store.list(key) if v.id == view_id), None)
    module = KINDS.get(view.kind) if view else None

    with _page_header(f"{key} — {view.title}" if view else key):
        _run_button(key, catalog)

    if view is None or module is None:
        ui.label("No such view").classes("text-gray-400 text-sm m-auto")
        return

    install = getattr(module, "install", None)
    if install is not None:
        install()
    ui.on("view_config_changed", lambda e: save_config(store, e))

    try:
        dts = catalog.dataset(key)
        await catalog.run(key)
    except Exception as exc:
        ui.label(f"Error: {exc}").classes("text-red-500 p-4")
        return

    with ui.column().classes("w-full").style(
        f"height:calc(100vh - {theme.HEADER_HEIGHT}px)"
    ):
        module.render(dts, view.id, store.config(key, view.id))


async def _dataset_page(key: str, catalog) -> None:
    ui.query("body").style("margin:0;overflow:hidden;")

    holder: dict = {"settings": None, "filter": None}
    with _page_header(key, lambda: ui.navigate.back()):
        _run_button(key, catalog)
        ui.button(
            icon="filter_list",
            on_click=lambda: holder["filter"] and holder["filter"].open(),
        ).props("flat round dense size=sm").tooltip("Filter")
        ui.button(
            icon="settings",
            on_click=lambda: holder["settings"] and holder["settings"].open(),
        ).props("flat round dense size=sm").tooltip("Columns")

    try:
        dts = catalog.dataset(key)
        await catalog.run(key)
    except Exception as exc:
        ui.label(f"Error: {exc}").classes("text-red-500 p-4")
        return

    table = TabulatorTable()
    table.refresh(dts)
    table.build()

    holder["settings"] = TabulatorPageSettings(table, dts)
    holder["filter"] = TabulatorFilter(table)
