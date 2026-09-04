"""What eight site types found in round seven, and the classes behind them.

Eight agents ran the shipped zip on an online shop, a museum, a Japanese
postal group, a global law firm, a software product site, a one-person recipe
blog, a hospital system and a documentation subdomain. 84 findings checked, 36
wrong or misleading.

That is 43%, against 18% in round six. Round six was four sites and 42
findings; these eight are the harder shapes - two crawls refused outright, two
sites not in English, one whose pages are mostly other people's writing - and
43% is what this audit actually does on them.

Two rules came out of it, both about the difference between what was seen and
what was concluded:

  7. A claim that something is present carries the words that make it present.
  8. A sentence that says nothing was found may only describe what was looked
     at.

The tests below pin both, and the individual fixes underneath them.
"""

from __future__ import annotations

import importlib.util
import io
import os
import re
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)

from audit_common import (  # noqa: E402
    content_soup, detect_challenge, dominant_script, looks_like_content,
    main_text, make_soup, sentence_with, sentences, sitemap_scope,
    sitemap_total_phrase, without_other_peoples_words, word_count,
    words_are_separated,
)
from page_extract import (  # noqa: E402
    DATE_TEXT_RE, _contact_facts, _declares_alt_dynamically, _headings,
    _postcode_in, _street_hint, text_without_urls,
)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FACTS = _module(os.path.join(ROOT, "skills", "fact-extractability-audit",
                             "scripts", "check.py"), "facts_round7")
COMPOSE = _module(os.path.join(SCRIPTS, "compose_report.py"), "compose_round7")


# --------------------------------------------------------------------------
# Rule 7: a claim that something is present carries the words
# --------------------------------------------------------------------------

def test_a_match_too_short_to_be_a_sentence_is_not_a_statement():
    """A menu label is not the site stating a fact."""
    assert sentence_with("Pricing", re.compile("Pricing")) is None


def test_a_present_verdict_produces_the_sentence_it_rests_on():
    text = "We have been based in Sheffield since the studio opened in 2011."
    quote = sentence_with(text, FACTS.SERVICE_AREA_RE)
    assert quote and "Sheffield" in quote


def test_feel_free_to_use_is_not_a_statement_that_something_is_free():
    """A documentation site's report said it states that it costs nothing.

    The words were "feel free to use those", in a paragraph about reusing
    example code. That false pass suppressed the honest finding.
    """
    assert not FACTS.COSTS_NOTHING_RE.search("feel free to use those examples")
    assert FACTS.COSTS_NOTHING_RE.search("Django is free to use and open source")


def test_a_bare_adverb_is_not_a_statement_of_where_a_business_operates():
    """"powers 70+ million accounts worldwide" was a sponsor's blurb about a
    different company, printed as the project's own service area."""
    assert not FACTS.SERVICE_AREA_RE.search("powers 70+ million accounts worldwide")
    assert not FACTS.SERVICE_AREA_RE.search("it improved things across the board")
    assert FACTS.SERVICE_AREA_RE.search("We deliver nationwide from three depots")
    assert FACTS.SERVICE_AREA_RE.search("The studio is based in Leeds")


def test_the_digits_inside_a_web_address_are_not_a_telephone_number():
    """A recipe blog's stated contact detail was 3922587923, which is the
    middle of a photo-sharing URL."""
    text = "More at photos.example.org/photos/someone/39225879230/ today"
    assert "39225879230" not in text_without_urls(text)
    facts = _contact_facts(text, make_soup("<html><body>{}</body></html>".format(text)))
    assert not facts["has_phone"]


# --------------------------------------------------------------------------
# Rule 8: silence describes what was looked at
# --------------------------------------------------------------------------

def test_the_robots_all_clear_says_what_it_set_aside():
    """A hospital's appendix said the only disallowed paths were the standard
    admin and cart routes. Its robots.txt closes a department's patient-guide
    documents and all of its video content to every crawler; those rules were
    filtered out before the sentence was written."""
    ca = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
                 "ca_round7")
    result = ca.SkillResult("crawl-access-audit")
    robots = {"status": 200, "groups": [{"agents": ["*"], "disallow": [
        "/-/media/orthopaedic/documents/patient-guides/", "/health/video-archive"],
        "allow": [], "crawl_delay": None}], "sitemaps": [], "errors": []}
    ca._check_robots_blocks(result, robots, "https://example.com")
    reason = next(r["reason"] for r in result.not_applicable
                  if r["check"] == "robots-blocks-content-paths")
    assert "set aside" in reason
    assert "unverified" in reason
    assert "the only disallowed paths are" not in reason


