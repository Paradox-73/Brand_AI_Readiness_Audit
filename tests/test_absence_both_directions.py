# -*- coding: utf-8 -*-
"""Absence claims, checked in both directions at once.

A core fact is reported either as stated or as absent, and the two errors are
not independent: a fact recorded as PRESENT suppresses the finding underneath
it, and a fact recorded as ABSENT is itself the finding. So widening a
detector and tightening it pull against each other, and every case here holds
both ends of one rule.

  real facts reported as absent               junk recorded as a fact

  "<Brand>, Thornbury Road, <Town>            "11418 CVE-2025-1816 59733", a run
  KT7 4LP" - no house number, so every        of ticket numbers off an advisory
  street pattern missed it, and "the          table, recorded as a postal
  site never states its location" was         address with `has_address: true`
  the report's only high-severity item

  "<Brand> is unlike any other.               "The number of issues <Brand> has
  Founded in 1884, it houses ..." -           in <Tool> is now lower than it has
  the second sentence's subject is a          been since 2016" recorded as the
  pronoun, so "no founding year               founding year of a project that
  appears in the page text"                   states none

  "Entry is free." on a museum told           "27 Monmouth Street SE16 4RA"
  that no price statement appears             quoted as a location - a street
  anywhere on it                              from one shop joined to a postcode
                                              from another, a string on no page

What separates the two, for an address, is the postal code: a named street
with one beside it is an address, and a number with a word after it is not.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import (  # noqa: E402
    _contact_facts, _phone_from_click_to_chat, _street_hint, make_soup,
)


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit", "facts_for_both_directions_tests")

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


def _facts(text):
    soup = make_soup("<html><body><p>{}</p></body></html>".format(text))
    return _contact_facts(text, soup)


def _missing(result):
    return {finding["title"] for finding in result.findings}


# --------------------------------------------------------------------------
# 1. A street that is named rather than numbered
# --------------------------------------------------------------------------

CONTACT_PAGE = ("Receptionist and general emails. Address: Kelvinhall Museum, "
                "Thornbury Road, Ashcombe KT7 4LP. Visitor Experience.")
VISIT_PAGE = ("Address Kelvinhall Museum Thornbury Rd Ashcombe KT7 4LP "
              "Phone number +44 (0)1865 613000")


def test_an_address_with_no_house_number_is_still_an_address():
    """Every English street pattern opened `\\b\\d+`, so a house number was
    mandatory. Museums, universities, colleges and hospitals do not have one,
    and a museum's single high-severity finding - the report's first "start
    here" item - was that it never states its location, over two crawled pages
    carrying its address."""
    for text in (CONTACT_PAGE, VISIT_PAGE):
        street = _street_hint(text)
        assert street is not None, text
        assert "Thornbury R" in street.group(0), street.group(0)

    facts = _facts(CONTACT_PAGE)
    assert facts["has_address"] is True
    assert facts["postcode_hint"] == "KT7 4LP"


def test_a_named_street_with_no_postal_code_near_it_is_not_an_address():
    """The postal code is what carries the weight once the house number is
    gone. Without it "Abbey Road" in a sentence about a record would be a
    postal address."""
    assert _street_hint("They recorded the whole thing on Abbey Road one "
                        "afternoon in the spring.") is None


# --------------------------------------------------------------------------
# 2. A run of record identifiers is not an address
# --------------------------------------------------------------------------

ADVISORY_TABLE = ("Fixed in this release. 11418 CVE-2025-1816 59733 "
                  "11907 CVE-2025-2044 60021")


def test_a_table_of_advisory_identifiers_is_not_a_postal_address():
    """An open-source project's report printed "11418 CVE-2025-1816 59733" as
    its postal address, with `has_address: true`. The site states no location
    anywhere, so recording that marked the core-facts check passed and
    suppressed the true finding.

    A street name is made of words. `CVE-2025-1816` is an identifier: letters
    and digits mixed the way no place name mixes them."""
    assert _street_hint(ADVISORY_TABLE) is None
    facts = _facts(ADVISORY_TABLE)
    assert facts["has_address"] is False
    assert facts["street_hint"] == ""


def test_a_number_a_caption_calls_a_ticket_is_not_a_house_number():
    """The second half of the same rule, and the half `not_a_telephone_number`
    already applies to digits: what the digits are, plus the words in front of
    them."""
    assert _street_hint("Ticket 12 Harbour Lane crash on startup") is None
    assert _street_hint("Fixed in build 4 Harbour Lane rendering") is None
    # The words that introduce a house number in half of Europe are not on
    # that list, and must not be: the postal-code rule disowns a number after
    # "no." and "number", and in front of a street those two words are how an
    # address is written.
    assert _street_hint("No. 12 Harbour Lane, Ashcombe KT7 4LP") is not None
    # And a labelled number sitting earlier on the page does not hide the
    # address printed further down it.
    assert _street_hint("Ticket 12 Harbour Lane crash. Write to us at "
                        "8 Kelvin Row, Ashcombe KT7 4LP.") is not None


# --------------------------------------------------------------------------
# 3. A quoted address is a string the page contains
# --------------------------------------------------------------------------

def test_a_quoted_address_is_never_two_fragments_joined():
    """A coffee retailer's report printed `Location: / - "27 Monmouth Street
    SE16 4RA"`. The street is one shop, the postal code is a different shop
    four miles away, and the joined string appears on no page of the site."""
    apart = ("Our shop at 27 Harbour Lane is open daily from eight. " + "word " * 40
             + "The roastery is at Kelvin Wharf, SE16 4RA.")
    assert FACTS._address_line(apart, "27 Harbour Lane", "SE16 4RA") == ""

    together = "Visit us. 27 Harbour Lane, Ashcombe SE16 4RA. Open daily."
    assert (FACTS._address_line(together, "27 Harbour Lane", "SE16 4RA")
            == "27 Harbour Lane, Ashcombe SE16 4RA")


def test_the_report_quotes_one_half_rather_than_an_invented_line():
    """When the page does not print the two together, one of them is quoted -
    each half on its own is a run the extractor read off this page."""
    page = _page(HOST + "/", body_text="Our shop at 27 Harbour Lane is open daily.",
                 contact_facts={"street_hint": "27 Harbour Lane",
                                "postcode_hint": "SE16 4RA", "has_address": True})
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [page]}, [page], "Kelvinhall")
    printed = " ".join(reason["reason"] for reason in result.not_applicable
                       if reason["check"] == "core-facts-present")
    assert "27 Harbour Lane SE16 4RA" not in printed


# --------------------------------------------------------------------------
# 4. A sentence whose subject is a pronoun
# --------------------------------------------------------------------------

ABOUT_TEXT = ("The much-loved Kelvinhall Museum is unlike any other. Founded in 1884, "
              "it houses within an atmospheric building more than 500,000 objects "
              "and specimens from every continent.")


def test_a_founding_sentence_continuing_the_brands_own_sentence_is_the_brands():
    """The tool used the sentence immediately before this one as the site's
    Organization description, so it demonstrably read the paragraph - and then
    reported "no founding year appears in the page text or in the page
    markup"."""
    found = FACTS._own_sentence(ABOUT_TEXT, FACTS.FOUNDING_FACT_RE, "Kelvinhall Museum",
                               states_the_fact=FACTS._states_a_founding_year)
    assert found is not None and "1884" in found


