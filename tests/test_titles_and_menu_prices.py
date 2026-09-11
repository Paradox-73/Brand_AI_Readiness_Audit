# -*- coding: utf-8 -*-
"""Two things a page says that the audit was reading wrongly.

*A title that is the page's own address.* A real site shipped pages whose
`<title>` was the address of the page - a template variable that was never
substituted, so the fallback printed the URL. Every existing test passed it:
the title is present, it is not short, it is not long, and it is unique
precisely because every page's address differs. An assistant asked what the
page is got back the address it already had.

*A price printed only in a form menu.* On a storefront the variant row - size,
colour, stock state and the price of each - is a `<select>`, and every variant
price but the default one is written inside an `<option>`. That text is kept
out of `body_text` deliberately, because forty size labels read as prose weld
themselves onto the preceding sentence. A page declaring an Offer price of
569.00 was therefore measured against the single 609 printed beside its
heading, and correct markup was reported at high severity as contradicting the
page: an accusation of hidden or mismatched pricing about a price the page
prints and a visitor can pick.

Every brand, product and host below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module(os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"),
             "sd_for_title_and_menu_tests")

SITE = "https://a-bootmaker-that-does-not-exist.test"


# --------------------------------------------------------------------------
# 1. A <title> that is the page's own address
# --------------------------------------------------------------------------

def _titled(pages):
    """Run the hygiene check over pages carrying only the keys it reads."""
    records = []
    for index, title in enumerate(pages):
        records.append({
            "url": "{}/p{}".format(SITE, index),
            "page_type": "other",
            "status": 200,
            "title": title,
            "meta_description":
                "A description long enough to be nobody's problem here. Page {}.".format(index),
            "headings": {},
            "links": {"internal": []},
            "jsonld": [],
            "jsonld_types": [],
        })
    result = SkillResult("structured-data-audit")
    SD._check_titles_and_descriptions(result, records)
    return result


def _evidence(result):
    return " ".join(f["evidence"] for f in result.findings)


def test_a_title_that_is_the_page_address_is_reported():
    """The defect itself: the template printed the URL into the title slot."""
    result = _titled([
        "https://a-bootmaker-that-does-not-exist.test/products/winter-boot",
        "https://a-bootmaker-that-does-not-exist.test/products/summer-sandal",
        "Care and repair - Invented Bootmakers",
    ])
    assert "own address" in _evidence(result)
    assert "2 of 3" in _evidence(result), (
        "the sentence has to count the pages it is about: a count that "
        "disagrees with the list under it is the defect this repo has found "
        "three times")


def test_an_address_title_weighs_the_same_as_a_missing_title():
    """It answers the one question a title exists to answer with the address
    the asker already had, so it is not a cosmetic length complaint."""
    result = _titled([
        "https://a-bootmaker-that-does-not-exist.test/products/winter-boot",
        "Care and repair - Invented Bootmakers",
        "Where our leather comes from - Invented Bootmakers",
    ])
    assert result.findings[0]["severity"] == "medium"
    assert "{}/p0".format(SITE) in result.findings[0]["affected_pages"], (
        "a page named in the sentence has to be in the affected list, or the "
        "reader cannot find it")


def test_a_title_that_merely_contains_an_address_is_not_this_defect():
    """A real name, then an address after it. The page says what it is; the
    template did not fail. Reporting it would make the finding's own sentence
    false for the pages it named."""
    result = _titled([
        "Winter Boot - buy at https://a-bootmaker-that-does-not-exist.test/winter-boot",
        "Summer Sandal - buy at https://a-bootmaker-that-does-not-exist.test/sandal",
        "Care and repair - Invented Bootmakers",
    ])
    assert "own address" not in _evidence(result)


def test_a_title_that_opens_with_an_address_and_then_names_the_page_is_not_this_defect():
    """The other direction, and the reason the match is not simply anchored at
    the start. The address is there and so is the name, so a reader still
    learns what the page is - a milder fault, and one this finding does not
    claim to be about."""
    result = _titled([
        "https://a-bootmaker-that-does-not-exist.test - Invented Bootmakers, "
        "hand-welted boots since 1974",
        "https://a-bootmaker-that-does-not-exist.test - Care and repair, "
        "how we resole a welted boot",
        "Where our leather comes from - Invented Bootmakers",
    ])
    assert "own address" not in _evidence(result)


def test_a_bare_hostname_in_front_of_a_name_is_not_this_defect():
    """No scheme and no `www.`: a site writing its domain as its name. It
    identifies no page, so no page variable went missing to produce it, and a
    pattern loose enough to catch it also catches ordinary titles that begin
    with a dotted word."""
    result = _titled([
        "shop.a-bootmaker-that-does-not-exist.test - Winter Boot",
        "shop.a-bootmaker-that-does-not-exist.test - Summer Sandal",
        "Care and repair - Invented Bootmakers",
    ])
    assert "own address" not in _evidence(result)


def test_the_pass_sentence_says_the_addresses_were_looked_for():
    """A skip that claims more than it tested is how "every crawled page has a
    unique title" came to be printed about a site where a quarter of them were
    URLs."""
    result = _titled([
        "Winter Boot - Invented Bootmakers",
        "Summer Sandal - Invented Bootmakers",
        "Care and repair - Invented Bootmakers",
    ])
    assert not result.findings
    reasons = " ".join(entry["reason"] for entry in result.not_applicable)
    assert "own address" in reasons


# --------------------------------------------------------------------------
# 2. A variant price printed only in a `<select>`
# --------------------------------------------------------------------------

# The prose a storefront theme renders beside the heading: the default
# variant's price, and nothing else.
_PROSE = ("Winter Boot. Hand-welted in our own workshop. "
          "MRP GBP 609.00 inclusive of all taxes. Free delivery over GBP 2,000.00.")

# The same page's size menu, as the extractor records it in `control_text`:
# labels only, apart from the body text because forty size codes are not prose.
_MENU = ("5 - GBP 609.00 6 - Sold Out - GBP 569.00 "
         "7 - GBP 569.00 8 - GBP 609.00")


def _product_page(declared, control_text, prose=_PROSE, title="Winter Boot"):
    return {
        "url": SITE + "/products/winter-boot",
        "page_type": "product",
        "status": 200,
        "title": title,
        "headings": {"h1": [title]},
        "body_text": prose,
        "control_text": control_text,
        "links": {"internal": []},
        "jsonld": [{"@type": "Product", "name": "Winter Boot",
                    "offers": {"@type": "Offer", "price": declared,
                               "priceCurrency": "GBP"}}],
        "jsonld_types": ["Product"],
    }


def _conflicts(page):
    result = SkillResult("structured-data-audit")
    SD._check_consistency(result, [page], {"name": "Invented Bootmakers"})
    return result


def test_a_variant_price_in_a_menu_is_a_price_the_page_shows():
    """The false accusation: correct markup, at high severity, about a price
    the page prints and a visitor picks from a menu."""
    result = _conflicts(_product_page("569.00", _MENU))
    assert not result.findings, (
        "the declared 569.00 is in the page's size menu: {}".format(
            [f["evidence"] for f in result.findings]))


def test_a_price_in_neither_the_prose_nor_the_menu_is_still_a_conflict():
    """The guard the fix has to survive. Reading the menu widens what counts as
    shown on the page; it may not silence the check."""
    result = _conflicts(_product_page("42.00", _MENU))
    assert result.findings, "42.00 appears neither in the prose nor in the menu"
    assert result.findings[0]["severity"] == "high"


def test_the_menu_is_only_read_where_the_node_is_what_the_page_is_about():
    """A menu carries no name beside it, so it cannot say which item a price
    belongs to. `_prices_stated_for` answers "what does this page print beside
    this item's name", and control text glued to the end of the body would sit
    inside the window of whichever name falls last on the page - grading one
    item on a grid against another item's variant prices."""
    page = _product_page("569.00", _MENU)
    page["body_text"] = ""
    assert SD._prices_stated_for({"@type": "Product", "name": "Winter Boot"},
                                 page, ["Invented Bootmakers"]) == []


