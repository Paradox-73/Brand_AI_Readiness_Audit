# -*- coding: utf-8 -*-
"""Five defects an independent review ranked, and the measurements behind them.

Each section names the site shape that produced the wrong finding and asserts
the behaviour that replaces it.

  1  A town library's two iCalendar export endpoints answered 200 with an
     empty body and `X-Robots-Tag: noindex, nofollow`, were counted as pages,
     and produced five findings each. Six of the ten false positives the review
     counted across six sites came from those two URLs.
  2  The image record could not support a proportion: the site-wide check
     counted distinct URLs out of a twenty-entry sample, so a page with 232
     undescribed images was reported as having four.
  3  The breadcrumb detector was the English word "breadcrumb". A Japanese tea
     grower's theme calls the element `pankuzu` and marks the trail up in
     RDFa; all 15 of its deep pages were reported as having no trail.
  4  A Polish agency's one-page site was reported as having a two-item
     navigation, because every fragment anchor was discarded and the two
     language toggles were all that was left.
  5  A robots.txt asking 45 seconds between requests had no check at all, and
     a 429 had no `Retry-After` handling and no backoff.
"""

from __future__ import annotations

import os
import sys

from conftest import SCRIPTS

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, SCRIPTS)

import crawl as crawl_module  # noqa: E402
import page_extract  # noqa: E402
from audit_common import make_soup, pages_of, SkillResult  # noqa: E402
from page_extract import _breadcrumb, _images, _links, extract_page  # noqa: E402

import importlib.util  # noqa: E402


def _skill_module(skill, name):
    scripts = os.path.join(os.path.dirname(SCRIPTS), "..", skill, "scripts")
    scripts = os.path.abspath(scripts)
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        name, os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ACCESS = _skill_module("crawl-access-audit", "access_for_hidden_page_tests")

SITE = "https://town-library.test"


def _extract(path, html, headers=None, status=200):
    url = SITE + path
    return extract_page(
        url=url, final_url=url, status=status,
        headers=dict({"content-type": "text/html"}, **(headers or {})),
        html=html, redirect_chain=[], elapsed_ms=12, depth=1, source="bfs",
        origin=SITE)


# --------------------------------------------------------------------------
# 1 - a page the site told every crawler to ignore
# --------------------------------------------------------------------------

# The two URLs, as the crawl recorded them: 200, `text/html`, no body at all,
# and the header saying discard this.
ICAL_HEADERS = {"content-type": "text/html", "x-robots-tag": "noindex, nofollow"}


def test_an_ical_export_that_answered_text_html_is_not_a_page():
    record = _extract("/events/category/childrens/list/?ical=1", "",
                      headers=ICAL_HEADERS)
    assert record["not_gradable"] is True
    assert record["not_gradable_reason"] == "empty-body"
    # The record stays in the snapshot with everything the crawl read on it.
    assert record["status"] == 200
    assert record["x_robots_tag"] == "noindex, nofollow"


def test_a_noindexed_stub_with_nothing_of_its_own_is_not_a_page_either():
    """The sibling shape: the export route answers with the theme's chrome and
    nothing else, so it is not empty and is still not a page."""
    html = ('<html><head><meta name="robots" content="noindex, nofollow"></head>'
            '<body><header>Town Library</header><footer>Opening hours</footer>'
            '</body></html>')
    record = _extract("/events/category/childrens/list/?outlook-ical=1", html)
    assert record["not_gradable"] is True
    assert record["not_gradable_reason"] == "noindex"


def test_no_other_check_counts_it():
    """`audit_common.pages_of` is the one gate every other skill reads its
    pages through, and `skipped` is what it drops on."""
    hidden = _extract("/events/category/childrens/list/?ical=1", "",
                      headers=ICAL_HEADERS)
    real = _extract("/about", "<html lang=\"en\"><head><meta name=\"viewport\" "
                              "content=\"width=device-width\"></head><body><h1>About</h1>"
                              "<p>" + ("The library opens at nine. " * 20) + "</p>"
                              "</body></html>")
    urls = [p["url"] for p in pages_of({"pages": [hidden, real]})]
    assert urls == [SITE + "/about"]


