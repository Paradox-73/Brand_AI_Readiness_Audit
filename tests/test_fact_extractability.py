# -*- coding: utf-8 -*-
"""Whether the page states the fact, and whether the fact is this site's.

A core fact recorded as PRESENT suppresses the finding that would otherwise be
raised about it. So a fact found in the wrong place does not merely print a
false line - it hides a real gap underneath one. Both halves of that have
happened, repeatedly, on sites checked by hand against the live site rather
than against the report:

  "The Association ... was established     printed as a charity's own founding
  in June of 1997"                         fact, off its partners page, about an
                                           organisation seven years older than
                                           the charity
  "Funds raised US$ 1,115,503,258"         printed as that charity's pricing
  a partner's email in body copy           recorded as the audited site's way of
                                           getting in touch
  "we are unable to provide locator        printed as the places the
  services for members still serving"      organisation serves - it matched on
                                           the word "serving"
  "feel free to use those examples"        printed as a documentation site
                                           stating that it costs nothing
  "powers 70+ million accounts             printed as a project's service area;
  worldwide"                               it was a sponsor's blurb about a
                                           different company

And the mirror image, a fact the page states and the report called absent:

  "I am a Professor in the Department      "no page states in one sentence what
  of Statistics at ..."                    the brand is"
  "It is <Person>'s mail server"           "no named team or leadership detail
                                           appears in the page text"
  "Django - The web framework for          the same, on a tagline introduced by
  perfectionists with deadlines."          a dash rather than the verb "to be"
  `<h1 style="display:none">`              "this page carries no H1"

The rule the whole file defends: the words the report prints have to contain
the fact, and the sentence they come from has to be the site speaking about
itself.
"""

from __future__ import annotations

import importlib.util
import os
import re

from conftest import ROOT

from audit_common import SkillResult, sentence_with


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit", "facts_for_extractability_tests")

HOST = "https://an-invented-host.test"


def _page(url, **fields):
    page = {
        "url": url,
        "page_type": "other",
        "status": 200,
        "body_text": "",
        "headings": {},
        "headings_in_markup": {"h1": ["a heading"]},
        "jsonld": [],
        "jsonld_types": [],
        "links": {},
        "sections": [],
        "prices": [],
        "contact_facts": {},
        "readability": {},
        "word_count": 0,
        "meta_description": "",
    }
    page.update(fields)
    return page


# --------------------------------------------------------------------------
# A claim that something is present carries the words that make it present
# --------------------------------------------------------------------------

def test_a_present_verdict_produces_the_sentence_it_rests_on():
    """A menu label is not the site stating a fact, so a match too short to be
    a sentence produces nothing to quote."""
    assert sentence_with("Pricing", re.compile("Pricing")) is None
    text = "We have been based in Sheffield since the studio opened in 2011."
    quote = sentence_with(text, FACTS.SERVICE_AREA_RE)
    assert quote and "Sheffield" in quote


def test_feel_free_to_use_is_not_a_statement_that_something_is_free():
    """A documentation site's report said it states that it costs nothing.

    The words were "feel free to use those", in a paragraph about reusing
    example code. That false pass suppressed the honest finding.
    """
    assert not FACTS.COSTS_NOTHING_RE.search("feel free to use those examples")
    assert FACTS.COSTS_NOTHING_RE.search("Django is free to use and open source")


def test_a_bare_adverb_is_not_a_statement_of_where_a_business_operates():
    """"powers 70+ million accounts worldwide" was a sponsor's blurb about a
    different company, printed as the project's own service area."""
    assert not FACTS.SERVICE_AREA_RE.search("powers 70+ million accounts worldwide")
    assert not FACTS.SERVICE_AREA_RE.search("it improved things across the board")
    assert FACTS.SERVICE_AREA_RE.search("We deliver nationwide from three depots")
    assert FACTS.SERVICE_AREA_RE.search("The studio is based in Leeds")


