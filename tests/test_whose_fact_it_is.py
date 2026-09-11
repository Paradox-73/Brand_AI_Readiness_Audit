# -*- coding: utf-8 -*-
"""Whose fact it is: a sentence on the site is not always about the site.

Every case here is a statement the report attributed to the audited brand that
belongs to somebody or something else - a second office, a magazine's review,
a partner's template, a product series, a subsidiary's advert - or a run of
interface labels that is not a statement at all.

  a second office          the Organization's own postal code, printed on the
                           contact page, read as contradicted by the other
                           office's code printed beneath it
  a review                 a magazine article about the brand counted as the
                           brand's own off-site profile
  somebody's template      "Free and Open Source <Brand> JS Template" filed as
                           the brand's own price
  a product series         "Launched in 2020, the <Name> Series ..." filed as
                           the brand's founding year
  a purchase               "since our purchase in 1969" filed as a founding
  another body's founding  "since its founding in 1973", about a subsidiary
  a cost below zero        "a less-than-zero cost" filed as "costs nothing"
  an advert                a subsidiary's number, in an advert naming its own
                           web address, filed as the brand's contact route
  a cart drawer            "Skip to content Close menu Cart ... ₹250 Add" quoted
                           as a statement of price, and counted as a sentence
  a surname and a town     counted as organisations sharing the brand's name

Every host, brand, name and Q-id below is invented.
"""

from __future__ import annotations

import importlib.util
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import SkillResult  # noqa: E402
from page_extract import recount_readability  # noqa: E402


def _module(skill, name):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module("fact-extractability-audit", "facts_for_whose_fact_tests")
FRESHNESS = _module("freshness-corroboration-audit", "freshness_for_whose_fact_tests")

HOST = "https://www.fernwork.test"
BRAND = "Fernwork"


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


def _everything_printed(result):
    parts = [entry["reason"] for entry in result.not_applicable]
    for finding in result.findings:
        parts += [finding.get("title") or "", finding.get("evidence") or ""]
    return " ".join(parts)


# --------------------------------------------------------------------------
# A second office is not a contradiction
# --------------------------------------------------------------------------

def _contact_page(body_text):
    return _page(
        HOST + "/contact", page_type="contact", body_text=body_text,
        jsonld=[{"@type": "Organization", "name": BRAND,
                 "address": {"@type": "PostalAddress", "postalCode": "560123"}}],
        # The extractor keeps one code per page, and here it kept the second
        # office's.
        contact_facts={"postcode_hint": "019988", "phones": []})


def _consistency(pages):
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_fact_consistency(result, {"pages": pages, "origin": HOST}, pages)
    return result


def test_a_page_listing_a_second_office_does_not_contradict_the_first():
    """The declared code is printed on the page, beside the other office's.
    Reported as "Organization postal code 560123 does not match the code shown
    on the page (019988)", at high severity."""
    page = _contact_page("Head office: 12 Mill Lane, Harbourtown - 560123. "
                         "Second office: Quay Tower, 4 Quay Road, Islandia - 019988.")
    result = _consistency([page])
    assert not any("contradicts itself" in f["title"] for f in result.findings)


def test_the_declared_code_printed_on_another_crawled_page_is_enough():
    contact = _contact_page("Our office: Quay Tower, 4 Quay Road, Islandia - 019988.")
    footer = _page(HOST + "/", body_text="Registered office 12 Mill Lane, Harbourtown 560 123.")
    result = _consistency([contact, footer])
    assert not any("contradicts itself" in f["title"] for f in result.findings)


def test_a_declared_code_the_site_prints_nowhere_is_still_a_contradiction():
    page = _contact_page("Our office: Quay Tower, 4 Quay Road, Islandia - 019988.")
    result = _consistency([page])
    assert any("contradicts itself" in f["title"] for f in result.findings)
    assert "560123" in _everything_printed(result)


# --------------------------------------------------------------------------
# A review of the brand is not the brand's account
# --------------------------------------------------------------------------

REVIEW = "https://www.homes-magazine.test/fernwork-nesting-coffee-table-review-37439576"


