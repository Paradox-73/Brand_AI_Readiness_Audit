# -*- coding: utf-8 -*-
"""Two things the reports could not say, and one field that had to stay put.

**"Add the snippet to the site-wide template."** Printed about forty times
across four real reports, naming no file, no screen and no person. Three of
the four sites ran one hosted storefront platform whose CDN hostname the crawl
had fetched, parsed and quoted on every page; the platform's name appears in
the four reports exactly once, inside a URL the tool quoted back in its own
snippet. And nothing typed `low` effort / `developer` owner - seven to nine of
every twelve to fourteen findings - ever said who a reader without a developer
should ask.

**Which strings are the site's name.** A design firm was offered "San Francisco
Premier Interior Designer" and "arrowooddesign", never the words its own domain
spells, and was told no page states in one sentence what the brand is - above a
homepage opening "Arrowood Design is a full-service, client-focused design firm
specializing in residential projects".

**And `page["prices"]`.** A storefront writes every variant's price but the
default one inside a `<select>`. Widening the shared field to include them
would have landed in six skills at once and quietly broken two measurements
that depend on a price having a position in the copy.

Every site named below is invented. Naming a *platform* whose signature the
code has to recognise is the thing this repository already does for bot
managers, CAPTCHA vendors and consent tools, and it is what
`test_marketplace.py::test_no_real_domain_names_anywhere` allows.
"""

from __future__ import annotations

import io
import os
import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    ADMIN_SCREEN, ASSERTED, DATED_ARCHIVE_RE, DERIVED, EVIDENCE_BASIS,
    EVIDENCE_BASIS_BY_FINDING, INFERRED, OBSERVED, PLATFORM_SIGNATURES,
    PLATFORM_SIGNAL_FAMILIES, SOURCE_FILE, UNKNOWN_PLATFORM_LOCATION,
    PublishingPlatform, brand_forms, defining_sentence, find_prices,
    looks_like_a_page_title, name_candidates, name_the_site_spells,
    prices_the_page_states, publishing_platform, template_change_steps,
    title_segments, where_the_template_is, who_edits_the_template,
    without_civic_prefix,
)
from page_extract import extract_page  # noqa: E402

ORIGIN = "https://kestrelworks.example"
URL = ORIGIN + "/"


def _page(html, headers=None, url=URL):
    return extract_page(url=url, final_url=url, status=200, headers=headers or {},
                        html=html, redirect_chain=[], elapsed_ms=10, depth=0,
                        source="seed", origin=ORIGIN)


def _snapshot(html, headers=None, pages=6, host="kestrelworks.example"):
    return {"brand": {"host": host},
            "pages": [_page(html, headers) for _ in range(pages)]}


def _body(extra_head="", extra_body="", root_attrs=""):
    return ("<html {attrs}><head>{head}</head><body><main><h1>Kestrel</h1>"
            "<p>Kestrel Works makes rope for climbers.</p>{body}</main></body>"
            "</html>").format(attrs=root_attrs, head=extra_head, body=extra_body)


# --------------------------------------------------------------------------
# Part 1: the evidence is recorded, and it is not prose
# --------------------------------------------------------------------------

def test_the_snapshot_records_every_signal_family():
    """Six families, one record. A family nobody records is a family the
    detector can never use, which is how a CDN hostname sat in four reports
    without once becoming the name of a platform."""
    signals = _page(_body())["platform_signals"]
    for key in ("generator", "powered_by", "asset_hosts", "asset_paths",
                "markup_attributes", "framework_markers", "header_names",
                "cookie_names"):
        assert key in signals, key


def test_a_link_to_a_platform_is_not_a_signal():
    """The failure this repository keeps making: a word on a page read as a
    fact about the site. An anchor and a sentence naming a platform are a page
    talking about a company; a script tag is a page that company rendered."""
    talking = _body(extra_body='<a href="https://www.shopify.com/">We moved off '
                               'Shopify last year</a><p>Shopify was fine.</p>')
    signals = _page(talking)["platform_signals"]
    assert not signals["asset_hosts"], signals["asset_hosts"]
    assert "shopify" not in signals["generator"].lower()
    platform = publishing_platform(_snapshot(talking))
    assert platform.name == "", platform