def test_a_check_that_reads_one_page_type_says_so_when_it_finds_none():
    """Twelve dated posts under /changelog/ and /now/ were typed `other`, and
    the freshness checks skipped with "no article or press pages were
    crawled" - about a crawl that had just read twelve of them."""
    fresh = _module(os.path.join(ROOT, "skills", "freshness-corroboration-audit",
                                 "scripts", "check.py"), "fresh_round7")
    pages = [
        {"url": "https://example.com/changelog/one", "page_type": "other",
         "dates": {"has_any": True}},
        {"url": "https://example.com/changelog/two", "page_type": "other",
         "dates": {"has_any": False}},
    ]
    result = fresh.SkillResult("freshness-corroboration-audit")
    fresh._check_date_signals(result, pages)
    assert result.findings, "the undated post in a dated section should be reported"
    assert "/changelog/two" in result.findings[0]["evidence"]


# --------------------------------------------------------------------------
# Text that is on the page and is not the page's own words
# --------------------------------------------------------------------------

BLOG_POST = """<html><body><main>
  <article><h1>Chiffon cake</h1><p>{article}</p></article>
  <div id="comments"><ol class="comment-list">
    <li id="comment-598456">Jayne says at 5:05 pm Reply {chatter}</li>
  </ol></div>
</main></body></html>""".format(article="The cake keeps three days in a tin. " * 8,
                                chatter="I made this twice and it worked. " * 80)


def test_reader_comments_are_not_the_pages_own_prose():
    """549 comments were counted as 30,184 words of the author's writing, and
    the page reported as written in blocks too large to skim."""
    text = main_text(make_soup(BLOG_POST))
    assert "Chiffon cake" in text
    assert "Jayne" not in text


def test_a_commenters_id_is_not_the_sites_postcode():
    facts = _contact_facts(*(lambda s: (s.get_text(" "), s))(
        without_other_peoples_words(make_soup(BLOG_POST))))
    assert "598456" not in (facts["postcode_hint"] or "")


def test_a_page_that_is_only_comments_keeps_them():
    """A forum thread and a question-and-answer page are their user content.
    Removing it there would delete the page rather than read it closely."""
    forum = "<html><body><main><h1>Thread</h1><div id=comments><ol class=comment-list>" \
            "<li>{}</li></ol></div></main></body></html>".format("a reply here. " * 60)
    assert len(main_text(make_soup(forum))) > 300


def test_a_customers_testimonial_is_not_the_sites_own_price():
    """"We save close to $30,000 per year" was recorded as a software
    company's pricing, on a page that has no price on it."""
    page = "<html><body><main><h1>Plan</h1><p>{}</p>" \
           "<div class=\"testimonial-card\"><p>We save close to $30,000 per year</p></div>" \
           "</main></body></html>".format("Coordinate work across teams. " * 10)
    assert "30,000" not in main_text(make_soup(page))


# --------------------------------------------------------------------------
# Scripts that do not write spaces between words
# --------------------------------------------------------------------------

JAPANESE = ("日本郵政グループは、郵便・物流事業を営んでおります。"
            "私たちは全国の郵便局ネットワークを通じて、サービスを提供しています。"
            "今後ともよろしくお願いいたします。")
ARABIC = ("نحن مؤسسة غير ربحية تعمل في مجال التعليم. "
          "نقدم خدماتنا في أكثر من عشرين دولة. اتصل بنا اليوم.")


def test_a_japanese_page_is_not_one_sentence():
    """Read for a full stop followed by a capital letter, a whole Japanese
    page is one sentence, and the check that measures sentence length skipped
    with "no page has enough prose to measure"."""
    assert len(sentences(JAPANESE)) == 3


def test_an_arabic_page_is_not_one_sentence():
    """Arabic has no capital letters, so the lookahead never fired."""
    assert len(sentences(ARABIC)) == 3


def test_a_full_stop_inside_a_name_still_does_not_end_a_sentence_alone():
    assert len(sentences("It opened in 1998. Patients come from three counties.")) == 2


def test_a_word_count_is_not_a_measurement_in_a_script_without_spaces():
    assert words_are_separated("The studio was founded in 2015 by two designers.")
    assert not words_are_separated(JAPANESE)
    assert words_are_separated(ARABIC)
    assert dominant_script("Hi") is None, "too short to characterise"


