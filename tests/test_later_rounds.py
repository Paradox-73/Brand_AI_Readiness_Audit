"""What fourteen sites found across rounds five and six, and the classes behind them.

Five agents ran the shipped zip on a city government, a university department,
a German manufacturer, a bilingual broadcaster, a national newspaper, a public
radio programme, a developer-tools company, an open-source framework, a pizza
chain and a single restaurant. 135 findings checked, 27 wrong or misleading.

Round 4 was 40%. This is 20%. Rule 5 - an agent's role comes from its
operator's own documentation - held on every site that exercised it, which was
the whole point of adding it. The failures below are the ones left, and each is
fixed as a class rather than as a case.

Two of the fixes in this file were themselves wrong on their first real run,
against a site none of the five agents had touched, and both are pinned here
too: a profile filter that turned "links to only 4" into "links to none at
all", and a hidden-navigation check that read a plainly visible menu bar as
hidden because the same links also appear in a dropdown.
"""

from __future__ import annotations

import importlib.util
import io
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    main_text, make_soup, response_timing, SkillResult, slow_origin_note,
)
from robots_parser import is_disallowed, parse_robots  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_round5")


# --------------------------------------------------------------------------
# A robots.txt token matches by prefix, never by substring
# --------------------------------------------------------------------------

FETCH_ROBOTS = parse_robots(
    "User-agent: Fetch\nDisallow: /\n\nUser-agent: *\nAllow: /\n")


def test_a_stray_token_inside_a_crawler_name_does_not_block_it():
    """A broadcaster ships a legacy blocklist entry reading `User-agent: Fetch`.

    Because "fetch" occurs inside "Meta-ExternalFetcher", the report said that
    crawler was disallowed from the whole site. The group that governs it there
    is `*`, which disallows nothing, so the finding was about a rule doing
    nothing to a crawler that was never blocked.

    Substring matching cannot be tuned safe: "bot" is inside almost every
    crawler name, and a shorter stray token captures more of them.
    """
    assert not is_disallowed(FETCH_ROBOTS, "Meta-ExternalFetcher", "/")
    assert not is_disallowed(FETCH_ROBOTS, "ChatGPT-User", "/")


def test_the_agent_the_rule_names_is_still_blocked():
    assert is_disallowed(FETCH_ROBOTS, "Fetch", "/")


def test_a_longer_name_starting_with_the_token_is_still_blocked():
    """Prefix matching is what RFC 9309 specifies, so `Fetcher` matches."""
    assert is_disallowed(FETCH_ROBOTS, "Fetcher", "/")


# --------------------------------------------------------------------------
# A guess about furniture may not delete the page
# --------------------------------------------------------------------------

def _announcement_page(count=10, chars=300):
    body = "".join(
        '<article class="node-announcement"><h2>Item {}</h2><p>{}</p></article>'.format(
            i, "x" * chars) for i in range(count))
    return ("<html><body><header><nav>Home About</nav></header>"
            "<main>" + body + "</main><footer>c</footer></body></html>")


def test_a_cms_content_type_whose_class_says_announcement_survives():
    """`[class*=announcement]` was written for the free-shipping bar above the
    header. On a public radio programme's site it matched
    `<article class="node-announcement">` - the CMS's own name for the content
    type - and deleted the page. Ten dated announcements, 3,228 characters,
    became 34, and the audit reported the page twice: once as text locked in
    images and once as too thin to quote. Both findings were false and both
    were about our own selector.
    """
    text = main_text(make_soup(_announcement_page()))
    assert len(text) > 2500, "the page was stripped to {} chars".format(len(text))
    assert "Item 7" in text


def test_the_announcement_bar_it_was_written_for_is_still_stripped():
    """The bar is real, and so is the retailer that stacks one free-shipping
    threshold per locale in it - "$40", "$60 CAD", "$110 AUD", "R$670" at once
    - which the audit read as the prices on the page."""
    html = ('<html><body><div class="announcement-bar">Free shipping over $40</div>'
            '<header><nav>Home</nav></header><main><p>' + "y" * 2000 +
            '</p></main></body></html>')
    text = main_text(make_soup(html))
    assert "Free shipping" not in text


def test_what_the_page_declares_as_chrome_comes_out_whatever_its_size():
    """The bound applies to guesses from a class name, not to a landmark
    element or an `aria-hidden` attribute. Those are the page saying so."""
    html = ('<html><body><nav>' + "n" * 4000 + '</nav>'
            '<main><p>the real content</p></main></body></html>')
    text = main_text(make_soup(html))
    assert "nnnn" not in text and "the real content" in text


# --------------------------------------------------------------------------
# A finding may only claim the checks its own function registered
# --------------------------------------------------------------------------

def _finding(**over):
    base = dict(id_hint="x", title="t", severity="low", confidence="low",
                evidence="e", mechanism="A", root_cause="robots-block",
                summary="s", how_to_fix=["f"], effort="low", owner="developer",
                rationale="r")
    base.update(over)
    return base


def test_a_check_left_unanswered_elsewhere_is_not_swept_up_as_fired():
    """`_group` was a flat list that only ever emptied on `skip()`.

    A check registered in one function and answered by neither a finding nor a
    decline sat in it until some unrelated `add()` later in the same skill
    swept it up as "fired" - which excludes it from the passed list without
    putting it anywhere else. Seven of seventy-one checks that ran on one site
    appeared nowhere in the report: not as findings, not as declines, not as
    passes. Among them was whether robots.txt blocks AI crawlers.
    """
    result = SkillResult("t")

    def registers_and_finds_nothing():
        result.check("robots-blocks-ai-answer-crawlers")
        result.check("robots-txt-reachable")

    def finds_something_else():
        result.check("sitemap-present")
        result.add(**_finding())

    registers_and_finds_nothing()
    finds_something_else()

    assert result.fired_checks == {"sitemap-present"}
    assert "robots-blocks-ai-answer-crawlers" not in result.fired_checks
    assert "robots-txt-reachable" not in result.fired_checks


def test_a_finding_still_claims_every_check_its_own_function_registered():
    """The behaviour this preserves: several checks registered together at the
    top of one function, any of which the finding might be about."""
    result = SkillResult("t")

    def one_function():
        result.check("sitemap-present")
        result.check("sitemap-parses")
        result.add(**_finding(root_cause="sitemap-missing"))

    one_function()
    assert result.fired_checks == {"sitemap-present", "sitemap-parses"}


