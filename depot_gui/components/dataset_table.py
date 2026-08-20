from __future__ import annotations

from nicegui import ui

from depot import Dataset

from .. import theme
from ..views import COLUMNS_ID, ViewStore


TABLE_HEADER_INJECTED_HTML = r"""
<q-tr :props="props">
    <q-th auto-width v-if="props.cols.some(col => col.classes && col.classes.includes('hidden'))" />
    <q-th v-for="col in props.cols" :key="col.name" :props="props"
            :class="col.headerClasses">
        {{ col.label }}
    </q-th>
</q-tr>
"""

TABLE_BODY_INJECTED_HTML = (r"""
<q-tr :props="props">
    <q-td auto-width v-if="props.cols.some(col => col.classes && col.classes.includes('hidden'))"
            style="height: __ROW_HEIGHT__; padding: 0 8px;">
        <q-btn size="sm" color="primary" round flat dense
            @click="props.expand = !props.expand"
            :icon="props.expand ? 'remove' : 'add'" />
    </q-td>
    <q-td v-for="col in props.cols" :key="col.name" :props="props"
            :class="col.classes" style="height: __ROW_HEIGHT__; padding: 0 8px;">
        {{ col.value }}
    </q-td>
</q-tr>
<q-tr v-show="props.expand" :props="props">
    <q-td colspan="100%" style="padding: 8px 16px;">
        <div class="text-sm flex flex-wrap gap-x-6 gap-y-1">
            <template v-for="col in props.cols" :key="col.name">
                <span v-if="col.classes && col.classes.includes('hidden')"
                        style="text-wrap-mode: wrap;">
                    <strong>{{ col.label }}:</strong> {{ props.row[col.field] }}
                </span>
            </template>
        </div>
    </q-td>
</q-tr>
""").replace("__ROW_HEIGHT__", f"{theme.TABLE_ROW_HEIGHT}px")




class DatasetTable:
    def __init__(self, store: ViewStore):
        self._dts_key: str | None = None
        self._store = store

        with ui.column().classes("w-full gap-2") as self.container:
            with ui.row().classes("gap-2 items-center w-full"):
                with ui.button(icon="view_column").props("outline dense"):
                    with ui.menu():
                        with ui.column().classes("gap-0 p-2") as self._switches_slot:
                            pass

                filter_input = (
                    ui.input(placeholder="Filter...")
                    .props("dense clearable")
                    .classes("flex-1")
                )

            self._table = ui.table(
                columns=[],
                rows=[],
                row_key="_idx",
                pagination={
                    "rowsPerPage": theme.TABLE_DEFAULT_PAGE_SIZE,
                    "rowsPerPageOptions": theme.TABLE_PAGE_SIZE_OPTIONS,
                },
            ).classes("w-full no-shadow")

            filter_input.bind_value_to(self._table, "filter")

            self._table.add_slot("header", TABLE_HEADER_INJECTED_HTML)

            self._table.add_slot("body", TABLE_BODY_INJECTED_HTML)

    # ------------------------------------------------------------------
    # Public
    # ------------------------------------------------------------------

    def refresh(self, dts: Dataset) -> None:
        df = dts.dataframe
        if df.empty:
            self._table.columns = []
            self._table.rows = []
            self._table.update()
            self._rebuild_switches()
            return

        col_names = list(df.columns)
        self._dts_key = dts.key

        visible = self._store.config(self._dts_key, COLUMNS_ID).get("visible")
        visible_set = set(visible) if visible is not None else set(col_names)

        self._table.columns = [
            {
                "name": c,
                "label": c.replace("_", " ").title(),
                "field": c,
                "sortable": True,
                "align": "left",
                "classes": "" if c in visible_set else "hidden",
                "headerClasses": "" if c in visible_set else "hidden",
                "style": (
                    f"min-width: {theme.TABLE_COLUMN_MIN_WIDTH}px;"
                    f" max-width: {theme.TABLE_COLUMN_MAX_WIDTH}px;"
                    " overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                ),
                "headerStyle": (
                    f"min-width: {theme.TABLE_COLUMN_MIN_WIDTH}px;"
                    f" max-width: {theme.TABLE_COLUMN_MAX_WIDTH}px;"
                    " overflow: hidden; text-overflow: ellipsis; white-space: nowrap;"
                ),
            }
            for c in col_names
        ]
        self._table.rows = [
            {"_idx": i, **{c: str(v) for c, v in row.items()}}
            for i, row in enumerate(df.to_dict("records"))
        ]
        self._table.update()
        self._rebuild_switches()

    # ------------------------------------------------------------------
    # Column visibility
    # ------------------------------------------------------------------

    def _rebuild_switches(self) -> None:
        self._switches_slot.clear()
        with self._switches_slot:
            for col in self._table.columns:
                ui.switch(
                    col["label"],
                    value=col.get("classes", "") != "hidden",
                    on_change=lambda e, c=col: self._toggle_column(c, e.value),
                )

    def _toggle_column(self, col: dict, visible: bool) -> None:
        col["classes"] = "" if visible else "hidden"
        col["headerClasses"] = "" if visible else "hidden"
        self._table.update()
        if self._dts_key:
            visible_names = [
                c["name"] for c in self._table.columns if c.get("classes", "") != "hidden"
            ]
            self._store.save_config(
                self._dts_key, COLUMNS_ID, {"visible": visible_names}
            )
