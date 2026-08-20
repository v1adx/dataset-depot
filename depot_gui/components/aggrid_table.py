from __future__ import annotations

import pandas as pd
from nicegui import ui

from depot import Dataset

from .. import theme
from .frames import records

LABEL = "AgGrid"
ICON = "table_view"


def _col_filter(dtype) -> str:
    kind = getattr(dtype, "kind", "")
    if kind in ("i", "u", "f"):
        return "agNumberColumnFilter"
    if kind == "M":
        return "agDateColumnFilter"
    return "agTextColumnFilter"


def column_defs(df: pd.DataFrame) -> list[dict]:
    return [
        {
            "field": c,
            "filter": _col_filter(df[c].dtype),
            "sortable": True,
            "minWidth": theme.TABLE_COLUMN_MIN_WIDTH,
            "maxWidth": theme.TABLE_COLUMN_MAX_WIDTH,
        }
        for c in df.columns
    ]


def render(dts: Dataset, view_id: str, config: dict) -> None:
    """AgGrid remembers nothing between openings, so view_id and config go
    unused here — the signature is the one every view component answers to."""
    df = dts.dataframe
    if df.empty:
        ui.label("No data").classes("text-gray-400 text-sm m-auto")
        return
    ui.aggrid({
        "columnDefs": column_defs(df),
        "rowData": records(df),
        "defaultColDef": {"sortable": True, "filter": True, "resizable": True},
        "pagination": True,
        "paginationPageSize": 50,
        "rowHeight": 22,
    }).classes("w-full flex-1").style("height: calc(100% - 52px);")
