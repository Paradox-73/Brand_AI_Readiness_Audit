#!/usr/bin/env python3
"""fact-extractability-audit: can a machine quote a clear fact? (mechanisms B, C)

The question this skill asks is narrower and harder than "is there content":
if an assistant fetched this page to answer "what is X", "how much does X cost"
or "where is X", is there a sentence it could lift verbatim and stand behind?

Makes no network requests.

Usage:
    python check.py --snapshot snapshot.json --out fact-extractability-audit.findings.json
"""

from __future__ import annotations

import argparse
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))

# The marketplace's shared library: one definition of the finding schema, the
# root-cause vocabulary and the page-type detector, so six skills cannot drift
# apart on any of the three.
#
# Looked for beside this file first, then in the orchestrator. `package.py`
# writes a copy into every skill directory when it builds the submission, so a
# skill folder lifted out on its own still runs; the checkout keeps a single
# source of truth so the copies cannot diverge from it.
_SHARED_CANDIDATES = (
    _HERE,
    os.path.join(os.path.dirname(os.path.dirname(_HERE)), "audit-orchestrator", "scripts"),
)
_SHARED = next(
    (path for path in _SHARED_CANDIDATES
     if os.path.isfile(os.path.join(path, "audit_common.py"))),
    None,
)
if _SHARED is None:
    raise SystemExit(os.linesep.join([
        "Cannot find the shared library that this skill depends on.",
        "  Looked in: " + "; ".join(_SHARED_CANDIDATES),
        "",
        "This skill reads audit_common.py, which should sit either beside this",
        "file or in skills/audit-orchestrator/scripts/. Copy the whole",
        "marketplace, or rebuild the submission with package.py.",
        "",
        "To perform these checks without it, follow the Procedure section of",
        "this skill's SKILL.md by hand. It states every check in prose and",
        "produces the same findings.",
    ]))
sys.path.insert(0, _SHARED)

from audit_common import (  # noqa: E402
    example_urls, has_price, language_of, load_snapshot, looks_like_soft_404,
    name_forms, pages_of, pct, plural, prose_skip_reason, sample,
    sells_something, sentences, SkillResult, truncate, word_count
)

SKILL = "fact-extractability-audit"

# Thresholds and why they sit here.
DEFINITION_WINDOW_WORDS = 150   # an assistant reads the top of a page first; a definition below this is rarely used
MIN_DEFINITION_PREDICATE = 20   # "Acme is a company." is technically a definition and tells nobody anything
ANSWER_FIRST_MIN_SECTIONS = 3   # below 3 sections the share is noise
FLUFF_FIRST_SHARE = 0.7         # 50% fired on 69% of real sites; a section opening with context
                                # is normal writing, so only a page that almost never leads with
                                # a fact is worth reporting
SLOGAN_HEADING_SHARE = 0.8      # nearly every heading using words the page never uses again
SLOGAN_MIN_SECTIONS = 5         # below 5 headings the share is one bad heading, not a pattern
LONG_SENTENCE_SHARE = 0.4       # 25% fired on 76% of real sites; technical prose runs long,
                                # and only a page where most sentences are unliftable is a defect

# How much of a site has to be readable before "the site never states X" is a
# claim worth making. Half: below that the sample is smaller than the part it
# is describing, and the missing half is exactly where an about page or a
# contact page would be. Measured against the two blocked sites that produced
# the false findings - 4 of 60 and 2 of 60 readable - and against every
# unblocked site in the same rounds, none of which fell below 0.9.
CORE_FACTS_MIN_COVERAGE = 0.5

# Page types where a visitor arrives with a specific question, so the first
# paragraph of each section is expected to answer it.
ANSWER_FIRST_TYPES = ("pricing", "faq", "product", "service", "location", "comparison")

COPULAR_RE_TEMPLATE = r"\b{brand}\b\s+(?:is|are|was|remains)\s+(?:an?|the)?\s*(?P<rest>[^.!?]{{{minlen},400}})"

CONCRETE_VALUE_RE = re.compile(
    r"\d"                                                    # any number
    r"|[$€£¥₹]"                                              # any currency mark
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b"
    r"|\b(?:is|are)\s+(?:an?|the)\b"                         # a definition
    r"|\b(?:means|refers to|defined as|consists of|includes)\b",
    re.I,
)

# Two ways of answering "what does it cost" other than with a number. Both are
# real answers a machine can quote; only silence is a defect. The second exists
# because the check asked a free open-source project for its pricing, and "free
# and open source" is a complete answer to the question.
#
# They used to share one pattern, and one wording. A free software project's
# report then said its homepage "states pricing is on request" - a sentence
# that appears nowhere on it, produced by describing a match on "free
# open-source" with the words written for "talk to sales". A report whose whole
# subject is unverifiable claims about a brand should not make one, so the two
# are matched separately and each described in its own words.
QUOTE_ON_REQUEST_RE = re.compile(
    r"\b(?:contact (?:us )?for (?:a )?(?:price|pricing|quote)"
    r"|request a quote|custom(?:ised|ized)? pricing|pricing on (?:request|application)"
    r"|talk to sales|get a quote|poa)\b", re.I)
