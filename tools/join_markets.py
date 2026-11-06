#
# partcad-svc-commerce-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Find each product of catalog.yaml in IKEA's catalogues of the United States and Canada.

A product's 'article' is IKEA's global article number, which every European
IKEA sells it under. The United States and Canada sell much the same range
under numbers of their own - KALLAX 77x147 cm is 802.758.87 in Europe and
206.316.63 in both - so a store there cannot be asked for the global one. Their
search answers say which global article each of their own is
('itemNoGlobal'), so each product is looked for by its name and type, and
matched on that.

What is found is each product's 'markets': {us: ..., ca: ...}, the article
number there; a market a product is not sold in is left out. With '--write'
the 'markets' of every product in catalog.yaml is rewritten - every product
gets one, even an empty one, so that a product that inherits its 'ikea'
facts from another ('<<: *kallax') does not inherit where that one is sold.
Without it, what would change is printed.

It is a maintainer's tool and reads the network; the tests do not.

    python tools/join_markets.py [--write]   # needs PyYAML
"""

import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import ikea_repo  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; partcad-svc-commerce-ikea-check; +https://github.com/partcad)"
SEARCH = "https://sik.search.blue.cdtapps.com/%s/search-result-page?%s"
# The markets with numbers of their own, and the site each is asked through.
MARKETS = {"us": "us/en", "ca": "ca/en"}


def digits(number):
    return re.sub(r"[^0-9]", "", str(number))


def search(locale, query, size=48):
    url = SEARCH % (locale, urllib.parse.urlencode({"q": query, "size": size, "types": "PRODUCT"}))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        found = json.load(response)
    items = ((found.get("searchResultPage") or {}).get("products") or {}).get("main", {}).get("items") or []
    return [item.get("product") or {} for item in items]


# What two markets call the same kind of thing.
TYPE_FAMILIES = ({"shelving unit", "shelf unit"}, {"tv bench", "tv unit"}, {"trolley", "utility cart"})


def _type(text):
    return " ".join(ikea_repo.fold(text or "").split())


def same_type(a, b):
    a, b = _type(a), _type(b)
    return a == b or any(a in family and b in family for family in TYPE_FAMILIES)


def same_colour(a, b):
    def norm(text):
        return " ".join(ikea_repo.fold(text or "").replace("gray", "grey").split())

    return norm(a) == norm(b)


def metric(size):
    """'80x28x202 cm' out of the Canadian '80x28x202 cm (31 1/2x11x79 1/2 ")'."""
    return (size or "").split(" (")[0].strip()


def _entry(found, combination):
    number = digits(found.get("itemNo") or "")
    if len(number) != 8:
        return None
    return {
        # A combination is sold as one, and its number keeps its 'S'.
        "article": ("S" if combination else "") + number,
        "global": digits(found.get("itemNoGlobal") or ""),
        "size": found.get("itemMeasureReferenceText"),
        "url": found.get("pipUrl"),
        "name": "%s %s" % (found.get("name"), found.get("typeName")),
    }


def _candidates(locale, facts):
    seen = set()
    for query in ("%s %s" % (facts["name"], facts.get("type") or ""), facts["name"]):
        for found in search(locale, query.strip()):
            if found.get("itemNo") not in seen:
                seen.add(found.get("itemNo"))
                yield found
        time.sleep(0.5)


def find(locale, facts, match):
    """The first of the market's answers for a product that 'match' accepts, as an _entry, or None."""
    combination = str(facts["article"])[:1].upper() == "S"
    for found in _candidates(locale, facts):
        if match(found):
            return _entry(found, combination)
    return None


def markets_of(product):
    """{market: _entry or None}: where the product is sold, and under what number.

    The same article first: an answer whose 'itemNoGlobal' is the product's
    global number. Failing that, the same thing under a number of the market's
    own - BILLY 80x28x202 cm white is 205.220.46 in North America, not
    002.638.50 - which is the same name, the same kind of thing, the same size
    and the same colour. Size is compared in centimetres, which only the
    Canadian site gives, so the United States is then matched to Canada's
    article.
    """
    facts = product["ikea"]
    wanted = digits(facts["article"])
    found = {
        m: find(locale, facts, lambda f: digits(f.get("itemNoGlobal") or "") == wanted) for m, locale in MARKETS.items()
    }
    if found["ca"] is None:

        def equivalent(f):
            return (
                ikea_repo.fold(f.get("name") or "") == ikea_repo.fold(facts["name"])
                and same_type(f.get("typeName"), facts.get("type"))
                and (not facts.get("size") or metric(f.get("itemMeasureReferenceText")) == facts["size"])
                and same_colour(f.get("validDesignText"), facts.get("colour"))
            )

        found["ca"] = find(MARKETS["ca"], facts, equivalent)
    if found["us"] is None and found["ca"] is not None:
        canada = found["ca"]
        found["us"] = find(
            MARKETS["us"],
            facts,
            lambda f: digits(f.get("itemNo") or "") == digits(canada["article"])
            or (canada["global"] and digits(f.get("itemNoGlobal") or "") == canada["global"]),
        )
    return found


def rewrite(text, key, markets):
    """catalog.yaml's text with the 'markets' of product 'key' set to 'markets' ({market: article})."""
    lines = text.split("\n")
    start = lines.index("  %s:" % key)
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^  \S", lines[i]) or not lines[i].strip()), None)
    end = len(lines) if end is None else end
    block = lines[start:end]
    # Drop what an earlier run wrote.
    kept, skipping = [], False
    for line in block:
        if re.match(r"^      markets:", line):
            skipping = True
            continue
        if skipping and re.match(r"^        ", line):
            continue
        skipping = False
        kept.append(line)
    url = next(i for i, line in enumerate(kept) if re.match(r"^      url:", line))
    if markets:
        inserted = ["      markets:"] + ['        %s: "%s"' % (m, a) for m, a in sorted(markets.items())]
    else:
        inserted = ["      markets: {}"]
    kept[url + 1 : url + 1] = inserted
    return "\n".join(lines[:start] + kept + lines[end:])


def main(argv):
    write = "--write" in argv
    catalog = ikea_repo.load()
    with open(ikea_repo.CATALOG, encoding="utf-8") as f:
        text = f.read()
    changed = 0
    for key, product in catalog["products"].items():
        found = markets_of(product)
        markets = {market: entry["article"] for market, entry in found.items() if entry}
        said = product["ikea"].get("markets")
        line = "%-50s %s" % (key, ", ".join("%s %s (%s)" % (m, e["article"], e["size"]) for m, e in found.items() if e))
        print(line if markets else "%-50s sold in neither" % key)
        if said != markets:
            changed += 1
            text = rewrite(text, key, markets)
    if write and changed:
        with open(ikea_repo.CATALOG, "w", encoding="utf-8") as f:
            f.write(text)
    print("%d product(s) %s" % (changed, "rewritten" if write else "would change"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