def test_a_finding_raised_by_a_helper_still_claims_its_callers_checks():
    """A `_check_*` function often registers and then delegates."""
    result = SkillResult("t")

    def helper():
        result.add(**_finding())

    def outer():
        result.check("robots-blocks-all-crawlers")
        helper()

    outer()
    assert result.fired_checks == {"robots-blocks-all-crawlers"}


def test_an_explicit_decline_still_wins_over_a_sibling_that_fired():
    result = SkillResult("t")

    def one_function():
        result.check("sitemap-present")
        result.skip("sitemap-parses", "no sitemap to parse")
        result.add(**_finding(root_cause="sitemap-missing"))

    one_function()
    assert result.fired_checks == {"sitemap-present"}
    assert [n["check"] for n in result.not_applicable] == ["sitemap-parses"]


# --------------------------------------------------------------------------
# Slow is not down
# --------------------------------------------------------------------------

def _timed(*millis):
    return [{"url": "https://x.test/{}".format(i), "status": 200, "elapsed_ms": ms}
            for i, ms in enumerate(millis)]


def test_a_slow_origin_is_described_as_slow_not_as_an_outage():
    """One site answered every page in ten to forty seconds. Enough requests
    timed out that the report called it unreachable, with outage wording and a
    fix reading "read the server log for the failing request" - on a site that
    was up, serving correct HTML, and merely slow."""
    note = slow_origin_note(_timed(11000, 24000, 38000))
    assert "answers slowly rather than not at all" in note
    assert "24.0 s" in note


def test_a_normal_origin_adds_no_such_sentence():
    assert slow_origin_note(_timed(180, 240, 310)) == ""


def test_an_untimed_run_is_not_reported_as_fast():
    """`--no-network` and a fully blocked site both produce no measurement, and
    a missing measurement is not a fast one."""
    assert response_timing([]) is None
    assert slow_origin_note([]) == ""


def test_the_slow_origin_finding_fires_and_says_what_it_measured():
    result = SkillResult("crawl-access-audit")
    CA._report_response_time(result, _timed(11000, 24000, 38000))
    assert [f["root_cause"] for f in result.findings] == ["slow-origin"]
    assert "24.0 s" in result.findings[0]["evidence"]
    assert result.findings[0]["severity"] == "high"


def test_a_merely_sluggish_origin_is_medium_not_high():
    result = SkillResult("crawl-access-audit")
    CA._report_response_time(result, _timed(3200, 3400, 3900))
    assert result.findings and result.findings[0]["severity"] == "medium"


def test_a_fast_origin_declines_the_check_and_says_the_number():
    result = SkillResult("crawl-access-audit")
    CA._report_response_time(result, _timed(180, 240, 310))
    assert not result.findings
    assert "0.2 s" in result.not_applicable[0]["reason"]


# --------------------------------------------------------------------------
# A place word in the plural is a place; in the singular it is a shop front
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/our-shops", "/our-locations", "/our-stores", "/store-finder",
    "/store-locator", "/branch-directory", "/where-we-are", "/branches",
    "/clinics", "/find-a-store", "/all-locations", "/restaurants", "/offices",
])
def test_a_locations_page_is_recognised_however_it_is_named(path):
    """A pizza chain kept its sixty branches at `/pizzerias/` and
    `/restaurants/`. No slug matched, so all 43 branch pages in the crawl were
    typed "other", dropped from every content check, and per-branch
    `LocalBusiness` markup - the highest-value fix a chain can make - was never
    recommended at all.
    """
    from audit_common import detect_page_type
    assert detect_page_type("https://x.test" + path, {
        "text": "", "headings": [], "jsonld_types": [], "title": ""}) == "location"


@pytest.mark.parametrize("path", ["/shop", "/store", "/shop/dresses", "/office"])
def test_a_singular_shop_front_is_not_a_locations_page(path):
    """`/shop` is the way into an online shop; `/shops` is a list of branches.
    One letter apart, two kinds of page - so matching the singular would have
    turned every e-commerce entry point into a locations page, which is worse
    than the miss it was fixing."""
    from audit_common import detect_page_type
    assert detect_page_type("https://x.test" + path, {
        "text": "", "headings": [], "jsonld_types": [], "title": ""}) != "location"


@pytest.mark.parametrize("path", [
    "/products/press-on-sponge", "/services/office-fitout",
    "/blog/studios-of-the-year", "/restaurant-week-menu", "/locate-a-vein",
])
def test_the_new_shapes_do_not_capture_ordinary_pages(path):
    from audit_common import detect_page_type
    assert detect_page_type("https://x.test" + path, {
        "text": "", "headings": [], "jsonld_types": [], "title": ""}) != "location"


# --------------------------------------------------------------------------
# The denominator is the population the check looked at
# --------------------------------------------------------------------------

COMPOSE = _module(os.path.join(SCRIPTS, "compose_report.py"), "compose_for_round5")

SNAPSHOT_WITH_TYPES = {
    "origin": "https://x.test", "site": "x.test",
    "pages": (
        [{"url": "https://x.test/a{}".format(i), "status": 200,
          "page_type": "article", "text": "t"} for i in range(7)]
        + [{"url": "https://x.test/p{}".format(i), "status": 200,
            "page_type": "product", "text": "t"} for i in range(53)]),
}


def test_a_finding_about_article_pages_is_sized_against_article_pages():
    """"7 of the 60 pages" reads as a 12% problem when it is a 100% one, and an
    owner triaging by that number does the wrong thing."""
    finding = {"evidence": "Article pages with no Article JSON-LD: a, b, c.",
               "affected_pages": ["https://x.test/a{}".format(i) for i in range(5)],
               "affected_page_count": 7}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "7 of the 7 article pages" in out


def test_a_finding_spanning_page_types_is_still_sized_against_the_crawl():
    finding = {"evidence": "Pages with no H1: a, b.",
               "affected_pages": ["https://x.test/a0", "https://x.test/p0"],
               "affected_page_count": 9}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "9 of the 60 pages this crawl read" in out