def test_a_magazine_review_linked_from_three_pages_is_coverage_not_a_profile():
    """The address contains the brand's name and three regional press pages
    link it, and both of those kept it as the brand's own profile."""
    assert FRESHNESS._looks_like_a_citation(REVIEW, 3, 30, None, [BRAND], set()) is True
    # However it is linked.
    assert FRESHNESS._looks_like_a_citation(REVIEW, 3, 30, None, [BRAND], {REVIEW}) is True


def test_an_account_on_an_unnamed_host_is_still_an_account():
    assert FRESHNESS._looks_like_a_citation(
        "https://www.homes-magazine.test/fernwork", 3, 30, None, [BRAND], set()) is False
    # Two words and a listing number is a directory entry, not a headline.
    assert FRESHNESS._is_coverage_not_an_account(
        "https://www.shop-directory.test/fernwork-home-12345678") is False
    # And an address the site claims in its own `sameAs` is never overruled.
    assert FRESHNESS._looks_like_a_citation(REVIEW, 1, 30, REVIEW, [BRAND], set()) is False


def test_a_long_handle_on_a_profile_platform_is_not_read_as_a_headline():
    platform = next(host for host in FRESHNESS.SOCIAL_PLATFORMS if "/" not in host)
    url = "https://www.{}/company/fernwork-home-furniture-and-decor-studio-12345678".format(
        platform)
    assert FRESHNESS._is_coverage_not_an_account(url) is False


# --------------------------------------------------------------------------
# Somebody else's template is not the brand's price
# --------------------------------------------------------------------------

def _site_with(themes_text):
    home = _page(
        HOST + "/", page_type="home",
        body_text=("Fernwork is a framework for building invented interfaces. "
                   "Fernwork was founded in 2014 in Ashcombe by Dana Okonkwo. "
                   "Write to us at 12 Mill Lane, Ashcombe KT7 4LP."),
        contact_facts={"chrome_emails": ["hello@fernwork.test"],
                       "emails": ["hello@fernwork.test"],
                       "street_hint": "12 Mill Lane", "postcode_hint": "KT7 4LP",
                       "has_address": True})
    themes = _page(HOST + "/ecosystem/themes", body_text=themes_text)
    return [home, themes]


def _core_facts(pages, brand=BRAND):
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, brand)
    return _everything_printed(result)


def test_a_partners_template_listed_on_the_site_is_not_the_sites_price():
    """The brand's name is in the sentence, which is why it was attributed,
    but only as a modifier: what is free is the template."""
    printed = _core_facts(_site_with(
        "Themes. Browse dashboard templates built by partners. "
        "Free and Open Source Fernwork JS Template for Admin Dashboard. "
        "Sing App Fernwork Admin Dashboard Template with a server backend."))
    assert "JS Template" not in printed


def test_the_brand_saying_it_is_free_is_still_its_price():
    printed = _core_facts(_site_with("Fernwork is free and open source. It always will be."))
    assert "Fernwork is free and open source." in printed


def test_only_the_brand_as_a_modifier_of_a_listing_noun_is_refused():
    built_on = FACTS._free_is_said_of_something_built_on_the_brand
    assert built_on("Free and Open Source Fernwork JS Template for Admin Dashboard", BRAND)
    assert built_on("Free and Open Source Fernwork.js Admin Dashboard.", "Fernwork.js")
    assert built_on("The Fernwork theme is free to download.", BRAND)
    # The brand as the subject, and the site in the first person, are the
    # site's own.
    assert not built_on("Fernwork is free and open source.", BRAND)
    assert not built_on("Fernwork is a free and open source library.", BRAND)
    assert not built_on("We publish a free and open source Fernwork plugin.", BRAND)


# --------------------------------------------------------------------------
# A series, a purchase and a subsidiary are not the brand's founding
# --------------------------------------------------------------------------

def _founding(sentence, brand=BRAND):
    match = FACTS.FOUNDING_FACT_RE.search(sentence)
    return bool(match) and FACTS._states_a_founding_year(sentence, match) and not (
        FACTS._someone_elses_founding(sentence, match, brand))


