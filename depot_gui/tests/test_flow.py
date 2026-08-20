import time

import pandas as pd
import pytest

from depot_gui import theme
from depot_gui.flow import build_edges, build_nodes, make_label, make_pipeline


def rows(*records) -> pd.DataFrame:
    columns = ["key", "type", "name", "layer", "refs", "epoch", "pipeline", "doc"]
    return pd.DataFrame(list(records), columns=columns)


def row(key, type_, name, layer=0, refs=(), epoch=0.0, pipeline=(), doc=""):
    return [key, type_, name, layer, list(refs), epoch, list(pipeline), doc]


# --- make_label ---

def test_label_of_a_dataset_that_never_ran_says_never():
    assert make_label("source", "records", 0.0) == "source\nrecords\nnever"


def test_label_replaces_underscores_in_the_name():
    assert make_label("staging", "daily_sales", 0.0).splitlines()[1] == "daily sales"


def test_label_of_a_fresh_dataset_reports_an_age():
    assert make_label("source", "records", time.time()).endswith("0s ago")


def test_label_of_an_hour_old_dataset_reports_hours():
    assert make_label("source", "records", time.time() - 3600 * 3).endswith("3h ago")


# --- make_pipeline ---

def test_pipeline_joins_the_names_with_arrows():
    assert make_pipeline(["fetch_clients", "transform", "export_to_csv"]) == (
        "Fetch\u00a0Clients → Transform → Export\u00a0To\u00a0Csv"
    )


def test_a_single_step_needs_no_arrow():
    assert make_pipeline(["extract"]) == "Extract"


def test_a_name_is_held_together_by_non_breaking_spaces():
    """The tooltip wraps at its own width, and a name split across two lines
    reads as two steps."""
    assert make_pipeline(["sync_with_store"]) == "Sync\u00a0With\u00a0Store"


def test_only_the_arrows_offer_somewhere_to_wrap():
    chain = make_pipeline(["fetch_clients", "sync_with_store"])
    # One ordinary space either side of the one arrow, and nowhere else.
    assert chain.count(" ") == 2


def test_a_dataset_with_no_callables_has_an_empty_pipeline():
    assert make_pipeline([]) == ""


# --- build_nodes ---

def test_node_id_is_the_dataset_key(settings):
    nodes = build_nodes(rows(row("source:records", "source", "records")), {}, settings)
    assert nodes[0]["data"]["id"] == "source:records"


def test_node_takes_the_colour_of_its_type(settings):
    nodes = build_nodes(rows(row("source:records", "source", "records")), {}, settings)
    assert nodes[0]["data"]["color"] == "#F8BE00"


def test_unknown_type_gets_the_fallback_colour(settings):
    nodes = build_nodes(rows(row("exotic:thing", "exotic", "thing")), {}, settings)
    assert nodes[0]["data"]["color"] == settings.UNKNOWN_COLOR


def test_a_dataset_that_never_ran_is_dimmed(settings):
    nodes = build_nodes(rows(row("source:records", "source", "records")), {}, settings)
    assert nodes[0]["data"]["loaded"] == 0


def test_a_dataset_that_ran_is_not_dimmed(settings):
    nodes = build_nodes(rows(row("a:b", "a", "b", epoch=time.time())), {}, settings)
    assert nodes[0]["data"]["loaded"] == 1


def test_a_nested_type_is_marked_as_a_helper(settings):
    nodes = build_nodes(rows(row("store/helper:categories", "store/helper", "categories")), {}, settings)
    assert nodes[0].get("classes") == "helper"


def test_a_top_level_type_is_not_a_helper(settings):
    nodes = build_nodes(rows(row("store:invoices", "store", "invoices")), {}, settings)
    assert "classes" not in nodes[0]


def test_layout_spreads_by_layer_and_position(settings):
    frame = rows(
        row("a:one", "a", "one", layer=0),
        row("b:two", "b", "two", layer=1),
        row("b:three", "b", "three", layer=1),
    )
    by_id = {n["data"]["id"]: n for n in build_nodes(frame, {}, settings)}
    assert by_id["a:one"]["position"] == {"x": 0, "y": 0}
    # x is the layer; y is the position within it, and within a layer nodes are
    # ordered by (type, name) — so "three" precedes "two" regardless of the
    # order the index happened to list them in.
    assert by_id["b:three"]["position"] == {"x": theme.LAYER_SPACING, "y": 0}
    assert by_id["b:two"]["position"] == {"x": theme.LAYER_SPACING, "y": theme.NODE_SPACING}


def test_saved_positions_win_over_the_layout(settings):
    frame = rows(row("a:one", "a", "one"))
    nodes = build_nodes(frame, {"a:one": {"x": 123, "y": 456}}, settings)
    assert nodes[0]["position"] == {"x": 123, "y": 456}


def test_node_carries_the_pipeline_for_the_tooltip(settings):
    frame = rows(row("source:clients", "source", "clients",
                     pipeline=["fetch_clients", "transform"]))
    nodes = build_nodes(frame, {}, settings)
    assert nodes[0]["data"]["pipeline"] == "Fetch\u00a0Clients → Transform"


def test_node_carries_the_docstring_for_the_tooltip(settings):
    frame = rows(row("source:clients", "source", "clients", doc="The client list."))
    nodes = build_nodes(frame, {}, settings)
    assert nodes[0]["data"]["doc"] == "The client list."


# --- build_edges ---

def test_an_edge_per_ref(settings):
    frame = rows(
        row("source:records", "source", "records"),
        row("staging:sales", "staging", "sales", layer=1, refs=["source:records"]),
    )
    edges = build_edges(frame)
    assert len(edges) == 1
    assert edges[0]["data"] == {
        "id": "source:records->staging:sales",
        "source": "source:records",
        "target": "staging:sales",
    }


def test_no_refs_means_no_edges(settings):
    assert build_edges(rows(row("a:one", "a", "one"))) == []


def test_a_ref_outside_the_table_is_dropped(settings):
    frame = rows(row("staging:sales", "staging", "sales", refs=["gone:missing"]))
    assert build_edges(frame) == []


# --- node states ---

def test_apply_started_marks_the_node_running(quiet_nicegui, settings):
    from unittest.mock import Mock

    from depot import Dataset, Decision
    from depot_gui.flow import FlowGraph

    graph = FlowGraph()
    graph.mark = Mock()
    graph.apply("started", Decision(dataset=Dataset(name="records", type="source")))
    graph.mark.assert_called_once_with("source:records", "running")


def test_apply_finished_on_work_refreshes_the_label(quiet_nicegui, settings):
    from unittest.mock import Mock

    from depot import Dataset, Decision
    from depot_gui.flow import FlowGraph

    dts = Dataset(name="records", type="source")
    dts.timestamp = time.time()
    decision = Decision(dataset=dts, transform=True)

    graph = FlowGraph()
    graph.mark = Mock()
    graph.apply("finished", decision)
    key, state = graph.mark.call_args[0][:2]
    assert (key, state) == ("source:records", "done")


def test_apply_finished_on_a_skip_only_clears_the_border(quiet_nicegui, settings):
    from unittest.mock import Mock

    from depot import Dataset, Decision
    from depot_gui.flow import FlowGraph

    decision = Decision(dataset=Dataset(name="records", type="source"))
    decision.reasons.append("timer 12m of 60m")

    graph = FlowGraph()
    graph.mark = Mock()
    graph.apply("finished", decision)
    assert graph.mark.call_args[0][1] == "idle"
    assert graph.mark.call_args.kwargs["reason"] == "timer 12m of 60m"