COSTS_NOTHING_RE = re.compile(
    r"\b(?:free (?:and )?open[- ]source|open[- ]source(?: and)? free"
    r"|completely free|entirely free|always free|free to (?:use|download|install)"
    r"|no (?:cost|charge|licence fee|license fee)|free of charge"
    r"|costs? nothing|zero cost)\b", re.I)

FOUNDING_FACT_RE = re.compile(
    r"\b(?:founded|established|incorporated|started|launched|since|operating since|"
    r"in business since)\b[^.!?]{0,60}\b(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\b[^.!?]{0,30}\b(?:founded|established|incorporated)\b", re.I)

TEAM_FACT_RE = re.compile(
    r"\b(?:our team|the team|founder|co-founder|chief executive|ceo|managing director|"
    r"employees|people work|staff of|headed by|led by|partners?)\b", re.I)

SERVICE_AREA_RE = re.compile(
    r"\b(?:serving|we serve|available (?:in|across|throughout)|operating (?:in|across)|"
    r"customers (?:in|across)|nationwide|worldwide|across the|based in|located in)\b", re.I)

STOPWORDS = frozenset("""
a an the and or but for nor so yet of to in on at by with from as is are was were be been being
your our their its it this that these those we you they he she what how why when where which who
whom whose can could will would shall should may might must do does did done have has had not no
more most other some such only own same than too very just about into over under again further
then once here there all any both each few own s t don now
""".split())


def run(snapshot):
    result = SkillResult(SKILL)
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}
    brand_name = (brand.get("name") or "").strip()

    if not pages:
        for name in ("entity-definition", "heading-hierarchy", "answer-first-paragraphs",
                     "core-facts-present", "brand-naming-consistency", "long-sentences"):
            result.skip(name, "no content pages returned HTTP 200")
        return result

    # Three of these six reason about English sentences. The other three are
    # structural - heading order, name agreement, whether a price appears as a
    # number - and hold in any language, so they run regardless.
    language = language_of(snapshot)
    result.signal("site_language", language.get("code") or "undetermined")
    result.signal("site_language_source", language.get("source", ""))

    if language.get("prose_checks_apply"):
        _check_entity_definition(result, snapshot, pages, brand_name, brand)
        _check_answer_first(result, snapshot, pages, brand_name)
        _check_long_sentences(result, pages)
    else:
        reason = prose_skip_reason(language)
        for name in ("entity-definition", "answer-first-paragraphs", "long-sentences"):
            result.skip(name, reason)

    _check_heading_hierarchy(result, pages_of(snapshot))
    # Every page the crawl read, not only the ones the classifier recognised.
    #
    # This check asks "does the site state its price / contact / location
    # anywhere". It was reading only pages typed home, about, faq or article -
    # so on a site with sixty crawled pages and four classified, it reported
    # "no price appears anywhere on the crawled pages" while a support page in
    # the same snapshot carried five prices the crawler had already extracted.
    # The evidence sentence claimed the whole site from a sample it never
    # disclosed. Four of five confirmed false positives on real sites came from
    # this one line.
    _check_core_facts(result, snapshot, pages_of(snapshot), brand_name,
                      english=bool(language.get("prose_checks_apply")))
    _check_naming_consistency(result, snapshot, pages, brand)
    return result


# --------------------------------------------------------------------------

def _brand_pattern(brand_name):
    """Match the brand name allowing for extra internal whitespace."""
    tokens = [re.escape(t) for t in brand_name.split() if t]
    if not tokens:
        return None
    return r"\s+".join(tokens)


def _brand_forms(brand_name, brand):
    """Every way the site could reasonably refer to itself in a sentence.

    A site is not obliged to write its full legal or formal name as the subject
    of its own definition. "Department of Computer Science, University of
    Oxford" is a correct declared name and will never appear verbatim in the
    sentence "The Department is one of the largest in the UK". Requiring the
    whole string made the check unsatisfiable for any organisation with a long
    formal name - which is most institutions, and 81% of a holdout sample.

    Returns the declared name, every variant the site asserts, and the short
    forms derivable from them, longest first so the most specific match wins.
    """
    forms = {brand_name}
    forms.update(v for v in (brand.get("authoritative_variants") or []) if v)
    forms.update(v for v in (brand.get("alternate_names") or []) if v)

    for value in list(forms):
        forms.update(name_forms(value))

    return sorted({f for f in forms if len(f) > 2}, key=len, reverse=True)


def _top_words(page, limit=DEFINITION_WINDOW_WORDS):
    """The opening of the page: headings plus the first N words of body text."""
    headings = page.get("headings") or {}
    lead = " ".join((headings.get("h1") or [])[:1] + (headings.get("h2") or [])[:2])
    body = page.get("body_text", "")
    words = body.split()
    return "{} {}".format(lead, " ".join(words[:limit])).strip()


