# -*- coding: utf-8 -*-
"""The name of a bot manager, and the advice that may be given about one.

`detect_challenge` hands back one of three different kinds of name, and the
verification-page finding used to drop all three into one sentence written for
the first of them:

    "Open the bot-management rules in a CAPTCHA widget (hCaptcha) and find the
     rule that serves a JavaScript challenge."

Two faults in one line. The grammar, because a CAPTCHA widget's name is a noun
phrase and the slot wanted a product's name. And the instruction, because that
run had identified no bot manager at all: it had found a widget embedded in
four intercepted pages of a shop running on a hosted store platform, whose
owner has no console to open and no rule to change. It stood second in "Start
here", priced at half a day of a developer's time.

So two things are held here:

  grammar     every sentence that carries the name reads correctly whichever
              of the three supplied it - a product ("Cloudflare"), the
              unnamed-edge fallback ("the site's edge"), or a widget name.
  standing    what the reader is told to do follows what was established. A
              product named has a console somebody can open. Nothing named
              means nobody can be sent to one, and the fix is the measurement
              plus a support ticket to whoever serves the site.

And the advice that was already right is pinned, because it is the best
advice in the tool: one status line per crawler from outside
the reader's network, before any rule is changed; the falsifier that reads the
output where every name including the browser's comes back refused; and the
refusal to hand the training crawlers over, which buy no citations.
"""

from __future__ import annotations

import importlib.util
import os
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    CHALLENGE_MARKERS, detect_challenge, SkillResult,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "ca_for_challenge_names")

ORIGIN = "https://an-invented-shop.test"
HOME = ORIGIN + "/"

WIDGET_NAME = "a CAPTCHA widget (hCaptcha)"
# A page carrying nothing but a CAPTCHA widget: the shape the four intercepted
# URLs had. No vendor scaffolding, no mitigation header, almost no text.
WIDGET_PAGE = ('<html><body><div class="h-captcha"></div>'
               '<script src="https://hcaptcha.com/1/api.js"></script></body></html>')
# A verification screen that names nobody at all: the words on the screen are
# the only thing identifying it.
PHRASE_PAGE = "<html><body><h1>Checking your browser</h1></body></html>"


def _snapshot(pages):
    return {"origin": ORIGIN, "pages": pages, "crawl": {"head_supported": True}}


def _page(path, challenge=None, status=200):
    record = {"url": HOME + path, "status": status, "page_type": "other"}
    if challenge:
        record["challenge"] = challenge
    return record


def _finding(pages, ua_verdict=None):
    result = SkillResult("crawl-access-audit")
    CA._check_challenge_pages(result, _snapshot(pages), ua_verdict)
    assert result.findings, "the verification page was not reported at all"
    return result.findings[0]


def _words(finding):
    """Everything the reader sees for this finding, in one string."""
    action = finding["suggested_action"]
    return " ".join([finding["title"], finding["evidence"], action["summary"]]
                    + list(action["how_to_fix"]) + [action["rationale"]])


# Four URLs of forty answered with a widget screen and the rest came back with
# content: the run that produced the sentence at the top of this file.
HOSTED_SHOP = ([_page("p%d" % i) for i in range(36)]
               + [_page("c%d" % i, WIDGET_NAME, status=403) for i in range(4)])
# A named product, across the whole crawl.
OWN_EDGE = [_page("p%d" % i, "Akamai Bot Manager", status=202) for i in range(6)]


# --------------------------------------------------------------------------
# The name, in each slot that carries it
# --------------------------------------------------------------------------

def test_the_names_this_file_sorts_are_the_names_the_detector_returns():
    """The three kinds are told apart by their spelling, so the spellings are
    checked against the detector rather than assumed.

    `detect_challenge` lives in the shared library and this check reads its
    output. If a widget name is ever spelled differently there, the sentences
    below quietly go back to naming a console nobody has - so this fails first.
    """
    widget = detect_challenge(WIDGET_PAGE, "")
    assert CA._is_widget_name(widget), widget
    assert not CA._names_a_console(widget)

    unnamed = detect_challenge(PHRASE_PAGE, "Checking your browser")
    assert unnamed == CA.UNNAMED_EDGE
    assert not CA._names_a_console(unnamed)

    for product in CHALLENGE_MARKERS:
        assert CA._names_a_console(product), product
        assert not CA._is_widget_name(product)


