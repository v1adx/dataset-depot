import importlib
import os
import sys

import pandas as pd
import pytest

from depot import cache, config
from depot.runner import run
from depot.templates import DatasetIndex, DatasetFromFile, DatasetFromLastFile, DatasetFromSetOfFiles, DatasetListOfFiles


@pytest.fixture(autouse=True)
def _cache(tmp_path):
    # Outside the watched folder on purpose: the probe watches directory mtimes,
    # so a cache written inside one would keep retriggering its own dataset.
    config.set_cache_dir(tmp_path / "cache")
    yield
    config.reset()


@pytest.fixture
def src(tmp_path):
    """The folder under watch — a sibling of the cache, never its parent."""
    path = tmp_path / "src"
    path.mkdir()
    return path


def _csv(path, rows):
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _age(path, seconds):
    """Backdate a file, so 'newest' and 'unchanged' are decidable in a test."""
    stamp = os.stat(path).st_mtime - seconds
    os.utime(path, (stamp, stamp))


# --- DatasetFromSetOfFiles -------------------------------------------------------

def test_reads_and_concatenates_every_match(src):
    _csv(src / "a.csv", [{"n": 1}])
    _csv(src / "b.csv", [{"n": 2}])

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    assert sorted(d.load()["n"]) == [1, 2]


def test_an_empty_glob_is_empty_not_an_error(src):
    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    assert d.load().empty


def test_unchanged_files_do_not_reread(src):
    path = _csv(src / "a.csv", [{"n": 1}])
    _age(path, 60)

    reads = []
    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t",
                          reader=lambda p, **kw: reads.append(p) or pd.read_csv(p))
    run(d)
    run(d)
    assert len(reads) == 1


def test_a_touched_file_is_reread(src):
    path = _csv(src / "a.csv", [{"n": 1}])
    _age(path, 60)

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    run(d)

    _csv(src / "a.csv", [{"n": 9}])
    run(d)
    assert d.dataframe["n"].tolist() == [9]


def test_a_new_file_is_picked_up(src):
    _age(_csv(src / "a.csv", [{"n": 1}]), 60)

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    run(d)
    assert d.dataframe.shape[0] == 1

    _csv(src / "b.csv", [{"n": 2}])
    run(d)
    assert d.dataframe.shape[0] == 2


def test_a_deleted_file_is_noticed(src):
    # The newest mtime cannot see a deletion — it does not move, and may move
    # backwards. The containing directory's mtime is what catches it.
    _csv(src / "a.csv", [{"n": 1}])
    _csv(src / "b.csv", [{"n": 2}])
    _age(src / "a.csv", 60)
    _age(src / "b.csv", 60)

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    run(d)
    assert sorted(d.dataframe["n"]) == [1, 2]

    (src / "b.csv").unlink()
    run(d)
    assert d.dataframe["n"].tolist() == [1]


def test_deleting_the_last_file_empties_the_dataset(src):
    _csv(src / "a.csv", [{"n": 1}])
    _age(src / "a.csv", 60)

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t")
    run(d)
    assert not d.dataframe.empty

    (src / "a.csv").unlink()
    run(d)
    assert d.dataframe.empty


def test_processor_runs_per_file(src):
    _csv(src / "a.csv", [{"n": 1}])
    _csv(src / "b.csv", [{"n": 2}])

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t",
                          processor=lambda df: df.assign(rows=len(df)))
    assert d.load()["rows"].tolist() == [1, 1]


def test_reader_kwargs_reach_the_reader(src):
    (src / "a.csv").write_text("skip me\nn\n1\n", encoding="utf-8")

    d = DatasetFromSetOfFiles(path=str(src / "*.csv"), name="files", type="t",
                          reader_kwargs={"skiprows": 1})
    assert d.load()["n"].tolist() == [1]


def test_several_patterns_are_one_dataset(src):
    _csv(src / "a.csv", [{"n": 1}])
    _csv(src / "b.tsv", [{"n": 2}])

    d = DatasetFromSetOfFiles(path=[str(src / "*.csv"), str(src / "*.tsv")], name="files", type="t")
    assert sorted(d.load()["n"]) == [1, 2]


# --- DatasetFromLastFile ----------------------------------------------------------