def _find_definition(text, brand_name, brand=None):
    """A quotable one-line definition: `<Brand> is a <category> that ...`.

    Tried against every form the site could use for itself, longest first, so a
    match reports the most specific subject the sentence actually used.
    """
    for form in _brand_forms(brand_name, brand or {}):
        pattern = _brand_pattern(form)
        if not pattern:
            continue
        regex = re.compile(
            COPULAR_RE_TEMPLATE.format(brand=pattern, minlen=MIN_DEFINITION_PREDICATE), re.I)
        match = regex.search(text)
        if match:
            return truncate(match.group(0), 300)
    return None


def _check_entity_definition(result, snapshot, pages, brand_name, brand=None):
    """The single most quotable fact on any site: what this brand actually is.

    Assistants need an identity sentence before they can say anything else
    about a brand, and it is the sentence most sites never write.
    """
    result.check("entity-definition")
    if not brand_name:
        result.skip("entity-definition",
                    "no brand name could be determined from structured data, og:site_name or "
                    "the page title, so a definition sentence cannot be looked for")
        return

    identity = [p for p in pages if p["page_type"] in ("home", "about")]
    if not identity:
        result.skip("entity-definition",
                    "neither a home nor an about page was crawled")
        return

    found = None
    for page in identity:
        definition = _find_definition(_top_words(page), brand_name, brand)
        if definition:
            found = (page, definition)
            break

    result.signal("entity_definition_found", bool(found))
    if found:
        page, definition = found
        result.signal("entity_definition", definition)
        result.skip("entity-definition",
                    'a quotable definition is present on {}: "{}"'.format(page["url"], definition))
        return

    # Distinguish "the brand is named but never defined" from "the brand is
    # never named at the top of its own homepage", because the fixes differ.
    home = identity[0]
    top = _top_words(home)
    named = any(re.search(_brand_pattern(form), top, re.I)
                for form in _brand_forms(brand_name, brand or {})
                if _brand_pattern(form))
    pronouns = len(re.findall(r"\b(?:we|our|us)\b", top, re.I))

    if not named and pronouns >= 3:
        evidence = ('The first {} words of {} use "we" or "our" {} times and never state the '
                    'brand name "{}". A machine reading this page cannot tell whose site it '
                    'is.'.format(DEFINITION_WINDOW_WORDS, home["url"], pronouns, brand_name))
        root = "no-entity-definition"
    else:
        evidence = ('No sentence of the form "{} is a ..." appears in the first {} words or the '
                    'opening headings of {}{}.'.format(
                        brand_name, DEFINITION_WINDOW_WORDS, home["url"],
                        " or the about page" if len(identity) > 1 else ""))
        root = "no-entity-definition"

    result.add(
        id_hint="no-quotable-entity-definition",
        title="No page states in one sentence what the brand is",
        # `high` only when the brand is never named at the top of its own
        # homepage, which is genuinely broken. Being named but not defined is
        # the common case - it holds for most sites, including ones assistants
        # name readily - so it is a strong recommendation, not a severe defect.
        severity="high" if (not named and pronouns >= 3) else "medium",
        confidence="high",
        evidence=evidence,
        mechanism="B", root_cause=root,
        summary='Add one sentence near the top of the homepage: "{} is a <category> that '
                '<does what> for <whom>."'.format(brand_name),
        how_to_fix=[
            "Write the sentence in that exact shape. Name the brand as the subject; do not "
            "open with \"We\".",
            "Put it in the first paragraph of the homepage, in plain HTML text, not inside an "
            "image or a slider.",
            "Repeat the same sentence verbatim on the about page, in the Organization "
            "`description` property, on your LinkedIn page and in your press kit.",
            "Keep it under 30 words and make it specific enough that a competitor could not "
            "use the same sentence.",
        ],
        effort="low", owner="marketing",
        rationale="Assistants quote what is easy to lift. Identity is the first "
                  "thing they need and the hardest thing to infer from marketing copy, so a "
                  "brand with no definition sentence gets described in whatever words a third "
                  "party used instead.",
        affected_pages=[p["url"] for p in identity],
        snippet="<h1>{brand}</h1>\n"
                "<p><strong>{brand} is a &lt;category&gt; that &lt;does what&gt; for "
                "&lt;whom&gt;.</strong> &lt;One more sentence with a concrete fact: a number, "
                "a place, or a name.&gt;</p>".format(brand=brand_name),
    )