@pytest.mark.parametrize(
    "vendors",
    [[name] for name in sorted(CHALLENGE_MARKERS)]
    + [[WIDGET_NAME], ["a CAPTCHA widget (Cloudflare Turnstile)"], [CA.UNNAMED_EDGE],
       ["Cloudflare", WIDGET_NAME], [CA.UNNAMED_EDGE, WIDGET_NAME], ["AWS WAF", "Cloudflare"]])
def test_every_sentence_that_carries_the_name_still_reads_as_english(vendors):
    """The substitution faults, held one by one.

    "a a CAPTCHA widget (hCaptcha) verification page" and "carrying a CAPTCHA
    widget (hCaptcha) challenge scaffolding" both shipped. Neither is a
    sentence, and both were built by putting a noun phrase in a slot that
    wanted a product name.
    """
    pages = [_page("p%d" % i, vendors[i % len(vendors)], status=202)
             for i in range(len(vendors) * 2)]
    text = _words(_finding(pages))
    for broken in ("a a ", "an a ", " a A ", "  "):
        assert broken not in text, broken
    assert "widget) " not in text
    # A widget is embedded by whatever served the page; it never serves one,
    # and it is never a place with rules in it.
    for verb in ("widget (hCaptcha) serves", "widget (Cloudflare Turnstile) serves",
                 "rules in a CAPTCHA", "through a CAPTCHA widget",
                 "in a CAPTCHA widget"):
        assert verb not in text, verb
    assert _finding(pages)["title"][0].isupper()


def test_the_title_names_what_was_found_and_who_it_was_served_to():
    """"served to crawlers" is a claim about clients this run never sent, so
    only the verdicts that held the client constant earn it - and the name has
    to survive in the title either way, because the reader matches it against
    what their own edge or host tells them."""
    keyed = _finding(OWN_EDGE, CA.UA_KEYED)["title"]
    assert "Akamai Bot Manager" in keyed and "served to crawlers" in keyed

    untested = _finding(OWN_EDGE)["title"]
    assert "Akamai Bot Manager" in untested and "served to crawlers" not in untested

    widget = _finding(HOSTED_SHOP)["title"]
    assert "hCaptcha" in widget and "served to crawlers" not in widget


# --------------------------------------------------------------------------
# What the reader is told to do, and what they are not
# --------------------------------------------------------------------------

def test_a_shop_on_a_hosted_platform_is_not_sent_to_a_console_it_cannot_open():
    """The reported defect. Four of forty URLs, a widget and no product name:
    nothing here establishes a rule anybody at this site can open, so nothing
    here tells them to open one."""
    finding = _finding(HOSTED_SHOP)
    action = finding["suggested_action"]
    steps = " ".join(action["how_to_fix"])

    assert "Open the bot-management rules" not in steps
    assert "Add an allow rule" not in steps
    assert "support ticket" in steps
    assert "hosts the site" in steps
    # The reading is stated as a reading, with the count it rests on and the
    # question that settles it.
    assert "Most likely" in action["how_to_fix"][0]
    assert "4 of 40" in action["how_to_fix"][0]
    assert "login to a CDN or WAF account" in action["how_to_fix"][0]
    # And the evidence says the same thing rather than leaving the conclusion
    # to the fix steps alone.
    assert "no request this audit can send sees the hosting account" in finding["evidence"]


def test_the_work_is_priced_as_the_ticket_it_is():
    """`developer` and "half a day to a day" priced a console visit. Where the
    steps are a curl loop and a support ticket, that is an hour and it is not
    a developer's hour."""
    hosted = _finding(HOSTED_SHOP)["suggested_action"]
    assert (hosted["effort"], hosted["owner"]) == ("low", "content owner")

    own = _finding(OWN_EDGE, CA.UA_KEYED)["suggested_action"]
    assert (own["effort"], own["owner"]) == ("medium", "developer")


