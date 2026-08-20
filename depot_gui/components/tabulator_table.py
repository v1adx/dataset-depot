from __future__ import annotations

import json
import uuid

import pandas as pd
from nicegui import app as nicegui_app
from nicegui import ui

from depot import Dataset

from ..settings import active
from ..state import StateFile


_FORMATTER_CONFIGS: dict[str, dict] = {
    "text":   {"formatter": "plaintext"},
    "number": {"formatter": "number",   "formatterParams": {"thousand": ",", "precision": False}, "hozAlign": "right"},
    "date":   {"formatter": "datetime", "formatterParams": {"inputFormat": "iso", "outputFormat": "yyyy-MM-dd"}, "hozAlign": "center"},
    "time":   {"formatter": "datetime", "formatterParams": {"inputFormat": "iso", "outputFormat": "HH:mm:ss"}, "hozAlign": "center"},
    "money":  {"formatter": "money",    "formatterParams": {"thousand": ",", "precision": 2, "symbol": ""}, "hozAlign": "right"},
    "link":   {"formatter": "link"},
    "tick":   {"formatter": "tickCross", "hozAlign": "center"},
    "textarea": {"formatter": "textarea"},
    "JSON": {"formatter": "function", "formatterParams": {"function": "return JSON.stringify(value, null, 2);"}},
    "Array": {"formatter": "function", "formatterParams": {"function": "return Array.isArray(value) ? value.join(', ') : value;"}},
}

_FORMAT_TYPES: list[str] = ["text", "number", "date", "time", "money", "link", "tick", "textarea", "JSON", "Array"]
_AGGREGATIONS: list[str | None] = [None, "count", "sum", "avg", "min", "max", "unique"]

_DEFAULT_TABLE_OPTIONS: dict = {
    "layout": "fitData",
    "movableColumns": True,
    "resizableColumns": True,
    "persistence": False,
    "nestedFieldSeparator": False,
    "rowHeight": 24,
    # Tabulator's default (columnCalcs: true) hides the table-level bottomCalc
    # row when groupBy is active. "table" keeps that row visible whether the
    # table is grouped or not.
    "columnCalcs": "table",
}


# ---------------------------------------------------------------------------
# Pure helpers (testable without NiceGUI)
# ---------------------------------------------------------------------------

def _serialize_df(df: pd.DataFrame) -> list[dict]:
    records = []
    for _, row in df.iterrows():
        clean: dict = {}
        for col in df.columns:
            val = row[col]
            kind = df[col].dtype.kind
            try:
                if pd.isna(val):
                    clean[col] = None
                    continue
            except (TypeError, ValueError):
                pass
            if kind in ("i", "u"):
                clean[col] = int(val)
            elif kind == "f":
                clean[col] = float(val)
            elif kind == "M":
                clean[col] = val.isoformat()
            else:
                clean[col] = val if isinstance(val, (str, bool)) else str(val)
        records.append(clean)
    return records


def _unique_values(data: list[dict], field: str, limit: int = 10) -> list[str] | None:
    """Return sorted distinct non-null values for `field`, stringified.

    Returns None if there are `limit` or more distinct values, or none at all.
    """
    seen: set = set()
    for row in data:
        val = row.get(field)
        if val is None:
            continue
        seen.add(val)
        if len(seen) >= limit:
            return None
    if not seen:
        return None
    try:
        ordered = sorted(seen)
    except TypeError:
        ordered = sorted(seen, key=str)
    return [str(v).lower() if isinstance(v, bool) else str(v) for v in ordered]


def _coerce_equality_value(data: list[dict], field: str, value: str) -> str | bool:
    """Coerce a "true"/"false" filter value back to a real bool when `field`
    genuinely holds Python bool values.

    Tabulator's "="/"!=" filters use JS loose equality (rowVal == filterVal).
    A field serialized as an actual JSON boolean never loose-equals the string
    "true"/"false" (only a real boolean does), so the string must be converted
    back before being sent to setFilter. Matching is case-insensitive since
    the value may come from free-typed text, not just the dropdown. Left
    untouched for fields that genuinely contain "true"/"false" as strings.
    """
    lowered = value.lower()
    if lowered not in ("true", "false"):
        return value
    if any(isinstance(row.get(field), bool) for row in data):
        return lowered == "true"
    return value