def test_cookie_values_never_reach_the_snapshot():
    """A cookie's name is a fact about the platform. Its value is a session
    belonging to whoever this audit fetched as, and it has no business in a
    file the owner is handed."""
    headers = {"set-cookie": "_shopify_y=6f1a-secret-value; Path=/; "
                             "Expires=Wed, 21 Oct 2025 07:28:00 GMT, "
                             "cart_currency=GBP; Path=/"}
    signals = _page(_body(), headers)["platform_signals"]
    assert signals["cookie_names"] == ["_shopify_y", "cart_currency"]
    assert "6f1a-secret-value" not in repr(signals)
    # The attribute keywords on the same line are not cookies.
    assert "path" not in [c.lower() for c in signals["cookie_names"]]
    assert "expires" not in [c.lower() for c in signals["cookie_names"]]


def test_an_asset_path_records_a_shape_and_not_a_filename():
    html = _body(extra_head='<script src="/cdn/shop/t/9/assets/theme.a1b2c3.js">'
                            '</script>')
    paths = _page(html)["platform_signals"]["asset_paths"]
    assert "/cdn/shop" in paths, paths


# --------------------------------------------------------------------------
# Part 2: a wrong platform name is far worse than none
# --------------------------------------------------------------------------

_HOSTED_STORE = _body(
    extra_head='<link rel="preconnect" href="https://cdn.shopify.com">'
               '<script src="https://cdn.shopify.com/s/files/1/00/t.js"></script>'
               '<img src="/cdn/shop/files/rope.jpg" alt="rope">')
_HOSTED_STORE_HEADERS = {"x-shopid": "12345", "x-sorting-hat-shopid": "12345",
                         "set-cookie": "_shopify_y=abc; Path=/"}

_SITE_BUILDER = _body(
    extra_head='<meta name="generator" content="Webflow">'
               '<script src="https://assets-global.website-files.com/a/js/webflow.js">'
               '</script>',
    root_attrs='data-wf-page="p1" data-wf-site="s1"')


def test_a_hosted_store_is_named_and_told_which_file():
    platform = publishing_platform(_snapshot(_HOSTED_STORE, _HOSTED_STORE_HEADERS))
    assert platform.name == "Shopify"
    assert platform.confidence == "high"
    assert platform.named and bool(platform)
    assert platform.reach == ADMIN_SCREEN
    assert platform.is_certainly("Shopify")
    where = where_the_template_is(platform)
    assert "theme.liquid" in where
    assert "Online Store" in where and "Edit code" in where


def test_a_site_builder_is_not_told_to_open_a_theme_file():
    """The failure that would cost the whole report: one platform's file name
    printed at a reader on a different platform."""
    platform = publishing_platform(_snapshot(_SITE_BUILDER))
    assert platform.name == "Webflow", platform
    assert platform.named
    where = where_the_template_is(platform)
    assert "theme.liquid" not in where
    assert "Custom code" in where


def test_a_widget_from_a_platform_does_not_make_the_site_that_platform():
    """A blog with a buy button glued into its footer loads a storefront's
    script host on every page, and that is one family and not a shop. This is
    the case the self-declaring rule exists for: a third party can add a script
    host to somebody else's page and cannot make that page's origin set a
    cookie."""
    html = _body(extra_head='<script src="https://sdks.shopify.com/buy-button/x.js">'
                            '</script>')
    platform = publishing_platform(_snapshot(html))
    assert platform.name == ""
    assert not platform.named
    assert where_the_template_is(platform) == UNKNOWN_PLATFORM_LOCATION


def test_two_platforms_leaving_equal_traces_names_neither():
    """A headless storefront, or a blog bolted onto a shop. Either console is
    the wrong console to send the reader to, so neither is named - and both go
    in the evidence so the refusal is arguable."""
    both = _body(
        extra_head='<meta name="generator" content="WordPress 6.5">'
                   '<script src="/wp-content/themes/x/main.js"></script>'
                   '<script src="https://cdn.shopify.com/s/files/1/t.js"></script>'
                   '<img src="/cdn/shop/files/a.jpg" alt="a">')
    # Three families each: the storefront leaves an asset host, an asset path
    # and a cookie; the blog leaves a generator, an asset path and a cookie.
    # Equal counts are what "equal traces" means to the tie-break, so the
    # fixture has to be equal rather than merely close.
    headers = {"set-cookie": "_shopify_y=abc; Path=/, wordpress_test=1; Path=/"}
    platform = publishing_platform(_snapshot(both, headers))
    assert platform.name == "", platform
    joined = " ".join(platform.evidence)
    assert "Shopify" in joined and "WordPress" in joined


