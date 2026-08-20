import importlib
import json
import sys

import pytest

from depot import config
from depot.cli import main


@pytest.fixture
def depot_tree(tmp_path, monkeypatch):
    root = tmp_path / "clidepot"
    (root / "raw").mkdir(parents=True)
    (root / "marts").mkdir()
    for folder in ("", "raw", "marts"):
        (root / folder / "__init__.py").write_text("", encoding="utf-8")

    (root / "raw" / "source.py").write_text(
        '"""Where it all starts."""\n'
        "import pandas as pd\n"
        "from depot import Dataset\n"
        "def extract(d):\n"
        "    d.dataframe = pd.DataFrame({'n': [1, 2]})\n"
        "dts = Dataset(threshold=3600, extractors=[extract])\n",
        encoding="utf-8")
    (root / "marts" / "top.py").write_text(
        '"""What the source becomes."""\n'
        "from depot import Dataset\n"
        "from clidepot.raw.source import dts as source\n"
        "def transform(d):\n"
        "    d.dataframe = source.dataframe\n"
        "dts = Dataset(refs=[source], transforms=[transform])\n",
        encoding="utf-8")

    monkeypatch.syspath_prepend(str(tmp_path))
    config.set_source(root)
    config.set_cache_dir(tmp_path / "cache")
    for name in [n for n in sys.modules if n == "clidepot" or n.startswith("clidepot.")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    yield root
    config.reset()


def _out(capsys):
    return capsys.readouterr().out


def _json(capsys):
    return json.loads(_out(capsys))


def test_the_banner_marks_a_source_that_is_not_there(tmp_path, capsys):
    """A root that does not exist answers "no datasets found" — the same words
    an empty depot answers with. The banner is already printed on every run, so
    it is where that difference can show without anything being raised."""
    config.set_source(tmp_path / "nowhere")
    main(["ls"])
    assert "no such directory" in capsys.readouterr().err


def test_the_banner_says_nothing_extra_about_a_source_that_is_there(depot_tree, capsys):
    main(["ls"])
    assert "no such directory" not in capsys.readouterr().err


def test_ls_lists_every_dataset_with_its_docstring(depot_tree, capsys):
    main(["ls"])
    out = _out(capsys)
    assert "raw:source" in out and "Where it all starts." in out
    assert "marts:top" in out


def test_ls_json_carries_the_index(depot_tree, capsys):
    main(["ls", "--json"])
    rows = {r["key"]: r for r in _json(capsys)}
    assert rows["raw:source"]["layer"] == 0
    assert rows["marts:top"]["layer"] == 1
    assert rows["raw:source"]["dependants"] == 1


def test_show_describes_without_running(depot_tree, capsys):
    main(["show", "raw:source"])
    out = _out(capsys)
    assert "Where it all starts." in out
    assert "nothing on disk yet" in out


def test_show_accepts_a_bare_name(depot_tree, capsys):
    main(["show", "source"])
    assert "raw:source" in _out(capsys)


def test_an_unknown_name_exits_with_a_message(depot_tree, capsys):
    with pytest.raises(SystemExit) as e:
        main(["show", "absent"])
    assert "no dataset" in str(e.value)


def test_plan_says_what_would_run_and_changes_nothing(depot_tree, capsys):
    main(["plan", "top"])
    out = _out(capsys)
    assert "will run" in out and "raw:source" in out

    main(["show", "top", "--json"])
    assert _json(capsys)["stored"] is False  # the plan touched nothing


def test_run_executes_and_reports(depot_tree, capsys):
    main(["run", "top"])
    assert "ran in" in _out(capsys)

    main(["show", "top", "--json"])
    assert _json(capsys)["rows"] == 2


def test_reset_drops_what_was_stored(depot_tree, capsys):
    main(["run", "top"])
    capsys.readouterr()

    main(["reset", "top"])
    assert "cache dropped" in _out(capsys)

    main(["reset", "top"])
    assert "nothing was stored" in _out(capsys)


def test_graph_indents_by_layer(depot_tree, capsys):
    main(["graph"])
    lines = _out(capsys).splitlines()
    assert lines[0].strip() == "raw:source"
    assert lines[1].startswith("    ")  # one layer deeper


def test_graph_speaks_mermaid(depot_tree, capsys):
    main(["graph", "--format", "mermaid"])
    out = _out(capsys)
    assert out.startswith("graph TD")
    assert "-->" in out


def test_check_is_quiet_when_nothing_is_wrong(depot_tree, capsys):
    main(["check"])
    assert "no problems found" in _out(capsys)


def test_check_catches_a_dataset_nothing_can_trigger(depot_tree, capsys):
    (depot_tree / "raw" / "stuck.py").write_text(
        '"""Nothing will ever run this."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(threshold=None, transforms=[lambda d: None])\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    assert "nothing will ever make it run" in _out(capsys)


def test_check_catches_a_probe_that_is_not_a_time(depot_tree, capsys):
    (depot_tree / "raw" / "etag.py").write_text(
        '"""A probe answering with a revision number."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(probe=lambda d: 42.0, extractors=[lambda d: None])\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    assert "not a time" in _out(capsys)


def test_template_prints_something_importable(depot_tree, capsys):
    main(["template"])
    out = _out(capsys)
    assert "from depot import Dataset" in out
    compile(out, "template", "exec")


def test_show_rows_prints_the_data_not_an_ellipsis(depot_tree, capsys):
    # pandas truncates to the terminal by default, and a row of "..." tells a
    # reader nothing — which is the whole reason --rows exists.
    main(["run", "source"])
    capsys.readouterr()

    main(["show", "source", "--rows", "2"])
    out = _out(capsys)
    assert "..." not in out
    assert "1" in out and "2" in out


def test_check_catches_a_probe_and_a_timer_together(depot_tree, capsys):
    (depot_tree / "raw" / "both.py").write_text(
        '"""Two ways of answering the same question."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(probe=lambda d: 1.7e9, threshold=60, extractors=[lambda d: None])\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    assert "probe already answers exactly" in _out(capsys)


def test_check_catches_a_cache_nothing_fills(depot_tree, capsys):
    (depot_tree / "raw" / "hollow.py").write_text(
        '"""Stores a dataframe it never produces."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(threshold=60)\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    assert "no phase that produces one" in _out(capsys)


def test_check_reports_a_probe_that_raises(depot_tree, capsys):
    (depot_tree / "raw" / "angry.py").write_text(
        '"""A probe that cannot answer."""\n'
        "def boom(d):\n"
        "    raise RuntimeError('the source is down')\n"
        "from depot import Dataset\n"
        "dts = Dataset(probe=boom, extractors=[lambda d: None])\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    out = _out(capsys)
    assert "probe raised RuntimeError" in out and "the source is down" in out


def test_check_reports_a_cycle(depot_tree, capsys):
    (depot_tree / "raw" / "loop.py").write_text(
        '"""Two datasets that wait for each other."""\n'
        "from depot import Dataset\n"
        "dts_a = Dataset(name='a', type='loop')\n"
        "dts_b = Dataset(name='b', type='loop', refs=[dts_a])\n"
        "dts_a.refs = [dts_b]\n",
        encoding="utf-8")
    importlib.invalidate_caches()

    main(["check"])
    assert "cycle" in _out(capsys)


def test_env_is_read_from_the_working_directory_not_the_package(tmp_path, monkeypatch, capsys):
    """The .env belongs to the project being run, not to wherever depot is installed.

    A bare load_dotenv() searches upwards from the module that called it. Once
    this package is installed from another directory that search never reaches
    the project at all, and every command silently falls back to the defaults.
    """
    project = tmp_path / "someproject"
    root = project / "depotdata"
    root.mkdir(parents=True)
    (root / "__init__.py").write_text("", encoding="utf-8")
    (root / "thing.py").write_text(
        '"""Declared under a root only .env knows about."""\n'
        "from depot import Dataset\n"
        "dts = Dataset(threshold=3600, extractors=[lambda d: None])\n",
        encoding="utf-8")
    (project / ".env").write_text(f"DEPOT_SOURCE={root}\n", encoding="utf-8")

    monkeypatch.delenv("DEPOT_SOURCE", raising=False)
    monkeypatch.chdir(project)
    config.reset()
    for name in [n for n in sys.modules if n == "depotdata" or n.startswith("depotdata.")]:
        del sys.modules[name]
    importlib.invalidate_caches()

    main(["ls"])
    assert "depotdata:thing" in _out(capsys)
