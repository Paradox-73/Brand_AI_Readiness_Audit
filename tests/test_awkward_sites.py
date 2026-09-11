# -*- coding: utf-8 -*-
"""Four things measured on six deliberately awkward sites.

Each part below holds one property that an earlier build got wrong, and each
one is written so it can only pass by measuring the thing the site actually
shows - not by matching the fixture it was written against.

  1. a language switcher does not stop the wrong-`lang` check firing
  2. a town library named after its town is a public body, and a byline is not
     an identity
  3. a `<title>` that is a section name or a line of copy is not the brand
  4. a Buddhist-era date is a date, and a founding year has words outside
     English

Every site, name and address here is invented.
"""

from __future__ import annotations

import os
import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    buddhist_era_dates, detect_site_language, founding_word_pattern,
    founding_words, gregorian_year, names_a_civic_institution,
    person_speaks_for_the_site, resolve_brand, script_contradicts_language,
    site_kind, without_section_labels, why_this_is_not_a_name,
)


# ==========================================================================
# Part 1 - the wrong-`lang` check and the site with a language switcher
# ==========================================================================

# Invented Thai prose, and the Latin a real Thai site carries beside it: an
# English section, a language switcher, and Latin-script names in the nav.
THAI = (u"วัดแสงจันทร์เป็นวัดเก่าแก่ในจังหวัดเชียงใหม่ "
        u"เปิดให้ประชาชนเข้าชมทุกวันตั้งแต่เช้าจนถึงเย็น "
        u"ภายในวัดมีพระอุโบสถและหอไตรที่สร้างขึ้นในสมัยโบราณ ")
LATIN_CHROME = ("EN TH Wat Saeng Chan temple home news events visit contact "
                "donate about the temple opening hours getting here ")


def _pages(text, lang=None, count=3):
    return [{"url": "https://saengchan.test/{}".format(i), "status": 200,
             "lang": lang, "body_text": text} for i in range(count)]


def _counts(sample):
    from audit_common import script_counts
    return script_counts(sample)


def test_latin_nav_and_an_english_section_do_not_hide_a_wrong_lang():
    """The temple serves `lang="en"` over Thai prose on 57 of 60 pages.

    Its sample counted 3,616 Thai letters to 2,146 Latin ones. Thai cleared
    the 60% share; Latin at 37% cleared the old 10% ceiling, so the check
    written for this exact site could not fire on it.
    """
    sample = THAI * 6 + LATIN_CHROME * 3
    counts = _counts(sample)
    assert counts["thai"] > counts["latin"] > counts["thai"] * 0.25, counts
    assert script_contradicts_language("en", sample) == "thai"

    language = detect_site_language(_pages(sample, lang="en"))
    assert language["script"] == "thai"
    assert "contradicted by page text written in the thai script" in language["source"]
    assert language["prose_checks_apply"] is False


def test_the_declared_script_leading_is_still_never_a_contradiction():
    """The Japanese shop the ceiling was written for.

    Its product codes and brand names are Latin and its prose is not, so the
    declared language's own script leads. Whichever way the plurality falls,
    a declaration whose script leads is left alone.
    """
    japanese = u"このお店は京都の小さな金物屋です。毎日開いています。" * 4
    codes = "SKU AB-1200 AB-1201 AB-1202 Saeng Kanamono Kyoto " * 2
    assert script_contradicts_language("ja", japanese + codes) == ""


def test_latin_leading_needs_the_declared_script_to_be_all_but_absent():
    """Latin turns up on sites of every language, so Latin leading proves
    nothing on its own - however lopsided the ratio is."""
    thai_minority = THAI[:40] + " " + LATIN_CHROME * 3
    counts = _counts(thai_minority)
    assert counts["latin"] > counts["thai"] * 2, counts
    assert script_contradicts_language("th", thai_minority) == ""
    # With the declared script genuinely absent, the same direction answers.
    assert script_contradicts_language("th", LATIN_CHROME * 3) == "latin"


def test_a_bilingual_site_gets_silence_rather_than_a_finding():
    """Half and half is "cannot tell", which is the right answer here."""
    even = THAI * 2 + LATIN_CHROME * 5
    counts = _counts(even)
    assert counts["thai"] < counts["latin"] * 1.5, counts
    assert script_contradicts_language("en", even) == ""


# ==========================================================================
# Part 2 - the town library
# ==========================================================================