def test_an_item_on_a_list_is_still_graded_against_its_own_neighbourhood():
    """The same rule end to end. The page is a shelf, so the node is not its
    subject; the shelf prints 609.00 beside this item's name and the menu
    elsewhere carries 569.00, and the conflict stands."""
    page = _product_page("569.00", _MENU, title="Footwear for the hills")
    page["page_type"] = "category"
    page["body_text"] = "Winter Boot GBP 609.00 - hand-welted. " + "Also in the range. " * 30
    result = _conflicts(page)
    assert result.findings, (
        "a price printed beside this item's own name is what its markup "
        "answers to, and a menu with no name beside it cannot excuse it")


def test_a_page_with_no_menu_at_all_reads_exactly_as_before():
    """`control_text` is empty on every page that has no form controls, and an
    empty string may not change any answer."""
    without = _conflicts(_product_page("569.00", ""))
    assert without.findings, "609.00 is the only price on a page with no menu"
    assert not _conflicts(_product_page("609.00", "")).findings


# --------------------------------------------------------------------------
# 3. One template bug, reported once, and never copied into the fix
# --------------------------------------------------------------------------

# What a theme emits when it runs values through an HTML-escape filter inside a
# script element. Entities are not decoded there, so a consumer reads the five
# characters and not the apostrophe. The doubled form is what an
# already-escaped value looks like after a second pass over it.
_ESCAPED_NAME = "Invented Bootmakers&amp;#39; Wholefoods"
_PLAIN_NAME = "Invented Bootmakers' Wholefoods"