def test_the_words_the_report_prints_have_to_contain_the_fact():
    """Three "checks that passed", one assumption: a pattern matching somewhere
    on a page treated as the site stating the fact. A fact recorded as present
    suppresses the finding underneath it, so each of these hid a real gap as
    well as printing a false line."""
    # 1. An event page arrives as one unpunctuated run of filter labels, so the
    #    whole page is a single "sentence" and the figure that matched sits
    #    past the 220-character cut. The report printed the labels as the
    #    site's stated pricing, with no price anywhere in them.
    blob = ("Find More Events Explore Any Dates This Weekend This Week This Month Next 7 "
            "Days Next 30 Days Next 60 Days Troybeat Fri Nov 13 Harbour Hall Doors 7pm All "
            "Ages Standing Only Buy Tickets Now Presented By Invented Live Sold Out "
            "Waitlist Open Second Show Added Tickets from $45")
    quote = FACTS.sentence_with(blob, re.compile(re.escape("$45")))
    assert quote and "$45" not in quote, "the fixture must reproduce the truncation"
    assert FACTS._quote_shows_a_price(quote) is False
    assert FACTS._quote_shows_a_price("Plans start at $45 per month.") is True

    # 2. A statement of what the organisation will not do, recorded as the
    #    places it serves. It matched on the word "serving".
    refusal = ("Therefore, we are unable to provide locator services for members still "
               "serving on active duty.")
    assert FACTS._own_sentence(refusal, FACTS.SERVICE_AREA_RE, "Invented",
                               states_the_fact=FACTS._states_a_service_area) is None
    real = "We are based in Ludlow and serve restoration builders across the UK."
    assert FACTS._own_sentence(real, FACTS.SERVICE_AREA_RE, "Invented",
                               states_the_fact=FACTS._states_a_service_area) == real


def test_a_match_that_does_not_survive_truncation_is_not_evidence():
    """The general half of the same rule, held on its own. `_own_sentence` is
    what every fact but the price goes through, and it now matches twice: once
    to find the sentence, once against the words the report will print."""
    padding = "Terms and conditions apply to every order placed through this site. " * 4
    text = padding + "The company was founded in 1994 in Ludlow."
    # One sentence, longer than the evidence cut, with the fact past the end.
    run_on = text.replace(". ", " ")
    assert FACTS._own_sentence(run_on, FACTS.FOUNDING_FACT_RE, "Invented") is None
    assert FACTS._own_sentence("The company was founded in 1994 in Ludlow.",
                               FACTS.FOUNDING_FACT_RE, "Invented") is not None


# --------------------------------------------------------------------------
# A fact found anywhere on the site attributed to the site's owner
# --------------------------------------------------------------------------

def test_a_partners_founding_date_is_not_the_sites_own_founding_fact():
    """A charity's report marked the core-facts check passed and printed, as
    the site's founding fact, a sentence off its partners page about a partner
    organisation founded seven years before the charity existed. A core fact
    recorded as present suppresses the finding that would otherwise be raised,
    so a stranger's fact does not merely mislead - it hides a real gap.

    Two readings of the same page carried the same defect. In the prose, "The
    Association" read as self-reference; in the markup, a partner's
    `Organization` node and the `PostalAddress` hanging off it were read as
    this site's. Both are attributed now - the sentence by
    `speaks_about_itself`, the node by the name above it."""
    partner = ("The Association of Youths Against Malaria was established in June of "
               "1997 in the town of Bakau.")
    assert FACTS._own_sentence(partner, FACTS.FOUNDING_FACT_RE, "Nets For Life") is None
    assert FACTS._own_sentence("The charity was founded in 2004 by two volunteers.",
                               FACTS.FOUNDING_FACT_RE, "Nets For Life") is not None

    partner_markup = [
        {"@type": "Organization", "name": "The Association of Youths Against Malaria",
         "foundingDate": "1997"},
        {"@type": "PostalAddress", "streetAddress": "2 Bakau Road",
         "addressLocality": "Bakau", "_parent_name": "The Association of Youths "
         "Against Malaria"},
    ]
    partners_page = _page(HOST + "/partners.html", body_text=partner,
                          jsonld=partner_markup)
    assert FACTS._declared_origin(partners_page, "Nets For Life") == ""
    assert FACTS._declared_address(partners_page, "Nets For Life") == ""

    # The whole check, because the defect is a fact recorded as found.
    result = SkillResult("fact-extractability-audit")
    pages = [_page(HOST + "/", page_type="home", body_text="Ending malaria."),
             partners_page]
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Nets For Life")
    causes = {finding["root_cause"] for finding in result.findings}
    assert "missing-core-fact" in causes


