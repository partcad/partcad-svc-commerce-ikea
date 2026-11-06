#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""One IKEA store, as a PartCAD provider of the type 'store'.

PartCAD runs this file with 'runpy', once per call: the call is '__name__'
('avail', 'quote', ...), its input is the global 'request', and the answer is
whatever is left in the global 'output'. See "Providers" in PartCAD's
configuration documentation.

What it quotes is IKEA articles - a part declared with 'vendor: ikea' and an
IKEA article number as its 'sku', which is every product of
//pub/svc/commerce/ikea/products - at one store: the price IKEA asks in that
store's country, and whether that store has it in stock to take home. Both come
from ikea.com's own product search, asked for the article number at the store,
which is what the website's search page asks: IKEA publishes no other API for
either, and this one needs no key. The store is a parameter, by its number or
its name (see 'stores'), and so are the country and the language of the site
it is asked through.

IKEA says how much a store has of an article in words, not numbers ('In stock',
'Only a few left', 'Order in store', 'Out of stock'), so a store that sells it -
off the shelf, or ordered at its desk - is taken to have as many as are asked
for, and a quote says which of those words it was quoted on. Nothing is put in
a basket at ikea.com, so a quote has no cart ID, and ordering is not
implemented.

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

USER_AGENT = "Mozilla/5.0 (compatible; partcad-ikea-store/%s; +https://github.com/partcad/partcad-repo-ikea)" % (
    request.get("partcad_version", "unknown")
)

# How long what ikea.com said is kept: a price and a store's stock move during
# a day, the list of stores does not.
ANSWER_TTL = 3600
STORES_TTL = 24 * 3600
QUOTE_TTL = timedelta(hours=1)

# What a store's stock is said as ('availability[].status'), and whether the
# store sells it: off its shelf, or ordered at its desk for collection or
# delivery later (a bed frame, a sofa). Anything else - a word IKEA starts using
# later - is reported as it is, and not taken for something the store sells.
IN_STOCK = {
    "HIGH_IN_STOCK": "in stock",
    "MEDIUM_IN_STOCK": "in stock",
    "LOW_IN_STOCK": "only a few left",
    "PLACE_ORDER_IN_STORE": "ordered in the store",
}
NOT_IN_STOCK = {"OUT_OF_STOCK": "out of stock", "NOT_AVAILABLE": "not sold there"}


def _parameter(name, env=None, default=None):
    """A provider parameter as a string, or the environment variable, or the default."""
    value = (request.get("parameters") or {}).get(name)
    value = str(value).strip() if value is not None else ""
    if not value and env:
        value = os.environ.get(env, "").strip()
    return value or default


# The site the store is asked through: ikea.com/<country>/<language>/. The
# country decides the stores there are, the prices and their currency.
COUNTRY = _parameter("country", "PARTCAD_IKEA_COUNTRY", "nl").lower()
LANGUAGE = _parameter("language", "PARTCAD_IKEA_LANGUAGE", "en").lower()
STORE = _parameter("store", "PARTCAD_IKEA_STORE", "088")
CURRENCY = _parameter("currency", None, "EUR").upper()

SEARCH = "https://sik.search.blue.cdtapps.com/%s/%s/search-result-page"
STORES = "https://www.ikea.com/%s/%s/meta-data/informera/stores-suggested-detailed.json"


class StoreError(Exception):
    """Something about the store or ikea.com, said the way the user can act on it."""


# --- HTTP, and what it said, kept for a while ----------------------------------


def _cache_dir():
    root = os.environ.get("PARTCAD_IKEA_CACHE") or os.path.join(os.path.expanduser("~"), ".cache", "partcad-ikea")
    os.makedirs(root, exist_ok=True)
    return root


def _cache_path(url):
    return os.path.join(_cache_dir(), hashlib.sha256(url.encode("utf-8")).hexdigest() + ".json")


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


# --- the store ---------------------------------------------------------------


def stores():
    """The stores of the country, as ikea.com lists them: [{'id', 'name', ...}]."""
    found = fetch(STORES % (COUNTRY, LANGUAGE), STORES_TTL)
    if not isinstance(found, list):
        raise StoreError("ikea.com does not list the stores of '%s/%s'" % (COUNTRY, LANGUAGE))
    return [s for s in found if isinstance(s, dict) and s.get("id") and not s.get("hideStore")]