def test_the_reason_reads_as_a_reason():
    """`compose_report.why_little_was_read` prints this string after a count,
    beside "redirects off this origin" and "non-HTML content type"."""
    record = _extract("/events/category/childrens/list/?ical=1", "",
                      headers=ICAL_HEADERS)
    assert record["skipped"].startswith("answered 200 with an empty body")


def test_a_javascript_shell_is_still_a_page():
    """A shell delivers no text either, and a shell is the site: it is reported
    as `js-shell`, which cannot happen if the record is set aside first."""
    record = _extract("/app", '<html><body><div id="root"></div>'
                              '<script>window.__NEXT_DATA__={}</script></body></html>')
    assert record.get("not_gradable") is None


def test_a_noindexed_page_with_content_on_it_stays_in_the_link_graph():
    """`noindex` is not `nofollow`. Setting a substantial page aside took its
    outbound links out of the site graph with it, and the page it was the only
    route to was then reported as "listed in the sitemap but linked from
    nowhere" - one false positive traded for another."""
    html = ('<html lang="en"><head><meta name="robots" content="noindex"></head>'
            '<body><main><h1>Pricing</h1><p>'
            + ("Every plan includes the same catalogue and the same support. " * 8)
            + '</p><a href="/products/autopilot">Autopilot</a></main></body></html>')
    record = _extract("/pricing", html)
    assert record.get("not_gradable") is None
    assert pages_of({"pages": [record]}) == [record]


def test_crawl_access_audit_still_sees_the_pages_that_were_set_aside():
    """The directive is a finding worth raising, and this is the skill that
    raises it - so it reads the set-aside records back out."""
    hidden = _extract("/events/category/childrens/list/?ical=1", "",
                      headers=ICAL_HEADERS)
    hidden["page_type"] = "category"
    real = _extract("/about", "<html><body><h1>About</h1><p>x</p></body></html>")
    real["page_type"] = "about"
    snapshot = {"pages": [hidden, real]}
    urls = [p["url"] for p in ACCESS.pages_including_noindexed(
        snapshot, pages_of(snapshot))]
    assert SITE + "/events/category/childrens/list/?ical=1" in urls
    assert SITE + "/about" in urls


# --------------------------------------------------------------------------
# 1b - the page that fell between the two rules
#
# A page shipping `<meta name="robots" content="noindex,nofollow">` was named
# in a finding about missing meta descriptions and counted in one about missing
# `og:title`, while the check whose whole purpose is to report the directive
# said "no crawled content page carries a noindex directive". It was typed
# `other`: the hygiene checks read every page `pages_of` returns, the noindex
# check read `page_type in CONTENT_TYPES`, and `other` is in the first set and
# not the second.
#
# The record's half of the fix is that there is one fact for both rules to
# read, and that nothing deciding whether a page is set aside looks at its
# type. So a page is either out of every check's population or in all of them,
# and the directive is on the record to be reported either way.
# --------------------------------------------------------------------------

NB6_HTML = ('<html lang="en"><head><meta name="robots" content="noindex,nofollow">'
            '<meta name="viewport" content="width=device-width"></head>'
            '<body><main><h1>Release notes</h1><p>'
            + ("Version 4.2 changes how holds are queued. " * 12)
            + '</p><a href="/help">Help</a></main></body></html>')


def test_the_page_that_fell_between_the_rules_declares_its_directive():
    record = _extract("/release-notes", NB6_HTML)
    record["page_type"] = "other"
    assert record["declares_noindex"] is True
    assert "noindex" in record["robots_directives"]


def test_a_page_graded_by_the_hygiene_checks_is_graded_by_all_of_them():
    """It holds content and links, so it stays in the population - which is the
    right answer, and was the right answer before. What was wrong is that one
    check read a different field and left it out."""
    record = _extract("/release-notes", NB6_HTML)
    record["page_type"] = "other"
    assert record.get("not_gradable") is None
    assert pages_of({"pages": [record]}) == [record]
    assert page_extract.declares_noindex(record) is True


