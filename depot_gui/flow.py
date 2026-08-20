"""The depot as a graph: the data cytoscape needs, and the widget around it.

A node is built from a row of the index, not from a live Dataset — at startup
not one dataset module has been imported, and the graph is drawn from the
parquet table. That is why these functions take a type, a name and a time
rather than an object.
"""
from __future__ import annotations

import json
import uuid

import pandas as pd
from nicegui import ui

from . import theme
from .catalog import _format_age
from .settings import Settings


def make_label(dataset_type: str, name: str, epoch: float) -> str:
    """Three lines: the type, the name, the age of the data.

    The age is formatted by the same format_age that depot info prints —
    otherwise "3h" in the graph and "3h" in the terminal would one day start
    meaning different things.
    """
    when = f"{_format_age(max(0.0, _now() - epoch))} ago" if epoch else "never"
    return f"{dataset_type}\n{name.replace('_', ' ')}\n{when}"


def make_pipeline(names) -> str:
    """The callables a run would use, in order, as one line.

    Names only. The run order is fixed — extractors, transforms, validators,
    extras — so a step's position already says which phase it belongs to, and
    spelling the phase out beside every name would only make the line longer.

    An underscore becomes a non-breaking space rather than an ordinary one.
    The tooltip wraps at its own width, and an ordinary space would let
    "Sync With Store" break across two lines, where the reader has to guess
    whether the halves are one step or two. This leaves the spaces around the
    arrows as the only places a chain can wrap, which is where it should.

    Spelled as an escape, never as the character itself: a literal
    non-breaking space in the source looks exactly like an ordinary one, and
    the next person to touch this line would have no way of telling.
    """
    return " → ".join(str(name).replace("_", "\u00a0").title() for name in names)


def build_nodes(rows: pd.DataFrame, positions: dict, settings: Settings) -> list[dict]:
    """Cytoscape nodes from the index table.

    A position comes from the saved layout, and in its absence is computed
    from the layer: x is the depth, y the order within the layer. After that
    dagre or a hand moves things about, and what was saved always beats what
    was computed.
    """
    nodes = []
    within_layer: dict[int, int] = {}
    for record in rows.sort_values(["layer", "type", "name"]).to_dict("records"):
        key = record["key"]
        layer = int(record["layer"])
        order = within_layer.get(layer, 0)
        within_layer[layer] = order + 1

        saved = positions.get(key, {})
        node = {
            "data": {
                "id": key,
                "label": make_label(record["type"], record["name"], float(record["epoch"])),
                "type": record["type"],
                "loaded": 1 if record["epoch"] else 0,
                "color": settings.color(record["type"]),
                "pipeline": make_pipeline(record["pipeline"]),
                "doc": record["doc"],
            },
            "position": {
                "x": saved.get("x", layer * theme.LAYER_SPACING),
                "y": saved.get("y", order * theme.NODE_SPACING),
            },
        }
        # A nested type is a helper dataset: drawn smaller, and dashed.
        if "/" in record["type"]:
            node["classes"] = "helper"
        nodes.append(node)
    return nodes


def build_edges(rows: pd.DataFrame) -> list[dict]:
    """Edges from the refs column. A ref outside the table is dropped in
    silence — an arrow into a node that is not on the canvas has nowhere to
    go."""
    known = set(rows["key"])
    edges = []
    for record in rows.to_dict("records"):
        target = record["key"]
        for source in record["refs"]:
            if source not in known:
                continue
            edges.append({
                "data": {"id": f"{source}->{target}", "source": source, "target": target}
            })
    return edges


def _now() -> float:
    import time

    return time.time()


