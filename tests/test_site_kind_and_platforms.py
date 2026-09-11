"""Two things the audit could not tell apart, and the advice that went wrong.

**What kind of thing is this site?** Every piece of advice about founding
facts, service areas, opening hours, a team page, a press page and a company
profile on a professional network was written for a business with premises and
a marketing department, and then applied to everyone. On one real run a
command-line JSON processor was told to add "X was founded in <year> in
<place>" to its about page and "X serves customers across <regions>"; a
national weather service was told to claim a profile on a startup-funding
database; a software heritage society was told its homepage has no call to
action while its links read "Join the mailing list" and "Read papers".

`site_kind` answers the question those findings never asked, from the site's
own structure, and is allowed to answer "undetermined" - which must leave every
gated check firing exactly as it does today, because a wrong guess is what
caused the failures in the first place.

**Is this one page or three?** Three menu items pointing at one destination
through three navigation-source parameters were crawled as three pages, all
declaring the same canonical, and reported as three pages sharing a title.
Thirteen of sixty page slots went on variants of pages already read.

**Is this host the brand, or the platform it is built on?** The storefront
platform a consumer brand runs on, that platform's developer site, a protocol
site and a demonstration store were all counted as the brand's own off-site
profiles, taking the count from 3 to 8 - past the threshold on the one measure
this marketplace calls its strongest measured signal.
"""

from __future__ import annotations

import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PERSONAL_OR_ACADEMIC,
    PROJECT, PUBLICATION, PUBLIC_BODY, SITE_KINDS, UNDETERMINED,
    _ACADEMIC_LABELS, _PUBLIC_LABELS, _REGISTRY_TOP_LEVEL,
    _registry_suffix_kind,
    counts_as_off_site_profile, detect_page_type, fold_declared_duplicates,
    is_faceted_listing, is_infrastructure_url, is_tracking_parameter,
    looks_like_an_account, page_identity, site_kind, strip_tracking_parameters,
)


# --------------------------------------------------------------------------
# Part 1: what kind of thing is this site?
# --------------------------------------------------------------------------

def _snapshot(origin="https://acme.example", pages=(), **extra):
    snapshot = {"origin": origin, "pages": list(pages)}
    snapshot.update(extra)
    return snapshot


def _page(url, page_type="other", text="", jsonld_types=(), internal=(),
          external=(), **extra):
    page = {
        "url": url,
        "page_type": page_type,
        "body_text": text,
        "jsonld_types": list(jsonld_types),
        "links": {"internal": [{"url": u} for u in internal],
                  "external": [{"url": u} for u in external]},
    }
    page.update(extra)
    return page


def test_a_government_address_suffix_decides_it_outright():
    """A national weather service was advised to claim a company page on a
    professional network and a profile on a startup-funding database. The one
    fact that would have stopped both is in its address, and nobody can buy
    that suffix."""
    kind = site_kind(_snapshot("https://met-service.gov.zz"))
    assert kind.kind == PUBLIC_BODY
    assert kind.confidence == "high"
    assert kind.is_certainly(PUBLIC_BODY)


def test_a_bare_government_suffix_counts_too():
    assert site_kind(_snapshot("https://coastal-warnings.gov")).kind == PUBLIC_BODY


def test_an_education_suffix_is_read_the_same_way():
    kind = site_kind(_snapshot("https://physics.ac.zz"))
    assert kind.kind == PERSONAL_OR_ACADEMIC
    assert kind.confidence == "high"


def test_an_ordinary_suffix_decides_nothing_by_itself():
    """`.org` is sold to anyone, so it is not evidence and is deliberately not
    read as any. A charity, a standards body and a shop all use it."""
    assert site_kind(_snapshot("https://anyone.example")).kind == UNDETERMINED


def test_a_command_line_tool_is_a_project_not_a_company():
    """The site that was told to publish its founding year and its service
    area. It is a program: a repository, a licence, a manual, and none of the
    things a company has."""
    kind = site_kind(_snapshot("https://jqlike.example", [
        _page("https://jqlike.example/", "home",
              text="A lightweight command-line JSON processor. "
                   "Licensed under the Apache License.",
              external=["https://github.com/invented-owner/jqlike"]),
        _page("https://jqlike.example/manual", "documentation"),
        _page("https://jqlike.example/tutorial", "documentation"),
    ]))
    assert kind.kind == PROJECT
    assert kind.is_certainly(PROJECT)


