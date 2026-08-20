"""The pivot, on Perspective instead of pivottable.js.

The thing pivottable.js could not do is more than one aggregation at a time:
its config holds a single `aggregatorName` for the whole table, so "sum of
revenue next to count of orders" — the ordinary Excel case — has no
representation in it. Perspective's does: `columns` is the list of measures on
show and `aggregates` maps each one to its own function, so the two live side
by side.

What is loaded here is the CDN build, imported from a `<script type="module">`
exactly as this component's predecessor pulled in jQuery: no bundler, no build
step, and the `.wasm` binaries come bootstrapped. The cost is that the module
finishes loading asynchronously and `ui.run_javascript` does not wait for it,
so build() polls for `window.depotPerspective` the same way it polls for its
own element.

State is the viewer's own `save()` token, kept per view rather than per
dataset: two Perspective views on one dataset are two different layouts. It is
opaque on purpose — it carries pivots, measures, aggregations, filters,
sorting, expressions, the plugin and the theme, and Perspective's own
`restore()` is the only thing that has to understand it. Writing it is not this
module's job: the token goes out as a `view_config_changed` event and
`ViewsBar` puts it away.
"""
from __future__ import annotations

import json
import re
import uuid
from string import Template

import pandas as pd
from nicegui import ui

from depot import Dataset

from .frames import records, split_datetime_columns

CDN = "https://cdn.jsdelivr.net/npm/@perspective-dev"
VERSION = "5.2.0"

LABEL = "Perspective"
ICON = "insights"

# pandas dtype kind -> one of Perspective's six column types. Anything else
# (object, category, timedelta) is a string: an explicit schema is what keeps
# the "01" of a Month column from being inferred as the number 1.
SCHEMA_TYPES = {
    "i": "integer",
    "u": "integer",
    "f": "float",
    "b": "boolean",
    "M": "datetime",
}

# A viewer with no saved token opens with its sidebar out, because a bare grid
# gives no hint that the columns are draggable. The theme is pinned rather than
# left to "whichever stylesheet loaded first". All of it is overwritten by the
# first token the user's own layout produces.
#
# The single null is an empty slot in the Columns pane, and it is what keeps a
# new dataset from opening with every one of its columns already measured. An
# empty list will not do: restore() reads it as "no opinion" and the plugin
# fills it back in with the whole schema.
DEFAULT_CONFIG = {"settings": True, "theme": "Pro Light", "columns": [None]}

