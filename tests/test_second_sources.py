# -*- coding: utf-8 -*-
"""The second place each thing can live, and the check that now looks there.

`test_absence_claims.py` holds the rule: a finding asserting a negative names
what it consulted. This file holds the consequences, one case per source that
was added because a real site kept its facts there and the audit called them
missing.

Each test is a page that is FINE. If one starts failing, a check has gone back
to reading a single place and calling what it cannot see absent.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT

sys.path.insert(0, os.path.join(ROOT, "skills", "audit-orchestrator", "scripts"))

from audit_common import make_soup  # noqa: E402
from page_extract import _described_some_other_way, _images, _typed_markup  # noqa: E402


def _skill(name):
    scripts = os.path.join(ROOT, "skills", name, "scripts")
    sys.path.insert(0, scripts)
    spec = importlib.util.spec_from_file_location(
        "{}_second_sources".format(name.replace("-", "_")),
        os.path.join(scripts, "check.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


structured = _skill("structured-data-audit")


# --------------------------------------------------------------------------
# Inline markup is markup
#
# A site that states its facts as Microdata or RDFa has stated them. Reading
# JSON-LD alone left five schema checks with one detector each, and a site
# using a CMS that emits `itemtype` attributes was told at high confidence
# that it carries no markup at all.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("html,expected", [
    ('<div itemscope itemtype="https://schema.org/Restaurant"></div>', "restaurant"),
    ('<div itemscope itemtype="http://schema.org/Product"></div>', "product"),
    ('<div typeof="Organization"></div>', "organization"),
    ('<div typeof="schema:LocalBusiness"></div>', "localbusiness"),
])
def test_inline_schema_types_are_read(html, expected):
    assert expected in _typed_markup(make_soup(html))


def test_open_graph_tags_are_not_mistaken_for_rdfa():
    """`[property]` matches every `<meta property="og:...">`, so the RDFa
    signal was above zero on any page carrying a social card and could never
    tell RDFa from Open Graph."""
    soup = make_soup('<meta property="og:title" content="t">')
    assert not _typed_markup(soup)


def test_a_page_using_microdata_is_not_told_it_has_no_product_markup():
    """And a page carrying neither notation still reports the gap, or the
    second source would have silenced the check."""
    page = {"url": "https://example.test/p/mug", "page_type": "product",
            "jsonld": [], "jsonld_types": [],
            "microdata_types": ["product", "offer"]}
    assert structured._declares_type(page, structured.PRODUCT_TYPES)
    assert not structured._declares_type(dict(page, microdata_types=[]),
                                         structured.PRODUCT_TYPES)


# --------------------------------------------------------------------------
# A picture can be described by something other than `alt`
# --------------------------------------------------------------------------

@pytest.mark.parametrize("html", [
    '<img src="a.png" aria-label="Sales by quarter">',
    '<figure><img src="a.png"><figcaption>Sales by quarter</figcaption></figure>',
    '<img src="a.png" role="presentation">',
])
def test_an_image_described_another_way_is_described(html):
    tag = make_soup(html).find("img")
    assert _described_some_other_way(tag)


def test_an_image_with_nothing_at_all_is_not():
    tag = make_soup('<img src="a.png">').find("img")
    assert not _described_some_other_way(tag)


def test_an_alt_attribute_bound_by_a_template_is_an_alt_attribute():
    """51 images "with no alt attribute" were three components binding
    `:alt="product.title"`, whose src is a JavaScript expression."""
    from page_extract import _declares_alt_dynamically
    bound = make_soup('<img :alt="p.title" src="x">').find("img")
    plain = make_soup('<img src="x">').find("img")
    assert _declares_alt_dynamically(bound)
    assert not _declares_alt_dynamically(plain)


def test_the_undescribed_count_excludes_images_named_elsewhere():
    """`missing_alt_count` counts a missing attribute. It cannot tell an image
    nobody describes from one described by its caption, so the missing-alt
    check had one detector and no way to be corroborated."""
    html = ('<img src="a.png">'
            '<figure><img src="b.png"><figcaption>Quarterly sales</figcaption></figure>'
            '<img src="c.png" aria-label="Logo">')
    images = _images(make_soup(html), "https://example.test/")
    assert images["missing_alt_count"] == 3
    assert images["undescribed_count"] == 1


# --------------------------------------------------------------------------
# A name is a name however the page spells it
#
# One comparison, shared. Each row below is a real site that was told its
# markup contradicts its own page.
# --------------------------------------------------------------------------

def test_a_name_written_another_way_is_the_same_name():
    """A trading name, and a plural where the page writes a singular. Each row
    is a real site that was told its markup contradicts its own page - and a
    name that really is different still has to come back as a contradiction."""
    assert structured._name_is_on_the_page(
        "Acme Holdings Group", "Acme | Wheel-thrown stoneware from York", ["Acme"])

    haystack = "Coppergate | Ceramic Studio York | Handmade tableware"
    assert structured._name_is_on_the_page("Coppergate Ceramic Studios", haystack)
    assert not structured._name_is_on_the_page("Northwind Traders", haystack)