def test_a_company_that_publishes_a_repository_is_not_a_project():
    """The rule must not swallow every software company. What separates them is
    everything a company also has - here, a careers page."""
    kind = site_kind(_snapshot("https://vendor.example", [
        _page("https://vendor.example/", "home",
              text="Licensed under the Apache License.",
              external=["https://github.com/invented-owner/sdk"]),
        _page("https://vendor.example/careers", "careers"),
    ]))
    assert kind.kind != PROJECT


def test_a_team_page_alone_stops_the_project_rule():
    kind = site_kind(_snapshot("https://vendor.example", [
        _page("https://vendor.example/", "home",
              text="Released under the MIT License.",
              external=["https://github.com/invented-owner/sdk"],
              internal=["https://vendor.example/about/team"]),
        _page("https://vendor.example/docs", "documentation"),
    ]))
    assert kind.kind != PROJECT


def test_a_declared_place_to_visit_is_a_local_business():
    kind = site_kind(_snapshot("https://cafe.example", [
        dict(_page("https://cafe.example/", "home"),
             jsonld=[{"@type": "Restaurant",
                      "address": {"@type": "PostalAddress",
                                  "streetAddress": "1 Invented Lane",
                                  "postalCode": "AB1 2CD"}}]),
    ]))
    assert kind.kind == LOCAL_BUSINESS
    assert kind.confidence == "high"


def test_a_shop_with_nowhere_to_visit_is_an_online_seller():
    kind = site_kind(_snapshot("https://store.example", [
        _page("https://store.example/", "home",
              internal=["https://store.example/cart"]),
        _page("https://store.example/products/mug", "product"),
    ]))
    assert kind.kind == ONLINE_SELLER


def test_a_site_whose_output_is_articles_is_a_publication():
    kind = site_kind(_snapshot("https://reports.example", [
        _page("https://reports.example/a", "article"),
        _page("https://reports.example/b", "article"),
        _page("https://reports.example/c", "article"),
        _page("https://reports.example/d", "article"),
        _page("https://reports.example/about", "about"),
    ]))
    assert kind.kind == PUBLICATION


def test_a_body_with_people_and_no_shop_is_an_organisation():
    kind = site_kind(_snapshot("https://society.example", [
        _page("https://society.example/", "home"),
        _page("https://society.example/jobs", "careers"),
    ]))
    assert kind.kind == ORGANISATION


def test_nothing_decisive_means_undetermined():
    kind = site_kind(_snapshot("https://quiet.example", [
        _page("https://quiet.example/", "home"),
    ]))
    assert kind.kind == UNDETERMINED
    assert not kind.determined


# --------------------------------------------------------------------------
# The gate: an undetermined kind must not silence anything
# --------------------------------------------------------------------------

def test_an_undetermined_kind_gates_nothing():
    """This is the whole safety property. Guessing wrong produced the
    failures, so a classifier that cannot decide has to leave every check
    exactly as it was."""
    kind = site_kind(_snapshot("https://quiet.example", [
        _page("https://quiet.example/", "home"),
    ]))
    for value in SITE_KINDS:
        assert kind.might_be(value), (
            "an undetermined kind answered no to {}, which would delete advice "
            "rather than tailor it".format(value))
    assert not kind.is_certainly(*SITE_KINDS)


def test_a_low_confidence_kind_also_gates_nothing():
    from audit_common import SiteKind
    weak = SiteKind(PROJECT, "low", ["a guess"])
    assert weak.might_be(LOCAL_BUSINESS)
    assert not weak.is_certainly(PROJECT)


def test_a_confident_kind_is_what_closes_the_gate():
    """The founding-year, service-area and opening-hours advice reads
    `might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION)`; on a program that
    has to come back False, and that is the only case where anything is
    suppressed."""
    kind = site_kind(_snapshot("https://jqlike.example", [
        _page("https://jqlike.example/", "home",
              text="Licensed under the Apache License.",
              external=["https://github.com/invented-owner/jqlike"]),
        _page("https://jqlike.example/manual", "documentation"),
    ]))
    assert not kind.might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION)
    assert kind.might_be(PROJECT)


def test_the_vocabulary_is_closed():
    """A kind invented at a call site would silently gate nothing."""
    assert len(set(SITE_KINDS)) == len(SITE_KINDS)
    assert UNDETERMINED not in SITE_KINDS
    for snapshot in (
        _snapshot("https://met-service.gov.zz"),
        _snapshot("https://physics.ac.zz"),
        _snapshot("https://quiet.example", [_page("https://quiet.example/", "home")]),
    ):
        decided = site_kind(snapshot)
        assert decided.kind in SITE_KINDS or decided.kind == UNDETERMINED


def test_every_kind_carries_the_evidence_it_was_decided_on():
    kind = site_kind(_snapshot("https://met-service.gov.zz"))
    assert kind.signals and kind.why()