@pytest.mark.parametrize("name", [
    "Millbrook Library",
    "Millbrook Museum",
    "Millbrook Historical Society",
    "Ashcombe Free Library",
    "Wrenfield Township Library",
])
def test_a_place_and_the_word_is_an_institution_name(name):
    """Most public libraries carry no civic adjective at all - they are named
    after the town they stand in and nothing else."""
    assert names_a_civic_institution(name) is True


@pytest.mark.parametrize("name", [
    "Millbrook Library Systems Inc",      # the vendor
    "Component Library",                  # a design system
    "Pattern Library",
    "Image Library",
    "The Library",                        # a bar
    "Our Museum",
    "Library",
    "Software for public libraries",
])
def test_a_business_named_after_an_institution_is_not_one(name):
    assert names_a_civic_institution(name) is False


def _library_pages(with_person_graph=True):
    """A WordPress town library: a byline on every post, no Organization."""
    article = {"@type": "Article", "@id": "https://millbrook-library.test/p1#article",
               "author": {"@id": "https://millbrook-library.test/#/schema/person/a1"}}
    person = {"@type": "Person", "@id": "https://millbrook-library.test/#/schema/person/a1",
              "name": "A Librarian"}
    nodes = [article, person] if with_person_graph else [article]
    types = ["Article", "Person"] if with_person_graph else ["Article"]
    pages = [{"url": "https://millbrook-library.test/", "status": 200,
              "page_type": "home", "title": "Millbrook Library",
              "headings": {"h1": ["Millbrook Library"]},
              "jsonld": nodes, "jsonld_types": types, "links": {}}]
    for i in range(4):
        pages.append({"url": "https://millbrook-library.test/p{}".format(i),
                      "status": 200, "page_type": "article", "title": "Story time",
                      "jsonld": nodes, "jsonld_types": types, "links": {}})
    return pages


def _library_snapshot():
    return {"origin": "https://millbrook-library.test",
            "pages": _library_pages(),
            "brand": {"name": "Millbrook Library",
                      "host": "millbrook-library.test"}}


def test_a_town_library_is_a_public_body_not_a_researcher():
    """It came back `personal-or-academic`, and its report told a town library
    to claim "your researcher identifier" and to repeat its boilerplate in
    "every conference or speaker biography"."""
    kind = site_kind(_library_snapshot())
    assert kind.kind == "public-body"
    assert any("public institution" in line for line in kind.signals)
    assert any("nothing is for sale" in line for line in kind.signals)


def test_the_class_still_rests_on_nothing_being_for_sale():
    """The name alone never decides it. A shop named after a library is a
    shop, and the basket path is what says so."""
    snapshot = _library_snapshot()
    snapshot["pages"][0]["links"] = {
        "internal": [{"url": "https://millbrook-library.test/cart"}]}
    assert site_kind(snapshot).kind != "public-body"


def test_a_posts_author_is_not_the_sites_identity():
    """`Person` markup is on every WordPress site with a byline. The commonest
    SEO plugin writes it as a top-level `@graph` node the `Article` points at
    by `@id`, so the flattened type names cannot tell it from a personal site.
    """
    assert person_speaks_for_the_site(_library_pages()) is False
    # A person who is nobody's author is the site talking about itself.
    own = [{"url": "https://raelindqvist.test/", "status": 200,
            "page_type": "home",
            "jsonld": [{"@type": "Person", "@id": "https://raelindqvist.test/#me",
                        "name": "Rae Lindqvist"}],
            "jsonld_types": ["Person"], "links": {}}]
    assert person_speaks_for_the_site(own) is True
    # A crawl that recorded no nodes has no evidence, and says so rather than
    # answering "no" - the rule that reads this behaves as it did before.
    assert person_speaks_for_the_site(
        [{"url": "https://x.test/", "status": 200, "jsonld_types": ["Person"]}]) is None


def test_a_person_who_is_only_a_byline_does_not_make_a_personal_site():
    """The same reading, through `site_kind`: a blog with a byline and no
    civic name reaches neither the academic class nor a wrong one."""
    pages = [{"url": "https://notes.test/", "status": 200, "page_type": "home",
              "title": "Notes", "headings": {"h1": ["Notes"]},
              "jsonld": [{"@type": "Article",
                          "author": {"@id": "https://notes.test/#/schema/person/1"}},
                         {"@type": "Person", "@id": "https://notes.test/#/schema/person/1",
                          "name": "A Writer"}],
              "jsonld_types": ["Article", "Person"], "links": {}}]
    snapshot = {"origin": "https://notes.test", "pages": pages,
                "brand": {"name": "Notes", "host": "notes.test"}}
    assert site_kind(snapshot).kind != "personal-or-academic"


# ==========================================================================
# Part 3 - the brand name taken from the <title>
# ==========================================================================