def test_a_japanese_postal_address_is_a_postal_address():
    """A postal group's contact page carries one and the report said no postal
    address was found anywhere on the site."""
    text = "所在地 〒100-8791 東京都千代田区大手町二丁目3番1号"
    assert _street_hint(text) is not None
    assert _postcode_in(text) is not None


def test_a_date_written_with_its_units_named_is_a_date():
    """2015年4月6日 sat under the headline of a page reported as carrying no
    date at all."""
    assert DATE_TEXT_RE.search("更新日 2015年4月6日")
    assert DATE_TEXT_RE.search("Published 6 April 2015")


# --------------------------------------------------------------------------
# A total is only a total if everything was counted
# --------------------------------------------------------------------------

INDEX = [
    {"url": "https://example.com/sitemap-index.xml", "status": 200, "urls": [],
     "url_count": 0, "lastmod_count": 0, "is_index": True,
     "child_sitemaps": ["https://example.com/s-{}.xml".format(n) for n in range(12)]},
]
INDEX += [{"url": "https://example.com/s-{}.xml".format(n), "status": 200,
           "urls": [], "url_count": 100, "lastmod_count": 0, "child_sitemaps": []}
          for n in range(3)]


def test_a_partial_sitemap_total_says_that_it_is_partial():
    """A documentation site was told it publishes 7,033 sitemap entries. It
    publishes 32,820 across twelve language sitemaps, three of which were
    read, and that number was then used as a denominator twice."""
    scope = sitemap_scope(INDEX)
    assert scope["urls_counted"] == 300
    assert not scope["complete"]
    phrase = sitemap_total_phrase(scope)
    assert phrase.startswith("at least 300")
    assert "12 sub-sitemaps" in phrase and "read 3" in phrase


def test_a_complete_sitemap_total_is_stated_plainly():
    plain = [{"url": "https://example.com/sitemap.xml", "status": 200, "urls": [],
              "url_count": 42, "lastmod_count": 0, "child_sitemaps": []}]
    assert sitemap_total_phrase(sitemap_scope(plain)) == "42 entries"


def test_a_site_wide_claim_states_how_much_of_the_site_was_read():
    """"No Organization markup anywhere on the site", from a 60-page crawl of
    a firm whose sitemap lists thousands of pages."""
    snapshot = {"sitemaps": INDEX, "pages": []}
    out = COMPOSE.state_the_coverage(
        "No Organization or LocalBusiness markup appears anywhere on the site.",
        snapshot, 60)
    assert "this crawl read 60" in out
    assert "not every page of the site" in out


def test_a_crawl_that_covered_the_site_says_nothing_extra():
    small = [{"url": "https://example.com/sitemap.xml", "status": 200, "urls": [],
              "url_count": 62, "lastmod_count": 0, "child_sitemaps": []}]
    text = "No Organization markup appears anywhere on the site."
    assert COMPOSE.state_the_coverage(text, {"sitemaps": small}, 60) == text


# --------------------------------------------------------------------------
# A verification page, recognised without knowing the vendor
# --------------------------------------------------------------------------

def test_a_challenge_page_from_an_unlisted_vendor_is_still_a_challenge():
    """A museum's homepage answered 429 with the title "Vercel Security
    Checkpoint" and the header `x-vercel-mitigated: challenge`. The appendix
    said no page answered with a verification page."""
    vendor = detect_challenge(
        "<html><title>Vercel Security Checkpoint</title><body>Verifying your browser</body></html>",
        "Vercel Security Checkpoint Verifying your browser",
        429, {"x-vercel-mitigated": "challenge"})
    assert vendor == "Vercel"


def test_a_javascript_shell_is_not_mistaken_for_a_challenge():
    """The single-page app's own noscript sentence is a real finding this
    audit must keep reporting, so it is deliberately not a challenge phrase."""
    assert detect_challenge(
        "<html><body><noscript>You need to enable JavaScript to run this app.</noscript></body></html>",
        "You need to enable JavaScript to run this app.", 200, {"server": "nginx"}) is None


def test_a_real_page_with_a_stray_header_is_not_a_challenge():
    assert detect_challenge("<html><body>{}</body></html>".format("real content " * 200),
                            "real content " * 200, 200,
                            {"x-cache-mitigated": "1"}) is None