def test_the_carried_attribution_stops_at_the_next_name():
    """The rule is "whoever the sentence in front of it was about", not "the
    site whatever happens next". A partner named in its own sentence takes the
    sentences after it with it."""
    partners = ("We work with many organisations. The Guild of Harbour Weavers is our "
                "oldest partner. Founded in 1911, it trains twelve apprentices a year.")
    assert FACTS._own_sentence(partners, FACTS.FOUNDING_FACT_RE, "Kelvinhall Museum",
                               states_the_fact=FACTS._states_a_founding_year) is None


# --------------------------------------------------------------------------
# 5. A year at the end of a count is not a founding year
# --------------------------------------------------------------------------

def test_the_far_end_of_a_measurement_is_not_a_founding_year():
    """An open-source project's report printed this as its founding fact. The
    sentence names the brand, so the attribution test passes it; the year in it
    is the span of a count. The project states no founding year at all, and
    that false pass hid the finding."""
    counting = ("The number of issues Kelvinhall has in Coverwatch (a static analyzer) "
                "is now lower than it has been since 2016.")
    assert FACTS._own_sentence(counting, FACTS.FOUNDING_FACT_RE, "Kelvinhall",
                               states_the_fact=FACTS._states_a_founding_year) is None
    # "Founded" says origin and nothing else, so it needs no second reading -
    # even in a sentence that goes on to compare something.
    origin = ("Kelvinhall was founded in 1993 and now has more collections than any "
              "other archive of its kind.")
    assert FACTS._own_sentence(origin, FACTS.FOUNDING_FACT_RE, "Kelvinhall",
                               states_the_fact=FACTS._states_a_founding_year) is not None


# --------------------------------------------------------------------------
# 6. Charging nothing is a price
# --------------------------------------------------------------------------

def test_free_entry_is_a_statement_of_what_something_costs():
    """A museum was told at high severity that "no price and no 'pricing on
    request' statement appears anywhere on the crawled pages", by a check whose
    own list of what it consulted says it reads sentences saying the site is
    free to use. Its pages say "Entry is free." and "Free, no booking
    required." - the words a visitor uses rather than the words a licence
    uses."""
    for text in ("Plan your visit. Entry is free.",
                 "Plan your visit. Free, no booking required.",
                 "Plan your visit. Admission is free for everyone."):
        assert FACTS._own_sentence(text, FACTS.COSTS_NOTHING_RE, "Kelvinhall Museum",
                                   minimum=FACTS.PRICE_STATEMENT_MIN) is not None, text