def test_a_finding_that_already_names_its_population_gets_no_second_one():
    """The appended sentence read "Seen on 2 of the 60 pages this crawl read"
    one clause after the evidence said "Checked 46 content page(s). None
    declares..." - contradicting it inside the same sentence."""
    finding = {"evidence": "Checked 46 content page(s) including /a, /b. None declares an "
                           "Organization-level JSON-LD type.",
               "affected_pages": ["https://x.test/a0", "https://x.test/a1"],
               "affected_page_count": 2}
    out = COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES)
    assert "Seen on" not in out
    assert out == finding["evidence"]


@pytest.mark.parametrize("evidence", [
    "Checked 12 product pages; none carries a price.",
    "Examined 8 pages and found no date.",
    "Across 30 pages the footer address differs.",
    "0 of 12 product pages contain schema.org markup.",
    "44% of pages have no H1.",
])
def test_every_way_a_finding_can_state_its_own_scope_is_recognised(evidence):
    finding = {"evidence": evidence, "affected_pages": ["https://x.test/a0"],
               "affected_page_count": 1}
    assert COMPOSE.state_the_denominator(finding, 60, SNAPSHOT_WITH_TYPES) == evidence


def test_a_finding_with_no_stated_scope_still_gets_one():
    finding = {"evidence": "The footer address differs from the contact page.",
               "affected_pages": ["https://x.test/a0", "https://x.test/p0"],
               "affected_page_count": 4}
    assert "4 of the 60" in COMPOSE.state_the_denominator(
        finding, 60, SNAPSHOT_WITH_TYPES)


# --------------------------------------------------------------------------
# A crawler name may never be typed into a sentence (the round-4 rule, again)
# --------------------------------------------------------------------------

def test_the_challenge_page_finding_renders_its_agents_from_the_table():
    """Round 4's fix rebuilt the robots.txt findings from the agent table.

    Forty lines away in the same file, the finding that fires when a bot
    manager serves a challenge page instead of the page still had a list of
    four crawler names typed into a remediation step, described as crawlers
    that "fetch a page to answer a question". One of them is a training
    crawler; another fetches nothing at all. Two verification agents found it
    independently, on two different sites, after the fix that was meant to
    remove exactly that sentence - because nothing connected the string to the
    data.

    The general test lives in test_crawler_roles.py. This one pins the case.
    """
    source = io.open(os.path.join(
        ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
        encoding="utf-8").read()
    step = source[source.index("bot-manager-serves-a-challenge-page"):]
    step = step[:step.index("def _check_robots_reachable")]
    assert "describe_agents(ANSWER_CRAWLERS" in step


# --------------------------------------------------------------------------
# A profile is somebody's, and shape does not say whose
# --------------------------------------------------------------------------

from audit_common import (  # noqa: E402
    profile_is_opaque, profile_names_brand, profiles_naming_brand,
)


@pytest.mark.parametrize("url,brand,expected", [
    # A contributor thanked in an open-source project's footer.
    ("https://github.com/Some-Person", "Preact", False),
    ("https://github.com/preactjs", "Preact", True),
    # A blog post about signals cited these; neither is about the project.
    ("https://en.wikipedia.org/wiki/Pure_function", "Preact", False),
    ("https://en.wikipedia.org/wiki/Time_complexity", "Preact", False),
    # An article that really is about the company.
    ("https://en.wikipedia.org/wiki/Example_Manufacturing_GmbH",
     "Example Manufacturing", True),
    # Handles brands really use.
    ("https://www.linkedin.com/company/example-global", "Example", True),
    ("https://x.com/getexample", "Example", True),
    ("https://youtube.com/ExampleGlobal", "Example", True),
])
def test_a_profile_counts_only_when_it_names_the_brand(url, brand, expected):
    """A quantified, false headline claim: "Distinct off-site profiles linked:
    Bluesky, GitHub, Wikipedia, X". Two of the four were a contributor's
    personal account and an article about a computer-science term, while the
    project's own organisation page - linked dozens of times, always as a
    repository path - was never counted at all."""
    assert profile_names_brand(url, brand) is expected


def test_a_profile_identified_by_number_cannot_be_named_and_still_counts():
    """A Wikidata item is a Q-number and a YouTube channel is a UC-string.
    Requiring a handle to name the brand dropped a fixture's own correct
    Wikidata link."""
    assert profile_is_opaque("https://www.wikidata.org/wiki/Q42")
    assert profile_is_opaque("https://www.youtube.com/channel/UCabcdef")
    assert not profile_is_opaque("https://en.wikipedia.org/wiki/Pure_function")


def test_a_profile_the_site_claims_in_same_as_needs_no_further_proof():
    """`sameAs` says "these accounts are me", and that settles it even for the
    platforms whose URL does state its subject."""
    candidates = {"Wikipedia": "https://en.wikipedia.org/wiki/Pure_function"}
    assert profiles_naming_brand(candidates, "Example") == {}
    assert profiles_naming_brand(
        candidates, "Example",
        declared={"Wikipedia": "https://en.wikipedia.org/wiki/Pure_function"}) == candidates


def test_a_handle_nobody_can_check_is_taken_at_face_value():
    """A handle is a name somebody chose, so a mismatch proves nothing.

    Applying the name test to every platform made a worse mistake than the one
    it fixed: on a language's own site it dropped the foundation's LinkedIn
    page, its X account and its conference YouTube channel - all real, all the
    project's own - because none of the handles spells the site's name, and
    turned "links to only 4 off-site profiles" into "links to no off-site
    profile at all" at high severity.
    """
    candidates = {"LinkedIn": "https://www.linkedin.com/company/example-foundation/",
                  "X": "https://twitter.com/TheEF",
                  "YouTube": "https://www.youtube.com/user/exconf08"}
    assert profiles_naming_brand(candidates, "Example.org", None,
                                 ["example"]) == candidates


def test_a_brand_named_after_its_domain_is_matched_without_the_tld():
    from audit_common import brand_key
    assert brand_key("Example.org") == "example"


# --------------------------------------------------------------------------
# A number the words disown is not a postcode
# --------------------------------------------------------------------------

from page_extract import _images, _postcode_hint, _street_hint  # noqa: E402


@pytest.mark.parametrize("html,expected", [
    # A bank's footer. Every regulator requires a number printed there, which
    # is why shape alone read one as the postcode and published it inside
    # paste-ready PostalAddress markup.
    ("<footer><p>Authorised and regulated by the Financial Conduct Authority. "
     "Financial Services Register number 730427.</p></footer>", None),
    ("<footer><p>Company registration number 084213.</p></footer>", None),
    ("<footer><p>VAT no. 123456</p></footer>", None),
    ("<footer><p>Charity no. 1094095</p></footer>", None),
    ("<footer><p>Item ID 374819</p></footer>", None),
    # A label that says postcode settles it the other way.
    ("<footer><p>Postcode 110001</p></footer>", "110001"),
    ("<footer><address>41 Walmgate, York YO1 9TT</address></footer>", "YO1 9TT"),
])
def test_a_number_labelled_as_something_else_is_not_a_postcode(html, expected):
    soup = make_soup(html)
    text = soup.get_text(" ")
    match = _postcode_hint(text, soup, _street_hint(text))
    assert (match.group(0).strip() if match else None) == expected


def test_a_real_postcode_beside_a_registration_number_still_reads():
    html = ("<footer><p>Company registration number 084213. Registered office: "
            "41 Walmgate, York YO1 9TT.</p></footer>")
    soup = make_soup(html)
    text = soup.get_text(" ")
    match = _postcode_hint(text, soup, _street_hint(text))
    assert match is not None and match.group(0).strip() == "YO1 9TT"


# --------------------------------------------------------------------------
# A beacon is not an image
# --------------------------------------------------------------------------

def test_a_tracking_pixel_is_not_an_image_missing_alt_text():
    """A report said "2 images have no alt attribute" and advised "write alt
    text that states what the image shows" and "prioritise product photos".
    Both were 1x1 beacons with display:none."""
    html = ('<img src="/beacon.gif" width="1" height="1" style="display:none">'
            '<img src="/fb.gif" width="1" height="1">'
            '<img src="/pizza.jpg" width="1200" height="800">')
    images = _images(make_soup(html), "https://x.test/")
    assert images["missing_alt_count"] == 1
    assert images["missing_alt_sample"] == ["https://x.test/pizza.jpg"]


def test_an_image_that_declares_no_size_is_still_an_image():
    """`_int_attr` returns 0 for a missing attribute, so reading the parsed
    values alone made every image with no width or height a beacon."""
    images = _images(
        make_soup('<img src="/a.png"><img src="/b.png" alt="A bowl">'),
        "https://x.test/")
    assert images["missing_alt_count"] == 1


# --------------------------------------------------------------------------
# A staging copy is not the site
# --------------------------------------------------------------------------

from robots_parser import benign_disallow  # noqa: E402


@pytest.mark.parametrize("rule", [
    "/club-preprod", "/digital-preprod", "/magazine-dev", "/magazine-test",
    "/staging", "/uat/", "/qa",
])
def test_a_disallowed_staging_copy_is_not_blocked_content(rule):
    """All eleven "paths that look like real content" in one broadcaster's
    report were -preprod, -dev or -test. Keeping a staging copy out of an index
    is the reason robots.txt exists, and opening it up would put unfinished
    pages into answers about the brand."""
    assert benign_disallow(rule)


@pytest.mark.parametrize("rule", [
    "/development-services", "/testing", "/legacy-giving", "/book-a-demo",
    "/mirrors", "/developers", "/archive", "/guides",
])
def test_a_word_with_a_second_meaning_is_not_an_environment(rule):
    """A word that also names a real section turns content into plumbing, which
    is the more expensive mistake - so development, testing, legacy, demo and
    mirror are deliberately left out."""
    assert not benign_disallow(rule)


# --------------------------------------------------------------------------
# An index of articles is not an article
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/news/previous/", "category"),
    ("/news/archive", "category"),
    ("/blog/all", "category"),
    ("/blog/previously-unseen-photographs", "article"),
    ("/news/my-archive-of-photos", "article"),
])
def test_an_archive_listing_is_not_asked_for_article_markup(path, expected):
    """`/news/previous/` is a news archive. Demanding Article markup on it
    produced a snippet whose headline was "News archive" - telling a machine
    the archive page is a piece of journalism published on a date."""
    from audit_common import detect_page_type
    assert detect_page_type("https://x.test" + path, {
        "text": "", "headings": [], "jsonld_types": [], "title": "",
        "lang": ""}) == expected