HEAD = Template("""
<link rel="stylesheet" crossorigin="anonymous"
      href="$cdn/viewer@$version/dist/css/themes.css">
<style>
  perspective-viewer { width: 100%; height: 100%; min-height: 0; flex: 1 1 auto; }
</style>
<script type="module">
import "$cdn/viewer@$version/dist/cdn/perspective-viewer.js";
import "$cdn/viewer-datagrid@$version/dist/cdn/perspective-viewer-datagrid.js";
import "$cdn/viewer-charts@$version/dist/cdn/perspective-viewer-charts.js";
import perspective from "$cdn/client@$version/dist/cdn/perspective.js";

const worker = await perspective.worker();
const mounted = [];

// Closing the dialog takes the viewer out of the document but leaves the
// WebAssembly Table behind it alive, and a Table is looked up by name — so
// re-opening the same dataset would collide with its own leftovers. Both go,
// viewer first: a Table refuses to delete while a viewer still holds it.
async function dropDetached() {
    for (let i = mounted.length - 1; i >= 0; i--) {
        if (mounted[i].viewer.isConnected) continue;
        const entry = mounted.splice(i, 1)[0];
        try { await entry.viewer.delete(); } catch (e) { console.warn(e); }
        try { await entry.table.delete(); } catch (e) { console.warn(e); }
    }
}

window.depotPerspective = async function (host, spec) {
    await dropDetached();
    const table = await worker.table(spec.schema, { name: spec.name });
    await table.update(spec.rows);
    const viewer = document.createElement("perspective-viewer");
    host.appendChild(viewer);
    mounted.push({ viewer: viewer, table: table });
    // The Client, not the Table. load(table) is a shorthand that selects the
    // table itself, and a selected table draws immediately — with every
    // column the plugin defaults to, a frame before our own layout replaces
    // it. Binding the client selects nothing, so the restore() below is the
    // only draw that happens.
    //
    // A token names the Table it was taken from. This one is named after the
    // dataset, so a token and its table agree across restarts — but a token
    // saved before that was true would send restore() looking for a table
    // that no longer exists, hence the override.
    await viewer.load(worker);
    try {
        await viewer.restore(Object.assign({}, spec.config, { table: spec.name }));
    } catch (e) {
        // A saved layout outliving the columns it names — a pipeline dropped
        // one — would otherwise leave the viewer blank with nothing on screen
        // to recover from. The dataset opens as if it were new instead, and
        // the next layout the user builds overwrites the stale token.
        console.warn("perspective: stale layout, opening at defaults", e);
        // The error overlay has to go first. Left standing it keeps the viewer
        // from settling, so the flush() below never returns — and the listener
        // after it never gets attached, which would silently stop this
        // dataset's layout from being saved at all.
        viewer.resetError();
        await viewer.restore(Object.assign({}, spec.reset, { table: spec.name }));
    }
    // restore() fires perspective-config-update itself. Subscribing after it
    // has settled keeps the viewer from reporting our own token back to us.
    await viewer.flush();
    let pending = null;
    viewer.addEventListener("perspective-config-update", function () {
        clearTimeout(pending);
        pending = setTimeout(async function () {
            window.emitEvent("view_config_changed", {
                key: spec.key,
                view: spec.view,
                config: await viewer.save(),
            });
        }, 400);
    });
};
</script>
""").substitute(cdn=CDN, version=VERSION)


def table_name(key: str) -> str:
    """A dataset key as a Perspective table name.

    Derived rather than generated so that a token saved in one session still
    names a table that exists in the next one.
    """
    return re.sub(r"[^0-9a-zA-Z]+", "_", key)


def schema_of(df: pd.DataFrame) -> dict[str, str]:
    """A frame's columns as Perspective's six types.

    An explicit schema is what keeps the "01" of a Month column from being
    inferred as the number 1.
    """
    return {c: SCHEMA_TYPES.get(df[c].dtype.kind, "string") for c in df.columns}


def config_or_default(config: dict) -> dict:
    """An empty config is a view nobody has laid out yet. The copy matters:
    two new views must not end up sharing one dict."""
    return config or dict(DEFAULT_CONFIG)


def install() -> None:
    ui.add_head_html(HEAD)


def render(dts: Dataset, view_id: str, config: dict) -> None:
    df = dts.dataframe
    if df.empty:
        ui.label("No data").classes("text-gray-400 text-sm m-auto")
        return
    df = split_datetime_columns(df)

    host_id = f"perspective-{uuid.uuid4().hex[:8]}"
    ui.html(
        f'<div id="{host_id}" '
        'style="width:100%;height:100%;min-height:0;display:flex;"></div>'
    ).classes("w-full flex-1")

    spec = json.dumps(
        {
            "key": dts.key,
            "view": view_id,
            "name": table_name(dts.key),
            "schema": schema_of(df),
            "rows": records(df),
            "config": config_or_default(config),
            "reset": DEFAULT_CONFIG,
        },
        ensure_ascii=False,
        default=str,
    )

    ui.run_javascript(f"""(async function() {{
    var host = null, mount = null;
    for (var i = 0; i < 100; i++) {{
        host = host || document.getElementById('{host_id}');
        mount = mount || window.depotPerspective;
        if (host && mount) break;
        await new Promise(r => setTimeout(r, 100));
    }}
    if (!host) return;
    if (!mount) {{
        host.textContent = 'Perspective did not load — is the CDN reachable?';
        return;
    }}
    await mount(host, {spec});
}})();""")
