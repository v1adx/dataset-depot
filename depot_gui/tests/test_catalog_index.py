import importlib
import sys
import textwrap

import pytest

from depot import config as depot_config
from depot_gui.catalog import Catalog


def write_dataset(root, path: str, body: str) -> None:
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    for parent in [file.parent, *file.parents]:
        if parent < root:
            break
        init = parent / "__init__.py"
        if parent.is_dir() and not init.exists():
            init.write_text("", encoding="utf-8")
    file.write_text(textwrap.dedent(body), encoding="utf-8")


@pytest.fixture()
def depot_tree(settings, tmp_path, monkeypatch):
    """A small depot on disk: a source, a derived dataset, and a broken one."""
    monkeypatch.syspath_prepend(str(tmp_path))
    depot_config.set_source(settings.datasets)
    depot_config.set_cache_dir(tmp_path / "cache")

    write_dataset(settings.datasets, "source/records.py", '''
        """Records from the source."""
        import pandas as pd
        from depot import Dataset

        def extract(d):
            d.dataframe = pd.DataFrame({"id": [1, 2]})

        dts = Dataset(extractors=[extract], threshold=0)
    ''')
    write_dataset(settings.datasets, "staging/sales.py", '''
        """Sales, computed from the records."""
        from depot import Dataset
        from datasets.source.records import dts as records

        def transform(d):
            d.dataframe = records.dataframe.assign(sold=True)

        dts = Dataset(refs=[records], transforms=[transform])
    ''')
    write_dataset(settings.datasets, "broken/oops.py", '''
        """Does not import."""
        import nonexistent_module_xyz
    ''')

    # This depot's root is called "datasets" for a reason: the modules inside it
    # refer to one another as "from datasets.source.records import ...", exactly
    # the way a real project's datasets do. sys.modules knows nothing of that:
    # once "datasets" (or "datasets.staging", and so on) is sitting there from
    # an earlier test — under a different tmp_path — Python stops consulting
    # sys.path to find a submodule and searches only inside the __path__ it has
    # already cached. A file that existed in no earlier test's depot then
    # becomes invisible altogether — not "different content" but
    # ModuleNotFoundError. So before each test we throw the whole "datasets"
    # subtree out of sys.modules and reset the finders' caches — the same device
    # depot/tests/test_registry.py and depot/tests/test_templates.py already
    # use. Do not delete this as redundant: it is what keeps
    # test_catalog_run.py::test_a_failure_propagates_and_the_node_is_still_reported
    # working, being the first test to write a module name the depot has never
    # seen before ("broken/raiser.py") — and see
    # test_a_brand_new_module_is_discoverable_even_if_datasets_was_cached_stale
    # below, which checks precisely this.
    for name in [n for n in sys.modules if n == "datasets" or n.startswith("datasets.")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    yield settings
    depot_config.reset()


def test_rows_lists_every_readable_dataset(depot_tree):
    keys = set(Catalog(depot_tree).rows()["key"])
    assert keys == {"source:records", "staging:sales"}


def test_rows_carries_the_refs_as_keys(depot_tree):
    rows = Catalog(depot_tree).rows().set_index("key")
    assert list(rows.loc["staging:sales", "refs"]) == ["source:records"]
    assert list(rows.loc["source:records", "refs"]) == []


def test_rows_carries_the_layer(depot_tree):
    rows = Catalog(depot_tree).rows().set_index("key")
    assert rows.loc["source:records", "layer"] == 0
    assert rows.loc["staging:sales", "layer"] == 1


def test_rows_carries_the_first_docstring_line(depot_tree):
    rows = Catalog(depot_tree).rows().set_index("key")
    assert rows.loc["source:records", "doc"] == "Records from the source."


def test_rows_carries_the_pipeline(depot_tree):
    rows = Catalog(depot_tree).rows().set_index("key")
    assert list(rows.loc["source:records", "pipeline"]) == ["extract"]
    assert list(rows.loc["staging:sales", "pipeline"]) == ["transform"]


def test_rows_fills_in_a_pipeline_column_an_older_index_lacks(depot_tree, monkeypatch):
    """A parquet written before the column existed, and no probe will notice.

    The index probe watches the dataset modules and other metafiles, never
    depot's own source, so adding a column to COLUMNS does not invalidate a
    stored table. Without the backfill build_nodes would raise KeyError on
    every start until something else happened to rebuild the index.
    """
    catalog = Catalog(depot_tree)
    original = catalog._index.load
    monkeypatch.setattr(
        catalog._index,
        "load",
        lambda *a, **kw: original(*a, **kw).drop(columns=["pipeline"]),
    )

    rows = catalog.rows()

    assert "pipeline" in rows.columns
    assert all(len(p) == 0 for p in rows["pipeline"])


def test_epoch_is_zero_for_a_dataset_that_never_ran(depot_tree):
    rows = Catalog(depot_tree).rows()
    assert (rows["epoch"] == 0.0).all()


def test_epoch_matches_the_dataset_clock_after_a_run(depot_tree):
    import time

    catalog = Catalog(depot_tree)
    dts = catalog.dataset("source:records")
    before = time.time()
    dts.pipeline()
    after = time.time()

    epoch = catalog.rows().set_index("key").loc["source:records", "epoch"]
    assert before - 1 <= epoch <= after + 1


def test_a_module_that_cannot_be_imported_is_reported(depot_tree):
    failures = Catalog(depot_tree).failures()
    assert len(failures) == 1
    path, why = next(iter(failures.items()))
    assert "oops.py" in path
    assert "nonexistent_module_xyz" in why


def test_a_broken_module_does_not_hide_the_others(depot_tree):
    assert len(Catalog(depot_tree).rows()) == 2


@pytest.fixture()
def _stale_datasets_package():
    """Fake what an earlier test really does leave behind in sys.modules:
    "datasets" and "datasets.staging" pointing somewhere else entirely — at a
    directory where the new file will never appear.

    It does not depend on depot_tree and does not clean up after itself: if
    depot_tree is intact, its own purge overwrites this before the body of the
    test sees a single import.
    """
    import types

    for name in ("datasets", "datasets.staging"):
        fake = types.ModuleType(name)
        fake.__path__ = [f"C:/nonexistent/{name.replace('.', '/')}"]
        sys.modules[name] = fake
    yield
    for name in ("datasets", "datasets.staging"):
        sys.modules.pop(name, None)


def test_a_brand_new_module_is_discoverable_even_if_datasets_was_cached_stale(
    _stale_datasets_package, depot_tree
):
    """A regression test for the bug test_catalog_run.py caught: without the
    sys.modules purge inside depot_tree, a file whose name no earlier test had
    written was not "the wrong content" but not found at all —
    ModuleNotFoundError instead of a dataset. Listing `_stale_datasets_package`
    before `depot_tree` in the parameters guarantees the fake is already in
    sys.modules by the time depot_tree's body reaches its purge, so the test
    strikes the fix itself rather than missing it.
    """
    write_dataset(depot_tree.datasets, "staging/brand_new.py", '''
        """No earlier test has written a file under this name."""
        import pandas as pd
        from depot import Dataset

        def extract(d):
            d.dataframe = pd.DataFrame({"x": [1]})

        dts = Dataset(name="brand_new", type="staging", extractors=[extract], threshold=0)
    ''')

    keys = set(Catalog(depot_tree).rows()["key"])
    assert "staging:brand_new" in keys


# --- the index as a dataset in its own right ---

def test_the_index_key_is_the_identity_it_was_built_with(depot_tree):
    assert Catalog(depot_tree).index_key == "system:index"


def test_the_index_is_not_one_of_the_datasets_it_lists(depot_tree):
    catalog = Catalog(depot_tree)
    assert catalog.index_key not in set(catalog.rows()["key"])


def test_the_index_resolves_to_the_very_object_that_built_the_table(depot_tree):
    """Not merely an equal one: the interface must run what it displays.

    registry.discover cannot find the index — it describes the depot rather
    than belonging to it — so `dataset()` has to answer for this one key
    itself, and answer with the same instance, or the panel would show one
    copy and the pipeline would run another.
    """
    catalog = Catalog(depot_tree)
    assert catalog.dataset(catalog.index_key) is catalog._index


def test_asking_the_registry_for_the_index_would_have_failed(depot_tree):
    """The reason the special case exists, pinned so it cannot be removed."""
    from depot import registry

    with pytest.raises(KeyError):
        registry.get("system:index", depot_tree.datasets)


def test_the_index_has_a_docstring_for_the_tooltip(depot_tree):
    catalog = Catalog(depot_tree)
    assert "depot" in catalog.doc(catalog.index_key).lower()


def test_running_the_index_produces_its_table(depot_tree):
    catalog = Catalog(depot_tree)
    catalog.dataset(catalog.index_key).pipeline()
    assert not catalog.dataset(catalog.index_key).dataframe.empty
