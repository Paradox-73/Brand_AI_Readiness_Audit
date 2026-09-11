"""What a bounded crawl comes back with, and what it says when it comes back empty.

Two failures found on sites nobody here had seen, both about
the sample rather than about any one check.

  the sample covers the deepest pages only
      A shop's homepage links to its shelves, its shelves link to their items,
      and both of those crowded out the handful of pages that say who the shop
      is. Forty-one pages were read - shelves, items and the homepage - and
      none of the nine `about`, `contact` and policy addresses linked from the
      homepage. The report then stated that the site never says what the brand
      is and never says when it was founded, with both sentences sitting on a
      page the crawler had seen linked and chosen not to fetch.

  the sample is empty and the snapshot does not say why
      A hosted store whose owner had switched it off answered the same refusal
      at every address. The status was recorded and nothing read it, so the
      whole report was one finding about a line in robots.txt - a change that
      would have altered nothing, because there was no site behind it.

Both are properties of the crawl, so they are tested here against a server
rather than against a hand-built snapshot: a queue that spreads correctly in a
unit test and starves in a real crawl would pass the first and fail the site.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from conftest import SCRIPTS, run_script
from fixture_server import FixtureServer

sys.path.insert(0, SCRIPTS)

from crawl import KindSpreadQueue, crawl_kind  # noqa: E402

CRAWL = os.path.join(SCRIPTS, "crawl.py")


# --------------------------------------------------------------------------
# A site shaped like a shop
# --------------------------------------------------------------------------

# Enough shelves that they cannot all be read inside the sample below, which is
# the whole point: a sample that fits the site proves nothing about a sample
# that does not.
SHELVES = ["walking", "running", "sandals", "boots", "trainers", "slippers",
           "school", "formal", "sports", "casual", "kids", "womens", "mens",
           "sale", "new-arrivals", "clearance", "bestsellers", "outdoor",
           "indoor", "travel", "beach", "winter", "summer", "autumn", "spring",
           "leather", "canvas", "waterproof", "lightweight", "cushioned"]

# The pages that carry a brand's identity. Their addresses sort after the
# shelves', which is how they came to be starved: the crawl expands a page's
# links in sorted order, so on a first-in-first-out queue every shelf is ahead
# of every one of these.
IDENTITY_PAGES = ["about-us", "contact", "terms-of-service", "privacy-policy",
                  "shipping-policy", "refund-policy", "faq", "careers", "press"]

ITEMS_PER_SHELF = 2


def _html(title, body, links=()):
    anchors = "".join(
        '<li><a href="{{{{BASE}}}}{}">{}</a></li>'.format(href, text)
        for href, text in links)
    return (
        '<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
        '<title>{title}</title><meta name="description" content="{body}">'
        '</head><body><main><h1>{title}</h1><p>{body}</p>'
        '<ul>{anchors}</ul></main></body></html>'
    ).format(title=title, body=body, anchors=anchors)


def _write(root, relative, text):
    path = os.path.join(root, relative.strip("/").replace("/", os.sep))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)


def _build_shop(root):
    """A shop with far more shelves and items than one sample can hold."""
    home_links = (
        [("/pages/{}/".format(name), name.replace("-", " ").title())
         for name in IDENTITY_PAGES]
        + [("/collections/{}/".format(name), name.title()) for name in SHELVES])
    _write(root, "index.html", _html(
        "Larkspur Footwear", "Larkspur Footwear makes walking shoes.", home_links))

    for name in IDENTITY_PAGES:
        _write(root, "pages/{}/index.html".format(name), _html(
            name.replace("-", " ").title(),
            "Incorporated in 2006, Larkspur Footwear is one of the leading makers "
            "of walking shoes in the north of England.",
            [("/", "Home")]))

    for shelf in SHELVES:
        items = [("/products/{}-{}/".format(shelf, n), "{} shoe {}".format(shelf, n))
                 for n in range(1, ITEMS_PER_SHELF + 1)]
        _write(root, "collections/{}/index.html".format(shelf), _html(
            shelf.title(), "Shoes filed under {}.".format(shelf), items))
        for href, name in items:
            _write(root, href.strip("/") + "/index.html", _html(
                name.title(),
                "{}. 49.00. In stock. Add to cart. Free shipping over 75.".format(name),
                [("/", "Home")]))

    _write(root, "robots.txt", "User-agent: *\nAllow: /\n")
    return root


def _sections(snapshot):
    """How many crawled URLs sit under each top-level directory."""
    counts = {}
    for page in snapshot["pages"]:
        parts = [p for p in (urlparse(page["url"]).path or "/").split("/") if p]
        key = "/" if not parts else "/" + parts[0] + "/"
        counts[key] = counts.get(key, 0) + 1
    return counts


def test_a_shop_sample_holds_the_pages_that_state_the_facts(tmp_path):
    """Shelves, items and the pages that say who the shop is - all three.

    The sample is deliberately far smaller than the site, because that is the
    only condition under which the queue's ordering rule decides anything.
    """
    site = _build_shop(str(tmp_path / "site"))
    out = str(tmp_path / "snapshot.json")
    with FixtureServer(site) as server:
        run_script([CRAWL, server.base_url, "--out", out, "--budget", "120",
                    "--delay", "0", "--max-pages", "24", "--no-render"],
                   "crawl.py [shop]")
    with open(out, encoding="utf-8") as handle:
        snapshot = json.load(handle)

    sections = _sections(snapshot)
    assert sections.get("/pages/"), (
        "no page under /pages/ was read. Those are the addresses that state what "
        "the brand is and when it was founded, and the report says the site never "
        "states either when they are missing. Sample: {}".format(sections))
    assert sections.get("/collections/"), (
        "no listing page was read: {}".format(sections))
    assert sections.get("/products/"), (
        "no item page was read - the failure the two-queue version existed to "
        "prevent, and it must not come back: {}".format(sections))

    types = {p.get("page_type") for p in snapshot["pages"]}
    assert {"about", "contact"} <= types, (
        "the crawl reached neither an about page nor a contact page, which are "
        "the two the identity and contact findings are written from. Types read: "
        "{}".format(sorted(t for t in types if t)))


# --------------------------------------------------------------------------
# The rule underneath it
# --------------------------------------------------------------------------

def test_a_shelf_and_the_thing_on_it_are_different_kinds():
    """The classifier alone cannot separate them, and the sample must.

    Run over an unfetched address the classifier reads an item under a listing
    segment as the listing type, because it has no page in front of it to find
    a price on. If that were the whole answer, shelves and items would share
    one turn and thirty shelves would take it.
    """
    shelf = crawl_kind("http://shop.test/collections/boots/")
    item = crawl_kind("http://shop.test/products/boots-1/")
    assert shelf != item, (
        "a listing and one of the things it lists were filed as the same kind of "
        "page ({!r}), so they would share a turn".format(shelf))


def test_an_identity_page_is_its_own_kind():
    """`about` and `contact` are neither listings nor items, which is exactly
    why a queue split into those two had nowhere to put them."""
    kinds = {crawl_kind("http://shop.test/pages/about-us/"),
             crawl_kind("http://shop.test/pages/contact/")}
    assert len(kinds) == 2, "about and contact should not share one turn: {}".format(kinds)
    assert not any(kind.endswith("/item") for kind in kinds)


def test_the_queue_serves_a_kind_that_arrived_last():
    """One address of a rare kind beats the thirtieth of a common one.

    First-in-first-out is what starved the identity pages: they are linked from
    the same page as the shelves and sort after them, so on one queue they are
    always behind thirty other URLs.
    """
    queue = KindSpreadQueue()
    queue.append(("http://shop.test/", 0, "homepage"))
    for shelf in SHELVES:
        queue.append(("http://shop.test/collections/{}/".format(shelf), 1, "bfs"))
    queue.append(("http://shop.test/pages/about-us/", 1, "bfs"))

    first_four = [queue.popleft()[0] for _ in range(4)]
    assert "http://shop.test/pages/about-us/" in first_four, (
        "the one address of its kind arrived last and was not reached in four "
        "turns: {}".format(first_four))


def test_a_kind_that_runs_out_stops_holding_a_place():
    """A rare kind gets its turn and then gets out of the way.

    A reservation that outlives the URLs it was for is the other half of the
    same bug: half a sample held for detail pages on a site with three of them
    is half a sample spent on nothing.
    """
    queue = KindSpreadQueue()
    queue.append(("http://shop.test/pages/about-us/", 1, "bfs"))
    for shelf in SHELVES:
        queue.append(("http://shop.test/collections/{}/".format(shelf), 1, "bfs"))

    served = []
    while queue:
        served.append(queue.popleft()[0])
    assert len(served) == len(SHELVES) + 1, (
        "the queue lost or repeated URLs: {} served, {} appended".format(
            len(served), len(SHELVES) + 1))
    assert len(set(served)) == len(served), "a URL was served twice"


def test_the_queue_order_is_the_same_every_run():
    """Determinism is what makes two audits of one site comparable."""
    def order():
        queue = KindSpreadQueue()
        for shelf in SHELVES:
            queue.append(("http://shop.test/collections/{}/".format(shelf), 1, "bfs"))
            queue.append(("http://shop.test/products/{}-1/".format(shelf), 2, "bfs"))
        queue.append(("http://shop.test/pages/contact/", 1, "bfs"))
        return [queue.popleft()[0] for _ in range(10)]

    assert order() == order()


# --------------------------------------------------------------------------
# A site that is not serving
# --------------------------------------------------------------------------

class SwitchedOffServer:
    """Answers one status at every address, and ordinary rules at robots.txt.

    That combination is the whole trap. A hosted store whose owner has stopped
    paying still serves robots.txt, and it serves it with `Disallow: /` - so
    the one file that answers normally is also the one that produces a finding,
    and "every address answered the same way" is false unless robots.txt is
    left out of the question.
    """

    def __init__(self, status, robots="User-agent: *\nDisallow: /\n"):
        self.status = status
        self.robots = robots
        self.base_url = ""
        self.paths = []

    def __enter__(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):
                pass

            def do_HEAD(self):
                self._serve(head_only=True)

            def do_GET(self):
                self._serve(head_only=False)

            def _serve(self, head_only):
                path = self.path.split("?", 1)[0]
                server.paths.append(path)
                if path == "/robots.txt":
                    body = server.robots.encode("utf-8")
                    content_type = "text/plain; charset=utf-8"
                    status = 200
                else:
                    body = (b"<html><head><title>Store unavailable</title>"
                            b"</head><body></body></html>")
                    content_type = "text/html; charset=utf-8"
                    status = server.status
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if not head_only:
                    self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base_url = "http://127.0.0.1:{}".format(self.httpd.server_address[1])
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)
        return False


def _crawl_switched_off(tmp_path, status):
    out = str(tmp_path / "snapshot.json")
    with SwitchedOffServer(status) as server:
        run_script([CRAWL, server.base_url, "--out", out, "--budget", "60",
                    "--delay", "0", "--no-render"], "crawl.py [switched off]")
    with open(out, encoding="utf-8") as handle:
        return json.load(handle)


def test_a_site_that_withholds_itself_everywhere_is_recorded_as_such(tmp_path):
    """The largest true fact about the site, in the snapshot, under one key."""
    snapshot = _crawl_switched_off(tmp_path, 402)

    record = snapshot.get("site_not_serving")
    assert record, (
        "every address this crawl reached answered the same withholding status "
        "and the snapshot recorded nothing. Sitemap statuses: {}".format(
            [s.get("status") for s in snapshot.get("sitemaps") or []]))
    assert record["status"] == 402, record
    assert record["meaning"], "the status was recorded with no plain-words meaning"
    assert record["addresses"], "no address was named as evidence"
    assert record["addresses_answered"] >= 1
    assert "402" in record["reason"]
    assert record["reason"] in (snapshot["crawl"]["notes"] or []), (
        "the sentence has to reach the crawl notes as well, so a reader of the "
        "appendix sees it even if nothing leads with it")
    assert snapshot["crawl"]["pages_crawled"] == 0, (
        "this site disallows the auditor at /, so nothing should have been fetched")


def test_a_persistent_unavailable_is_the_same_fact(tmp_path):
    """503 everywhere is the site saying it is not serving, not a dead page.

    The rule is uniformity plus meaning, not a hand-written list of one
    platform's behaviour, so a second status in the same family has to work
    without anything else being added.
    """
    record = _crawl_switched_off(tmp_path, 503).get("site_not_serving")
    assert record and record["status"] == 503, record


def test_a_refusal_of_this_crawler_is_not_the_site_being_switched_off(tmp_path):
    """403 everywhere is about who is asking, and another check owns that.

    Reading it as "the site is not serving" would assert an outage on every
    bot-managed site that serves people perfectly well.
    """
    assert _crawl_switched_off(tmp_path, 403).get("site_not_serving") is None


def test_one_page_that_is_gone_is_not_the_whole_site(tmp_path):
    """410 at one address among pages that serve is a deleted page.

    Same status, opposite conclusion, and the only thing separating them is
    whether anything else on the site answered differently.
    """
    site = str(tmp_path / "site")
    _write(site, "index.html", _html(
        "Larkspur Footwear", "Larkspur Footwear makes walking shoes.",
        [("/about.html", "About"), ("/retired.html", "Retired")]))
    _write(site, "about.html", _html(
        "About", "Larkspur Footwear was founded in 2006 in the north of England."))
    _write(site, "retired.html", _html("Retired", "This page has been retired."))
    _write(site, "robots.txt", "User-agent: *\nAllow: /\n")
    _write(site, "_rules.json", json.dumps({"status": {"/retired.html": 410}}))

    out = str(tmp_path / "snapshot.json")
    with FixtureServer(site) as server:
        run_script([CRAWL, server.base_url, "--out", out, "--budget", "60",
                    "--delay", "0", "--no-render"], "crawl.py [one gone page]")
    with open(out, encoding="utf-8") as handle:
        snapshot = json.load(handle)

    assert 410 in [p.get("status") for p in snapshot["pages"]], (
        "the fixture did not produce the 410 this test is about")
    assert snapshot.get("site_not_serving") is None, (
        "one retired page was read as the whole site declining to serve")


def test_a_site_that_answers_normally_records_nothing(tmp_path):
    """The key is null on every ordinary site, so its presence means something."""
    site = str(tmp_path / "site")
    _write(site, "index.html", _html(
        "Larkspur Footwear", "Larkspur Footwear makes walking shoes.",
        [("/about.html", "About")]))
    _write(site, "about.html", _html(
        "About", "Larkspur Footwear was founded in 2006 in the north of England."))
    _write(site, "robots.txt", "User-agent: *\nAllow: /\n")

    out = str(tmp_path / "snapshot.json")
    with FixtureServer(site) as server:
        run_script([CRAWL, server.base_url, "--out", out, "--budget", "60",
                    "--delay", "0", "--no-render"], "crawl.py [healthy]")
    with open(out, encoding="utf-8") as handle:
        snapshot = json.load(handle)

    assert snapshot.get("site_not_serving") is None
    assert snapshot["crawl"]["pages_read"] >= 1