def test_the_same_page_with_nothing_on_it_leaves_every_population_at_once():
    """The other side of the guarantee: set aside is `skipped`, which
    `pages_of` drops, so the page leaves all of them together rather than one
    at a time."""
    bare = ('<html><head><meta name="robots" content="noindex,nofollow"></head>'
            '<body><header>Town Library</header></body></html>')
    record = _extract("/release-notes", bare)
    record["page_type"] = "other"
    assert record["not_gradable"] is True
    assert record["not_gradable_reason"] == "noindex"
    assert pages_of({"pages": [record]}) == []
    assert record["declares_noindex"] is True


def test_the_page_type_never_decides_whether_a_page_is_set_aside():
    """The field the two rules disagreed on. Whatever the classifier made of
    the page, the record says the same thing about it."""
    for page_type in ("other", "article", "category", "home", "product"):
        record = _extract("/release-notes", NB6_HTML)
        record["page_type"] = page_type
        assert record.get("not_gradable") is None, page_type
        assert record["declares_noindex"] is True, page_type


def test_one_reader_answers_for_a_record_this_file_did_not_build():
    """`crawl.py` returns early for a redirect off the origin, a non-HTML
    content type and a body it could not decompress, so those records never
    reach the stamping above. Each carries `headers` and no `x_robots_tag` key,
    and a URL answering 200 with `Content-Type: application/json` and
    `X-Robots-Tag: noindex` is exactly the shape this question is asked
    about."""
    early = {"url": SITE + "/events.json", "status": 200, "skipped": "non-HTML content type",
             "page_type": "other", "headers": {"content-type": "application/json",
                                               "x-robots-tag": "noindex, nofollow"}}
    assert "declares_noindex" not in early
    assert page_extract.declares_noindex(early) is True
    assert page_extract.declares_noindex(
        {"url": SITE + "/x", "headers": {"content-type": "text/html"}}) is False


# --------------------------------------------------------------------------
# 2 - the counts a proportion needs
# --------------------------------------------------------------------------

def test_every_image_on_the_page_is_counted():
    """"page_extract.py records exactly 10 images per page ... which makes
    image-alt-coverage structurally unable to measure a site." The counts are
    over the page; only the URL lists are samples."""
    html = "<html><body>" + "".join(
        '<img src="/p{}.jpg">'.format(n) for n in range(232)) + "</body></html>"
    images = _images(make_soup(html), "https://shop.test/")
    assert images["count"] == 232
    assert images["missing_alt_count"] == 232
    assert images["undescribed_count"] == 232
    assert len(images["missing_alt_sample"]) == 20, "the sample is still a sample"


def test_the_distinct_count_is_not_read_off_the_sample():
    """The site-wide check counts distinct image URLs, because one shared
    header icon on sixty pages is one fix. It was counting them out of a
    twenty-entry sample, so 25 undescribed images on a homepage were reported
    as "4 distinct image URL(s) with no alt attribute"."""
    html = "<html><body>" + "".join(
        '<img src="/p{}.jpg">'.format(n) for n in range(25)) + "</body></html>"
    images = _images(make_soup(html), "https://shop.test/")
    assert images["undescribed_distinct_count"] == 25
    assert images["missing_alt_distinct_count"] == 25
    assert images["distinct_count"] == 25
    assert len(images["undescribed_sample"]) == 20


def test_one_template_image_repeated_is_one_distinct_image():
    """The distinct count is what the number of edits the fix takes; a header
    texture on every page is one line in one template."""
    html = ('<html><body><img src="/texture.png"><img src="/texture.png">'
            '<img src="/texture.png"></body></html>')
    images = _images(make_soup(html), "https://shop.test/")
    assert images["count"] == 3
    assert images["undescribed_count"] == 3
    assert images["undescribed_distinct_count"] == 1


# --------------------------------------------------------------------------
# 3 - the trail whose container is not called "breadcrumb"
# --------------------------------------------------------------------------

PANKUZU = (
    '<html><body><div class="pankuzu">'
    '<span property="itemListElement" typeof="ListItem">'
    '<a property="item" href="/"><span property="name">Home</span></a></span> &gt; '
    '<span property="itemListElement" typeof="ListItem">'
    '<a property="item" href="/tea"><span property="name">Tea</span></a></span> &gt; '
    '<span property="itemListElement" typeof="ListItem">'
    '<span property="name">Sencha</span></span>'
    '</div></body></html>'
)


