from __future__ import annotations

from nicegui import ui

from depot import Dataset

from .tabulator_table import TabulatorTable, _AGGREGATIONS, _FORMAT_TYPES, _FORMATTER_CONFIGS


def _detect_formatter_type(col_cfg: dict) -> str:
    fmt = col_cfg.get("formatter")
    if not fmt or fmt == "plaintext":
        return ""
    if fmt == "datetime":
        out = col_cfg.get("formatterParams", {}).get("outputFormat", "")
        return "date" if "yyyy" in out else "time"
    for key, entry in _FORMATTER_CONFIGS.items():
        if entry.get("formatter") == fmt:
            return key
    return ""


class TabulatorPageSettings:
    def __init__(self, tab: TabulatorTable, dts: Dataset) -> None:
        self._tab = tab
        self._dts = dts
        self._col_states: dict[str, dict] = {}
        self._groupby_order: list[str] = []
        self._columns_order: list[str] = []
        self._id_to_col: dict[int, str] = {}
        self._groupby_col_id: int = 0
        self._columns_col_id: int = 0

        with ui.dialog() as self._dialog:
            with ui.card().classes("w-full").style("min-width:620px;max-width:860px;"):
                with ui.row().classes("items-center justify-between w-full"):
                    ui.label("Table Settings").classes("font-bold text-base")
                    ui.button(icon="close", on_click=self._dialog.close).props("flat round size=sm")
                self._content_row = ui.row().classes("w-full gap-4")
                with ui.row().classes("w-full justify-end gap-2 q-mt-sm"):
                    ui.button("Cancel", on_click=self._dialog.close).props("flat")
                    ui.button("Apply", on_click=self._apply).props("color=primary")

        ui.add_head_html('<script src="https://cdn.jsdelivr.net/npm/sortablejs@1.15.0/Sortable.min.js"></script>')

    def open(self) -> None:
        groupby, col_config = self._tab.get_raw_config()
        all_cols = list(col_config.keys())
        self._groupby_order = [c for c in groupby if c in col_config]
        self._columns_order = [c for c in all_cols if c not in self._groupby_order]

        self._col_states = {
            name: {
                "visible": cfg.get("visible", True),
                "bottomCalc": cfg.get("bottomCalc") or "",
                "formatter_type": _detect_formatter_type(cfg),
            }
            for name, cfg in col_config.items()
        }

        self._content_row.clear()
        with self._content_row:
            self._build_content()

        self._dialog.open()

    def _build_content(self) -> None:
        self._id_to_col = {}

        with ui.column().classes("gap-4 w-full"):
            with ui.card().props("flat bordered").classes("w-full"):
                ui.label("Group By").classes("font-bold text-sm q-mb-xs")
                with ui.column().classes("gap-2 w-full") as groupby_col:
                    for name in self._groupby_order:
                        card = self._build_column_card(name)
                        self._id_to_col[card.id] = name
            self._groupby_col_id = groupby_col.id

            with ui.card().props("flat bordered").classes("w-full"):
                ui.label("Columns").classes("font-bold text-sm q-mb-xs")
                with ui.column().classes("gap-2 w-full") as columns_col:
                    for name in self._columns_order:
                        card = self._build_column_card(name)
                        self._id_to_col[card.id] = name
            self._columns_col_id = columns_col.id

        ui.run_javascript(f"""
            setTimeout(function() {{
                var gb = getHtmlElement({self._groupby_col_id});
                var cols = getHtmlElement({self._columns_col_id});
                if (gb && cols) {{
                    new Sortable(gb, {{group: 'shared', animation: 150, handle: '.drag-handle'}});
                    new Sortable(cols, {{group: 'shared', animation: 150, handle: '.drag-handle'}});
                }}
            }}, 200);
        """)

    def _build_column_card(self, name: str) -> ui.card:
        state = self._col_states[name]
        with ui.card().props("flat bordered").classes("w-full !p-1") as card:
            with ui.row().classes("items-center gap-2 w-full"):
                ui.icon("drag_indicator").classes("text-gray-400 cursor-grab drag-handle")
                ui.checkbox(
                    name.upper(),
                    value=state["visible"],
                    on_change=lambda e, n=name: self._col_states[n].update(visible=e.value),
                ).classes("flex-1 text-sm")
                ui.select(
                    options=[""] + [v for v in _AGGREGATIONS if v],
                    value=state["bottomCalc"],
                    with_input=False,
                    label="Aggregation",
                    on_change=lambda e, n=name: self._col_states[n].update(bottomCalc=e.value),
                ).props("dense options-dense filled").style("width:120px")
                ui.select(
                    options=[""] + _FORMAT_TYPES,
                    value=state["formatter_type"],
                    label="Type",
                    on_change=lambda e, n=name: self._col_states[n].update(formatter_type=e.value),
                ).props("dense options-dense filled").style("width:120px")
        return card

    async def _apply(self) -> None:
        result = await ui.run_javascript(rf"""
            function getIds(niceId) {{
                var el = getHtmlElement(niceId);
                if (!el) return [];
                return Array.from(el.children).map(function(c) {{
                    var m = c.id && c.id.match(/^c(\d+)$/);
                    return m ? parseInt(m[1]) : null;
                }}).filter(function(n) {{ return n !== null; }});
            }}
            return {{gb: getIds({self._groupby_col_id}), cols: getIds({self._columns_col_id})}};
        """)
        gb_ids = result.get("gb", []) if isinstance(result, dict) else []
        col_ids = result.get("cols", []) if isinstance(result, dict) else []
        groupby = [self._id_to_col[i] for i in gb_ids if i in self._id_to_col]
        columns = [self._id_to_col[i] for i in col_ids if i in self._id_to_col]
        if not groupby and not columns:
            groupby = self._groupby_order
            columns = self._columns_order

        col_states = {
            name: {
                "visible": state["visible"],
                "bottomCalc": state["bottomCalc"] or None,
                "formatter_type": state["formatter_type"] or None,
            }
            for name, state in self._col_states.items()
        }
        self._tab.apply_settings(self._dts, groupby, columns, col_states)
        self._dialog.close()
