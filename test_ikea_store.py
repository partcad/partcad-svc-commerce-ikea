#
# partcad-svc-commerce-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Tests of the IKEA store provider, run the way PartCAD runs it, against answers in the shapes ikea.com gives.

Nothing here touches the network: every request is answered by 'Site' below,
and PartCAD's answer to which store is nearest by 'User'.

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


def shop(number, name, city, state, lat, lng, url=None, hidden=False):
    """A store of 'stores-suggested-detailed.json'."""
    return {
        "id": number,
        "name": name,
        "displayName": name,
        "lat": str(lat),
        "lng": str(lng),
        "address": {"city": city, "stateProvinceCode": state, "zipCode": "1000 AA"},
        "storePageUrl": url,
        "hideStore": hidden,
    }


STORES = {
    "nl": [
        shop("088", "IKEA Amsterdam", "Amsterdam", "NLNH", 52.30, 4.95, "https://x/amsterdam/"),
        shop("415", "IKEA Amersfoort", "Amersfoort", "NLUT", 52.18, 5.42),
        shop("270", "IKEA Utrecht", "Utrecht", "NLUT", 52.08, 5.07, "https://x/utrecht/"),
        shop("272", "IKEA Arnhem - Duiven", "Duiven", "NLGE", 51.94, 6.03),
        shop("999", "IKEA Hidden", "Amsterdam", "NLNH", 52.3, 4.9, hidden=True),
    ],
    "us": [
        shop("165", "IKEA Brooklyn", "Brooklyn", "USNY", 40.67, -74.01),
        shop("026", "IKEA Paramus", "Paramus", "USNJ", 40.92, -74.07),
        shop("154", "IKEA Elizabeth", "Elizabeth", "USNJ", 40.67, -74.17),
    ],
}


def product(item_no, name, type_name, price, status=None, combination=False, currency="EUR", global_no=None):
    """A product of the search's answer: 'searchResultPage.products.main.items[].product'."""
    found = {
        "name": name,
        "typeName": type_name,
        "itemNo": item_no,
        "itemNoGlobal": global_no or item_no,
        "itemType": "SPR" if combination else "ART",
        "pipUrl": "https://www.ikea.com/xx/en/p/%s-%s%s/" % (name.lower(), "s" if combination else "", item_no),
        "itemMeasureReferenceText": "147x147 cm",
        "salesPrice": {"currencyCode": currency, "numeral": price},
        "availability": [],
    }
    if status:
        found["availability"] = [
            {"type2": "HOME_DELIVERY", "status": "HIGH_IN_STOCK"},
            {"type2": "CASH_AND_CARRY", "status": status},
        ]
    return found


class Site:
    """ikea.com, as far as the provider asks it: a country's stores, and the search by article number."""

    def __init__(self):
        self.products = {
            "nl": {
                "30275861": product("30275861", "KALLAX", "Shelving unit", 99.99, "HIGH_IN_STOCK"),
                "39240787": product("39240787", "POÄNG", "Armchair", 69, "LOW_IN_STOCK", combination=True),
                "00263850": product("00263850", "BILLY", "Bookcase", 49.99, "OUT_OF_STOCK"),
                "80213074": product("80213074", "MICKE", "Desk", 79.99),
                "69385793": product("69385793", "MALM", "Bed frame, high", 169, "PLACE_ORDER_IN_STORE", True),
                "70458203": product("70458203", "PAX", "Wardrobe frame", 95, "SOMETHING_NEW"),
            },
            "us": {
                # KALLAX 77x147 cm is 802.758.87 in Europe and 206.316.63 here.
                "20631663": product(
                    "20631663", "KALLAX", "Shelf unit", 79.99, "HIGH_IN_STOCK", False, "USD", "80275887"
                ),
                "20522046": product("20522046", "BILLY", "Bookcase", 69.99, "LOW_IN_STOCK", False, "USD"),
            },
        }
        # The languages each country's site is in.
        self.languages = {"nl": ("nl", "en"), "us": ("en", "es"), "de": ("de", "en")}
        self.asked = []
        self.fail = None

    def __call__(self, req, timeout=None):
        url = req.full_url
        self.asked.append(url)
        assert req.get_header("User-agent").startswith("Mozilla/5.0 (compatible; partcad-ikea-store/")
        if self.fail:
            raise self.fail
        path = urllib.parse.urlsplit(url).path.strip("/").split("/")
        country, language = path[:2]
        if language not in self.languages.get(country, ()):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)
        if url.endswith("/stores-suggested-detailed.json"):
            return io.BytesIO(json.dumps(STORES.get(country, [])).encode())
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        found = self.products.get(country, {}).get(query["q"][0])
        items = [{"product": found}] if found else []
        # The search also answers with things it merely finds alike.
        items.append({"product": product("12345678", "LACK", "Side table", 9.99, "HIGH_IN_STOCK")})
        return io.BytesIO(json.dumps({"searchResultPage": {"products": {"main": {"items": items}}}}).encode())

    def searched(self):
        return [url for url in self.asked if "search-result-page" in url]


