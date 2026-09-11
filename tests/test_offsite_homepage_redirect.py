"""A redirect off the origin means two different things, and this tells them apart.

The seed URL is the address the audit was pointed at. When that address answers
with a redirect to another hostname, the site is saying where it lives, and an
apex domain pointing at a country or regional domain is the ordinary
arrangement for a brand that sells in more than one market. Refusing to follow
it produced a crawl of one page, no content read at all, and a headline verdict
of "no blocking or significant problems were found" about a site the audit had
never seen.

A redirect off the origin found on any other page is somebody else's content
and stays refused, for the reason it always was: one site's `/go/` route filed
seventeen of another organisation's pages as this site's own.
"""

from __future__ import annotations

import json
import os

import pytest

import crawl as crawl_module
from crawl import (
    _settle_origin, fetch_page, registrable_name,
)
from fixture_server import FixtureServer


# --------------------------------------------------------------------------
# The name behind a hostname
# --------------------------------------------------------------------------

def test_the_registered_name_is_read_out_of_a_hostname():
    """`example-shop` is the name; `www`, the suffix and a registry grouping
    label under it are addressing."""
    assert registrable_name("example-shop.test") == "example-shop"
    assert registrable_name("www.example-shop.test") == "example-shop"
    assert registrable_name("shop.example-shop.test") == "example-shop"
    assert registrable_name("EXAMPLE-SHOP.TEST") == "example-shop"
    # A grouping label the registry sells under, not a name anybody owns.
    assert registrable_name("example-shop.co.uk") == "example-shop"
    assert registrable_name("example-shop.or.jp") == "example-shop"


def test_an_address_is_not_a_name():
    """Two hosts on one subnet share every label shape there is and belong to
    nobody in common, so an IP literal is only ever equal to itself."""
    assert registrable_name("192.0.2.1") != registrable_name("192.0.2.9")
    assert registrable_name("192.0.2.1") == registrable_name("192.0.2.1")
    assert registrable_name("[::1]") == registrable_name("[::1]")


def test_a_name_under_somebody_else_s_name_is_not_the_same_name():
    """A storefront hosted under a platform's own domain is the platform's
    address, not the brand's. Following one would audit the platform."""
    assert registrable_name("example-shop.marketplace.test") == "marketplace"
    assert registrable_name("example-shop.test") != registrable_name(
        "example-shop.marketplace.test")


# --------------------------------------------------------------------------
# What the seed URL's redirect is allowed to do
# --------------------------------------------------------------------------

class _LandsOn:
    """A fetcher whose every request ends up at one fixed URL."""

    def __init__(self, url, status_code=200):
        self._response = type("_Response", (), {"url": url,
                                                "status_code": status_code})()

    def try_get(self, url, method=None, **kwargs):
        return self._response


def test_the_seed_is_followed_to_the_same_name_at_another_address():
    """An apex answering 301 to a regional domain is the site saying where it
    lives. Refusing it read nothing and reported a clean bill of health."""
    notes = []
    origin, seed, redirect = _settle_origin(
        _LandsOn("https://www.example-shop.in/in"),
        "https://example-shop.test", "https://example-shop.test/", notes)

    assert origin == "https://www.example-shop.in"
    assert seed == "https://www.example-shop.in/in"
    assert redirect["followed"] is True
    assert redirect["same_registered_name"] is True
    assert redirect["requested_origin"] == "https://example-shop.test"
    assert redirect["final_origin"] == "https://www.example-shop.in"
    # The sentence has to name both hosts, or a reader cannot tell which one
    # the rest of the report measured.
    assert "example-shop.test" in redirect["reason"]
    assert "www.example-shop.in" in redirect["reason"]


def test_the_seed_is_not_followed_to_a_different_name():
    """The refusal the off-origin rule exists for: one site's redirect route
    filed another organisation's pages as this site's own and then took the
    brand name from one of them."""
    notes = []
    origin, seed, redirect = _settle_origin(
        _LandsOn("https://unrelated-name.test/"),
        "https://example-shop.test", "https://example-shop.test/", notes)

    assert origin == "https://example-shop.test"
    assert seed == "https://example-shop.test/"
    assert redirect["followed"] is False
    assert redirect["same_registered_name"] is False
    assert "unrelated-name.test" in redirect["reason"]
    # `_settle_origin` writes no note for a redirect off the origin: the caller
    # records it, because the caller is the one that can also put it in the
    # snapshot. A note without the snapshot key is a sentence nothing can act
    # on.
    assert notes == []