# --------------------------------------------------------------------------
# A localised homepage is a homepage
# --------------------------------------------------------------------------

@pytest.mark.parametrize("url,lang,expected", [
    ("https://x.test/", "", "home"),
    ("https://x.test/de/", "de", "home"),
    ("https://x.test/en-gb/", "en-GB", "home"),
    # Not on shape alone: plenty of English sites keep an IT department here.
    ("https://x.test/it", "en", "other"),
    ("https://x.test/de/about", "de", "about"),
])
def test_a_language_prefixed_root_is_home_when_the_page_says_so(url, lang, expected):
    """Only the bare root qualified, so on a path-prefixed site the audit's
    idea of "the homepage" snapped to whatever sat at the root - on a bilingual
    broadcaster, an English redirect target - even when the audit had been
    pointed at the localised entry point explicitly."""
    from audit_common import detect_page_type
    assert detect_page_type(url, {
        "text": "", "headings": [], "jsonld_types": [], "title": "",
        "lang": lang}) == expected


# --------------------------------------------------------------------------
# An English threshold measures English
# --------------------------------------------------------------------------

from audit_common import other_language_pages, pages_in_prose_language  # noqa: E402

MIXED = [
    {"url": "https://x.test/en/a", "lang": "en"},
    {"url": "https://x.test/de/a", "lang": "de"},
    {"url": "https://x.test/fr/a", "lang": "fr-FR"},
    {"url": "https://x.test/plain", "lang": ""},
]


def test_a_page_declaring_another_language_is_not_measured_in_english():
    """Site language is one majority vote. On a bilingual broadcaster it was
    decided by luck - 28 pages tagged en against 18 tagged de - and the English
    30-word sentence threshold then ran over the German pages too. One scored
    59% long sentences and escaped a finding only because its page type was not
    in the allowlist."""
    kept = [p["url"] for p in pages_in_prose_language(MIXED)]
    assert kept == ["https://x.test/en/a", "https://x.test/plain"]


def test_a_page_declaring_nothing_inherits_the_site_verdict():
    """The common case on smaller sites, and the best answer available."""
    assert {"url": "https://x.test/plain", "lang": ""} in pages_in_prose_language(MIXED)


def test_the_pages_set_aside_can_be_counted_and_reported():
    assert len(other_language_pages(MIXED)) == 2


# --------------------------------------------------------------------------
# A price stated in a nested object is still stated
# --------------------------------------------------------------------------