class User:
    """'partcad_user': PartCAD's answer to which store is nearest, and what it was asked."""

    def __init__(self, nearest=None):
        self.nearest = nearest
        self.asked = []

    def choose_place(self, places, what="place"):
        self.asked.append((what, places))
        return self.nearest


@pytest.fixture
def site(monkeypatch, tmp_path):
    answering = Site()
    monkeypatch.setattr(urllib.request, "urlopen", answering)
    monkeypatch.setenv("PARTCAD_IKEA_CACHE", str(tmp_path))
    return answering


AMSTERDAM = {"country": "NL", "state": "NH", "city": "Amsterdam", "language": "en", "currency": "EUR"}


def run(api, parameters=None, user_info=AMSTERDAM, partcad_user=None, **request):
    request.update({"api": api, "partcad_version": "0.9.4", "parameters": parameters or {}})
    if user_info is not None:
        request["userInfo"] = dict(user_info)
    init = {"request": request, "partcad_user": partcad_user or User()}
    return runpy.run_path(SCRIPT, init_globals=init, run_name=api).get("output")


def cart(*lines):
    return {"qos": "standard", "skus": [{"vendor": v, "sku": s, "count": c, "parts": ["p"]} for v, s, c in lines]}


# --- avail -----------------------------------------------------------------------


def test_an_article_the_country_sells_is_available_at_its_store(site):
    out = run("avail", vendor="ikea", sku="30275861")
    assert out["available"] is True and out["stock"] == "in stock"
    assert out["store"] == {
        "id": "088",
        "name": "IKEA Amsterdam",
        "url": "https://x/amsterdam/",
        "chosen": "the only IKEA store in Amsterdam",
    }
    (search,) = site.searched()
    assert search.startswith("https://sik.search.blue.cdtapps.com/nl/en/search-result-page?")
    assert "store=088" in search and "q=30275861" in search


@pytest.mark.parametrize("sku", ["00263850", "80213074", "70458203", "S69385793", "S39240787"])
def test_whatever_the_store_s_stock_it_is_available(site, sku):
    # Out of stock, not stocked, a word nobody knows yet: the user weighs that.
    assert run("avail", vendor="ikea", sku=sku)["available"] is True


def test_what_the_country_does_not_sell_is_not_available_and_says_why(site):
    out = run("avail", vendor="ikea", sku="11111111")
    assert out["available"] is False and "IKEA NL does not sell article 11111111" in out["reason"]


def test_another_vendor_is_not_asked_about(site):
    assert run("avail", vendor="lego", sku="3001") == {"available": False}
    assert site.asked == []


# --- quote -----------------------------------------------------------------------


def test_a_quote_is_the_store_s_prices_times_the_counts_in_its_currency(site):
    out = run("quote", cart=cart(("ikea", "30275861", 2), ("ikea", "S39240787", 1)))
    assert out["price"] == pytest.approx(268.98)
    assert out["currency"] == "EUR" and out["cartId"] is None and out["qos"] == "standard"
    assert out["store"]["id"] == "088"
    kallax, poang = out["lines"]
    assert (kallax["sku"], kallax["count"], kallax["unitPrice"], kallax["price"]) == ("30275861", 2, 99.99, 199.98)
    assert kallax["name"] == "KALLAX Shelving unit" and kallax["stock"] == "in stock" and kallax["inStock"]
    assert poang["stock"] == "only a few left"