def test_the_year_a_product_series_launched_is_not_the_founding_year():
    assert not _founding("Launched in 2020 with 5 coffees, the Harvest Series featured "
                         "7 coffees last year, sourced from our partner farms.")
    assert not _founding("The Harvest Series was established in 2020.")
    assert not _founding("Our winter collection first launched in 2019.")


def test_when_we_started_roasting_is_a_founding_year():
    assert _founding("When we started roasting in 2013, we had a small machine that ran "
                     "all night.")
    # A body that is itself a festival or a club is founded like any other.
    assert _founding("The festival was founded in 1947.")


def test_a_purchase_is_not_a_founding():
    assert not _founding("On the contrary, since our purchase in 1969, dividends of "
                         "$20 million have been paid.")
    assert not _founding("We have owned the bank since we acquired it in 1969.")
    # The founding is still read where the sentence says both.
    assert _founding("Founded in 1965 and acquired by a larger group in 1969, the bakery "
                     "still bakes every morning.")


def test_establishing_a_shareholding_is_not_a_founding():
    assert not _founding("We also added to a small position in Kelvin Industries that we "
                         "had established in late 1990.")
    assert not _founding("In 2011 we established a partnership with a regional grower.")
    assert not _founding("The firm established its presence in the northern market in 2008.")
    # The body itself, established, is still its founding.
    assert _founding("The Kelvin Trust was established in 1990 to fund local libraries.")


def test_its_founding_is_the_founding_of_the_body_the_sentence_names():
    brand = "Harrowgate Holdings"
    assert not _founding("Our poorest performer has been Kelvin Insurance Company of "
                         "Ashcombe, at which large losses have been sustained annually "
                         "since its founding in 1973.", brand)
    assert _founding("Harrowgate Holdings Company has grown every year since its "
                     "founding in 1990.", brand)


# --------------------------------------------------------------------------
# A cost below zero, a quoted phrase and a dated letter are not a price
# --------------------------------------------------------------------------

def test_a_cost_below_zero_is_not_free():
    assert FACTS.COSTS_NOTHING_RE.search(
        "Both operations recorded an underwriting profit, thereby generating float at "
        "a less-than-zero cost.") is None
    assert FACTS.COSTS_NOTHING_RE.search("Delivery is zero cost to you.") is not None


def test_a_phrase_in_quotation_marks_is_a_mention():
    sentence = 'If you subscribe to this "no cash-no cost" theory, give us a call.'
    match = FACTS.COSTS_NOTHING_RE.search(sentence)
    assert match and FACTS._inside_quotation_marks(sentence, match)
    plain = "Delivery comes at no cost to you."
    assert not FACTS._inside_quotation_marks(plain, FACTS.COSTS_NOTHING_RE.search(plain))


def test_a_letter_filed_under_its_year_does_not_state_the_sites_price():
    assert FACTS._a_dated_document({"url": HOST + "/letters/1992.html"})
    assert FACTS._a_dated_document({"url": HOST + "/2023/05/our-news"})
    assert not FACTS._a_dated_document({"url": HOST + "/pricing"})
    pages = _site_with("Browse the ecosystem.")
    pages.append(_page(HOST + "/letters/1992.html",
                       body_text="Shipping is free for every shareholder this year, and "
                                 "we will happily post the report to you."))
    assert "Shipping is free" not in _core_facts(pages)


# --------------------------------------------------------------------------
# A number in somebody else's advert is theirs
# --------------------------------------------------------------------------

ADVERT = ("Corporate Governance Common Stock Information FOR A FREE CAR INSURANCE RATE QUOTE "
          "THAT COULD SAVE YOU SUBSTANTIAL MONEY WWW.RIVALINSURE.TEST OR CALL 1-800-555-0199, "
          "24 HOURS A DAY If you have any comments about our web page, write to us.")


def test_a_number_in_an_advert_naming_another_website_is_not_the_sites_contact():
    page = {"url": "https://www.harrowgate-holdings.test/", "body_text": ADVERT}
    assert FACTS._usable_phones({"phones": ["800-555-0199"]}, page) == []