def _identity_page(path, name, title="Invented Bootmakers"):
    return {
        "url": SITE + path,
        "page_type": "home" if path == "/" else "about",
        "status": 200,
        "title": title,
        "headings": {"h1": [title]},
        "body_text": ("Invented Bootmakers' Wholefoods has grown pulses on the same "
                      "land since 1974, and sells them dried, milled and tinned."),
        "meta_description": "Pulses grown and milled on our own land since 1974.",
        "links": {"internal": []},
        "social_profiles": {},
        "jsonld": [{"@type": "Organization", "name": name, "url": SITE + "/",
                    "description": "Pulses grown on our own land.",
                    "logo": SITE + "/logo.png",
                    "sameAs": ["https://social-elsewhere.test/inventedbootmakers"]}],
        "jsonld_types": ["Organization"],
    }


def test_an_escaped_value_is_reported_as_the_template_bug_it_is():
    """One finding naming the cause, not a scatter of symptoms."""
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [_identity_page("/", _ESCAPED_NAME)])
    assert result.findings, "an HTML entity inside a JSON string value is a defect"
    finding = result.findings[0]
    assert finding["root_cause"] == "invalid-jsonld"
    assert "escape" in " ".join(finding["suggested_action"]["how_to_fix"]).lower()


def test_a_block_with_no_entities_in_it_is_not_reported():
    """The guard: the ordinary case must stay silent."""
    result = SkillResult("structured-data-audit")
    SD._check_jsonld_validity(result, [_identity_page("/", _PLAIN_NAME)])
    assert not result.findings


def test_two_spellings_of_one_escaped_name_are_one_name():
    """The symptom the cause finding explains. Decoded, they are the same
    string, and two identities are not asserted where one was written."""
    assert SD._as_published(_ESCAPED_NAME) == _PLAIN_NAME


def test_no_snippet_carries_a_character_reference():
    """A snippet is the tool's own writing. An owner who pastes the fix must
    not publish the bug the fix was generated for."""
    block = SD._snippet_block({"@type": "Organization", "name": _ESCAPED_NAME,
                               "mainEntity": [{"text": "beans &amp;amp; peas"}]})
    assert "&#39;" not in block and "&amp;" not in block
    assert _PLAIN_NAME in block and "beans & peas" in block


def test_a_headerlink_glyph_never_reaches_a_snippet():
    """A documentation generator prints an anchor mark beside every heading,
    and a paste-ready Article block was offered with it inside `headline`."""
    heading = "Journal" + chr(0x00B6)
    assert SD._as_published(heading) == "Journal"
    assert chr(0x00B6) not in SD._snippet_block({"headline": heading})


# --------------------------------------------------------------------------
# 4. A block that fails to parse is not a block that was never written
# --------------------------------------------------------------------------

