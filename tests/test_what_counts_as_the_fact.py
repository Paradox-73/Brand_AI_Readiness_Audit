# -*- coding: utf-8 -*-
"""What counts as the fact, in both directions, for four facts at once.

One rule runs through this whole file: the words the report prints have to
contain the fact, and a fact recorded as found suppresses the finding
underneath it, so a wrong "found" hides a true gap and a wrong "missing" tells
an owner to write something they have already written. Four checks got that
wrong on one batch of sites, in both directions, and each section below pins
one of them from both sides.

  a team fact names a person       a shop's category menu, read as the person
                                   behind the brand
  a founding fact is a body        a town chartered in 1764, told it states no
  coming into existence            founding year, with a fix a municipality
                                   cannot truthfully write
  a definition says what this      a library whose H1 is its name and whose
  is, and need not be a sentence   next line is what it is, told no page says
                                   what the brand is
  a contact detail is a token      ten digits out of the middle of a hex hash,
  on the page                      printed as the way to reach the site

Part one: a team fact names a person, and a shop's category menu names nobody.

Two consumer clothing and footwear shops were told they state who is behind
the brand. The words the report quoted were their own collection navigation -
"<Brand> Men's", "<Brand> Women's" - which is two capitalised words followed
by an apostrophe-s, the same shape as "<Person>'s mail server". Neither page
names a single human being. A core fact recorded as found suppresses the
finding underneath it, so both shops were told a gap they have is closed.

Both directions are pinned here, because this pattern has been wrong in both
on one batch of sites:

  "<Brand> Men's <Brand> Women's"      recorded as the person behind the brand,
                                       off a run of collection labels
  "New In Sale Women's clothing"       the same shape with no brand in it
  "Court Classic Everyday Essentials   a product line read as a person doing
   Run Club"                           something, through the subject shape
  "Journal Partners Stockists"         a footer link read as the person in the
                                       role in front of it

  "It is <Person>'s mail server ...    told that no named team or leadership
   <Person> continues to maintain      detail appears in its page text, two
   <Brand>."                           sentences under the name of the person
                                       who maintains it
  "Founded in 2011 by <Person>"        the four shapes that carry their own
  "Founder: <Person>"                  evidence in the words, which a shop's
  "<Person>, our managing director"    menu does not contain and which no
  "<Person> is the owner"              capitalisation test may throw away
"""

from __future__ import annotations

import importlib.util
import os

from conftest import ROOT

from audit_common import SkillResult


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit", "facts_for_team_fact_tests")

HOST = "https://an-invented-shop.test"

# The brand is one word, which is the ordinary case for a consumer shop and
# the one that matters: `speaks_about_itself` attributes a run of text to the
# site as soon as the brand's letters appear anywhere in it, and a menu whose
# every category is prefixed with the brand puts them in every run.
BRAND = "Larkspur"


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


def _team_fact(text, brand=BRAND, labels=()):
    """What the check would record as this site's stated team fact, or None."""
    return FACTS._own_sentence(
        text, FACTS.TEAM_FACT_RE, brand,
        states_the_fact=lambda sentence, match: FACTS._states_a_named_person(
            sentence, match, brand, labels))


# The menu of a clothing shop, as the extractor delivers it. The theme builds
# its menu out of plain `div`s, so the chrome removal leaves it in `body_text`
# and every pattern in the check sees it; `_readable_text` then puts a full
# stop after each heading, which is what turns single labels into "sentences"
# short enough to quote and long enough to clear the fifteen-character floor.
SHOP_MENU_RUN = (
    "Larkspur Supply Co. Shop New Arrivals Larkspur Men's Larkspur Women's "
    "Larkspur Kids' Accessories Sale Outerwear Knitwear Denim Shirts Trousers "
    "Footwear Gift Cards Stockists Journal Search Cart Account"
)
SHOP_TILES = (
    "Larkspur Supply Co. Larkspur Men's. Larkspur Women's. Larkspur Kids'. "
    "Everyday clothing, built to last. Every piece is cut from mid-weight organic "
    "cotton and finished with flat-felled seams. Larkspur Men's. Shirts, trousers "
    "and knitwear Larkspur Women's. Dresses, shirts and knitwear Larkspur Kids'."
)


# --------------------------------------------------------------------------
# A run of labels is not a sentence and a category is not a person
# --------------------------------------------------------------------------

