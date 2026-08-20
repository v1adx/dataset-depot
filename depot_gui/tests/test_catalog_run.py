import pytest

from depot_gui.catalog import Catalog

from .test_catalog_index import depot_tree, write_dataset  # noqa: F401


async def test_run_reports_every_node_in_topological_order(depot_tree):
    seen = []
    await Catalog(depot_tree).run("staging:sales", on_node=lambda stage, d: seen.append((stage, d.dataset.key)))

    assert seen == [
        ("started", "source:records"),
        ("finished", "source:records"),
        ("started", "staging:sales"),
        ("finished", "staging:sales"),
    ]


async def test_run_returns_a_decision_per_node(depot_tree):
    decisions = await Catalog(depot_tree).run("staging:sales")
    assert [d.dataset.key for d in decisions] == ["source:records", "staging:sales"]


async def test_run_actually_produces_the_data(depot_tree):
    catalog = Catalog(depot_tree)
    await catalog.run("staging:sales")
    assert list(catalog.dataset("staging:sales").dataframe.columns) == ["id", "sold"]


async def test_a_second_run_skips_the_derived_node(depot_tree):
    catalog = Catalog(depot_tree)
    await catalog.run("staging:sales")
    decisions = await catalog.run("staging:sales")
    by_key = {d.dataset.key: d for d in decisions}
    assert by_key["staging:sales"].works is False


async def test_a_failure_propagates_and_the_node_is_still_reported(depot_tree, tmp_path):
    from .test_catalog_index import write_dataset as write

    write(depot_tree.datasets, "broken/raiser.py", '''
        """Raises in its transform."""
        from depot import Dataset

        def transform(d):
            raise RuntimeError("boom")

        dts = Dataset(name="raiser", type="broken", transforms=[transform], threshold=0)
    ''')

    seen = []
    with pytest.raises(RuntimeError, match="boom"):
        await Catalog(depot_tree).run("broken:raiser", on_node=lambda s, d: seen.append((s, d.dataset.key)))

    assert ("started", "broken:raiser") in seen


async def test_a_probe_failure_reddens_the_node_that_failed_not_the_one_before(depot_tree):
    """End to end, through the real runner: the bug this whole chain had.

    A probe runs before its node has a verdict, and a probe that reaches a
    remote source can time out. Until the runner announced a node before
    probing it, the failure surfaced right after the PREVIOUS node's
    "finished" — so the graph reddened a dataset that had just succeeded and
    left the broken one green. Wiring the real Catalog to the real painter is
    the only test that would have caught it; the two halves are each correct
    in isolation.
    """
    from depot_gui.pages import node_painter

    from .test_catalog_index import write_dataset

    write_dataset(depot_tree.datasets, "staging/downstream.py", '''
        """Its probe cannot reach the source."""
        from depot import Dataset
        from datasets.source.records import dts as records

        def probe(d):
            raise RuntimeError("the database is down")

        dts = Dataset(refs=[records], probe=probe)
    ''')

    class FakeFlow:
        def __init__(self):
            self.marks = []

        def mark(self, key, state, label=None, reason=""):
            self.marks.append((key, state))

        def apply(self, stage, decision):
            pass

    flow = FakeFlow()
    paint = node_painter(flow)

    # The three stages the way DatasetPanel._run assembles them: it brackets
    # the run, and "failed" is its own synthesis — Catalog.run never emits it,
    # it just raises.
    paint("run-started", None)
    with pytest.raises(RuntimeError, match="the database is down"):
        try:
            await Catalog(depot_tree).run("staging:downstream", on_node=paint)
        except Exception:
            paint("failed", None)
            raise

    assert flow.marks == [("staging:downstream", "error")]


async def test_the_decisions_render_as_the_cli_log(depot_tree):
    from depot.log import render

    decisions = await Catalog(depot_tree).run("staging:sales")
    text = render(decisions, target="staging:sales")
    assert "source:records" in text
    assert "staging:sales" in text
