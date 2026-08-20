import json
from pathlib import Path

import pandas as pd
import pytest

from depot import cache, config
from depot.dataset import Dataset


def _dts(name="records", type="source"):
    return Dataset(name=name, type=type)


# --- paths ------------------------------------------------------------------

def test_paths_are_derived_from_type_and_name(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    assert cache.data_path(d) == tmp_path / "source" / "records.parquet"
    assert cache.meta_path(d) == tmp_path / "source" / "records.meta"


def test_the_cache_mirrors_the_type_folder_for_folder(tmp_path):
    config.set_cache_dir(tmp_path)
    nested = Dataset(name="agents", type="store/helper")
    flat = Dataset(name="agents", type="store_helper")

    assert cache.data_path(nested) == tmp_path / "store" / "helper" / "agents.parquet"
    assert cache.data_path(flat) == tmp_path / "store_helper" / "agents.parquet"
    # The pair that used to share one parquet, each overwriting the other.
    assert cache.data_path(nested) != cache.data_path(flat)


def test_a_key_can_always_be_split_back_into_type_and_name(tmp_path):
    # What a UI needs: no matter how deep the folders go, one separator tells
    # it where the type ends. A colon can do that because a filename may not
    # contain one, so no name will ever look like the separator.
    config.set_cache_dir(tmp_path)
    d = Dataset(name="agents", type="store/helper")

    assert d.key == "store/helper:agents"
    assert d.key.split(":") == [d.type, d.name]


def test_an_identity_the_cache_could_not_hold_is_refused(tmp_path):
    config.set_cache_dir(tmp_path)

    with pytest.raises(ValueError, match="daily sales"):
        Dataset(name="daily sales", type="staging")
    # The separator itself: a key that split into three parts would leave the
    # reader guessing which one is the name.
    with pytest.raises(ValueError, match="a:b"):
        Dataset(name="a:b", type="staging")
    # A name is one segment. Nesting belongs to the type.
    with pytest.raises(ValueError, match="a/b"):
        Dataset(name="a/b", type="staging")


# --- metadata ---------------------------------------------------------------

def test_read_missing_meta_is_the_never_run_state(tmp_path):
    config.set_cache_dir(tmp_path)
    meta = cache.read_meta(_dts())
    assert (meta.timestamp, meta.changed) == (0.0, 0.0)
    assert meta.nested == [] and meta.schema == {} and meta.shape is None


def test_meta_roundtrip(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.timestamp = 1234.5
    d.changed = 999.25
    d.dataframe = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    cache.save(d)

    meta = cache.read_meta(d)
    assert meta.timestamp == 1234.5
    assert meta.changed == 999.25
    assert meta.shape == (2, 2)
    assert set(meta.schema) == {"id", "name"}


def test_meta_records_schema_and_shape(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"n": [1, 2, 3], "s": ["a", "b", "c"]})
    cache.save(d)

    meta = cache.read_meta(d)
    assert meta.shape == (3, 2)
    assert set(meta.schema) == {"n", "s"}
    # The exact spelling of a dtype moves between pandas versions; what the
    # metafile promises is that every column has one recorded.
    assert meta.schema["n"].startswith("int")
    assert meta.schema["s"] in ("object", "str")


def test_meta_is_written_even_without_a_dataframe(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.cache = False
    d.timestamp = 7.0
    d.changed = 8.0
    cache.save(d)

    assert not cache.exists(d)
    meta = cache.read_meta(d)
    assert (meta.timestamp, meta.changed) == (7.0, 8.0)
    assert meta.shape is None


def test_save_creates_the_directory(tmp_path):
    config.set_cache_dir(tmp_path / "deep" / "nested")
    d = _dts()
    d.dataframe = pd.DataFrame({"x": [1]})
    cache.save(d)
    assert cache.data_path(d).is_file()
    assert cache.meta_path(d).is_file()


def test_meta_is_plain_json(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.timestamp = 10.0
    d.changed = 20.0
    d.cache = False
    cache.save(d)
    payload = json.loads(cache.meta_path(d).read_text(encoding="utf-8"))
    assert payload["timestamp"] == 10.0
    assert payload["changed"] == 20.0


def test_corrupt_meta_reads_as_never_run(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    cache.meta_path(d).parent.mkdir(parents=True, exist_ok=True)
    cache.meta_path(d).write_text("not json", encoding="utf-8")
    meta = cache.read_meta(d)
    assert (meta.timestamp, meta.changed) == (0.0, 0.0)


# --- data -------------------------------------------------------------------

def test_missing_file_loads_empty(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    assert not cache.exists(d)
    loaded = cache.load(d)
    assert isinstance(loaded, pd.DataFrame)
    assert loaded.empty


def test_roundtrip_plain_columns(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"id": [1, 2], "name": ["a", "b"]})
    cache.save(d)
    assert cache.exists(d)
    pd.testing.assert_frame_equal(cache.load(d), d.dataframe)


def test_a_meaningful_index_survives(tmp_path):
    # A remote table arrives keyed by id, and the columns that reference it are
    # resolved with .map(), which looks up by index. Dropping the index on the
    # way to disk turns that lookup positional and silently maps every row to
    # the wrong value.
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"title": ["a", "b"]}, index=pd.Index([115, 240], name="id"))
    cache.save(d)

    loaded = cache.load(d)
    assert loaded.index.name == "id"
    assert loaded.index.tolist() == [115, 240]
    assert pd.Series([240, 115]).map(loaded["title"]).tolist() == ["b", "a"]


def test_a_plain_range_index_is_not_stored_as_a_column(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"x": [1, 2]})
    cache.save(d)

    loaded = cache.load(d)
    assert list(loaded.columns) == ["x"]
    assert loaded.index.tolist() == [0, 1]


def test_roundtrip_nested_columns(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({
        "id": [1, 2],
        "services": [[{"cost": 3}], []],
        "client": [{"name": "Anna"}, None],
    })
    cache.save(d)
    loaded = cache.load(d)
    assert loaded["services"].tolist() == [[{"cost": 3}], []]
    assert loaded["client"].tolist()[0] == {"name": "Anna"}
    assert loaded["client"].tolist()[1] is None


def test_nested_list_is_dropped_when_no_longer_nested(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"x": [[1], [2]]})
    cache.save(d)
    assert cache.read_meta(d).nested == ["x"]

    d.dataframe = pd.DataFrame({"x": [1, 2]})
    cache.save(d)
    assert cache.read_meta(d).nested == []
    assert cache.load(d)["x"].tolist() == [1, 2]


def test_nested_column_with_numpy_values_roundtrips(tmp_path):
    import numpy as np

    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({
        "id": [1],
        "services": [[{"cost": np.int64(3), "rate": np.float64(1.5)}]],
    })
    cache.save(d)
    assert cache.load(d)["services"].tolist() == [[{"cost": 3, "rate": 1.5}]]


def test_load_survives_metadata_that_disagrees_with_the_file(tmp_path):
    # The parquet can still be replaced from outside the framework.
    # Lengths 2 and 0 are deliberate: those are the ones for which pd.isna
    # returns an array whose truth value is ambiguous. A length-1 list is
    # silently fine and would prove nothing.
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"x": [[1, 2], []]})
    cache.save(d)

    pd.DataFrame({"x": [[1, 2], []]}).to_parquet(cache.data_path(d), index=False)

    loaded = cache.load(d)
    assert [list(v) for v in loaded["x"]] == [[1, 2], []]


def test_drop_removes_both_files(tmp_path):
    config.set_cache_dir(tmp_path)
    d = _dts()
    d.dataframe = pd.DataFrame({"x": [[1]]})
    cache.save(d)
    cache.drop(d)
    assert not cache.data_path(d).exists()
    assert not cache.meta_path(d).exists()


# --- configuration ----------------------------------------------------------

def test_cache_env_var_sets_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPOT_CACHE", str(tmp_path / "from-env"))
    config.reset()
    assert config.cache_dir() == tmp_path / "from-env"


def test_source_env_var_sets_default(tmp_path, monkeypatch):
    monkeypatch.setenv("DEPOT_SOURCE", str(tmp_path / "datasets"))
    config.reset()
    assert config.source() == tmp_path / "datasets"


def test_the_defaults_do_not_name_anybody_s_project(monkeypatch):
    # A library's defaults are a statement about convention, so they say what
    # the framework calls things — not what the first project to use it did.
    monkeypatch.delenv("DEPOT_SOURCE", raising=False)
    monkeypatch.delenv("DEPOT_CACHE", raising=False)
    config.reset()

    assert config.source() == Path("datasets")
    assert config.cache_dir() == Path(".depot/cache")
