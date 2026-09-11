# -*- coding: utf-8 -*-
"""Two name tests that decided a verdict paragraph and a headline count.

Both live in `audit_common` because two skills each have to reach the same
answer, and both were wrong about the same kind of thing: a name written one
way in one place and another way in another.

  the definition rule       a site declares "<Name>s" in `og:site_name` and in
                            every page title, and writes "<Name> is one of the
                            largest ... " in the paragraph that says what it
                            is. Every sentence shape in the rule matched that
                            sentence and none of them was ever tried, because
                            the subject the patterns were built from was the
                            declared spelling. The finding said no page states
                            in one sentence what the brand is, and the verdict
                            paragraph at the top of the report repeated it.

  the profile rule          a list of the words people put around their own
                            name in a handle - `get`, `the`, `official` - read
                            as the rule that decides whose account this is. It
                            was unreachable code sitting under a containment
                            test that answers first, and the live rule was the
                            name anywhere inside the handle with no floor
                            under it, so a three-letter name inside a
                            stranger's handle counted as this brand's.

The sentence shapes were never the problem in the first case, and the list was
never the rule in the second, so both are pinned here as well: what each rule
already accepted has to keep working, or the next reader will "fix" it again.

Every brand, handle and host below is invented.
"""

from __future__ import annotations

import os
import re
import sys

import pytest

from conftest import SCRIPTS

sys.path.insert(0, SCRIPTS)

import audit_common as AC  # noqa: E402


HOST = "https://a-platform.test"


# --------------------------------------------------------------------------
# One name, two spellings: the trailing "s"
# --------------------------------------------------------------------------

DEFINITION = "{} is one of the largest branded footwear retailers in the country."


@pytest.mark.parametrize("declared,written", [
    # The measured case: the markup carries the plural, the prose the singular.
    ("Kestrels", "Kestrel"),
    # And the other way, which is the same disagreement.
    ("Kestrel", "Kestrels"),
    # Whatever else stands between the name and the verb.
    ("Kestrels", "The Kestrel"),
])
def test_a_name_is_the_same_name_with_or_without_its_trailing_s(declared, written):
    brand = {"name": declared, "domain_token": declared.lower()}
    found = AC.defining_sentence(DEFINITION.format(written), declared, brand)
    assert found, "no definition found for declared {!r} written {!r}".format(
        declared, written)
    assert "largest branded footwear retailers" in found


def test_a_name_ending_in_a_double_s_keeps_both_of_them():
    """Dropping the last letter of "<Name>ss" leaves a different word, not the
    same word spelled two ways, so that name is matched as written."""
    brand = {"name": "Harrowgate Press", "domain_token": "harrowgatepress"}
    assert AC.defining_sentence(
        DEFINITION.format("Harrowgate Press"), "Harrowgate Press", brand)
    assert AC.defining_sentence(
        DEFINITION.format("Harrowgate Pres"), "Harrowgate Press", brand) == ""


def test_a_two_letter_name_is_never_reduced_to_one():
    """One letter matches a word that is not the brand at all."""
    assert AC.defining_sentence(DEFINITION.format("U"), "Us", {"name": "Us"}) == ""


def test_the_letter_is_optional_and_nothing_more_is():
    """"<Name>s" may stand for "<Name>". A longer word that merely opens with
    the name is a different word and always was."""
    brand = {"name": "Kestrel", "domain_token": "kestrel"}
    assert AC.defining_sentence(DEFINITION.format("Kestrelson"), "Kestrel", brand) == ""
    assert AC.defining_sentence(DEFINITION.format("Kestrelware"), "Kestrel", brand) == ""


# --------------------------------------------------------------------------
# The sentence shapes were not the defect
# --------------------------------------------------------------------------

# Three shapes a reading of the failure blamed for it. Each states what kind of
# thing the brand is, each was already accepted, and a change that dropped any
# of them would be a regression dressed as a fix.
ALREADY_ACCEPTED = [
    ("is-one-of", "Kestrel is one of the largest branded footwear retailers in the country."),
    ("is-the", "Kestrel is the largest branded footwear retailer in the country."),
    ("comma-appositive", "Kestrel, a payments ledger for small manufacturers, opened here."),
]


@pytest.mark.parametrize("case,text", ALREADY_ACCEPTED, ids=[c for c, _ in ALREADY_ACCEPTED])
def test_the_shapes_blamed_for_the_miss_were_always_accepted(case, text):
    assert AC.defining_sentence(text, "Kestrel", {"name": "Kestrel"})


@pytest.mark.parametrize("text", [
    "Kestrel is a leading global platform trusted by innovative teams.",
    # The same boast in the shape that was blamed for the miss. "one" was not
    # among the function words, so it counted as the content word that saves a
    # predicate made of nothing else, and the check quoted this back to a site
    # as the sentence that says what it is.
    "Kestrel is one of the leading global brands trusted by innovative teams.",
])
def test_a_predicate_that_names_no_category_is_still_rejected(text):
    """The article after the copula is optional and always was, so nothing here
    rests on it. What rejects an empty predicate is the vocabulary."""
    assert AC.defining_sentence(text, "Kestrel", {"name": "Kestrel"}) == ""


# --------------------------------------------------------------------------
# Whose handle is this?
# --------------------------------------------------------------------------