class FlowGraph:
    def __init__(self):
        self._id = f"cy{uuid.uuid4().hex[:8]}"
        self._node_select_handlers: list = []
        self._positions_handlers: list = []

        ui.add_head_html(
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/cytoscape/3.29.2/cytoscape.min.js"></script>\n'
            '<script src="https://cdnjs.cloudflare.com/ajax/libs/dagre/0.8.5/dagre.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/cytoscape-dagre@2.5.0/cytoscape-dagre.js"></script>'
        )

        with ui.element("div").classes("w-full h-full").style("position:relative;") as self.container:
            ui.html(f'<div id="{self._id}" style="position:absolute;inset:0;"></div>')

        ui.on("cy_node_click", self._on_node_click)
        ui.on("cy_positions_changed", self._on_positions_changed)

    def _on_node_click(self, e) -> None:
        data = e.args if not isinstance(e.args, list) else e.args[0]
        for handler in self._node_select_handlers:
            handler(data)

    def _on_positions_changed(self, e) -> None:
        data = e.args if not isinstance(e.args, list) else e.args[0]
        for handler in self._positions_handlers:
            handler(data)

    def on_node_select(self, handler):
        self._node_select_handlers.append(handler)
        return self

    def on_positions_changed(self, handler):
        self._positions_handlers.append(handler)
        return self

    def refresh_labels(self, rows) -> None:
        """Refresh every label at once — the periodic tick, not a run event."""
        updates = {
            r["key"]: make_label(r["type"], r["name"], float(r["epoch"]))
            for r in rows.to_dict("records")
        }
        ui.run_javascript(
            f"(function(){{var c=document.getElementById('{self._id}');"
            f"if(!c||!c._cy)return;"
            f"var u={json.dumps(updates, ensure_ascii=False)};"
            f"Object.keys(u).forEach(function(id){{"
            f"var n=c._cy.getElementById(id);"
            f"if(!n.empty())n.data('label',u[id]);}});"
            f"}})();"
        )

    def mark(self, key: str, state: str, label: str | None = None, reason: str = "") -> None:
        """Repaint one node. The states are running, done, idle, error."""
        payload = json.dumps(
            {"key": key, "state": state, "label": label, "reason": reason},
            ensure_ascii=False,
        )
        ui.run_javascript(
            f"(function(){{var c=document.getElementById('{self._id}');"
            f"if(!c||!c._cy)return;"
            f"var p={payload};var n=c._cy.getElementById(p.key);"
            f"if(n.empty())return;"
            f"n.removeClass('running error');"
            f"if(p.state==='running')n.addClass('running');"
            f"if(p.state==='error')n.addClass('error');"
            f"if(p.label){{n.data('label',p.label);n.data('loaded',1);}}"
            f"if(p.reason)n.data('reason',p.reason);"
            f"}})();"
        )

    def apply(self, stage: str, decision) -> None:
        """A run event → how the node looks.

        There is exactly one reading of it: a node that has been started is
        lit; a node that did some work gets a new label; a node that was
        skipped puts its reason in the tooltip. The error state is set by the
        caller — only the caller knows that the run fell over.
        """
        dts = decision.dataset
        key = dts.key
        if stage == "started":
            self.mark(key, "running")
            return
        if decision.works:
            self.mark(key, "done", label=make_label(dts.type, dts.name, dts.timestamp))
        else:
            self.mark(key, "idle", reason=decision.reason)

    def init(self, nodes: list[dict], edges: list[dict], has_positions: bool = True) -> None:
        elements_json = json.dumps(nodes + edges, ensure_ascii=False)
        node_w = theme.NODE_WIDTH
        node_h = theme.NODE_HEIGHT
        element_id = self._id
        layout_js = "{ name: 'preset' }" if has_positions else "{ name: 'dagre', rankDir: 'LR', nodeSep: 60, rankSep: 120 }"

        js = f"""
(async function() {{
    var container = null;
    for (var i = 0; i < 30; i++) {{
        container = document.getElementById('{element_id}');
        if (container && container.offsetHeight > 0) break;
        await new Promise(r => setTimeout(r, 100));
    }}
    if (!container) {{ console.error('Cytoscape: container not found'); return; }}
    if (container.offsetHeight === 0) {{
        var panel = container.closest('.q-splitter__panel');
        var h = (panel && panel.offsetHeight > 0) ? panel.offsetHeight : window.innerHeight;
        container.style.height = h + 'px';
    }}

    var cy = cytoscape({{
        container: container,
        elements: {elements_json},
        wheelSensitivity: 0.2,
        style: [
            {{
                selector: 'node',
                style: {{
                    'label': 'data(label)',
                    'text-valign': 'center',
                    'text-halign': 'center',
                    'background-color': 'data(color)',
                    'background-opacity': 0.6,
                    'border-color': 'data(color)',
                    'border-opacity': 1,
                    'border-width': 2,
                    'width': {node_w},
                    'height': {node_h},
                    'shape': 'roundrectangle',
                    'color': 'black',
                    'font-size': '12px',
                    'text-wrap': 'wrap',
                    'text-max-width': '185px',
                    'white-space': 'pre'
                }}
            }},
            {{
                selector: 'node:selected',
                style: {{ 'background-opacity': 0.9, 'border-color': '#000000' }}
            }},
            {{
                selector: 'node[loaded = 0]',
                style: {{ 'opacity': 0.45 }}
            }},
            {{
                selector: 'node.helper',
                style: {{
                    'width': {theme.SECONDARY_NODE_WIDTH},
                    'height': {theme.SECONDARY_NODE_HEIGHT},
                    'border-style': 'dashed',
                    'font-size': '9px',
                    'color': 'black'
                }}
            }},
            {{
                selector: 'node.running',
                style: {{
                    'border-color': '{theme.RUNNING_BORDER_COLOR}',
                    'border-width': {theme.RUNNING_BORDER_WIDTH},
                    'border-opacity': 1
                }}
            }},
            {{
                selector: 'node.error',
                style: {{ 'background-color': '{theme.ERROR_COLOR}', 'border-color': '{theme.ERROR_COLOR}' }}
            }},
            {{
                selector: 'edge',
                style: {{
                    'width': 2,
                    'line-color': '#94A3B8',
                    'target-arrow-color': '#94A3B8',
                    'target-arrow-shape': 'triangle',
                    'curve-style': 'unbundled-bezier',
                    'control-point-distances': 30,
                    'control-point-weights': 0.5
                }}
            }}
        ],
        layout: {layout_js}
    }});

    container._cy = cy;

    var tip = document.createElement('div');
    tip.style.cssText = 'position:absolute;display:none;z-index:20;'
        + 'pointer-events:none;max-width:{theme.TOOLTIP_MAX_WIDTH}px;'
        + 'padding:6px 8px;border-radius:4px;background:rgba(255,255,255,0.96);'
        + 'border:1px solid #CBD5E1;box-shadow:0 2px 6px rgba(0,0,0,0.15);'
        + 'font-size:11px;line-height:1.35;color:#0F172A;white-space:pre-wrap;';
    container.appendChild(tip);

    function tipSection(text) {{
        var section = document.createElement('div');
        // textContent, never innerHTML: a docstring may hold a '<' and would
        // otherwise take the rest of the box with it.
        section.textContent = text;
        return section;
    }}

    function tipRule() {{
        var rule = document.createElement('div');
        rule.style.cssText = 'border-top:1px solid #E2E8F0;margin:4px 0;';
        return rule;
    }}

    function hideTip() {{ tip.style.display = 'none'; }}

    cy.on('mouseover', 'node', function(e) {{
        var node = e.target;
        var parts = [node.data('doc'), node.data('pipeline'), node.data('reason')]
            .filter(function(part) {{ return part; }});
        if (!parts.length) {{ hideTip(); return; }}

        tip.textContent = '';
        parts.forEach(function(part, i) {{
            if (i) tip.appendChild(tipRule());
            tip.appendChild(tipSection(part));
        }});
        tip.style.display = 'block';

        // Measured only once it is displayed, or offsetWidth is zero and the
        // box lands half a width to the left of where it belongs.
        var point = node.renderedPosition();
        var left = point.x - tip.offsetWidth / 2;
        var top = point.y + node.renderedHeight() / 2 + 8;
        left = Math.max(4, Math.min(left, container.clientWidth - tip.offsetWidth - 4));
        top = Math.max(4, Math.min(top, container.clientHeight - tip.offsetHeight - 4));
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
    }});

    cy.on('mouseout', 'node', hideTip);
    cy.on('drag', 'node', hideTip);
    cy.on('pan zoom', hideTip);

    var pulse = 0;
    setInterval(function() {{
        pulse = (pulse + 1) % 2;
        cy.nodes('.running').style('border-opacity', pulse ? 1 : 0.35);
    }}, 400);

    window.addEventListener('resize', function() {{
        var panel = container.closest('.q-splitter__panel');
        var h = (panel && panel.offsetHeight > 0) ? panel.offsetHeight : window.innerHeight;
        container.style.height = h + 'px';
        cy.resize();
    }});
    if (!{str(has_positions).lower()}) {{
        cy.one('layoutstop', function() {{
            var positions = {{}};
            cy.nodes().forEach(function(n) {{
                positions[n.id()] = {{ x: Math.round(n.position('x')), y: Math.round(n.position('y')) }};
            }});
            window.emitEvent('cy_positions_changed', positions);
        }});
    }}

    cy.on('tap', 'node', function(e) {{
        var node = e.target;
        window.emitEvent('cy_node_click', {{ id: node.id(), type: node.data('type') }});
    }});

    var dragTimer = null;
    cy.on('dragfree', 'node', function() {{
        clearTimeout(dragTimer);
        dragTimer = setTimeout(function() {{
            var positions = {{}};
            cy.nodes().forEach(function(n) {{
                positions[n.id()] = {{ x: Math.round(n.position('x')), y: Math.round(n.position('y')) }};
            }});
            window.emitEvent('cy_positions_changed', positions);
        }}, 500);
    }});
}})();
"""
        ui.run_javascript(js)