def _article_page(path, broken=False):
    page = {
        "url": SITE + path,
        "page_type": "article",
        "status": 200,
        "title": "How we mill a pulse",
        "headings": {"h1": ["How we mill a pulse"]},
        "body_text": "The mill runs twice a week. " * 12,
        "paragraphs": ["The mill runs twice a week, and the stones are dressed monthly."],
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
    }
    if broken:
        page["jsonld_errors"] = [{
            "error": "Invalid control character at (line 4, col 21)",
            "excerpt": '{ "@context": "http://schema.org", "@type": "Article", "articleBody":',
        }]
    return page


def test_a_page_whose_article_block_does_not_parse_is_not_missing_one():
    """Thirteen posts were told to add an Article block in the same report that
    said their Article block dies on a raw newline. Following it adds a second
    block beside a broken one."""
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": [_article_page("/blog/mill", broken=True)]})
    assert not result.findings, [f["title"] for f in result.findings]
    reasons = " ".join(entry["reason"] for entry in result.not_applicable)
    assert "does not parse" in reasons and "Article" in reasons


def test_a_page_with_no_block_at_all_is_still_reported():
    """The guard. An absence is still an absence."""
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": [_article_page("/blog/mill")]})
    assert result.findings
    assert result.findings[0]["root_cause"] == "no-article-schema"


def test_the_two_findings_cannot_disagree_about_one_page():
    """Where some pages are broken and some are bare, the finding names the
    broken ones as not counted rather than folding them in."""
    pages = [_article_page("/blog/mill"), _article_page("/blog/store", broken=True)]
    result = SkillResult("structured-data-audit")
    SD._check_article(result, {"article": pages})
    finding = result.findings[0]
    assert finding["affected_pages"] == [SITE + "/blog/mill"]
    assert SITE + "/blog/store" in " ".join(finding["suggested_action"]["how_to_fix"])


# --------------------------------------------------------------------------
# 5. FAQPage advice aimed at a list of other pages
# --------------------------------------------------------------------------

def _card_listing(path):
    """A paginated index of posts. Its headings are titles and its paragraphs
    are excerpts the crawl cut off, so every answer would be a placeholder."""
    titles = ["Why Does Wool Felt When It Is Washed?",
              "How Long Should A Welted Sole Last?",
              "Can A Rubber Sole Be Resoled?"]
    excerpt = ("We are often asked this, and the answer depends on how the boot "
               "was made and on" + chr(0x2026))
    return {
        "url": SITE + path,
        "page_type": "category",
        "status": 200,
        "title": "Journal",
        "headings": {"h1": ["Journal"], "h2": titles},
        "sections": [{"heading": t, "first_paragraph": excerpt} for t in titles],
        "paragraphs": [excerpt],
        "body_text": " ".join(titles) + " We are often asked this. " * 8,
        "links": {"internal": [{"url": SITE + "/journal/wool", "text": t} for t in titles]},
        "jsonld": [],
        "jsonld_types": [],
    }


def test_a_paginated_index_of_posts_is_not_a_page_of_questions_and_answers():
    """Its "questions" are post titles and its "answers" are truncated
    excerpts, so every answer slot in the block would be a placeholder."""
    result = SkillResult("structured-data-audit")
    SD._check_faq(result, {"category": [_card_listing("/journal?page=3")]})
    assert not result.findings, [f["title"] for f in result.findings]


def test_the_pagination_parameter_alone_says_the_page_is_a_list():
    """`/page/2` was known as a path and never as a query string."""
    assert SD._why_this_page_is_a_list({"url": SITE + "/how-a-boot-is-made?page=3"})
    ordinary = {"url": SITE + "/how-a-boot-is-made?pagename=about", "headings": {},
                "paragraphs": ["The mill runs twice a week, and the stones are dressed."],
                "links": {"internal": []}, "sections": [], "jsonld_types": []}
    assert not SD._why_this_page_is_a_list(ordinary)


# --------------------------------------------------------------------------
# 6. Present and empty is not absent
# --------------------------------------------------------------------------

_LOOP_WITH_BLANKS = {
    "@type": "Organization",
    "name": "Invented Bootmakers",
    "sameAs": ["", "", "https://social-elsewhere.test/inventedbootmakers", "", ""],
}