# --------------------------------------------------------------------------
# Why a refusal happened is observed, not assumed
# --------------------------------------------------------------------------

CA7 = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
              "ca_refusal_round7")


def test_a_blanket_refusal_is_not_blamed_on_the_user_agent():
    """Three sites were told to allow-list crawler user agents. On all three
    the refusal followed the request whatever name it sent."""
    steps = CA7._refusal_steps(CA7.UA_BLIND)
    joined = " ".join(steps).lower()
    assert "do not start with the crawler allow-list" in joined
    assert CA7._refusal_steps(CA7.UA_KEYED)[1].lower().startswith("allow-list")


def test_an_untested_refusal_asks_for_the_comparison_first():
    first = CA7._refusal_steps(CA7.UA_UNTESTED)[0].lower()
    assert "compare" in first or "twice" in first


def test_the_cause_note_never_names_a_cause_that_was_not_tested():
    assert "not established" in CA7._refusal_cause_note(CA7.UA_UNTESTED)
    assert "not keyed to the user agent" in CA7._refusal_cause_note(CA7.UA_BLIND)


# --------------------------------------------------------------------------
# The rest, each from one site
# --------------------------------------------------------------------------

def test_a_heading_that_is_a_logo_is_still_a_heading():
    soup = make_soup('<html><body><h1><a href="/"><img src="l.gif" alt="Japan Post Holdings">'
                     '</a></h1></body></html>')
    assert _headings(soup)["h1"] == ["Japan Post Holdings"]


def test_an_alt_attribute_bound_by_a_template_is_an_alt_attribute():
    """51 images "with no alt attribute" were three components binding
    `:alt="product.title"`, whose src is a JavaScript expression."""
    bound = make_soup('<img :alt="p.title" src="x">').find("img")
    plain = make_soup('<img src="x">').find("img")
    assert _declares_alt_dynamically(bound)
    assert not _declares_alt_dynamically(plain)


def test_an_unrecognised_url_with_a_heading_and_text_is_content():
    """Everything the slug table does not recognise is typed `other`, which
    `content_only` then drops - so the checks see a slice and the report
    describes the site."""
    post = {"page_type": "other", "body_text_len": 900, "headings": {"h1": ["Release 5.2"]}}
    assert looks_like_content(post)


def test_a_page_type_excluded_on_purpose_stays_excluded():
    """`legal` is not `other`: it was recognised and left out deliberately."""
    privacy = {"page_type": "legal", "body_text_len": 4000, "headings": {"h1": ["Privacy"]}}
    assert not looks_like_content(privacy)


def test_a_tagline_after_a_dash_is_a_definition():
    """"Django - The web framework for perfectionists with deadlines." was
    reported as a site that never states in one sentence what it is."""
    found = FACTS._find_definition(
        "Django – The web framework for perfectionists with deadlines.", "Django", {})
    assert found and "web framework" in found


def test_a_hyphenated_name_is_not_read_as_a_definition():
    assert FACTS._find_definition("Marks-and-Spencer sells clothing", "Marks", {}) is None


def test_a_price_line_is_quotable_even_though_it_is_short():
    """A software company's pricing page - "Basic $10 per user/month" - was
    listed as having nothing an assistant could quote."""
    assert COMPOSE.PRICE_LINE_RE.search("Basic $10 per user/month")
    assert COMPOSE.CONTACT_LINE_RE.search("For other questions, email us hello@example.com")


# --------------------------------------------------------------------------
# The escape that was not an escape
# --------------------------------------------------------------------------

def test_no_source_file_contains_a_control_character():
    """`\\b` written into a patch through a shell heredoc arrived as a
    backspace, and two regular expressions in this repo silently matched
    nothing for it. A corrupted pattern looks exactly like a working one.
    """
    offenders = []
    for base in ("skills", "tests"):
        for directory, _, files in os.walk(os.path.join(ROOT, base)):
            if "__pycache__" in directory:
                continue
            for filename in files:
                if not filename.endswith((".py", ".md", ".json")):
                    continue
                path = os.path.join(directory, filename)
                with io.open(path, encoding="utf-8", errors="ignore") as handle:
                    text = handle.read()
                for index, char in enumerate(text):
                    if ord(char) < 32 and char not in "\n\r\t":
                        offenders.append("{}: U+{:04X} at offset {}".format(
                            os.path.relpath(path, ROOT), ord(char), index))
                        break
    assert not offenders, "control characters in source:\n" + "\n".join(offenders)