def _docs_snapshot():
    """A documentation tree on a `docs.` subdomain."""
    home = {"url": "https://docs.frostvane.test/", "status": 200,
            "page_type": "home", "title": "Index - Frostvane user guide",
            "headings": {"h1": ["Frostvane"]},
            "body_text": ("Frostvane is a blazingly fast DataFrame library "
                          "for manipulating structured data."),
            "jsonld": [], "og": {}}
    return {"origin": "https://docs.frostvane.test", "pages": [home],
            "brand": {"name": "Frostvane user guide", "source": "title-part-1",
                      "host": "docs.frostvane.test", "domain_token": "docs",
                      "authoritative_variants": [], "alternate_names": [],
                      "fallback_candidates": ["Index", "Frostvane user guide"],
                      "declared_by": {}, "declared_on": {}, "name_candidates": []}}


def test_a_documentation_title_is_not_the_projects_name():
    """"Index - Frostvane user guide" made the brand "Frostvane user guide",
    the Organization snippet published that as the project's name, and the
    definition check hunted for "Frostvane user guide is a ..." above a
    homepage that opens "Frostvane is a blazingly fast DataFrame library"."""
    brand = resolve_brand(_docs_snapshot())
    assert brand["name"] == "Frostvane"
    whys = [r["why"] for r in brand["rejected_names"]]
    assert any("names a section of a site" in why for why in whys), whys
    assert "user guide" not in brand["name"]


def test_the_leftmost_label_of_a_docs_subdomain_is_not_the_brand():
    """`docs` was the second candidate, from the hostname's leftmost label.
    On a documentation subdomain that label is the section, not the site."""
    assert resolve_brand(_docs_snapshot())["name"] != "Docs"
    assert without_section_labels(["docs", "frostvane"]) == ["frostvane"]
    # Unless it is all there is: a site living at `docs.test` is called that.
    assert without_section_labels(["docs"]) == ["docs"]


def test_a_marketing_sentence_in_the_title_is_not_the_brand():
    """A one-page coaching site whose `<title>` is a whole marketing line
    became "Mentorship for women with <a person>", and that string was
    searched for literally in prose that opens "Hi! I'm <a person>!"."""
    home = {"url": "https://brightpath.test/", "status": 200,
            "page_type": "home",
            "title": "Mentorship for women with Rae Lindqvist",
            "headings": {"h1": ["Hi! I'm Rae Lindqvist"]},
            "body_text": ("Hi! I'm Rae Lindqvist! I'm a Holistic Life Coach "
                          "for parents and entrepreneurs. Brightpath is where "
                          "I work."),
            "jsonld": [], "og": {}}
    snapshot = {
        "origin": "https://brightpath.test", "pages": [home],
        "brand": {"name": "Mentorship for women with Rae Lindqvist",
                  "source": "title", "host": "brightpath.test",
                  "domain_token": "brightpath",
                  "authoritative_variants": [], "alternate_names": [],
                  "fallback_candidates": ["Mentorship for women with Rae Lindqvist"],
                  "declared_by": {}, "declared_on": {}, "name_candidates": []}}
    brand = resolve_brand(snapshot)
    assert brand["name"] == "Brightpath"
    assert any("line of copy" in r["why"] for r in brand["rejected_names"])


@pytest.mark.parametrize("name", [
    "Habitat for Humanity",
    "Museum of Craft and Design",
    "Boys & Girls Clubs of America",
    "Ashcombe Community Centre",
    "Millbrook News",
])
def test_a_real_name_is_not_read_as_a_line_of_copy(name):
    """The words real names are built out of - "of", "the", "and" - are
    deliberately not counted, and one joining word is not two."""
    home = {"url": "https://millbrook.test/", "status": 200, "page_type": "home",
            "title": name, "headings": {"h1": [name]}, "body_text": name,
            "jsonld": [], "og": {}}
    snapshot = {"origin": "https://millbrook.test", "pages": [home],
                "brand": {"host": "millbrook.test", "domain_token": "millbrook"}}
    assert why_this_is_not_a_name(name, snapshot) == ""


def test_the_address_is_the_escape_hatch_for_a_section_word():
    """A site living at `tvguide.test` really is named for a guide."""
    home = {"url": "https://tvguide.test/", "status": 200, "page_type": "home",
            "title": "TV Guide", "headings": {"h1": ["TV Guide"]},
            "body_text": "TV Guide lists what is on tonight.",
            "jsonld": [], "og": {}}
    snapshot = {"origin": "https://tvguide.test", "pages": [home],
                "brand": {"host": "tvguide.test", "domain_token": "tvguide"}}
    assert why_this_is_not_a_name("TV Guide", snapshot) == ""