def test_a_property_whose_first_entries_are_blank_is_not_missing():
    """The theme loop over unfilled social fields. The owner opens View Source,
    sees `sameAs`, and stops believing the report."""
    assert "sameAs" not in SD._missing_props(_LOOP_WITH_BLANKS, ["name", "sameAs"])
    assert SD._blank_entries(_LOOP_WITH_BLANKS, "sameAs") == 4


def test_a_property_holding_nothing_at_all_is_told_apart_from_an_absent_one():
    """Different defects, different fixes: one wants the property written, the
    other wants the template's blank fields filled."""
    empty = {"@type": "Organization", "name": "Invented Bootmakers", "sameAs": ["", ""]}
    assert SD._states_nothing(empty, "sameAs")
    assert not SD._states_nothing({"@type": "Organization"}, "sameAs")


# --------------------------------------------------------------------------
# 7. The platforms named are the ones this site has
# --------------------------------------------------------------------------

def test_the_same_as_step_names_the_profiles_the_site_already_links_to():
    """A sandal shop was told to claim a startup-funding database and a source
    repository, twenty lines above the same report's own advice naming the
    marketplaces it already sells on."""
    profile = "https://social-elsewhere.test/inventedbootmakers"
    pages = [{"url": SITE + "/", "social_profiles": {"elsewhere": profile}}]
    step = SD._same_as_fix_step(pages, {"name": "Invented Bootmakers"},
                                {"site": SITE, "origin": SITE, "pages": pages})
    assert profile in step
    for named_for_a_different_kind_of_site in ("Crunchbase", "GitHub", "LinkedIn"):
        assert named_for_a_different_kind_of_site not in step


def test_the_same_as_step_does_not_claim_the_profiles_were_followed():
    """A school's report offered a YouTube channel in this snippet while a
    finding lower down said the same URL returns HTTP 404 and that the entry
    should be deleted. Nothing in this skill can tell: the only code that
    fetches these addresses runs two sub-skills later and reports to its own
    findings file. The step has to say what was and was not measured, or the
    reader meets two opposite instructions for one property with nothing to
    choose between them."""
    profile = "https://social-elsewhere.test/inventedbootmakers"
    pages = [{"url": SITE + "/", "social_profiles": {"elsewhere": profile}}]
    step = SD._same_as_fix_step(pages, {"name": "Invented Bootmakers"},
                                {"site": SITE, "origin": SITE, "pages": pages})
    assert "not followed" in step
    assert "no longer resolve" in step


def test_the_fallback_names_a_kind_of_place_rather_than_a_brand():
    """With no profiles found there is nothing to name, and a fixed list is
    what put the wrong platforms in front of the wrong site."""
    step = SD._same_as_fix_step([], {}, {"site": SITE, "origin": SITE, "pages": []})
    for named_for_a_different_kind_of_site in ("Crunchbase", "GitHub"):
        assert named_for_a_different_kind_of_site not in step


# --------------------------------------------------------------------------
# 8. The same page at two addresses, with nothing saying which is the page
# --------------------------------------------------------------------------

_DOC_TEXT = ("The reference lists every public class in the package, with its "
             "constructor arguments and the exceptions it raises. ") * 4


def _doc_page(path):
    return {
        "url": SITE + path,
        "page_type": "other",
        "status": 200,
        "title": "Reference",
        "meta_description": "Every public class in the package, with its arguments.",
        "headings": {"h1": ["Reference"]},
        "body_text": _DOC_TEXT,
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
    }


_VERSIONED = [_doc_page("/en/latest/reference.html"),
              _doc_page("/en/stable/reference.html"),
              _doc_page("/en/v2/reference.html")]


def _titled_records(pages):
    result = SkillResult("structured-data-audit")
    SD._check_titles_and_descriptions(result, [dict(p) for p in pages])
    return result


def test_the_same_page_at_three_addresses_with_no_canonical_is_reported():
    """No canonical declared was being read as no canonical problem. It is the
    opposite: this is the case the tag exists for, and the audit had both the
    duplication and the absence of the declaration in hand."""
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, list(_VERSIONED))
    assert result.findings, [entry["reason"] for entry in result.not_applicable]
    finding = result.findings[0]
    assert len(finding["affected_pages"]) == 3
    assert finding["checked"], "an absence claim has to say where it looked"


