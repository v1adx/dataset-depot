"""The row of view buttons, and the one place a view's settings are written.

A view opens as a page of its own, in a new tab, rather than in a dialog over
the graph. The dialog was a bad host: every component here is third-party code
that renders its own menus into the body — Perspective's copy and export
dropdowns, AgGrid's filter popovers — and a Quasar dialog pulls focus back from
anything outside itself, closing those menus the moment they open. A page has
no such layer to fight.

The consequence to keep in mind is that the tab showing a view and the graph
listing it are two different clients. `view_config_changed` is emitted by the
former, so the handler has to be registered there, not here — which is why
`save_config` is a plain function rather than a method: the page owns it now.
The payload names the dataset and the view it belongs to, so a stale tab writes
to its own dataset and never to whichever one the graph has moved on to.

A component is a module, not a class: LABEL, ICON, render(dts, view_id,
config), and an install() for the ones that need CDN tags in the head. The
registry below is what [+] offers, which is why `columns` is not in it — the
column picker draws inline under the meta card and has no page of its own.
"""
from __future__ import annotations

from nicegui import ui

from depot import Dataset

from ..views import View, ViewStore
from . import aggrid_table, chart, perspective_table, pivot_table

KINDS = {
    "aggrid": aggrid_table,
    "pivot": pivot_table,
    "perspective": perspective_table,
    "chart": chart,
}


def view_url(key: str, view_id: str) -> str:
    """Where a view lives.

    The dataset page's own route, with the view named in the query rather than
    in the path: the key is matched as `{key:path}` because it carries slashes,
    and a greedy path segment followed by another one is a guessing game.
    """
    return f"/dts/{key}?view={view_id}"


def save_config(store: ViewStore, e) -> None:
    """Write a layout the browser has just reported.

    A function rather than a method because the client that receives the event
    is the view's own tab, which has no ViewsBar in it.
    """
    args = e.args if not isinstance(e.args, list) else e.args[0]
    key, view_id, config = args.get("key"), args.get("view"), args.get("config")
    if not key or not view_id or config is None:
        return
    store.save_config(key, view_id, config)


class ViewsBar:
    def __init__(self, row, store: ViewStore) -> None:
        self._row = row
        self._store = store
        self._dts: Dataset | None = None

    def refresh(self, dts: Dataset) -> None:
        """Rebuild the row for a dataset. [+] is always last."""
        self._dts = dts
        self._row.clear()
        with self._row:
            for view in self._store.list(dts.key):
                module = KINDS.get(view.kind)
                if module is None:
                    # The column picker, or a kind typed into the json by hand
                    # that no component answers to.
                    continue
                # href on the button rather than a click handler that navigates:
                # QBtn renders as an anchor when given one, and a real anchor is
                # never taken for a popup. ui.navigate.to(new_tab=True) arrives
                # over the websocket, outside any user gesture, and the browser
                # is within its rights to block it.
                button = ui.button(icon=module.ICON).props(
                    f'outline size=sm href="{view_url(dts.key, view.id)}" '
                    'target="_blank"'
                ).tooltip(view.title)
                with button, ui.context_menu():
                    ui.menu_item("Delete", on_click=lambda v=view: self._remove(v))
            with ui.button(icon="add").props("outline size=sm").tooltip("Add a view"):
                with ui.menu():
                    for kind, module in KINDS.items():
                        ui.menu_item(module.LABEL, on_click=lambda k=kind: self._add(k))

    def _add(self, kind: str) -> None:
        """Added, not opened. Opening it from here would mean navigating on the
        browser's behalf, which is the popup the anchors above avoid."""
        if self._dts is None:
            return
        self._store.add(self._dts.key, kind, KINDS[kind].LABEL)
        self.refresh(self._dts)

    def _remove(self, view: View) -> None:
        if self._dts is None:
            return
        self._store.remove(self._dts.key, view.id)
        self.refresh(self._dts)