def test_an_rdfa_trail_counts_however_its_container_is_named():
    """`pankuzu` is Japanese for breadcrumb. The selector was the English word,
    so all 15 deep pages of that site were reported as showing no trail and no
    link up to the section they sit in."""
    breadcrumb = _breadcrumb(make_soup(PANKUZU), [])
    assert breadcrumb["visible"] is True
    assert breadcrumb["markup"] is True
    assert breadcrumb["itemlist_chain"] is True


def test_a_microdata_trail_counts_too():
    html = ('<html><body><ol class="wegweiser">'
            '<li itemprop="itemListElement" itemtype="https://schema.org/ListItem">'
            '<a href="/"><span itemprop="name">Start</span></a></li>'
            '<li itemprop="itemListElement" itemtype="https://schema.org/ListItem">'
            '<a href="/tee"><span itemprop="name">Tee</span></a></li>'
            '</ol></body></html>')
    assert _breadcrumb(make_soup(html), [])["visible"] is True


def test_a_page_with_no_trail_still_has_none():
    html = '<html><body><h1>Sencha</h1><p>A green tea.</p></body></html>'
    breadcrumb = _breadcrumb(make_soup(html), [])
    assert breadcrumb["any"] is False
    assert breadcrumb["itemlist_chain"] is False


def test_a_product_carousel_is_not_a_breadcrumb():
    """`itemListElement` marks up a carousel as well as a trail. A trail is a
    line of a few short links; a carousel is a block of names and prices, and
    reading one as a trail would silence a true finding."""
    html = '<html><body><div class="grid">' + "".join(
        '<div property="itemListElement" typeof="ListItem">'
        '<a href="/p{n}"><span property="name">Single origin sencha lot {n}, '
        'harvested in spring, 100g caddy</span></a></div>'.format(n=n)
        for n in range(12)) + '</div></body></html>'
    assert _breadcrumb(make_soup(html), [])["itemlist_chain"] is False


# --------------------------------------------------------------------------
# 4 - the one-page site whose menu is its fragments
# --------------------------------------------------------------------------

ONE_PAGER = (
    '<html><body><nav>'
    '<a href="#hero">INTRO</a><a href="#services">USLUGI</a>'
    '<a href="#works">PORTFOLIO</a><a href="#contact">KONTAKT</a>'
    '<a href="/pl/">PL</a><a href="/en/">EN</a></nav>'
    '<section id="hero">x</section><section id="services">y</section>'
    '<section id="works">z</section><section id="contact">w</section>'
    '</body></html>'
)


def test_the_menu_of_a_one_page_site_is_its_fragment_anchors():
    """The report said, in full: "the primary navigation has 2 item(s). Labels
    found: "EN", "PL"." Four labelled sections were discarded on the way."""
    links = _links(make_soup(ONE_PAGER), "https://studio.test/", "https://studio.test")
    labels = [entry["text"] for entry in links["nav"]]
    assert labels[:4] == ["INTRO", "USLUGI", "PORTFOLIO", "KONTAKT"]
    assert links["nav_visible_count"] == 6


def test_a_skip_link_on_a_site_with_pages_is_still_not_a_menu_item():
    """A fragment anchor on a sixty-page site is a skip link or an accordion.
    Whichever kind of link the navigation holds more of is what it is made
    of, so a menu of paths is unaffected."""
    html = ('<html><body><nav><a href="#main">Skip to content</a>'
            + "".join('<a href="/p{n}">Page {n}</a>'.format(n=n) for n in range(8))
            + '</nav><main id="main">Text</main></body></html>')
    links = _links(make_soup(html), "https://big.test/", "https://big.test")
    assert "Skip to content" not in [entry["text"] for entry in links["nav"]]
    assert len(links["nav"]) == 8


def test_a_fragment_the_page_does_not_answer_at_is_not_a_section():
    """An address with no element behind it is a JavaScript hook."""
    html = ('<html><body><nav><a href="#open-menu">Menu</a>'
            '<a href="#open-search">Search</a></nav>'
            '<p>Nothing on this page has either id.</p></body></html>')
    links = _links(make_soup(html), "https://x.test/", "https://x.test")
    assert links["nav"] == []