def test_the_seed_is_not_followed_into_a_sign_in_page():
    """The audit reads nothing under `/login` or `/account`, and a redirect is
    how it reached one before. The destination being the right company's does
    not make a customer portal a page to grade a brand on."""
    notes = []
    origin, _seed, redirect = _settle_origin(
        _LandsOn("https://account.example-shop.in/login"),
        "https://example-shop.test", "https://example-shop.test/", notes)

    assert origin == "https://example-shop.test"
    assert redirect["followed"] is False
    assert redirect["same_registered_name"] is True
    assert "sign-in" in redirect["reason"]


def test_moving_between_a_bare_domain_and_www_is_not_reported_as_a_move():
    """One site with two names. It is adopted, as it always was, and it is not
    an off-origin redirect: reporting every such site as "we audited a
    different hostname" would make the sentence meaningless where it matters."""
    notes = []
    origin, seed, redirect = _settle_origin(
        _LandsOn("https://www.example-shop.test/"),
        "https://example-shop.test", "https://example-shop.test/", notes)

    assert origin == "https://www.example-shop.test"
    assert seed == "https://www.example-shop.test/"
    assert redirect is None
    assert notes and "redirects to" in notes[0]


def test_a_seed_that_stays_put_settles_nothing():
    notes = []
    origin, seed, redirect = _settle_origin(
        _LandsOn("https://example-shop.test/"),
        "https://example-shop.test", "https://example-shop.test/", notes)
    assert (origin, seed, redirect) == (
        "https://example-shop.test", "https://example-shop.test/", None)
    assert notes == []


def test_a_host_that_refuses_head_is_asked_again_with_get():
    """A refused HEAD says nothing about where a GET lands. A retailer
    measured for this answers 403 to every HEAD and 200 to every GET, and
    taking the HEAD at its word would report the seed as staying put - which
    is the whole defect, back again on any host that dislikes HEAD."""
    calls = []

    class _RefusesHead:
        def try_get(self, url, method=None, **kwargs):
            calls.append(method or "GET")
            if method == "HEAD":
                return type("_R", (), {"url": url, "status_code": 403})()
            return type("_R", (), {"url": "https://www.example-shop.in/in",
                                   "status_code": 200})()

    notes = []
    origin, _seed, redirect = _settle_origin(
        _RefusesHead(), "https://example-shop.test",
        "https://example-shop.test/", notes)

    assert calls == ["HEAD", "GET"]
    assert origin == "https://www.example-shop.in"
    assert redirect["followed"] is True


def test_a_host_that_answers_head_is_not_asked_twice():
    """The second request is for hosts that refused the first one. Spending it
    on every crawl would be a request per site for nothing."""
    calls = []

    class _AnswersHead:
        def try_get(self, url, method=None, **kwargs):
            calls.append(method or "GET")
            return type("_R", (), {"url": url, "status_code": 200})()

    _settle_origin(_AnswersHead(), "https://example-shop.test",
                   "https://example-shop.test/", [])
    assert calls == ["HEAD"]


def test_a_host_that_does_not_answer_settles_nothing():
    class _Silent:
        def try_get(self, url, method=None, **kwargs):
            return None

    notes = []
    assert _settle_origin(_Silent(), "https://example-shop.test",
                          "https://example-shop.test/", notes) == (
        "https://example-shop.test", "https://example-shop.test/", None)
    assert notes == []


# --------------------------------------------------------------------------
# A redirect met later in the crawl is still somebody else's content
# --------------------------------------------------------------------------

class _Response:
    history = ()
    headers = {"content-type": "text/html; charset=utf-8"}
    content = b"<html><body><h1>Another organisation</h1></body></html>"
    status_code = 200
    elapsed_ms = 1

    def __init__(self, url):
        self.url = url


class _Redirecting:
    def __init__(self, url):
        self._url = url

    def get(self, url, **kwargs):
        return _Response(self._url)


def test_a_page_deep_in_the_crawl_that_redirects_off_site_is_still_skipped():
    """The rule this file relaxes for the seed URL is unchanged everywhere
    else. A `/go/<id>` route answering into another organisation's document
    tracker filed seventeen of their pages as this site's, spent 28% of the
    crawl budget on them, and took the brand name from one of them."""
    record = fetch_page(
        _Redirecting("https://unrelated-name.test/doc/1"),
        "https://example-shop.test/go/17", 2, "bfs", "https://example-shop.test")

    assert record["skipped"] == "redirects off this origin"
    assert record["offsite_redirect"] is True
    assert record["text"] == ""