def _check_heading_hierarchy(result, pages):
    """Every page a machine can fetch, not only the content-typed ones.

    An `<h1>` is expected on any page. Read across content types only, this
    reported "3 page(s) have no H1" on a site whose own crawl data showed 15
    without one - release notes, catalogue pages and an archive index, all
    typed `other` and all silently outside the sample. Someone fixing the three
    named would have left twelve behind.

    A page whose own title says it is missing is left out: an error page has no
    heading because there is nothing to head.
    """
    result.check("heading-hierarchy")
    pages = [p for p in pages if not looks_like_soft_404(p)]
    no_h1 = [p for p in pages if len(p.get("headings", {}).get("h1") or []) == 0]
    # Multiple H1s are valid HTML5 sectioning, and skipped heading levels are
    # near-universal on real sites. Neither stops a machine reading the page.
    # Reporting them fired this check on 27 of 29 real sites and drowned the
    # findings that matter, so only a page with no top-level heading at all
    # counts now. See references/cited-vs-uncited-study.md.
    many_h1, skipped = [], []

    if not (no_h1 or many_h1 or skipped):
        result.skip("heading-hierarchy",
                    "every crawled content page has exactly one H1 and no skipped heading levels")
    else:
        problems = []
        affected = set()
        if no_h1:
            problems.append("{} of {} crawled page(s) have no H1".format(
                len(no_h1), len(pages)))
            affected.update(p["url"] for p in no_h1)
        if many_h1:
            problems.append("{} page(s) have more than one H1".format(len(many_h1)))
            affected.update(p["url"] for p in many_h1)
        if skipped:
            problems.append("{} page(s) skip a heading level".format(len(skipped)))
            affected.update(p["url"] for p in skipped)

        result.add(
            id_hint="heading-structure-unclear",
            title="Heading structure does not describe the page reliably",
            severity="medium" if (no_h1 or many_h1) else "low", confidence="high",
            evidence="{}. Examples: {}.".format("; ".join(problems),
                                                ", ".join(example_urls(sorted(affected)))),
            mechanism="C", root_cause="heading-structure",
            summary="Give every page exactly one H1 that names its subject, and use H2/H3 in order.",
            how_to_fix=[
                "Set one H1 per page stating what the page is about, not a slogan.",
                "Demote extra H1s to H2. Styling can stay the same; only the tag changes.",
                "Do not skip levels: an H4 should follow an H3, not an H2.",
            ],
            effort="low", owner="developer",
            rationale="Headings are how a machine works out which part of a long "
                      "page answers which question. Broken structure means the whole page is "
                      "treated as one undifferentiated block.",
            affected_pages=sorted(affected),
        )

    _check_slogan_headings(result, pages)


def _check_slogan_headings(result, pages):
    """H2s whose vocabulary appears nowhere in the page are slogans.

    A slogan heading ("Built to fly", "Dream bigger") tells a machine nothing
    about what follows, so a page of them has no navigable structure.

    The comparison is against the whole page text rather than the paragraph
    directly beneath, and the bar is set deliberately high. A good heading
    often does not repeat itself in its own first sentence - "Starter plan"
    followed by "$480 per month" is correct writing - and the earlier, stricter
    version of this check flagged exactly that. Only a page where nearly every
    heading is vocabulary the page never uses again is reported.
    """
    result.check("headings-name-their-topic")
    candidates = [p for p in pages if len(_content_sections(p)) >= SLOGAN_MIN_SECTIONS]
    if not candidates:
        result.skip("headings-name-their-topic",
                    "no crawled page has {} or more H2 sections, so heading vocabulary cannot "
                    "be assessed meaningfully".format(SLOGAN_MIN_SECTIONS))
        return

    offenders = []
    for page in candidates:
        body = page.get("body_text", "").lower()
        judged = 0
        disconnected = 0
        for section in _content_sections(page):
            tokens = {w for w in re.findall(r"[a-z]{4,}", section["heading"].lower())
                      if w not in STOPWORDS}
            # A heading made entirely of short or common words ("Who it is for")
            # carries no vocabulary to match, so it is not evidence either way.
            if not tokens:
                continue
            judged += 1
            if not any(t[:6] in body for t in tokens):
                disconnected += 1
        if judged >= SLOGAN_MIN_SECTIONS and disconnected / float(judged) > SLOGAN_HEADING_SHARE:
            offenders.append((page, disconnected, judged))

    if not offenders:
        result.skip("headings-name-their-topic",
                    "headings on the crawled pages use vocabulary that appears in the page text")
        return

    result.add(
        id_hint="headings-are-slogans",
        title="{} slogan headings that do not name their topic".format(
            plural(len(offenders), "page uses", "pages use")),
        severity="low", confidence="medium",
        evidence="Pages where over {}% of H2s use vocabulary that appears nowhere else in the "
                 "page text: {}.".format(
                     int(SLOGAN_HEADING_SHARE * 100),
                     "; ".join("{} ({}/{} headings)".format(p["url"], d, t)
                               for p, d, t in sorted(offenders, key=lambda x: x[0]["url"])[:4])),
        mechanism="B", root_cause="heading-structure",
        summary="Rewrite section headings to name what the section is about.",
        how_to_fix=[
            'Replace slogans with the question or topic: "Built for speed" becomes '
            '"How fast the platform delivers results".',
            "Use the words a customer would use, and the words that appear in the paragraph below.",
            "Keep the slogan as a subheading or in the body copy if it matters to the brand.",
        ],
        effort="low", owner="content owner",
        rationale="A heading is a machine's index into a long page. A heading that "
                  "does not name its topic means the section beneath it cannot be matched to a "
                  "question.",
        affected_pages=[p["url"] for p, _, _ in offenders],
    )