def test_a_number_beside_the_sites_own_address_or_its_account_is_kept():
    own = {"url": "https://www.harrowgate-holdings.test/",
           "body_text": "Call us on 800-555-0199 or visit www.harrowgate-holdings.test today."}
    assert FACTS._usable_phones({"phones": ["800-555-0199"]}, own) == ["800-555-0199"]
    platform = next(host for host in FACTS.SOCIAL_PLATFORMS if "/" not in host)
    social = {"url": "https://www.harrowgate-holdings.test/",
              "body_text": "Follow us at https://{}/harrowgate or call 800-555-0199 "
                           "on weekdays.".format(platform)}
    assert FACTS._usable_phones({"phones": ["800-555-0199"]}, social) == ["800-555-0199"]
    # A framework's name with a dot in it is not a website.
    framework = {"url": "https://www.harrowgate-holdings.test/",
                 "body_text": "Support for Fernwork.js users: call 800-555-0199."}
    assert FACTS._usable_phones({"phones": ["800-555-0199"]}, framework) == ["800-555-0199"]


def test_a_column_of_years_is_not_a_telephone_number():
    page = {"url": HOST + "/letters/1978.html",
            "body_text": "Holdings at year end 1978 1977 1978 Total"}
    assert FACTS._usable_phones({"phones": ["1978 1977 1978"]}, page) == []


def test_the_tail_of_a_figure_in_a_table_is_not_a_telephone_number():
    page = {"url": HOST + "/annual-review",
            "body_text": "Kelvin Financial Corporation - Parent.... 1,771 2,006 777 813 665 "
                         "419 Kelvin Savings Association....."}
    assert FACTS._usable_phones({"phones": ["006 777 813"]}, page) == []


def test_a_number_given_as_another_named_businesss_is_theirs():
    holding = "https://www.harrowgate-holdings.test/message.html"
    theirs = {"url": holding,
              "body_text": "Call Dana Okonkwo or Ravi Sethuraman at Kelvin's (800-555-0142) "
                           "and save on your next purchase."}
    assert FACTS._usable_phones({"phones": ["800-555-0142"]}, theirs) == []
    ours = {"url": holding,
            "body_text": "For a printed report, call Harrowgate's (800-555-0142) on weekdays."}
    assert FACTS._usable_phones({"phones": ["800-555-0142"]}, ours) == ["800-555-0142"]


def _core_result(pages, brand=BRAND):
    result = SkillResult("fact-extractability-audit")
    FACTS._check_core_facts(result, {"pages": pages}, pages, brand)
    return result


def _undated_home():
    return _page(
        HOST + "/", page_type="home",
        body_text="Fernwork is a framework for building invented interfaces. "
                  "Write to us at 12 Mill Lane, Ashcombe KT7 4LP.",
        contact_facts={"street_hint": "12 Mill Lane", "postcode_hint": "KT7 4LP",
                       "has_address": True})


def test_a_dated_letter_supplies_neither_a_contact_number_nor_an_elapsed_time_founding():
    """A letter filed under its year speaks for that year. Its "since 1980"
    is a holding period and its telephone number is the one printed then."""
    letter = _page(HOST + "/letters/1985.html",
                   body_text="We have held these shares since 1980, and we still do. "
                             "Call our office on 800-555-0142 for a printed copy.",
                   contact_facts={"phones": ["800-555-0142"]})
    result = _core_result([_undated_home(), letter])
    found = result.signals.get("core_facts_found") or []
    assert "founding facts" not in found
    # The homepage's "Write to us at 12 Mill Lane" is a contact route, so a
    # contact is found - but from the address, never from the letter's number,
    # which would have been read as text and stamped with its source.
    assert "contact" in found
    assert not result.signals.get("contact_detail_source")


def test_a_dated_page_still_supplies_a_founding_said_with_a_founding_verb():
    release = _page(HOST + "/news/2019/05/new-office",
                    body_text="Fernwork was founded in 2014 in Ashcombe.")
    found = _core_result([_undated_home(), release]).signals.get("core_facts_found") or []
    assert "founding facts" in found


