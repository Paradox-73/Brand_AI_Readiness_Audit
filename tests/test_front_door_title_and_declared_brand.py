# -*- coding: utf-8 -*-
"""Three defects measured in one validation pass, and the guards on each.

*A shop's front door titled after a promotion.* A candle brand's homepage
`<title>` was "Warehouse Sale | <brand>" - the one title with a fixed job,
naming an event that ends rather than the business that stays. Measured
against the report rather than the site, the word "Warehouse" appeared 0 times
in the 70 KB report. The skill checked titles for length, for duplication and
for whether a declared `name` was really a page title, and none of those asks
what the homepage title says.

*The real name inconsistency, unread.* A leather-goods shop declared
`"brand":{"@type":"Brand","name":"<one string>"}` on 12 product pages, a second
string on 2 more, and a third string in its `Organization` block. Three
spellings of one brand in the site's own markup, and no check in this skill
read the `Brand` node at all.

*An example naming the wrong language.* A Norwegian site was told "Set the lang
attribute in your base template, for example `<html lang="en">`". Following it
literally declares Norwegian pages English, which is the defect
`lang-contradicts-page-text` reports elsewhere in the same skill.

Every brand, host and address below is invented.
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
             "sd_for_front_door_tests")

CANDLES = "https://a-candle-shop-that-does-not-exist.test"
LEATHER = "https://a-leather-shop-that-does-not-exist.test"
CAMERAS = "https://a-camera-shop-that-does-not-exist.test"


def _page(path, site=CANDLES, **extra):
    """A snapshot page carrying only the keys these checks read."""
    page = {
        "url": site + path,
        "page_type": "other",
        "status": 200,
        "title": "",
        "headings": {},
        "sections": [],
        "paragraphs": [],
        "body_text": "",
        "control_text": "",
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
        "og": {},
    }
    page.update(extra)
    return page


# --------------------------------------------------------------------------
# 1. The homepage title names a promotion
# --------------------------------------------------------------------------

BRAND = {"name": "Aurelia Candle Works"}


def _org_page(title, site=CANDLES, name="Aurelia Candle Works"):
    return _page("/", site=site, page_type="home", title=title,
                 jsonld=[{"@type": "Organization", "name": name,
                          "url": site + "/"}],
                 jsonld_types=["Organization"])


def _homepage_title(pages, site=CANDLES, brand=None):
    result = SkillResult("structured-data-audit")
    SD._check_homepage_title(result, {"origin": site}, pages,
                             BRAND if brand is None else brand)
    return result


def test_a_homepage_titled_after_a_sale_is_reported():
    """The measured defect. "Warehouse Sale | <brand>" on the front door."""
    result = _homepage_title([_org_page("Warehouse Sale | Aurelia Candle Works")])
    assert result.findings, "the front door names a promotion and nothing said so"
    finding = result.findings[0]
    assert finding["id_hint"] == "homepage-title-names-a-promotion"
    # In the real report the offending word never appeared at all. It has to
    # appear in the evidence, or the finding is about a
    # string the reader cannot find on their own site.
    assert "Warehouse" in finding["evidence"]
    assert finding["affected_pages"] == [CANDLES + "/"]


def test_the_brand_plus_a_descriptor_is_not_reported():
    """The false positive on the other side, and the common case.

    "<brand> - handmade candles" is a correct homepage title. What separates it
    from a promotion is that the leading segment is the business.
    """
    result = _homepage_title([_org_page("Aurelia Candle Works - handmade candles")])
    assert not result.findings
    assert [entry["check"] for entry in result.not_applicable] == ["homepage-title"]


def test_a_descriptor_leading_the_title_is_not_reported():
    """No name in the leading segment, and no event either. Not this finding."""
    result = _homepage_title([_org_page("Handmade soy candles | Aurelia Candle Works")])
    assert not result.findings


def test_for_sale_in_the_leading_segment_is_a_business_not_a_promotion():
    """The one common phrase where the word is permanent.

    An estate agent and a used-car dealer both describe what they do with it,
    and firing here would report the business for naming itself.
    """
    result = _homepage_title(
        [_org_page("Homes For Sale in Rivermouth | Kestrel Realty",
                   name="Kestrel Realty")],
        brand={"name": "Kestrel Realty"})
    assert not result.findings


def test_a_word_containing_sale_does_not_fire():
    """`\\b` and nothing looser: "wholesale" and "sales" are not "sale"."""
    for title in ("Wholesale candle supplies | Aurelia Candle Works",
                  "Sales training for candle makers | Aurelia Candle Works"):
        assert not _homepage_title([_org_page(title)]).findings, title


def test_a_not_open_yet_notice_is_reported():
    """The other half of the vocabulary: a site saying it is not trading."""
    result = _homepage_title([_org_page("Coming Soon | Aurelia Candle Works")])
    assert result.findings
    assert "Coming Soon" in result.findings[0]["evidence"]


def test_the_fix_step_names_the_site_and_never_a_placeholder_brand():
    """The step tells the owner what to title the page, using their own name.

    The same rule the language fix follows: an example value in a fix step is
    read off this site or it is not printed.
    """
    result = _homepage_title([_org_page("Warehouse Sale | Aurelia Candle Works")])
    steps = " ".join(result.findings[0]["suggested_action"]["how_to_fix"])
    assert "Aurelia Candle Works" in steps


def test_a_long_opening_segment_is_a_sentence_and_not_a_label():
    """A campaign banner is short. A sentence about the business is not, and
    one that happens to contain a promotion word is not this defect."""
    result = _homepage_title([_org_page(
        "Every candle we sell is poured by hand and never on sale to clear stock")])
    assert not result.findings


def test_no_homepage_reached_declines_rather_than_guessing():
    result = _homepage_title([_page("/about", page_type="about", title="About us")])
    assert not result.findings
    assert result.not_applicable[0]["check"] == "homepage-title"
    assert result.not_applicable[0]["reason"].strip()


# --------------------------------------------------------------------------
# 2. Three spellings of one brand in the site's own markup
# --------------------------------------------------------------------------

LEATHER_BRAND = {"name": "Sarai Leathers"}


def _product(brand_name, path, site=LEATHER):
    return _page(path, site=site, page_type="product",
                 title="A bag",
                 jsonld=[{"@type": "Product", "name": "Shoulder bag",
                          "brand": {"@type": "Brand", "name": brand_name}}],
                 jsonld_types=["Product"])


def _identity(org_name, site=LEATHER):
    return _page("/", site=site, page_type="home", title=org_name,
                 jsonld=[{"@type": "Organization", "name": org_name,
                          "url": site + "/"}],
                 jsonld_types=["Organization"])


def _brand_names(pages, site=LEATHER, brand=None):
    result = SkillResult("structured-data-audit")
    SD._check_declared_brand_names(
        result, {"origin": site, "pages": pages}, pages,
        LEATHER_BRAND if brand is None else brand)
    return result


def test_three_spellings_of_one_brand_across_fourteen_pages_are_reported():
    """The measured defect, at the shape it was measured in: 12 pages, then 2,
    against a third string in the Organization block."""
    pages = [_identity("Sarai Leathers")]
    pages += [_product("Sarai Leather", "/p/{}".format(n)) for n in range(12)]
    pages += [_product("SaraiLeathers Co", "/p/x{}".format(n)) for n in range(2)]
    result = _brand_names(pages)
    assert result.findings, "the site's own markup names its brand three ways"
    finding = result.findings[0]
    assert finding["id_hint"] == "markup-declares-more-than-one-brand-name"
    for spelling in ("Sarai Leather", "SaraiLeathers Co", "Sarai Leathers"):
        assert spelling in finding["evidence"], spelling


def test_every_string_quoted_is_one_the_named_sources_hold():
    """The half of the naming defect that is about verifiability, not detection.

    The pair of names an earlier naming finding quoted could not be located in
    any of the four sources that finding listed. Here every quoted string
    has to be a value the snapshot actually carries.
    """
    pages = [_identity("Sarai Leathers")]
    pages += [_product("Sarai Leather", "/p/{}".format(n)) for n in range(3)]
    finding = _brand_names(pages).findings[0]
    published = {"Sarai Leathers", "Sarai Leather"}
    quoted = {chunk.split('"')[0]
              for chunk in finding["evidence"].split('"')[1::2]}
    assert quoted <= published, "the evidence quotes a string no source holds: {}".format(
        quoted - published)


def test_a_shop_selling_other_peoples_goods_is_not_reported():
    """The guard that keeps every retailer out of this finding.

    A camera shop declaring a different maker on each product is publishing
    correct markup about other companies, not disagreeing with itself.
    """
    pages = [_identity("Rivermouth Camera Exchange", site=CAMERAS)]
    pages += [_product("Kestrel Optics", "/p/1", site=CAMERAS),
              _product("Marram Imaging", "/p/2", site=CAMERAS),
              _product("Thornwick Lenses", "/p/3", site=CAMERAS)]
    result = _brand_names(pages, site=CAMERAS,
                          brand={"name": "Rivermouth Camera Exchange"})
    assert not result.findings
    assert result.not_applicable[0]["check"] == "brand-name-in-markup"


def test_one_spelling_agreeing_with_the_organization_is_a_pass_with_a_reason():
    pages = [_identity("Sarai Leathers")]
    pages += [_product("Sarai Leathers", "/p/{}".format(n)) for n in range(4)]
    result = _brand_names(pages)
    assert not result.findings
    assert "Sarai Leathers" in result.not_applicable[0]["reason"]


def test_one_page_disagreeing_with_the_organization_is_a_typo_not_a_template():
    """Two is the floor. One page carrying a different spelling is an
    oversight, and reporting it as a template fault would send an owner to
    edit a template that is right."""
    pages = [_identity("Sarai Leathers"), _product("Sarai Leather", "/p/1")]
    result = _brand_names(pages)
    assert not result.findings


def test_a_brand_written_as_a_bare_string_is_read_too():
    """`"brand": "<name>"` is valid schema.org and real templates emit it."""
    pages = [_identity("Sarai Leathers")]
    pages += [_page("/p/{}".format(n), site=LEATHER, page_type="product",
                    jsonld=[{"@type": "Product", "name": "Belt",
                             "brand": "Sarai Leather"}],
                    jsonld_types=["Product"])
              for n in range(3)]
    result = _brand_names(pages)
    assert result.findings
    assert "Sarai Leather" in result.findings[0]["evidence"]


def test_no_brand_declared_anywhere_declines_rather_than_passing():
    pages = [_identity("Sarai Leathers")]
    result = _brand_names(pages)
    assert not result.findings
    assert result.not_applicable[0]["reason"].strip()


# --------------------------------------------------------------------------
# 3. The language example is this site's language, or there is none
# --------------------------------------------------------------------------

NORWEGIAN = (
    "Vi lager sko for hand i verkstedet vart i byen. Hver sale er sydd av "
    "vegetabilsk garvet lær, og vi selger dem bare direkte til kunder som "
    "kommer innom butikken eller bestiller pa nett. Alle skoene blir "
    "reparert gratis det forste aret etter kjopet, og vi tar imot gamle "
    "par til ombygging nar sallen er slitt ut."
)

CYRILLIC = (
    "Ми виготовляєм "
    "свічки ручної "
    "роботи з бджоли"
    "ного воску та "
    "продаємо їх у "
    "магазині та "
    "онлайн по всій "
    "країні протягом "
    "десяти років."
)


def _lang(pages):
    result = SkillResult("structured-data-audit")
    SD._check_lang(result, pages)
    return result


def _missing_lang_finding(result):
    return next(f for f in result.findings if f["id_hint"] == "missing-html-lang")


def test_the_example_is_never_english_on_a_site_that_is_not():
    """The measured defect, verbatim: a Norwegian site told to set `lang="en"`.

    One page declares the site's language and the rest do not, which is the
    template gap this finding is about - and the code to use is then a value
    read off the site rather than a guess.
    """
    pages = [_page("/", page_type="home", lang="nb", title="Skomakeriet",
                   body_text=NORWEGIAN),
             _page("/om-oss", page_type="about", title="Om oss",
                   body_text=NORWEGIAN),
             _page("/kontakt", page_type="contact", title="Kontakt",
                   body_text=NORWEGIAN)]
    finding = _missing_lang_finding(_lang(pages))
    text = " ".join([finding["suggested_action"]["summary"]] + list(finding["suggested_action"]["how_to_fix"]))
    assert "nb" in text, "the site declares nb and the fix has to say so"
    assert 'lang="en"' not in text
    assert "en-GB" not in text and "pt-BR" not in text


def test_no_example_code_at_all_where_only_the_script_is_known():
    """`detect_site_language` returns no `code` for a Cyrillic site on purpose:
    Cyrillic is Russian, Ukrainian, Bulgarian and Serbian. The step must print
    no code and must say the owner has to supply the language."""
    pages = [_page("/", page_type="home", title="Про",
                   body_text=CYRILLIC),
             _page("/about", page_type="about", title="Про",
                   body_text=CYRILLIC)]
    finding = _missing_lang_finding(_lang(pages))
    text = " ".join([finding["suggested_action"]["summary"]] + list(finding["suggested_action"]["how_to_fix"]))
    assert 'lang="' not in text, "a code was printed for a script that names no language"
    assert "cyrillic" in text.lower()
    assert "supply the code yourself" in text


def test_the_region_step_names_no_country_of_its_own():
    """"such as en-GB or pt-BR" were two codes chosen for no reason connected
    to the site. The shape of a region subtag is worth stating; which region
    this site publishes for was never read."""
    pages = [_page("/", page_type="home", title="Skomakeriet", body_text=NORWEGIAN)]
    finding = _missing_lang_finding(_lang(pages))
    steps = " ".join(finding["suggested_action"]["how_to_fix"])
    for guess in ("en-GB", "pt-BR", "en-US"):
        assert guess not in steps