def _content_sections(page):
    """Sections that make a claim, excluding related-links and share blocks.

    Judging a "Read next" block for whether it opens with a number would
    penalise exactly the wayfinding this marketplace recommends elsewhere.
    """
    return [s for s in (page.get("sections") or [])
            if not s.get("navigational") and len(s.get("first_paragraph") or "") >= 40]


def _check_answer_first(result, snapshot, pages, brand_name):
    result.check("answer-first-paragraphs")
    sells = sells_something(snapshot, pages)
    candidates = [p for p in pages
                  if p["page_type"] in ANSWER_FIRST_TYPES
                  and len(_content_sections(p)) >= ANSWER_FIRST_MIN_SECTIONS]
    if not candidates:
        result.skip("answer-first-paragraphs",
                    "no pricing, FAQ, product, service, location or comparison page with at "
                    "least {} sections was crawled".format(ANSWER_FIRST_MIN_SECTIONS))
        result.signal("fluff_first", False)
        return

    total_sections = 0
    fluff_sections = 0
    offenders = []
    examples = []
    for page in candidates:
        sections = _content_sections(page)
        fluff = [s for s in sections
                 if not CONCRETE_VALUE_RE.search(_first_sentence(s["first_paragraph"]))]
        total_sections += len(sections)
        fluff_sections += len(fluff)
        if len(sections) and len(fluff) / float(len(sections)) > FLUFF_FIRST_SHARE:
            offenders.append((page, len(fluff), len(sections)))
            if fluff and len(examples) < 2:
                examples.append('{} - "{}" opens with: "{}"'.format(
                    page["url"], truncate(fluff[0]["heading"], 60),
                    truncate(_first_sentence(fluff[0]["first_paragraph"]), 110)))

    result.signal("fluff_first", bool(offenders))
    result.signal("fluff_first_share", round(fluff_sections / float(total_sections), 3) if total_sections else 0.0)

    if not offenders:
        result.skip("answer-first-paragraphs",
                    "{} of {} sections on question-answering pages open with a concrete value, "
                    "so they are already answer-first".format(
                        total_sections - fluff_sections, total_sections))
        return

    result.add(
        id_hint="sections-do-not-answer-first",
        title="{} their sections with warm-up prose instead of the answer".format(
            plural(len(offenders), "page opens", "pages open")),
        severity="medium", confidence="medium",
        evidence="{} of {} sections on pages a buyer visits with a specific question ({}%) begin "
                 "with no number, date, price or definition. {}".format(
                     fluff_sections, total_sections, pct(fluff_sections, total_sections),
                     " ".join(examples)),
        mechanism="B", root_cause="fluff-first",
        summary="Put the concrete answer in the first sentence of every section, then explain it.",
        how_to_fix=[
            "For each section, move the number, price, date or definition into sentence one.",
            "Keep the context and the persuasion; they work better after the fact than before it.",
            'Test each section by reading only its first sentence: does it answer the heading? '
            "If not, it is not answer-first yet.",
            "Add a two-to-three sentence summary block at the top of each key page containing "
            "the facts you most want quoted.",
        ],
        effort="medium", owner="content owner",
        rationale="An assistant lifts a short passage, usually the opening of the "
                  "most relevant section. If the opening is throat-clearing, the passage that "
                  "gets quoted contains no facts, and a competitor's page gets used instead.",
        affected_pages=[p["url"] for p, _, _ in offenders],
        # The example used to be a SaaS pricing table, and it was generated for
        # a charity's abortion-care FAQ, a free database engine, an open-source
        # video toolkit and a museum. What the snippet demonstrates is a shape -
        # heading, then the concrete answer in sentence one - so the example is
        # drawn from what this site actually is.
        snippet=_answer_first_example(sells, brand_name),
    )


def _answer_first_example(sells, brand_name):
    """A worked example of answer-first writing, in this site's own terms."""
    name = brand_name or "The brand"
    if sells:
        return ("<h2>How much does it cost?</h2>\n"
                "<p><strong>{} costs &lt;price&gt; per &lt;unit&gt;.</strong> That includes "
                "&lt;what is included&gt;. &lt;Then the context and the persuasion.&gt;</p>"
                .format(name))
    return ("<h2>What is {0}?</h2>\n"
            "<p><strong>{0} is a &lt;category&gt; for &lt;who it is for&gt;, "
            "&lt;the one fact that matters most&gt;.</strong> &lt;Then the context and the "
            "persuasion.&gt;</p>".format(name))


def _first_sentence(text):
    parts = sentences(text)
    return parts[0] if parts else (text or "")


