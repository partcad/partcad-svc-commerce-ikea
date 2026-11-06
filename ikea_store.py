#
# partcad-svc-commerce-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""The IKEA store nearest the user, as a PartCAD provider of the type 'store'.

PartCAD runs this file with 'runpy', once per call: the call is '__name__'
('avail', 'quote', ...), its input is the global 'request', and the answer is
whatever is left in the global 'output'. See "Providers" in PartCAD's
configuration documentation.

What it quotes is IKEA articles - a part declared with 'vendor: ikea' and an
IKEA article number as its 'sku', which is every product of
//pub/svc/commerce/ikea/products - at one IKEA store: the price IKEA asks in
that store's country, in its currency, and what the store says of its stock.
Both come from ikea.com's own product search, asked for the article at the
store, which is what the website's search page asks: IKEA publishes no other
API for either, and this one needs no key.

Where the user is comes from PartCAD, not from this provider's parameters:
'request["userInfo"]' says the country, the state and city of the user's
address, the language they read and the currency they pay in - and nothing
finer (PartCAD's "Country, Language, Currency and Location"). The country
decides the site asked (ikea.com/<country>/<language>/), its stores and its
prices; the language is the site's when IKEA has the site in it, else English.

Which store: the one the 'store' parameter names, by its number or a word of
its name, when the user set it - in ~/.partcad/config.yaml:

    parameters:
      //pub/svc/commerce/ikea:ikea-store:
        store: Utrecht

It is unset by default, and then the stores of the user's city are the ones to
choose from, else those of their state, else the country's; with one, that is
the store, and with several PartCAD is asked which is nearest
('partcad_user.choose_place'), which it answers from the user's location or
address without telling this provider either. When PartCAD cannot say, the
first of them by name is taken, and every answer says which store it is from
and how it was chosen.

The catalog is the European, American and Canadian ranges together, and every
product is offered at every store: what a store has is the store's to say. An
article the store's country does not sell - under the global article number in
Europe, under its own in the United States and Canada (catalog.yaml's
'markets') - is not available there, and a quote does not take it. Stock is
said, never refused: a quote is for what is asked, and says per line whether
the store had it ('In stock', 'Only a few left', 'Order in store', 'Out of
stock'), for the user to weigh. Nothing is put in a basket at ikea.com, so a
quote has no cart ID, and ordering is not implemented.

Answers are kept for an hour, and the list of stores for a day, under
~/.cache/partcad-ikea/ (or $PARTCAD_IKEA_CACHE). Using ikea.com is subject to
IKEA's terms of use.
"""

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

NOW = datetime.now(timezone.utc)

# What every product of //pub/svc/commerce/ikea says it is sold as.
VENDOR = "ikea"

if "request" not in globals():
    request = {"api": "caps"}

USER_AGENT = (
    "Mozilla/5.0 (compatible; partcad-ikea-store/%s; +https://github.com/partcad/partcad-svc-commerce-ikea)"
    % (request.get("partcad_version", "unknown"))
)

# How long what ikea.com said is kept: a price and a store's stock move during
# a day, the list of stores does not.
ANSWER_TTL = 3600
STORES_TTL = 24 * 3600
QUOTE_TTL = timedelta(hours=1)

# What a store's stock is said as ('availability[].status'), in words, and
# whether that is the store having it to sell: off its shelf, or ordered at its
# desk for collection or delivery later (a bed frame, a sofa). A word IKEA
# starts using later is reported as it is.
IN_STOCK = {
    "HIGH_IN_STOCK": "in stock",
    "MEDIUM_IN_STOCK": "in stock",
    "LOW_IN_STOCK": "only a few left",
    "PLACE_ORDER_IN_STORE": "ordered in the store",
}
NOT_IN_STOCK = {"OUT_OF_STOCK": "out of stock", "NOT_AVAILABLE": "not stocked there"}

# The countries that sell IKEA's range under article numbers of their own,
# which catalog.yaml keeps per product ('markets').
OWN_NUMBERS = ("us", "ca")

CATALOG = os.path.join(os.path.dirname(os.path.abspath(globals().get("__file__") or "ikea_store.py")), "catalog.yaml")

SEARCH = "https://sik.search.blue.cdtapps.com/%s/%s/search-result-page"
STORES = "https://www.ikea.com/%s/%s/meta-data/informera/stores-suggested-detailed.json"


class StoreError(Exception):
    """Something about the store or ikea.com, said the way the user can act on it."""


# --- where the user is, as PartCAD says it -------------------------------------


def _user_info():
    """'request["userInfo"]': the country, state, city, language and currency, and nothing finer.

    A PartCAD older than 'userInfo' handed every plugin the whole 'user'
    section; of that, the country, state and city are taken, and nothing else.
    """
    info = request.get("userInfo")
    if isinstance(info, dict):
        return info
    user = request.get("user") or {}
    return {
        key: user.get(field)
        for key, field in (("country", "countryCode"), ("state", "stateCode"), ("city", "city"))
        if user.get(field)
    }


USER = _user_info()
COUNTRY = str(USER.get("country") or "").strip().lower()
LANGUAGE = str(USER.get("language") or "en").strip().lower()
STATE = str(USER.get("state") or "").strip()
CITY = str(USER.get("city") or "").strip()
CURRENCY = str(USER.get("currency") or "").strip().upper() or None

# The store the user set, or nobody did and it is chosen (see 'store').
STORE = str((request.get("parameters") or {}).get("store") or "").strip() or None


def _fold(text):
    return " ".join(str(text or "").casefold().split())


def state_code(code):
    """A state as the user's address writes it: 'NH', for IKEA's 'NLNH' in the Netherlands."""
    code = str(code or "").strip().upper()
    if len(code) > 2 and COUNTRY and code.startswith(COUNTRY.upper()):
        code = code[len(COUNTRY) :]
    return code


# --- HTTP, and what it said, kept for a while ----------------------------------


def _cache_dir():
    root = os.environ.get("PARTCAD_IKEA_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "partcad-ikea")
    os.makedirs(root, exist_ok=True)
    return root


def _cache_path(url):
    return os.path.join(_cache_dir(), hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")


class NotThere(StoreError):
    """ikea.com has no such page: a country or a language it has no site in."""


def fetch(url, ttl):
    """The JSON at 'url', from the cache when it is younger than 'ttl' seconds."""
    path = _cache_path(url)
    try:
        if time.time() - os.path.getmtime(path) < ttl:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError):
        pass
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (400, 404):
            raise NotThere("ikea.com has no %s" % url) from None
        raise StoreError("ikea.com answered HTTP %d for %s" % (e.code, url)) from None
    except (urllib.error.URLError, OSError, ValueError) as e:
        raise StoreError("ikea.com could not be asked (%s): %s" % (url, e)) from None
    temporary = path + ".%d" % os.getpid()
    try:
        with open(temporary, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(temporary, path)
    except OSError:
        pass  # a cache that cannot be written is only slower
    return data


# --- the site and its stores -----------------------------------------------------

_site = {}


def site():
    """(country, language, stores): the ikea.com site asked, in the user's language where it has one."""
    if _site:
        return _site["country"], _site["language"], _site["stores"]
    if not COUNTRY:
        raise StoreError(
            "PartCAD did not say which country you are in: set 'country' in ~/.partcad/config.yaml "
            "(or PC_COUNTRY), or the 'countryCode' of your address in its 'user' section"
        )
    for language in dict.fromkeys((LANGUAGE, "en")):
        try:
            found = fetch(STORES % (COUNTRY, language), STORES_TTL)
        except NotThere:
            continue
        if isinstance(found, list):
            listed = [s for s in found if isinstance(s, dict) and s.get("id") and not s.get("hideStore")]
            _site.update(country=COUNTRY, language=language, stores=listed)
            return COUNTRY, language, listed
    raise StoreError("ikea.com has no site for the country '%s' in '%s' or English" % (COUNTRY.upper(), LANGUAGE))


def _address(candidate):
    return candidate.get("address") or {}


def _named(listed, wanted):
    """The store 'wanted' names: by its number ('088'), or by a word of its name ('Amsterdam')."""
    for candidate in listed:
        if str(candidate["id"]) == wanted:
            return candidate
    folded = _fold(wanted)
    named = [
        s
        for s in listed
        if folded in _fold(" ".join(str(s.get(k) or "") for k in ("name", "displayName", "displayNameAlternate")))
    ]
    if len(named) == 1:
        return named[0]
    if named:
        raise StoreError(
            "'%s' names %d IKEA stores in %s: %s; name one by its number"
            % (wanted, len(named), COUNTRY.upper(), ", ".join("%s (%s)" % (s.get("name"), s["id"]) for s in named))
        )
    raise StoreError(
        "there is no IKEA store '%s' in %s; there are %s"
        % (wanted, COUNTRY.upper(), ", ".join("%s (%s)" % (s.get("name"), s["id"]) for s in listed))
    )


def _place(candidate):
    """What PartCAD is handed to choose by, of a store: where it is."""
    address = _address(candidate)
    return {
        "id": str(candidate["id"]),
        "name": candidate.get("name"),
        "latitude": candidate.get("lat"),
        "longitude": candidate.get("lng"),
        "zip": address.get("zipCode"),
        "city": address.get("city"),
        "state": state_code(address.get("stateProvinceCode")),
    }


def _in_city(candidate):
    # 'Berlin,Tempelhof' is in Berlin.
    return any(_fold(part) == _fold(CITY) for part in str(_address(candidate).get("city") or "").split(","))


def nearby(listed):
    """(stores, where): the stores of the user's city, else of their state, else of the country."""
    if CITY:
        found = [s for s in listed if _in_city(s)]
        if found:
            return found, CITY
    if STATE:
        found = [s for s in listed if state_code(_address(s).get("stateProvinceCode")) == state_code(STATE)]
        if found:
            return found, STATE
    return listed, COUNTRY.upper()


_store = {}


def store():
    """(the store, how it was chosen)."""
    if _store:
        return _store["store"], _store["chosen"]
    _country, _language, listed = site()
    if not listed:
        raise StoreError("ikea.com lists no IKEA store in %s" % COUNTRY.upper())
    if STORE:
        found, chosen = _named(listed, STORE), "as configured"
    else:
        candidates, where = nearby(listed)
        candidates = sorted(candidates, key=lambda s: (str(s.get("name") or ""), str(s["id"])))
        if len(candidates) == 1:
            found, chosen = candidates[0], "the only IKEA store in %s" % where
        else:
            # PartCAD knows where the user is, and says which store, not where.
            asked = globals().get("partcad_user")
            nearest = asked.choose_place([_place(s) for s in candidates], what="store") if asked else None
            found = next((s for s in candidates if str(s["id"]) == str(nearest)), None)
            if found is not None:
                chosen = "the nearest IKEA store in %s" % where
            else:
                found = candidates[0]
                chosen = (
                    "the first of %d IKEA stores in %s: set your address or location, or the store, "
                    "in ~/.partcad/config.yaml to choose another" % (len(candidates), where)
                )
    _store.update(store=found, chosen=chosen)
    return found, chosen


def describe_store(found, chosen):
    return {"id": str(found["id"]), "name": found.get("name"), "url": found.get("storePageUrl"), "chosen": chosen}


# --- an article in the store's country ---------------------------------------------


def digits(sku):
    """An article number as the search takes it: its eight digits, without dots or the 'S' of a combination."""
    return re.sub(r"[^0-9]", "", str(sku))


_markets = {}


def markets():
    """{global article digits: {market: article}}, from catalog.yaml."""
    if not _markets:
        try:
            import yaml  # declared in partcad.yaml's 'pythonRequirements'
        except ImportError as e:
            raise StoreError("PyYAML is needed to read catalog.yaml: %s" % e) from None
        with open(CATALOG, encoding="utf-8") as f:
            catalog = yaml.safe_load(f) or {}
        for product in (catalog.get("products") or {}).values():
            facts = (product or {}).get("ikea") or {}
            if facts.get("article"):
                _markets[digits(facts["article"])] = dict(facts.get("markets") or {})
        _markets.setdefault("", {})
    return _markets


def local_article(sku):
    """The article number the store's country sells 'sku' under, or None when it does not sell it.

    Europe sells IKEA's range under its global article numbers, which are the
    catalog's; the United States and Canada mostly under their own, which are
    its 'markets'. An article that is not the catalog's is asked for as it is.
    """
    if COUNTRY not in OWN_NUMBERS:
        return str(sku)
    known = markets().get(digits(sku))
    if known is None:
        return str(sku)
    return known.get(COUNTRY)


def price_of(sales_price):
    try:
        return Decimal(str(sales_price["numeral"]))
    except (KeyError, TypeError, InvalidOperation):
        return None


class Offer:
    """What the store says of one article: what it is, its price, and its stock."""

    def __init__(self, sku, number, product, at):
        self.sku = sku
        self.number = number
        self.name = " ".join(str(product.get(k) or "") for k in ("name", "typeName")).strip()
        self.measure = product.get("itemMeasureReferenceText") or ""
        self.url = product.get("pipUrl")
        sales = product.get("salesPrice") or {}
        self.price = price_of(sales)
        self.currency = (sales.get("currencyCode") or "").upper() or None
        self.status = None
        self.store = at.get("name")
        for line in product.get("availability") or []:
            # The store's own shelf, rather than delivery to the door.
            if isinstance(line, dict) and line.get("type2") == "CASH_AND_CARRY":
                self.status = line.get("status")
                self.store = line.get("store") or self.store
                break

    @property
    def in_stock(self):
        return self.status in IN_STOCK

    @property
    def stock(self):
        """What the store's stock was said as, in words."""
        if self.status is None:
            return "not stocked there"
        return IN_STOCK.get(self.status) or NOT_IN_STOCK.get(self.status) or self.status

    def describe(self):
        return {
            "article": self.number,
            "name": self.name,
            "measure": self.measure,
            "url": self.url,
            "stock": self.stock,
            "inStock": self.in_stock,
        }


def offer(sku, at):
    """The store's Offer for an article, or None when its country does not sell it."""
    number = local_article(sku)
    if number is None or len(digits(number)) != 8:
        return None
    country, language, _listed = site()
    query = urllib.parse.urlencode({"q": digits(number), "size": 24, "types": "PRODUCT", "store": at["id"]})
    found = fetch("%s?%s" % (SEARCH % (country, language), query), ANSWER_TTL)
    items = (((found or {}).get("searchResultPage") or {}).get("products") or {}).get("main", {}).get("items") or []
    for item in items:
        product = (item or {}).get("product") or {}
        page = (product.get("pipUrl") or "").rstrip("/").lower()
        if digits(number) in (product.get("itemNo"), product.get("itemNoGlobal")) or page.endswith(digits(number)):
            return Offer(str(sku), str(number), product, at)
    return None


def why_not(sku, found):
    """Why the store's country cannot sell an article, or None when it can - whatever the store's stock."""
    if found is None:
        return "IKEA %s does not sell article %s" % (COUNTRY.upper(), sku)
    if found.price is None:
        return "%s (%s) has no price in IKEA %s" % (found.name, sku, COUNTRY.upper())
    return None


# --- the API -------------------------------------------------------------------


def order_lines(cart):
    """(vendor, SKU, how many of it to order), one per SKU, as PartCAD worked it out."""
    for name, part in (cart.get("parts") or {}).items():
        if not part.get("vendor") or not part.get("sku"):
            raise StoreError("'%s' is not something a store sells: it has no vendor and SKU" % name)
    if "skus" in cart:
        return [(line["vendor"], str(line["sku"]), int(line["count"])) for line in cart["skus"]]
    needed = {}
    for part in (cart.get("parts") or {}).values():
        key = (part["vendor"], str(part["sku"]))
        per_sku = max(int(part.get("count_per_sku") or 1), 1)
        needed[key] = needed.get(key, 0) + -(-int(part["count"]) // per_sku)
    return [(vendor, sku, count) for (vendor, sku), count in sorted(needed.items())]


def avail(vendor, sku):
    """Available wherever the store's country sells it: whether this store has it today is the quote's to say."""
    if vendor != VENDOR or not sku:
        return {"available": False}
    at, chosen = store()
    found = offer(sku, at)
    reason = why_not(sku, found)
    if reason:
        return {"available": False, "reason": reason, "store": describe_store(at, chosen)}
    return {"available": True, "stock": found.stock, "store": describe_store(at, chosen)}


def quote(cart):
    at, chosen = store()
    lines = []
    total = Decimal(0)
    currencies = set()
    for vendor, sku, count in order_lines(cart):
        if vendor != VENDOR:
            raise StoreError("SKU '%s' is sold by '%s', not by IKEA" % (sku, vendor))
        found = offer(sku, at)
        reason = why_not(sku, found)
        if reason:
            raise StoreError(reason)
        currencies.add(found.currency or CURRENCY)
        price = found.price * count
        total += price
        lines.append(dict(found.describe(), sku=sku, count=count, unitPrice=float(found.price), price=float(price)))
    currencies.discard(None)
    if len(currencies) > 1:
        raise StoreError("IKEA %s priced this cart in %s at once" % (COUNTRY.upper(), " and ".join(sorted(currencies))))
    answer = {
        "qos": cart.get("qos"),
        "price": float(total),
        "expire": (NOW + QUOTE_TTL).timestamp(),
        # Nothing was put in a basket at ikea.com.
        "cartId": None,
        "store": describe_store(at, chosen),
        "lines": lines,
    }
    # What IKEA priced it in; else what the user pays in, as PartCAD says it -
    # which is the store's country's. A quote saying neither is PartCAD's to
    # take as dollars, and to warn about.
    if currencies:
        answer["currency"] = currencies.pop()
    return answer


if __name__ == "caps":
    raise Exception("Not supported by stores")

elif __name__ == "avail":
    output = avail(request.get("vendor"), str(request.get("sku") or ""))

elif __name__ == "quote":
    output = quote(request["cart"])

elif __name__ == "order":
    raise Exception("Not implemented")

elif __name__ != "__main__":
    raise Exception("Unknown API: {}".format(__name__))