SD = _module(os.path.join(ROOT, "skills", "structured-data-audit",
                          "scripts", "check.py"), "sd_for_round5")


def test_a_price_inside_a_price_specification_is_not_missing():
    """A restaurant's shop states every price as
    offers[0].priceSpecification[0].price, which is valid, is what its platform
    emits, and carries more than a flat price does. Reading only offer["price"]
    reported eighteen pages of correct markup as offers "missing price and
    priceCurrency", and the fix would have had the owner flatten working
    data."""
    offer = {"@type": "Offer",
             "priceSpecification": [{"@type": "UnitPriceSpecification",
                                     "price": "30.00", "priceCurrency": "GBP"}]}
    missing = SD._missing_props(offer, SD._offer_props(offer))
    assert "price" not in missing and "priceCurrency" not in missing


def test_an_offer_stating_no_price_anywhere_is_still_reported():
    offer = {"@type": "Offer", "priceCurrency": "GBP"}
    assert "price" in SD._missing_props(offer, SD._offer_props(offer))


# --------------------------------------------------------------------------
# A whole-brand claim comes off a whole-brand page
# --------------------------------------------------------------------------

def test_a_brand_definition_is_never_read_off_a_product_page():
    """The paste-ready LocalBusiness block described a restaurant as "not only
    bold in colour but also in versatility - this bag has it all for your every
    day storage needs". The homepage states no definition, which the same
    report said correctly in a separate finding, so the search fell through in
    URL order to a product page where the sentence matched on the word "is".
    """
    pages = [
        {"url": "https://x.test/", "page_type": "home",
         "paragraphs": ["Open Tuesday to Saturday from six."]},
        {"url": "https://x.test/product/tote", "page_type": "product",
         "paragraphs": ["Example's blue tote is not only bold in colour but also "
                        "in versatility - it has it all for your storage needs."]},
    ]
    assert SD._brand_definition(pages, {"name": "Example"}) == ""


def test_a_definition_on_the_about_page_is_still_used():
    pages = [
        {"url": "https://x.test/", "page_type": "home", "paragraphs": ["Welcome."]},
        {"url": "https://x.test/about", "page_type": "about",
         "paragraphs": ["Example is a neighbourhood wine bar and kitchen in east "
                        "London."]},
    ]
    assert "wine bar" in SD._brand_definition(pages, {"name": "Example"})


def test_a_site_description_is_never_read_off_a_product_page():
    pages = [
        {"url": "https://x.test/", "page_type": "home", "meta_description": ""},
        {"url": "https://x.test/product/tote", "page_type": "product",
         "meta_description": "A bold blue tote for every day."},
    ]
    assert SD._site_description(pages) == ""


# --------------------------------------------------------------------------
# A page with its own address describes a place
# --------------------------------------------------------------------------

from audit_common import is_multi_location, pages_with_their_own_address  # noqa: E402


def _branch(i, street, postcode):
    return {"url": "https://x.test/restaurants/branch{}".format(i), "status": 200,
            "page_type": "other", "jsonld_types": [],
            "contact_facts": {"street_hint": street, "postcode_hint": postcode,
                              "declared_phones": ["+441000000{}".format(i)]}}


CHAIN = {"origin": "https://x.test", "site": "x.test", "brand": {"name": "Example"},
         "pages": [_branch(1, "51 Berwick Street", "W1F 8SJ"),
                   _branch(2, "12 Park Row", "LS1 5HD"),
                   _branch(3, "9 Union Street", "BS1 5EF"),
                   _branch(4, "9 Union Street", "BS1 5EF")]}


def test_a_chain_is_recognised_from_its_addresses_not_its_url_slugs():
    """A pizza chain kept its branches at /restaurants/<slug>, printed each
    address in plain text, and declared no place markup at all - so the slug
    signal missed it and the markup signal could not fire, because the markup
    that would have identified the chain is the markup the chain is missing.
    The bug and the defect were the same fact.
    """
    assert is_multi_location(CHAIN)
    assert len(pages_with_their_own_address(CHAIN)) == 3   # the duplicate folds


def test_one_office_does_not_make_a_chain():
    single = {"origin": "https://x.test", "site": "x.test",
              "pages": [_branch(1, "41 Walmgate", "YO1 9TT")]}
    assert not is_multi_location(single)
    assert len(pages_with_their_own_address(single)) == 1


def test_branch_pages_with_no_place_markup_are_reported():
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, CHAIN, CHAIN["pages"])
    assert [f["root_cause"] for f in result.findings] == ["no-org-schema"]
    finding = result.findings[0]
    assert "LocalBusiness" in finding["suggested_action"]["summary"]
    assert "51 Berwick Street" in finding["suggested_action"]["snippet"]


def test_branch_pages_that_already_declare_a_place_are_left_alone():
    marked = {"origin": "https://x.test", "site": "x.test", "pages": []}
    for page in CHAIN["pages"]:
        copy = dict(page)
        copy["jsonld_types"] = ["Restaurant"]
        marked["pages"].append(copy)
    result = SkillResult("structured-data-audit")
    SD._check_per_location_markup(result, marked, marked["pages"])
    assert not result.findings
    assert result.not_applicable


# --------------------------------------------------------------------------
# Whole months, never rounded up
# --------------------------------------------------------------------------

FR = _module(os.path.join(ROOT, "skills", "freshness-corroboration-audit",
                          "scripts", "check.py"), "fr_for_round5")


def test_a_gap_of_twenty_two_point_two_months_reads_as_twenty_two():
    """"23 months" is a number the reader can check against a visible date and
    find wrong."""
    import datetime
    assert FR.months_between(datetime.date(2024, 10, 20),
                             datetime.date(2026, 9, 3)) == 22


def test_a_full_month_boundary_still_counts():
    import datetime
    assert FR.months_between(datetime.date(2024, 10, 1),
                             datetime.date(2026, 9, 3)) == 23


# --------------------------------------------------------------------------
# Both of this file's own fixes, caught on a site nobody had run
# --------------------------------------------------------------------------

