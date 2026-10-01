import pandas as pd
import pytest

from depot import cache, config
from depot.dataset import Dataset
from depot.runner import run


def test_dataframe_loads_from_cache_on_first_read(tmp_path):
    config.set_cache_dir(tmp_path)
    writer = Dataset(name="records", type="source")
    writer.dataframe = pd.DataFrame({"x": [1, 2, 3]})
    cache.save(writer)

    reader = Dataset(name="records", type="source")
    assert reader.dataframe["x"].tolist() == [1, 2, 3]


def test_dataframe_is_empty_when_nothing_stored(tmp_path):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="records", type="source")
    assert d.dataframe.empty


def test_cache_is_read_only_once(tmp_path, monkeypatch):
    config.set_cache_dir(tmp_path)
    writer = Dataset(name="records", type="source")
    writer.dataframe = pd.DataFrame({"x": [1]})
    cache.save(writer)

    reader = Dataset(name="records", type="source")
    calls = []
    original = cache.load
    monkeypatch.setattr(cache, "load", lambda d: calls.append(d) or original(d))

    _ = reader.dataframe
    _ = reader.dataframe
    assert len(calls) == 1


def test_assignment_skips_the_cache(tmp_path, monkeypatch):
    config.set_cache_dir(tmp_path)

    def boom(dts):
        raise AssertionError("cache.load must not run after an assignment")

    monkeypatch.setattr(cache, "load", boom)

    d = Dataset(name="records", type="source")
    d.dataframe = pd.DataFrame({"x": [7]})
    assert d.dataframe["x"].tolist() == [7]


def test_load_meta_restores_state(tmp_path):
    config.set_cache_dir(tmp_path)
    writer = Dataset(name="records", type="source")
    writer.cache = False
    writer.timestamp = 111.0
    writer.changed = 222.0
    cache.save(writer)

    reader = Dataset(name="records", type="source")
    reader.load_meta()
    assert reader.timestamp == 111.0
    assert reader.changed == 222.0


def test_load_meta_runs_only_once(tmp_path):
    config.set_cache_dir(tmp_path)
    writer = Dataset(name="records", type="source")
    writer.cache = False
    writer.timestamp = 111.0
    writer.changed = 222.0
    cache.save(writer)

    d = Dataset(name="records", type="source")
    d.load_meta()
    assert d.timestamp == 111.0

    d.timestamp = 999.0
    d.load_meta()  # a second call must not touch in-memory state
    assert d.timestamp == 999.0


def test_reset_clears_memory_and_disk(tmp_path):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="records", type="source")
    d.dataframe = pd.DataFrame({"x": [1]})
    d.timestamp = 5.0
    d.changed = 6.0
    cache.save(d)

    d.reset()

    assert d.dataframe.empty
    assert d.timestamp == 0.0
    assert d.changed == 0.0
    assert not cache.exists(d)
    assert not cache.meta_path(d).exists()


def test_assigning_none_is_rejected(tmp_path):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="records", type="source")
    with pytest.raises(TypeError):
        d.dataframe = None


# --- a loader has no parquet to fall back on (cache=False) ------------------

def _brings(rows):
    def extract(dts):
        dts.dataframe = pd.DataFrame(rows)
    return extract


def test_a_loader_materialises_itself_instead_of_answering_empty(tmp_path):
    # Regression: a loader stores no dataframe, so the lazy read found no
    # parquet and answered empty — indistinguishable from a source that had
    # nothing. Meanwhile the timer, restored from the metafile, told the
    # runner not to fetch. Only force=True ever produced data.
    config.set_cache_dir(tmp_path)
    run(Dataset(name="loader", type="raw", threshold=3600, cache=False,
                extractors=[_brings({"x": [1, 2]})]))

    fresh = Dataset(name="loader", type="raw", threshold=3600, cache=False,
                    extractors=[_brings({"x": [1, 2]})])
    assert fresh.load()["x"].tolist() == [1, 2]


def test_a_loader_read_as_a_ref_is_not_empty(tmp_path):
    config.set_cache_dir(tmp_path)
    run(Dataset(name="loader", type="raw", threshold=3600, cache=False,
                extractors=[_brings({"x": [1, 2]})]))

    src = Dataset(name="loader", type="raw", threshold=3600, cache=False,
                  extractors=[_brings({"x": [1, 2]})])
    seen = []
    derived = Dataset(name="derived", type="t", refs=[src],
                      transforms=[lambda d: seen.append(len(src.dataframe))])
    run(derived)
    assert seen == [2]


def test_a_derived_loader_recomputes_itself_when_read(tmp_path):
    # The same hole one layer up: nothing is stored, so the data is the
    # transform's to produce.
    config.set_cache_dir(tmp_path)
    src = Dataset(name="src", type="raw", threshold=0, cache=False,
                  extractors=[_brings({"x": [1, 2]})])
    derived = Dataset(name="derived", type="t", refs=[src], cache=False,
                      transforms=[lambda d: setattr(d, "dataframe", src.dataframe)])
    assert derived.dataframe["x"].tolist() == [1, 2]