@pytest.mark.parametrize("handle,brand,expected", [
    # The name decorated at either end, which is the same handle either way.
    ("ordwellvn", "Ordwell", True),
    ("happyordwell", "Ordwell", True),
    ("ordwellofficial", "Ordwell", True),
    ("weareordwell", "Ordwell", True),
    ("theordwellstore", "Ordwell", True),
    # The name on its own.
    ("ordwell", "Ordwell", True),
    # A syllable is not a name. Each of these is a real word of somebody
    # else's handle that happens to contain a short name.
    ("marchsupplies", "Arc", False),
    ("onthemarch", "Arch", False),
    ("honestbakery", "Nest", False),
    # A short name has to be most of the handle it is found in.
    ("nestbakerysupplies", "Nest", False),
    ("nestbakery", "Nest", False),
    ("nestco", "Nest", True),
    # A longer name is not a coincidence, so the arithmetic stops applying.
    ("ordwellbakerysupplies", "Ordwell", True),
    # The handle marking its own words says which of them it is about, and
    # that is evidence a bare run of letters cannot give.
    ("abc-store", "Abc", True),
    ("AbcStore", "Abc", True),
    ("abcstore", "Abc", False),
    # A handle shorter than the name: a fragment of the name, with no other
    # word in it to belong to anybody else.
    ("ordwell", "Ordwell Manufacturing", True),
    # Except one too short to be a name.
    ("get", "GetOrdwell", False),
])
def test_a_handle_names_the_brand_or_it_does_not(handle, brand, expected):
    assert AC.profile_names_brand(
        "{}/{}".format(HOST, handle), brand) is expected


def test_a_query_string_and_a_trailing_slash_change_nothing():
    for url in ("{}/happyordwell".format(HOST),
                "{}/happyordwell/".format(HOST),
                "{}/happyordwell?ref=footer".format(HOST),
                "{}/happyordwell#about".format(HOST)):
        assert AC.profile_names_brand(url, "Ordwell") is True


def test_a_brand_name_that_reduces_to_nothing_still_rejects_nothing_by_name():
    """A name in a script a handle cannot carry reduces to no comparable
    letters, and a test that cannot pass must not be the thing that rejects.
    The caller reads a False here as "not evidence", never as "somebody
    else's" - see `_looks_like_a_citation`."""
    assert AC.profile_names_brand("{}/ordwell".format(HOST), "オード") is False
    assert AC.profile_names_brand("{}/ordwell".format(HOST), "") is False


def test_the_list_of_handle_decorations_is_gone():
    """It was unreachable before it was deleted: every branch of it asked
    whether the handle is one of those words joined to the name, and the
    containment test above it answered yes to all of them first. A list of the
    ways people decorate a handle can never be finished, and leaving a dead one
    in place invites the next reader to extend it instead of the rule."""
    with open(os.path.join(SCRIPTS, "audit_common.py"), encoding="utf-8") as handle:
        source = handle.read()
    assert not re.search(r"^_HANDLE_AFFIXES\s*=", source, re.M)


# --------------------------------------------------------------------------
# Neither change may reach the comparisons the rest of the audit runs on
# --------------------------------------------------------------------------

def test_the_chrome_address_source_is_the_string_the_extractor_writes():
    """`pages_with_their_own_address` skips a page whose only street came from
    the header or the footer, and it recognises that by comparing against the
    wording the extractor chose. Two copies of one string that must agree are
    one string; the extractor cannot import this constant without a cycle, so
    the build checks instead of hoping."""
    path = os.path.join(SCRIPTS, "page_extract.py")
    with open(path, encoding="utf-8") as handle:
        source = handle.read()
    assert '"{}"'.format(AC.CHROME_ADDRESS_SOURCE) in source, (
        "page_extract.py no longer writes {!r} as a street_source".format(
            AC.CHROME_ADDRESS_SOURCE))


def test_a_footer_street_is_not_a_branch():
    """One office printed in the furniture of forty pages is one office. Read
    as forty, it is a high-severity multi-location headline on a shop with a
    single address."""
    def page(url, source):
        return {"url": url, "page_type": "other",
                "contact_facts": {"street_hint": "1 Invented Row",
                                  "postcode_hint": "AA1 1AA",
                                  "street_source": source,
                                  "postcode_source": "in an address region"}}

    footer_only = {"pages": [page("https://a-site.test/a", AC.CHROME_ADDRESS_SOURCE),
                             page("https://a-site.test/b", AC.CHROME_ADDRESS_SOURCE)]}
    assert AC.pages_with_their_own_address(footer_only) == []
    stated = {"pages": [page("https://a-site.test/a", "in an address region")]}
    assert len(AC.pages_with_their_own_address(stated)) == 1


def test_the_definition_rule_now_agrees_with_the_name_comparison():
    """`names_match` has always read a plural as the same name - it is one of
    the six variations it was written for - and the definition rule was the one
    place in the audit that did not. The trailing "s" is handled where the
    subject pattern is built, so the two answers agree without `name_forms`
    inventing a spelling the site never wrote."""
    assert AC.names_match("Kestrel", "Kestrels") is True
    assert "Kestrel" not in AC.name_forms("Kestrels", domain_token="kestrels")
    assert AC.brand_pattern("Kestrels") == AC.brand_pattern("Kestrel")