def _check_core_facts(result, snapshot, pages, brand_name, english=True):
    """The five facts someone asks an assistant for, checked one at a time.

    "This site never states X" is a claim about the site, and it is only worth
    making if most of the site was read. On two blocked sites it was made from
    four pages of sixty and two of sixty: a charity was told it never states
    its founding facts while `/who-we-are/our-history` - listed in the same
    report's own crawl table, marked 403 - opens "founded in 1971 in France",
    and a museum was told it never states a contact method while `/contacts`
    sat in the same table under the same block.

    That is not a content problem the owner can fix by writing anything. The
    report already carries the finding that explains it, and repeating it as
    four content defects charges them twice for one cause.
    """
    result.check("core-facts-present")
    attempted = len(snapshot.get("pages") or [])
    if attempted and len(pages) < attempted * CORE_FACTS_MIN_COVERAGE:
        result.skip("core-facts-present",
                    "only {} of the {} URLs the crawl reached could be read - the rest were "
                    "refused or challenged - so whether the site states these facts could not "
                    "be determined. What it publishes on the pages this crawler was not "
                    "allowed to see is unknown, and the access finding above is the one to "
                    "act on".format(len(pages), attempted))
        return
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)

    missing = []
    found = {}

    # 1. Price, or an explicit statement that pricing is on request.
    price_page = next((p for p in pages if has_price(p.get("body_text", ""))), None)
    on_request = next((p for p in pages
                       if QUOTE_ON_REQUEST_RE.search(p.get("body_text", ""))), None)
    free_page = next((p for p in pages
                      if COSTS_NOTHING_RE.search(p.get("body_text", ""))), None)
    if price_page:
        found["pricing"] = price_page["url"]
    elif free_page:
        found["pricing"] = "{} (states that it costs nothing)".format(free_page["url"])
    elif on_request:
        found["pricing"] = "{} (states pricing is on request)".format(on_request["url"])
    elif not sells_something(snapshot, pages):
        # "This site never states its pricing" is only a defect if the site has
        # a price. A medical charity was told it, and handed a fix reading
        # "contact sales for a quote".
        found["pricing"] = ("not applicable - nothing on this site is for sale, so there is "
                            "no price for an assistant to be missing")
    else:
        has_pricing_page = bool(by_type.get("pricing"))
        missing.append(("pricing", "high" if has_pricing_page else "medium",
                        "a pricing page was crawled but shows no figure and no "
                        "'contact us for pricing' statement" if has_pricing_page
                        else "no price and no 'pricing on request' statement appears anywhere "
                             "on the crawled pages"))

    # 2. Where the business is, or who it serves.
    address_page = next((p for p in pages if (p.get("contact_facts") or {}).get("has_address")), None)
    # Also an English pattern ("serving", "based in", "available across"), so
    # it only speaks where it can read the language.
    area_page = next((p for p in pages
                      if SERVICE_AREA_RE.search(p.get("body_text", ""))), None) if english else None
    local_signals = bool(by_type.get("location")) or any(
        "localbusiness" in {t.lower() for t in p.get("jsonld_types") or []} for p in pages)
    if address_page:
        found["location"] = address_page["url"]
    elif area_page:
        found["service area"] = area_page["url"]
    elif not english:
        found["location"] = ("not checked - no postal address was found, and the pattern for "
                             "a stated service area is English while this site is not")
    else:
        missing.append(("location or service area", "high" if local_signals else "medium",
                        "no postal address and no statement of where the business operates "
                        "appears in the page text"))

    # 3. A way to make contact.
    contact_page = next((p for p in pages
                         if (p.get("contact_facts") or {}).get("has_email")
                         or (p.get("contact_facts") or {}).get("has_phone")), None)
    form_page = next((p for p in pages if (p.get("contact_facts") or {}).get("has_contact_form")), None)
    if contact_page:
        found["contact"] = contact_page["url"]
    elif form_page:
        found["contact"] = "{} (form only, no email or phone in text)".format(form_page["url"])
    else:
        missing.append(("contact method", "medium",
                        "no email address, telephone number or contact form was found in the "
                        "text of any crawled page"))

    # 4. Founding or team facts - the corroborating detail that separates one
    #    brand from another with a similar name.
    #
    #    Both patterns are English: "founded", "established", "our team", "led
    #    by". Run on a French bakery's site they matched nothing, and the report
    #    said no founding year appears in the page text - while its own
    #    "what an assistant would quote" table, for the same homepage, quoted
    #    "Creee en 1932 par Pierre Poilane". One section of the report asserted
    #    the fact was absent from a page the next section quoted it from.
    #
    #    Everything else in this check is structural - a price is a number, a
    #    telephone is a `tel:` link - so only this pair is language-gated.
    fact_page = team_page = None
    if english:
        fact_page = next((p for p in pages
                          if FOUNDING_FACT_RE.search(p.get("body_text", ""))), None)
        team_page = next((p for p in pages
                          if TEAM_FACT_RE.search(p.get("body_text", ""))), None)
    if not english:
        found["founding facts"] = ("not checked - the patterns for a founding year and a "
                                   "named team are English, and this site is not in English")
    elif fact_page:
        found["founding facts"] = fact_page["url"]
    elif team_page:
        found["team facts"] = team_page["url"]
    else:
        missing.append(("founding or team facts", "medium",
                        "no founding year and no named team or leadership detail appears in "
                        "the page text"))

    result.signal("core_facts_found", sorted(found.keys()))
    result.signal("core_facts_missing", [name for name, _, _ in missing])

    if not missing:
        result.skip("core-facts-present",
                    "all four core facts are stated in plain text: {}".format(
                        "; ".join("{} on {}".format(k, v) for k, v in sorted(found.items()))))
        return

    for name, severity, why in missing:
        result.add(
            id_hint="core-fact-missing-{}".format(re.sub(r"[^a-z]+", "-", name.lower()).strip("-")),
            title="The site never states its {} in plain text".format(name),
            severity=severity, confidence="medium",
            # Say what was searched. "anywhere on the crawled pages" read as a
            # whole-site claim built from a sample the reader could not see.
            evidence="Across the {} of {} page(s) this audit read, {}. A crawl is a "
                     "sample: a page it did not reach may say it.".format(
                         len(pages), (snapshot.get("crawl") or {}).get("pages_crawled", len(pages)),
                         why),
            mechanism="B", root_cause="missing-core-fact",
            summary="State the {} explicitly, in a sentence, on the page where a visitor would "
                    "look for it.".format(name),
            how_to_fix=_core_fact_steps(name, brand_name),
            effort="low", owner="content owner",
            rationale="An assistant answers with facts it can quote. A fact that "
                      "is implied, shown only in an image, or held only in a form nobody fills "
                      "in is a fact it will not state, so the brand loses that question to "
                      "whoever did write it down.",
            affected_pages=sorted({p["url"] for p in pages if p["page_type"] in
                                   ("home", "about", "contact", "pricing")})[:5],
        )


