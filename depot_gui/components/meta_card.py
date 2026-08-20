from __future__ import annotations

from datetime import datetime
from typing import Callable

from nicegui import ui

from depot import Dataset

from ..settings import active
from ..widgets.function_runner import FunctionRunner


def refs_html(dts: Dataset) -> str:
    """Refs as links to their own pages. The key goes into the URL as it is —
    the route takes {key:path}, so a slash in a nested type does not trouble
    it."""
    if not dts.refs:
        return "No refs"
    links = " · ".join(f'<a href="/dts/{ref.key}">{ref.name}</a>' for ref in dts.refs)
    return "Refs: " + links


def shape_text(dts: Dataset) -> str:
    df = dts.dataframe
    return f"{df.shape[0]} × {df.shape[1]}"


class DatasetMetaCard:
    def __init__(self, on_force: Callable, on_tabulator: Callable, catalog) -> None:

        self._runner = FunctionRunner()
        self.on_force = on_force
        self._catalog = catalog

        with ui.card().classes("w-full no-shadow dark bg-orange-1") as self.container:
            with ui.row().classes("items-center justify-between"):
                self._name_label = ui.label("").classes("text-bold")
                with self._name_label:
                    self._name_tooltip = ui.tooltip("").style("white-space: pre-line; max-width: 480px")
                self._type_badge = ui.badge("")

                # Tabulator is a page of its own, not a view: it has its own
                # settings and filter dialogs and its own state file, and the
                # ref links on this very card lead to it. So it keeps a fixed
                # button, and the views of the dataset follow it.
                ui.button(icon="table_rows", on_click=on_tabulator).props(
                    "outline size=sm"
                ).tooltip("Open Tabulator")

                self.views_row = ui.row().classes("items-center gap-2")

            with ui.row().classes("gap-2 items-center"):
                self._ts_label = ui.label("").classes("text-xs text-gray-500 gap-4")
                self._refs_label = ui.html("").classes("text-xs text-gray-500")
                self._shape_label = ui.label("").classes("text-xs text-gray-500")

            self._actions_row = ui.row().classes("gap-2 items-center")

    def refresh(self, dts: Dataset) -> None:
        ts = (
            datetime.fromtimestamp(dts.timestamp).strftime("%Y-%m-%d %H:%M")
            if dts.timestamp > 0
            else "—"
        )
        shape = shape_text(dts)
        refs_str = refs_html(dts)
        type_color = active().color(dts.type)
        dts_name = dts.name.replace("_", " ").title() if dts.name else "—"
        dts_type = dts.type.replace("_", " ").upper() if dts.type else "—"

        doc = self._catalog.doc(dts.key)
        self._name_label.set_text(dts_name)
        self._name_tooltip.set_text(doc or "")
        self._name_tooltip.set_visibility(bool(doc))
        self._type_badge.set_text(dts_type)
        self._type_badge.props(f"color={type_color}")
        self._ts_label.set_text(f"Updated: {ts}")
        self._shape_label.set_text(f"Size: {shape}")
        self._refs_label.set_content(refs_str)

        utils = getattr(dts, "utilities", None) or []
        artifacts = getattr(dts, "artifacts", None) or []

        self._actions_row.clear()
        with self._actions_row:
            ui.button(
                "" if utils else "Pipeline",
                on_click=self.on_force,
                icon="refresh",
            ).props("unelevated color=deep-orange size=sm")

            for func in utils:
                label = func.__name__.replace("_", " ")
                ui.button(
                    label,
                    on_click=lambda f=func, d=dts: self._runner.run(f, d),
                ).props("unelevated color=blue-grey size=sm")

            for func in artifacts:
                label = func.__name__.replace("_", " ")
                ui.button(
                    label,
                    on_click=lambda f=func, d=dts: self._runner.run(f, d),
                ).props("unelevated color=teal size=sm")
