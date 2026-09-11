# -*- coding: utf-8 -*-
"""Shop vocabulary in advice that is delivered to things that are not shops.

The site-kind gating already works: a validation pass confirmed that no `Product`,
`Offer`, `AggregateOffer` or `priceCurrency` ask reached a temple, a town
government, a public library or a documentation tree, and that each report says
why - "nothing on this site is for sale, so there is no price for an assistant
to be missing." What leaked was the wording around the asks that stayed:

    the library was told   "Put it above the product grid"
    the docs tree          "Prioritise product photos"

Cosmetic, and it still costs. A reader who meets "your product grid" on a
library's report learns that the tool does not know what kind of site it is
looking at, and discounts every true finding under it.

The pin is a scan of the source rather than a run of the audit, because the
defect is in the sentence and not in when the sentence fires: these strings are
constants, and any of them can be reintroduced by a one-word edit to a finding
that has nothing to do with selling. Every string literal inside the `summary`,
`how_to_fix` and `rationale` arguments of every `result.add(...)` in the four
files below is read, keyed by the finding's own `id_hint`, and a shop noun in
any of them fails unless the finding is one of the few whose whole subject is
selling - listed by name in `SELLING_IS_THE_SUBJECT` below, where adding one is
a deliberate act rather than a silent widening.
"""

from __future__ import annotations

import ast
import os
import re

import pytest

from conftest import ROOT

FILES = (
    os.path.join("skills", "engagement-audit", "scripts", "check.py"),
    os.path.join("skills", "render-readability-audit", "scripts", "check.py"),
    os.path.join("skills", "fact-extractability-audit", "scripts", "check.py"),
    # Added after the sweep of the three above was followed by two more reports
    # of the same defect in this file - "the article's lead image, the
    # product photograph" and "the post template, the product page, the page of
    # questions" printed verbatim to a Thai school, a Japanese shrine, a public
    # library and a personal homepage. The scan covered three skills and the
    # regression landed in the fourth.
    os.path.join("skills", "structured-data-audit", "scripts", "check.py"),
)

# The arguments a reader acts on. `evidence` and `checked` describe what was
# measured on this site and may name a product page because the site has one;
# these three tell the owner what to do, in this audit's own words.
ADVICE_ARGUMENTS = ("summary", "how_to_fix", "rationale")

# Findings whose entire subject is selling. Each one is already gated on the
# site having a price: the two landing-page checks only read pages that publish
# one, and the priced branch of the PDF check runs behind `sells_something`. A
# shop word in these is the right word.
SELLING_IS_THE_SUBJECT = frozenset({
    "landing-pages-do-not-answer-the-arriving-question",
    "landing-pages-bury-the-price",
    "facts-locked-in-pdfs",
    # `structured-data-audit`. Each of these four is raised only over pages the
    # crawl typed `product`, or over `Product` markup the site itself
    # published, so none of them can reach a site that sells nothing - and each
    # is telling the owner what to do with a `Product` block, which is a word
    # the step has to be able to say.
    "no-product-schema",
    "product-schema-missing-offer-details",
    "markup-declares-more-than-one-brand-name",
    "listing-pages-have-no-itemlist-markup",
    # Two more raised only over `Product` and `Offer` blocks the site itself
    # published: a category word in `brand`, and a first offer that is out of
    # stock. Neither can fire on a site that sells nothing.
    "product-brand-is-a-category",
    "first-offer-is-out-of-stock",
})

# Nouns that assert the site sells something. Not a style list: every one of
# these names a thing a library, a council, a temple or a manual does not have,
# so a sentence containing one is either about a shop or is wrong.
SHOP_NOUNS = (
    "product", "products", "cart", "carts", "basket", "baskets",
    "buyer", "buyers", "shopper", "shoppers", "customer", "customers",
    "storefront", "storefronts", "checkout", "merchandise", "in stock",
)
_SHOP_NOUN_RE = re.compile(
    r"\b(?:{})\b".format("|".join(re.escape(w) for w in SHOP_NOUNS)), re.I)


def _module_constants(tree):
    """{name: expression} for every module-level `NAME = ...` assignment."""
    bound = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bound[target.id] = node.value
    return bound