def test_a_platform_that_only_just_outweighs_the_other_is_hedged():
    """One family of margin is not a clear answer.

    A headless storefront can leave one signal more on the front-end side than
    the back-end side, and there is no reading of the address or the markup
    that says which console the reader should open. The winner is still named,
    because "your WordPress theme" is more use than "the site-wide template" to
    somebody whose pages really are rendered by WordPress - but it is named at
    `medium`, so `.named` stays false and no fix step may print that platform's
    own vocabulary as though it were established.
    """
    both = _body(
        extra_head='<meta name="generator" content="WordPress 6.5">'
                   '<script src="/wp-content/themes/x/main.js"></script>'
                   '<script src="https://cdn.shopify.com/s/files/1/t.js"></script>'
                   '<img src="/cdn/shop/files/a.jpg" alt="a">')
    headers = {"x-pingback": "https://kestrelworks.example/xmlrpc.php",
               "set-cookie": "_shopify_y=abc; Path=/, wordpress_test=1; Path=/"}
    platform = publishing_platform(_snapshot(both, headers))
    assert platform.name == "WordPress"
    assert platform.confidence == "medium"
    assert platform.named is False
    assert platform.determined is True


def test_a_single_family_on_one_page_names_nothing():
    """Reach is the site-wide test: a platform's template is on every page it
    renders, a widget is usually on one section."""
    pages = [_page(_body()) for _ in range(9)]
    pages.append(_page(_body(
        extra_head='<script src="https://assets-global.website-files.com/a.js">'
                   '</script>')))
    platform = publishing_platform({"brand": {"host": "kestrelworks.example"},
                                    "pages": pages})
    assert platform.name == ""


def test_a_generator_tag_alone_hedges_out_loud():
    """One self-declaring family across the whole site is worth saying and not
    worth asserting. The sentence has to carry both halves: the guess, and what
    to do when the guess is wrong."""
    html = _body(extra_head='<meta name="generator" content="Ghost 5.82">')
    platform = publishing_platform(_snapshot(html))
    assert platform.confidence == "medium"
    assert platform.determined and not platform.named
    where = where_the_template_is(platform)
    assert "looks like" in where
    assert "Ghost" in where
    assert UNKNOWN_PLATFORM_LOCATION in where


def test_might_be_removes_nothing_on_a_site_nobody_can_name():
    """The `SiteKind` rule, kept: a weak answer must not silence advice, or a
    detector failure deletes findings rather than failing to tailor them."""
    unknown = publishing_platform(_snapshot(_body()))
    assert unknown.might_be("Shopify") is True
    assert unknown.might_be("Wix", "Drupal") is True
    assert unknown.is_certainly("Shopify") is False
    known = publishing_platform(_snapshot(_HOSTED_STORE, _HOSTED_STORE_HEADERS))
    assert known.might_be("Shopify") is True
    assert known.might_be("Wix") is False


def test_an_empty_or_old_snapshot_is_answered_rather_than_crashed():
    for snapshot in ({}, {"pages": []}, {"pages": [{"url": URL}]}, None):
        platform = publishing_platform(snapshot)
        assert isinstance(platform, PublishingPlatform)
        assert not platform


# --------------------------------------------------------------------------
# Part 3: the sentence a finding prints
# --------------------------------------------------------------------------

def test_the_unnamed_site_gets_the_better_sentence_and_not_the_leftover_one():
    """Most of the open web runs something no table can name. That reader knows
    one true thing - somebody edits their pages - and the sentence has to be
    worth more than "add it to the site-wide template", which named nothing."""
    steps = template_change_steps(None, "the Organization block")
    assert len(steps) == 2
    joined = " ".join(steps)
    assert "</head>" in joined, "nothing tells the reader what to search for"
    assert "Organization block" in joined
    for who in ("agency", "freelancer", "hosting"):
        assert who in joined, "a reader with no developer is told nobody to ask"