def test_a_menu_that_is_also_in_a_dropdown_is_not_reported_as_hidden():
    """The same link usually appears in both a visible bar and a hidden
    dropdown of the same menu. Comparing hidden links against the deduplicated
    total then said "every navigation link is hidden" about a language's own
    site, whose main bar is plainly visible - the false positive this finding
    exists to avoid making in reverse."""
    from page_extract import _links
    visible = "".join('<a href="/p{0}">Page {0}</a>'.format(i) for i in range(6))
    html = ("<html><body><nav>" + visible + "</nav>"
            '<div role="menu" aria-hidden="true">' + visible + "</div>"
            "<main><p>hi</p></main></body></html>")
    links = _links(make_soup(html), "https://x.test/", "https://x.test")
    assert links["nav_visible_count"] == 6
    assert links["nav_hidden_count"] == 0


def test_a_menu_that_exists_only_inside_a_hidden_panel_is_still_reported():
    from page_extract import _links
    html = ('<html><body><div role="dialog" aria-hidden="true"><div role="menu">'
            + "".join('<a href="/p{0}">Page {0}</a>'.format(i) for i in range(30))
            + "</div></div><main><p>hi</p></main></body></html>")
    links = _links(make_soup(html), "https://x.test/", "https://x.test")
    assert links["nav_visible_count"] == 0
    assert links["nav_hidden_count"] == 30


def test_the_same_address_written_three_ways_is_one_place():
    """The street hint is a fuzzy substring match, so one head-office address
    came out three slightly different ways across three pages of a
    single-location fixture and was counted as three branches. A postal code
    identifies a place; the street only describes it."""
    pages = [{"url": "https://x.test/{}".format(i), "status": 200,
              "contact_facts": {"street_hint": street, "postcode_hint": "97204"}}
             for i, street in enumerate(("418 Harbour Street",
                                         "418 Harbour Street, Portland",
                                         "418 Harbour Street Portland OR"))]
    snapshot = {"origin": "https://x.test", "site": "x.test", "pages": pages}
    assert len(pages_with_their_own_address(snapshot)) == 1
    assert not is_multi_location(snapshot)


# --------------------------------------------------------------------------
# A URL and its own declared canonical are one page
# --------------------------------------------------------------------------

from audit_common import fold_declared_duplicates  # noqa: E402


def test_a_url_and_its_declared_canonical_are_counted_once():
    """Two of eleven "duplicate title" pairs on a broadcaster were a URL and
    its own canonical target. The site had already said they are one page, and
    counting both inflated the finding and the denominator underneath it."""
    pages = [{"url": "https://x.test/a", "canonical": "https://x.test/a"},
             {"url": "https://x.test/a/", "canonical": "https://x.test/a"},
             {"url": "https://x.test/b", "canonical": ""}]
    assert [p["url"] for p in fold_declared_duplicates(pages)] == [
        "https://x.test/a", "https://x.test/b"]


def test_a_canonical_the_crawl_never_reached_folds_nothing():
    """Folding on it would hide a page rather than count it, and a canonical
    pointing somewhere unreached is a separate finding of its own."""
    pages = [{"url": "https://x.test/c", "canonical": "https://x.test/elsewhere"}]
    assert len(fold_declared_duplicates(pages)) == 1


# --------------------------------------------------------------------------
# A name written in one script cannot be looked for in another
# --------------------------------------------------------------------------

from audit_common import written_in_the_same_script  # noqa: E402


@pytest.mark.parametrize("name,text,expected", [
    # A foundation's Arabic edition was told at high severity that its
    # structured data contradicted the page, about markup that was correct and
    # a page that was correct - on a comparison that could only have one
    # answer.
    ("Example Foundation", "\u0645\u0624\u0633\u0633\u0629 \u0648\u064a\u0643\u064a", False),
    ("Example Foundation", "The Example Foundation is a nonprofit", True),
    # No letters shared and none to compare: nothing can be decided, so the
    # check runs as before rather than declining on a technicality.
    ("Example Foundation", "A page with no mention of it at all", True),
    ("\u30c8\u30e8\u30bf", "\u30c8\u30e8\u30bf\u306f\u4f1a\u793e\u3067\u3059", True),
    ("Toyoda", "\u30c8\u30e8\u30bf\u306f\u4f1a\u793e\u3067\u3059", False),
])
def test_a_name_is_only_looked_for_where_it_could_appear(name, text, expected):
    assert written_in_the_same_script(name, text) is expected


# --------------------------------------------------------------------------
# Every check that ran is in exactly one place in the appendix
# --------------------------------------------------------------------------

def test_no_check_that_ran_is_accounted_for_nowhere(audit):
    """The README promises that every check which stays quiet says why.

    A check registered alongside the one a finding names was marked as having
    fired - correctly keeping it out of the passed list - and then appeared
    nowhere at all: not a finding, not a decline, not a pass. Four of
    seventy-three on one real site, and among them, on another, was whether
    robots.txt blocks AI crawlers.
    """
    for name in ("good-site", "blocked-site", "dead-end-site", "no-schema-site",
                 "stale-site", "js-shell-site"):
        report = audit(name).report
        ran = {(c["skill"], c["check"]) for c in report.get("checks_run") or []}
        passed = {(c["skill"], c["check"]) for c in report.get("checks_passed") or []}
        declined = {(c.get("skill"), c["check"])
                    for c in report.get("not_applicable") or []}
        contributed = {(c["skill"], c["check"])
                       for c in report.get("checks_that_found_something") or []}
        named = {f.get("check") for f in report["findings"] if f.get("check")}
        stranded = [c for c in ran
                    if c not in passed and c not in declined
                    and c not in contributed and c[1] not in named]
        assert not stranded, "{}: {} check(s) appear nowhere: {}".format(
            name, len(stranded), sorted(stranded))


def test_a_check_appears_in_only_one_place(audit):
    """Four buckets, no overlaps: a reader counting them should reach the
    number of checks that ran."""
    report = audit("blocked-site").report
    passed = {(c["skill"], c["check"]) for c in report.get("checks_passed") or []}
    declined = {(c.get("skill"), c["check"])
                for c in report.get("not_applicable") or []}
    contributed = {(c["skill"], c["check"])
                   for c in report.get("checks_that_found_something") or []}
    assert not passed & declined
    assert not passed & contributed
    assert not declined & contributed


# --------------------------------------------------------------------------
# noindex on a page that should not be indexed is correct
# --------------------------------------------------------------------------

from audit_common import is_search_result_page  # noqa: E402