def _core_fact_steps(name, brand_name):
    brand = brand_name or "the brand"
    if name == "pricing":
        return [
            "Publish the actual figures on a pricing page, even as a starting-from number.",
            'If you genuinely cannot publish prices, write the sentence explicitly: "{} does '
            'not publish prices; contact sales for a quote, typically returned within N days." '
            "An explicit statement is quotable; silence is not.".format(brand),
            "Add the same figure to Product/Offer structured data so it is unambiguous.",
        ]
    if name.startswith("location"):
        return [
            "Put the full postal address in text on the contact page, inside an <address> element.",
            'If the business has no public premises, state the service area instead: '
            '"{} serves customers across &lt;regions&gt;."'.format(brand),
            "Repeat the address in the footer of every page and in Organization structured data.",
        ]
    if name == "contact method":
        return [
            "Put an email address and a telephone number in text, not only behind a form.",
            "Use mailto: and tel: links so both a person and a machine can act on them.",
            "State response times: an answer time is itself a quotable fact.",
        ]
    return [
        'Add a short paragraph on the about page: "{} was founded in &lt;year&gt; in '
        '&lt;place&gt; by &lt;names&gt;."'.format(brand),
        "Name the leadership team with their roles.",
        "These details are what distinguishes you from another company with a similar name.",
    ]