def test_a_named_product_is_named_and_still_leaves_the_reader_a_way_out():
    """The other case, unchanged where it was right: a product that named
    itself has a console, and the step names it. What is new is the last
    clause - naming a console is not establishing that the reader holds the
    account behind it."""
    steps = _finding(OWN_EDGE, CA.UA_KEYED)["suggested_action"]["how_to_fix"]
    assert steps[0].startswith("Open the bot-management rules in Akamai Bot Manager")
    assert "no login" in steps[0] or "nobody here has a login" in steps[0]
    assert any("support ticket" in step for step in steps)


def test_an_undetermined_case_is_written_both_ways_and_claims_neither():
    """A widget across the whole crawl. Either reading is live, so the step
    gives both and the reader is told how to tell which they are in."""
    everywhere = [_page("p%d" % i, WIDGET_NAME, status=403) for i in range(6)]
    first = _finding(everywhere)["suggested_action"]["how_to_fix"][0]
    assert "was not established" in first
    assert "If you or your developer run a CDN or WAF account" in first
    assert "hosted platform" in first
    assert "Most likely" not in first


# --------------------------------------------------------------------------
# The advice that was already right
# --------------------------------------------------------------------------

@pytest.mark.parametrize("pages,verdict", [
    (HOSTED_SHOP, None),
    (HOSTED_SHOP, CA.UA_CLIENT_REFUSED),
    (OWN_EDGE, None),
    (OWN_EDGE, CA.UA_CLIENT_REFUSED),
])
def test_the_measurement_the_owner_can_make_survives_both_branches(pages, verdict):
    """One status line per crawler name, from outside the reader's network,
    before any rule is changed. In the hosted case it is also the evidence the
    support ticket carries, which is why it comes before the ticket."""
    steps = _finding(pages, verdict)["suggested_action"]["how_to_fix"]
    loop = next(step for step in steps if "curl" in step)
    assert "$ua" in loop and "%{http_code}" in loop
    assert "from a machine outside your network" in loop
    assert steps.index(loop) < len(steps) - 1


@pytest.mark.parametrize("pages,verdict", [
    (HOSTED_SHOP, None),
    (HOSTED_SHOP, CA.UA_KEYED),
    (OWN_EDGE, None),
    (OWN_EDGE, CA.UA_KEYED),
])
def test_the_training_crawler_refusal_survives_every_branch(pages, verdict):
    """The advice every list on the subject gets wrong, refused in all four
    shapes of this finding: the training crawlers are separate user agents,
    they produce no citations, and opening them buys nothing here."""
    steps = " ".join(_finding(pages, verdict)["suggested_action"]["how_to_fix"])
    assert "training crawler" in steps
    for token in ("GPTBot", "ClaudeBot", "CCBot", "Google-Extended"):
        assert token not in steps, token


@pytest.mark.parametrize("pages,verdict", [
    (HOSTED_SHOP, None),
    (HOSTED_SHOP, CA.UA_CLIENT_REFUSED),
    (OWN_EDGE, None),
    (OWN_EDGE, CA.UA_CLIENT_REFUSED),
    (OWN_EDGE, CA.UA_KEYED),
])
def test_every_verification_step_says_what_both_outcomes_would_mean(pages, verdict):
    """A step whose result reads the same either way tells the reader nothing.

    Where the loop is printed, the falsifier is the run in which every name
    comes back refused, the reader's own browser included: nothing there is
    reading the crawler name. Where an allow rule was earned instead, the
    single verify request carries the same two-sided reading.
    """
    steps = _finding(pages, verdict)["suggested_action"]["how_to_fix"]
    loop = [step for step in steps if "for ua in" in step]
    if loop:
        assert any("including your own browser's" in step or "including your browser's" in step
                   for step in steps)
    else:
        verify = next(step for step in steps if step.startswith("Verify with `curl"))
        assert "Real HTML back means" in verify and "challenge page back means" in verify