def test_a_shops_category_menu_is_not_the_person_behind_the_brand():
    """The failure this file exists for, in the two shapes it arrived in."""
    assert _team_fact(SHOP_MENU_RUN) is None
    assert _team_fact(SHOP_TILES) is None


def test_a_category_label_with_no_brand_in_it_is_not_a_person_either():
    """The fix cannot be "the brand is not a person": the shops whose labels
    carry no brand produce the same shape out of two common nouns."""
    for run in ("Home New In Sale Women's Clothing Men's Clothing Accessories Gift Cards",
                "Menu Shop Kids' Footwear Adults' Footwear Size Guide Delivery Returns",
                "Larkspur Supply Co. Shop New In Trail Runner Court Classic "
                "Everyday Essentials Run Club Gift Cards Stockists"):
        assert _team_fact(run) is None, run


def test_a_footer_link_is_not_the_person_holding_the_role_in_front_of_it():
    """"Partners" is a leadership role in the pattern and a footer link on a
    shop, and the label beside it is the next footer link."""
    footer = ("Larkspur Supply Co. Help Delivery Returns Size Guide Journal Partners "
              "Stockists Press Contact Newsletter")
    labels = FACTS.site_labels([_page(HOST + "/", links={"footer": [
        {"url": HOST + "/pages/partners", "text": "Partners"},
        {"url": HOST + "/pages/stockists", "text": "Stockists"},
    ]})])
    assert _team_fact(footer, labels=labels) is None


def test_the_site_says_which_of_its_strings_are_labels():
    """The page's own evidence, and the only one that needs no vocabulary: a
    string the template puts in the menu, in the footer or in a heading it
    repeats across pages is furniture, whatever language it is in."""
    menu = [{"url": HOST + "/collections/mens", "text": "Larkspur Men's"},
            {"url": HOST + "/collections/womens", "text": "Larkspur Women's"}]
    pages = [
        _page(HOST + "/", links={"nav": menu},
              headings={"h1": ["Everyday clothing, built to last"],
                        "h2": ["Delivery"]}),
        _page(HOST + "/pages/about", links={"nav": menu},
              headings={"h1": ["Our workshop"], "h2": ["Delivery"]}),
    ]
    labels = FACTS.site_labels(pages)

    assert FACTS._is_a_label_on_this_site("Larkspur Men", labels)
    # A heading the template repeats on both pages is the template speaking.
    assert FACTS._is_a_label_on_this_site("Delivery", labels)
    # A heading appearing once is this page speaking, and on an about page it
    # is very often the name of a person. Ruling those out would be the
    # opposite error to the one this file is about.
    assert not FACTS._is_a_label_on_this_site("Our workshop", labels)
    assert not FACTS._is_a_label_on_this_site("Wilma Vermeer", labels)

    # And the label test survives the strapline a link often wraps with the
    # category name, which arrives as one label.
    tiles = FACTS.site_labels([_page(HOST + "/", links={"nav": [
        {"url": HOST + "/collections/womens",
         "text": "Larkspur Women's Dresses, shirts and knitwear"}]})])
    assert FACTS._is_a_label_on_this_site("Larkspur Women", tiles)


def test_a_run_of_labels_is_not_a_sentence():
    """`sentences` splits on terminators, so an unpunctuated menu is one
    "sentence" however long. What separates it from prose is that a label
    capitalises every word and a sentence does not."""
    assert not FACTS._prose_not_a_label_run(SHOP_MENU_RUN.split(". ", 1)[1])
    assert not FACTS._prose_not_a_label_run("New In Sale Women's Clothing Men's Clothing")
    assert not FACTS._prose_not_a_label_run("Larkspur Men's")
    assert FACTS._prose_not_a_label_run(
        "It is Wilma Vermeer's mail server that started life at a research lab.")
    assert FACTS._prose_not_a_label_run(
        "After eight years elsewhere, Wilma continues to maintain Corvid.")


# --------------------------------------------------------------------------
# And the sentences that really do name somebody still count
# --------------------------------------------------------------------------

