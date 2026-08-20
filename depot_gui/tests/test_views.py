from depot_gui.views import COLUMNS_ID, ViewStore


def store(tmp_path) -> ViewStore:
    return ViewStore(tmp_path / "views")


# --- the column picker is always there ---

def test_an_untouched_dataset_has_exactly_the_column_picker(tmp_path):
    views = store(tmp_path).list("staging:sales")
    assert [v.id for v in views] == [COLUMNS_ID]
    assert views[0].kind == "columns"


def test_listing_writes_nothing_to_disk(tmp_path):
    """Browsing datasets must not seed the state directory with a file each."""
    s = store(tmp_path)
    s.list("staging:sales")
    assert not (tmp_path / "views").exists()


def test_the_column_picker_cannot_be_removed(tmp_path):
    s = store(tmp_path)
    s.add("staging:sales", "pivot", "Pivot")
    s.remove("staging:sales", COLUMNS_ID)
    assert COLUMNS_ID in [v.id for v in s.list("staging:sales")]


# --- add ---

def test_add_appends_after_the_views_already_there(tmp_path):
    s = store(tmp_path)
    s.add("staging:sales", "pivot", "Pivot")
    s.add("staging:sales", "aggrid", "AgGrid")
    assert [v.kind for v in s.list("staging:sales")] == ["columns", "pivot", "aggrid"]


def test_add_returns_the_view_it_created(tmp_path):
    view = store(tmp_path).add("staging:sales", "pivot", "Pivot")
    assert view.kind == "pivot"
    assert view.title == "Pivot"
    assert view.id


def test_a_repeated_component_gets_a_numbered_title(tmp_path):
    s = store(tmp_path)
    s.add("staging:sales", "pivot", "Pivot")
    s.add("staging:sales", "pivot", "Pivot")
    s.add("staging:sales", "pivot", "Pivot")
    assert [v.title for v in s.list("staging:sales")[1:]] == ["Pivot", "Pivot 2", "Pivot 3"]


def test_two_views_never_share_an_id(tmp_path):
    s = store(tmp_path)
    first = s.add("staging:sales", "pivot", "Pivot")
    second = s.add("staging:sales", "pivot", "Pivot")
    assert first.id != second.id


# --- remove ---

def test_remove_takes_out_the_one_view_it_names(tmp_path):
    s = store(tmp_path)
    doomed = s.add("staging:sales", "pivot", "Pivot")
    kept = s.add("staging:sales", "aggrid", "AgGrid")
    s.remove("staging:sales", doomed.id)
    assert [v.id for v in s.list("staging:sales")] == [COLUMNS_ID, kept.id]


def test_removing_an_unknown_id_changes_nothing(tmp_path):
    s = store(tmp_path)
    kept = s.add("staging:sales", "pivot", "Pivot")
    s.remove("staging:sales", "nosuchid")
    assert [v.id for v in s.list("staging:sales")] == [COLUMNS_ID, kept.id]


# --- config ---

def test_a_saved_config_comes_back(tmp_path):
    s = store(tmp_path)
    view = s.add("staging:sales", "perspective", "Perspective")
    s.save_config("staging:sales", view.id, {"group_by": ["month"]})
    assert s.config("staging:sales", view.id) == {"group_by": ["month"]}


def test_saving_one_config_leaves_its_neighbours_alone(tmp_path):
    s = store(tmp_path)
    first = s.add("staging:sales", "pivot", "Pivot")
    second = s.add("staging:sales", "pivot", "Pivot")
    s.save_config("staging:sales", first.id, {"rows": ["a"]})
    s.save_config("staging:sales", second.id, {"rows": ["b"]})
    assert s.config("staging:sales", first.id) == {"rows": ["a"]}
    assert s.config("staging:sales", second.id) == {"rows": ["b"]}


def test_saving_onto_a_deleted_view_does_not_raise(tmp_path):
    """A dialog left open on a view that has since been removed."""
    s = store(tmp_path)
    view = s.add("staging:sales", "pivot", "Pivot")
    s.remove("staging:sales", view.id)
    s.save_config("staging:sales", view.id, {"rows": ["a"]})
    assert [v.id for v in s.list("staging:sales")] == [COLUMNS_ID]


def test_the_column_picker_holds_a_config_like_any_other_view(tmp_path):
    s = store(tmp_path)
    s.save_config("staging:sales", COLUMNS_ID, {"visible": ["a", "b"]})
    assert s.config("staging:sales", COLUMNS_ID) == {"visible": ["a", "b"]}


def test_an_unknown_view_has_an_empty_config(tmp_path):
    assert store(tmp_path).config("staging:sales", "nosuchid") == {}


# --- one file per dataset ---

def test_two_datasets_do_not_see_each_other(tmp_path):
    s = store(tmp_path)
    s.add("staging:sales", "pivot", "Pivot")
    assert [v.id for v in s.list("staging:other")] == [COLUMNS_ID]


def test_keys_that_differ_only_by_separator_land_in_different_files(tmp_path):
    """`store/helper:a` and `store:helper_a` would collide if the key were
    folded into underscores to make a filename."""
    s = store(tmp_path)
    s.add("store/helper:a", "pivot", "Pivot")
    assert [v.id for v in s.list("store:helper_a")] == [COLUMNS_ID]


def test_a_corrupt_file_reads_as_an_untouched_dataset(tmp_path):
    root = tmp_path / "views"
    root.mkdir(parents=True)
    (root / "staging%3Asales.json").write_text("not json", encoding="utf-8")
    assert [v.id for v in store(tmp_path).list("staging:sales")] == [COLUMNS_ID]
