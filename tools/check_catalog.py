#
# partcad-repo-ikea, 2026
#
# Licensed under Apache License, Version 2.0.
#
"""Ask IKEA and the model sites again whether catalog.yaml still holds.

The rule of this package is that a product is in it only while a model of it
can be found in one of the model-site collections. This checks that rule, and
what the catalog says IKEA sells, against the live sites:

- IKEA: ikea.com's own product search (what its search page reads), for the
  catalog's locale, finds the article, under the name and type the catalog says.
- Printables: the model is there, and so is the file, by its number.
- MakerWorld: the model is there, and so is the print profile whose preview it is.
- GrabCAD: the model is there, and lists the file, by its name.
- Thingiverse and Cults3D take credentials to ask anything, so their entries are
  reported as not checked.

Nothing is changed: what disagrees is printed, and the exit status is 1 when
anything does. It is a maintainer's tool and reads the network; the tests do not.

    python tools/check_catalog.py            # needs PyYAML
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import ikea_repo  # noqa: E402

USER_AGENT = "Mozilla/5.0 (compatible; PartCAD-ikea-check/%d; +https://github.com/partcad)" % ikea_repo.CACHE_VERSION
LOCALE = "nl/en"


def fetch(url, data=None):
    body = json.dumps(data).encode() if data is not None else None
    headers = {"User-Agent": USER_AGENT}
    if body is not None:
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def ikea(product):
    """What is wrong with what the catalog says IKEA sells, or None."""
    facts = product["ikea"]
    number = str(facts["article"]).lower()
    url = "https://sik.search.blue.cdtapps.com/%s/search-result-page?%s" % (
        LOCALE,
        urllib.parse.urlencode({"q": number.lstrip("s"), "size": 24, "types": "PRODUCT"}),
    )
    items = fetch(url).get("searchResultPage", {}).get("products", {}).get("main", {}).get("items", [])
    for item in items:
        found = item.get("product") or {}
        page = (found.get("pipUrl") or "").rstrip("/").lower()
        if page.endswith(number) or str(found.get("itemNoGlobal")) == number:
            if (found.get("name") or "").upper() != facts["name"].upper():
                return "IKEA calls %s '%s', not '%s'" % (number, found.get("name"), facts["name"])
            return None
    return "IKEA's search does not find article %s" % ikea_repo.article(facts["article"])


def printables(model):
    query = "query M($id: ID!) { print(id: $id) { id stls { id } otherFiles { id } } }"
    answer = fetch("https://api.printables.com/graphql/", {"query": query, "variables": {"id": model["model"]}})
    found = (answer.get("data") or {}).get("print")
    if not found:
        return "Printables has no model %s" % model["model"]
    files = {str(f["id"]) for f in found.get("stls") or []}
    files |= {"other-%s" % f["id"] for f in found.get("otherFiles") or []}
    if model.get("file") and str(model["file"]) not in files:
        return "Printables model %s has no file %s" % (model["model"], model["file"])
    return None


def makerworld(model):
    found = fetch("https://makerworld.com/api/v1/design-service/design/%s" % model["model"])
    if not found.get("id"):
        return "MakerWorld has no model %s" % model["model"]
    profiles = {"preview-%s" % instance.get("id") for instance in found.get("instances") or []}
    if str(model.get("file", "")).startswith("preview-") and model["file"] not in profiles:
        return "MakerWorld model %s has no print profile %s" % (model["model"], model["file"][len("preview-") :])
    return None


def grabcad(model):
    api = "https://grabcad.com/community/api/v1/models/%s" % urllib.parse.quote(model["model"])
    if not fetch(api + "?extended=true").get("cached_slug"):
        return "GrabCAD has no model %s" % model["model"]
    names, queue, seen = set(), [None], set()
    while queue:
        folder = queue.pop(0)
        seen.add(folder)
        url = api + "/files" + ("?" + urllib.parse.urlencode({"folder_id": folder}) if folder is not None else "")
        answer = fetch(url)
        names |= {entry.get("name") for entry in answer.get("files") or []}
        queue += [sub.get("id") for sub in answer.get("folders") or [] if sub.get("id") not in seen]
    if model.get("file") and model["file"] not in names:
        return "GrabCAD model %s has no file %s" % (model["model"], model["file"])
    return None


CHECKS = {"printables": printables, "makerworld": makerworld, "grabcad": grabcad}


def main():
    catalog = ikea_repo.load()
    problems = 0
    for name, product in catalog["products"].items():
        said = []
        try:
            wrong = ikea(product)
        except (urllib.error.URLError, OSError, ValueError) as e:
            wrong = "IKEA could not be asked: %s" % e
        if wrong:
            said.append(wrong)
        found = 0
        for model in product["models"]:
            check = CHECKS.get(model["site"])
            if check is None:
                said.append(
                    "%s %s: not checked (%s takes credentials)" % (model["site"], model["model"], model["site"])
                )
                continue
            try:
                wrong = check(model)
            except (urllib.error.URLError, OSError, ValueError) as e:
                wrong = "%s could not be asked about %s: %s" % (model["site"], model["model"], e)
            if wrong:
                said.append(wrong)
            else:
                found += 1
        if not found:
            said.append("no model of it was found: it no longer belongs in the catalog")
        problems += sum(1 for line in said if "not checked" not in line)
        print("%s %s" % ("ok  " if not said else "!!  ", name))
        for line in said:
            print("      " + line)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