def test_the_four_shapes_that_carry_their_own_evidence_still_count():
    """"Maintained by", "our founder", "is the owner" are words a menu does
    not contain, so these four shapes are judged on the words alone. Every one
    of them is how an open-source project, a personal site or an academic page
    attributes itself, and this check reported all of them absent once."""
    for text, who in (
            ("Larkspur Archive was founded in 2011 in Ludlow by Wilma Vermeer.",
             "Wilma Vermeer"),
            ("Our founder, Wilma Vermeer, still answers the post.", "Wilma Vermeer"),
            ("Wilma Vermeer, our managing director, opened the workshop.", "Wilma Vermeer"),
            ("Wilma Vermeer is the owner of Larkspur Archive and cuts every pattern.",
             "Wilma Vermeer"),
    ):
        found = _team_fact(text, brand="Larkspur Archive")
        assert found == text, text
        match = FACTS.TEAM_FACT_RE.search(text)
        assert who in FACTS._named_person(match), text


def test_a_maintainer_named_in_prose_survives_the_label_rule():
    """The case the possessive and subject shapes were widened for. A mail
    server's homepage reads "It is <Person>'s mail server ..." and "<Person>
    continues to maintain <Brand>", and was told no named team or leadership
    detail appears in its page text."""
    prose = ("What is Corvid? It is Wilma Vermeer's mail server that started life at a "
             "research lab as an alternative to an older program. After eight years "
             "elsewhere, Wilma continues to maintain Corvid.")
    assert _team_fact(prose, brand="Corvid")

    # Each of the two sentences on its own, so neither is carried by the other.
    assert _team_fact("Corvid is Wilma Vermeer's mail server, and has been since 2014.",
                      brand="Corvid")
    assert _team_fact("Wilma Vermeer maintains Corvid and answers its mail.", brand="Corvid")


# --------------------------------------------------------------------------
# What the whole check says about a shop that names nobody
# --------------------------------------------------------------------------

def _shop_pages():
    menu = [{"url": HOST + "/collections/mens", "text": "Larkspur Men's"},
            {"url": HOST + "/collections/womens", "text": "Larkspur Women's"},
            {"url": HOST + "/collections/kids", "text": "Larkspur Kids'"},
            {"url": HOST + "/pages/stockists", "text": "Stockists"}]
    home = _page(
        HOST + "/", page_type="home", body_text=SHOP_TILES,
        links={"nav": menu},
        headings={"h1": ["Everyday clothing, built to last"],
                  "h2": ["Larkspur Men's", "Larkspur Women's", "Larkspur Kids'"]},
        contact_facts={"emails": ["post@an-invented-shop.test"],
                       "chrome_emails": ["post@an-invented-shop.test"],
                       "has_address": True,
                       "street_hint": "1 Invented Street",
                       "postcode_hint": "X1 2YZ"})
    about = _page(
        HOST + "/pages/about", page_type="about", links={"nav": menu},
        headings={"h1": ["Our workshop"]},
        body_text=("Larkspur Supply Co. Shop New Arrivals Larkspur Men's Larkspur Women's. "
                   "We cut and sew everything in a workshop above a bakery. "
                   "Orders placed before noon ship the same working day."))
    return home, about


def test_a_shop_that_names_nobody_is_not_told_it_states_who_is_behind_it():
    """End to end. A wrong "found" does not merely print a false line: it is
    the reason the finding underneath it never fires, and it lowers the
    severity of the founding-facts finding beside it on the grounds that the
    site has named who runs it."""
    home, about = _shop_pages()
    pages = [home, about]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, BRAND)

    found = result.signals.get("core_facts_found") or []
    assert "team facts" not in found, found
    for entry in result.checks_run + result.not_applicable + result.findings:
        printed = str(entry)
        assert "Larkspur Men" not in printed, printed
        assert "Larkspur Women" not in printed, printed

    founding = [f for f in result.findings if "founding" in f["title"]]
    assert founding, "the shop states no founding year and should be told so"
    assert founding[0]["severity"] == "medium", (
        "a false team fact was what dropped this to low")


def test_the_same_shop_naming_its_maker_is_told_it_states_it():
    """The mirror image on the same site, so the rule is not "shops never name
    anyone". One sentence of prose on the about page is the whole difference."""
    home, about = _shop_pages()
    about = dict(about, body_text=(
        about["body_text"] + " Larkspur was founded by Wilma Vermeer, who still cuts "
        "every pattern herself."))
    pages = [home, about]

    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, BRAND)

    found = result.signals.get("core_facts_found") or []
    assert "team facts" in found, found