# --------------------------------------------------------------------------
# 5a - a Crawl-delay that starves every compliant crawler
# --------------------------------------------------------------------------

def _robots(delay):
    return {"status": 200, "url": "https://skincare.test/robots.txt",
            "groups": [{"agents": ["*"], "allow": [], "disallow": [],
                        "crawl_delay": delay}],
            "sitemaps": [], "errors": []}


def _crawl_delay_result(delay, pages_read=0, elapsed=186.0):
    result = SkillResult("crawl-access-audit")
    snapshot = {"origin": "https://skincare.test", "pages": [],
                "crawl": {"pages_read": pages_read, "pages_crawled": pages_read,
                          "elapsed_s": elapsed}}
    ACCESS._check_crawl_delay(result, _robots(delay), snapshot)
    return result


def test_a_forty_five_second_crawl_delay_is_reported_to_the_owner():
    """"Crawl-delay: 45 is arguably the highest-severity discoverability defect
    on the site: it starves every compliant AI crawler." The tool honoured it,
    read 0 pages in 186 seconds, and said so only in an appendix note about
    itself."""
    result = _crawl_delay_result(45)
    finding = next(f for f in result.findings
                   if f["id_hint"] == "robots-crawl-delay-starves-crawlers")
    assert finding["severity"] == "high"
    assert finding["root_cause"] == "robots-block"
    assert "Crawl-delay: 45" in finding["evidence"]
    assert "read 0 page(s)" in finding["evidence"]


def test_a_delay_a_crawler_can_live_with_is_not_a_finding():
    result = _crawl_delay_result(1)
    assert result.findings == []
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "robots-crawl-delay")
    assert "1s between requests" in reason


def test_no_crawl_delay_line_declines_with_that_reason():
    result = _crawl_delay_result(None)
    assert result.findings == []
    reason = next(n["reason"] for n in result.not_applicable
                  if n["check"] == "robots-crawl-delay")
    assert "sets no `Crawl-delay`" in reason


# --------------------------------------------------------------------------
# 5b - a rate limiter that answered, and the route the owner did not write
# --------------------------------------------------------------------------

class _Response(object):
    def __init__(self, status, headers=None):
        self.status_code = status
        self.headers = headers or {}


def test_retry_after_reads_both_forms_the_header_takes():
    assert crawl_module.retry_after_seconds("30") == 30.0
    assert crawl_module.retry_after_seconds("") is None
    assert crawl_module.retry_after_seconds(None) is None
    assert crawl_module.retry_after_seconds("not a number") is None
    # An HTTP-date already in the past means "ask again now", not a negative
    # wait that would cancel the delay the crawl is already keeping.
    past = crawl_module.retry_after_seconds("Wed, 21 Oct 2015 07:28:00 GMT")
    assert past == 0.0


def test_a_429_slows_the_crawl_by_what_the_site_asked_for():
    """A grocer answered 429 on 45 of 59 URLs. Nothing read `Retry-After` and
    nothing backed off, so the crawl kept asking at the same rate and the
    report then withheld nine true findings because of throttling it had
    caused itself."""
    fetcher = crawl_module.PageFetcher.__new__(crawl_module.PageFetcher)
    fetcher.delay = 0.5
    fetcher.deadline = None
    fetcher.rate_limited_urls = []
    fetcher.retry_after_s = None
    fetcher.rate_limit_gave_up = False
    fetcher.slowed_to = None
    fetcher._back_off("https://grocer.test/aisle", _Response(429, {"retry-after": "5"}))
    assert fetcher.delay == 5.0
    assert fetcher.retry_after_s == 5.0
    assert fetcher.rate_limit_gave_up is False
    assert fetcher.rate_limited_urls == ["https://grocer.test/aisle"]


