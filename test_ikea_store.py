#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Tests of the IKEA store provider, run the way PartCAD runs it, against answers in the shapes ikea.com gives.

Nothing here touches the network: every request is answered by 'Site' below.

    python -m pytest
"""

import io
import json
import os
import runpy
import urllib.error
import urllib.parse
import urllib.request

import pytest

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ikea_store.py")

STORES = [
    {"id": "088", "name": "IKEA Amsterdam", "displayName": "IKEA Amsterdam", "storePageUrl": "https://x/amsterdam/"},
    {"id": "270", "name": "IKEA Utrecht", "displayName": "IKEA Utrecht", "storePageUrl": "https://x/utrecht/"},
    {"id": "272", "name": "IKEA Arnhem - Duiven", "displayName": "IKEA Duiven"},
    {"id": "999", "name": "IKEA Hidden", "hideStore": True},
]


def product(item_no, name, type_name, price, status=None, store="IKEA Amsterdam", combination=False, currency="EUR"):
    """A product of the search's answer: 'searchResultPage.products.main.items[].product'."""
    found = {
        "name": name,
        "typeName": type_name,
        "itemNo": item_no,
        "itemNoGlobal": item_no,
        "itemType": "SPR" if combination else "ART",
        "pipUrl": "https://www.ikea.com/nl/en/p/%s-%s%s/" % (name.lower(), "s" if combination else "", item_no),
        "itemMeasureReferenceText": "147x147 cm",
        "salesPrice": {"currencyCode": currency, "numeral": price},
        "availability": [],
    }
    if status:
        found["availability"] = [
            {"type2": "HOME_DELIVERY", "status": "HIGH_IN_STOCK"},
            {"type2": "CASH_AND_CARRY", "status": status, "store": store},
        ]
    return found


class Site:
    """ikea.com, as far as the provider asks it: the store list, and the search by article number."""

    def __init__(self):
        self.products = {
            "30275861": product("30275861", "KALLAX", "Shelving unit", 99.99, "HIGH_IN_STOCK"),
            "39240787": product("39240787", "POÄNG", "Armchair", 69, "LOW_IN_STOCK", combination=True),
            "00263850": product("00263850", "BILLY", "Bookcase", 49.99, "OUT_OF_STOCK"),
            "80213074": product("80213074", "MICKE", "Desk", 79.99),
            "69385793": product("69385793", "MALM", "Bed frame, high", 169, "PLACE_ORDER_IN_STORE", combination=True),
            "70458203": product("70458203", "PAX", "Wardrobe frame", 95, "SOMETHING_NEW"),
        }
        self.asked = []
        self.fail = None

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.asked.append(url)
        assert req.get_header("User-agent").startswith("Mozilla/5.0 (compatible; partcad-ikea-store/")
        if self.fail:
            raise self.fail
        if url.endswith("/stores-suggested-detailed.json"):
            return io.BytesIO(json.dumps(STORES).encode())
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        found = self.products.get(query["q"][0])
        items = [{"product": found}] if found else []
        # The search also answers with things it merely finds alike.
        items.append({"product": product("12345678", "LACK", "Side table", 9.99, "HIGH_IN_STOCK")})
        return io.BytesIO(json.dumps({"searchResultPage": {"products": {"main": {"items": items}}}}).encode())


@pytest.fixture
def site(monkeypatch, tmp_path):
    answering = Site()
    monkeypatch.setattr(urllib.request, "urlopen", answering)
    monkeypatch.setenv("PARTCAD_IKEA_CACHE", str(tmp_path))
    for name in ("PARTCAD_IKEA_COUNTRY", "PARTCAD_IKEA_LANGUAGE", "PARTCAD_IKEA_STORE"):
        monkeypatch.delenv(name, raising=False)
    return answering


def run(api, parameters=None, **request):
    request.update({"api": api, "partcad_version": "0.9.4", "parameters": parameters or {}})
    return runpy.run_path(SCRIPT, init_globals={"request": request}, run_name=api).get("output")


def cart(*lines):
    return {"qos": "standard", "skus": [{"vendor": v, "sku": s, "count": c, "parts": ["p"]} for v, s, c in lines]}


# --- avail -----------------------------------------------------------------------


def test_an_article_in_stock_at_the_store_is_available(site):
    assert run("avail", vendor="ikea", sku="30275861") == {"available": True}
    # Asked of IKEA Amsterdam, in the Netherlands, in English, unless told otherwise.
    search = [url for url in site.asked if "search-result-page" in url][0]
    assert search.startswith("https://sik.search.blue.cdtapps.com/nl/en/search-result-page?")
    assert "store=088" in search and "q=30275861" in search


def test_a_store_that_has_only_a_few_left_has_it(site):
    assert run("avail", vendor="ikea", sku="S39240787") == {"available": True}


def test_what_is_ordered_at_the_store_s_desk_is_sold_there(site):
    assert run("avail", vendor="ikea", sku="S69385793") == {"available": True}
    (line,) = run("quote", cart=cart(("ikea", "S69385793", 1)))["lines"]
    assert line["stock"] == "ordered in the store" and line["price"] == 169


def test_a_stock_word_nobody_knows_yet_is_reported_and_not_taken_for_stock(site):
    out = run("avail", vendor="ikea", sku="70458203")
    assert out["available"] is False and "is SOMETHING_NEW at IKEA Amsterdam" in out["reason"]


