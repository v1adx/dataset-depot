"""The row of view buttons, and the one place a view's settings are written.

Every component on JS emits the same `view_config_changed`, carrying the
dataset key and the id of the view it belongs to. That is why one handler is
enough for all of them — and why a dialog left open on a dataset the panel has
since moved off cannot write onto the wrong one: the payload names its own
destination, so there is nothing to compare it against and nothing to get
wrong.

A component is a module, not a class: LABEL, ICON, render(dts, view_id,
config), and an install() for the ones that need CDN tags in the head. The
registry below is what [+] offers, which is why `columns` is not in it — the
column picker draws inline under the meta card and has no dialog to open.
"""
from __future__ import annotations

from nicegui import ui

from depot import Dataset

from ..views import View, ViewStore
from ..widgets.fullscreen_dialog import FullscreenDialog
from . import aggrid_table, perspective_table, pivot_table

KINDS = {
    "aggrid": aggrid_table,
    "pivot": pivot_table,
    "perspective": perspective_table,
}


class ViewsBar:
    def __init__(self, row, dialog: FullscreenDialog, store: ViewStore) -> None:
        self._row = row
        self._dialog = dialog
        self._store = store
        self._dts: Dataset | None = None

        for module in KINDS.values():
            install = getattr(module, "install", None)
            if install is not None:
                install()
        ui.on("view_config_changed", self._save)

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
                ui.button(
                    icon=module.ICON, on_click=lambda v=view: self._open(v)
                ).props("outline size=sm").tooltip(view.title)
            with ui.button(icon="add").props("outline size=sm").tooltip("Add a view"):
                with ui.menu():
                    for kind, module in KINDS.items():
                        ui.menu_item(module.LABEL, on_click=lambda k=kind: self._add(k))

    def _open(self, view: View) -> None:
        dts = self._dts
        if dts is None:
            return
        module = KINDS[view.kind]
        config = self._store.config(dts.key, view.id)
        self._dialog.open(
            f"{dts.type} / {dts.name} — {view.title}",
            lambda: module.render(dts, view.id, config),
            on_delete=lambda: self._remove(view),
        )

    def _add(self, kind: str) -> None:
        """A view is added in order to be used, so it opens straight away."""
        if self._dts is None:
            return
        view = self._store.add(self._dts.key, kind, KINDS[kind].LABEL)
        self.refresh(self._dts)
        self._open(view)

    def _remove(self, view: View) -> None:
        if self._dts is None:
            return
        self._store.remove(self._dts.key, view.id)
        self._dialog.close()
        self.refresh(self._dts)

    def _save(self, e) -> None:
        args = e.args if not isinstance(e.args, list) else e.args[0]
        key, view_id, config = args.get("key"), args.get("view"), args.get("config")
        if not key or not view_id or config is None:
            return
        self._store.save_config(key, view_id, config)