def test_a_wait_longer_than_the_run_has_left_stops_the_crawl():
    """Staying inside the wall-clock budget matters more than finishing the
    crawl. A wait that runs past the deadline buys nothing - the request would
    be refused by the clock anyway - so the crawl degrades and says so."""
    import time as _time
    fetcher = crawl_module.PageFetcher.__new__(crawl_module.PageFetcher)
    fetcher.delay = 0.5
    fetcher.deadline = _time.monotonic() + 3
    fetcher.rate_limited_urls = []
    fetcher.retry_after_s = None
    fetcher.rate_limit_gave_up = False
    fetcher.slowed_to = None
    fetcher._back_off("https://grocer.test/aisle", _Response(429, {"retry-after": "600"}))
    assert fetcher.rate_limit_gave_up is True
    assert fetcher.delay == 0.5, "the crawl does not sleep past its own deadline"


def test_the_backoff_is_bounded():
    fetcher = crawl_module.PageFetcher.__new__(crawl_module.PageFetcher)
    fetcher.delay = 0.5
    fetcher.deadline = None
    fetcher.rate_limited_urls = []
    fetcher.retry_after_s = None
    fetcher.rate_limit_gave_up = False
    fetcher.slowed_to = None
    fetcher._back_off("https://grocer.test/a", _Response(429, {"retry-after": "9000"}))
    assert fetcher.delay == crawl_module.RATE_LIMIT_MAX_DELAY


def test_a_cdn_email_decoder_is_never_requested():
    """`/cdn-cgi/l/email-protection` is the href of every obfuscated mailto on
    the page and returns 404 to a plain GET by design. It led one report's
    "Start here" at high severity as "1 of 3 fetched URLs returned a non-200
    status (1x404)" on a healthy one-page site."""
    ok, reason = crawl_module.crawlable(
        "https://studio.test/cdn-cgi/l/email-protection",
        "https://studio.test", {}, respect_robots=False)
    assert ok is False
    assert "CDN" in reason
    # An ordinary page on the same site is unaffected.
    assert crawl_module.crawlable(
        "https://studio.test/contact", "https://studio.test", {},
        respect_robots=False)[0] is True


def test_the_snapshot_says_what_the_rate_limiter_did(tmp_path):
    """Written on every crawl, so a check can tell "nothing was rate limited"
    from "this snapshot predates the question"."""
    from fixture_server import FixtureServer
    from conftest import FIXTURES
    with FixtureServer(os.path.join(FIXTURES, "good-site")) as server:
        snapshot = crawl_module.crawl(
            server.base_url, str(tmp_path / "snapshot.json"),
            max_pages=3, budget_s=60, respect_robots=False, delay=0, render=False)
    rate_limit = snapshot["crawl"]["rate_limit"]
    assert rate_limit["count"] == 0
    assert rate_limit["gave_up"] is False
    assert rate_limit["urls"] == []


# --------------------------------------------------------------------------
# Handed over: the readability numbers still described the page with its menu
# in the text, and the two fields two other skills were waiting on
# --------------------------------------------------------------------------

def test_the_site_furniture_pass_redoes_the_readability_numbers():
    """`_strip_sitewide_boilerplate` rewrites `body_text`, `word_count`,
    `above_fold_text` and `cta`, and left `readability` describing the page
    with its account menu and category nav still in it. One report read
    "47% long, average 67.6 words" off a run whose own notes say twelve blocks
    of site furniture had been identified."""
    before = {"sentence_count": 14, "avg_sentence_words": 67.6,
              "long_sentence_count": 7, "long_sentence_share": 0.47,
              "wall_of_text_count": 0, "subheading_count": 2,
              "words_per_subheading": 500.0, "has_subheadings": True,
              "list_text_share": 0.1}
    after = page_extract.recount_readability(
        dict(before), "The club meets monthly. Anyone may join, and there is no fee.")
    assert after["sentence_count"] == 2
    assert after["long_sentence_share"] == 0.0
    assert after["avg_sentence_words"] == 6.0
    assert after["words_per_subheading"] == 6.0
    # Measurements of the document, which the crawl no longer holds when the
    # furniture pass runs, are left exactly as the extractor took them.
    for key in ("wall_of_text_count", "subheading_count", "list_text_share"):
        assert after[key] == before[key]