def test_a_rate_since_a_year_is_not_a_founding():
    assert not _founding("A premium increase of 10% per year since 1979 would have produced "
                         "an aggregate increase of 61%.")
    assert not _founding("Our sales have grown by a fifth every year since 2015, per year "
                         "on average.")


# --------------------------------------------------------------------------
# A cart drawer is not a sentence
# --------------------------------------------------------------------------

CART = ("Skip to content Close menu Cart Close cart Redeem your points Recommended Products "
        "₹250 Add ₹250 Add ₹600 Cold Brew Bags - Bold Add ₹600 Cold Brew Bags - Dark Add ₹300 "
        "Add PEOPLE ALSO BOUGHT Special instructions for seller Subtotal ₹0 Shipping, taxes and "
        "discounts calculated at checkout")
PICKER = ("Size Grind GRIND GUIDE Add to cart Subscribe & Save Upto 20% Enter PIN to check "
          "shipping option & delivery time CHECK Harvest Blend Kelvin Estate Subscription "
          "Coffee Mixed Bag Save Approx 10% SUBSCRIBE Size Grind FREQUENCY START FROM BUY NOW "
          "Mixed Bag, FREE STANDARD SHIPPING")
BREW_TABLE = ("VISIT THE FARM French Press How to Brew 4:00 MINS / BREW TIME 16G / COFFEE "
              "AMOUNT 255ML / WATER VOLUME, 96°C / WATER TEMPERATURE VERY COARSE / GRIND SIZE "
              "VIEW OTHER RECIPES Stovetop Pot How to Brew 3:30 MINS / BREW TIME 15G / COFFEE "
              "AMOUNT")
SPEC_TABLE = ("Additional Details Country of Origin Ashcombe Manufactured By Kelvin Enterprises "
              "Private Limited Licence No 10821005 Net Quantity 250g/500g/1000g Harvest Blend "
              "- Kelvin Estate, Tasting Notes Cocoa Dried Fruit Roast Level Medium Dark Body "
              "Medium Acidity Low Origin Hills Altitude Grade Premium")
SHORT = [
    "The Harvest Blend is grown on a hillside farm above the river valley.",
    "Beans are dried slowly in the wind before they are sorted by hand.",
    "We roast every batch on the morning it leaves for the post.",
    "A medium roast brings out notes of cocoa and dried fruit.",
    "Brew it in a press or a stovetop pot for the fullest cup.",
]
LONG = [
    "While the process has its roots in a long sea voyage when a cargo of green beans was "
    "soaked by the monsoon winds for weeks, the same conditions are now recreated in open "
    "warehouses for every lot.",
    "When it is roasted to a medium dark level, the result is a full cup that opens with "
    "sweet aromas of butter biscuits and moves through cocoa and dried fruit before a long "
    "finish of dark chocolate.",
    "Best enjoyed as an espresso, in a stovetop pot or in a French press, the nutty "
    "character of this coffee makes for a rounded cup that works equally well with milk or "
    "drunk on its own.",
    "Situated beside a small village in the hills, the farm is named after an old dynasty "
    "that ruled the valley for several centuries and whose ruined forts can still be seen "
    "from its terraces.",
]


def test_interface_labels_are_told_apart_from_sentences():
    for run in (CART, PICKER, BREW_TABLE, SPEC_TABLE):
        assert FACTS._reads_as_interface_labels(run), run[:40]
    for sentence in SHORT + LONG:
        assert not FACTS._reads_as_interface_labels(sentence), sentence[:40]
    # A disclaimer in block capitals is a sentence being shouted.
    assert not FACTS._reads_as_interface_labels(
        "IN NO EVENT WILL FERNWORK LIMITED, OR ANY PERSON INVOLVED IN CREATING THIS SITE, BE "
        "LIABLE FOR ANY DAMAGES OF ANY KIND ARISING FROM ITS USE.")
    # And a script with no capitals is never read this way.
    assert not FACTS._reads_as_interface_labels(
        "本页面介绍 Fernwork 的 GitHub 仓库、Discord 社区和 API 文档，以及如何提交问题和贡献代码，"
        "欢迎所有开发者参与 Fernwork Admin UI 的开发与测试工作。")