def test_only_the_newest_file_is_read(src):
    _age(_csv(src / "old.csv", [{"n": 1}]), 600)
    _csv(src / "new.csv", [{"n": 2}])

    d = DatasetFromLastFile(path=str(src / "*.csv"), reader=pd.read_csv, name="last", type="t")
    assert d.load()["n"].tolist() == [2]


def test_a_newer_export_replaces_the_previous_one(src):
    _age(_csv(src / "old.csv", [{"n": 1}]), 600)

    d = DatasetFromLastFile(path=str(src / "*.csv"), reader=pd.read_csv, name="last", type="t")
    run(d)
    assert d.dataframe["n"].tolist() == [1]

    _csv(src / "new.csv", [{"n": 2}])
    run(d)
    assert d.dataframe["n"].tolist() == [2]


def test_last_file_with_no_matches_is_empty(src):
    d = DatasetFromLastFile(path=str(src / "*.csv"), reader=pd.read_csv, name="last", type="t")
    assert d.load().empty


# --- DatasetListOfFiles -----------------------------------------------------------

def test_describes_the_files_without_reading_them(src):
    (src / "a.pdf").write_bytes(b"1234")
    (src / "b.txt").write_bytes(b"12")

    d = DatasetListOfFiles(path=str(src / "*.*"), name="inventory", type="t")
    df = d.load().set_index("filename")

    assert list(df.columns) == ["path", "extension", "mtime", "size"]
    assert df.loc["a", "extension"] == "pdf" and df.loc["a", "size"] == 4
    assert df.loc["b", "extension"] == "txt" and df.loc["b", "size"] == 2


def test_a_recursive_pattern_descends(src):
    # ** only recurses when glob is asked to; without it a folder tree of
    # documents silently reports only its top level.
    (src / "sub" / "deeper").mkdir(parents=True)
    (src / "top.pdf").write_bytes(b"1")
    (src / "sub" / "deeper" / "buried.pdf").write_bytes(b"2")

    d = DatasetListOfFiles(path=str(src / "**" / "*.pdf"), name="inventory", type="t")
    assert sorted(d.load()["filename"]) == ["buried", "top"]


def test_an_inventory_of_nothing_is_empty(src):
    d = DatasetListOfFiles(path=str(src / "*.*"), name="inventory", type="t")
    assert d.load().empty


def test_the_inventory_follows_the_folder(src):
    (src / "a.pdf").write_bytes(b"1")
    _age(src / "a.pdf", 60)

    d = DatasetListOfFiles(path=str(src / "*.*"), name="inventory", type="t")
    run(d)
    assert d.dataframe.shape[0] == 1

    (src / "b.pdf").write_bytes(b"2")
    run(d)
    assert sorted(d.dataframe["filename"]) == ["a", "b"]


# --- DatasetFromFile ---------------------------------------------------------------

def test_reads_the_named_file(src):
    _csv(src / "one.csv", [{"n": 7}])

    d = DatasetFromFile(path=str(src / "one.csv"), name="one", type="t")
    assert d.load()["n"].tolist() == [7]


def test_a_missing_named_file_is_an_error(src):
    # Unlike a glob, an explicit path that is not there is a misconfiguration:
    # staying quietly empty would hide it for as long as the dataset exists.
    d = DatasetFromFile(path=str(src / "absent.csv"), name="one", type="t")
    with pytest.raises(FileNotFoundError):
        run(d)


# --- DatasetIndex ------------------------------------------------------------