def test_even_the_same_registered_name_is_skipped_deep_in_the_crawl():
    """The seed URL is followed because the person asking for the audit
    vouched for that address. A `Location` header on page forty vouches for
    nothing, and a name match is not a reason to start crawling a second
    domain's pages into this domain's sample."""
    record = fetch_page(
        _Redirecting("https://www.example-shop.in/in/product/1"),
        "https://example-shop.test/product/1", 2, "bfs",
        "https://example-shop.test")

    assert record["skipped"] == "redirects off this origin"


# --------------------------------------------------------------------------
# What the crawl does with the answer
# --------------------------------------------------------------------------

PAGE = ("<html lang=\"en\"><head><title>{title}</title></head><body>"
        "<h1>{title}</h1><p>{body}</p>{links}</body></html>")


def _write_site(root, pages, rules=None):
    os.makedirs(root, exist_ok=True)
    for name, html in pages.items():
        path = os.path.join(root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(html)
    if rules is not None:
        with open(os.path.join(root, "_rules.json"), "w", encoding="utf-8") as handle:
            json.dump(rules, handle)
    return root


def _crawl(base_url, out_path):
    return crawl_module.crawl(base_url, str(out_path), max_pages=6, budget_s=30,
                              delay=0.0, render=False)


@pytest.fixture()
def path_redirect_site(tmp_path):
    """A site whose root redirects to a path on the same host."""
    root = _write_site(
        str(tmp_path / "path-redirect"),
        {
            "en/index.html": PAGE.format(
                title="Example Shop", body="Handmade lamps, shipped from stock.",
                links='<a href="/en/about.html">About</a>'),
            "en/about.html": PAGE.format(
                title="About Example Shop", body="Founded to make one good lamp.",
                links='<a href="/en/">Home</a>'),
        },
        rules={"redirects": {"/": "/en/"}},
    )
    with FixtureServer(root) as server:
        yield server


def test_a_root_that_redirects_to_a_path_on_the_same_host_is_audited(
        path_redirect_site, tmp_path):
    """`/` to `/en/` is one host talking about itself, so nothing is re-based
    and the destination is read as the homepage. Common on multilingual and
    statically hosted sites, and the pages behind it are the site."""
    snapshot = _crawl(path_redirect_site.base_url, tmp_path / "snapshot.json")

    assert snapshot["origin_redirect"] is None
    assert snapshot["origin"] == path_redirect_site.base_url
    assert snapshot["crawl"]["pages_read"] >= 2
    home = [p for p in snapshot["pages"] if p.get("page_type") == "home"]
    assert home and home[0]["final_url"].endswith("/en/")
    assert not any(p.get("offsite_redirect") for p in snapshot["pages"])


@pytest.fixture()
def two_sites(tmp_path):
    """The address that was asked about, and the address it points at."""
    asked = _write_site(str(tmp_path / "asked"), {
        "index.html": PAGE.format(title="Moved", body="", links=""),
        "robots.txt": "User-agent: *\nDisallow: /nothing\n",
    })
    answers = _write_site(str(tmp_path / "answers"), {
        "index.html": PAGE.format(
            title="Example Shop", body="Handmade lamps, shipped from stock.",
            links='<a href="/about.html">About</a>'),
        "about.html": PAGE.format(
            title="About Example Shop", body="Founded to make one good lamp.",
            links='<a href="/">Home</a>'),
        "robots.txt": "User-agent: *\nDisallow: /basket\nSitemap: /sitemap.xml\n",
    })
    with FixtureServer(asked) as asked_server, FixtureServer(answers) as answers_server:
        yield asked_server, answers_server


def test_everything_derived_from_the_origin_moves_with_it(two_sites, tmp_path,
                                                          monkeypatch):
    """Re-basing that leaves anything behind is worse than not re-basing.

    robots.txt read from the host that no longer serves the site is a set of
    rules from nowhere, and a brand name taken from the old hostname names a
    domain that redirects away. Both come off one `origin` variable, and this
    is the test that says so.
    """
    asked, answers = two_sites
    settled = crawl_module.origin_of(answers.base_url)
    record = {
        "requested_origin": crawl_module.origin_of(asked.base_url),
        "requested_url": asked.base_url + "/",
        "final_url": answers.base_url + "/",
        "final_origin": settled,
        "status": 301,
        "same_registered_name": True,
        "followed": True,
        "reason": "the address asked about redirects to the address audited",
    }
    monkeypatch.setattr(crawl_module, "_settle_origin",
                        lambda *a, **k: (settled, answers.base_url + "/", record))

    snapshot = _crawl(asked.base_url, tmp_path / "snapshot.json")

    assert snapshot["origin"] == settled
    assert snapshot["site"] == crawl_module.site_label(settled)
    assert snapshot["requested_origin"] == crawl_module.origin_of(asked.base_url)
    assert snapshot["origin_redirect"]["followed"] is True
    assert record["reason"] in snapshot["crawl"]["notes"]
    # robots.txt from the host that answers, not from the one that redirected.
    assert snapshot["robots"]["url"].startswith(settled)
    assert "/basket" in snapshot["robots"]["raw"]
    # And every page, including the `/llms.txt` probe addresses.
    assert snapshot["pages"], "no page was read from the host that answers"
    assert all(page["url"].startswith(settled) for page in snapshot["pages"])
    assert snapshot["llms_txt"]["url"].startswith(settled)
    assert snapshot["crawl"]["pages_read"] >= 2


@pytest.fixture()
def redirects_to_another_name(tmp_path):
    """A root that redirects to a hostname carrying a different name.

    Two names for one loopback interface is the only pair of genuinely
    different hostnames a test can reach without a network, and the rule under
    test is about names rather than about who answers them.
    """
    root = _write_site(str(tmp_path / "another-name"), {
        "en/index.html": PAGE.format(title="Somewhere else", body="Not this site.",
                                     links=""),
    })
    with FixtureServer(root) as server:
        port = server.base_url.rsplit(":", 1)[1]
        elsewhere = "http://localhost:{}/en/".format(port)
        try:
            import requests
            requests.get(elsewhere, timeout=5)
        except Exception:
            pytest.skip("this machine does not resolve the second loopback name")
        # `rules` is read per request, so the destination can name the port
        # the server was given after it started.
        server.rules["redirects"] = {"/": elsewhere}
        yield server, elsewhere


def test_a_seed_pointing_at_a_different_name_is_recorded_not_audited(
        redirects_to_another_name, tmp_path):
    """The refusal, end to end. Nothing of the requested site was read, and
    the snapshot has to say that in a form a report can act on - an empty page
    list on its own was read as "nothing wrong here"."""
    server, elsewhere = redirects_to_another_name
    snapshot = _crawl(server.base_url, tmp_path / "snapshot.json")

    assert snapshot["origin"] == crawl_module.origin_of(server.base_url)
    redirect = snapshot["origin_redirect"]
    assert redirect is not None and redirect["followed"] is False
    assert redirect["same_registered_name"] is False
    assert redirect["final_url"].startswith(elsewhere)
    assert snapshot["crawl"]["pages_read"] == 0
    assert redirect["reason"] in snapshot["crawl"]["notes"]


def test_a_seed_redirect_the_probe_missed_is_still_recorded(
        redirects_to_another_name, tmp_path, monkeypatch):
    """The probe is one request and a host can answer it differently from the
    GET that follows. The homepage GET is the ground truth, so a redirect that
    only shows up there is recorded from there rather than going unmentioned."""
    server, elsewhere = redirects_to_another_name
    monkeypatch.setattr(crawl_module, "_settle_origin",
                        lambda f, origin, seed, notes: (origin, seed, None))

    snapshot = _crawl(server.base_url, tmp_path / "snapshot.json")

    redirect = snapshot["origin_redirect"]
    assert redirect is not None and redirect["followed"] is False
    assert redirect["final_url"].startswith(elsewhere)
    assert snapshot["crawl"]["pages_read"] == 0
    assert any("was read" in note for note in snapshot["crawl"]["notes"])


@pytest.fixture()
def unreadable_home(tmp_path):
    """A root that answers 200 with something that is not a page."""
    root = _write_site(
        str(tmp_path / "unreadable"),
        {"index.html": PAGE.format(title="Example Shop", body="x", links="")},
        rules={"headers": {"/": {"Content-Type": "application/pdf"}}},
    )
    with FixtureServer(root) as server:
        yield server


def test_a_200_that_carried_no_page_is_not_counted_as_a_page_read(
        unreadable_home, tmp_path):
    """`pages_ok` counts the status line. A record carrying `skipped` has no
    text, no links and no headings, and a crawl of nothing but those scored
    `pages_ok` of 1 - which is how a report reached "no blocking or significant
    problems were found" having read no content at all. `pages_read` is the
    count a verdict can rest on."""
    snapshot = _crawl(unreadable_home.base_url, tmp_path / "snapshot.json")

    assert snapshot["crawl"]["pages_ok"] >= 1
    assert snapshot["crawl"]["pages_read"] == 0