def test_the_other_sense_of_free_is_not_a_price():
    """"Free" also means "at liberty", and that reading must not mark the
    pricing fact present - a fact recorded as present suppresses the finding
    underneath it."""
    for text in ("You are free to cancel at any time before the visit.",
                 "This page is free of advertising and always will be.",
                 "Please feel free to use those examples in your own work."):
        assert FACTS.COSTS_NOTHING_RE.search(text) is None, text


# --------------------------------------------------------------------------
# 7. The whole check, both directions, on one site
# --------------------------------------------------------------------------

def test_a_museum_that_states_its_facts_is_not_told_it_states_none():
    """All three direction-one defects arrived in one report: the location
    finding was its only high-severity item and its first "start here" entry,
    the founding finding sat beside it, and the pricing finding was raised
    against two pages that say entry costs nothing."""
    home = _page(HOST + "/", page_type="home", body_text=ABOUT_TEXT,
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    visit = _page(HOST + "/visit", page_type="pricing",
                  body_text="Plan your visit. Free, no booking required.",
                  contact_facts={})
    contact = _page(HOST + "/contact", page_type="contact", body_text=CONTACT_PAGE,
                    contact_facts=_facts(CONTACT_PAGE))
    pages = [home, visit, contact]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Kelvinhall Museum")
    assert _missing(result) == set(), _missing(result)


def test_a_project_that_states_none_of_them_is_still_told_so():
    """The mirror image, and the reason the widening above has to be paid for.
    Neither the advisory table nor the issue count is a fact about the
    project, so both findings must still fire."""
    home = _page(HOST + "/", page_type="home",
                 body_text=("Kelvinhall is a library for reading structured data. "
                            "The number of issues Kelvinhall has in Coverwatch "
                            "(a static analyzer) is now lower than it has been "
                            "since 2016."),
                 contact_facts={"emails": ["hello@an-invented-host.test"],
                                "chrome_emails": ["hello@an-invented-host.test"]})
    security = _page(HOST + "/security.html", body_text=ADVISORY_TABLE,
                     contact_facts=_facts(ADVISORY_TABLE))
    pages = [home, security]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, "Kelvinhall")
    assert _missing(result) == {
        "The site never states its location or service area in plain text",
        "The site never states its founding facts in plain text",
    }, _missing(result)


# --------------------------------------------------------------------------
# 5. A telephone number published as a click-to-chat link
#
# An Indonesian shoe brand's homepage carries
# `<a href="https://wa.me/6282127323136">`, and the report said "no email
# address, telephone number, contact form, mailto: or tel: link and no
# ContactPoint markup was found on any crawled page". The number is in the
# delivered bytes of a page the crawl fetched.
#
# Both directions, as everywhere else in this file: a chat link that addresses
# a number is a contact route, and a "share this on WhatsApp" button is not,
# because recording a share button as the shop's telephone number would
# suppress the true finding instead of adding a false one.
# --------------------------------------------------------------------------

def _shop_page(body, html):
    soup = make_soup(html)
    return _page(HOST + "/", page_type="home", body_text=body,
                 links={"mailto_count": 0, "tel_count": 0,
                        "click_to_chat_count": sum(
                            1 for a in soup.find_all("a", href=True)
                            if _phone_from_click_to_chat(a.get("href")))},
                 contact_facts=_contact_facts(body, soup))


CHAT_ONLY = ('<html><body><p>Pesan sekarang.</p>'
             '<a href="https://wa.me/6282127323136">Chat WhatsApp</a></body></html>')
SHARE_ONLY = ('<html><body><p>Pesan sekarang.</p>'
              '<a href="https://wa.me/?text=Lihat">Bagikan</a></body></html>')


def test_a_shop_whose_only_number_is_a_chat_link_is_not_told_it_states_none():
    page = _shop_page("Pesan sekarang.", CHAT_ONLY)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [page]}, [page], "Sepatu")
    assert ("The site never states its contact method in plain text"
            not in _missing(result)), _missing(result)
    # Recorded as a fact the site states, which is what suppresses the finding
    # and what the report's own appendix reads.
    assert "contact" in result.signals["core_facts_found"]
    assert page["contact_facts"]["declared_phones"] == ["+6282127323136"]


def test_a_share_button_leaves_the_true_finding_standing():
    """The direction that pays for the widening. A page whose only chat link is
    a share button has published no number, and the finding must still fire."""
    page = _shop_page("Pesan sekarang.", SHARE_ONLY)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [page]}, [page], "Sepatu")
    assert "The site never states its contact method in plain text" in _missing(result), _missing(result)


def test_the_finding_names_the_link_kind_it_looked_for():
    """A report that lists `mailto:` and `tel:` and nothing else tells a reader
    with a chat link that the audit did not look for theirs."""
    page = _shop_page("Pesan sekarang.", SHARE_ONLY)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": [page]}, [page], "Sepatu")
    finding = next(f for f in result.findings
                   if f["title"] == "The site never states its contact method in plain text")
    everything = finding["evidence"] + " " + " ".join(finding.get("checked") or ())
    assert "click-to-chat" in everything, everything