# --------------------------------------------------------------------------
# Part 2a: one page, one identity
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "ref", "utm_source", "utm_campaign", "fbclid", "gclid", "msclkid",
    "variant", "referrer", "source",
])
def test_a_navigation_source_parameter_is_recognised(name):
    assert is_tracking_parameter(name)


@pytest.mark.parametrize("name", ["q", "page", "id", "lang", "sort", "query"])
def test_a_parameter_that_chooses_content_is_left_alone(name):
    """Dropping one of these would merge two different pages into one and hide
    whichever lost, which is worse than the page slot a stray parameter costs."""
    assert not is_tracking_parameter(name)


def test_the_content_parameter_survives_and_the_tracking_one_does_not():
    assert strip_tracking_parameters(
        "https://acme.example/search?q=mugs&utm_source=news&page=2"
    ) == "https://acme.example/search?q=mugs&page=2"


def test_three_menu_variants_of_one_address_are_one_page():
    """The measured failure: `?ref=nav-topALTM`, `?ref=nav-topM` and
    `?ref=nav-topSLW`, three menu items pointing at one destination, crawled as
    three pages and reported as three pages sharing a title."""
    keys = {page_identity("https://acme.example/?ref=nav-top" + tail)
            for tail in ("ALTM", "M", "SLW")}
    assert len(keys) == 1
    assert keys == {page_identity("https://acme.example/")}


def test_the_host_case_and_the_www_prefix_do_not_make_a_second_page():
    assert page_identity("https://WWW.Acme.Example/thing") == \
        page_identity("https://acme.example/thing")


def test_a_canonical_the_crawl_never_reached_still_folds_its_own_group():
    """The general fix. The old rule folded a page only onto a canonical the
    crawl had itself fetched, so three tagged variants of one address - none of
    them the bare address they all named - folded not at all."""
    pages = [{"url": "https://acme.example/?ref=nav-topALTM",
              "canonical": "https://acme.example/"},
             {"url": "https://acme.example/?ref=nav-topM",
              "canonical": "https://acme.example/"},
             {"url": "https://acme.example/?ref=nav-topSLW",
              "canonical": "https://acme.example/"}]
    assert len(fold_declared_duplicates(pages)) == 1


def test_folding_a_group_never_empties_it():
    """What the old rule was protecting: a site that mass-declares a canonical
    nobody has read must keep its pages. Folding onto a group member cannot
    hide a group, because every group keeps one."""
    pages = [{"url": "https://acme.example/a", "canonical": "https://acme.example/x"},
             {"url": "https://acme.example/b", "canonical": "https://acme.example/y"},
             {"url": "https://acme.example/c", "canonical": ""}]
    assert len(fold_declared_duplicates(pages)) == 3


def test_the_canonical_target_is_the_entry_that_survives():
    pages = [{"url": "https://acme.example/thing/", "canonical": "https://acme.example/thing"},
             {"url": "https://acme.example/thing", "canonical": "https://acme.example/thing"}]
    assert [p["url"] for p in fold_declared_duplicates(pages)] == \
        ["https://acme.example/thing"]


def test_two_tagged_variants_fold_with_no_canonical_at_all():
    """No reading of `rel=canonical` could settle this one, because neither
    page declares one."""
    pages = [{"url": "https://acme.example/x?utm_source=news", "canonical": ""},
             {"url": "https://acme.example/x?utm_source=mail", "canonical": ""}]
    assert len(fold_declared_duplicates(pages)) == 1


def test_two_genuinely_different_pages_are_still_two():
    pages = [{"url": "https://acme.example/search?q=mugs", "canonical": ""},
             {"url": "https://acme.example/search?q=plates", "canonical": ""}]
    assert len(fold_declared_duplicates(pages)) == 2


# --------------------------------------------------------------------------
# Part 2b: a hosted platform's address conventions
# --------------------------------------------------------------------------

_ITEM_TEXT = ("Walmgate mug. 18.00. In stock. Free shipping over 75. "
              "Add to cart. Ships within 3 working days.")


def _meta(text="", jsonld_types=(), headings=None):
    return {"text": text, "jsonld_types": list(jsonld_types),
            "headings": headings or {}, "title": ""}


def test_a_collection_handle_is_a_listing_not_an_item():
    """On the commonest hosted store platform `/products/<handle>` is the item
    and `/collections/<handle>` is the shelf. The shelf renders its items'
    prices and quick-buy buttons, which is exactly what the product-detail
    reading measures - so a collection of four jackets was typed `product` and
    the report said the product page carried no Product markup."""
    assert detect_page_type("https://store.example/collections/jackets",
                            _meta(_ITEM_TEXT)) == "category"