def _sync_columns(df: pd.DataFrame, col_config: dict) -> tuple[dict, bool]:
    """Reconcile col_config with actual DataFrame columns.

    Removes orphaned columns, adds new ones with defaults, reorders to match df.
    Returns (updated col_config, changed).
    """
    df_cols = list(df.columns)
    changed = False

    for col in list(col_config.keys()):
        if col not in set(df_cols):
            del col_config[col]
            changed = True

    first_non_numeric_done = any(
        col_config.get(c, {}).get("bottomCalc") == "count"
        for c in df_cols
        if not pd.api.types.is_numeric_dtype(df[c].dtype) and c in col_config
    )
    for col in df_cols:
        if col not in col_config:
            if pd.api.types.is_numeric_dtype(df[col].dtype):
                col_config[col] = {"bottomCalc": "sum"}
            elif not first_non_numeric_done:
                col_config[col] = {"bottomCalc": "count"}
                first_non_numeric_done = True
            else:
                col_config[col] = {}
            changed = True

    ordered = {col: col_config[col] for col in df_cols if col in col_config}
    if list(col_config.keys()) != list(ordered.keys()):
        changed = True
    return ordered, changed


def _build_column_defs(df: pd.DataFrame, bottom_calcs: dict[str, str | None] | None = None) -> list[dict]:
    """Build Tabulator column defs with grouping for dot-notation fields."""
    groups: dict[str, list[dict]] = {}
    order: list[tuple] = []
    bc = bottom_calcs or {}

    for col in df.columns:
        col_def: dict = {"field": col, "responsive": 1}
        calc = bc.get(col)
        if calc is not None:
            col_def["bottomCalc"] = calc

        if "." in col:
            prefix, suffix = col.split(".", 1)
            col_def["title"] = suffix
            if prefix not in groups:
                groups[prefix] = []
                order.append(("group", prefix))
            groups[prefix].append(col_def)
        else:
            col_def["title"] = col
            order.append(("col", col_def))

    result: list[dict] = []
    for kind, val in order:
        if kind == "group":
            result.append({"title": val, "columns": groups[val]})
        else:
            result.append(val)
    return result


def _apply_column_overrides(columns: list[dict], col_config: dict) -> list[dict]:
    """Merge col_config props (except bottomCalc) into column defs recursively."""
    result = []
    for col in columns:
        if "columns" in col:
            col = dict(col, columns=_apply_column_overrides(col["columns"], col_config))
        elif col.get("field") in col_config:
            overrides = {k: v for k, v in col_config[col["field"]].items() if k != "bottomCalc"}
            if overrides:
                col = {**col, **overrides}
        result.append(col)
    return result