def test_a_quote_says_the_stock_and_is_not_refused_for_it(site):
    out = run("quote", cart=cart(("ikea", "00263850", 1), ("ikea", "80213074", 1), ("ikea", "70458203", 1)))
    assert [(line["stock"], line["inStock"]) for line in out["lines"]] == [
        ("out of stock", False),
        ("not stocked there", False),
        ("SOMETHING_NEW", False),
    ]
    assert out["price"] == pytest.approx(49.99 + 79.99 + 95)


def test_a_cart_without_skus_is_ordered_by_its_parts(site):
    parts = {
        "a": {"vendor": "ikea", "sku": "30275861", "count": 3, "count_per_sku": 1},
        "b": {"vendor": "ikea", "sku": "30275861", "count": 1, "count_per_sku": 1},
    }
    out = run("quote", cart={"qos": None, "parts": parts})
    assert out["lines"][0]["count"] == 4 and out["price"] == pytest.approx(399.96)


def test_a_quote_the_country_cannot_fill_fails_saying_why(site):
    with pytest.raises(Exception, match="IKEA NL does not sell article 11111111"):
        run("quote", cart=cart(("ikea", "30275861", 1), ("ikea", "11111111", 1)))
    with pytest.raises(Exception, match="not by IKEA"):
        run("quote", cart=cart(("lego", "3001", 1)))
    with pytest.raises(Exception, match="has no vendor and SKU"):
        run("quote", cart={"parts": {"thing": {"count": 1}}})


def test_without_a_currency_from_ikea_the_user_s_is_said(site):
    del site.products["nl"]["30275861"]["salesPrice"]["currencyCode"]
    assert run("quote", cart=cart(("ikea", "30275861", 1)))["currency"] == "EUR"
    # And with neither, nothing is made up: PartCAD takes it as dollars, and warns.
    info = dict(AMSTERDAM, currency=None)
    assert "currency" not in run("quote", user_info=info, cart=cart(("ikea", "30275861", 1)))


def test_a_cart_priced_in_two_currencies_at_once_is_refused(site):
    site.products["nl"]["30275861"]["salesPrice"]["currencyCode"] = "SEK"
    with pytest.raises(Exception, match="priced this cart in EUR and SEK at once"):
        run("quote", cart=cart(("ikea", "30275861", 1), ("ikea", "00263850", 1)))


# --- where the user is -------------------------------------------------------------


def test_the_country_and_language_choose_the_site_asked(site):
    run("avail", user_info={"country": "NL", "language": "nl"}, partcad_user=User("088"), vendor="ikea", sku="30275861")
    assert all("/nl/nl/" in url for url in site.asked)


def test_a_language_the_site_is_not_in_falls_back_to_english(site):
    run("avail", user_info={"country": "NL", "language": "fr"}, partcad_user=User("088"), vendor="ikea", sku="30275861")
    assert any("/nl/fr/" in url for url in site.asked)
    assert all("/nl/en/" in url for url in site.searched())


def test_without_a_country_the_user_is_told_how_to_say_it(site):
    with pytest.raises(Exception, match="did not say which country you are in"):
        run("avail", user_info={}, vendor="ikea", sku="30275861")


def test_a_country_ikea_has_no_site_in_is_said_as_such(site):
    with pytest.raises(Exception, match="has no site for the country 'ZZ'"):
        run("avail", user_info={"country": "ZZ"}, vendor="ikea", sku="30275861")


def test_an_older_partcad_s_user_section_is_read_for_its_country_state_and_city_only(site):
    user = {"firstName": "Ann", "line1": "Damrak 1", "city": "Amsterdam", "stateCode": "NH", "countryCode": "NL"}
    out = run("avail", user_info=None, vendor="ikea", sku="30275861", user=user)
    assert out["store"]["id"] == "088"


# --- which store -----------------------------------------------------------------