def test_the_item_handle_is_still_an_item():
    assert detect_page_type("https://store.example/products/walmgate-mug",
                            _meta(_ITEM_TEXT)) == "product"


def test_an_item_addressed_inside_a_collection_is_still_the_item():
    assert detect_page_type("https://store.example/collections/all/products/mug",
                            _meta(_ITEM_TEXT)) == "product"


def test_a_tag_archive_is_a_listing_not_an_article():
    """`/blogs/<blog>/tagged/<tag>` was typed `article` because `blogs` is an
    article slug and the path is too deep for the section-root rule. The fix
    offered was Article markup with a headline and a publication date for a
    page that is a list of other pages' headlines."""
    assert detect_page_type("https://store.example/blogs/news/tagged/coffee",
                            _meta("Coffee")) == "category"


@pytest.mark.parametrize("path", [
    "/collections/jackets", "/category/news", "/tag/coffee",
    "/blogs/news/tagged/coffee", "/author/invented-writer", "/blog/page/2",
    "/catalogue/mugs", "/topics/climate",
])
def test_every_grouping_word_names_a_listing(path):
    assert is_faceted_listing("https://acme.example" + path)


@pytest.mark.parametrize("path", [
    "/products/walmgate-mug", "/about/team", "/blogs/news",
    "/collections/all/products/mug", "/services/roofing",
])
def test_an_ordinary_page_is_not_read_as_a_listing(path):
    assert not is_faceted_listing("https://acme.example" + path)


# --------------------------------------------------------------------------
# Part 2c: the brand, or the platform it is built on
# --------------------------------------------------------------------------

def test_a_vendors_front_door_is_not_an_account():
    """The four wrong entries were all of this shape: an origin with nothing
    after the slash. A company's home page is not a profile on it."""
    for url in ("https://storefront-vendor.example",
                "https://storefront-vendor.example/",
                "https://protocol-spec.example/?utm_source=footer"):
        assert not looks_like_an_account(url)


def test_a_handle_inside_a_platform_is_an_account():
    assert looks_like_an_account("https://linkedin.com/company/invented-brand")
    assert looks_like_an_account("https://github.com/invented-brand")


@pytest.mark.parametrize("url", [
    "https://cdn.storefront-vendor.example/files/theme.js",
    "https://assets.storefront-vendor.example/x",
    "https://analytics.some-vendor.example/collect",
    "https://storefront-vendor.example/assets/storefront.js",
    "https://widget.review-vendor.example/loader.js",
])
def test_infrastructure_is_recognised_by_its_role_not_its_name(url):
    assert is_infrastructure_url(url)


def test_a_profile_url_is_not_mistaken_for_infrastructure():
    assert not is_infrastructure_url("https://linkedin.com/company/invented-brand")


def test_a_host_the_site_only_loads_scripts_from_is_not_a_profile():
    """The strongest reading, and the one no URL supplies on its own: the
    extractor already records these per page as `scripts.third_party_hosts`."""
    hosts = ["cdn.storefront-vendor.example", "storefront-vendor.example"]
    assert not counts_as_off_site_profile(
        "https://storefront-vendor.example/merchants/invented-brand",
        subresource_hosts=hosts)


def test_a_declaration_does_not_turn_a_script_host_into_a_profile():
    """How the measured failure arrived: the site's own agent file naming the
    platform it runs on. A declaration is evidence of identity, not evidence
    that a machine host is somewhere the brand keeps an account."""
    assert not counts_as_off_site_profile(
        "https://cdn.storefront-vendor.example/",
        declared=True, subresource_hosts=["cdn.storefront-vendor.example"])


def test_a_declared_subdomain_account_still_counts():
    """A hosted platform that gives each customer a subdomain leaves no path to
    read, so the declaration is the whole of the evidence - and dropping it
    would cost real profiles to fix a false one."""
    assert counts_as_off_site_profile(
        "https://invented-brand.newsletter-platform.example/", declared=True)


def test_a_real_profile_still_counts():
    assert counts_as_off_site_profile("https://linkedin.com/company/invented-brand")