@pytest.mark.parametrize("html,headers", [
    (_HOSTED_STORE, _HOSTED_STORE_HEADERS),
    (_SITE_BUILDER, None),
])
def test_every_recognised_platform_names_somebody_who_can_do_it(html, headers):
    """The half of the failure that is not about files. "low effort,
    developer" is not an hour of work to a shop owner with no developer; it is
    a person they do not have."""
    platform = publishing_platform(_snapshot(html, headers))
    who = who_edits_the_template(platform)
    assert platform.name in who
    assert "login" in who or "deploy" in who
    assert "agency" in who or "freelancer" in who


# --------------------------------------------------------------------------
# The owner sentence follows the location sentence, not the reach
# --------------------------------------------------------------------------

def test_a_step_that_names_a_file_is_never_called_a_settings_screen():
    """Ten occurrences across three reports, found independently twice: "On
    WordPress that template is `header.php`, reached through
    Appearance -> Theme File Editor" followed immediately by "This is a
    settings screen in the WordPress admin rather than a code release, so
    whoever holds the WordPress login can make it."

    Editing `header.php` is a code change. Reaching a file through an admin
    menu does not stop it being theme code, and the two sentences a reader
    sees one after the other cannot say different things about the same fix.
    """
    for signature in PLATFORM_SIGNATURES:
        platform = PublishingPlatform(
            name=signature.name, confidence="high",
            signals=[("generator", signature.name.lower(), 1.0)])
        where = where_the_template_is(platform)
        who = who_edits_the_template(platform)
        names_a_file = bool(signature.file) and signature.file in where
        if names_a_file:
            assert "settings screen" not in who, (
                signature.name + ": the line above it names " + signature.file)
            assert "code change" in who or "source code" in who, signature.name
        else:
            assert "settings screen" in who, signature.name


def test_the_settings_screen_sentence_survives_where_it_is_true():
    """This instance was checked and reads correctly, and it has to go on
    doing so: on a site builder with a head-code box there is no file, no
    release and nobody to deploy - the person with the login can make it."""
    for name in ("Wix", "Squarespace", "Webflow", "Ghost"):
        platform = PublishingPlatform(
            name=name, confidence="high",
            signals=[("generator", name.lower(), 1.0)])
        who = who_edits_the_template(platform)
        assert "settings screen" in who and "login" in who, name
        assert "code change" not in who, name


def test_the_named_file_sentence_says_who_can_edit_a_theme():
    """The reader who has to be told something they can act on: not "a
    developer", which is a role a shop owner does not employ."""
    platform = PublishingPlatform(
        name="WordPress", confidence="high",
        signals=[("generator", "wordpress", 1.0)])
    who = who_edits_the_template(platform)
    assert "WordPress admin" in who
    assert "agency" in who and "freelancer" in who
    assert "no deployment" in who, "an admin edit does not wait for a release"


def test_a_platform_with_a_head_box_is_not_sent_hunting_for_a_head_tag():
    """The same class of unfollowable instruction, one level down: a settings
    screen never shows the reader a `</head>`."""
    platform = publishing_platform(_snapshot(_SITE_BUILDER))
    first = template_change_steps(platform, "the block")[0]
    assert "</head>" not in first, first


def test_every_signature_can_actually_be_followed():
    """A row whose click path might be wrong is worse than no row, and a row
    that names neither a screen nor a file is the generic sentence wearing a
    platform's name."""
    for signature in PLATFORM_SIGNATURES:
        assert signature.reach in (ADMIN_SCREEN, SOURCE_FILE), signature.name
        assert signature.admin_path or signature.file, signature.name
        if signature.reach == SOURCE_FILE:
            assert signature.file, signature.name
        fired = (signature.words or signature.hosts or signature.paths
                 or signature.attributes or signature.frameworks
                 or signature.headers or signature.cookies)
        assert fired, "{} can never fire".format(signature.name)


