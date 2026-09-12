"""What the person who owns the website actually reads.

The hackathon brief asks for "a clear, structured, actionable report a
non-expert could act on". Every other test in this suite checks the report's
*schema*. None checked whether it says true things in words a marketing manager
can use.

A trial run - an AI assistant given the marketplace and told to answer "why does
ChatGPT never mention us", then to explain the result to a business owner who
does not know what a crawler is - found what those tests could not:

  - The report opened with "The foundations are sound" on a site with no
    off-site presence, no identity markup, invalid markup where it existed and
    six of seven pages sharing a title. The verdict was a pure severity-count
    ladder: no critical, one high, so the tone went reassuring.
  - Off-site profile breadth is called "the strongest signal we measured" in
    the README. A site linking to zero profiles was rated `medium`.
  - "Start here" ranked missing Open Graph tags - which control what a link
    preview looks like when shared - above fixing structured data that does not
    parse, because a `low` finding still scores 1 and a cheap site-wide fix has
    full reach and lowest effort.
  - Every finding was tagged "mechanism C" with no expansion anywhere the
    reader could see it.
  - Paste-ready snippets were pre-filled with the audited address. On a local
    or staging origin that is a URL nobody else can reach, inside code labelled
    ready to paste.

None of these is a schema violation. All of them are the report being wrong or
unusable at the moment somebody relies on it.
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import MECHANISMS, plural  # noqa: E402


def _compose():
    spec = importlib.util.spec_from_file_location(
        "compose_for_report_tests", os.path.join(SCRIPTS, "compose_report.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compose = _compose()

COUNTS = {"critical": 0, "high": 1, "medium": 8, "low": 3, "info": 0}


# --------------------------------------------------------------------------
# The verdict
# --------------------------------------------------------------------------

def test_a_brand_with_no_identity_and_no_corroboration_is_not_told_it_is_sound():
    # The id hint is load-bearing, not decoration. Four checks raise
    # `no-org-schema` and two of them say the site *has* identity markup, so
    # the verdict reads the hint and only this one means "nowhere on the site".
    # See `tests/test_the_verdict_reads_the_whole_site.py`.
    findings = [{"root_cause": "no-org-schema", "severity": "medium",
                 "id_hint": "no-organization-schema"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    verdict = compose._verdict(COUNTS, findings, {}, {"profile_breadth": 0})
    assert "foundations are sound" not in verdict
    assert "who the brand is" in verdict


def test_that_verdict_names_the_pattern_rather_than_counting():
    findings = [{"root_cause": "no-entity-definition", "severity": "medium"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    verdict = compose._verdict(COUNTS, findings, {}, {"profile_breadth": 1}, "A Brand")
    assert "nothing it can safely repeat about who the brand is" in verdict
    # Specific to this site, not a template: it names the brand and the gap.
    assert "A Brand" in verdict
    assert "no page states in one sentence what it is" in verdict


def test_a_definition_on_the_about_page_is_not_reported_as_no_page_having_one():
    """A programming-language foundation's verdict said "no page states in one
    sentence what it is" and then quoted the site's own sentence "from its own
    one-sentence definition"; its finding F-008 said "The homepage never says
    what the brand is, though the about page does". The finding shares its root
    cause with the one that found nothing, and the verdict read the cause."""
    about = "https://www.quillwort-lang.test/about/"
    sentence = ("Quillwort is a programming language that lets you work quickly and "
                "integrate systems more effectively.")
    findings = [{"root_cause": "no-org-schema", "severity": "medium",
                 "id_hint": "no-organization-schema"},
                {"root_cause": "no-entity-definition", "severity": "low",
                 "id_hint": "definition-not-on-the-homepage"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    signals = {"profile_breadth": 1, "entity_definition_found": True,
               "entity_definition": sentence, "entity_definition_placement": "at the top of"}
    pages = [{"url": "https://www.quillwort-lang.test/", "body_text": "Welcome to Quillwort"},
             {"url": about, "body_text": "About. " + sentence + " Learn more."}]
    verdict = compose._verdict(
        COUNTS, findings, {}, signals, "Quillwort",
        quotable=compose.what_an_assistant_can_repeat(signals, (), "Quillwort", pages))
    assert "who the brand is" in verdict
    assert "no page states" not in verdict
    assert "one-sentence definition" not in verdict
    assert "the homepage does not state in one sentence what it is, though {} does".format(
        about) in verdict
    assert "({})".format(about) in verdict
    # And the finding still leads "Start here": the paragraph names it.
    assert "definition-not-on-the-homepage" in compose.verdict_diagnosis(findings, signals)


def test_where_the_page_is_unknown_the_verdict_still_does_not_say_no_page():
    findings = [{"root_cause": "no-org-schema", "severity": "medium",
                 "id_hint": "no-organization-schema"},
                {"root_cause": "no-entity-definition", "severity": "low",
                 "id_hint": "definition-not-on-the-homepage"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    signals = {"profile_breadth": 0, "entity_definition_found": True,
               "entity_definition": "Quillwort is the home of the Quillwort programming "
                                    "language and its community."}
    verdict = compose._verdict(COUNTS, findings, {}, signals, "Quillwort")
    assert "no page states" not in verdict
    assert "one-sentence definition" not in verdict
    assert "though another crawled page does" in verdict


def test_a_definition_elsewhere_does_not_on_its_own_make_the_identity_verdict():
    """The same rule as homepage-only markup: the site has said what it is,
    one link from the front door, so that alone is not "cannot establish who
    the brand is"."""
    findings = [{"root_cause": "no-entity-definition", "severity": "low",
                 "id_hint": "definition-not-on-the-homepage"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    signals = {"profile_breadth": 0, "entity_definition_found": True}
    assert "who the brand is" not in compose._verdict(COUNTS, findings, {}, signals)
    assert compose.verdict_diagnosis(findings, signals) == set()


def _location_page(path, street="", postcode=""):
    facts = {"has_address": bool(street), "declared_phones": []}
    if street:
        facts.update(street_hint=street, postcode_hint=postcode, street_source="main")
    return {"url": "https://quillwort-lang.test" + path, "status": 200,
            "page_type": "location", "content_type": "text/html",
            "headings": {"h1": ["Our Events"]},
            "paragraphs": ["Find a meeting near you and come along."],
            "contact_facts": facts}


def test_venue_listings_under_a_location_path_are_not_a_shared_audience_template():
    """A language foundation's event calendar keeps a venue listing for each
    user-group meeting at `/events/<calendar>/locations/<id>/`, one heading on
    all of them and no address on any. `page_type` is read off the path, so
    four of them met R-USE-CASE-PAGES' two-page bar on their own."""
    venues = {"pages": [_location_page("/events/groups/locations/{}/".format(n))
                        for n in (1418, 1494, 1584)]}
    assert compose.audience_pages_running_one_template(venues) == []


def test_branch_pages_printing_their_own_addresses_still_count():
    branches = {"pages": [_location_page("/find-us/york", "14 Harbour Lane", "YO1 7HH"),
                          _location_page("/find-us/leeds", "3 Canal Wharf", "LS1 4BR")]}
    assert compose.audience_pages_running_one_template(branches) == sorted(
        p["url"] for p in branches["pages"])


def test_a_site_that_does_declare_itself_gets_the_ordinary_verdict():
    """The guard must not swallow every site into one diagnosis."""
    findings = [{"root_cause": "meta-hygiene", "severity": "high"}]
    verdict = compose._verdict(COUNTS, findings, {}, {"profile_breadth": 8})
    assert "who the brand is" not in verdict
    assert "high-severity" in verdict


def test_a_blocked_site_still_explains_itself_first():
    verdict = compose._verdict(
        {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}, [],
        {"audit_blocked_by_robots": True}, {})
    assert "robots.txt disallows this auditor" in verdict


def test_a_critical_access_finding_still_outranks_the_diagnosis():
    counts = dict(COUNTS, critical=1)
    findings = [{"root_cause": "bot-manager-block", "severity": "critical", "mechanism": "A"},
                {"root_cause": "weak-corroboration", "severity": "medium"}]
    verdict = compose._verdict(counts, findings, {}, {"profile_breadth": 0})
    assert "shut out before they read anything" in verdict


def test_a_critical_that_is_not_about_access_does_not_claim_a_shut_out():
    """"Machines are being shut out before they read anything" was printed for
    any critical at all, and `render-readability-audit` raises one at
    mechanism C - the pages returned 200 and were read fine, and the same
    report's "what an assistant would quote" table then printed a sentence
    lifted off a page the verdict said was never read."""
    counts = dict(COUNTS, critical=1)
    findings = [{"root_cause": "js-shell", "severity": "critical", "mechanism": "C"}]
    verdict = compose._verdict(counts, findings, {}, {"profile_breadth": 0})
    assert "shut out before they read anything" not in verdict
    assert "fetched and read" in verdict


# --------------------------------------------------------------------------
# Start here
# --------------------------------------------------------------------------

def _finding(fid, severity, score):
    return {"id": fid, "severity": severity, "root_cause": "x",
            "suggested_action": {"priority_score": score}}


def test_start_here_prefers_substantive_findings_over_cheap_low_ones():
    ranked = [_finding("F-001", "low", 3.0), _finding("F-002", "low", 2.9),
              _finding("F-003", "low", 2.8), _finding("F-004", "high", 1.0),
              _finding("F-005", "medium", 0.9)]
    chosen = compose._start_here_ids(ranked)
    assert "F-004" in chosen and "F-005" in chosen


def test_start_here_falls_back_to_low_but_never_to_info():
    """`low` findings are real work and can lead when nothing else does. An
    `info` finding is an observation, and leading with one would tell an owner
    to start on something that is not a problem."""
    only_low = [_finding("F-001", "low", 3.0), _finding("F-002", "low", 2.0)]
    assert compose._start_here_ids(only_low) == ["F-001", "F-002"]

    with_info = [_finding("F-001", "info", 9.0), _finding("F-002", "medium", 1.0)]
    assert compose._start_here_ids(with_info) == ["F-002"]


# --------------------------------------------------------------------------
# Words the reader can use
# --------------------------------------------------------------------------

def test_every_mechanism_letter_has_a_plain_english_expansion():
    """The report tags findings with a letter; the letter must resolve."""
    for letter in sorted(MECHANISMS):
        assert MECHANISMS[letter] and len(MECHANISMS[letter]) > 20, letter


@pytest.mark.parametrize("count,expected", [(0, "0 pages"), (1, "1 page")])
def test_plural_reads_like_english(count, expected):
    assert plural(count, "page") == expected


def test_plural_takes_an_irregular_form():
    assert plural(1, "entity") == "1 entity"
    assert plural(3, "entity", "entities") == "3 entities"


# --------------------------------------------------------------------------
# Snippets that would be pasted into a live site
# --------------------------------------------------------------------------

@pytest.mark.parametrize("snippet", [
    '{"url": "http://127.0.0.1:8000/"}',
    '{"logo": "http://localhost:3000/logo.png"}',
    '{"url": "https://staging.example.local/"}',
    '{"url": "https://192.168.1.4/"}',
])
def test_a_snippet_filled_in_from_a_private_address_is_flagged(snippet):
    assert compose._non_public_host(snippet) is True


@pytest.mark.parametrize("snippet", ['{"url": "https://shop.a-brand.example/products/1"}'])
def test_a_snippet_from_a_public_address_is_not_flagged(snippet):
    assert compose._non_public_host(snippet) is False


# --------------------------------------------------------------------------
# Raw library text is not a diagnosis
#
# Pointing the audit at a homepage that redirects in a loop produced
# "returned no response (Exceeded 30 redirects.)". Pointing it at one that
# redirects to a domain which does not exist produced a full urllib3
# connection-pool message including the class name of the underlying
# exception. Both went into a document written for a marketing manager.
# --------------------------------------------------------------------------

from audit_common import explain_fetch_error  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    ("Exceeded 30 redirects.", "redirects in a loop"),
    ("HTTPSConnectionPool(host='x.test', port=443): Max retries exceeded with url: "
     "/landing (Caused by NameResolutionError(...))", "hostname that does not exist"),
    ("[WinError 10061] No connection could be made because the target machine "
     "actively refused it", "nothing is listening"),
    ("Read timed out.", "did not answer in time"),
    # A missing intermediate is named as one, because its fix is an hour's
    # work on the server's chain file and not a search through its logs.
    ("certificate verify failed: unable to get local issuer certificate",
     "without the intermediate certificate"),
    ("certificate verify failed: certificate has expired",
     "certificate a client will not accept"),
    ("response was still arriving after 30s", "so slowly the audit stopped waiting"),
])
def test_a_fetch_failure_is_explained_not_quoted(raw, expected):
    assert expected in explain_fetch_error(raw)


