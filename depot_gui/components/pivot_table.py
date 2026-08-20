"""The pivot on pivottable.js.

Kept alongside Perspective rather than replaced by it: it is the lighter of the
two, drag-and-drop and nothing else, and a dataset that only needs one grouping
opens it faster. Its config is the seven fields pivotUI hands back on every
refresh, which is the whole of what it knows how to restore.
"""
from __future__ import annotations

import json
import uuid

import pandas as pd
from nicegui import ui

from depot import Dataset

from .frames import split_datetime_columns

LABEL = "Pivot"
ICON = "pivot_table_chart"

HEAD = (
    '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/jqueryui/1.13.2/themes/base/jquery-ui.min.css">\n'
    '<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.23.0/pivot.min.css">\n'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/jquery/3.7.1/jquery.min.js"></script>\n'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/jqueryui/1.13.2/jquery-ui.min.js"></script>\n'
    '<script src="https://cdnjs.cloudflare.com/ajax/libs/pivottable/2.23.0/pivot.min.js"></script>'
)


def rows(df: pd.DataFrame) -> list[dict]:
    """The frame as pivottable.js wants it: datetime parts added, everything a
    string. A pivot groups by discrete values, so a float 1.0 next to an int 1
    would otherwise open two groups where the data has one."""
    return split_datetime_columns(df).astype(str).to_dict("records")


def install() -> None:
    ui.add_head_html(HEAD)


def render(dts: Dataset, view_id: str, config: dict) -> None:
    df = dts.dataframe
    if df.empty:
        ui.label("No data").classes("text-gray-400 text-sm m-auto")
        return

    div_id = f"pivot-{uuid.uuid4().hex[:8]}"
    ui.html(
        f'<div id="{div_id}" style="width:100%;height:100%;overflow:auto;padding:8px;"></div>'
    )

    data_json = json.dumps(rows(df), ensure_ascii=False)
    config_json = json.dumps(config, ensure_ascii=False)
    key_json = json.dumps(dts.key)
    view_json = json.dumps(view_id)

    ui.run_javascript(f"""(async function() {{
    var el = null;
    for (var i = 0; i < 30; i++) {{
        el = document.getElementById('{div_id}');
        if (el) break;
        await new Promise(r => setTimeout(r, 100));
    }}
    if (!el) return;
    var opts = Object.assign({{}}, {config_json}, {{
        onRefresh: function(cfg) {{
            window.emitEvent('view_config_changed', {{
                key: {key_json},
                view: {view_json},
                config: {{
                    rows: cfg.rows,
                    cols: cfg.cols,
                    vals: cfg.vals,
                    aggregatorName: cfg.aggregatorName,
                    rendererName: cfg.rendererName,
                    rowOrder: cfg.rowOrder,
                    colOrder: cfg.colOrder
                }}
            }});
        }}
    }});
    $( el ).pivotUI({data_json}, opts);
}})();""")