def test_the_crawl_calls_it():
    """The recompute is only worth anything if the pass that changes the text
    is the thing that runs it."""
    import inspect
    source = inspect.getsource(crawl_module._strip_sitewide_boilerplate)
    assert "recount_readability" in source


def test_a_run_of_menu_labels_is_not_a_long_sentence():
    """The longest "sentence" on one page was 307 words: "login all Profile
    Orders Store Credit RM 0.00 Favourites Address Book Review Purchases
    Membership My Coupon Affiliate Log Out all shop categories ...". Nothing
    terminates it and nothing separates its parts, because they were separate
    elements of a menu."""
    menu = ("login all Profile Orders Store Credit RM 0.00 Favourites Address Book "
            "Review Purchases Membership My Coupon Affiliate Log Out all shop "
            "categories signature market breakfast nuts snacks drinks pantry gifts "
            "wellness beauty home kitchen baby pets outdoors travel books music")
    sents, set_aside = page_extract.prose_sentences(menu)
    assert sents == []
    assert set_aside == 1


def test_a_long_sentence_that_is_writing_is_still_counted():
    """The rule may not swallow prose. A sentence that long either ends in a
    terminator or has a clause separator in it, and this one has both."""
    prose = ("The library has run a reading club since nineteen twelve, and it still "
             "meets on the first Tuesday of every month in the upstairs room, where "
             "anyone from the district is welcome to join without paying a "
             "subscription of any kind.")
    sents, set_aside = page_extract.prose_sentences(prose)
    assert len(sents) == 1
    assert set_aside == 0


def test_a_long_sentence_in_a_script_with_no_latin_punctuation_is_still_writing():
    """A test for an English verb would read every Japanese, Polish and Arabic
    page as a list. The terminator and the clause separator are read in the
    scripts this audit has met, not only in Latin ones."""
    japanese = (u"当店は平成元年からこの"
                u"土地で茶をつくっており"
                u"、毎年春になると一番茶"
                u"の収穫をご案内しており"
                u"ます。")
    sents, set_aside = page_extract.prose_sentences(japanese)
    assert set_aside == 0
    assert len(sents) == 1


def test_a_short_fragment_with_no_full_stop_is_untouched():
    """A heading or a caption is under the word bound and is not a run."""
    sents, set_aside = page_extract.prose_sentences("Opening hours and directions")
    assert sents == ["Opening hours and directions"]
    assert set_aside == 0


def test_a_video_a_visitor_can_operate_is_recorded_as_such():
    """`render-readability-audit` caps its background-loop count at
    `native_count - controls_count`: a clip with controls is one somebody may
    play deliberately, and may hold speech worth transcribing."""
    from audit_common import make_soup as _soup
    video = page_extract._video(
        _soup('<video autoplay muted loop controls></video>'
              '<video autoplay muted loop></video>'), "", "https://x.test/")
    assert video["native_count"] == 2
    assert video["controls_count"] == 1


def test_muted_alone_is_not_recorded_as_a_reason_to_call_a_clip_decorative():
    """Only `controls`. `tests/test_wallpaper_and_records.py` locks in that
    `muted` alone must never mark a clip decorative."""
    from audit_common import make_soup as _soup
    video = page_extract._video(
        _soup('<video autoplay muted loop></video>'), "", "https://x.test/")
    assert video["controls_count"] == 0
    assert "muted_count" not in video


# --------------------------------------------------------------------------
# Handed over: a title template whose brand slot came out empty
# --------------------------------------------------------------------------

def test_an_og_site_name_that_lost_its_brand_is_not_the_brand():
    """Every page of one site carries
    `<meta property="og:site_name" content="- Empowering communities">`. The
    separator was stripped as stray punctuation and the tagline became the
    company's name - in the verdict, in four fix templates, in a Wikidata
    lookup and in a paste-ready Organization snippet."""
    pages = [{"url": "https://community.test/", "page_type": "home", "status": 200,
              "title": "Community Trust", "headings": {"h1": ["Community Trust"]},
              "jsonld": [], "og": {"og:site_name": "- Empowering communities"}}]
    brand = crawl_module.detect_brand(pages, "https://community.test")
    assert "Empowering communities" != brand["name"]
    assert brand["source"] != "og:site_name"