def _strings_under(node, constants=None, seen=()):
    """Every string constant anywhere inside this expression.

    Module-level constants referenced by name are followed. Without that this
    scan read the `og:image` step as the bare name `OPEN_GRAPH_FIX_STEPS` and
    saw nothing: the sentence a library's report printed - "the
    article's lead image, the product photograph" - lives in a dict beside the
    check and reaches `how_to_fix` through a comprehension over it. A pin that
    misses the way the defect was actually written is not a pin.
    """
    constants = {} if constants is None else constants
    found = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            found.append(child.value)
        elif isinstance(child, ast.Name) and child.id in constants and child.id not in seen:
            found.extend(_strings_under(constants[child.id], constants,
                                        tuple(seen) + (child.id,)))
    return found


def _advice_by_finding(path):
    """{id_hint: [advice string, ...]} for every `result.add(...)` in a file."""
    with open(path, encoding="utf-8") as handle:
        tree = ast.parse(handle.read(), filename=path)
    constants = _module_constants(tree)
    found = {}
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        target = call.func
        if not (isinstance(target, ast.Attribute) and target.attr == "add"):
            continue
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        if "id_hint" not in keywords:
            continue
        # A computed id_hint keeps the literal part of its own name, which is
        # what identifies the family in the allowlist below.
        names = _strings_under(keywords["id_hint"]) or ["<computed>"]
        advice = []
        for argument in ADVICE_ARGUMENTS:
            if argument in keywords:
                advice.extend(_strings_under(keywords[argument], constants))
        found.setdefault(names[0], []).extend(advice)
    return found


@pytest.mark.parametrize("relative", FILES)
def test_advice_carries_no_shop_noun_outside_the_selling_checks(relative):
    advice = _advice_by_finding(os.path.join(ROOT, relative))
    assert advice, "no result.add(...) calls were read from {}".format(relative)
    offenders = []
    for id_hint, strings in sorted(advice.items()):
        if id_hint in SELLING_IS_THE_SUBJECT:
            continue
        for text in strings:
            match = _SHOP_NOUN_RE.search(text)
            if match:
                offenders.append("{}: {!r} in {!r}".format(id_hint, match.group(0), text))
    assert not offenders, (
        "advice that assumes the site is a shop, on findings that reach every kind of "
        "site:\n" + "\n".join(offenders))


def test_the_two_quoted_sentences_are_gone_and_still_say_something():
    """The exact pair, by name, so a revert is a failure rather than a silence.

    Both sentences still have to be given: the instruction was right and only
    the noun was a claim about the site.
    """
    definition = _advice_by_finding(
        os.path.join(ROOT, "skills", "fact-extractability-audit", "scripts", "check.py")
    )["definition-not-on-the-homepage"]
    joined = " ".join(definition)
    assert "product grid" not in joined
    assert "outside any slider or carousel" in joined
    assert "the main content" in joined

    alt = _advice_by_finding(
        os.path.join(ROOT, "skills", "render-readability-audit", "scripts", "check.py")
    )["images-missing-alt-text"]
    joined = " ".join(alt)
    assert "product photos" not in joined
    assert "diagrams" in joined and "any image containing words" in joined


def test_the_two_later_quoted_sentences_are_gone_and_still_say_something():
    """The `structured-data-audit` pair, by name, for the same reason as the
    pair above: both sentences still have to be given, and only the noun was a
    claim about the site.

      og:image   "the article's lead image, the product photograph", printed on
                 every page missing the tag, on a Thai school, a Japanese
                 shrine, a public library and a personal homepage;
      the fold   "the post template, the product page, the page of questions",
                 printed whatever kinds of page had been folded.
    """
    advice = _advice_by_finding(
        os.path.join(ROOT, "skills", "structured-data-audit", "scripts", "check.py"))

    og = " ".join(advice["incomplete-open-graph-tags"])
    assert "product photograph" not in og
    assert "the picture the page leads with" in og
    assert "1200x630" in og, "the instruction itself must survive the rewording"

    fold = " ".join(advice["no-structured-data-at-all"])
    assert "the product page, the page of questions" not in fold
    assert "the file that renders that kind of page" in fold


def test_the_allowlisted_findings_are_all_real_findings():
    """An allowlist entry that names nothing is a hole that looks like a rule."""
    known = set()
    for relative in FILES:
        known |= set(_advice_by_finding(os.path.join(ROOT, relative)))
    assert SELLING_IS_THE_SUBJECT <= known, SELLING_IS_THE_SUBJECT - known