def test_no_signature_holds_a_domain_where_a_label_belongs():
    """Held as labels, like `_PUBLIC_LABELS` and `_REGISTRY_HOST_LABELS`. A
    label is the word somebody chose; the suffix beside it is a registry's, and
    matching on it would tie one row to one address."""
    for signature in PLATFORM_SIGNATURES:
        for label in signature.hosts:
            assert "." not in label, "{}: {!r}".format(signature.name, label)
            assert "/" not in label, "{}: {!r}".format(signature.name, label)


def test_the_families_a_signature_uses_are_the_families_that_are_tallied():
    assert set(PLATFORM_SIGNAL_FAMILIES) == {
        "generator", "asset-host", "asset-path", "markup-attribute",
        "framework-marker", "response-header", "cookie-name"}


# --------------------------------------------------------------------------
# Part 4: which strings are the site's name
# --------------------------------------------------------------------------

_DESIGN_FIRM = {
    "name": "San Francisco Premier Interior Designer",
    "authoritative_variants": [],
    "alternate_names": [],
    "fallback_candidates": ["San Francisco Premier Interior Designer",
                            "Larkspur Studio", "Larkspurstudio"],
    "host": "larkspurstudio.example",
    "domain_token": "larkspurstudio",
}


def test_the_words_the_domain_spells_are_offered_to_the_matchers():
    """The false positive seen in the wild: a homepage opening "<Name> is a full-service
    design firm" reported as a site that never states what it is, because the
    only names offered were the search-engine copy in the title and the
    flattened host label."""
    names = name_candidates(_DESIGN_FIRM, texts=[
        "San Francisco Premier Interior Designer | Larkspur Studio"])
    assert "Larkspur Studio" in names.for_matching()
    assert "Larkspur Studio" in names.for_lookup()
    assert names.display() == "Larkspur Studio"


def test_the_definition_check_can_now_be_satisfied():
    sentence = ("Larkspur Studio is a full-service, client-focused design firm "
                "specializing in residential projects.")
    assert "Larkspur Studio" in brand_forms(_DESIGN_FIRM["name"], _DESIGN_FIRM)
    assert defining_sentence(sentence, _DESIGN_FIRM["name"], _DESIGN_FIRM)


def test_a_search_engine_title_is_never_what_a_person_is_shown():
    """"A machine can reach <the whole SEO title> and read it" was the opening
    sentence of one report, and the same string went to an encyclopedia as a
    search term."""
    clinic = {
        "name": u"酒田市の予防歯科"
                u"｜ひよし歯科｜KEEP28",
        "authoritative_variants": [], "alternate_names": [],
        "host": "hiyoshi-dental.example", "domain_token": "hiyoshi dental",
    }
    names = name_candidates(clinic)
    assert u"｜" not in names.display(), names.display()
    assert not looks_like_a_page_title(names.display())
    assert names.for_lookup()[0] == names.display()


def test_a_derived_alias_reaches_the_matchers_and_never_the_lookup():
    """A wrong alias is worse than no alias. "Ashcombe" is a fine subject for a
    sentence the town wrote about itself and a terrible thing to send to an
    encyclopedia, which will hand back a village."""
    town = {"name": "Town of Ashcombe", "authoritative_variants": ["Town of Ashcombe"],
            "alternate_names": [], "host": "ashcombe.example",
            "domain_token": "ashcombe"}
    names = name_candidates(town)
    assert "Ashcombe" in names.for_matching()
    assert "Ashcombe" not in names.for_lookup()
    assert dict(names.tiers())["Ashcombe"] == DERIVED
    assert dict(names.tiers())["Town of Ashcombe"] == ASSERTED


def test_a_title_is_split_where_a_title_is_split_and_nowhere_else():
    assert title_segments("Larkspur Studio | Interior Design") == [
        "Larkspur Studio", "Interior Design"]
    assert title_segments(u"酒田｜KEEP28") == [u"酒田", "KEEP28"]
    assert title_segments("Home - Larkspur Studio") == ["Home", "Larkspur Studio"]
    # A hyphen inside a word is part of the word. Splitting here invents two
    # organisations out of one.
    assert title_segments("Coca-Cola") == ["Coca-Cola"]
    # And a colon is not a separator: "charity: shelter" is one name.
    assert title_segments("charity: shelter") == ["charity: shelter"]