def test_dns_failure_is_not_mistaken_for_a_redirect_loop():
    """urllib3 wraps a DNS failure in text containing "Max retries exceeded"."""
    raw = ("HTTPSConnectionPool(host='nowhere.test', port=443): Max retries exceeded "
           "with url: / (Caused by NameResolutionError('no address'))")
    assert "hostname that does not exist" in explain_fetch_error(raw)
    assert "redirects in a loop" not in explain_fetch_error(raw)
    # And a message nothing recognises is passed through rather than swallowed.
    assert "something nobody predicted" in explain_fetch_error("something nobody predicted")


def test_no_verdict_wording_contains_a_template_placeholder():
    """`1 high-severity problem(s) are holding back` shipped in the verdict."""
    findings = [{"root_cause": "meta-hygiene", "severity": "high"}]
    for counts in ({"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
                   {"critical": 0, "high": 1, "medium": 0, "low": 0, "info": 0},
                   {"critical": 0, "high": 4, "medium": 0, "low": 0, "info": 0},
                   {"critical": 0, "high": 0, "medium": 1, "low": 0, "info": 0},
                   {"critical": 0, "high": 0, "medium": 3, "low": 0, "info": 0},
                   {"critical": 0, "high": 0, "medium": 0, "low": 0, "info": 0}):
        verdict = compose._verdict(counts, findings, {}, {"profile_breadth": 8})
        assert "(s)" not in verdict, verdict
        assert "1 " not in verdict or " problem " in verdict or " item " in verdict


# --------------------------------------------------------------------------
# Verbs agree with their counts
#
# Removing "(s)" from thirty-five titles introduced "1 page carry no next
# step" and "1 FAQ page have no FAQPage markup". Fixing one grammar bug by
# creating another is not progress, so the count and the verb now move
# together.
# --------------------------------------------------------------------------

PLURAL_TITLE = re.compile(r'plural\([^,]+, "([^"]+)", "([^"]+)"\)')


def test_every_plural_title_form_agrees_with_its_count():
    import glob
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "skills", "*", "scripts", "check.py"))):
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
        for singular, plural_form in PLURAL_TITLE.findall(source):
            skill = os.path.basename(os.path.dirname(os.path.dirname(path)))
            # A bare noun pair is fine; a pair carrying a verb must inflect it.
            if len(singular.split()) < 2 or len(plural_form.split()) < 2:
                continue
            # The real invariant: the two forms must actually differ, and the
            # singular must not be the plural with an "s" bolted on the wrong
            # word - "page stills" was a rewrite treating an adverb as a verb.
            if singular == plural_form:
                offenders.append("{}: {!r} does not change with the count".format(
                    skill, singular))
            if singular.split()[-1].rstrip("s") == plural_form.split()[-1] and                     singular.split()[-1].endswith("s") and                     plural_form.split()[-1] in ("still", "on", "of", "with", "in"):
                offenders.append("{}: {!r} inflects a non-verb".format(skill, singular))
    assert not offenders, "\n".join(offenders)


@pytest.mark.parametrize("singular,plural_form,expected_one", [
    ("page carries", "pages carry", "1 page carries"),
    ("content page is", "content pages are", "1 content page is"),
])
def test_the_singular_reads_like_english(singular, plural_form, expected_one):
    assert plural(1, singular, plural_form) == expected_one
    assert plural(2, singular, plural_form).startswith("2 ")
    assert plural(2, singular, plural_form).endswith(plural_form)