@pytest.mark.parametrize("url", [
    "https://x.test/?search=Artemis",
    "https://x.test/search?q=mars",
    "https://x.test/?s=hello",
    "https://x.test/?query=abc",
    "https://x.test/suche?q=x",
])
def test_a_search_result_page_is_recognised(url):
    """A space agency's report ranked "remove noindex from these pages" second
    in what to fix first. All five were search-result URLs, where `noindex` is
    what every search-engine guideline asks for, and following the advice would
    have published five URLs of somebody else's search history."""
    assert is_search_result_page(url)


@pytest.mark.parametrize("url", [
    "https://x.test/blog/research",
    "https://x.test/products/searchlight",
    "https://x.test/?page=2",
    "https://x.test/research/soil-science",
])
def test_an_ordinary_page_is_not_a_search_result(url):
    assert not is_search_result_page(url)


# ==========================================================================
# Round 6: four sites, on the zip built from the round-5 fixes
#
# A gym chain, a sportswear retailer behind a bot manager, a humanitarian
# organisation serving six languages under path prefixes, and an Arabic-script
# news site. 42 findings checked, 18% wrong or misleading.
#
# Rules 1 to 6 all held where exercised, and the crawler-role table held on the
# one site whose robots.txt named AI crawlers - it blocks five training
# crawlers and two answer crawlers, and the report filed each correctly. What
# follows is what was left.
# ==========================================================================


# --------------------------------------------------------------------------
# A page we failed to read is not a page with nothing on it
# --------------------------------------------------------------------------

RR = _module(os.path.join(ROOT, "skills", "render-readability-audit",
                          "scripts", "check.py"), "rr_for_round6")

from audit_common import extraction_looks_incomplete  # noqa: E402


def test_a_page_whose_text_we_could_not_extract_is_not_called_a_shell():
    """A news site's liveblog wraps server-rendered Arabic prose in a nested
    div none of the content selectors reach. The extractor kept 89 of the
    page's 1,660 visible characters, and two findings followed - "delivered as
    an empty JavaScript shell" and "too little text to quote" - about a page
    that is complete and server-rendered. The fix offered was several days of
    development work.
    """
    page = {"url": "https://x.test/live", "text_len": 1660, "body_text_len": 89,
            "spa_shell": {}, "scripts": {"inline_bytes": 0, "external_count": 9}}
    assert extraction_looks_incomplete(page)
    assert RR._shell_reasons(page) == []


def test_a_genuine_shell_is_still_reported():
    """A framework root with nothing in it, or a state blob holding the
    content, is the site telling us the text arrives later. That evidence
    survives the guard."""
    page = {"url": "https://x.test/", "text_len": 120, "body_text_len": 40,
            "spa_shell": {"root_selector": "#__next", "root_text_len": 0,
                          "state_blobs": ["__NEXT_DATA__"], "state_blob_bytes": 90000},
            "scripts": {"inline_bytes": 90000, "external_count": 4}}
    assert RR._shell_reasons(page)


def test_a_page_that_really_is_empty_is_still_reported():
    """Nothing to compare means nothing failed: an empty page is a finding."""
    page = {"url": "https://x.test/", "text_len": 60, "body_text_len": 60,
            "spa_shell": {"root_selector": "#app", "root_text_len": 0},
            "scripts": {"inline_bytes": 0, "external_count": 8}}
    assert not extraction_looks_incomplete(page)
    assert RR._shell_reasons(page)


@pytest.mark.parametrize("page,expected", [
    ({"text_len": 1660, "body_text_len": 89}, True),
    ({"text_len": 1660, "body_text_len": 1400}, False),
    ({"text_len": 120, "body_text_len": 20}, False),     # too short to judge
    ({"text_len": 0, "body_text_len": 0}, False),
])
def test_the_extraction_shortfall_is_measured_against_the_whole_page(page, expected):
    assert extraction_looks_incomplete(page) is expected


# --------------------------------------------------------------------------
# One branch's address is not the company's address
# --------------------------------------------------------------------------

CHAIN_PAGES = [
    {"url": "https://x.test/", "jsonld": [{"@type": "Organization", "name": "The Group"}]},
    {"url": "https://x.test/find-a-gym/wimbledon/", "jsonld": [
        {"@type": "ExerciseGym", "name": "Wimbledon",
         "address": {"@type": "PostalAddress", "streetAddress": "122 The Broadway",
                     "postalCode": "SW19 1RH"}}]},
]


def test_a_chains_brand_snippet_takes_no_address_from_a_branch_page():
    """A gym chain's report offered its homepage a paste-ready Organization
    block carrying the Wimbledon branch's own address, lifted from the single
    branch page the crawl happened to reach. The registered office is four
    miles and one postcode away, printed on the site's own privacy page.

    The telephone reader already refused to do this - "a bakery with three
    shops was offered the Cherche-Midi shop's line as the telephone for its
    whole company record" - and the guard was never extended to the address.
    """
    assert SD._declared_address(CHAIN_PAGES, whole_brand_only=True) == {}


def test_a_chains_brand_snippet_uses_the_address_on_its_organization_node():
    pages = [{"url": "https://x.test/", "jsonld": [
        {"@type": "Organization", "name": "The Group",
         "address": {"@type": "PostalAddress", "streetAddress": "7 St Johns Road",
                     "postalCode": "SW11 1QN"}}]}]
    assert SD._declared_address(pages, whole_brand_only=True)["postal_code"] == "SW11 1QN"


def test_a_single_location_business_still_uses_its_own_address():
    """Its one place's address really is the company's, and that is the common
    case - so the guard applies only where there are branches."""
    pages = [{"url": "https://x.test/", "jsonld": [
        {"@type": "LocalBusiness", "name": "One Shop",
         "address": {"@type": "PostalAddress", "streetAddress": "41 Walmgate",
                     "postalCode": "YO1 9TT"}}]}]
    assert SD._declared_address(pages, whole_brand_only=False)["postal_code"] == "YO1 9TT"


@pytest.mark.parametrize("declared,expected", [
    ({"@type": "Organization"}, True),
    ({"@type": "NGO"}, True),
    ({"@type": ["Organization", "NewsMediaOrganization"]}, True),
    ({"@type": "LocalBusiness"}, False),
    ({"@type": "Restaurant"}, False),
    ({"@type": ["Organization", "Restaurant"]}, False),
    ({"@type": "Product"}, False),
])
def test_which_nodes_speak_for_the_whole_business(declared, expected):
    assert SD._speaks_for_the_whole_brand(declared) is expected