@pytest.fixture
def tiny_depot(tmp_path, monkeypatch):
    """A tiny depot of two datasets, importable and rooted at its own folder."""
    root = tmp_path / "tinydepot"
    (root / "raw").mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "raw" / "__init__.py").write_text("", encoding="utf-8")
    (root / "raw" / "source.py").write_text(
        "import pandas as pd\n"
        "from depot import Dataset\n"
        "def extract(d):\n"
        "    d.dataframe = pd.DataFrame({'n': [1, 2, 3]})\n"
        "dts = Dataset(threshold=0, extractors=[extract])\n",
        encoding="utf-8",
    )
    (root / "raw" / "derived.py").write_text(
        "from depot import Dataset\n"
        "from tinydepot.raw.source import dts as source\n"
        "def transform(d):\n"
        "    d.dataframe = source.dataframe\n"
        "dts = Dataset(refs=[source], transforms=[transform])\n",
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))
    config.set_source(root)

    # Every test gets its own tmp_path, but sys.modules does not know that: a
    # tinydepot left behind by the previous test would answer for this one's,
    # and the import machinery caches directory listings besides.
    for name in [n for n in sys.modules if n == "tinydepot" or n.startswith("tinydepot.")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    return root


def test_lists_every_dataset_in_the_depot(tiny_depot):
    d = DatasetIndex(name="index", type="t")
    df = d.load().set_index("name")

    # The index is declared here rather than inside the depot, so it does not
    # list itself; one declared in a module under the root does.
    assert sorted(df.index) == ["derived", "source"]
    assert df.loc["source", "type"] == "raw"
    assert df.loc["derived", "refs"] == ["raw:source"]
    assert df.loc["source", "refs"] == []


def test_reports_size_without_opening_the_parquet(tiny_depot, monkeypatch):
    from tinydepot.raw.source import dts as source
    source.pipeline()

    from depot import cache as cache_module
    monkeypatch.setattr(cache_module, "load", lambda d: pytest.fail("the index opened a parquet"))

    d = DatasetIndex(name="index", type="t")
    row = d.load().set_index("name").loc["source"]

    assert row["rows"] == 3
    assert row["size_of_cache"] > 0
    assert pd.notna(row["changed"]) and pd.notna(row["timestamp"])


def test_a_dataset_that_never_ran_reports_nothing(tiny_depot):
    d = DatasetIndex(name="index", type="t")
    row = d.load().set_index("name").loc["derived"]

    assert row["rows"] == 0 and row["size_of_cache"] == 0
    assert pd.isna(row["changed"])


def test_the_index_does_not_wake_itself(tiny_depot):
    d = DatasetIndex(name="index", type="t")
    run(d)

    calls = []
    d.extractors = [lambda dts: calls.append(1)]
    run(d)
    # Its own metafile is rewritten by every run; watching it would mean the
    # index could never be up to date.
    assert calls == []


def test_a_broken_module_is_skipped_not_fatal(tiny_depot, capsys):
    (tiny_depot / "raw" / "broken.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")

    d = DatasetIndex(name="index", type="t")
    assert "source" in set(d.load()["name"])
    assert "broken.py" in capsys.readouterr().out


def test_load_makes_every_dataset_a_ref(tiny_depot):
    d = DatasetIndex(load_all=True, name="index", type="t")
    assert sorted(r.key for r in d.refs) == ["raw:derived", "raw:source"]


def test_load_brings_the_whole_depot_up_to_date(tiny_depot):
    from tinydepot.raw.derived import dts as derived

    d = DatasetIndex(load_all=True, name="index", type="t")
    run(d)

    # Nobody asked for derived; running the index was enough.
    assert not derived.dataframe.empty
    assert d.dataframe.set_index("name").loc["derived", "rows"] == 3


def test_without_load_nothing_is_run(tiny_depot):
    from tinydepot.raw.source import dts as source

    d = DatasetIndex(name="index", type="t")
    run(d)

    assert d.refs == []
    assert not cache.exists(source)


def test_an_index_inside_the_depot_is_not_its_own_ref(tiny_depot):
    # The real one is declared in a module under the root, so discovery finds
    # it. Referring to itself would be a cycle the graph refuses to walk.
    (tiny_depot / "index.py").write_text(
        "from depot.templates import DatasetIndex\n"
        "dts = DatasetIndex(load_all=True)\n",
        encoding="utf-8",
    )
    from tinydepot.index import dts as index

    assert index.key not in [r.key for r in index.refs]
    assert sorted(r.key for r in index.refs) == ["raw:derived", "raw:source"]
    run(index)  # would raise CycleError otherwise


@pytest.fixture
def full_pipeline_row(tiny_depot):
    """The index row of a dataset that fills every phase, manual ones included."""
    (tiny_depot / "raw" / "full.py").write_text(
        "import pandas as pd\n"
        "from depot import Dataset\n"
        "def fetch(d):\n"
        "    d.dataframe = pd.DataFrame({'n': [1]})\n"
        "def shape(d):\n"
        "    pass\n"
        "def check(d):\n"
        "    pass\n"
        "def publish(d):\n"
        "    pass\n"
        "def by_hand(d):\n"
        "    pass\n"
        "def a_report(d):\n"
        "    return None\n"
        "dts = Dataset(threshold=0, extractors=[fetch], transforms=[shape],\n"
        "              validators=[check], extras=[publish],\n"
        "              utilities=[by_hand], artifacts=[a_report])\n",
        encoding="utf-8",
    )
    d = DatasetIndex(name="index", type="t")
    return d.load().set_index("name").loc["full"]


def test_the_row_lists_the_pipeline_in_run_order(full_pipeline_row):
    assert list(full_pipeline_row["pipeline"]) == ["fetch", "shape", "check", "publish"]


def test_manual_actions_are_not_part_of_the_pipeline(full_pipeline_row):
    # The pipeline never calls them, so an arrow between them would be a lie.
    listed = list(full_pipeline_row["pipeline"])
    assert "by_hand" not in listed and "a_report" not in listed


def test_an_empty_phase_contributes_nothing_to_the_pipeline(tiny_depot):
    d = DatasetIndex(name="index", type="t")
    df = d.load().set_index("name")

    assert list(df.loc["source", "pipeline"]) == ["extract"]
    assert list(df.loc["derived", "pipeline"]) == ["transform"]


def test_a_callable_object_is_named_by_its_class(tiny_depot):
    """A step need not be a plain function, and getattr(__name__) would raise."""
    (tiny_depot / "raw" / "objectish.py").write_text(
        "from depot import Dataset\n"
        "class Enrich:\n"
        "    def __call__(self, d):\n"
        "        pass\n"
        "dts = Dataset(transforms=[Enrich()])\n",
        encoding="utf-8",
    )
    d = DatasetIndex(name="index", type="t")

    assert list(d.load().set_index("name").loc["objectish", "pipeline"]) == ["Enrich"]


def test_reload_sees_a_dataset_added_after_the_index_was_built(tiny_depot):
    """The depot changes while the process runs; the index has to be able to look again.

    Refs are gathered once, at construction. A dataset written afterwards —
    by a person, by an agent, by a migration — is invisible to an index that
    never re-reads the tree, and running it would quietly skip the newcomer.
    """
    d = DatasetIndex(load_all=True, name="index", type="t")
    assert "raw:latecomer" not in [ref.key for ref in d.refs]

    (tiny_depot / "raw" / "latecomer.py").write_text(
        "import pandas as pd\n"
        "from depot import Dataset\n"
        "dts = Dataset(threshold=0,\n"
        "              extractors=[lambda d: setattr(d, 'dataframe', pd.DataFrame({'n': [9]}))])\n",
        encoding="utf-8",
    )
    importlib.invalidate_caches()

    d.reload()

    assert "raw:latecomer" in [ref.key for ref in d.refs]
    assert "latecomer" in d.load()["name"].tolist()


def test_run_all_brings_the_whole_depot_up_to_date(tiny_depot):
    d = DatasetIndex(load_all=True, name="index", type="t")

    d.run_all()

    df = d.load().set_index("name")
    assert df.loc["source", "rows"] == 3
    assert df.loc["derived", "rows"] == 3


def test_run_all_forced_recomputes_every_node(tiny_depot):
    """Force reaches the refs, so the whole depot is redone, not just the table."""
    calls = []
    d = DatasetIndex(load_all=True, name="index", type="t")
    d.run_all()
    for ref in d.refs:
        ref.extractors = [lambda x, c=calls: c.append(x.key)] + list(ref.extractors)

    d.run_all(force=True)

    assert "raw:source" in calls


def test_run_all_refuses_an_index_that_holds_no_datasets(tiny_depot):
    """Without load_all the index is declared as "describe, run nothing".

    Quietly taking every dataset as a ref for the duration of one call would
    change what the dataset is — its version is computed from its refs — so the
    answer is to say so rather than to improvise.
    """
    d = DatasetIndex(name="index", type="t")

    with pytest.raises(ValueError, match="load_all"):
        d.run_all()


def test_the_index_offers_its_actions_as_utilities(tiny_depot):
    """Reachable as buttons too, under the contract every manual action follows.

    A utility is called with the dataset it belongs to; for the index that is
    the index itself, which is why one definition serves both callers.
    """
    d = DatasetIndex(load_all=True, name="index", type="t")
    names = [getattr(f, "__name__", type(f).__name__) for f in d.utilities]
    assert names == ["reload", "run_all", "run_all_forced"]

    for utility in d.utilities:
        utility(d)

    assert d.load().set_index("name").loc["derived", "rows"] == 3
