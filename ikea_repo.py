#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""A PartCAD 'basic' repository plugin that serves IKEA products as parts.

Published as ``//pub/svc/commerce/ikea/products``. Its child packages are IKEA's own
categories, two levels deep, and the parts of a category are the IKEA products
in it that a model can be found of in one of PartCAD's model-site collections
(``//pub/collection/<site>``). Every product is a part of type ``alias`` of
that model, placed the way every product of this package is placed:

    //pub/svc/commerce/ikea/products/storage-furniture/bookcases-shelving-units:kallax-shelving-unit-77x147
        -> //pub/collection/printables/models:1787571;file=7454422   (scaled 25:1)

What the products are, and which models they are drawn with, is the catalog
beside this script (catalog.yaml), not anything asked of IKEA or of a model site
while serving: a package answers the same thing on every machine, and the model
sites are asked by their own plugins, when a part is built.

Every package that holds products searches them itself ('searchKinds'):
``pc search parts -P //pub/svc/commerce/ikea/products... -k poang`` finds the POÄNG
armchair, as does ``-k 392.407.87`` (its article number) or ``-k "lounge chair"``.

The plugin's one parameter, 'sources', is which model sites to draw a product
with, best first: a product is drawn with its model on the first of them that
has one, and a product none of them has a model of is not served at all - it is
not listed, found or searched. That is the rule of this package, and the user's
to narrow: 'printables,makerworld' is only the products that build without a
sign-in anywhere.

PartCAD runs this script once per key (via runpy) with the key in
``request["key"]``; the answer goes in ``output``.
"""

import os
import re
import sys
import unicodedata
import urllib.parse

# When this plugin's answers stopped meaning what they meant before. PartCAD
# keys everything it caches of this package on it, so raise it with any change
# to what is answered - here or in catalog.yaml.
#
# v1: IKEA products as aliases of models in //pub/collection, by category, each
#     category supplied by the package's own IKEA store.
CACHE_VERSION = 1

CATALOG = os.path.join(os.path.dirname(os.path.abspath(__file__)), "catalog.yaml")

# The model-site collections, and what each calls itself on a page.
SITES = {
    "printables": "Printables",
    "makerworld": "MakerWorld",
    "grabcad": "GrabCAD",
    "thingiverse": "Thingiverse",
    "cults3d": "Cults3D",
}
COLLECTION = "//pub/collection/%s/models"
DEFAULT_SOURCES = "printables,makerworld,grabcad,thingiverse,cults3d"

VENDOR = "ikea"
WEB = "https://www.ikea.com/nl/en/"

# Where a product is quoted: this package's own provider, which quotes an IKEA
# article at one IKEA store (ikea_store.py). Named by where the index puts it,
# as a store's other products are; a PartCAD that cannot load it passes over it.
SUPPLIERS = ("//pub/svc/commerce/ikea:ikea-store",)

DEFAULT_LIMIT = 20

# Other words for what a product is, so that a search for one finds the other.
# Matched both ways, a word at a time.
SYNONYMS = (
    ("sofa", "couch", "settee"),
    ("bookcase", "bookshelf", "shelving", "shelves", "shelf"),
    ("armchair", "chair"),
    ("wardrobe", "closet", "armoire"),
    ("tv", "television", "media"),
    ("table", "desk"),
    ("lamp", "light", "lighting"),
    ("children", "childrens", "kids", "kid", "child"),
    ("stool", "seat"),
    ("pegboard", "peg", "board"),
)


def warn(message):
    """Say something to the user. PartCAD reports a plugin's stderr, line by line."""
    print(message, file=sys.stderr)


# --- the catalog -------------------------------------------------------------


def load(path=CATALOG):
    """The catalog: {"categories": {...}, "products": {...}}."""
    import yaml  # declared in partcad.yaml's 'pythonRequirements'

    with open(path, encoding="utf-8") as f:
        catalog = yaml.safe_load(f) or {}
    catalog.setdefault("categories", {})
    catalog.setdefault("products", {})
    return catalog


def article(number):
    """IKEA's article number as it prints it: '302.758.61', 'S693.857.93'."""
    number = str(number)
    prefix, digits = (number[0].upper(), number[1:]) if number[:1].isalpha() else ("", number)
    if len(digits) == 8 and digits.isdigit():
        return "%s%s.%s.%s" % (prefix, digits[:3], digits[3:6], digits[6:])
    return number


def sources_of(request):
    """The model sites to draw products with, best first, from the 'sources' parameter."""
    text = ((request or {}).get("parameters") or {}).get("sources") or DEFAULT_SOURCES
    sources = []
    for site in re.split(r"[\s,]+", str(text).strip().lower()):
        if not site:
            continue
        if site not in SITES:
            warn("ikea: 'sources' names '%s', which is not one of %s" % (site, ", ".join(SITES)))
            continue
        if site not in sources:
            sources.append(site)
    return sources


def model_of(product, sources):
    """The model a product is drawn with: the first of its models on the best of 'sources'; None if none is."""
    ranked = [m for m in product.get("models") or [] if m.get("site") in sources]
    if not ranked:
        return None
    return min(ranked, key=lambda m: sources.index(m["site"]))


def name_part(value):
    """A model's file named in a part name: what PartCAD's grammar cannot hold there is refused."""
    value = str(value)
    if any(c in value for c in ",;="):
        raise ValueError("a file name with ',', ';' or '=' cannot be named in a part name: %r" % value)
    return value


def source_of(model):
    """The part of the collection that a model is: '//pub/collection/<site>/models:<model>;file=<file>'."""
    name = str(model["model"])
    if model.get("file") not in (None, ""):
        name += ";file=" + name_part(model["file"])
    return "%s:%s" % (COLLECTION % model["site"], name)


def describe(product, model):
    """What 'pc list parts' says of a product: IKEA's words, then whose model it is drawn with."""
    ikea = product.get("ikea") or {}
    what = " ".join(w for w in (ikea.get("name"), ikea.get("type")) if w)
    details = ", ".join(str(v) for v in (ikea.get("size"), ikea.get("colour")) if v)
    text = "IKEA %s%s" % (what, (", " + details) if details else "")
    if ikea.get("article"):
        text += " (%s)" % article(ikea["article"])
    by = " by %s" % model["author"] if model.get("author") else ""
    licence = " (%s)" % model["license"] if model.get("license") else ""
    text += ". Drawn with '%s'%s on %s%s" % (
        model.get("title") or model["model"],
        by,
        SITES[model["site"]],
        licence,
    )
    scale = model.get("scale")
    if scale not in (None, 1, 1.0):
        text += ", scaled %s:1" % ("%g" % float(scale))
    if model.get("placed") == "as-drawn":
        text += ", placed as its designer drew it"
    return text + "."


def part_config(product, model):
    """The declaration of a product: an alias of its model, placed by the package's convention."""
    ikea = product.get("ikea") or {}
    config = {
        "type": "alias",
        "source": source_of(model),
        "desc": describe(product, model),
        "vendor": VENDOR,
    }
    if ikea.get("article"):
        config["sku"] = str(ikea["article"])
    if ikea.get("url"):
        config["url"] = ikea["url"]
    if model.get("placed") != "as-drawn":
        if model.get("offset") is not None:
            config["offset"] = model["offset"]
        if model.get("scale") not in (None, 1, 1.0):
            config["scale"] = model["scale"]
    return config


# --- search ------------------------------------------------------------------


def fold(text):
    """Lower case, without diacritics, an article number without its dots: what search compares."""
    text = unicodedata.normalize("NFKD", str(text))
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    text = re.sub(r"(?<=\d)\.(?=\d)", "", text)
    return text


def words(text):
    return [w for w in re.split(r"[^0-9a-z]+", fold(text)) if w]


_SYNONYM = {}
for _group in SYNONYMS:
    for _word in _group:
        _SYNONYM.setdefault(_word, set()).update(_group)


def haystack(name, product, categories):
    """Every word a product may be found by, and its IKEA name's words apart."""
    ikea = product.get("ikea") or {}
    texts = [name, product.get("category", "")]
    texts += [ikea.get(k) or "" for k in ("name", "type", "size", "colour", "article")]
    texts += list(product.get("keywords") or [])
    path = product.get("category", "")
    while path:
        texts.append((categories.get(path) or {}).get("name") or "")
        path = path.rpartition("/")[0]
    found = set()
    for text in texts:
        found.update(words(text))
    return found, set(words(ikea.get("name") or ""))


def score(query, name, product, categories):
    """How well a product answers a query; 0 when some word of it is not found at all."""
    found, brand = haystack(name, product, categories)
    total = 0
    for word in words(query):
        alternatives = _SYNONYM.get(word, set()) | {word}
        if word in brand:
            total += 4
        elif any(a in found for a in alternatives):
            total += 2
        elif any(word in f for f in found):
            total += 1  # 'kall' finds KALLAX, '3027586' its article number
        else:
            return 0
    return total


# --- what PartCAD is answered ------------------------------------------------


class Ikea:
    def __init__(self, request=None, catalog=None):
        self.request = request or {}
        self.catalog = catalog if catalog is not None else load()
        self.sources = sources_of(self.request)
        self.categories = self.catalog["categories"]

    def products(self, category):
        """{name: (product, model)} of the products served in 'category' itself, in catalog order."""
        served = {}
        for name, product in self.catalog["products"].items():
            if product.get("category") != category:
                continue
            model = model_of(product, self.sources)
            if model is not None:
                served[name] = (product, model)
        return served

    def served_below(self, category):
        """Whether 'category', or any category under it, serves a product."""
        prefix = category + "/"
        for product in self.catalog["products"].values():
            path = product.get("category", "")
            if (path == category or path.startswith(prefix)) and model_of(product, self.sources):
                return True
        return False

    def children(self, category):
        """The categories directly under 'category' ('' is the root) that serve something, by their last name."""
        depth = 0 if not category else category.count("/") + 1
        names = []
        for path in self.categories:
            parent = path.rpartition("/")[0]
            if parent == category and path.count("/") == depth and self.served_below(path):
                names.append(path.rpartition("/")[2])
        return names

    def meta(self, category):
        if not category:
            return {
                "desc": "IKEA products, by IKEA's own categories: each one a part of type 'alias' of a model of it "
                "in one of PartCAD's model-site collections (//pub/collection). Only the products that a model "
                "of can be found of are here.",
                "url": WEB,
                "manufacturable": False,
                "objectKinds": [],
            }
        info = self.categories.get(category) or {}
        served = self.products(category)
        names = sorted({(p.get("ikea") or {}).get("name") for p, _ in served.values()} - {None})
        desc = "IKEA's '%s'" % (info.get("name") or category)
        if info.get("ikea"):
            desc += " (%s on ikea.com)" % info["ikea"]
        if names:
            desc += ": " + ", ".join(names)
        meta = {"desc": desc + ".", "manufacturable": False, "objectKinds": ["part"] if served else []}
        if served:
            meta["searchKinds"] = ["part"]
            meta["suppliers"] = {name: {} for name in SUPPLIERS}
        return meta

    def search(self, category, query):
        fields = urllib.parse.parse_qs(query)
        text = " ".join(fields.get("q", [])).strip()
        try:
            limit = int((fields.get("limit") or ["0"])[0] or 0)
        except ValueError:
            limit = 0
        if not text:
            return {}
        ranked = []
        for index, (name, (product, model)) in enumerate(self.products(category).items()):
            points = score(text, name, product, self.categories)
            if points:
                ranked.append((-points, index, name, part_config(product, model)))
        ranked.sort()
        return {name: config for _, _, name, config in ranked[: limit or DEFAULT_LIMIT]}

    def get(self, key):
        """One key of the repository protocol; None for a key there is no answer to."""
        # A child package's keys come prefixed with its folder: the category.
        category, rest = "", key
        while True:
            head, _, tail = rest.partition("/")
            path = (category + "/" + head) if category else head
            if tail and path in self.categories:
                category, rest = path, tail
                continue
            break
        if rest == "meta":
            return self.meta(category)
        if rest == "deps":
            return self.children(category)
        if rest.startswith("search/"):
            kind, _, query = rest[len("search/") :].partition("?")
            return self.search(category, query) if kind == "part" else {}
        if rest.startswith("objects/"):
            kind, _, name = rest[len("objects/") :].partition("/")
            if kind != "part":
                return {} if not name else None
            served = self.products(category)
            if not name:
                return {n: part_config(p, m) for n, (p, m) in served.items()}
            if name not in served:
                return None
            product, model = served[name]
            return part_config(product, model)
        return None


def get(key):
    try:
        return Ikea(globals().get("request")).get(key)
    except Exception as e:  # said on stderr, so that PartCAD reports it with the key
        warn("ikea: answering '%s': %s: %s" % (key, type(e).__name__, e))
        return None


# Last, so that everything above is defined when PartCAD runs this.
if __name__ == "get":
    output = {"result": get(request["key"])}  # noqa: F821 - injected by the runtime
else:
    output = {}