def test_the_one_store_of_the_user_s_city(site):
    user = User()
    out = run(
        "quote",
        user_info=dict(AMSTERDAM, city="Utrecht", state="UT"),
        partcad_user=user,
        cart=cart(("ikea", "30275861", 1)),
    )
    assert out["store"]["id"] == "270" and out["store"]["chosen"] == "the only IKEA store in Utrecht"
    assert user.asked == []


def test_of_several_in_the_user_s_state_partcad_says_which_is_nearest(site):
    user = User("415")
    info = {"country": "NL", "state": "UT", "city": "Zeist"}
    out = run("quote", user_info=info, partcad_user=user, cart=cart(("ikea", "30275861", 1)))
    assert out["store"]["id"] == "415" and out["store"]["chosen"] == "the nearest IKEA store in UT"
    ((what, places),) = user.asked
    # Asked about the state's stores only, each saying where it is - as
    # PartCAD matches the user's address, without IKEA's country prefix.
    assert what == "store"
    assert [(p["id"], p["city"], p["state"]) for p in places] == [("415", "Amersfoort", "UT"), ("270", "Utrecht", "UT")]
    assert places[0]["latitude"] == "52.18" and places[0]["zip"] == "1000 AA"


def test_when_partcad_cannot_say_the_first_by_name_is_taken_and_said(site):
    out = run("quote", user_info={"country": "NL"}, partcad_user=User(None), cart=cart(("ikea", "30275861", 1)))
    # 'IKEA Amersfoort' comes before 'IKEA Amsterdam'.
    assert out["store"]["id"] == "415"
    assert out["store"]["chosen"].startswith("the first of 4 IKEA stores in NL: set your address or location")


def test_a_store_the_user_set_is_named_by_its_number_or_a_word_of_its_name(site):
    for named, number in (("270", "270"), ("utrecht", "270"), ("Duiven", "272")):
        out = run("quote", {"store": named}, partcad_user=User("088"), cart=cart(("ikea", "30275861", 1)))
        assert out["store"]["id"] == number and out["store"]["chosen"] == "as configured"


def test_a_store_that_is_not_one_is_answered_with_the_stores_there_are(site):
    with pytest.raises(Exception, match=r"no IKEA store 'Berlin' in NL; there are IKEA Amsterdam \(088\)"):
        run("avail", {"store": "Berlin"}, vendor="ikea", sku="30275861")
    with pytest.raises(Exception, match="names 4 IKEA stores"):
        run("avail", {"store": "IKEA"}, vendor="ikea", sku="30275861")
    # A store ikea.com hides is not one to quote at.
    with pytest.raises(Exception, match="no IKEA store 'Hidden'"):
        run("avail", {"store": "Hidden"}, vendor="ikea", sku="30275861")


# --- the United States and Canada ---------------------------------------------------

NEW_JERSEY = {"country": "US", "state": "NJ", "language": "en", "currency": "USD"}


def test_an_american_store_is_asked_for_the_american_article(site):
    out = run("quote", user_info=NEW_JERSEY, partcad_user=User("026"), cart=cart(("ikea", "80275887", 2)))
    assert out["currency"] == "USD" and out["price"] == pytest.approx(159.98)
    (line,) = out["lines"]
    assert line["sku"] == "80275887" and line["article"] == "20631663"
    assert all("q=20631663" in url and "/us/en/" in url for url in site.searched())


def test_the_same_thing_under_an_american_number_of_its_own(site):
    # BILLY 80x28x202 cm white: 002.638.50 in Europe, 205.220.46 in America.
    out = run("avail", user_info=NEW_JERSEY, partcad_user=User("154"), vendor="ikea", sku="00263850")
    assert out["available"] is True and out["store"]["id"] == "154"


def test_what_america_does_not_sell_is_not_available_there(site):
    # The European MALM: the American one is Queen-size.
    out = run("avail", user_info=NEW_JERSEY, partcad_user=User("154"), vendor="ikea", sku="S69385793")
    assert out["available"] is False and "IKEA US does not sell article S69385793" in out["reason"]
    assert site.searched() == []


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