def test_a_name_the_site_declares_is_left_alone():
    """Asserted is not derived. A site that puts a sentence in its own
    `Organization.name` has stated it, and these two tests are for strings cut
    out of a title, which nobody stated."""
    declared = "Mentorship for women with Rae Lindqvist"
    home = {"url": "https://brightpath.test/", "status": 200, "page_type": "home",
            "title": declared, "headings": {"h1": [declared]},
            "body_text": declared,
            "jsonld": [{"@type": "Organization", "name": declared}],
            "og": {}}
    snapshot = {"origin": "https://brightpath.test", "pages": [home],
                "brand": {"host": "brightpath.test", "domain_token": "brightpath"}}
    assert why_this_is_not_a_name(declared, snapshot) == ""


# ==========================================================================
# Part 4 - the Buddhist era and the founding words outside English
# ==========================================================================

@pytest.mark.parametrize("text,ce", [
    (u"43932 VIEW | 08/12/2561", 2018),
    (u"72878 VIEW | 07/12/2561", 2018),
    (u"224144 VIEW | 31/03/2563", 2020),
])
def test_a_buddhist_era_date_is_a_date(text, ce):
    """Every date pattern here anchors on `(?:19|20)\\d{2}`, so 2561 was not a
    year to any of them and the temple was told its articles carry no date."""
    found = buddhist_era_dates(text, script="thai")
    assert found and found[0][1] == ce, found


def test_a_conversion_that_cannot_be_justified_does_not_happen():
    """A wrongly converted date is worse than an unread one, so the calendar
    has to be evidenced by the page's own script or its declared language."""
    assert buddhist_era_dates(u"31/03/2563", script="latin", language="en") == []
    assert buddhist_era_dates(u"31/03/2563") == []
    # Declaring Thai is the other way of saying it, and is enough on its own.
    assert buddhist_era_dates(u"31/03/2563", language="th-TH")[0][1] == 2020


@pytest.mark.parametrize("year,script,expected", [
    (2561, "thai", 2018),        # in the window, on a Thai page
    (2018, "thai", 2018),        # already common era: left alone
    (2561, "latin", 2561),       # not a Thai page: never converted
    (2300, "thai", 2300),        # below the window
    (2700, "thai", 2700),        # above it
])
def test_only_a_year_in_the_window_on_a_thai_page_is_converted(year, script, expected):
    assert gregorian_year(year, script=script) == expected


def test_a_founding_year_reads_in_the_language_the_site_is_written_in():
    """A Norwegian site prints "( Fra 07.11.2004)" directly under its own name
    and was told "no founding year appears in the page text or in the page
    markup". The vocabulary was `since`, `founded`, `established` - English."""
    import re
    pattern = re.compile(
        r"\b(?:" + founding_word_pattern("no") + r")\b[^.!?]{0,30}"
        r"\b\d{1,2}[./]\d{1,2}[./](?:19|20)\d{2}\b", re.I | re.U)
    assert pattern.search(u"( Fra 07.11.2004)")


@pytest.mark.parametrize("code,word", [
    ("de", u"gegründet"), ("fr", "depuis"), ("es", "fundada"),
    ("nl", "sinds"), ("sv", "sedan"), ("no", "fra"), ("da", "grundlagt"),
    ("fi", "vuodesta"), ("it", "fondata"), ("pt", "fundado"),
    ("pl", u"założona"), ("th", u"ก่อตั้ง"), ("en", "founded"),
])
def test_every_language_the_crawl_meets_has_founding_words(code, word):
    assert word in founding_words(code)


def test_no_founding_word_outside_english_is_an_english_word():
    """Which is why every language's words may be read over a page whose
    language is unknown: none of them can fire on English prose."""
    english_prose = (
        "The gallery opened its doors and the team has been working here for "
        "years, with more than a dozen shows a season, so that is all of it."
    ).lower().split()
    for code, words in [(c, founding_words(c)) for c in
                        ("de", "fr", "es", "nl", "sv", "no", "da", "fi",
                         "it", "pt", "pl", "th")]:
        for word in words:
            assert word.lower() not in english_prose, (code, word)


def test_the_pattern_matches_the_longest_word_first():
    """"in business since" is one phrase, not `since` with three loose words
    in front of it."""
    pattern = founding_word_pattern("en")
    assert pattern.index("in\\ business\\ since") < pattern.index("since|") \
        or pattern.startswith("in\\ business\\ since")