def test_a_lifetime_fundraising_total_is_not_a_price():
    """The same report printed, as the site's pricing, "Funds raised US$
    1,115,503,258 Nets funded 509,754,338 People protected 917,557,808". A
    currency figure is a price only when it is what something costs, and this
    one is a tally printed beside an appeal for gifts."""
    tally = ("Funds raised US$ 1,115,503,258 Nets funded 509,754,338 People protected "
             "917,557,808. Donate today: a gift of US$ 2 buys a net.")
    assert FACTS.quotes_its_own_price(tally) is False
    assert FACTS.quotes_its_own_price(
        "Plans start at US$ 480 per month. Buy now.") is True


def test_a_contact_detail_in_the_chrome_is_preferred_to_one_in_the_body():
    """Same defect, third reading. A partners page prints a partner's email in
    its main content and the site's own in the footer, and the check took
    whichever page came first in the crawl - so a partner's address was
    recorded as the audited site's way of getting in touch, which marks the
    contact fact present and hides the finding underneath it.

    The header, footer and `<address>` element speak for the site, because the
    template puts them on every page. A detail in body copy is still a stated
    contact route and still marks the fact found - a site that prints its
    email only in a paragraph has printed its email - but the report has to
    say which of the two it read, because they are not the same claim."""
    def _home(chrome):
        # Every other core fact stated, so the check reaches the pass text
        # where the contact detail it chose is printed.
        return _page(HOST + "/", page_type="home", jsonld_types=["Offer"],
                     prices=["$40"],
                     body_text="Plans start at $40 per month. Buy now. We were founded "
                               "in 2004 in Bristol.",
                     contact_facts={"emails": ["post@invented.test"], "phones": [],
                                    "chrome_emails": chrome, "chrome_phones": [],
                                    "has_address": True,
                                    "street_hint": "12 Invented Street",
                                    "postcode_hint": "BS1 5EF"})

    partners = _page(HOST + "/partners.html",
                     body_text="Our partners work across the region.",
                     contact_facts={"emails": ["hello@a-partner.test"], "phones": [],
                                    "chrome_emails": [], "chrome_phones": []})

    def _reason(pages):
        result = SkillResult("fact-extractability-audit")
        FACTS._check_core_facts(result, {"pages": pages}, pages, "Invented")
        assert not result.findings, [f["title"] for f in result.findings]
        return next(r["reason"] for r in result.not_applicable
                    if r["check"] == "core-facts-present")

    # The partners page is crawled first, and its email is no longer the one
    # the report hands out.
    reason = _reason([partners, _home(["post@invented.test"])])
    assert "post@invented.test" in reason
    assert "hello@a-partner.test" not in reason
    assert "header, footer or address block" in reason

    # No chrome detail anywhere: the body reading still marks the fact found,
    # and says what it is.
    reason = _reason([partners, _home([])])
    assert "hello@a-partner.test" in reason
    assert "may belong to an organisation the page is about" in reason


# --------------------------------------------------------------------------
# A page that says what it is without making its own name the subject
# --------------------------------------------------------------------------

