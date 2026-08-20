from __future__ import annotations

from nicegui import ui

from .tabulator_table import TabulatorTable


_FILTER_TYPES = ["=", "!=", "like", "keywords", "starts", "ends", "<", ">", "<=", ">=", "in", "regex"]


class TabulatorFilter:
    def __init__(self, tab: TabulatorTable) -> None:
        self._tab = tab
        self._filters: list[dict] = []
        self._available_cols: list[str] = []

        with ui.dialog() as self._dialog:
            with ui.card().classes("w-full").style("min-width:660px;max-width:900px;"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("Filter").classes("font-bold text-base")
                    ui.button(icon="close", on_click=self._dialog.close).props("flat round size=sm")
                self._filters_col = ui.column().classes("w-full gap-2")
                ui.label("For 'in' type, separate values with commas").classes("text-gray-400 text-xs q-mt-xs")
                with ui.row().classes("w-full justify-end gap-2 q-mt-sm"):
                    ui.button("Clear All", on_click=self._clear_all).props("flat")
                    ui.button("Cancel", on_click=self._dialog.close).props("flat")
                    ui.button("Apply", on_click=self._apply).props("color=primary")

    def open(self) -> None:
        _, col_config = self._tab.get_raw_config()
        self._available_cols = list(col_config.keys())
        if not self._filters:
            col = self._available_cols[0] if self._available_cols else ""
            self._filters = [{"col": col, "type": "=", "value": ""}]
        self._rebuild_ui()
        self._dialog.open()

    def _rebuild_ui(self) -> None:
        self._filters_col.clear()
        with self._filters_col:
            for i, f in enumerate(self._filters):
                self._build_filter_card(i, f)

    def _build_filter_card(self, i: int, f: dict) -> None:
        with ui.card().props("flat bordered").classes("w-full !p-1"):
            with ui.row().classes("items-center gap-2 w-full"):
                ui.button(
                    icon="add",
                    on_click=lambda _, idx=i: self._insert_after(idx),
                ).props("flat round size=xs dense")
                ui.select(
                    options=self._available_cols,
                    value=f["col"],
                    label="Column",
                    on_change=lambda e, idx=i: self._on_col_change(idx, e.value),
                ).props("dense options-dense filled").classes("flex-1")
                ui.select(
                    options=_FILTER_TYPES,
                    value=f["type"],
                    label="Type",
                    on_change=lambda e, idx=i: self._filters[idx].update(type=e.value),
                ).props("dense options-dense filled").style("width:100px")
                self._build_value_widget(i, f)
                ui.button(
                    icon="close",
                    on_click=lambda _, idx=i: self._remove(idx),
                ).props("flat round size=xs dense")

    def _on_col_change(self, idx: int, col: str) -> None:
        self._filters[idx]["col"] = col
        self._rebuild_ui()

    def _build_value_widget(self, i: int, f: dict) -> None:
        values = self._tab.unique_values(f["col"])
        if values is None:
            ui.input(
                value=f["value"],
                label="Value",
                on_change=lambda e, idx=i: self._filters[idx].update(value=e.value),
            ).props("dense filled").classes("flex-1")
            return

        options = list(values)
        current = f["value"]
        if current and current not in options:
            options.append(current)
        ui.select(
            options=options,
            value=current or None,
            label="Value",
            with_input=True,
            new_value_mode="add-unique",
            on_change=lambda e, idx=i: self._filters[idx].update(value=e.value or ""),
        ).props("dense options-dense filled").classes("flex-1")

    def _insert_after(self, idx: int) -> None:
        col = self._available_cols[0] if self._available_cols else ""
        self._filters.insert(idx + 1, {"col": col, "type": "=", "value": ""})
        self._rebuild_ui()

    def _remove(self, idx: int) -> None:
        if len(self._filters) > 1:
            self._filters.pop(idx)
        else:
            col = self._available_cols[0] if self._available_cols else ""
            self._filters = [{"col": col, "type": "=", "value": ""}]
        self._rebuild_ui()

    def _clear_all(self) -> None:
        self._filters = []
        self._tab.apply_filter([])
        self._dialog.close()

    def _apply(self) -> None:
        result = []
        for f in self._filters:
            if not f["col"] or not f["type"]:
                continue
            value = f["value"]
            if f["type"] == "in":
                value = [v.strip() for v in value.split(",") if v.strip()]
                if not value:
                    continue
            elif not value:
                continue
            elif f["type"] in ("<", ">", "<=", ">="):
                try:
                    value = float(value) if "." in value else int(value)
                except ValueError:
                    pass
            elif f["type"] in ("=", "!="):
                value = self._tab.coerce_equality_value(f["col"], value)
            result.append({"field": f["col"], "type": f["type"], "value": value})
        self._tab.apply_filter(result)
        self._dialog.close()
