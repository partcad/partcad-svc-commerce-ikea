#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Tests of the IKEA repository plugin and of its catalog. Nothing here touches the network.

python -m pytest
"""

import os
import re
import runpy
import urllib.parse

import pytest

import ikea_repo

HERE = os.path.dirname(os.path.abspath(__file__))


@pytest.fixture(scope="module")
def catalog():
    return ikea_repo.load()


def served(request=None, catalog=None):
    return ikea_repo.Ikea(request or {}, catalog)


def search(plugin, category, text, limit=None):
    query = {"q": text}
    if limit:
        query["limit"] = limit
    return plugin.get("%s/search/part?%s" % (category, urllib.parse.urlencode(query)))


# --- the catalog -------------------------------------------------------------


def test_every_product_is_in_a_category_the_catalog_declares(catalog):
    for name, product in catalog["products"].items():
        assert product.get("category") in catalog["categories"], name
        path = product["category"]
        while "/" in path:
            path = path.rpartition("/")[0]
            assert path in catalog["categories"], "%s: no parent category %s" % (name, path)


def test_every_product_has_a_model_on_a_known_site(catalog):
    for name, product in catalog["products"].items():
        assert product.get("models"), name
        for model in product["models"]:
            assert model["site"] in ikea_repo.SITES, name
            assert str(model.get("model") or ""), name
            assert model.get("placed") in ("measured", "as-drawn"), name
            # What the part name will carry: PartCAD's grammar has no room for these.
            ikea_repo.name_part(model.get("file", ""))


def test_a_measured_model_says_how_it_is_placed(catalog):
    for name, product in catalog["products"].items():
        for model in product["models"]:
            if model["placed"] != "measured":
                assert "offset" not in model and "scale" not in model, name
                continue
            translation, axis, angle = model["offset"]
            assert len(translation) == 3 and len(axis) == 3 and any(axis), name
            assert isinstance(angle, (int, float)), name
            assert float(model["scale"]) > 0, name


def test_every_product_names_what_ikea_sells_it_as(catalog):
    for name, product in catalog["products"].items():
        ikea = product["ikea"]
        assert ikea["name"] and ikea["type"], name
        assert re.fullmatch(r"S?\d{8}", str(ikea["article"])), name
        assert ikea["url"].startswith("https://www.ikea.com/nl/en/p/"), name
        assert ikea["url"].rstrip("/").lower().endswith(str(ikea["article"]).lower()), name
        # The product's name says what it is, the way the others' do.
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), name


def test_article_numbers_are_printed_the_way_ikea_prints_them():
    assert ikea_repo.article("30275861") == "302.758.61"
    assert ikea_repo.article("S69385793") == "S693.857.93"
    assert ikea_repo.article("123") == "123"


# --- the packages: IKEA's categories -----------------------------------------


def test_the_root_lists_the_top_categories_and_holds_no_object(catalog):
    plugin = served(catalog=catalog)
    assert plugin.get("meta")["objectKinds"] == []
    assert plugin.get("meta")["manufacturable"] is False
    assert plugin.get("deps") == [
        "storage-furniture",
        "beds-mattresses",
        "sofas-armchairs-footstools",
        "tables-chairs",
        "desks-desk-chairs",
        "baby-children",
        "small-storage-organisers",
        "lighting",
    ]


def test_a_category_lists_the_categories_under_it_by_their_last_name(catalog):
    plugin = served(catalog=catalog)
    assert plugin.get("storage-furniture/deps") == [
        "bookcases-shelving-units",
        "tv-media-furniture",
        "storage-solution-systems",
    ]
    assert plugin.get("storage-furniture/bookcases-shelving-units/deps") == []


def test_a_category_with_products_lists_and_searches_them(catalog):
    plugin = served(catalog=catalog)
    meta = plugin.get("storage-furniture/bookcases-shelving-units/meta")
    assert meta["objectKinds"] == ["part"]
    assert meta["searchKinds"] == ["part"]
    assert "KALLAX" in meta["desc"] and "st002" in meta["desc"]
    # Every product finds the package's own store.
    assert meta["suppliers"] == {"//pub/svc/commerce/ikea:ikea-store": {}}
    # One with none under it directly lists nothing, and searches nothing.
    meta = plugin.get("storage-furniture/meta")
    assert meta["objectKinds"] == [] and "searchKinds" not in meta and "suppliers" not in meta


def test_a_product_is_an_alias_of_its_model_placed_by_the_convention(catalog):
    plugin = served(catalog=catalog)
    part = plugin.get("storage-furniture/bookcases-shelving-units/objects/part/kallax-shelving-unit-147x147")
    assert part["type"] == "alias"
    assert part["source"] == "//pub/collection/printables/models:1787571;file=7454419"
    assert part["vendor"] == "ikea" and part["sku"] == "30275861"
    assert part["url"] == "https://www.ikea.com/nl/en/p/kallax-shelving-unit-white-30275861/"
    assert part["scale"] == 25
    assert part["offset"] == [[34.4, 0, 0], [1, 0, 0], 90]
    assert "302.758.61" in part["desc"] and "Wondrej" in part["desc"] and "CC0 1.0" in part["desc"]


def test_a_category_lists_its_products_and_no_others(catalog):
    plugin = served(catalog=catalog)
    listed = plugin.get("tables-chairs/armchairs/objects/part")
    assert list(listed) == ["poang-armchair"]
    assert plugin.get("tables-chairs/armchairs/objects/part/kallax-shelving-unit-147x147") is None
    assert plugin.get("tables-chairs/armchairs/objects/sketch") == {}
    assert plugin.get("tables-chairs/armchairs/objects/sketch/poang-armchair") is None


def test_a_model_placed_as_drawn_is_aliased_as_it_is(catalog):
    plugin = served(catalog=catalog)
    part = plugin.get("sofas-armchairs-footstools/sofas/objects/part/klippan-2-seat-sofa")
    assert part["source"] == "//pub/collection/grabcad/models:ikea-klippan-sofa;file=Ikea Kipplan sofa.stp"
    assert "offset" not in part and "scale" not in part
    assert "as its designer drew it" in part["desc"]


def test_an_unknown_key_has_no_answer(catalog):
    plugin = served(catalog=catalog)
    assert plugin.get("files/anything") is None
    assert plugin.get("nonsense") is None
    assert plugin.get("storage-furniture/nonsense") is None


# --- 'sources' ---------------------------------------------------------------


def test_sources_choose_the_model_a_product_is_drawn_with(catalog):
    plugin = served({"parameters": {"sources": "grabcad, printables"}}, catalog)
    part = plugin.get("storage-furniture/bookcases-shelving-units/objects/part/kallax-shelving-unit-147x147")
    assert part["source"] == "//pub/collection/grabcad/models:ikea-kallax-2;file=KALLAX_4x4.stl"


def test_a_product_none_of_the_sources_has_is_not_served(catalog):
    plugin = served({"parameters": {"sources": "printables,makerworld"}}, catalog)
    assert "sofas-armchairs-footstools" not in plugin.get("deps")
    assert "lighting" not in plugin.get("deps")
    assert plugin.get("sofas-armchairs-footstools/sofas/objects/part") == {}
    assert plugin.get("sofas-armchairs-footstools/sofas/objects/part/klippan-2-seat-sofa") is None
    assert "micke-desk-105x50" not in plugin.get("desks-desk-chairs/desks-computer-desks/objects/part")
    assert "kallax-desk-111x39" in plugin.get("desks-desk-chairs/desks-computer-desks/objects/part")


def test_a_site_that_is_not_one_is_said_and_left_out(catalog, capsys):
    assert ikea_repo.sources_of({"parameters": {"sources": "printables,ikea.com,printables"}}) == ["printables"]
    assert "ikea.com" in capsys.readouterr().err
    assert ikea_repo.sources_of({}) == list(ikea_repo.SITES)


# --- search ------------------------------------------------------------------


def test_search_finds_a_name_without_its_diacritics(catalog):
    plugin = served(catalog=catalog)
    assert list(search(plugin, "tables-chairs/armchairs", "poang")) == ["poang-armchair"]
    assert list(search(plugin, "small-storage-organisers/hooks-wall-organisation", "SKADIS")) == [
        "skadis-pegboard-56x56"
    ]


def test_search_finds_an_article_number_however_it_is_written(catalog):
    plugin = served(catalog=catalog)
    for spelling in ("302.758.61", "30275861", "302 758 61"):
        hits = search(plugin, "storage-furniture/bookcases-shelving-units", spelling)
        assert "kallax-shelving-unit-147x147" in hits, spelling


def test_search_finds_a_product_by_another_word_for_it(catalog):
    plugin = served(catalog=catalog)
    assert "klippan-2-seat-sofa" in search(plugin, "sofas-armchairs-footstools/sofas", "couch")
    assert "billy-bookcase-80x28x202" in search(plugin, "storage-furniture/bookcases-shelving-units", "bookshelf")


def test_every_word_of_a_search_has_to_be_found(catalog):
    plugin = served(catalog=catalog)
    assert search(plugin, "storage-furniture/bookcases-shelving-units", "kallax walnut") == {}
    assert list(search(plugin, "storage-furniture/bookcases-shelving-units", "billy white")) == [
        "billy-bookcase-80x28x202"
    ]


def test_search_answers_declarations_best_first_and_no_more_than_asked(catalog):
    plugin = served(catalog=catalog)
    hits = search(plugin, "storage-furniture/bookcases-shelving-units", "kallax", limit=2)
    assert len(hits) == 2
    assert all(config["type"] == "alias" for config in hits.values())
    # The products named KALLAX before one that only mentions a cube.
    assert all(
        name.startswith("kallax") for name in search(plugin, "storage-furniture/bookcases-shelving-units", "kallax")
    )
    assert search(plugin, "storage-furniture/bookcases-shelving-units", "") == {}
    assert plugin.get("storage-furniture/bookcases-shelving-units/search/sketch?q=kallax") == {}


# --- as PartCAD runs it ------------------------------------------------------


def test_the_script_answers_the_key_it_is_run_with():
    script = os.path.join(HERE, "ikea_repo.py")
    request = {"api": "get", "key": "tables-chairs/armchairs/objects/part", "parameters": {}}
    result = runpy.run_path(script, init_globals={"request": request}, run_name="get")
    assert list(result["output"]["result"]) == ["poang-armchair"]


def test_a_failure_is_said_on_stderr_and_answered_with_nothing(monkeypatch, capsys):
    def broken(*_args, **_kwargs):
        raise OSError("no catalog")

    monkeypatch.setattr(ikea_repo, "load", broken)
    monkeypatch.setattr(ikea_repo, "request", {"key": "deps"}, raising=False)
    assert ikea_repo.get("deps") is None
    assert "no catalog" in capsys.readouterr().err


def test_cache_version_is_a_plain_integer_constant():
    """PartCAD reads it out of the script without running it, so it must stay a literal."""
    source = open(os.path.join(HERE, "ikea_repo.py"), encoding="utf-8").read()
    assert re.search(r"^CACHE_VERSION = \d+$", source, re.MULTILINE)