def _check_naming_consistency(result, snapshot, pages, brand):
    """The same brand written several ways reads as several brands.

    Only names the site *asserts* as its identity are compared: Organization
    `name` and og:site_name. Names guessed from a <title> are excluded, because
    "Brand | Tagline" splits into a brand and a tagline, and treating the
    tagline as a name variant would fire this finding on almost every site.
    """
    result.check("brand-naming-consistency")
    variants = [v for v in (brand.get("authoritative_variants") or []) if v]

    # Zero is not one-fewer-than-two. A site that asserts its own name nowhere
    # a machine reads has not passed this check; it has failed the thing the
    # check presupposes. Reporting "nothing to disagree with" put that in the
    # appendix under "run and found nothing to report", beside genuine passes,
    # where a reader takes it for a clean result. A check declining because its
    # own prerequisite is missing has to say so in the finding list, not the
    # exemption list.
    if not variants:
        result.skip("brand-naming-consistency",
                    "the site never asserts its own name in a form a machine reads - no "
                    "Organization `name` and no og:site_name - so there are no declared "
                    "variants to compare. That absence is reported as a finding in its own "
                    "right rather than counted as a pass here")
        result.signal("authoritative_name_declarations", 0)
        return

    if len(variants) < 2:
        result.skip("brand-naming-consistency",
                    "the site declares its name in {} authoritative place(s) ({}), so there is "
                    "nothing to disagree with. Names guessed from page titles were deliberately "
                    "not compared.".format(
                        len(variants), ", ".join(brand.get("authoritative_sources") or []) or "none"))
        return

    normalised = {}
    for variant in variants:
        key = re.sub(r"[^a-z0-9]+", "", variant.lower())
        if len(key) < 3:
            continue
        normalised.setdefault(key, set()).add(variant)

    # Variants that normalise to the same string differ only in spacing or
    # capitalisation; variants that normalise differently are separate names.
    spelling_conflicts = [group for group in normalised.values() if len(group) > 1]
    distinct = list(normalised.keys())
    declared_alternates = {re.sub(r"[^a-z0-9]+", "", a.lower()) for a in brand.get("alternate_names") or []}

    # A second name already declared as `alternateName` is not a contradiction:
    # the site has explicitly said the two refer to one entity.
    undeclared = [k for k in distinct if k not in declared_alternates]

    if not spelling_conflicts and len(undeclared) < 2:
        result.skip("brand-naming-consistency",
                    "the brand name is written consistently, or the alternative forms are "
                    "declared in Organization `alternateName`")
        return

    detail = []
    if spelling_conflicts:
        detail.append("the same name is written as {}".format(
            " / ".join('"{}"'.format(x) for group in spelling_conflicts for x in sorted(group)[:3])))
    if len(undeclared) >= 2:
        detail.append("{} distinct names are asserted as the site's identity: {}".format(
            len(undeclared), ", ".join('"{}"'.format(v) for v in sorted(variants)[:4])))

    # The pages that actually declared a name. A finding that lists none is
    # scored as affecting the whole site, and this one was: a disagreement
    # between two names declared on a single store page out of sixty scored
    # higher than a confirmed crawler block on the same report, because
    # "no pages listed" is meant to mean robots.txt, not "we did not look".
    declared_on = brand.get("declared_on") or {}
    pages_declaring = sorted({url for name in variants
                              for url in declared_on.get(name, []) if url})
    pages_seen = brand.get("pages_seen") or 0
    scope = ""
    if pages_declaring and pages_seen:
        scope = (" Both forms were found on {} of the {} pages crawled; the rest declare no "
                 "name at all, so this is a disagreement between the few pages that do."
                 .format(len(pages_declaring), pages_seen))

    result.add(
        id_hint="brand-name-written-inconsistently",
        affected_pages=pages_declaring,
        title="The brand name is written more than one way",
        severity="medium", confidence="medium",
        evidence="Sources checked: Organization JSON-LD `name` and og:site_name. "
                 "Result: {}. The primary form was taken to be \"{}\" (from {}).{}".format(
                     "; ".join(detail), brand.get("name"), brand.get("source"), scope),
        mechanism="D", root_cause="name-inconsistency",
        summary="Pick one written form of the name and use it everywhere, character for character.",
        how_to_fix=[
            'Choose the canonical form, including capitalisation and spacing.',
            "Set it identically in Organization `name`, og:site_name, the <title> suffix and "
            "the visible logo alt text.",
            "Add the other forms to Organization `alternateName` so they are declared as the "
            "same entity rather than being guessed at.",
            "Update the same string on your off-site profiles.",
        ],
        effort="low", owner="marketing",
        rationale="Agreement across sources is what makes a fact trustworthy. Two "
                  "spellings halve the evidence for each and make it harder to tell that both "
                  "refer to one company.",
    )


def _check_long_sentences(result, pages):
    result.check("long-sentences")
    measurable = [p for p in pages
                  if (p.get("readability") or {}).get("sentence_count", 0) >= 8]
    if not measurable:
        result.skip("long-sentences",
                    "no crawled page has enough prose (8+ sentences) to measure sentence length")
        return

    offenders = [p for p in measurable
                 if (p.get("readability") or {}).get("long_sentence_share", 0) > LONG_SENTENCE_SHARE]
    if not offenders:
        result.skip("long-sentences",
                    "fewer than {}% of sentences run over 30 words on every measurable "
                    "page".format(int(LONG_SENTENCE_SHARE * 100)))
        return

    result.add(
        id_hint="sentences-too-long-to-quote",
        title="{} written in sentences too long to quote".format(
            plural(len(offenders), "page is", "pages are")),
        severity="low", confidence="medium",
        evidence="Pages where over {}% of sentences exceed 30 words: {}.".format(
            int(LONG_SENTENCE_SHARE * 100),
            "; ".join("{} ({}% long, average {} words)".format(
                p["url"],
                int((p.get("readability") or {}).get("long_sentence_share", 0) * 100),
                (p.get("readability") or {}).get("avg_sentence_words"))
                for p in sorted(offenders, key=lambda x: x["url"])[:4])),
        mechanism="B", root_cause="long-sentences",
        summary="Break long sentences into single-claim sentences.",
        how_to_fix=[
            "Split any sentence carrying more than one claim into one sentence per claim.",
            "Aim for an average of 15 to 20 words; keep the occasional long sentence for rhythm.",
            "Put each concrete fact in its own short sentence so it can be lifted on its own.",
        ],
        effort="medium", owner="content owner",
        rationale="A quotable fact has to survive being taken out of its paragraph. "
                  "A 40-word sentence carrying three qualifications cannot be excerpted without "
                  "changing its meaning, so it tends not to be excerpted at all.",
        affected_pages=[p["url"] for p in offenders],
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--no-network", action="store_true", help="accepted for interface parity")
    args = parser.parse_args(argv)

    result = run(load_snapshot(args.snapshot))
    result.write(args.out)
    print("{}: {} finding(s)".format(SKILL, len(result.findings)), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