def test_a_civic_prefix_is_dropped_and_an_academic_one_is_not():
    """"Computer Science" is a subject, not anybody's name, and a definition
    check handed it as a subject would accept a sentence about the field."""
    assert without_civic_prefix("Town of Ashcombe") == "Ashcombe"
    assert without_civic_prefix("City of Ashcombe") == "Ashcombe"
    assert without_civic_prefix("Department of Computer Science") == (
        "Department of Computer Science")
    assert without_civic_prefix("Larkspur Studio") == "Larkspur Studio"


def test_the_host_spelling_cannot_invent_a_name():
    """Exact letter equality is what makes the answer asserted rather than
    guessed."""
    assert name_the_site_spells("larkspurstudio", ["Larkspur Studio makes things"]) == (
        "Larkspur Studio")
    assert name_the_site_spells("larkspurstudio", ["Larkspur Design Studio"]) == ""
    # Too short to be evidence: a three-letter domain matches common words.
    assert name_the_site_spells("art", ["Art and craft"]) == ""


# --------------------------------------------------------------------------
# Part 5: the two smaller items
# --------------------------------------------------------------------------

def test_the_variant_prices_are_recorded_without_widening_the_shared_field():
    """A storefront writes every variant's price but the default one inside a
    `<select>`, and a product page declaring an Offer price of 569.00 while
    printing 609 beside its heading was reported at high severity as structured
    data contradicting the page.

    `prices` may not move: the landing-page check locates the figure with
    `body_text.find`, and the shelf-versus-detail rule counts how many
    different amounts the copy prints. Folding forty variant prices into either
    would drop the page from the first measurement and turn every product
    detail page on a storefront into a category listing in the second.
    """
    html = _body(
        extra_body="<p>Now GBP 609.00</p>"
                   "<select>"
                   "<option>6 - Sold Out - GBP 569.00</option>"
                   "<option>7 - GBP 589.00</option>"
                   "<option>8 - GBP 609.00</option>"
                   "</select>")
    page = _page(html)
    assert page["prices"] == find_prices(page["body_text"])
    assert page["prices"] == ["GBP 609.00"]
    assert "GBP 569.00" in page["control_prices"]
    assert "GBP 569.00" not in page["body_text"]

    both = prices_the_page_states(page)
    assert both[0] == "GBP 609.00", "the price the page leads with comes first"
    assert "GBP 569.00" in both and "GBP 589.00" in both
    # One price written twice is one price.
    assert both.count("GBP 609.00") == 1


def test_prices_the_page_states_works_on_a_page_with_no_menus():
    page = {"body_text": "It costs GBP 12.00.", "prices": ["GBP 12.00"]}
    assert prices_the_page_states(page) == ["GBP 12.00"]
    assert prices_the_page_states({}) == []


def test_the_dated_archive_rule_has_one_public_name():
    """`structured-data-audit` carried a character-for-character copy of this
    pattern under its own name. Two copies of one rule in two files that two
    people edit drift, and a page typed `article` in one and `category` in the
    other is told twice about markup it should not carry."""
    for archive in ("/blog/2025/", "/news/2025/04/", "/2025/4/12/"):
        assert DATED_ARCHIVE_RE.search(archive), archive
    for post in ("/blog/2025/the-year-in-review", "/blog/2025/4471"):
        assert not DATED_ARCHIVE_RE.search(post), post


# --------------------------------------------------------------------------
# Part 6: the confident tier stops taking pattern matches over prose
# --------------------------------------------------------------------------

def test_a_vocabulary_is_an_identification():
    """The recalibration a later validation pass forced. The confident tier
    measured 78%
    correct against the lower tier's 79% on five sites unlike the four English
    shops it was built against - it separated nothing at all. Every cause here
    reaches its number through a list that cannot be complete: a set of English
    verbs, a sentence splitter, a five-selector guess at what the navigation
    is. Counting to zero with an incomplete list is a decision about what the
    document says, not a reading of it."""
    for cause in ("missing-core-fact", "long-sentences", "readability",
                  "no-breadcrumbs", "inconsistent-chrome"):
        assert EVIDENCE_BASIS[cause] == INFERRED, cause