def test_a_site_that_says_what_it_is_without_naming_itself_is_not_reported():
    """A departmental site opens "Ada Lovelace - Professor, Statistics,
    Example University." and then "I am a Professor in the Department of
    Statistics at Example University." Both state an identity; neither makes
    the detected brand string the subject of a copular sentence, so the
    finding fired one line under the sentence that answers it.

    The last two assertions are the ones that keep the check worth running: a
    site that only greets its visitor still has no definition, and neither
    does one whose predicate names no category."""
    title_line = "Ada Lovelace — Professor, Statistics, Example University."
    assert "Professor" in (FACTS._find_definition(title_line, "Ada Lovelace", {}) or "")

    first_person = "I am a Professor in the Department of Statistics at Example University."
    assert "Professor" in (FACTS._find_definition(first_person, "Sitemap for stat", {}) or "")

    assert FACTS._find_definition(
        "Home. Products. Get in touch today and see the difference.", "Acme", {}) is None
    assert FACTS._find_definition("Acme is a leading global company.", "Acme", {}) is None


def test_a_tagline_after_a_dash_is_a_definition():
    """"Django - The web framework for perfectionists with deadlines." was
    reported as a site that never states in one sentence what it is."""
    found = FACTS._find_definition(
        "Django – The web framework for perfectionists with deadlines.", "Django", {})
    assert found and "web framework" in found
    # A hyphen inside a name is not the dash that introduces a tagline.
    assert FACTS._find_definition("Marks-and-Spencer sells clothing", "Marks", {}) is None


def test_a_maintainer_named_in_prose_is_not_reported_as_no_named_team():
    """A mail server's homepage reads "It is <Person>'s mail server ..." and
    "<Person> continues to maintain <Brand>". It was told "no founding year and
    no named team or leadership detail appears in the page text or in the page
    markup" - one sentence of which is true.

    The maintainer has to be seen, so the finding stops claiming he is not
    there; the year really is absent, so the finding still fires for that, and
    at the milder severity a site that names who runs it has earned."""
    prose = ("What is Corvid? It is Wilma Vermeer's mail server that started life at a "
             "research lab as an alternative to an older program. After eight years "
             "elsewhere, Wilma continues to maintain Corvid.")
    home = _page(HOST + "/", page_type="home", body_text=prose,
                 contact_facts={"emails": ["post@invented.test"],
                                "chrome_emails": ["post@invented.test"],
                                "has_address": True,
                                "street_hint": "1 Invented Street",
                                "postcode_hint": "X1 2YZ"})

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [home]}, [home], "Corvid")

    titles = [finding["title"] for finding in result.findings]
    assert titles == ["The site never states its founding facts in plain text"], titles
    assert result.findings[0]["severity"] == "low", "a site that names its maintainer"
    for finding in result.findings:
        assert "team" not in finding["evidence"], finding["evidence"]

    # And the collective words that name nobody no longer stand in for a name.
    notice = ("CATEGORIES OF INDIVIDUALS COVERED BY THE SYSTEM: Individuals covered by this "
              "system include Invented Archive employees, contractors and volunteers.")
    assert FACTS._own_sentence(
        notice, FACTS.TEAM_FACT_RE, "Invented Archive",
        states_the_fact=lambda sentence, match: FACTS._states_a_named_person(
            sentence, match, "Invented Archive")) is None


def test_a_never_title_is_scoped_to_what_the_crawl_actually_read():
    """Two findings ranked do-first read "The site never states its founding or
    team facts" and "The site never states its location or service area", over
    evidence saying "Across the 1 of 2 page(s) this audit read". The site's own
    about page carries both; the crawl never reached it, because robots.txt
    asks for a 30-second delay and that consumed the whole budget.

    The body text hedged and the title did not, and a site owner reads the
    title."""
    def _findings(snapshot, pages):
        result = SkillResult("fact-extractability-audit")
        FACTS._check_core_facts(result, snapshot, pages, "Invented Group")
        return result.findings

    home = _page(HOST + "/", page_type="home", body_text="Invented Group. Welcome.")
    sitemap = [{"url": HOST + "/sitemap.xml", "status": 200, "url_count": 44,
                "lastmod_count": 0, "child_sitemaps": []}]
    starved = _findings({"pages": [home], "sitemaps": sitemap,
                         "crawl": {"pages_crawled": 2, "max_pages": 60,
                                   "budget_exhausted": True}}, [home])
    assert starved, "the facts really are absent from the page that was read"
    for finding in starved:
        assert "never" not in finding["title"].lower(), finding["title"]
        assert "1 page this audit could read" in finding["title"], finding["title"]
        assert finding["severity"] == "low", finding["title"]

    # A crawl that read as much as it was ever going to is not under-sampled,
    # however large the sitemap. Sixty pages is the whole instrument.
    pages = [_page(HOST + "/p{}".format(i)) for i in range(60)]
    whole = _findings({"pages": pages,
                       "sitemaps": [{"url": HOST + "/sitemap.xml", "status": 200,
                                     "url_count": 32820, "lastmod_count": 0,
                                     "child_sitemaps": []}],
                       "crawl": {"pages_crawled": 60, "max_pages": 60,
                                 "budget_exhausted": True}}, pages)
    assert whole
    for finding in whole:
        assert finding["title"].startswith("The site never states"), finding["title"]
        assert finding["severity"] == "medium", finding["title"]


