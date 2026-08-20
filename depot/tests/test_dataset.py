import importlib.util
from pathlib import Path

import pandas as pd
import pytest

from depot import config
from depot.dataset import Dataset


def test_defaults():
    d = Dataset(name="sales", type="staging")
    assert d.refs == []
    assert d.extractors == []
    assert d.transforms == []
    assert d.validators == []
    assert d.extras == []
    assert d.utilities == []
    assert d.artifacts == []
    assert d.probe is None
    assert d.threshold is None
    assert d.cache is True
    assert d.props == {}
    assert d.timestamp == 0.0
    assert d.changed == 0.0


def test_dataframe_is_never_none():
    d = Dataset(name="sales", type="staging")
    assert isinstance(d.dataframe, pd.DataFrame)
    assert d.dataframe.empty


def _load_user_module(path: Path, body: str):
    """Write a module at the given path, execute it and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    spec = importlib.util.spec_from_file_location(f"probe_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_DEFINE_DTS = "from depot.dataset import Dataset\ndts = Dataset()\n"


def test_name_and_type_from_user_module(tmp_path):
    # tmp_path is outside the datasets root, so this exercises the fallback:
    # the type is the name of the file's immediate directory.
    module = _load_user_module(tmp_path / "staging" / "sales.py", _DEFINE_DTS)
    assert module.dts.name == "sales"
    assert module.dts.type == "staging"


def test_type_is_the_directory_path_relative_to_the_root(tmp_path):
    config.set_source(tmp_path)
    module = _load_user_module(tmp_path / "source" / "raw" / "records.py", _DEFINE_DTS)
    assert module.dts.name == "records"
    assert module.dts.type == "source/raw"


def test_nesting_keeps_identities_distinct(tmp_path):
    # Taking only the immediate directory would give both of these the same
    # identity, and with it the same cache files.
    config.set_source(tmp_path)
    shallow = _load_user_module(tmp_path / "a" / "x.py", _DEFINE_DTS)
    deep = _load_user_module(tmp_path / "b" / "a" / "x.py", _DEFINE_DTS)

    assert shallow.dts.key == "a:x"
    assert deep.dts.key == "b/a:x"
    assert shallow.dts != deep.dts


def test_factory_inside_package_attributes_to_user_module(tmp_path):
    # A factory inside depot builds a Dataset on behalf of a user module —
    # exactly how the templates in the next layer will work.
    module = _load_user_module(
        tmp_path / "raw" / "prices.py",
        "from depot.tests._factory import make\ndts = make()\n",
    )
    assert module.dts.name == "prices"
    assert module.dts.type == "raw"


def test_explicit_name_and_type_win():
    d = Dataset(name="explicit", type="custom")
    assert d.name == "explicit"
    assert d.type == "custom"


def test_identity_is_type_and_name():
    a = Dataset(name="x", type="raw")
    b = Dataset(name="x", type="raw")
    c = Dataset(name="x", type="staging")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    assert len({a, b, c}) == 2


def test_repr():
    d = Dataset(name="sales", type="staging")
    assert repr(d) == "Dataset('staging:sales')"


# --- info --------------------------------------------------------------------

def test_info_runs_the_dataset_before_describing_it(tmp_path, capsys):
    # Printing the state of something nobody computed would only ever say
    # "empty", which is why info runs first.
    config.set_cache_dir(tmp_path)
    d = Dataset(name="table", type="t", threshold=3600,
                extractors=[lambda x: setattr(x, "dataframe", pd.DataFrame({"n": [1, 2]}))])
    d.info()

    out = capsys.readouterr().out
    assert "t:table" in out
    assert "2 rows × 1 cols" in out
    assert "n" in out and "int64" in out


def test_info_shows_rows_when_asked(tmp_path, capsys):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="table", type="t", threshold=3600,
                extractors=[lambda x: setattr(x, "dataframe", pd.DataFrame({"n": [7]}))])
    d.info(rows=1)
    assert "7" in capsys.readouterr().out


def test_format_age_uses_one_coarse_unit():
    from depot.dataset import _format_age

    assert _format_age(45) == "45s"
    assert _format_age(12 * 60) == "12m"
    assert _format_age(3 * 3600) == "3h"
    assert _format_age(5 * 86400) == "5d"
    assert _format_age(3 * 604800) == "3w"


def test_info_says_how_long_ago_not_when(tmp_path, capsys):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="table", type="t", threshold=3600,
                extractors=[lambda x: setattr(x, "dataframe", pd.DataFrame({"n": [1]}))])
    d.info()
    assert "changed: 0s ago" in capsys.readouterr().out


def test_info_says_never_for_a_dataset_with_no_version(tmp_path, capsys):
    config.set_cache_dir(tmp_path)
    d = Dataset(name="quiet", type="t", threshold=3600, cache=False,
                extractors=[lambda x: setattr(x, "dataframe", pd.DataFrame())])
    d.info()
    assert "changed: never" in capsys.readouterr().out


def test_a_dataset_with_no_identity_at_all_is_refused(monkeypatch):
    # Nothing to derive from and nothing given. The old code cached it as
    # "__.parquet", and one of those is still lying in the real depot.
    import depot.dataset as module

    monkeypatch.setattr(module, "_caller_file", lambda: None)
    with pytest.raises(ValueError, match="needs an identity"):
        Dataset()