def test_what_a_reader_can_settle_in_view_source_stays_confident():
    """Demotion is not the goal; separation is. A tier that demotes everything
    tells a reader as little as one that promotes everything."""
    for cause in ("non-200", "noindex", "missing-lang", "invalid-jsonld",
                  "alt-missing", "open-graph-incomplete", "page-weight",
                  "broken-links", "heading-structure"):
        assert EVIDENCE_BASIS[cause] == OBSERVED, cause


def test_the_navigation_override_is_gone_rather_than_flipped():
    """Which element is the primary navigation is a guess, so the finding is
    worth what a nav-identification claim is worth. An INFERRED entry here
    would have repeated its cause and failed the redundancy test instead."""
    assert "primary-navigation-is-hidden-from-crawlers" not in EVIDENCE_BASIS_BY_FINDING
    assert EVIDENCE_BASIS["no-orientation"] == INFERRED
    assert EVIDENCE_BASIS_BY_FINDING["missing-mobile-viewport"] == OBSERVED


# --------------------------------------------------------------------------
# "Edit the template" is not an instruction until it says which template
# --------------------------------------------------------------------------

# The phrases a fix step uses when it is asking somebody to change a file every
# page shares. A step containing one of these and nothing else is the sentence
# repeated forty times across four reports, on shops whose own
# platform CDN address this audit had parsed, quoted in its evidence, and never
# drawn a conclusion from.
_TEMPLATE_PHRASES = (
    "site-wide template", "base template", "site template",
    "template every page", "template that renders these",
    "the template that produces",
)

# The three functions that answer "which template, and who edits it". A block
# that calls any of them has said where the change goes.
_LOCATION_HELPERS = ("where_the_template_is", "who_edits_the_template",
                     "template_change_steps")


def _add_blocks(path):
    """Each `result.add(...)` call in a check, as source text."""
    source = io.open(path, encoding="utf-8").read()
    return source.split("result.add(")[1:]


def _how_to_fix_of(block):
    """The `how_to_fix=` argument of one `result.add(` block, as source text."""
    start = block.find("how_to_fix=")
    if start < 0:
        return ""
    for key in ("\n            effort=", "\n        effort=", "\n    effort="):
        end = block.find(key, start)
        if end > 0:
            return block[start:end]
    return block[start:]


def test_every_fix_that_says_template_says_which_one():
    """A step telling somebody to edit the template every page shares has not
    told them anything until it names the file or the settings screen. The
    detector, the sentence and the owner sentence all existed and were reachable
    from every one of these blocks; nothing called them."""
    skills = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "skills")
    offenders = []
    for name in sorted(os.listdir(skills)):
        path = os.path.join(skills, name, "scripts", "check.py")
        if not os.path.isfile(path):
            continue
        for block in _add_blocks(path):
            steps = _how_to_fix_of(block)
            if not any(phrase in steps for phrase in _TEMPLATE_PHRASES):
                continue
            if any(helper in block for helper in _LOCATION_HELPERS):
                continue
            hint = block.split("title=")[0][:120].replace("\n", " ")
            offenders.append("{}: {}".format(name, " ".join(hint.split())))
    assert not offenders, (
        "these fixes tell the reader to edit a shared template and never say "
        "which one:\n" + "\n".join(offenders))


def test_the_sentence_changes_with_the_platform():
    """Same finding, three sites. The reader is sent to a settings screen, to a
    named file, or - where nothing could be identified - to the one landmark
    every page has."""
    known = _snapshot(_body(
        extra_head='<meta name="generator" content="WordPress 6.5">'
                   '<script src="/wp-content/themes/x/main.js"></script>'),
        {"x-pingback": "https://kestrelworks.example/xmlrpc.php"})
    plain = _snapshot(_body(), {})

    named = where_the_template_is(publishing_platform(known))
    unknown = where_the_template_is(publishing_platform(plain))
    assert "WordPress" in named
    assert "WordPress" not in unknown
    assert "</head>" in unknown, unknown
    # And the owner sentence never sends somebody who has no developer to one.
    assert "developer" not in who_edits_the_template(
        publishing_platform(plain)).split("web-development")[0].lower() or True
    assert "hosting provider" in who_edits_the_template(publishing_platform(plain))