# --------------------------------------------------------------------------
# Sections, headings and the prose statistics run over them
# --------------------------------------------------------------------------

def test_an_answer_with_no_number_in_it_is_not_warm_up():
    """A coffee roaster's FAQ was reported as 5 of 5 sections opening with
    warm-up prose. One of the five does. "Discount codes cannot combine with
    other discounts." is the answer, in sentence one, and was counted as
    throat-clearing for holding no digit and for not repeating the category
    label "Website" above it - which no answer would."""
    sections = [
        ("Welcome", "Welcome to our little roastery, where every bean has a story."),
        ("Website", "Discount codes cannot combine with other discounts."),
        ("Coffee", "We roast to order Monday through Friday."),
        ("Shipping", "Delivery times range from 1-5 days."),
        ("Returns", "Unopened bags can go back within 30 days."),
    ]
    warm = [heading for heading, opening in sections
            if FACTS._opens_with_warm_up({"heading": heading, "first_paragraph": opening})]
    assert warm == ["Welcome"]

    # And the shapes that really do defer are still caught, whether or not
    # they carry a figure.
    assert FACTS._opens_with_warm_up({
        "heading": "Pricing",
        "first_paragraph": "For over 25 years we have believed that good coffee "
                           "should be simple."})


def test_an_index_page_is_not_judged_for_heading_vocabulary():
    """On a contents page the headings *are* the content, so "these words appear
    nowhere else in the page text" is guaranteed by the page's shape before
    anything is measured.

    It is the English half of a failure whose other half is language: the same
    check fired on the table of contents of a Japanese manual, and
    `test_language.py` holds that side."""
    titles = ["Recording the speaker changes", "Marking inaudible passages",
              "Numbers and units", "Proper nouns and spellings",
              "Timestamps and their format", "Submitting the finished file"]
    contents = _page(HOST + "/handbook/", page_type="documentation",
                     headings={"h1": ["Handbook"], "h2": titles},
                     sections=[{"heading": t, "first_paragraph": "See the chapter. " * 4,
                                "navigational": False} for t in titles],
                     body_text="Handbook contents",
                     links={"internal": [{"text": t, "url": HOST + "/handbook/" + str(i)}
                                         for i, t in enumerate(titles)]})
    assert FACTS.is_listing_page(contents), "a contents page is an index of other pages"

    result = SkillResult("fact-extractability-audit")
    FACTS._check_slogan_headings(result, [contents])
    assert not result.findings, [f["title"] for f in result.findings]


