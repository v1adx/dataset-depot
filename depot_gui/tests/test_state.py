from depot_gui.state import StateFile


def test_missing_file_reads_as_empty(tmp_path):
    assert StateFile(tmp_path / "nope.json").read() == {}


def test_corrupted_file_reads_as_empty(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("not json", encoding="utf-8")
    assert StateFile(path).read() == {}


def test_write_then_read_round_trip(tmp_path):
    state = StateFile(tmp_path / "s.json")
    state.write({"staging:daily_sales": {"x": 1}})
    assert state.read() == {"staging:daily_sales": {"x": 1}}


def test_set_keeps_the_other_keys(tmp_path):
    state = StateFile(tmp_path / "s.json")
    state.set("a:one", [1, 2])
    state.set("b:two", [3])
    assert state.read() == {"a:one": [1, 2], "b:two": [3]}


def test_get_returns_the_default_when_absent(tmp_path):
    assert StateFile(tmp_path / "s.json").get("nope", {"d": 1}) == {"d": 1}


def test_write_creates_the_parent_directory(tmp_path):
    state = StateFile(tmp_path / "deep" / "s.json")
    state.write({"k": 1})
    assert (tmp_path / "deep" / "s.json").is_file()


def test_a_colon_in_the_key_survives_the_round_trip(tmp_path):
    state = StateFile(tmp_path / "s.json")
    state.set("store/helper:categories", {"visible": ["a"]})
    assert "store/helper:categories" in state.read()