# --------------------------------------------------------------------------
# A feed is not a page
# --------------------------------------------------------------------------

def test_an_rss_feed_is_not_crawled_as_a_page():
    """A news site's RSS feed was crawled as a page. It has no `<h1>` and no
    `<html lang>` because it has no `<html>` element at all, so the report told
    the newsroom to add both to a document where neither exists.

    XML used to be let through wholesale because sitemaps are XML - but the
    sitemap step fetches those itself, so nothing is lost.
    """
    import crawl as crawl_module
    source = io.open(os.path.join(SCRIPTS, "crawl.py"), encoding="utf-8").read()
    assert 'if content_type and "html" not in content_type:' in source
    assert 'and "xml" not in content_type' not in source
    assert crawl_module is not None


@pytest.mark.parametrize("content_type,is_a_page", [
    ("text/html", True),
    ("application/xhtml+xml", True),
    ("application/rss+xml", False),
    ("application/atom+xml", False),
    ("application/xml", False),
    ("application/json", False),
    ("application/pdf", False),
])
def test_only_html_content_types_are_read_as_pages(content_type, is_a_page):
    assert ("html" in content_type) is is_a_page


# --------------------------------------------------------------------------
# A three-way split is not a two-way one
# --------------------------------------------------------------------------

def test_the_chrome_sentence_names_all_three_buckets():
    """"The site's usual header/footer links appear on 9 of 32 pages" reads as
    "23 pages are broken" when 7 are. The sixteen in between share enough of
    the menu to be fine and were counted in neither number, so a reader doing
    the subtraction was wrong by more than three times."""
    source = io.open(os.path.join(ROOT, "skills", "engagement-audit", "scripts",
                                  "check.py"), encoding="utf-8").read()
    assert "share enough of it" in source
    assert "The site's usual header/footer links appear on {} of {} pages" not in source


# --------------------------------------------------------------------------
# A health endpoint is not content
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rule,benign", [
    ("/healthcheck.html", True),
    ("/api/commerce/healthcheck/", True),
    ("/healthz", True),
    # A bare `health` or `status` would swallow these, and on an insurance site
    # or a gallery they are the content.
    ("/healthcare", False),
    ("/health-and-safety", False),
    ("/statuses-of-liberty", False),
])
def test_a_health_endpoint_is_excluded_and_a_health_section_is_not(rule, benign):
    """One report excluded `/api/commerce/healthcheck/` under the machine-file
    rule and then listed `/healthcheck.html` as a path that looks like real
    content, in the same finding."""
    assert benign_disallow(rule) is benign


# --------------------------------------------------------------------------
# robots.txt is read from the host that answers
# --------------------------------------------------------------------------

def test_the_crawl_settles_on_the_host_the_homepage_redirects_to():
    """A retailer redirects `/` from the bare domain to `www`, and answers 404
    for `/robots.txt` there. robots.txt and the sitemap were both fetched from
    the host that has neither, and the report's top finding was "No XML sitemap
    is available" about a site with a valid sitemap index, referenced from a
    valid robots.txt, one hop away.
    """
    import crawl as crawl_module

    class _Response(object):
        url = "https://www.x.test/"

    class _Fetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return _Response()

    notes = []
    origin, seed = crawl_module._resolve_host(
        _Fetcher(), "https://x.test", "https://x.test/", notes)
    assert origin == "https://www.x.test"
    assert seed == "https://www.x.test/"
    assert notes and "redirects to" in notes[0]


def test_the_crawl_never_follows_a_redirect_onto_another_site():
    """Auditing whatever the homepage redirects to, without that check, would
    report somebody else's site under this one's name."""
    import crawl as crawl_module

    class _Response(object):
        url = "https://somewhere-else.test/"

    class _Fetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return _Response()

    notes = []
    origin, seed = crawl_module._resolve_host(
        _Fetcher(), "https://x.test", "https://x.test/", notes)
    assert origin == "https://x.test"
    assert notes == []


def test_an_unreachable_homepage_leaves_the_host_alone():
    import crawl as crawl_module

    class _Fetcher(object):
        def try_get(self, url, method=None, **kwargs):
            return None

    notes = []
    origin, seed = crawl_module._resolve_host(
        _Fetcher(), "https://x.test", "https://x.test/", notes)
    assert origin == "https://x.test" and notes == []


# --------------------------------------------------------------------------
# The spacing this audit introduced comes back out
# --------------------------------------------------------------------------

from audit_common import tidy_spacing  # noqa: E402


def test_a_sentence_broken_by_inline_markup_is_rejoined_cleanly():
    """Joining inline elements with a space is what makes the extracted text
    readable, and it also puts a space in front of every punctuation mark that
    sat outside a `<b>` or an `<a>`. A database project's own tagline came out
    as "a small , fast , self-contained , high-reliability , full-featured ,
    SQL database engine" - and that string went into a paste-ready
    `Organization` description, so an owner following the advice would publish
    their own tagline with six stray spaces in it.
    """
    html = ("<p>It is a <b>small</b>, <b>fast</b>, <b>self-contained</b>, "
            "SQL database engine.</p>")
    assert main_text(make_soup(html)) == (
        "It is a small, fast, self-contained, SQL database engine.")


@pytest.mark.parametrize("before,after", [
    ("a small , fast , engine .", "a small, fast, engine."),
    ("see ( the docs ) for 40 % more", "see (the docs) for 40% more"),
    ("Really ? Yes ; truly !", "Really? Yes; truly!"),
])
def test_only_the_spacing_changes_never_the_words(before, after):
    assert tidy_spacing(before) == after


def test_a_hyphen_between_words_is_left_alone():
    """"Mon - Fri" is how the site wrote it, and it is not punctuation this
    audit introduced."""
    assert tidy_spacing("Open Mon - Fri. Call now.") == "Open Mon - Fri. Call now."


def test_a_scale_written_in_ordinary_english_is_not_restated():
    """"Both forms were found on 54 of the 60 pages crawled. Seen on 54 of the
    60 pages this crawl read." The finding had already said it; `the` between
    the numbers was all it took for the rule not to notice."""
    finding = {"evidence": "Both forms were found on 54 of the 60 pages crawled.",
               "affected_pages": ["https://x.test/a"], "affected_page_count": 54}
    assert COMPOSE.state_the_denominator(finding, 60) == finding["evidence"]