def test_a_group_that_declares_a_canonical_is_already_answered():
    """The guard. A site that says which address is the page has done the work,
    whether or not the target is any good - that is another skill's question."""
    declared = [dict(p, canonical=SITE + "/en/stable/reference.html") for p in _VERSIONED]
    result = SkillResult("structured-data-audit")
    SD._check_canonical_declaration(result, declared)
    assert not result.findings


def test_two_addresses_for_one_page_are_not_two_pages_sharing_a_title():
    """60 of 60 pages "share a title", owner content owner, effort half a day
    to a day - and 29 of the 30 collisions were one page counted twice."""
    assert "share a <title>" not in _evidence(_titled_records(_VERSIONED))


# --------------------------------------------------------------------------
# 9. A name that is really a page title, and the fix that propagated it
# --------------------------------------------------------------------------

# The shape a template produces when it fills `name` from the SEO title: the
# town, the speciality, the practice and a programme name, divided by the
# fullwidth vertical line used across East Asia.
_TITLE_AS_NAME = ("Invented Town preventive dentistry" + chr(0xFF5C)
                  + "Invented Dental Rooms" + chr(0xFF5C) + "the KEEP programme")


def _clinic_snapshot():
    node = {"@type": "Organization", "name": _TITLE_AS_NAME,
            "description": _TITLE_AS_NAME, "url": SITE + "/blog",
            "logo": SITE + "/logo.png",
            "sameAs": ["https://social-elsewhere.test/inventeddental"]}
    blog = {"url": SITE + "/blog/", "page_type": "article", "status": 200,
            "title": _TITLE_AS_NAME, "headings": {"h1": ["Journal"]},
            "body_text": "Notes on preventive care, written by the practice. " * 6,
            "meta_description": "Notes on preventive care from the practice.",
            "links": {"internal": []}, "social_profiles": {},
            "jsonld": [node], "jsonld_types": ["Organization"]}
    home = {"url": SITE + "/", "page_type": "home", "status": 200,
            "title": "Invented Dental Rooms", "headings": {"h1": ["Invented Dental Rooms"]},
            "body_text": "Invented Dental Rooms has cared for the town since 1998. " * 6,
            "meta_description": "A dental practice in Invented Town, open six days.",
            "links": {"internal": []}, "social_profiles": {},
            "jsonld": [], "jsonld_types": []}
    return {"site": SITE, "origin": SITE, "pages": [home, blog]}, [home, blog], node


def test_a_name_that_is_the_page_title_is_reported_rather_than_propagated():
    """This is the exact failure the product exists to catch: the
    only identity markup on the site names the practice wrongly, and the fix
    told the owner to copy it onto the homepage."""
    snapshot, pages, _node = _clinic_snapshot()
    result = SkillResult("structured-data-audit")
    by_type = {"home": [pages[0]], "article": [pages[1]]}
    SD._check_organization(result, snapshot, pages, by_type,
                           {"name": "Invented Dental Rooms"})
    assert any("title" in f["title"].lower() for f in result.findings), (
        [f["title"] for f in result.findings])
    for finding in result.findings:
        assert _TITLE_AS_NAME not in (finding.get("snippet") or ""), (
            "the snippet may not carry the value the finding calls wrong")
        assert "byte-identical" not in " ".join(finding["suggested_action"]["how_to_fix"]), (
            "copying is only advice when the block being copied is right")


def test_a_separator_in_a_name_is_what_decides_it():
    """No brand name and no language needed: the separator is the site's own
    punctuation."""
    assert SD._name_is_really_a_page_title(_TITLE_AS_NAME)
    assert not SD._name_is_really_a_page_title("Invented Dental Rooms")


def test_an_identity_url_pointing_at_a_section_is_pulled_back_to_the_site():
    """`url` is what a machine follows to find the organisation again."""
    snapshot, _pages, node = _clinic_snapshot()
    assert SD._org_url_is_a_section(node, snapshot) == SITE + "/blog"
    assert SD._brand_url(SITE + "/blog", snapshot) == SITE + "/"