def store():
    """The store 'store' names: by its number ('088'), or by a word of its name ('Amsterdam')."""
    listed = stores()
    for candidate in listed:
        if str(candidate["id"]) == STORE:
            return candidate
    wanted = STORE.casefold()
    named = [
        s
        for s in listed
        if wanted in " ".join(str(s.get(k) or "") for k in ("name", "displayName", "displayNameAlternate")).casefold()
    ]
    if len(named) == 1:
        return named[0]
    everything = ", ".join("%s (%s)" % (s.get("name"), s["id"]) for s in listed)
    if named:
        raise StoreError(
            "'%s' names %d IKEA stores in '%s': %s; name one by its number"
            % (STORE, len(named), COUNTRY, ", ".join("%s (%s)" % (s.get("name"), s["id"]) for s in named))
        )
    raise StoreError("there is no IKEA store '%s' in '%s'; there are %s" % (STORE, COUNTRY, everything))


# --- an article at the store ---------------------------------------------------


def digits(sku):
    """An article number as the search takes it: its eight digits, without dots or the 'S' of a combination."""
    return re.sub(r"[^0-9]", "", str(sku))


def price_of(sales_price):
    try:
        return Decimal(str(sales_price["numeral"]))
    except (KeyError, TypeError, InvalidOperation):
        return None


class Offer:
    """What the store says of one article: what it is, its price, and its stock."""

    def __init__(self, sku, product, at):
        self.sku = sku
        self.name = " ".join(str(product.get(k) or "") for k in ("name", "typeName")).strip()
        self.measure = product.get("itemMeasureReferenceText") or ""
        self.url = product.get("pipUrl")
        sales = product.get("salesPrice") or {}
        self.price = price_of(sales)
        self.currency = sales.get("currencyCode")
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
        return {"article": self.sku, "name": self.name, "measure": self.measure, "url": self.url, "stock": self.stock}


def offer(sku, at):
    """The store's Offer for an article, or None when the country does not sell it under that number."""
    number = digits(sku)
    if len(number) != 8:
        return None
    query = urllib.parse.urlencode({"q": number, "size": 24, "types": "PRODUCT", "store": at["id"]})
    found = fetch("%s?%s" % (SEARCH % (COUNTRY, LANGUAGE), query), ANSWER_TTL)
    items = (((found or {}).get("searchResultPage") or {}).get("products") or {}).get("main", {}).get("items") or []
    for item in items:
        product = (item or {}).get("product") or {}
        page = (product.get("pipUrl") or "").rstrip("/").lower()
        if number in (product.get("itemNo"), product.get("itemNoGlobal")) or page.endswith(number):
            return Offer(str(sku), product, at)
    return None


def why_not(sku, found, at):
    """Why the store cannot sell an article, or None when it can."""
    if found is None:
        return "IKEA %s does not sell article %s under that number" % (COUNTRY.upper(), sku)
    if found.price is None:
        return "%s (%s) has no price at %s" % (found.name, sku, found.store)
    if not found.in_stock:
        return "%s (%s) is %s at %s" % (found.name, sku, found.stock, found.store)
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
    if vendor != VENDOR or not sku:
        return {"available": False}
    at = store()
    reason = why_not(sku, offer(sku, at), at)
    if reason:
        return {"available": False, "reason": reason}
    return {"available": True}


def quote(cart):
    at = store()
    lines = []
    total = Decimal(0)
    for vendor, sku, count in order_lines(cart):
        if vendor != VENDOR:
            raise StoreError("SKU '%s' is sold by '%s', not by IKEA" % (sku, vendor))
        found = offer(sku, at)
        reason = why_not(sku, found, at)
        if reason:
            raise StoreError(reason)
        if found.currency and found.currency.upper() != CURRENCY:
            raise StoreError(
                "IKEA %s prices in %s, and this provider quotes in %s: set its 'currency' to %s"
                % (COUNTRY.upper(), found.currency, CURRENCY, found.currency)
            )
        price = found.price * count
        total += price
        lines.append(dict(found.describe(), sku=sku, count=count, unitPrice=float(found.price), price=float(price)))
    return {
        "qos": cart.get("qos"),
        "price": float(total),
        "currency": CURRENCY,
        "expire": (NOW + QUOTE_TTL).timestamp(),
        # Nothing was put in a basket at ikea.com.
        "cartId": None,
        "store": {"id": at["id"], "name": at.get("name"), "url": at.get("storePageUrl")},
        "lines": lines,
    }


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