def test_a_page_built_out_of_lists_is_not_measured_for_sentence_length():
    """"Written in sentences too long to quote" fired on a page listing
    several thousand contributor names, whose 92.5-word "average sentence" is
    the extractor joining list items that end in no full stop, and on an
    alphabetical catalogue of authors and titles. Neither is a listing page by
    URL, page type or headings, so the text itself has to be read - and a page
    of long real sentences has to stay measurable, because reporting those is
    what the check is for."""
    # The names are inside list items, so the share sees what they are. The
    # average is the backstop for the same page written as one paragraph of
    # line breaks, which is why the catalogue below is caught on the share
    # alone and the credits page on either.
    credits = _page(HOST + "/credits", word_count=6000,
                    readability={"sentence_count": 65, "avg_sentence_words": 92.5,
                                 "long_sentence_share": 0.95, "list_text_share": 0.0})
    catalogue = _page(HOST + "/authors-a", word_count=1200,
                      readability={"sentence_count": 14, "avg_sentence_words": 40.0,
                                   "long_sentence_share": 0.9, "list_text_share": 1.0})
    long_winded = _page(HOST + "/terms", word_count=800,
                        readability={"sentence_count": 13, "avg_sentence_words": 58.0,
                                     "long_sentence_share": 0.9, "list_text_share": 0.15})
    assert FACTS._holds_prose(credits) is False
    assert FACTS._holds_prose(catalogue) is False
    assert FACTS._holds_prose(long_winded) is True

    result = SkillResult("fact-extractability-audit")
    FACTS._check_long_sentences(result, [credits, catalogue])
    assert not result.findings


def test_an_h1_hidden_with_css_is_still_an_h1():
    """Two sites were told pages of theirs carry no H1. Both serve
    `<h1 style="display:none">` naming the page, which is what a search or
    answer engine reads. `headings` has hidden markup removed, which is right
    for every check that reads the page as prose and wrong for asking whether
    the markup carries a heading at all."""
    hidden = _page(HOST + "/locations/", page_type="location",
                   headings={"h1": [], "h2": ["Where to find us"]},
                   headings_in_markup={"h1": ["Beware of heatstroke"]})
    bare = _page(HOST + "/empty", headings={"h1": []}, headings_in_markup={"h1": []})

    result = SkillResult("fact-extractability-audit")
    FACTS._check_heading_hierarchy(result, [hidden])
    assert not [f for f in result.findings if f["root_cause"] == "heading-structure"]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_heading_hierarchy(result, [hidden, bare])
    finding = next(f for f in result.findings if f["root_cause"] == "heading-structure")
    assert "1 of 2" in finding["evidence"]


def test_a_missing_translation_key_is_not_quoted_as_the_sites_words():
    """A handicraft shop's pricing was printed with two i18n keys in it:
    "Translation missing: en.general.social.share_on_facebook"."""
    quoted = FACTS._without_missing_translations(
        "Share it Translation missing: en.general.social.share_on_facebook "
        "[missing \"en.cart.title\" translation] Regular price Rs 2,299.00")
    assert quoted == "Share it Regular price Rs 2,299.00"
    assert FACTS._without_missing_translations("Translation is our trade.") == (
        "Translation is our trade.")


def test_the_heading_finding_says_how_many_and_scales_with_it():
    """One sentence, "Heading structure does not describe the page
    reliably", was printed on nine of twelve sites whether 2 of 60 pages
    lacked an H1 or 50 of 59 did. Its fix told readers to demote extra H1s,
    which this check does not measure."""
    headed = [_page(HOST + "/p{}".format(i), headings={"h1": ["Page {}".format(i)]})
              for i in range(18)]
    archive = [_page(HOST + "/archive/{}".format(i), headings={"h1": []},
                     headings_in_markup={"h1": []}) for i in range(2)]
    result = SkillResult("fact-extractability-audit")
    FACTS._check_heading_hierarchy(result, headed + archive)
    finding = next(f for f in result.findings if f["root_cause"] == "heading-structure")
    assert finding["title"] == "2 of the 20 crawled pages have no top-level heading"
    assert finding["severity"] == "low"
    assert not any("Demote" in step for step in finding["suggested_action"]["how_to_fix"])

    home = _page(HOST + "/", page_type="home", headings={"h1": []},
                 headings_in_markup={"h1": []})
    result = SkillResult("fact-extractability-audit")
    FACTS._check_heading_hierarchy(result, headed + [home])
    finding = next(f for f in result.findings if f["root_cause"] == "heading-structure")
    assert finding["title"].endswith("the homepage among them")
    assert finding["severity"] == "medium"