def test_an_article_out_of_stock_or_not_stocked_is_not_available_and_says_why(site):
    out = run("avail", vendor="ikea", sku="00263850")
    assert out["available"] is False and "out of stock at IKEA Amsterdam" in out["reason"]
    out = run("avail", vendor="ikea", sku="80213074")
    assert out["available"] is False and "not stocked there" in out["reason"]
    out = run("avail", vendor="ikea", sku="11111111")
    assert out["available"] is False and "does not sell article 11111111" in out["reason"]


def test_another_vendor_is_not_asked_about(site):
    assert run("avail", vendor="lego", sku="3001") == {"available": False}
    assert site.asked == []


# --- quote -----------------------------------------------------------------------


def test_a_quote_is_the_store_s_prices_times_the_counts(site):
    out = run("quote", cart=cart(("ikea", "30275861", 2), ("ikea", "S39240787", 1)))
    assert out["price"] == pytest.approx(268.98)
    assert out["currency"] == "EUR" and out["cartId"] is None and out["qos"] == "standard"
    assert out["store"] == {"id": "088", "name": "IKEA Amsterdam", "url": "https://x/amsterdam/"}
    kallax, poang = out["lines"]
    assert (kallax["sku"], kallax["count"], kallax["unitPrice"], kallax["price"]) == ("30275861", 2, 99.99, 199.98)
    assert kallax["name"] == "KALLAX Shelving unit" and kallax["stock"] == "in stock"
    assert poang["stock"] == "only a few left"


def test_a_cart_without_skus_is_ordered_by_its_parts(site):
    parts = {
        "a": {"vendor": "ikea", "sku": "30275861", "count": 3, "count_per_sku": 1},
        "b": {"vendor": "ikea", "sku": "30275861", "count": 1, "count_per_sku": 1},
    }
    out = run("quote", cart={"qos": None, "parts": parts})
    assert out["lines"][0]["count"] == 4 and out["price"] == pytest.approx(399.96)


def test_a_quote_the_store_cannot_fill_fails_saying_why(site):
    with pytest.raises(Exception, match="BILLY Bookcase .00263850. is out of stock at IKEA Amsterdam"):
        run("quote", cart=cart(("ikea", "30275861", 1), ("ikea", "00263850", 1)))
    with pytest.raises(Exception, match="not by IKEA"):
        run("quote", cart=cart(("lego", "3001", 1)))
    with pytest.raises(Exception, match="has no vendor and SKU"):
        run("quote", cart={"parts": {"thing": {"count": 1}}})


def test_a_price_in_another_currency_is_refused(site):
    site.products["30275861"]["salesPrice"]["currencyCode"] = "SEK"
    with pytest.raises(Exception, match="prices in SEK, and this provider quotes in EUR"):
        run("quote", cart=cart(("ikea", "30275861", 1)))


# --- which store -----------------------------------------------------------------


def test_a_store_is_named_by_its_number_or_a_word_of_its_name(site):
    assert run("quote", {"store": "270"}, cart=cart(("ikea", "30275861", 1)))["store"]["name"] == "IKEA Utrecht"
    assert run("quote", {"store": "utrecht"}, cart=cart(("ikea", "30275861", 1)))["store"]["id"] == "270"
    assert run("quote", {"store": "Duiven"}, cart=cart(("ikea", "30275861", 1)))["store"]["id"] == "272"


def test_a_store_that_is_not_one_is_answered_with_the_stores_there_are(site):
    with pytest.raises(Exception, match=r"no IKEA store 'Berlin' in 'nl'; there are IKEA Amsterdam \(088\)"):
        run("avail", {"store": "Berlin"}, vendor="ikea", sku="30275861")
    with pytest.raises(Exception, match="names 3 IKEA stores"):
        run("avail", {"store": "IKEA"}, vendor="ikea", sku="30275861")
    # A store ikea.com hides is not one to quote at.
    with pytest.raises(Exception, match="no IKEA store 'Hidden'"):
        run("avail", {"store": "Hidden"}, vendor="ikea", sku="30275861")


def test_the_country_and_language_choose_the_site_asked(site):
    run("avail", {"country": "DE", "language": "de", "store": "088"}, vendor="ikea", sku="30275861")
    assert any(url.startswith("https://www.ikea.com/de/de/meta-data/") for url in site.asked)
    assert any(url.startswith("https://sik.search.blue.cdtapps.com/de/de/") for url in site.asked)


# --- what ikea.com said, kept ------------------------------------------------------


def test_answers_are_kept_and_not_asked_again(site):
    run("avail", vendor="ikea", sku="30275861")
    asked = len(site.asked)
    run("avail", vendor="ikea", sku="30275861")
    assert len(site.asked) == asked


def test_ikea_com_not_answering_is_said_as_such(site):
    site.fail = urllib.error.HTTPError("https://x", 503, "Service Unavailable", {}, None)
    with pytest.raises(Exception, match="ikea.com answered HTTP 503"):
        run("avail", vendor="ikea", sku="30275861")


def test_capabilities_and_orders_are_not_offered(site):
    with pytest.raises(Exception, match="Not supported by stores"):
        run("caps")
    with pytest.raises(Exception, match="Not implemented"):
        run("order", cartId=None)