def test_a_name_that_lost_its_tagline_keeps_its_name():
    """"Acme -" has lost its tagline, not its name. Rejecting that would throw
    a good name away, so only the leading position counts."""
    pages = [{"url": "https://acme.test/", "page_type": "home", "status": 200,
              "title": "Acme", "headings": {"h1": ["Acme"]}, "jsonld": [],
              "og": {"og:site_name": "Acme Instruments -"}}]
    brand = crawl_module.detect_brand(pages, "https://acme.test")
    assert brand["name"] == "Acme Instruments"


def test_the_domain_spelling_reading_is_the_shared_one():
    """Two spellings of one rule is the drift this codebase argues against.
    `crawl.py::_site_spelling` is now the homepage-shaped wrapper around
    `audit_common.name_the_site_spells`."""
    import inspect
    from audit_common import name_the_site_spells
    assert "name_the_site_spells" in inspect.getsource(crawl_module._site_spelling)
    assert crawl_module.name_the_site_spells is name_the_site_spells


# --------------------------------------------------------------------------
# Handed over: the sitemap snippet wrote its own URLs and its own date
# --------------------------------------------------------------------------

def _snapshot_for_snippet(pages):
    return {"origin": "https://shop.test", "pages": pages}


def _crawled(path, page_type="other", dates=None, headers=None):
    url = "https://shop.test" + path
    return {"url": url, "final_url": url, "status": 200, "page_type": page_type,
            "dates": dates or {}, "headers": headers or {}}


def test_the_sitemap_snippet_lists_urls_the_crawl_actually_fetched():
    """`<loc>{origin}/about</loc>` was written into the snippet on every site.
    It turned up on three of eleven, and `/about` returned 404 on all three -
    one a single-page site where it cannot exist, another writing its URLs in
    Arabic slugs."""
    snippet = ACCESS._sitemap_snippet(_snapshot_for_snippet([
        _crawled("/", "home"), _crawled("/kontakt"), _crawled("/produkty")]))
    assert "/about" not in snippet
    assert "<loc>https://shop.test/kontakt</loc>" in snippet
    # The homepage first, as every generator writes one.
    assert snippet.index("https://shop.test/<") < snippet.index("/kontakt")


def test_a_lastmod_is_printed_only_where_a_date_was_read():
    """The written-in date was seven months before the audit and sat directly
    above the snippet's own bullet: "Include a <lastmod> date on every entry
    and keep it accurate"."""
    snippet = ACCESS._sitemap_snippet(_snapshot_for_snippet([
        _crawled("/", "home", dates={"jsonld_date_modified": "2026-08-14T10:00:00Z"}),
        _crawled("/undated"),
        _crawled("/header-date",
                 headers={"last-modified": "Tue, 15 Nov 2025 12:45:26 GMT"})]))
    assert "<loc>https://shop.test/</loc><lastmod>2026-08-14</lastmod>" in snippet
    assert "<loc>https://shop.test/undated</loc></url>" in snippet
    assert "<lastmod>2025-11-15</lastmod>" in snippet
    assert "2026-01-31" not in snippet


def test_an_ampersand_in_a_crawled_url_does_not_break_the_xml():
    snippet = ACCESS._sitemap_snippet(_snapshot_for_snippet([
        _crawled("/list?page=2&sort=new")]))
    assert "page=2&amp;sort=new" in snippet
    assert "page=2&sort=new" not in snippet


def test_a_crawl_that_read_nothing_brackets_what_it_could_not_supply():
    """The one case with no address to offer. Bracketed in the wording the
    Organization snippet uses, because that is what tells a reader at a glance
    which half is theirs to fill in."""
    snippet = ACCESS._sitemap_snippet(_snapshot_for_snippet([]))
    assert "fill this in>" in snippet


def test_the_snippet_is_capped():
    pages = [_crawled("/p{}".format(n)) for n in range(40)]
    snippet = ACCESS._sitemap_snippet(_snapshot_for_snippet(pages))
    assert snippet.count("<url>") == ACCESS.SITEMAP_SNIPPET_MAX_URLS