def _long_sentence_findings(text):
    readability = recount_readability({"list_text_share": 0.0, "subheading_count": 4}, text)
    page = _page(HOST + "/products/harvest-blend", page_type="product", body_text=text,
                 readability=readability)
    result = SkillResult("fact-extractability-audit")
    FACTS._check_long_sentences(result, [page])
    return [f for f in result.findings if "too long to quote" in f["title"]]


def test_a_cart_drawer_does_not_make_a_product_page_long_winded():
    """Four runs of interface labels among five short sentences: counted, they
    are 44% of the page's sentences and all of its long ones."""
    text = " ".join(SHORT[:3] + [CART + ".", PICKER + ".", BREW_TABLE + ".",
                                 SPEC_TABLE + "."] + SHORT[3:])
    assert _long_sentence_findings(text) == []


def test_long_real_sentences_are_still_reported():
    text = " ".join(SHORT[:3] + LONG + SHORT[3:])
    assert len(_long_sentence_findings(text)) == 1


# --------------------------------------------------------------------------
# A surname, a town and a dance are not organisations sharing the name
# --------------------------------------------------------------------------

class _Json:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def json(self):
        return self._payload


class _StubWikidata:
    def __init__(self, items, sites):
        self.items = items
        self.sites = sites

    def try_get(self, url, **kwargs):
        if "wbgetentities" in url:
            return _Json({"entities": {
                item_id: {"claims": {"P856": [{"mainsnak": {"datavalue": {"value": site}}}]}}
                for item_id, site in self.sites.items()}})
        return _Json({"search": self.items})


OWN_ITEM = "Q7780001"
UNIVERSITY = "https://www.kelvin-university.test"
OTHER_KINDS = [
    {"id": "Q7780002", "label": "Kelvin", "description": "family name", "aliases": []},
    {"id": "Q7780003", "label": "Kelvin", "description": "town in an invented county",
     "aliases": []},
    {"id": "Q7780004", "label": "Kelvin", "description": "island in an invented sea",
     "aliases": []},
    {"id": "Q7780005", "label": "Kelvin", "description": "invented music genre and dance",
     "aliases": []},
]


def _ambiguity(items):
    own = {"id": OWN_ITEM, "label": "Kelvin University", "aliases": ["Kelvin"],
           "description": "university in an invented country"}
    stub = _StubWikidata([own] + items, sites={OWN_ITEM: UNIVERSITY})
    result = SkillResult("freshness-corroboration-audit")
    FRESHNESS._check_entity_ambiguity(
        result, {"origin": UNIVERSITY}, [],
        {"name": "Kelvin", "declared_by": {"Kelvin": ["jsonld:Organization"]}},
        {}, stub, True)
    return result


def test_things_of_another_kind_sharing_the_name_are_not_a_collision():
    """Reported as "4 other entities share the name", at high severity, about
    a university whose own item Wikidata already holds."""
    result = _ambiguity(OTHER_KINDS)
    assert not any(f["root_cause"] == "entity-ambiguity" for f in result.findings)
    reason = next(entry["reason"] for entry in result.not_applicable
                  if entry["check"] == "entity-ambiguity")
    assert "different kind of thing" in reason
    assert result.signals["wikidata_match_count"] == 0


def test_organisations_sharing_the_name_are_still_counted_and_the_name_is_sourced():
    others = [
        {"id": "Q7780006", "label": "Kelvin", "description": "company in an invented city",
         "aliases": []},
        # An organisation word wins over a place word.
        {"id": "Q7780007", "label": "Kelvin", "description": "college in an invented town",
         "aliases": []},
    ]
    result = _ambiguity(others + OTHER_KINDS)
    finding = next(f for f in result.findings if f["root_cause"] == "entity-ambiguity")
    assert finding["title"] == '2 other entities share the name "Kelvin"'
    assert "the name read from the site's Organization markup" in finding["evidence"]
    assert "different kind of thing" in finding["evidence"]
    assert finding["severity"] != "high"