def test_the_four_wrong_entries_all_fall_out():
    """Reconstructed by shape, not by name: a platform's own site, its
    developer site, a protocol site and a demonstration store, each reached
    only from a script tag. The count read 8 where the truth was 3, which is
    the difference between a pass and a finding on the one measure this
    marketplace calls its strongest."""
    script_hosts = ["storefront-vendor.example", "developers.storefront-vendor.example",
                    "protocol-spec.example", "demo-store.example"]
    wrong = ["https://storefront-vendor.example",
             "https://developers.storefront-vendor.example",
             "https://protocol-spec.example",
             "https://demo-store.example"]
    right = ["https://linkedin.com/company/invented-brand",
             "https://instagram.com/inventedbrand",
             "https://github.com/invented-brand"]
    counted = [u for u in wrong + right
               if counts_as_off_site_profile(u, subresource_hosts=script_hosts)]
    assert counted == right


# --------------------------------------------------------------------------
# What a registry suffix says, and where it stops saying it
# --------------------------------------------------------------------------
#
# A Japanese city of 1.6 million people was classed as a business and told to
# "claim the company record too - a business database entry and an
# employer profile". One label was missing: `lg` is Japan's label for local
# government and every city, town and village in the country sits under it.
#
# The rule was right and the list under it was written from whichever countries
# this audit had happened to meet. That is a failure to generalise, so the list
# was widened by reading registries rather than by waiting for the next site to
# break it - and widening it immediately produced the opposite fault, which the
# second half of this section holds.
#
# Every host below is invented. Only the suffix is real, and a suffix is not a
# site.

@pytest.mark.parametrize("host", [
    "www.city-of-harrowfen.lg.jp",      # Japanese local government
    "records.nic.in",                   # India's National Informatics Centre
    "finanz.bmk-office.gv.at",          # Austria
    "supply.mod.uk",                    # United Kingdom, defence
    "www.parliament.uk",                # and its legislature
    "tribunal-do-vale.jus.br",          # Brazil, judiciary
    "camara-nordeste.leg.br",           # Brazil, legislature
    "presidencia.gub.uy",               # Uruguay
    "trust-clinic.nhs.uk",              # a health service
    "westmarch.police.uk",              # a force
])
def test_a_registry_suffix_nobody_can_buy_makes_it_a_public_body(host):
    """Held as labels rather than as domains: a role label, then a two-letter
    country. That covers the national spellings this audit has met and the ones
    it has not, without a list of countries and without naming a real site."""
    assert _registry_suffix_kind(host) == PUBLIC_BODY, host


@pytest.mark.parametrize("host", [
    "minato-daini-es.ed.jp",            # a Japanese school
    "institute.re.kr",                  # a Korean research institute
    "chemical-labs.res.in",             # an Indian one
    "st-brannocks.sch.uk",              # a British school
    "clare-hall.ac.uk",                 # a British university
])
def test_the_academic_suffixes_are_read_the_same_way(host):
    assert _registry_suffix_kind(host) == PERSONAL_OR_ACADEMIC, host


@pytest.mark.parametrize("host", [
    "brackwater-supplies.re",           # Reunion's country code, on sale
    "nic.example.test",                 # a label in the wrong position
    "mod-supplies.zz",                  # part of a name, not a label
    "helloworld.uni",                   # a generic TLD anyone may register
    "listen.public",                    # likewise
    "brackwater.admin",                 # likewise
    "leggings.store",                   # an ordinary shop
])
def test_a_label_that_is_not_a_registry_suffix_settles_nothing(host):
    """The fault widening the list produced, and the reason for
    `_REGISTRY_TOP_LEVEL`. `re` is Korea's research label **and** Reunion's
    country code, so reading role labels in final position turned every name
    registered under `.re` into a research institute. A label that is not a
    top-level domain is only evidence in the middle of an address."""
    assert _registry_suffix_kind(host) == "", host


def test_the_four_labels_that_are_top_level_domains_still_work_alone():
    """`gov`, `mil`, `edu` and `int` are registries in their own right, so they
    are the four that mean something with nothing after them."""
    assert _registry_suffix_kind("coastal-warnings.gov") == PUBLIC_BODY
    assert _registry_suffix_kind("northern-fleet.mil") == PUBLIC_BODY
    assert _registry_suffix_kind("brackwater-college.edu") == PERSONAL_OR_ACADEMIC
    assert _registry_suffix_kind("maritime-treaty.int") == PUBLIC_BODY


def test_every_label_that_is_not_a_top_level_domain_is_declared_as_one():
    """`_REGISTRY_TOP_LEVEL` has to stay a subset of the two role lists, or a
    label is allowed in final position that neither list has ever seen."""
    assert _REGISTRY_TOP_LEVEL <= (_PUBLIC_LABELS | _ACADEMIC_LABELS)
    assert _PUBLIC_LABELS.isdisjoint(_ACADEMIC_LABELS), (
        "a label reading as both a public body and a school decides nothing")
