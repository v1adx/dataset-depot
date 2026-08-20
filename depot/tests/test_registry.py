import importlib
import sys

import pytest

from depot import config, registry
from depot.dataset import Dataset


@pytest.fixture
def depot_tree(tmp_path, monkeypatch):
    """A depot with a diamond in it: two branches over one source."""
    root = tmp_path / "tinydepot"
    (root / "raw").mkdir(parents=True)
    (root / "marts").mkdir()
    for folder in ("", "raw", "marts"):
        (root / folder / "__init__.py").write_text("", encoding="utf-8")

    (root / "raw" / "source.py").write_text(
        '"""The one source.\n\nSecond line, which the index should not show.\n"""\n'
        "import pandas as pd\n"
        "from depot import Dataset\n"
        "dts = Dataset(threshold=0, extractors=[lambda d: setattr(d, 'dataframe', pd.DataFrame({'n': [1]}))])\n",
        encoding="utf-8")
    for side in ("left", "right"):
        (root / "marts" / f"{side}.py").write_text(
            f'"""The {side} branch."""\n'
            "from depot import Dataset\n"
            "from tinydepot.raw.source import dts as source\n"
            "dts = Dataset(refs=[source], transforms=[lambda d: setattr(d, 'dataframe', source.dataframe)])\n",
            encoding="utf-8")
    (root / "marts" / "top.py").write_text(
        '"""Both branches joined."""\n'
        "from depot import Dataset\n"
        "from tinydepot.marts.left import dts as left\n"
        "from tinydepot.marts.right import dts as right\n"
        "dts = Dataset(refs=[left, right], transforms=[lambda d: setattr(d, 'dataframe', left.dataframe)])\n",
        encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    config.set_source(root)
    config.set_cache_dir(tmp_path / "cache")
    for name in [n for n in sys.modules if n == "tinydepot" or n.startswith("tinydepot.")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    yield root
    config.reset()


def test_finds_every_declared_dataset(depot_tree):
    scan = registry.discover()
    assert sorted(scan.found) == ["marts:left", "marts:right", "marts:top", "raw:source"]
    assert scan.failures == {}


def test_records_where_a_dataset_was_declared(depot_tree):
    found = registry.discover().found["raw:source"]
    assert found.module == "tinydepot.raw.source"
    assert found.variable == "dts"
    assert found.doc.startswith("The one source.")


def test_a_broken_module_is_handed_back_not_swallowed(depot_tree):
    (depot_tree / "raw" / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    importlib.invalidate_caches()

    scan = registry.discover()
    assert "raw:source" in scan.found          # the others still arrive
    assert any("broken.py" in p for p in scan.failures)
    assert "boom" in " ".join(scan.failures.values())


def test_a_lookup_blames_the_broken_module_not_the_name(depot_tree):
    # The dataset is there; its module is one import away from working. Saying
    # "no such dataset" sends the reader hunting for the wrong thing.
    (depot_tree / "raw" / "source.py").write_text(
        "from tinydepot.raw.absent import dts\n", encoding="utf-8")
    for name in [n for n in sys.modules if n.startswith("tinydepot")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    with pytest.raises(KeyError, match="could not be imported"):
        registry.get("source")


def test_get_by_full_key_and_by_bare_name(depot_tree):
    assert registry.get("raw:source").key == "raw:source"
    assert registry.get("source").key == "raw:source"


def test_an_ambiguous_bare_name_says_so(depot_tree):
    (depot_tree / "raw" / "left.py").write_text(
        '"""A second left."""\nfrom depot import Dataset\ndts = Dataset()\n', encoding="utf-8")
    importlib.invalidate_caches()

    with pytest.raises(KeyError, match="ambiguous"):
        registry.get("left")


def test_an_unknown_name_says_so(depot_tree):
    with pytest.raises(KeyError, match="no dataset"):
        registry.get("absent")


def test_layers_put_sources_at_the_bottom(depot_tree):
    depth = registry.layers(registry.datasets())
    assert depth["raw:source"] == 0
    assert depth["marts:left"] == depth["marts:right"] == 1
    assert depth["marts:top"] == 2


def test_dependants_count_the_datasets_that_name_you(depot_tree):
    counts = registry.dependants(registry.datasets())
    assert counts["raw:source"] == 2  # both branches
    assert counts["marts:top"] == 0


def test_layers_survive_a_cycle():
    # The graph refuses to run a cycle, but the index still has to describe a
    # depot that contains one rather than recursing until the stack gives out.
    a = Dataset(name="a", type="t")
    b = Dataset(name="b", type="t", refs=[a])
    a.refs = [b]

    depth = registry.layers([a, b])
    assert set(depth) == {"t:a", "t:b"}


def test_an_unrelated_lookup_still_mentions_the_broken_modules(depot_tree):
    # The name asked for is genuinely absent, but something in the depot would
    # not import — and that may well be why. Saying so beats a flat denial.
    (depot_tree / "raw" / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    importlib.invalidate_caches()

    with pytest.raises(KeyError, match="could not be imported"):
        registry.get("nowhere")


def test_nesting_no_longer_folds_two_files_onto_one_identity(depot_tree):
    # The pair the fold used to merge. The type keeps its folders now, so a
    # flat directory and a nested one are two identities and two cache paths.
    (depot_tree / "raw_extra").mkdir()
    (depot_tree / "raw_extra" / "__init__.py").write_text("", encoding="utf-8")
    (depot_tree / "raw_extra" / "thing.py").write_text(
        '"""Flat."""\nfrom depot import Dataset\ndts = Dataset(threshold=60)\n', encoding="utf-8")

    (depot_tree / "raw" / "extra").mkdir()
    (depot_tree / "raw" / "extra" / "__init__.py").write_text("", encoding="utf-8")
    (depot_tree / "raw" / "extra" / "thing.py").write_text(
        '"""Nested."""\nfrom depot import Dataset\ndts = Dataset(threshold=60)\n', encoding="utf-8")
    importlib.invalidate_caches()

    scan = registry.discover()
    assert "raw_extra:thing" in scan.found
    assert "raw/extra:thing" in scan.found
    assert scan.failures == {}


def test_two_modules_claiming_one_identity_are_reported(depot_tree):
    # A path cannot produce a clash any more, but a hand-written type can, from
    # anywhere in the tree. Whichever module is found second used to be dropped
    # without a word, and the two would have shared a parquet.
    (depot_tree / "marts" / "impostor.py").write_text(
        '"""Claims the source\'s identity."""\n'
        "from depot import Dataset\n"
        'dts = Dataset(name="source", type="raw", threshold=60)\n', encoding="utf-8")
    importlib.invalidate_caches()

    scan = registry.discover()
    assert "raw:source" in scan.found                     # one of the two stands
    assert any("already taken" in why for why in scan.failures.values())


def test_a_root_outside_sys_path_is_still_discoverable(tmp_path):
    # The datasets root may live anywhere: DEPOT_SOURCE is a path, not a promise
    # that the project happens to sit one directory above it. Refs are plain
    # imports, so the root has to be importable — and making it so is the
    # library's job, not a line of PYTHONPATH the caller has to guess at.
    root = tmp_path / "elsewhere" / "faraway"
    (root / "raw").mkdir(parents=True)
    for folder in ("", "raw"):
        (root / folder / "__init__.py").write_text("", encoding="utf-8")
    (root / "raw" / "thing.py").write_text(
        '"""A dataset nobody put on the path."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(threshold=60)\n",
        encoding="utf-8")

    assert str(root.parent) not in sys.path      # the premise of the test
    config.set_source(root)

    scan = registry.discover()
    assert "raw:thing" in scan.found
    assert scan.failures == {}


def test_the_root_is_put_on_the_path_once(tmp_path):
    # Twice would be harmless but sloppy, and a long-lived UI process calls
    # discover() on every refresh.
    root = tmp_path / "elsewhere" / "faraway"
    (root / "raw").mkdir(parents=True)
    for folder in ("", "raw"):
        (root / folder / "__init__.py").write_text("", encoding="utf-8")
    (root / "raw" / "thing.py").write_text(
        '"""A dataset."""\nfrom depot import Dataset\ndts = Dataset(threshold=60)\n',
        encoding="utf-8")
    config.set_source(root)

    registry.discover()
    registry.discover()
    assert sys.path.count(str(root.parent)) == 1


def test_an_imported_dataset_is_not_a_second_declaration(depot_tree):
    """Importing a ref under a dts-prefixed name does not declare it again.

    Reading a ref as ``ref.dataframe`` means the object has to be imported, and
    the importer names it whatever reads best. A name that happens to start with
    ``dts`` must not hand the importer an identity it never declared — nor cost
    the real module its place in the index.
    """
    (depot_tree / "marts" / "consumer.py").write_text(
        '"""Reads the source under a dts-prefixed name."""\n'
        "from depot import Dataset\n"
        "from tinydepot.raw.source import dts as dts_source\n"
        "dts = Dataset(refs=[dts_source],\n"
        "              transforms=[lambda d: setattr(d, 'dataframe', dts_source.dataframe)])\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    scan = registry.discover()

    assert scan.failures == {}
    assert scan.found["raw:source"].module == "tinydepot.raw.source"
    assert "marts:consumer" in scan.found
