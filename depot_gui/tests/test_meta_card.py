import pandas as pd
from depot import Dataset

from depot_gui.components.meta_card import refs_html, shape_text


def test_refs_render_as_links_to_the_dataset_page():
    ref = Dataset(name="records", type="source")
    html = refs_html(Dataset(name="sales", type="staging", refs=[ref]))
    assert '<a href="/dts/source:records">records</a>' in html


def test_a_nested_type_keeps_its_slash_in_the_link():
    ref = Dataset(name="categories", type="store/helper")
    assert "/dts/store/helper:categories" in refs_html(Dataset(name="x", type="y", refs=[ref]))


def test_no_refs_says_so():
    assert refs_html(Dataset(name="records", type="source")) == "No refs"


def test_shape_of_an_empty_dataset_is_zero_by_zero():
    dts = Dataset(name="a", type="b")
    dts.dataframe = pd.DataFrame()
    assert shape_text(dts) == "0 × 0"


def test_shape_reports_rows_and_columns():
    dts = Dataset(name="a", type="b")
    dts.dataframe = pd.DataFrame({"x": [1, 2], "y": [3, 4]})
    assert shape_text(dts) == "2 × 2"