def _extract_bottom_calcs(columns: list[dict]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for col in columns:
        if "columns" in col:
            result.update(_extract_bottom_calcs(col["columns"]))
        elif "field" in col:
            result[col["field"]] = col.get("bottomCalc") or None
    return result


def _parse_entry(entry: dict) -> tuple[dict, list, dict, list]:
    """Returns (table_options, groupBy, col_config, columns_order)."""
    return (
        entry.get("tableOptions", {}),
        entry.get("groupBy", []),
        entry.get("columns", {}),
        entry.get("columnsOrder", []),
    )


def _state() -> StateFile:
    return StateFile(active().state / "tabulator.json")


# ---------------------------------------------------------------------------
# FastAPI endpoint — registered once at import time
# ---------------------------------------------------------------------------

@nicegui_app.post("/_tabulator_state/{key:path}")
async def _tabulator_state_endpoint(key: str, payload: dict):
    entry = _state().get(key, {})
    new_entry = {}
    if "tableOptions" in entry:
        new_entry["tableOptions"] = entry["tableOptions"]
    if "columnsOrder" in entry:
        new_entry["columnsOrder"] = entry["columnsOrder"]
    new_entry["groupBy"] = payload.get("groupBy", [])
    existing_cols = entry.get("columns", {})
    new_entry["columns"] = {
        field: {**existing_cols.get(field, {}), **js_state}
        for field, js_state in payload.get("columns", {}).items()
    }
    _state().set(key, new_entry)


# ---------------------------------------------------------------------------
# Component
# ---------------------------------------------------------------------------

class TabulatorTable:
    def __init__(self):
        self._dts_key: str = ""
        self._data: list[dict] = []
        self._tabulator_config: dict = {}
        self._container_id: str = ""

        ui.add_head_html(
            '<link rel="stylesheet" href="https://unpkg.com/tabulator-tables@6.3.1/dist/css/tabulator.min.css">\n'
            '<script type="text/javascript" src="https://unpkg.com/tabulator-tables@6.3.1/dist/js/tabulator.min.js"></script>'
            '<script src="https://cdn.jsdelivr.net/npm/luxon@3.7.2/build/global/luxon.min.js"></script>'
        )

    def refresh(self, dts: Dataset) -> None:
        df = dts.dataframe
        if df.empty:
            self._dts_key = ""
            self._data = []
            self._tabulator_config = {}
            return

        self._dts_key = dts.key
        self._data = _serialize_df(df)

        entry = _state().get(self._dts_key, {})
        table_options, groupby, col_config, columns_order = _parse_entry(entry)

        col_config, changed = _sync_columns(df, col_config)
        if changed:
            new_entry = {k: v for k, v in entry.items() if k != "columns"}
            new_entry["columns"] = col_config
            if groupby:
                new_entry["groupBy"] = groupby
            _state().set(self._dts_key, new_entry)

        bottom_calcs = {f: v["bottomCalc"] for f, v in col_config.items() if "bottomCalc" in v}
        cols = _build_column_defs(df, bottom_calcs)
        cols = _apply_column_overrides(cols, col_config)
        if columns_order:
            rank = {f: i for i, f in enumerate(columns_order)}
            def _col_key(c: dict) -> int:
                if "field" in c:
                    return rank.get(c["field"], len(columns_order))
                sub = c.get("columns", [])
                ranks = [rank.get(x.get("field", ""), len(columns_order)) for x in sub]
                return min(ranks) if ranks else len(columns_order)
            cols = sorted(cols, key=_col_key)
        self._tabulator_config = {
            **_DEFAULT_TABLE_OPTIONS,
            **table_options,
            "columns": cols,
            "groupBy": groupby,
        }

    def build(self) -> None:
        if not self._data:
            ui.label("No data").classes("text-gray-400 text-sm m-auto")
            return

        container_id = f"tabulator-{uuid.uuid4().hex[:8]}"
        self._container_id = container_id

        ui.html(f'<div id="{container_id}" style="width:100%;"></div>')
        config_json = json.dumps(self._tabulator_config, ensure_ascii=False)
        data_json   = json.dumps(self._data,              ensure_ascii=False)
        state_url   = json.dumps(f"/_tabulator_state/{self._dts_key}")

        ui.run_javascript(f"""(async function() {{
    var el = null;
    for (var i = 0; i < 30; i++) {{
        el = document.getElementById('{container_id}');
        if (el) break;
        await new Promise(r => setTimeout(r, 100));
    }}
    if (!el) return;

    // The div lives inside NiceGUI's padded content wrapper. Because it is
    // width:100%, its left offset equals that wrapper's (uniform) padding, so
    // reuse it as the right/bottom pad — otherwise the table overflows past
    // the wrapper's right and bottom padding.
    var rect = el.getBoundingClientRect();
    var pad = rect.left;
    var tableWidth  = window.innerWidth  - rect.left - pad;
    var tableHeight = window.innerHeight - rect.top  - pad;
    el.style.width  = tableWidth + "px";

    var config   = {config_json};
    var stateUrl = {state_url};
    var groupBy  = config.groupBy || [];

    config.data   = {data_json};
    config.height = tableHeight;
    config.groupHeader = function(value, count, data, group) {{
        var field = group.getField ? group.getField() : '';
        var label = (value !== null && value !== undefined && value !== '') ? value : '—';
        return field + ': ' + label + ' (' + count + ')';
    }};

    var table = new Tabulator(el, config);
    el._tabulator = table;

    var initialized = false;

    table.on("tableBuilt", function() {{
        setTimeout(function() {{ initialized = true; }}, 500);
    }});

    function getGroupBy() {{
        var gb = table.options.groupBy;
        if (!gb) return [];
        return Array.isArray(gb) ? gb : [gb];
    }}

    function saveState() {{
        if (!initialized) return;
        var leafCols = table.getColumns().filter(function(c) {{ return !!c.getField(); }});
        var columns = {{}};
        leafCols.forEach(function(c) {{ columns[c.getField()] = {{ width: c.getWidth() }}; }});
        var snapshot = {{ groupBy: getGroupBy(), columns: columns }};
        fetch(stateUrl, {{
            method: 'POST',
            headers: {{'Content-Type': 'application/json'}},
            body: JSON.stringify(snapshot)
        }});
    }}

    function debounce(fn, ms) {{
        var t;
        return function() {{ clearTimeout(t); t = setTimeout(fn, ms); }};
    }}
    var saveStateDebounced = debounce(saveState, 300);

    table.on("columnMoved",   saveStateDebounced);
    table.on("columnResized", saveStateDebounced);
    table.on("groupAdded",    saveState);
    table.on("groupDeleted",  saveState);
}})();""")

    def apply_filter(self, filters: list[dict]) -> None:
        filters_json = json.dumps(filters, ensure_ascii=False)
        ui.run_javascript(f"""
            var el = document.getElementById('{self._container_id}');
            var table = el && el._tabulator;
            if (table) {{
                table.setFilter({filters_json});
            }}
        """)

    def unique_values(self, field: str) -> list[str] | None:
        """Return distinct non-null values for `field` if there are fewer than 10, else None."""
        return _unique_values(self._data, field)

    def coerce_equality_value(self, field: str, value: str) -> str | bool:
        """Coerce a "True"/"False" filter value back to bool for genuinely-boolean fields."""
        return _coerce_equality_value(self._data, field, value)

    def get_raw_config(self) -> tuple[list[str], dict]:
        """Return (groupby_list, col_config) from the persisted JSON config."""
        entry = _state().get(self._dts_key, {})
        _, groupby, col_config, _ = _parse_entry(entry)
        return groupby, col_config


    def apply_settings(self, dts: Dataset, groupby: list[str], columns_order: list[str], col_states: dict) -> None:
        """Merge settings from dialog into config, persist, and update the live table."""
        entry = _state().get(self._dts_key, {})
        _, _, col_config, _ = _parse_entry(entry)

        for name, state in col_states.items():
            cfg_entry = col_config.setdefault(name, {})
            if not state.get("visible", True):
                cfg_entry["visible"] = False
            else:
                cfg_entry.pop("visible", None)
            if state.get("bottomCalc"):
                cfg_entry["bottomCalc"] = state["bottomCalc"]
            else:
                cfg_entry.pop("bottomCalc", None)
            for k in ("formatter", "formatterParams", "hozAlign"):
                cfg_entry.pop(k, None)
            fmt = state.get("formatter_type")
            if fmt and fmt in _FORMATTER_CONFIGS:
                cfg_entry.update(_FORMATTER_CONFIGS[fmt])

        new_entry = {k: v for k, v in entry.items() if k not in ("groupBy", "columns", "columnsOrder")}
        new_entry["groupBy"] = groupby
        new_entry["columnsOrder"] = columns_order
        new_entry["columns"] = col_config
        _state().set(self._dts_key, new_entry)

        self.refresh(dts)

        all_order = groupby + columns_order
        rank = {f: i for i, f in enumerate(all_order)}

        def _sort_key(entry: dict) -> int:
            if "field" in entry:
                return rank.get(entry["field"], len(all_order))
            sub = entry.get("columns", [])
            ranks = [rank.get(c.get("field", ""), len(all_order)) for c in sub]
            return min(ranks) if ranks else len(all_order)

        self._tabulator_config["columns"] = sorted(
            self._tabulator_config["columns"], key=_sort_key
        )

        cols_json = json.dumps(self._tabulator_config["columns"], ensure_ascii=False)
        groupby_json = json.dumps(self._tabulator_config.get("groupBy", []), ensure_ascii=False)
        container_id = self._container_id
        ui.run_javascript(f"""
            var el = document.getElementById('{container_id}');
            var table = el && el._tabulator;
            if (table) {{
                table.setColumns({cols_json});
                var gb = {groupby_json};
                if (gb.length) {{ table.setGroupBy(gb.length === 1 ? gb[0] : gb); }}
                else {{ table.setGroupBy([]); }}
            }}
        """)
