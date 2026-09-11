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

from urllib.parse import urlsplit

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

# `prose_blocks` survives the crawl's site-wide boilerplate strip and
# `body_text` does not, which is the whole reason this check can see a footer
# tagline at all. `reads_as_source_not_prose` is the sentence-level test the
# extractor asks every consumer to apply before quoting one of those blocks:
# a leaked `<script>` body is text by the time this audit parses the page.
from page_extract import reads_as_source_not_prose  # noqa: E402
# The extractor's own sentence splitter and long-sentence bound, so the
# sentence-length check can recount a page without its interface labels and
# still be counting the sentences the extractor counted. See
# `_prose_readability`.
from page_extract import (  # noqa: E402
    LONG_SENTENCE_WORDS, prose_sentences, word_count as extractor_word_count)

from audit_common import (  # noqa: E402
    brand_forms, brand_pattern, CODE_LABEL_WINDOW, comparison_key, CONTENTLESS_NOUNS,
    defining_sentence, dominant_script, EVIDENCE_SENTENCE_MAX,
    EVIDENCE_SENTENCE_MIN, example_urls, find_prices, is_listing_page, is_search_result_page,
    jsonld_type_names, language_of, letter_runs, load_snapshot, LOCAL_BUSINESS,
    looks_like_soft_404, MAX_PAGES, name_appears_in, name_forms, names_match,
    name_candidates, name_the_site_spells,
    not_a_telephone_number, ONLINE_SELLER, ORGANISATION, PERSONAL_OR_ACADEMIC,
    other_language_pages, pages_in_prose_language, pages_of, pct, plural, PRICE_BEARING_TYPES,
    primary_subtag, PROJECT, prose_skip_reason, PUBLIC_BODY, PUBLICATION,
    quotes_its_own_price, reads_like_an_address, says_something, sells_something,
    sentence_with, sentences, site_kind, sitemap_scope, SkillResult, some_subject_is_defined,
    speaks_about_itself, STOPWORDS, strip_www, title_segments, truncate
)

# The two coverage rules, read from where the report composer keeps them rather
# than written a second time here.
#
# `page_shaped_urls` is the denominator of every "how much of this site was
# read" figure: the URLs the crawl went looking for a page at, minus the ones
# whose own response proved they were never a page. A crawl of sixteen URLs
# that downloaded eight release archives and a patch file counted all nine as
# URLs it "reached but could not read", which put this skill's coverage under
# the bar and silenced true findings about the seven pages it had read.
#
# `why_little_was_read` is the clause that says why: it counts the reasons the
# crawl itself recorded instead of naming one. The sentence here asserted that
# the unread URLs "were refused or challenged" on a crawl where all sixteen
# answered HTTP 200 and nothing was refused.
#
# Two rules that must agree between the composer and this skill are one rule.
# `package.py` follows this import when it vendors the shared library into each
# skill directory, so a skill folder lifted out on its own still runs.
from compose_report import page_shaped_urls, why_little_was_read  # noqa: E402

SKILL = "fact-extractability-audit"

# `PRICE_BEARING_TYPES` - the page types whose job is to state what something
# costs - is imported from the shared library rather than defined here. The
# local copy and `sells_something` had to agree about which pages carry a
# site's own price, and two sets that must agree are one set.

# Thresholds and why they sit here.
DEFINITION_WINDOW_WORDS = 150   # an assistant reads the top of a page first; a definition below this is rarely used
ANSWER_FIRST_MIN_SECTIONS = 3   # below 3 sections the share is noise
FLUFF_FIRST_SHARE = 0.7         # 50% fired on 69% of real sites; a section opening with context
                                # is normal writing, so only a page that almost never leads with
                                # a fact is worth reporting
# A share on its own is not a quantity. A page needs three sections to be a
# candidate, so three sections that read as warm-up are 100% of the page, and
# the finding then said a site opens its sections with prose on the strength of
# three paragraphs. Below this many offending sections there is no pattern to
# report, whatever the share.
FLUFF_FIRST_MIN_OFFENDING = 4
SLOGAN_HEADING_SHARE = 0.8      # nearly every heading using words the page never uses again
SLOGAN_MIN_SECTIONS = 5         # below 5 headings the share is one bad heading, not a pattern
LONG_SENTENCE_SHARE = 0.4       # 25% fired on 76% of real sites; technical prose runs long,
                                # and only a page where most sentences are unliftable is a defect

# Where a page stops holding sentences at all, so that measuring their length
# stops being a measurement. See `_holds_prose`, which says which of these
# does the work.
#
# Over half the words inside list items or table cells. An article with a
# bulleted list in it runs at 0.1 to 0.4; a credits page or a catalogue is the
# list, and runs at 0.9 to 1.0.
PROSE_MAX_LIST_TEXT_SHARE = 0.5
# 80 words between full stops, not 40: the longest-winded real writing this
# audit has measured averages around 60, and reporting writing like that is
# this check's whole purpose. What it must not report is a list joined into
# one string, which arrives at 85 and 92.
PROSE_MAX_AVG_SENTENCE_WORDS = 80

# How much of a site has to be readable before "the site never states X" is a
# claim worth making. Half: below that the sample is smaller than the part it
# is describing, and the missing half is exactly where an about page or a
# contact page would be. Measured against the two blocked sites that produced
# the false findings - 4 of 60 and 2 of 60 readable - and against every
# unblocked site in the same batches of runs, none of which fell below 0.9.
#
# Still 0.5 now that the denominator counts only page-shaped URLs, and the
# calibration is what says so rather than a preference: a refused page stays in
# the denominator, so the two blocked sites are still 4 of 60 and 2 of 60, and
# dropping responses that were never pages can only raise an unblocked site's
# share, so the sites that sat at 0.9 sit at or above it. Half still falls in
# the gap between the two populations, with more room on both sides than it
# had. What moved is the site that was never in either: sixteen URLs, seven
# pages and nine downloads, which read 0.44 and now reads 1.0.
CORE_FACTS_MIN_COVERAGE = 0.5

# Page types where a visitor arrives with a specific question, so the first
# paragraph of each section is expected to answer it.
ANSWER_FIRST_TYPES = ("pricing", "faq", "product", "service", "location", "comparison")

# The sentence shapes that say what a brand is - the brand as the subject of a
# copula or of a defining verb, the brand set against a description by a dash,
# a colon or a comma, the site describing itself in the first person - are
# `defining_sentence` in the shared library, together with the vocabulary that
# judges a predicate and the three tests that separate a definition from a row
# of a table. `structured-data-audit` reads the same rule for the `description`
# of its paste-ready Organization block. The two skills cannot import each
# other, so it was written twice, and one report then gave two answers about
# one site's identity.

# What warm-up prose actually does, in five kinds. Every one of them defers:
# the sentence is about the greeting, the scene, the page, the question or the
# writer's feelings rather than about the subject of the heading above it.
#
# This is a closed English vocabulary, and it is consulted only where the
# prose checks run at all - `run` gates this whole check on the site's prose
# language. The alternative it replaces was not language-independent either;
# it was a digit test, which passes any language and answers a different
# question. A page that warms up in words this does not know is read as
# answering, which is the safe direction for a check whose whole output is an
# accusation that a page says nothing.
DEFERS_RE = re.compile(
    # 1. Greeting the reader.
    r"\bwelcome to\b|\bthanks?( you)? for (visiting|stopping by|being here)\b"
    # 2. Announcing what the page is about to do, instead of doing it.
    r"|\bin this (?:article|post|guide|section|page|chapter)\b"
    r"|\bthis (?:article|post|guide|page|section) (?:will |aims? to |is going to )?"
    r"(?:explains?|covers?|looks? at|walks?|shows?|takes? you|explores?)\b"
    r"|\b(?:we(?:'ll| will)|you(?:'ll| will)) (?:explain|cover|look at|walk|show|explore|see)\b"
    r"|\bbefore we (?:begin|start|dive|get|go)\b|\blet(?:'s| us) (?:start|begin|dive|take a look|explore)\b"
    r"|\bread on\b|\bbelow (?:you(?:'ll| will) find|we)\b"
    # 3. Restating the question rather than answering it.
    r"|\b(?:we(?:'re| are)|it(?:'s| is)) (?:often|frequently|sometimes|commonly) asked\b"
    r"|\bone of the (?:most )?common(?:est)? questions\b"
    r"|\b(?:people|customers|clients|visitors|guests|readers) (?:often|frequently|always|sometimes) ask\b"
    r"|\b(?:you may|you might|people) (?:be )?wonder(?:ing|ed)?\b|\bever wondered\b"
    # 4. Setting a scene the heading did not ask about.
    r"|\bin today(?:'s)?\b|\bthese days\b|\bin recent years\b|\bover the years\b"
    r"|\bin an? [a-z-]+ (?:landscape|world|market|environment|climate|era|industry)\b"
    r"|\b(?:has|have|it's) never been more (?:important|critical|vital)\b"
    r"|\bnow more than ever\b|\bas (?:you|we) (?:may |might |all )?know\b"
    r"|\bit goes without saying\b|\bwhen it comes to\b"
    r"|\bwhether you(?:'re| are|)\b"
    # 5. Announcing a belief or a feeling instead of a fact.
    # "we have always believed", not only "we believe". The auxiliaries and
    # the adverbs in front of the verb are the half of this shape that varies,
    # and a pattern reading only the bare present tense missed "For over 25
    # years we have believed that good coffee should be simple" - which is the
    # shape itself, with a number in front of it.
    r"|\bwe (?:have |had |has |always |long |firmly |truly |really )*"
    r"(?:believed?|understand|know that|pride ourselves|loved?|are passionate"
    r"|'re passionate|are committed|'re committed|are proud|'re proud|want you to)\b"
    r"|\bour (?:mission|goal|passion|promise|philosophy) is\b"
    r"|^at [^,.]{2,40}, we\b",
    re.I)

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
# The vocabulary was written for software, and "free" is not a software word.
#
# A museum was told, at high severity, that "no price and no 'pricing on
# request' statement appears anywhere on the crawled pages", by a check whose
# own list of what it consulted says it reads "sentences saying the site is
# free to use". Its pages say "Entry is free." and "Free, no booking
# required." Charging nothing is the answer to "what does it cost", and a
# building that charges nothing says so in the words a visitor uses, not in
# the words a licence uses.
#
# "Free" also means "at liberty", which is the one reading that must not
# count: "you are free to cancel at any time" is not a price. The two are
# separated by what follows the word - "free to <verb>", "free from" and "free
# of <anything but charge>" are the liberty sense - so the predicate branch
# refuses those and the phrases that really do state a price keep their own
# alternatives.
COSTS_NOTHING_RE = re.compile(
    r"\b(?:free (?:and )?open[- ]source|open[- ]source(?: and)? free"
    r"|completely free|entirely free|always free|(?<!feel )free to (?:use|download|install)"
    r"|no (?:cost|charge|licence fee|license fee)|free of charge"
    # "Zero cost" but not "less-than-zero cost" or "below-zero cost". A
    # conglomerate's shareholder letter says its insurers generated "float at a
    # less-than-zero cost", and that was printed as the site's own statement
    # that what it sells costs nothing. A cost below zero is a profit.
    r"|costs? nothing|(?<!than )(?<!than-)(?<!below )(?<!below-)(?<!negative )zero cost"
    # "Entry is free.", "Admission is free for under-16s."
    r"|(?:is|are) free\b(?!\s+(?:of|to|from)\b)"
    # "Free entry", "Free parking", "Free to visit."
    r"|free (?:entry|entrance|admission|parking|access|to (?:enter|visit|attend|join))"
    # "Free, no booking required." The word "free" is the anchor; what follows
    # is what a visitor does not have to do.
    r"|free\W{1,3}no [\w-]+ (?:required|needed|necessary)"
    r")\b", re.I)

# `(?:1[5-9]|20)`, not `(?:19|20)`. A museum founded in 1759, a university in
# 1636 and a brewery in 1840 each read as a site that states no founding year,
# and the fix then told them to add one they have had on the page for decades.
_FOUNDING_YEAR = r"(?:1[5-9]|20)\d{2}"

# A founding fact is a body, a date, and a word that puts the body into
# existence. Three shapes say it, and the verb list is only one of them.
#
# A town is never "founded in a place". It is chartered, and a Vermont town
# whose own page reproduces its 1764 royal charter - "Corinth Charter, dated
# February 4, 1764" - was ranked second in its report's do-first list under
# "no founding year appears in the page text or in the page markup", with a fix
# telling a municipality to write a sentence no municipality can write. A
# village hall whose committee page opens "The management committee was formed
# on 25th April 1957 in preparation for the official opening of the village
# hall" was told the same thing.
#
# 1. The constitutive verbs. Founding, establishing, incorporating, chartering
#    and constituting mean bringing into existence and nothing else, so a year
#    beside one of them needs no second reading. `charter` is here as a noun as
#    well: a charter is dated, not verbed, and the date on it is the origin.
_CONSTITUTIVE_VERBS = (r"founded|established|incorporated|chartered|constituted"
                       r"|instituted|inaugurated")
# 2. Brought into being, said in the passive. This is a shape rather than a
#    list, and the shape is what carries the meaning: a thing that comes into
#    existence is what the act was done to, so English puts it in the passive -
#    "the committee was formed", "the hall was opened", "the parish was settled".
#    The same verbs in the active voice are an event doing something - "the
#    exhibition opened in 2019", "the season started in 2021" - which is not an
#    origin at all, and adding those verbs to a flat list is how this check
#    would trade one wrong finding for another. The auxiliary does the work no
#    longer list of verbs could.
_BROUGHT_INTO_BEING = (
    r"(?:was|were|is|are|has been|have been)\s+"
    r"(?:first\s+|formally\s+|officially\s+|originally\s+)?"
    r"(?:formed|created|opened|built|settled|organised|organized|registered"
    r"|set up|brought together)")
# 3. The elapsed-time shapes, which say the year without naming an act.
_IN_BUSINESS_SINCE = r"since|operating since|in business since|started|launched"

FOUNDING_FACT_RE = re.compile(
    r"\b(?:" + _CONSTITUTIVE_VERBS + r"|" + _IN_BUSINESS_SINCE + r")\b"
    r"[^.!?]{0,60}\b" + _FOUNDING_YEAR + r"\b"
    r"|\b" + _BROUGHT_INTO_BEING + r"\b[^.!?]{0,60}\b" + _FOUNDING_YEAR + r"\b"
    r"|\b" + _FOUNDING_YEAR + r"\b[^.!?]{0,30}\b(?:" + _CONSTITUTIVE_VERBS + r")\b"
    # The charter as a document: "<Name> Charter, dated February 4, 1764".
    r"|\b(?:charter|charters|letters patent|act of incorporation)\b"
    r"[^.!?]{0,40}\b" + _FOUNDING_YEAR + r"\b", re.I)

# "Who runs this" is not only a job title, and it is never a job title alone.
#
# The question this pattern is for is whether any person is named as
# responsible for the thing. A role title is one way English says that; a
# possessive, an attribution and a plain active sentence are the other three,
# and they are how every open-source project, most personal sites and most
# academic pages attribute themselves. A mail server's homepage reading "It is
# <Person>'s mail server ... <Person> continues to maintain <Brand>." was told
# that no named team or leadership detail appears in its page text, two
# sentences under the name of the person who maintains it, because a pattern
# built out of corporate titles could not see any of it.
#
# Every shape requires a name, and the name is captured so the caller can check
# it. The finding this feeds says "no *named* team or leadership detail", and
# the bare collective words this pattern used to carry - "employees", "our
# team", "staff of" - name nobody: a privacy notice's "Individuals covered by
# this system include <Brand> employees" was recorded and printed as the site's
# own leadership fact, which marked the fact present and suppressed the finding
# underneath it.
#
# Latin capitals, because this pattern is only ever consulted on a site whose
# prose language is English - `run` gates every caller on that.
# One capitalised word, hyphens, apostrophes and initials included.
_NAME_WORD = "[A-ZÀ-ÖØ-Þ][^\\W\\d_]*(?:['’.-][^\\W\\d_]+)*"
# A person as prose writes them: a capitalised word, up to two more, and the
# particles a surname carries between them.
_PERSON_NAME = (
    _NAME_WORD +
    r"(?:\s+(?:van|von|der|den|de|del|della|di|du|da|la|le|bin|ibn|al))*"
    r"(?:\s+" + _NAME_WORD + r"){0,2}")

# The words that put a person in charge of something.
_ATTRIBUTION_VERBS = (r"maintained|created|built|written|designed|developed|run|curated"
                      r"|founded|led|headed|edited|published|started|compiled")
_LEADERSHIP_ROLES = (r"founders?|co-?founders?|chief executive|ceo|cto|coo|cfo"
                     r"|managing director|directors?|president|chair(?:man|woman|person)?"
                     r"|owner|proprietor|principal|head of [a-z ]{2,30}|partners?"
                     r"|editors?|maintainers?|curators?|professors?")
_RESPONSIBLE_VERBS = (r"maintains|maintained|writes|wrote|creates|created|builds|built"
                      r"|develops|developed|runs|ran|leads|led|founded|curates|curated"
                      r"|edits|edited|designs|designed|owns|owned")

TEAM_FACT_RE = re.compile(
    # 1. Attributed: "maintained by <Person>", "founded in 2011 by <Person>".
    #    The words between the verb and the "by" are what a real sentence puts
    #    there - a place, a date - so they are allowed for.
    r"\b(?i:" + _ATTRIBUTION_VERBS + r")\b[^.!?]{0,60}?\bby\s+(?P<attributed>" + _PERSON_NAME + r")"
    # 2. A role with the name beside it, either way round.
    r"|\b(?i:" + _LEADERSHIP_ROLES + r")\b[,:]?\s+(?:is\s+|are\s+)?(?P<titled>" + _PERSON_NAME + r")"
    r"|(?P<named>" + _PERSON_NAME + r")\s*,\s*(?:the\s+|our\s+)?(?i:" + _LEADERSHIP_ROLES + r")\b"
    r"|(?P<holder>" + _PERSON_NAME + r")\s+(?:is|was|serves as|became)\s+"
    r"(?:the\s+|our\s+|an?\s+)?(?i:" + _LEADERSHIP_ROLES + r")\b"
    # 3. The person as the subject of what they do: "<Person> continues to
    #    maintain it", "<Person> writes the newsletter".
    r"|(?P<subject>" + _PERSON_NAME + r")\s+"
    r"(?:still\s+|now\s+|also\s+|continues\s+to\s+|helps\s+to\s+|helps\s+)?"
    r"(?P<does>(?i:" + _RESPONSIBLE_VERBS +
    r"|maintain|write|create|build|develop|run|lead|curate|edit))\b"
    # 4. The possessive, which is how a project most often names its author:
    #    "<Person>'s mail server". Two capitalised words, so a brand
    #    ("<Brand>'s users"), a weekday and "Today's" are not read as people.
    r"|(?P<owner>" + _NAME_WORD + r"\s+" + _NAME_WORD + r")['’]s\b"
    r"(?:\s+(?P<owned>[^\W\d_]+))?")

_TEAM_NAME_GROUPS = ("attributed", "titled", "named", "holder", "subject", "owner")

# The two shapes above that rest on capitalisation and nothing else, and the
# word each of them needs in order to be a sentence rather than a label.
#
# Shapes 1 and 2 carry their own evidence in the match: "maintained by", "our
# founder", "is the owner" are words a shop's menu does not contain. Shapes 3
# and 4 carry none - they read "two capitalised words, then a word that could
# be a verb" and "two capitalised words, then an apostrophe-s" - and a
# storefront's category navigation is made of exactly that. Two consumer shops
# had "<Brand> Men's" and "<Brand> Women's" recorded and printed as the person
# behind the brand, off a run of collection labels naming nobody, which marked
# the fact present and hid the finding that neither page names anyone at all.
#
# What separates the two readings is the word that governs the name, and
# English capitalises it in one case and not the other. A person who does
# something is followed by a lower-case verb ("Wilma continues to maintain");
# a category label is followed by the next capitalised label ("<Brand> Men's
# <Brand> Women's") or by nothing. A person who owns something is followed by
# the lower-case noun they own ("<Person>'s mail server"); a label owns
# nothing. So the governing word is captured and read.
_TEAM_SHAPES_RESTING_ON_CAPITALS = {"subject": "does", "owner": "owned"}

# "across the" matched "across the board". Bare "worldwide" matched a
# sponsor's blurb about a different company on an open-source documentation
# site, and that one match was printed as the project's own stated service
# area. What is left still has to be attributed - see `speaks_about_itself` at
# the caller - because a place name on a page belongs to whoever the sentence
# is about.
SERVICE_AREA_RE = re.compile(
    r"\b(?:serving|we serve|available (?:in|across|throughout)|operating (?:in|across)|"
    r"customers (?:in|across)|based in|located in|headquartered in"
    r"|(?:operat|serv|deliver|ship|trad)\w*\s+(?:nationwide|worldwide|internationally"
    r"|globally))\b", re.I)


# --------------------------------------------------------------------------
# One rule for every fact this check records as found
#
# A pattern matching somewhere on a page is not the site stating the fact, and
# a fact recorded as PRESENT does not merely add a false line to the report -
# it suppresses the finding that would otherwise have fired, so a wrong "found"
# hides a true gap. Three reached one batch of reports: a date-filter control
# ("Any Dates This Weekend This Week Next 7 Days ...") printed as the site's
# stated pricing, a sentence refusing a service printed as the area it covers,
# and a privacy notice's mention of employees printed as a leadership fact.
#
# The rule they all break is the same one, and it is the test a person applies
# without thinking: the words the report is about to print have to contain the
# fact. A pricing quote holds a price. A service-area statement names where you
# operate, not what you decline to do. A team fact names a person. A founding
# fact carries a year. The pattern finds a candidate; these decide whether the
# candidate is a statement.
#
# `_own_sentence` enforces the first half for every fact by re-matching the
# pattern against the *truncated* sentence, so a match that survives only in
# the part the reader never sees no longer counts - which is exactly how the
# date-filter blob passed, its figure sitting past the 220-character cut. The
# functions below are the per-fact half.

# A sentence that denies the service is not a statement of where it is offered.
_DECLINES_RE = re.compile(
    r"\b(?:not|never|unable|cannot|no longer|without|except|excluding|outside"
    r"|unavailable|declines?|declined|regret|sorry)\b|n't\b", re.I)

# Where, said as a scope rather than as a place name. "We deliver nationwide"
# names an area without naming a proper noun.
_WHOLE_MARKET_RE = re.compile(
    r"\b(?:nationwide|worldwide|internationally|globally|countrywide"
    r"|across the (?:country|world|globe|uk|us)|in every (?:state|county|region))\b", re.I)


def _states_a_service_area(sentence, match):
    """Does this sentence say where the site operates?

    Two tests, and a real statement passes both. It must not be a refusal:
    "Therefore, we are unable to provide locator services for members still
    serving on active duty" matched on the word "serving" and was printed as
    the organisation's service area - a sentence about what it will not do,
    recorded as the places it covers. And it must name somewhere, after the
    phrase that introduces it, because a service area that names no place has
    not been stated.

    The cost is a sentence like "We serve the local community", which names an
    area in words no test can resolve to a place. That is a fact reported
    missing rather than a refusal reported as a service area, and of the two
    errors it is the recoverable one.
    """
    # Only up to the trigger, because a denial governs what follows it. "Our
    # office is in Leeds and is not open on Sundays" states where the office is
    # and then denies something else; reading the whole sentence for a negative
    # threw that away and turned one recoverable miss into another.
    if _DECLINES_RE.search(sentence[:match.end()]):
        return False
    tail = sentence[match.end():]
    if _WHOLE_MARKET_RE.search(match.group(0) + " " + tail):
        return True
    # A proper noun after the trigger. Only the tail is read, so "Serving" at
    # the head of its own sentence is not mistaken for the place it introduces.
    return any(word[:1].isupper() for word in tail.split())


def _named_person(match):
    """The person a `TEAM_FACT_RE` match names, or ''."""
    for group in _TEAM_NAME_GROUPS:
        value = (match.groupdict().get(group) or "").strip()
        if value:
            return value
    return ""


def _label_words(text):
    """A label or a name reduced to its words, for comparing one against another.

    Both sides go through the same reducer, so the possessive on a menu entry
    is not what decides the comparison: "<Brand> Men's" and the "<Brand> Men"
    a possessive match captures out of it both reduce to the same opening two
    words and the second is found inside the first.
    """
    return tuple(word.lower() for word in letter_runs(text or "", 1))


def site_labels(pages):
    """The strings this site uses as labels rather than as words in a sentence.

    A name is a name because of where it sits, and the page says where. A menu
    entry, a footer link and a heading the template repeats on page after page
    are the site's own furniture - it wrote them to be clicked, not read - and
    a run of them is not a sentence however it is capitalised. A shop's
    category navigation is the case this exists for: "<Brand> Men's" and
    "<Brand> Women's" are two capitalised words followed by an apostrophe-s in
    the navigation of every page of the site, and were printed as the person
    behind the brand on two consumer shops in one batch of runs.

    Read from three places, and each is evidence of the same thing:

      the `nav` link labels    what the template puts in the menu, including a
                               menu built out of plain `div`s - the extractor
                               finds those by shape, so a theme the chrome
                               removal misses still declares its menu here
      the `footer` link labels the same furniture at the other end of the page
      a repeated heading       a heading appearing on two or more crawled
                               pages is the template speaking, not this page

    A heading seen once is left out on purpose. An about page whose team
    section heads each person with their own name would otherwise have every
    one of those names ruled out, which is the opposite error.

    Language-neutral: it compares the page's own strings against each other and
    carries no vocabulary of its own.
    """
    labels, heading_pages = set(), {}
    for page in pages:
        links = page.get("links") or {}
        for where in ("nav", "footer"):
            for link in links.get(where) or []:
                if isinstance(link, dict) and (link.get("text") or "").strip():
                    labels.add(link["text"].strip())
        headings = page.get("headings") or {}
        on_this_page = {(heading or "").strip()
                        for level in ("h1", "h2", "h3")
                        for heading in headings.get(level) or []}
        for heading in on_this_page:
            if heading:
                heading_pages[heading] = heading_pages.get(heading, 0) + 1
    labels.update(heading for heading, seen in heading_pages.items() if seen > 1)
    return frozenset(words for words in (_label_words(label) for label in labels) if words)


def _is_a_label_on_this_site(name, labels):
    """Is this name one of the site's own labels rather than words in a sentence?

    A run, not a whole label, because a menu entry is often the name of a
    category with its own strapline stuck to it - a link wrapping a heading and
    the line under it arrives as one label - and the words that matched sit at
    the front of it.
    """
    words = _label_words(name)
    if not words or not labels:
        return False
    width = len(words)
    return any(label[start:start + width] == words
               for label in labels
               for start in range(len(label) - width + 1))


# How many words a run has to have, and how few of them may be capitalised,
# before it is read as a sentence rather than as a row of labels.
#
# Prose puts lower-case words between its capitalised ones; a menu, a category
# grid and a size chart do not. "New In Sale Women's Clothing Men's Clothing
# Accessories" is eight words with one lower-case word in it and no verb; "It
# is <Person>'s mail server that started life at a research lab" is fifteen
# with eleven. The floor is a share rather than a count so a long menu cannot
# accumulate its way over the line.
_A_SENTENCE_MIN_WORDS = 6
_A_SENTENCE_MIN_LOWER_SHARE = 0.5

# Apostrophes kept inside the word, unlike `letter_runs`, because "Women's"
# counted as "Women" plus a lower-case "s" is a menu entry scoring itself as
# half prose.
_WORD_IN_A_SENTENCE_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*")


def _prose_not_a_label_run(text):
    """Is this a sentence, or a run of labels with no punctuation in it?

    `sentences` splits on terminators, so a page whose menu arrives as one
    unpunctuated run is one "sentence" however long it is - the run this was
    written for was 180 characters of category names and the ones that reach
    this check in the wild go to several thousand. Length cannot tell them
    apart and neither can punctuation. Capitalisation can: every word of a menu
    is capitalised because each one is a label, and most words of a sentence
    are not.

    Only consulted for the two match shapes that rest on capitalisation alone.
    A credit line - "Founder: <Person>" - is three capitalised words and is a
    statement, because the words it carries say so.
    """
    words = _WORD_IN_A_SENTENCE_RE.findall(text or "")
    if len(words) < _A_SENTENCE_MIN_WORDS:
        return False
    # The first word is not counted either way: every sentence capitalises it.
    rest = words[1:]
    lower = sum(1 for word in rest if word[:1].islower())
    return lower >= _A_SENTENCE_MIN_LOWER_SHARE * len(rest)


def _states_a_named_person(sentence, match, brand_name="", labels=()):
    """Does this match name a person, rather than the site or a common noun?

    The brand is not a person. "<Brand> builds forecasting software" has the
    shape of a sentence naming who does the work and names no one, and a team
    fact that names no one is what this whole pattern exists to stop being
    recorded.

    Neither is a category. Two consumer shops were told they state who is
    behind the brand on the strength of "<Brand> Men's" and "<Brand> Women's",
    lifted out of a run of collection labels, and neither page names anybody -
    so the false "found" suppressed the finding that would have said so. Three
    tests separate a name from a label, and a match has to survive all three:

      it is not the site's own furniture   `labels` is what the site puts in
                                           its menu, its footer and the
                                           headings its template repeats
      the word governing it is lower case  only for the two shapes that rest
                                           on capitalisation - see
                                           `_TEAM_SHAPES_RESTING_ON_CAPITALS`
      it sits in a sentence                `_prose_not_a_label_run`, same two
                                           shapes

    None of the three carries a list of first names or any other vocabulary, so
    a site in any language is judged by its own pages.

    `sentence` is the words the report will print, and the third test reads
    them. `labels` is empty when the caller has no pages to read them from, and
    then the other two carry the rule on their own.
    """
    who = _named_person(match)
    if not who:
        return False
    words = [w.lower() for w in letter_runs(who, 1)]
    if not words or any(w in STOPWORDS or w in CONTENTLESS_NOUNS for w in words):
        return False
    brand_words = {w.lower() for w in letter_runs(brand_name or "", 2)}
    if brand_words and set(words) <= brand_words:
        return False
    # A string the site itself uses as a label is a label wherever it turns up.
    # This one applies to every shape: a footer reading "Journal Partners
    # Stockists" matched the role shape and offered "Stockists" as the person
    # in charge, and "Stockists" is a link in that same footer.
    if _is_a_label_on_this_site(who, labels):
        return False
    groups = match.groupdict()
    for shape, governor in _TEAM_SHAPES_RESTING_ON_CAPITALS.items():
        if not groups.get(shape):
            continue
        if not (groups.get(governor) or "")[:1].islower():
            return False
        return _prose_not_a_label_run(sentence)
    return True


def _quote_shows_a_price(quote):
    """Can the reader see the price in the words the report is printing?"""
    return bool(quote) and bool(find_prices(quote))


# Something built on the brand, listed on the brand's site.
#
# A JavaScript framework's documentation site lists partners' dashboard
# templates, and one of them reads "Free and Open Source <Brand> JS Template
# for Admin Dashboard". The report filed that as the framework's own price,
# "(states that it costs nothing)". The sentence names the brand, which is why
# `speaks_about_itself` passed it, but the brand is only a modifier there: what
# is free is the template. The head nouns below are the things other people
# publish for a platform - a theme, a plugin, a starter kit - and never the
# platform itself.
_BUILT_ON_THE_BRAND_NOUNS = (
    r"templates?|themes?|plugins?|plug-ins?|extensions?|librar(?:y|ies)|starters?"
    r"|kits?|components?|boilerplates?|modules?|packages?|add-?ons?|integrations?"
    r"|skins?|widgets?|wrappers?|bindings?|ports?|presets?|icon sets?"
    # The same listing split at a heading arrives as "Free and Open Source
    # <Brand> Admin Dashboard." An admin dashboard or panel for a framework is
    # a template somebody sells for it.
    r"|admin dashboards?|admin panels?|admin templates?|dashboard templates?")
_FIRST_PERSON_RE = re.compile(r"\b(?:we|we're|we've|our|ours|us)\b", re.I)


def _inside_quotation_marks(sentence, match):
    """Does this match sit inside a pair of quotation marks in the sentence?

    A phrase in quotation marks is a mention, not a statement. A shareholder
    letter's 'this "no cash-no cost" theory of accounting' was filed as the
    site's own statement that something costs nothing.
    """
    before = (sentence or "")[:match.start()]
    if before.count('"') % 2 == 1:
        return True
    return before.rfind("“") > before.rfind("”")


# A page whose address carries a year is a dated document: a letter, a
# release, a post. What it said something cost is what it cost then, and the
# weak textual reading of "free" does not make it the site's price now. The
# same conglomerate's only "costs nothing" statements were in shareholder
# letters filed under the year they were written.
_DATED_PATH_RE = re.compile(r"/(?:1[89]|20)\d{2}(?:[/._-]|$)")


def _a_dated_document(page):
    """Is this page filed under a year in its own address?"""
    return bool(_DATED_PATH_RE.search(urlsplit((page or {}).get("url") or "").path))
_BRAND_IS_THE_SUBJECT = (r"\s+(?:is|are|remains|stays|costs|will always be"
                         r"|has always been)\b")


def _free_is_said_of_something_built_on_the_brand(sentence, brand_name):
    """Is this "free" about a template, theme or plugin for the brand?

    The site's own price is where the brand is what is free: the brand as the
    subject ("<Brand> is free and open source"), or the site in the first
    person ("we charge nothing"). A sentence whose head noun is one of the
    nouns above, with the brand in front of it as a modifier and no verb in
    between, is a listing of somebody else's thing. The words between the
    brand and the noun may not be function words, so "<Brand> is an open
    source library" - the brand as the subject of "is" - is never read as a
    library for the brand.
    """
    words = letter_runs(brand_name or "", 2)
    if not words or not sentence or _FIRST_PERSON_RE.search(sentence):
        return False
    name = r"\b" + r"[\s.\-]*".join(re.escape(w) for w in words) + r"\b"
    if re.search(name + _BRAND_IS_THE_SUBJECT, sentence, re.I):
        return False
    shape = re.compile(name + r"((?:[\s.\-]+[\w.+#-]+){0,4}?)[\s.\-]+(?:"
                       + _BUILT_ON_THE_BRAND_NOUNS + r")\b", re.I)
    for match in shape.finditer(sentence):
        between = re.findall(r"[\w+#-]+", match.group(1))
        if not any(word.lower() in STOPWORDS for word in between):
            return True
    return False


# A run of interface labels is not a sentence.
#
# The extractor joins every element with a space, and a shop's cart drawer and
# variant picker have no full stops in them, so each arrives as one "sentence":
# "Skip to content Close menu Cart Close cart ... Recommended Products ₹250 Add
# ₹250 Add ₹600 Cold Brew Bags - Bold Add". One coffee roaster's report quoted
# that as the site's statement of its prices, and counted it, with a variant
# picker of 93 words and a brew-guide table of 104, among the sentences that
# made a product page "57% long, average 37.5 words".
#
# The extractor already sets aside a long run with no punctuation in it at all
# (`page_extract._is_a_run_of_labels`). This is the run that carries a stray
# comma - "Subtotal Rs. 0 Shipping, taxes, and discounts" - and so passes that
# test. What gives it away is its capitals: a label is capitalised, a price is
# a figure, and a sentence is mostly lower-case words. Across eleven crawled
# sites, 2,463 runs over 30 words: the longest real sentences sit well under
# 0.6 capitalised-or-figure tokens, and cart drawers, pickers, spec tables and
# date-stamped index lists sit above it.
#
# Two kinds of real sentence are all capitals and are left alone. A legal
# disclaimer in block capitals is a sentence being shouted, so a run whose
# cased words are mostly upper case is not read as labels. And a script with
# no capitals - Chinese, Japanese, Thai, Arabic - cannot be read this way at
# all, so a run whose letters are mostly uncased is not read.
INTERFACE_LABEL_SHARE = 0.6
INTERFACE_LABEL_MIN_TOKENS = 8
_SHOUTED_SHARE = 0.8
_UNCASED_LETTER_SHARE = 0.2
_LABEL_TOKEN_RE = re.compile(r"[^\W_]+(?:['’][^\W_]+)?|[^\w\s]?\d[\d.,:/%-]*")


def _reads_as_interface_labels(text):
    """Is this run of text a strip of labels and prices rather than a sentence?"""
    letters = [c for c in (text or "") if c.isalpha()]
    if not letters:
        return False
    uncased = sum(1 for c in letters if not (c.isupper() or c.islower()))
    if uncased > _UNCASED_LETTER_SHARE * len(letters):
        return False
    title = upper = lower = figure = 0
    # The first word is skipped: every sentence capitalises it.
    for token in _LABEL_TOKEN_RE.findall(text)[1:]:
        first = token[:1]
        if first.isdigit() or not first.isalnum():
            figure += 1
        elif len(token) > 1 and token.isupper():
            upper += 1
        elif first.isupper():
            title += 1
        elif first.islower():
            lower += 1
    cased = title + upper + lower
    if cased + figure < INTERFACE_LABEL_MIN_TOKENS or not cased:
        return False
    if upper / cased >= _SHOUTED_SHARE:
        return False
    return (title + upper + figure) / (cased + figure) >= INTERFACE_LABEL_SHARE


# A year beside a founding word is not always the year the thing began.
#
# An open-source project's report printed, as the site's founding fact, "The
# number of issues <Brand> has in <Tool> (a static analyzer) is now lower than
# it has been since 2016." The site states no founding year on any page. The
# sentence names the brand, so it is the site's own sentence; the year in it is
# the far end of a measurement, not an origin. Recorded as found, it marked
# three of the four core facts present and suppressed the finding that the
# founding year is absent.
#
# "Founded", "established" and "incorporated" mean origin and nothing else, so
# a match carrying one of them needs no second test. "Since" is the trigger
# that reads both ways - the year a business opened, or the year a count has
# been running - and it is the one this test is for. Where the sentence is
# comparing or counting something, a bare "since" is the span of the count.
_A_MEASUREMENT_NOT_AN_ORIGIN_RE = re.compile(
    r"\b(?:lower|higher|greater|larger|smaller|fewer|less|more|better|worse|"
    r"faster|slower|cheaper|worst|best)\b[^.!?]{0,24}\bthan\b"
    r"|\b(?:number|count|rate|total|average|share|percentage|proportion|"
    r"figures?|statistics|score|level|volume)s? of\b"
    # A rate running since a year: "a premium increase of 10% per year since
    # 1979" was filed as a conglomerate's founding fact.
    r"|\d\s?%|\bper (?:year|annum|month|week)\b|\bper ?cent\b", re.I)

# The triggers that mean origin and nothing else, so a match carrying one needs
# no second reading. The passive shape is here for the same reason: "was
# formed", "was opened", "was settled" is a thing being brought into existence,
# and the auxiliary is what separates it from an event that merely happened.
_STATES_AN_ORIGIN_RE = re.compile(
    r"\b(?:" + _CONSTITUTIVE_VERBS + r"|in business since|operating since"
    r"|charter|charters|letters patent|act of incorporation)\b"
    r"|\b" + _BROUGHT_INTO_BEING + r"\b", re.I)


# The thing that began is one of the brand's products, not the brand.
#
# A coffee roaster's report printed, as the site's founding fact, "Launched in
# 2020 with 5 coffees, the Producer Series featured 7 coffees last year" - the
# year a product series started - while its own about page says "When we
# started roasting in 2013". The sentence is the site's own and the year is an
# origin; it is the origin of something the brand sells.
#
# The nouns are the ones that name a thing a business puts out, never the
# business: a club, a festival, a society or a programme is very often the
# whole entity a site is about, and "The festival was founded in 1947" on a
# festival's own site is its founding year. Two shapes carry the subject: the
# participle opening the sentence with the subject after the comma, and the
# subject in front of the verb.
_OFFERING_NOUNS = (r"series|collection|range|line|lineup|blend|edition|product|products"
                   r"|flavou?r|menu|model|release|version|campaign|drop|capsule")
_SOMETHING_THE_BRAND_SELLS_BEGAN_RE = re.compile(
    r"^\W*(?:first\s+)?(?:launched|started|introduced|founded|established|created|begun|born)\b"
    r"[^,;]{0,80},\s*(?:the|our|its|this|their)\s+(?:[\w'’-]+\s+){0,3}?"
    r"(?:" + _OFFERING_NOUNS + r")\b"
    r"|\b(?:" + _OFFERING_NOUNS + r")\s+"
    r"(?:(?:was|were|is|are|has been|have been|first|originally|officially|formally)\s+)*"
    r"(?:launched|started|introduced|founded|established|created|began|begun|inaugurated)\b",
    re.I)

# A year the body changed hands, not the year it began. A conglomerate's
# report printed "since our purchase in 1969, dividends of $20 million have
# been paid" as its founding fact: "since" plus a year, attributed through
# "our" - and 1969 is the year it bought a bank. The purchase word has to sit
# right in front of the year, so "founded in 1965 and acquired in 1969" is
# still read for the founding, which the constitutive verb settles first.
# Established, but what was established is not a body. The same conglomerate's
# next candidate was "We also added to a small position in <Name> Industries
# that we had established in late 1990" - a shareholding. A stake, a position,
# a presence or a partnership is set up by a body that already exists.
#
# Two shapes. The thing in front of the verb ("a position ... that we had
# established") takes only nouns that never name a body. The thing after it
# ("established a partnership") may take the nouns that sometimes do - a
# holding company, a law partnership, a record label - because there the noun
# is the verb's object, and a body is not founded by founding something else.
_NOT_A_BODY_BEFORE = r"position|stake|presence|foothold|reputation|tradition|relationship"
_NOT_A_BODY_AFTER = (_NOT_A_BODY_BEFORE
                     + r"|holding|partnership|record|lead|link|connection|standard|policy")
_ESTABLISHED_SOMETHING_THAT_IS_NOT_A_BODY_RE = re.compile(
    r"\b(?:" + _NOT_A_BODY_BEFORE + r")s?\b[^.!?]{0,40}?"
    r"\b(?:established|instituted|inaugurated|formed|built|created|opened)\b"
    r"|\b(?:established|instituted|formed|built|created|opened)\s+"
    r"(?:(?:a|an|the|our|its|their|his|her|new|small|large|first|strong|close|lasting)\s+){0,3}"
    r"(?:" + _NOT_A_BODY_AFTER + r")s?\b",
    re.I)

_CHANGED_HANDS_RE = re.compile(
    r"\b(?:purchase[ds]?|purchasing|acquisitions?|acquired|acquiring|bought|buying"
    r"|takeover|took over|mergers?|merged)\b(?:\W+\w+){0,3}?\W+" + _FOUNDING_YEAR + r"\b",
    re.I)


def _states_a_founding_year(sentence, match):
    """Is the year this sentence carries the year the thing began?

    `sentence` is the words the report will print, so the reading is the one
    the reader gets. The match is checked first because the trigger word it
    caught is what decides whether a second reading is needed at all.

    Whose beginning it is comes before any of that. A product series
    "established in 2020" is established, and it is still not the brand.
    """
    if _SOMETHING_THE_BRAND_SELLS_BEGAN_RE.search(sentence):
        return False
    if _ESTABLISHED_SOMETHING_THAT_IS_NOT_A_BODY_RE.search(sentence):
        return False
    if _STATES_AN_ORIGIN_RE.search(match.group(0)):
        return True
    if _CHANGED_HANDS_RE.search(match.group(0)):
        return False
    return not _A_MEASUREMENT_NOT_AN_ORIGIN_RE.search(sentence)


# "Its founding", where "it" is somebody the sentence names. A conglomerate's
# shareholder letter reads "Our poorest performer has been <Name> Insurance
# Company of <State>, at which large losses have been sustained annually since
# its founding in 1973" - "our" makes the sentence the site's, and the year is
# a subsidiary's. A possessive in front of the founding word points back at a
# body, and where the sentence names an organisation that is not the brand,
# that is the body. Where the only organisation it names is the brand - "<Brand>
# has grown every year since its founding in 1990" - the year is the brand's.
_A_POSSESSED_ORIGIN_RE = re.compile(
    r"\b(?:its|their|his|her)\s+(?:founding|foundation|establishment|incorporation"
    r"|inception|creation|formation)\b", re.I)
_ORGANISATION_SUFFIX = (r"(?:Company|Companies|Corporation|Corp|Inc|Ltd|Limited|LLC|PLC"
                        r"|Group|Bank|Holdings|Partners|Association|Society|Foundation|Trust"
                        r"|University|College|Institute)")
_NAMED_BODY_RE = re.compile(
    r"(?:(?!(?:The|This|That|Our|Its|Their)\b)[A-Z][\w&'’-]*\s+){1,4}" + _ORGANISATION_SUFFIX
    + r"\b(?:\s+of(?:\s+[A-Z][\w'’-]*){1,3})?")


def _someone_elses_founding(sentence, match, brand_name):
    """Is the founding in this sentence that of another organisation it names?"""
    if not _A_POSSESSED_ORIGIN_RE.search(match.group(0)):
        return False
    brand = comparison_key(brand_name or "")
    for body in _NAMED_BODY_RE.finditer(sentence or ""):
        key = comparison_key(body.group(0))
        if key and not (brand and (brand in key or key in brand)):
            return True
    return False


# A page that lists other pages. Its text is a run of somebody else's
# headlines, teasers, bylines and chart labels stitched together, so a sentence
# lifted out of it is not a statement this page is making. Three "facts" this
# check certified came from one: "Last week we got im Read more By Brad Gessler
# 2 min Read Logbook: October 29 to November 6, 2022" as a founding fact, and a
# chart page's axis labels as a postal address.
#
# The listing word has to end the path. `/blog/` is an index; `/blog/why-we-
# started-this` is a post, and a founding sentence in one is the site's own.
# What a listing page is listing. A blog index and a shop's category page are
# both runs of somebody else's headings, and the page type cannot tell them
# apart: the shared classifier types both `category`. The difference is whether
# the entries are articles or things for sale, and only the second kind is a
# price list.
_EDITORIAL_INDEX_PATH_RE = re.compile(
    r"/(?:blog|news|posts?|articles?|stories|archives?"
    r"|tags?|topics?|latest|search)(?:/page/\d+)?/?$", re.I)
_EDITORIAL_JSONLD_TYPES = frozenset({"blog", "webfeed", "searchresultspage"})


def _is_price_list_page(page):
    """A listing page whose entries are for sale, so its figures are prices.

    The listing exclusion answers "is this figure one product's price", and it
    was deciding "does this site state its pricing at all" as well. A software
    vendor whose shop page carried five figures the extractor had already
    pulled out was told across 28 of 28 pages read that no price appears
    anywhere on the site. The exclusion still holds for an editorial index,
    where the figures belong to whatever the page is linking to.
    """
    url = page.get("url") or ""
    if page.get("page_type") not in PRICE_BEARING_TYPES:
        return False
    if is_search_result_page(url):
        return False
    if {t.lower() for t in page.get("jsonld_types") or []} & _EDITORIAL_JSONLD_TYPES:
        return False
    return not _EDITORIAL_INDEX_PATH_RE.search(urlsplit(url).path)


# --------------------------------------------------------------------------
# What a page declares about itself outside its prose.
#
# Every check below used to read paragraphs and nothing else, and then said
# "the site does not state X". A browser-compatibility database carrying a
# clean one-sentence `description` in its Organization markup was told no page
# states in one sentence what the brand is, while the same report pasted that
# description back as the brand's own words three findings later. Markup is a
# declaration the site wrote on purpose, and it is read by exactly the machines
# this audit is about, so it is a second source rather than a decoration.

def _jsonld_nodes(page, wanted_types=None):
    """The JSON-LD nodes on this page, optionally only those of some types.

    The crawler flattens nested nodes into the same list, so a `PostalAddress`
    inside an `Organization` is reachable without walking the tree. Anything
    that is not a mapping is skipped rather than raising: one real template
    interpolated a number where a node belonged.
    """
    for node in page.get("jsonld") or []:
        if not isinstance(node, dict):
            continue
        if wanted_types is None:
            yield node
            continue
        if jsonld_type_names(node.get("@type")) & wanted_types:
            yield node


# The node types whose `description` is a statement about the brand rather
# than about one item it sells or one article it published.
_SELF_DESCRIBING_JSONLD_TYPES = frozenset({
    "organization", "corporation", "ngo", "educationalorganization",
    "governmentorganization", "nonprofit", "nonprofitorganization",
    "localbusiness", "store", "restaurant", "hotel", "museum", "library",
    "website", "softwareapplication", "webapplication", "brand",
})


def _declared_descriptions(page):
    """`(where it came from, the words)` for each self-description on a page.

    The `description` of an Organization-like or WebSite node, and only that.
    It is written deliberately by the site, it says which entity it describes,
    and answering "what is this brand" is the whole job of the field, so the
    caller takes it as a statement rather than searching it for a sentence
    pattern.

    The meta description is the other place a site writes one, and it is read
    at the caller instead, under two conditions this function cannot express: a
    meta description names no entity, and a section page's reads "Acme -
    Pricing and plans", which has the shape of a tagline definition and is a
    navigation label. So it is read on the home and about pages only, and it is
    read with the same sentence matcher as the prose.
    """
    for node in _jsonld_nodes(page, _SELF_DESCRIBING_JSONLD_TYPES):
        value = node.get("description")
        if isinstance(value, str) and value.strip():
            yield "the JSON-LD `description`", value.strip()


def _declared_price(page):
    """A price the page declares in `Offer` or `PriceSpecification`, or ''.

    The text scan reads currency figures out of the visible words. A shop whose
    prices are rendered into an element the extractor reads as a label, and
    declared exactly once in its Offer markup, has published its prices; read
    for text alone it was told it never states them.
    """
    for node in _jsonld_nodes(page, {"offer", "aggregateoffer", "pricespecification",
                                     "unitpricespecification"}):
        for field in ("price", "lowPrice", "highPrice"):
            value = node.get(field)
            if isinstance(value, (int, float)) or (isinstance(value, str) and value.strip()):
                currency = node.get("priceCurrency")
                return "{} {}".format(
                    currency if isinstance(currency, str) else "", value).strip()
    return ""


def _declared_address(page, brand_name=""):
    """A postal address the page declares in JSON-LD, as one line, or ''.

    Independent of `contact_facts.has_address`, which reads the visible text
    and the `<address>` element. A site whose only address is in its markup was
    told no postal address appears anywhere on it.

    Whose address it is is read off the node above it: a `PostalAddress` names
    nobody, and a partners page carries one per partner.
    """
    for node in _jsonld_nodes(page, {"postaladdress"}):
        if _belongs_to_someone_else(node, brand_name):
            continue
        parts = [node.get(field) for field in
                 ("streetAddress", "postalCode", "addressLocality", "addressRegion",
                  "addressCountry")]
        line = " ".join(p.strip() for p in parts if isinstance(p, str) and p.strip())
        if line:
            return line
    return ""


def _belongs_to_someone_else(node, brand_name):
    """Does this node say it is about an organisation other than this one?

    A partners page, a sponsor list and a press page all carry an
    `Organization` node per organisation named on them, and the crawler
    flattens every node on the page into one list. Read without this, the
    founding date of a partner charity was recorded as the audited charity's
    own - a fact marked PRESENT, which suppresses the finding that the site
    states none of its own.

    Two names to read, because a fact is not always on the node that names its
    owner. An `Organization` names itself. A `PostalAddress` or a
    `ContactPoint` names nobody and hangs off the node that does, which the
    extractor records as `_parent_name` - so a partner's address and a
    partner's telephone number are attributable now as well.

    A node that names nobody and hangs off nothing is the page describing
    itself, which is the ordinary case and stays readable. `names_match` and
    `name_appears_in` decide what counts as this brand's name, in the one
    place every skill asks.
    """
    if not brand_name:
        return False
    names = []
    if jsonld_type_names(node.get("@type")) & _SELF_DESCRIBING_JSONLD_TYPES:
        names.append(node.get("name"))
    names.append(node.get("_parent_name"))
    for name in names:
        if not isinstance(name, str) or not name.strip():
            continue
        return not (names_match(brand_name, name) or name_appears_in(brand_name, name))
    return False


def _declared_contact(page, brand_name=""):
    """An email address or telephone number the page declares in JSON-LD.

    `ContactPoint` is the node whose whole job is this, but a great many sites
    put `email` and `telephone` directly on the Organization instead, so both
    are read. The `mailto:`/`tel:` link counts are handled at the caller,
    because they are a property of the page rather than of a node.
    """
    for node in _jsonld_nodes(page):
        if _belongs_to_someone_else(node, brand_name):
            continue
        for field in ("email", "telephone"):
            value = node.get(field)
            if isinstance(value, str) and value.strip():
                cleaned = re.sub(r"^(?:mailto|tel):", "", value.strip(), flags=re.I)
                if cleaned and not _NOT_A_CONTACT_EMAIL_RE.match(cleaned):
                    return cleaned
    return ""


def _declared_origin(page, brand_name=""):
    """A founding date or a named person the page declares in JSON-LD.

    The founding and team patterns are English, and a French bakery whose
    homepage says "Creee en 1932" matched neither. `foundingDate` and `founder`
    are the same facts written in a form that has no language.

    Whose founding date it is still has to be decided, which is what
    `_belongs_to_someone_else` does: a page that marks up three partner
    organisations carries three founding dates, and none of them is the site's.
    """
    for node in _jsonld_nodes(page):
        if _belongs_to_someone_else(node, brand_name):
            continue
        date = node.get("foundingDate")
        if isinstance(date, str) and date.strip():
            return "foundingDate {}".format(date.strip())
        for field in ("founder", "employee", "member"):
            value = node.get(field)
            values = value if isinstance(value, list) else [value]
            for entry in values:
                if isinstance(entry, dict):
                    entry = entry.get("name")
                if isinstance(entry, str) and entry.strip():
                    return "{} {}".format(field, entry.strip())
    return ""


def run(snapshot):
    result = SkillResult(SKILL)
    pages = pages_of(snapshot, content_only=True)
    brand = snapshot.get("brand") or {}
    brand_name = (brand.get("name") or "").strip()

    if not pages:
        # Every check this skill runs, so the report's four buckets stay
        # exhaustive. `headings-name-their-topic` was left out, so on a fully
        # blocked site it appeared in none of them - neither run, nor fired,
        # nor skipped, nor declined - while the report presents those four as
        # covering everything.
        #
        # "no content pages returned HTTP 200" named a status code with no
        # test for one. A crawl whose every URL answered 200 and whose every
        # body was a download, or a challenge screen, or empty, arrives here
        # too, and the reader was told about a status the site never sent.
        # `why_little_was_read` counts the reasons the crawl recorded.
        # No page-shaped URL at all is its own answer, and it is not the same
        # answer: a crawl whose every response proved it was never a page did
        # not reach a page to be refused at.
        if not page_shaped_urls(snapshot):
            reason = "the crawl reached no page of this site"
        else:
            reason = ("no page of this site came back with text this skill could read ({})"
                      .format(why_little_was_read(snapshot) or "no reason was recorded"))
        for name in ("entity-definition", "heading-hierarchy", "headings-name-their-topic",
                     "answer-first-paragraphs", "core-facts-present",
                     "brand-naming-consistency", "long-sentences"):
            result.skip(name, reason)
        return result

    # Four of these seven reason about English sentences. The other three are
    # structural - heading order, name agreement, whether a price appears as a
    # number - and hold in any language, so they run regardless.
    #
    # `headings-name-their-topic` is the fourth, and it was the one that
    # escaped this gate for a long time because it was called from inside
    # the structural heading check rather than from here. It scores how much of
    # each heading's vocabulary reappears in the page text, which is a word
    # overlap - and a word overlap on a script that writes no spaces between
    # words is not a measurement. The table of contents of a Japanese manual
    # was reported for "slogan headings that do not name their topic", with the
    # advice to rewrite "Built for speed" as "How fast the platform delivers
    # results", about seven numbered section titles.
    language = language_of(snapshot)
    result.signal("site_language", language.get("code") or "undetermined")
    result.signal("site_language_source", language.get("source", ""))

    if language.get("prose_checks_apply"):
        # Only the pages that are in the language these checks reason about.
        # The site verdict is one majority vote; a page declaring a different
        # language is the authority on itself, and measuring German prose
        # against an English sentence-length threshold is not a measurement.
        prose_pages = pages_in_prose_language(pages)
        set_aside = other_language_pages(pages)
        if set_aside:
            result.signal("pages_in_another_language", len(set_aside))
        _check_entity_definition(result, snapshot, prose_pages, brand_name, brand)
        _check_answer_first(result, snapshot, prose_pages, brand_name)
        _check_slogan_headings(result, prose_pages)
        _check_long_sentences(result, prose_pages)
    else:
        reason = prose_skip_reason(language)
        for name in ("entity-definition", "answer-first-paragraphs",
                     "headings-name-their-topic", "long-sentences"):
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

# How many of a page's blocks are read for a definition. The tagline is at
# one end of the document or the other and the blocks between are the content,
# which every reading above has already been over; this is a second source, not
# a second whole-page scan.
_BLOCKS_READ_PER_PAGE = 60


def _quotable_blocks(page):
    """The page's own passages, source and markup removed.

    `prose_blocks` keeps the furniture, which is the point, and the furniture
    is where a leaked script body ends up. `reads_as_source_not_prose` is the
    extractor's own test and is applied here rather than there because the
    field has other consumers that want the block whatever it holds.
    """
    blocks = [str(block or "").strip()
              for block in (page.get("prose_blocks") or [])]
    return [block for block in blocks[:_BLOCKS_READ_PER_PAGE]
            if block and not reads_as_source_not_prose(block)]


def _definition_in_a_repeated_line(page, brand_name, brand):
    """`(block, definition)` from a line the boilerplate strip removed.

    Read one block at a time and never as a joined string: a definition has to
    begin its own sentence, and running the block above a tagline into it puts
    the brand name mid-clause where the matcher cannot see it. That is the
    same failure `_readable_text` exists to fix one level up.
    """
    for block in _quotable_blocks(page):
        definition = _find_definition(block, brand_name, brand)
        if definition:
            return block, definition
    return None, None


def _readable_text(page):
    """The page's own text with a full stop after each of its own headings.

    The extractor joins block elements with a space, so a heading that ends in
    no punctuation runs straight into the paragraph beneath it:

        Demand forecasting for mid-market retail warehouses Brightpath
        Analytics is a demand-forecasting platform that cuts stockouts ...

    Read as one sentence, the brand name sits mid-clause and the definition
    under it is invisible. The page states where its headings end, so the
    boundary is taken from the page rather than guessed at.
    """
    text = page.get("body_text") or ""
    headings = page.get("headings") or {}
    values = {h.strip() for level in ("h1", "h2", "h3")
              for h in (headings.get(level) or []) if h and h.strip()}
    for heading in sorted(values, key=len, reverse=True):
        if heading[-1:] in ".!?" or len(heading) < 3:
            continue
        rebuilt, last = [], 0
        for match in re.finditer(r"{}\s+".format(re.escape(heading)), text):
            # Only where a new block plainly starts. A heading is very often
            # also the opening words of the sentence beneath it - "Poppy" over
            # "Poppy is a car-sharing service" - and a full stop dropped in
            # there cuts the sentence in half, which is the failure this
            # function exists to prevent. A capital or a digit next is the
            # evidence that a new block started; a script with no capitals
            # gets no boundary rather than a wrong one.
            after = text[match.end():match.end() + 1]
            rebuilt.append(text[last:match.start()])
            rebuilt.append(heading + (". " if after.isupper() or after.isdigit() else " "))
            last = match.end()
        rebuilt.append(text[last:])
        text = "".join(rebuilt)
    return text


def _top_words(page, limit=DEFINITION_WINDOW_WORDS):
    """The opening of the page: headings plus the first N words of body text.

    Joined with full stops, not spaces. A definition has to begin its own
    sentence to be one, and running a heading into the paragraph under it made
    the paragraph's first sentence look like the tail of the heading.
    """
    headings = page.get("headings") or {}
    lead = [h.strip().rstrip(".!?")
            for h in (headings.get("h1") or [])[:1] + (headings.get("h2") or [])[:2]
            if h and h.strip()]
    lead.append(" ".join(_readable_text(page).split()[:limit]))
    return ". ".join(part for part in lead if part).strip()


def _find_definition(text, brand_name, brand=None):
    """A quotable one-line definition on this text, or None.

    The rule itself - the four sentence shapes, the vocabulary that judges a
    predicate, and the three tests that separate a definition from a row of a
    table - is `defining_sentence` in the shared library, which
    `structured-data-audit` reads for the `description` of its paste-ready
    Organization block. It was written twice, once in each skill, and one
    report then gave two answers about one site's identity.

    `None` rather than "", because every caller here and every test of this
    function compares the result against `None`.
    """
    return defining_sentence(text, brand_name, brand) or None


def _declares_a_definition(text, brand_name, brand=None):
    """A declared `description` read as what it is, or None.

    The `description` of an Organization or WebSite node is a definition by
    construction: saying what the entity is is the whole job of that field, and
    a site that filled it in has written the sentence. Running the prose
    sentence matcher over it made the two readings this finding names one
    detector, so one bug in the matcher emptied both.

    Two things are still asked of it, and they are what separates a description
    of this entity from a slogan dropped into the field: it has to name the
    brand, and it has to say something other than that the brand is a company.
    """
    text = " ".join((text or "").split())
    if not text:
        return None
    patterns = [p for p in (brand_pattern(form)
                            for form in brand_forms(brand_name, brand)) if p]
    if not any(re.search(r"\b(?:{})\b".format(pattern), text, re.I) for pattern in patterns):
        return None
    # The name comes out before the predicate is judged. A brand name is words
    # no list of empty nouns can hold, so "<brand> is a leading global company"
    # counted as saying something purely because the brand is called what it is
    # called.
    predicate = text
    for pattern in patterns:
        predicate = re.sub(r"\b(?:{})\b".format(pattern), " ", predicate, flags=re.I)
    if not says_something(predicate):
        return None
    return truncate(text, 300)


def _definition_sources(declared_descriptions_seen):
    """What was read before saying no page defines the brand.

    Two entries only where there really were two readings. The prose pass and
    the declared-description pass both called `_find_definition`, so the list
    named two sources for one detector: one bug in that matcher emptied both,
    and two entries were enough for `make_finding` to leave the confidence at
    `high`. A declared `description` is now read as a declaration rather than
    searched for a sentence pattern, so the two no longer share anything - but
    only on a site that declares one. Where none exists, the meta descriptions
    were the only other text read and they were read with the same matcher, so
    that is one source, said in one entry, and the clamp caps the confidence at
    medium.
    """
    # What the four shapes are, said out loud. A `checked` entry reading "a
    # sentence naming the brand as its subject" described a narrower reading
    # than the one that ran and a narrower one than the finding claims: a site
    # that says what it is in the first person, or in a title line under its
    # own name, has written the sentence without ever making the brand string
    # the subject.
    prose = ("the opening headings and first {} words of the homepage and about page, then "
             "the whole text of every crawled page, for a sentence saying what this is: the "
             "brand as the subject of \"is a\" or of a defining verb, the brand set against a "
             "description by a dash, a colon or a comma, or the site describing itself in the "
             "first person (\"We are a ...\", \"I am a ...\"); and the line directly under an "
             "H1 that names the brand, where the heading supplies the subject and the line "
             "the category".format(DEFINITION_WINDOW_WORDS))
    if declared_descriptions_seen:
        # What the reading actually required, not only what it read. This
        # entry used to promise the JSON-LD `description` of every crawled page
        # and stop there, on a report whose own next finding quoted a
        # `WebSite` description reading "<Name> is a full-service design firm"
        # - read here, and discarded without a word because the brand name the
        # audit had arrived with does not appear in it. A `checked` entry that
        # names a source but not the test applied to it describes a stronger
        # search than the one that ran.
        return (prose,
                "the `description` of the Organization and WebSite nodes in the JSON-LD of "
                "every crawled page, read as a declaration of what the brand is rather than "
                "searched for a sentence pattern, and the meta description of the homepage "
                "and about page. A declared description counts only where the brand name "
                "appears in it, either the name this audit read from the site or the name "
                "the node gives itself")
    return (prose + ". No Organization or WebSite node on any crawled page carries a "
            "`description`, so the only other text read was the meta description of the "
            "homepage and about page, read with that same matcher: one reading, not two",)


def _repeat_the_definition_step(kind):
    """Where to repeat the definition sentence, for a site of this kind.

    "on your LinkedIn page and in your press kit" is a marketing department's
    checklist. A national weather service was advised to claim a company page
    on a professional network and a profile on a startup-funding database, and
    a program with a repository and a manual has neither a press kit nor a
    company page to put one in. The instruction that matters - say the same
    thing everywhere something describes you - is true of every site; only the
    list of places is a claim about what the site is, so only the list moves.

    `might_be` and not `is_certainly`: an undetermined site keeps the wording
    it has today, which is the point of the gate.
    """
    if kind.might_be(LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION):
        return ("Repeat the same sentence verbatim on the about page, in the Organization "
                "`description` property, on your LinkedIn page and in your press kit.")
    return ("Repeat the same sentence verbatim on the about page, in the Organization "
            "`description` property, and everywhere else this site is described in "
            "somebody else's words - a repository summary, a package or registry entry, "
            "a directory listing, a profile page.")


# Where the definition sits, in the words the finding uses, and how much it
# costs the site to have it there.
#
# A definition on the about page is a page away from where an assistant starts.
# A definition below the opening of a page is a scroll away. A definition in a
# blog post is neither: the sentence that says what the brand is exists only
# inside an article about something else, on a page an assistant reaches last
# if at all, and the site reads to a machine as one that never says what it is.
# Keyed on the `where` string each reading returns, and a reading added
# without its key here takes the whole skill down with a `KeyError` - which is
# what adding the repeated-line pass did, on every site the pass was written
# for. `_definition_placement` refuses an unknown key at import instead.
_DEFINITION_PLACEMENTS = {
    # Each reads on from "sits", so each is a phrase and not a noun.
    "at the top of": ("in the opening of {}, which is not the homepage", "low"),
    "further down": ("further down {}, below the part an assistant reads first", "low"),
    "on": ("on {}, which is neither the homepage nor the about page", "medium"),
    # A line the site repeats on every page. The sentence exists and a person
    # sees it, so this is not the severe case; but it sits in the furniture,
    # where an assistant reading one page for an answer weighs it as chrome,
    # and it is not the opening of the homepage either. Low, like the other two
    # placements that are "the sentence is here, just not there".
    "in a line repeated across": (
        "in a line the site repeats on every page rather than in the copy of {}", "low"),
}

# The readings that produce a `where`. Held beside the table so the two cannot
# drift: a reading whose key is missing is a crash, not a wrong sentence.
DEFINITION_PLACEMENT_KEYS = frozenset(_DEFINITION_PLACEMENTS)


def _declared_subject_names(page):
    """The names the self-describing nodes on this page give themselves."""
    names = []
    for node in _jsonld_nodes(page, _SELF_DESCRIBING_JSONLD_TYPES):
        name = node.get("name")
        if isinstance(name, str) and name.strip():
            names.append(name.strip())
    return names


def _defines_a_differently_named_subject(page, text):
    """`(the name, the sentence)` where a description defines its own subject.

    The same test that failed against the brand name this audit is holding,
    run against the name the node gives itself. Where that succeeds, the site
    has written the definition sentence and the two names disagree - which is a
    different fact from "no page states what the brand is", and the only one of
    the two that is true.
    """
    for name in _declared_subject_names(page):
        definition = _declares_a_definition(text, name, None)
        if definition:
            return name, definition
    return None


# How long the line under a heading may be and still be a definition of the
# thing the heading names, and how far past the heading it may start.
_HEADING_DEFINITION_MIN_WORDS = 4
_HEADING_DEFINITION_MAX_WORDS = 25
_HEADING_DEFINITION_WINDOW = 300


# How many pages have to print a line before it is the template speaking
# rather than this page. The same number the report composer uses to decide a
# sentence is a site's furniture, for the same reason: a block repeated across
# three pages belongs to the theme, not to any one of them.
_LINE_REPEAT_LIMIT = 3


def _repeated_across_pages(line, pages, limit=_LINE_REPEAT_LIMIT):
    """Is this line printed on enough pages to be the site's furniture?"""
    if not line:
        return False
    seen = 0
    for page in pages or ():
        if line in (page.get("body_text") or ""):
            seen += 1
            if seen >= limit:
                return True
    return False


def _definition_under_the_heading(page, brand_name, brand, labels=(), pages=()):
    """The line under an H1 naming the brand, read as what the brand is.

    A definition does not have to be a sentence. A Python client library's
    documentation home puts its name in the H1 and, directly under it, the
    line "Unofficial Python client library for Semantic Scholar APIs." - which
    is what the library is, in the form a library, a museum, a clinic or a
    charity normally writes it. The finding said "No sentence of the form
    '<brand> is a ...', 'We are a ...' or 'I am a ...' appears in the first 150
    words or the opening headings", which is literally true and draws a false
    conclusion: the page does state what this is, in a noun phrase.

    `defining_sentence` in the shared library cannot see this and should not
    try. It is handed text and nothing else, so a rule that accepted any noun
    phrase would accept a fragment from anywhere on any page - and it is the
    matcher two skills share, measured once and relied on by both. What makes
    this line a definition is not its grammar but its position: the H1 supplies
    the subject the line has no need to repeat. Position is something only a
    caller holding the page can read, so the rule lives here, on top of the
    matcher rather than inside it.

    Three things are asked of the line, and each rules out something a heading
    is also followed by:

      the H1 names the brand      otherwise the heading supplies no subject and
                                  the line is predicated of nothing. This is
                                  also what keeps a shop's slogan H1 - "Made to
                                  walk in" - from lending its position to the
                                  offer underneath it
      it carries no figure        an offer, a discount or a statistic is not
                                  what the thing is
      it says something, and it   a category rather than a row of empty nouns,
      is not one of the site's    and not a menu entry or a button the template
      own labels                  repeats
      this page alone prints it   an announcement bar sits directly under the
                                  H1 on every page of a shop, and "Free
                                  shipping on every order" has the same shape
                                  and the same position as a definition. What
                                  separates them is that a definition of the
                                  brand is written once and a promise in the
                                  template is on every page
    """
    headings = (page.get("headings") or {}).get("h1") or []
    heading = next((h.strip() for h in headings if h and h.strip()), "")
    if not heading:
        return None
    patterns = [p for p in (brand_pattern(form)
                            for form in brand_forms(brand_name, brand)) if p]
    if not any(re.search(p, heading, re.I) for p in patterns):
        return None
    text = page.get("body_text") or ""
    at = text.find(heading)
    if at < 0:
        return None
    after = text[at + len(heading):at + len(heading) + _HEADING_DEFINITION_WINDOW]
    line = next(iter(sentences(after)), "").strip()
    if not line or line.endswith("?"):
        return None
    words = letter_runs(line, 1)
    if not (_HEADING_DEFINITION_MIN_WORDS <= len(words) <= _HEADING_DEFINITION_MAX_WORDS):
        return None
    # A figure in the line means it is an offer, a price or a count, not a
    # category. "Free shipping on orders over 50" sits under a heading exactly
    # where a definition would.
    if any(ch.isdigit() for ch in line) or find_prices(line):
        return None
    # The line must add the category the heading does not carry. One that
    # repeats the brand had its chance with the shared matcher, and this rule
    # is only for the case where the heading is the subject.
    if any(re.search(p, line, re.I) for p in patterns):
        return None
    if not says_something(line):
        return None
    if _is_a_label_on_this_site(line, labels):
        return None
    if _repeated_across_pages(line, pages):
        return None
    return truncate(line, 300)


def _first_declared_definition(page, brand_name, brand):
    """`(where it was declared, the sentence)` for this one page, or None.

    The markup half of "does this page say what the brand is". A homepage whose
    prose never defines the brand and whose `Organization` node does has said
    it to the machines this audit is about, and a finding saying otherwise
    would be false in exactly the reading that matters most.
    """
    for where_from, text in _declared_descriptions(page):
        definition = _declares_a_definition(text, brand_name, brand)
        if definition:
            return where_from, definition
    return None


def _definition_is_in_the_wrong_place(result, snapshot, pages, brand_name, brand,
                                      home, page, definition, where):
    """The site defines itself, but not where the first machine to arrive looks.

    This used to be a decline, and it was the most expensive miss in the
    tool. On a footwear shop the report said, in an appendix nobody reads,
    that a quotable definition exists on `/blogs/.../best-modern-footwear-...`
    "though not in the opening of the homepage, which is the part an assistant
    reads first" - and raised nothing. The shop's complaint was that assistants
    never mention it.

    The decline was defensible as far as it went: a definition does exist, so
    "this site never defines itself" would be false. But "the definition exists
    only inside a blog post" is a different claim, it is true, the evidence for
    it is the sentence the check already has in its hand, and the fix is one
    sentence a shop owner can write without a developer. That is a finding.

    Two readings of the homepage stand behind it, and they share no code: the
    prose matcher, which read the opening and then the whole text, and the
    declared `description` in its markup, which is read as a declaration rather
    than searched for a sentence shape. Where the markup carries the definition
    the finding is not raised at all - a machine has it from the homepage, and
    that is the reading this audit is about.
    """
    result.check("definition-on-the-homepage")
    declared_on_the_homepage = _first_declared_definition(home, brand_name, brand)
    if declared_on_the_homepage:
        where_from, declared_sentence = declared_on_the_homepage
        result.skip("definition-on-the-homepage",
                    'the homepage declares a definition in {} - "{}" - so a machine fetching '
                    'the homepage can read what this is without following a link'.format(
                        where_from, declared_sentence))
        return

    placement, severity = _DEFINITION_PLACEMENTS[where]  # noqa: E501 - see DEFINITION_PLACEMENT_KEYS
    where_it_is = placement.format(page["url"])
    result.signal("entity_definition_placement", where)
    result.add(
        id_hint="definition-not-on-the-homepage",
        # The title names the page that has the sentence, because the fix is to
        # move a copy of it and the owner has to know which sentence is meant.
        title="The homepage never says what the brand is, though {} does".format(
            # Which of the three it is, because the fix is to move a copy of
            # the sentence and the owner has to know which sentence is meant.
            # "another page" was printed over a line sitting in the homepage's
            # own footer, and an instruction to look at another page for a
            # sentence on this one is one a reader checks and cannot follow.
            "a line in the site's own furniture" if where == "in a line repeated across"
            else "the about page" if page["page_type"] == "about"
            else "another page"),
        severity=severity,
        # One page, read in full, twice, by two detectors that share nothing.
        # A larger crawl would not make this claim stronger and a smaller one
        # does not weaken it, so it is not scaled to the sample the way the
        # site-wide claims above it are.
        confidence="high",
        evidence='The one sentence on this site that says what "{}" is sits {} - "{}". '
                 'Nothing in the opening of {}, in the rest of its text, or in the '
                 '`description` its markup declares says what the brand is, so an assistant '
                 'that fetches the homepage and reads no further has nothing to '
                 'repeat. Examined the whole of 1 of the {} pages this crawl read - the '
                 'homepage - {}.'.format(
                     brand_name, where_it_is, definition, home["url"],
                     _urls_reached(snapshot, len(pages)),
                     # The same sentence had to name "the page carrying the
                     # sentence" when there is no other page carrying it.
                     "and the line its template repeats"
                     if where == "in a line repeated across"
                     else "plus the page carrying the sentence"),
        checked=(
            "the opening headings and first {} words of the homepage, then the whole of its "
            "text, read for a sentence saying what this is - the brand as the subject of "
            "\"is a\" or of a defining verb, the brand set against a tagline, or the same "
            "sentence written in the first person".format(DEFINITION_WINDOW_WORDS),
            "the `description` of the Organization, WebSite and other self-describing nodes "
            "in the homepage's JSON-LD, read as a declaration rather than searched for a "
            "sentence shape, and its meta description",
        ),
        mechanism="B", root_cause="no-entity-definition",
        summary="Copy that sentence, or one like it, into the first paragraph of the homepage.",
        how_to_fix=[
            'Put the sentence in the first paragraph of {} as plain HTML text: "{}"'.format(
                home["url"], definition),
            "Keep the wording the same on both pages. Two different sentences describing the "
            "same brand give an assistant two answers and it will pick one at random.",
            # "Above the product grid" was delivered to a public library and a
            # documentation tree. The instruction is true of every site and
            # only the noun was a claim about this one, so the noun goes rather
            # than the sentence splitting into one branch per kind of site:
            # "the main content" reads correctly on a shop, a library and a
            # manual alike.
            "Put it above the main content - the grid, the listing, the article - and outside "
            "any slider or carousel, so it is in the HTML a fetcher receives rather than in "
            "markup a browser assembles.",
            _repeat_the_definition_step(site_kind(snapshot)),
        ],
        # A sentence, written once, by whoever writes the site's words. This is
        # one of the few entries in this report that needs no developer.
        effort="low", owner="content owner",
        rationale="An assistant asked about a brand fetches the homepage first and often only. "
                  "A definition it can only reach by opening a blog post or scrolling past the "
                  "main content is a definition it will not quote, and the brand gets "
                  "described in whatever words somebody else used instead.",
        affected_pages=[home["url"]],
        snippet="<h1>{brand}</h1>\n<p><strong>{sentence}</strong></p>".format(
            brand=brand_name, sentence=definition),
    )


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
        # "was crawled" is a claim about the crawl, and it was tested against a
        # list this function is handed already filtered - by status, by
        # challenge, by prose language. A homepage that answered 403, or one
        # served as a verification screen, made the reason tell the reader the
        # crawl had never asked for it. Same rule as the coverage gate below:
        # what the crawl recorded, counted, rather than a cause assumed.
        reached = [p for p in page_shaped_urls(snapshot)
                   if p.get("page_type") in ("home", "about")]
        if not reached:
            reason = "neither a home nor an about page was crawled"
        else:
            reason = ("a home or about page was reached but is not among the pages this check "
                      "could read ({}), so a definition sentence cannot be looked for".format(
                          why_little_was_read({"pages": reached})
                          or "it carried no text these checks read"))
        result.skip("entity-definition", reason)
        return

    found = None
    for page in identity:
        definition = _find_definition(_top_words(page), brand_name, brand)
        if definition:
            found = (page, definition, "at the top of")
            break

    # A definition does not have to be a sentence. Where the H1 names the brand
    # it supplies the subject, and the line under it supplies the rest: "<Name>"
    # over "Unofficial Python client library for <Something> APIs." is a
    # definition in the form a library, a museum, a clinic and a charity all
    # write it. See `_definition_under_the_heading` for why this is a rule about
    # position and not a loosening of the shared sentence matcher.
    if found is None:
        labels = site_labels(pages)
        for page in identity:
            line = _definition_under_the_heading(page, brand_name, brand, labels, pages)
            if line:
                found = (page, line, "at the top of")
                break

    # The title of this finding is "No page states in one sentence what the
    # brand is", and it was raised about sites that state it on a page this
    # search never looked at. Only the home and about pages are searched
    # first, because that is where the sentence belongs and where an assistant
    # looks; but before claiming no page has one, look at all of them, and at
    # the whole of them rather than the opening.
    if found is None:
        for page in identity:
            definition = _find_definition(_readable_text(page), brand_name, brand)
            if definition:
                found = (page, definition, "further down")
                break
    if found is None:
        # The whole text of each, not its opening. The `checked` list on the
        # finding below promises "the whole text of every crawled page" and
        # this pass read only its opening window, so a definition in a
        # footer - where a statistics charity states, on every page of its
        # site, that it is a project of a named nonprofit - sat outside the
        # only pass that claimed to cover it.
        for page in pages:
            if page in identity:
                continue
            # Not off a page that lists other pages. A sentence lifted from an
            # index, a search result or an issue list is not that page speaking
            # for the brand, and this marketplace applies that rule everywhere
            # else it quotes a sentence back to a site.
            if is_listing_page(page):
                continue
            definition = _find_definition(_readable_text(page), brand_name, brand)
            if definition:
                found = (page, definition, "on")
                break

    # The line the site repeats on every page, which is the one line a
    # definition check most wants and the one line it could not see.
    #
    # Every reading above this point goes through `body_text`, and the crawl's
    # `_strip_sitewide_boilerplate` removes from `body_text` any block
    # appearing on half the site's pages. A footer tagline does that by
    # construction. A bag maker's homepage carried "<brand> is a proudly Indian
    # and PETA-approved lifestyle brand" twice - once in a paragraph, once in
    # the footer - and this check reported that no page states in one sentence
    # what the brand is, with the sentence in the report's own snapshot.
    #
    # `prose_blocks` is the same document read before that strip, one entry per
    # block. Read here rather than earlier because a line in the furniture is a
    # weaker place for the sentence than a paragraph in the content, and the
    # decline says which it was; read before the declared descriptions because
    # it is prose a person can see, which a `description` property is not.
    if found is None:
        for page in identity:
            block, definition = _definition_in_a_repeated_line(page, brand_name, brand)
            if definition:
                found = (page, definition, "in a line repeated across")
                result.signal("definition_block_outside_the_main_text", truncate(block, 200))
                break

    # Prose is not the only place a site says what it is, and it was the only
    # place this check read. The Organization `description` and the meta
    # description are declarations the site wrote on purpose, so a site whose
    # markup carries a clean one-sentence definition is not a site that never
    # states what it is - and this finding's title says it never does.
    #
    # Read on its own terms rather than with the sentence matcher above. Both
    # readings used to call `_find_definition`, so the "two sources" this
    # finding names were one detector: the leading-article bug emptied both at
    # once, and because the list still had two entries the confidence clamp did
    # not fire and the finding printed `high`.
    declared = None
    declared_descriptions_seen = False
    # A declared description that defines a differently-named subject.
    #
    # An interior design firm's `WebSite` node reads "Arrowood Design is a
    # full-service Bay Area design firm specializing in residential projects."
    # The audit was holding the brand name "San Francisco Premier Interior
    # Designer", taken off the page title, so `_declares_a_definition` read
    # that description, found no occurrence of the name it was given, and
    # returned nothing - and the finding then told the firm that no page states
    # in one sentence what the brand is, while the same report quoted that
    # description back to it three findings later.
    #
    # The description is not silently discarded any more. A site that has
    # written the sentence has written it, whatever name this audit arrived
    # with, and the disagreement between the two names is itself the fact worth
    # reporting - it is what `brand-naming-consistency` exists for.
    under_another_name = None
    if found is None:
        for page in identity + [p for p in pages if p not in identity]:
            for where_from, text in _declared_descriptions(page):
                declared_descriptions_seen = True
                definition = _declares_a_definition(text, brand_name, brand)
                if definition:
                    declared = (page, where_from, definition)
                    break
                if under_another_name is None:
                    other = _defines_a_differently_named_subject(page, text)
                    if other:
                        under_another_name = (page, where_from, other, text)
            if declared:
                break
    if found is None and declared is None:
        # The meta description is not a declaration about the entity the way an
        # Organization `description` is - a section page's reads "<Brand> -
        # Pricing and plans", which has the shape of a tagline definition and
        # is a navigation label - so it is read only on the home and about
        # pages, and it is still read with the sentence matcher. That makes it
        # the same detector as the prose pass, which the `checked` list below
        # says out loud when it is the only second reading there was.
        for page in identity:
            meta = (page.get("meta_description") or "").strip()
            definition = _find_definition(meta, brand_name, brand) if meta else None
            if definition:
                declared = (page, "the meta description", definition)
                break

    # The last reading before a denial, and the second half of the rule in
    # `audit_common.ABSENCE_DECIDED_BY_A_VOCABULARY`: a definition written in
    # the prose under a name this audit did not resolve.
    #
    # `_defines_a_differently_named_subject` above reads the names the page's
    # own JSON-LD nodes give themselves, and a site with no JSON-LD names none.
    # A jute-bag maker's `<title>` carries both its search-engine copy and its
    # legal name; `resolve_brand` took the copy, and the report published "No
    # sentence of the form '<the copy> is a ...', '<the domain word> is a ...'
    # appears" over an about page reading "<Legal name> is a leading
    # manufacturer and supplier of eco-friendly packaging products". The
    # sentence was on the page; the subject was never tried.
    #
    # `some_subject_is_defined` is the shared matcher with the subject taken
    # out, and it is loose on purpose: a sentence defining a partner, a product
    # or a person stops this check saying "no page states what this is", which
    # is a claim it was not entitled to make on that evidence either. It runs
    # after every reading that can attribute a definition, so it only ever
    # replaces a denial and never a finding, and what it finds is printed as a
    # name disagreement rather than as this site's own definition.
    if found is None and declared is None and under_another_name is None:
        for page in identity:
            other = some_subject_is_defined(_top_words(page))
            if (other and not names_match(brand_name, other[0])
                    and _the_site_calls_itself_this(other[0], snapshot, pages, brand)):
                under_another_name = (page, "a sentence in the text", other, "")
                break
    # And the same reading over the repeated lines, for the same reason the
    # pass above exists: a site whose only statement of what it is sits in its
    # footer, under a name this audit did not resolve, is still a site that
    # states what it is. One block at a time, which is what `prose_blocks` is
    # for - flattened, the block before a tagline welds onto it and the subject
    # of the sentence stops being the subject of the tagline.
    if found is None and declared is None and under_another_name is None:
        for page in identity:
            for block in _quotable_blocks(page):
                other = some_subject_is_defined(block)
                if (other and not names_match(brand_name, other[0])
                        and _the_site_calls_itself_this(other[0], snapshot, pages, brand)):
                    under_another_name = (page, "a line repeated across the site",
                                          other, "")
                    break
            if under_another_name:
                break

    result.signal("entity_definition_found", bool(found) or bool(declared))
    if found:
        page, definition, where = found
        result.signal("entity_definition", definition)
        home = next((p for p in identity if p["page_type"] == "home"), None)
        if where == "at the top of" and page is home:
            result.skip("entity-definition",
                        'a quotable definition is present on {}: "{}"'.format(
                            page["url"], definition))
            return
        if home is None:
            # Nothing may be claimed about a homepage this crawl never read.
            # The sentence exists; where it sits relative to a page that was
            # not fetched is not a fact this audit has.
            result.skip("entity-definition",
                        'a quotable definition exists {} {} - "{}" - and no homepage was among '
                        'the pages this check could read, so where it sits relative to the '
                        'homepage is unknown'.format(where, page["url"], definition))
            return
        # Two questions, and they were being answered as one. "Does this site
        # say what it is" is answered here, and the answer is yes. "Does it say
        # it where the first machine to arrive will read it" is a different
        # question with a different answer, and it used to be a clause tacked
        # onto this decline instead of a finding of its own.
        result.skip("entity-definition",
                    'a quotable definition exists {} {} - "{}"'.format(
                        where, page["url"], definition))
        _definition_is_in_the_wrong_place(result, snapshot, pages, brand_name, brand,
                                          home, page, definition, where)
        return

    if declared:
        page, where_from, definition = declared
        result.signal("entity_definition", definition)
        result.signal("entity_definition_source", where_from)
        result.skip("entity-definition",
                    'a quotable definition is declared in {} of {} - "{}" - so the sentence '
                    'exists and a machine can read it, though no paragraph on the site says '
                    'it where a person would see it'.format(where_from, page["url"], definition))
        return

    if under_another_name:
        page, where_from, (other_name, definition), _ = under_another_name
        result.signal("entity_definition", definition)
        result.signal("definition_name_mismatch", other_name)
        result.skip("entity-definition",
                    '{} of {} states what this is - "{}" - under the name "{}", while the name '
                    'this audit read from the site is "{}". The sentence exists; the two names '
                    'disagree, which is what the brand-naming check reports. Claiming no page '
                    'states what the brand is would be false'.format(
                        where_from, page["url"], definition, other_name, brand_name))
        return

    # Distinguish "the brand is named but never defined" from "the brand is
    # never named at the top of its own homepage", because the fixes differ.
    home = identity[0]
    top = _top_words(home)
    searched = brand_forms(brand_name, brand)
    named = any(re.search(brand_pattern(form), top, re.I)
                for form in searched if brand_pattern(form))
    pronouns = len(re.findall(r"\b(?:we|our|us)\b", top, re.I))

    if not named and pronouns >= 3:
        evidence = ('The first {} words of {} use "we" or "our" {} times and never state the '
                    'brand name "{}". A machine reading this page cannot tell whose site it '
                    'is.'.format(DEFINITION_WINDOW_WORDS, home["url"], pronouns, brand_name))
        root = "no-entity-definition"
    else:
        # Every subject that was searched for, not only the declared name. The
        # sentence used to quote the full formal name alone, so a restaurant
        # group read that no sentence of the form "<three-word legal name> is a
        # ..." appears on its site - a string no site writes, and not what was
        # searched for even then.
        subjects = ", ".join('"{} is a ..."'.format(form) for form in
                             list(dict.fromkeys(searched[:1] + searched[-2:])))
        # The first-person shapes belong in the sentence too, because they
        # were searched for. A finding that lists only the subjects built from
        # the brand name reads as though writing "We are a ..." would not have
        # satisfied it, and it would have.
        # "was recognised", not "appears": the subjects listed are the ones
        # this audit built from the name it resolved, and a jute-bag maker's
        # about page defines the company under the legal name in its own
        # `<title>` - a subject that was never tried. What the check can say is
        # what it looked for and did not match.
        evidence = ('No sentence of the form {}, "We are a ..." or "I am a ..." was recognised '
                    'in the first {} words or the opening headings of {}{}.'.format(
                        subjects or '"{} is a ..."'.format(brand_name),
                        DEFINITION_WINDOW_WORDS, home["url"],
                        " or the about page" if len(identity) > 1 else ""))
        root = "no-entity-definition"

    # How much was read, said by the finding itself. This finding asserts an
    # absence, and the sentence the report composer appends to a finding that
    # states no scale of its own read "Seen on 3 of the 60 pages this crawl
    # read" under the title "No page states in one sentence what the brand is"
    # - telling a reader the missing sentence was seen on three pages, and
    # naming the pages the finding is attached to rather than the pages that
    # were searched. A finding about something not being there has to size what
    # was examined, and it has to say that is what the number is. The wording
    # carries its own "N of the M", which is how the composer knows the scale
    # is already stated and does not add a second one.
    evidence += (' Examined {} of the {} pages this crawl read: the opening and the whole text '
                 'of each, and the descriptions declared in their markup.'.format(
                     len(pages), _urls_reached(snapshot, len(pages))))

    # `high` only when the brand is never named at the top of its own homepage,
    # which is genuinely broken. Being named but not defined is the common case
    # - it holds for most sites, including ones assistants name readily - so it
    # is a strong recommendation, not a severe defect.
    #
    # And "no page" is a claim about every page, which a crawl that read one of
    # them cannot make. Same rule as the core facts: below half of what this
    # crawl could have read, the title says how many pages it is speaking for
    # and the severity comes down with it.
    share = _sample_share(snapshot, len(pages))
    title, severity = _scaled_to_the_sample(
        "No page states in one sentence what the brand is",
        "None of the {} this audit could read states in one sentence what the brand is".format(
            plural(len(pages), "page")),
        "high" if (not named and pronouns >= 3) else "medium", share)

    result.add(
        id_hint="no-quotable-entity-definition",
        title=title,
        severity=severity,
        confidence="high",
        evidence=evidence,
        checked=_definition_sources(declared_descriptions_seen),
        mechanism="B", root_cause=root,
        summary='Add one sentence near the top of the homepage: "{} is a <category> that '
                '<does what> for <whom>."'.format(brand_name),
        how_to_fix=[
            "Write the sentence in that exact shape. Name the brand as the subject; do not "
            "open with \"We\".",
            "Put it in the first paragraph of the homepage, in plain HTML text, not inside an "
            "image or a slider.",
            _repeat_the_definition_step(site_kind(snapshot)),
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
    # A stub the browser never read cannot answer this. On a software vendor's
    # site seven pages delivered 45 to 65 characters and were never rendered;
    # all seven were counted here as pages with no H1, and a browser shows a
    # heading on every one of them. `render_skipped` is set by the crawl on
    # exactly those pages.
    unread = [p for p in pages if p.get("render_skipped")]
    pages = [p for p in pages if not p.get("render_skipped")]
    if unread:
        result.signal("pages_not_judged_as_unrendered_stubs",
                      sorted(p["url"] for p in unread)[:10])
    # Both readings of the page's headings, and a page counts as having none
    # only when both agree. `headings` is built from the document with hidden
    # markup removed, which is what every prose check needs - a modal's H2 is
    # not a section of the article - and it is the wrong question here.
    # "Does the markup carry a heading" is answered by the DOM, which is what
    # a search or answer engine reads, and CMS themes routinely ship a
    # screen-reader-only `<h1 style="display:none">` naming the page exactly
    # as this check asks them to. Two sites were told "2 of 59 crawled page(s)
    # have no H1" and "1 of 57 crawled page(s) have no H1" about pages whose
    # H1 says what the page is and is hidden with CSS; the second finding
    # should not have existed at all.
    no_h1 = [p for p in pages
             if not (p.get("headings") or {}).get("h1")
             and not (p.get("headings_in_markup") or {}).get("h1")]
    # Multiple H1s are valid HTML5 sectioning, and skipped heading levels are
    # near-universal on real sites. Neither stops a machine reading the page.
    # Reporting them fired this check on 27 of 29 real sites and drowned the
    # findings that matter, so only a page with no top-level heading at all
    # counts now. See references/cited-vs-uncited-study.md.
    #
    # The `many_h1` and `skipped` lists that carried those two rules were kept
    # as empty lists and still tested, which left two `problems.append` blocks,
    # two `affected.update` calls and the `low` severity arm unreachable. Dead
    # code that reads as live is a description of behaviour the code does not
    # have, so it is gone rather than pinned empty.

    if not no_h1:
        # The sentence outlived the test: it said "exactly one H1 and no
        # skipped heading levels" about a museum where 58 of 60 pages carry
        # two H1s. What the check now tests is that every page has one at all.
        #
        # And these are every crawled page, not the content-typed ones.
        # `run()` passes all of them, so a home plus a legal, a careers and an
        # `other` page printed "each of the 4 crawled content pages".
        result.skip("heading-hierarchy",
                    "each of the {} carries a top-level heading. Pages with more than one H1, "
                    "and heading levels skipped in sequence, are deliberately not treated as "
                    "defects here and were not counted".format(
                        plural(len(pages), "crawled page")))
    else:
        affected = {p["url"] for p in no_h1}
        result.add(
            id_hint="heading-structure-unclear",
            title="Heading structure does not describe the page reliably",
            # `medium`, not `high`, and the reason is the line below it. One
            # detector reads the heading elements, and `heading_sequence` is
            # built from the same parsed tree rather than a second reading of
            # the page, so there is nothing here to corroborate it with. An H1
            # this audit cannot see - injected after load, or carried by an
            # image whose alt text the extractor missed - is still an H1.
            severity="medium", confidence="medium",
            evidence="{} of {} crawled page(s) have no H1. Examples: {}.".format(
                len(no_h1), len(pages), ", ".join(example_urls(sorted(affected)))),
            checked=("the h1 elements, including any alt text they carry, on every crawled "
                     "page whose own title does not say it is an error page - read twice, "
                     "once from the visible document and once from the whole markup, so an "
                     "h1 hidden with CSS still counts as an h1",),
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

    Called from `run` alongside the other three checks that reason about
    English prose, and passed the same pages they get. It used to be called
    from the structural heading check, which runs whatever the site is written
    in - so on a site declaring Japanese, where its three siblings each said "a
    word-overlap test cannot read this", it ran and reported. Word overlap
    needs words, and a script that writes no spaces between them has none to
    compare.
    """
    result.check("headings-name-their-topic")
    # And not a page that lists other pages. On a table of contents the
    # headings *are* the content, so "this heading's words appear nowhere else
    # in the page text" is guaranteed by the page's shape before anything is
    # measured: a manual's contents page scored 7 of 7 and was told to rewrite
    # its numbered section titles as questions.
    candidates = [p for p in pages
                  if not is_listing_page(p) and len(_content_sections(p)) >= SLOGAN_MIN_SECTIONS]
    if not candidates:
        result.skip("headings-name-their-topic",
                    "no crawled page that is not an index of other pages has {} or more H2 "
                    "sections, so heading vocabulary cannot be assessed "
                    "meaningfully".format(SLOGAN_MIN_SECTIONS))
        return

    offenders = []
    pages_measured = 0
    headings_seen = 0
    headings_judged = 0
    for page in candidates:
        body = (page.get("body_text") or "").lower()
        # The page's own text decides the minimum word length, because a
        # heading is too short to tell one script from another.
        minimum = _word_minimum(body)
        judged = 0
        disconnected = 0
        for section in _content_sections(page):
            headings_seen += 1
            # `letter_runs`, not `[a-z]{4,}`. That expression returns nothing
            # for Korean, Japanese, Arabic, Greek, Cyrillic or Devanagari, so
            # `judged` stayed 0, the loop fell through, and the check reported
            # that headings use vocabulary from the page text - about text it
            # had not managed to split into words at all.
            tokens = set(_content_words(section["heading"], minimum))
            # A heading made entirely of short or common words ("Who it is for")
            # carries no vocabulary to match, so it is not evidence either way.
            if not tokens:
                continue
            judged += 1
            if not any(t[:6] in body for t in tokens):
                disconnected += 1
        headings_judged += judged
        if judged < SLOGAN_MIN_SECTIONS:
            continue
        pages_measured += 1
        if disconnected / float(judged) > SLOGAN_HEADING_SHARE:
            offenders.append((page, disconnected, judged))

    if not offenders:
        # A threshold nothing could be measured against is not a pass. Saying
        # "headings use vocabulary that appears in the page text" about pages
        # whose headings were never split into words is asserting the
        # favourable answer to a question that was never asked.
        if not pages_measured:
            result.skip("headings-name-their-topic",
                        "only {} of the {} headings read carried words specific enough to "
                        "compare, and no page reached the {} needed, so heading vocabulary "
                        "could not be measured".format(
                            headings_judged, headings_seen, SLOGAN_MIN_SECTIONS))
            return
        result.skip("headings-name-their-topic",
                    "on the {} measured, headings use vocabulary that appears in the page "
                    "text".format(plural(pages_measured, "crawled page")))
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
        # One detector, and it is a vocabulary overlap rather than a reading of
        # meaning. "Starter plan" over "$480 per month" is correct writing that
        # this test cannot tell from a slogan, which is why the share has to be
        # near-total before anything is said and why it stays at medium.
        checked=("the words of each H2 against the whole body text of the same page",),
        mechanism="B", root_cause="heading-structure",
        summary="Rewrite section headings to name what the section is about.",
        how_to_fix=[
            'Replace slogans with the question or topic: "Built for speed" becomes '
            '"How fast the platform delivers results".',
            # "A customer" named the reader of a shop. The people this advice
            # is about are a library's borrowers, a manual's developers and a
            # council's residents as often as they are anybody's customers,
            # and "a reader" is all of them.
            "Use the words a reader would use, and the words that appear in the paragraph below.",
            "Keep the slogan as a subheading or in the body copy if it matters to the brand.",
        ],
        effort="low", owner="content owner",
        rationale="A heading is a machine's index into a long page. A heading that "
                  "does not name its topic means the section beneath it cannot be matched to a "
                  "question.",
        affected_pages=[p["url"] for p, _, _ in offenders],
    )


# Scripts whose words run to four letters or more the way English does. A whole
# word is two or three characters in Hangul, Han, Kana, Thai and Devanagari, so
# a four-letter minimum reads those headings as carrying no words at all - and
# the checks built on it then reported the favourable answer about text they
# had not split.
_LONG_WORD_SCRIPTS = frozenset({"latin", "cyrillic", "greek"})


def _word_minimum(text):
    """How many letters a run needs before it counts as a word here."""
    script = dominant_script(text or "")
    return 4 if script is None or script in _LONG_WORD_SCRIPTS else 2


def _content_words(text, minimum=4):
    """The words in `text` that carry its topic, lower-cased, any script.

    `letter_runs`, not `[a-z]{4,}`. That expression finds nothing at all in
    Korean, Japanese, Arabic, Greek, Cyrillic or Devanagari, so every check
    built on it silently compared two empty lists and then printed the
    favourable answer.
    """
    return [word for word in (run.lower() for run in letter_runs(text or "", minimum))
            if word not in STOPWORDS]


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
    worst_page = 0
    offenders = []
    examples = []
    for page in candidates:
        sections = _content_sections(page)
        fluff = [s for s in sections if _opens_with_warm_up(s)]
        total_sections += len(sections)
        fluff_sections += len(fluff)
        worst_page = max(worst_page, len(fluff))
        # A count as well as a share. Three sections is the minimum for a page
        # to be looked at, so three warm-up openings were 100% of a page and
        # the finding said the site opens its sections with prose - on the
        # strength of three paragraphs, one rewrite away from clean.
        if (len(fluff) >= FLUFF_FIRST_MIN_OFFENDING
                and len(fluff) / float(len(sections)) > FLUFF_FIRST_SHARE):
            offenders.append((page, len(fluff), len(sections)))
            if fluff and len(examples) < 2:
                examples.append('{} - "{}" opens with: "{}"'.format(
                    page["url"], truncate(fluff[0]["heading"], 60),
                    truncate(_first_sentence(fluff[0]["first_paragraph"]), 110)))

    result.signal("fluff_first", bool(offenders))
    result.signal("fluff_first_share", round(fluff_sections / float(total_sections), 3) if total_sections else 0.0)

    if not offenders:
        if worst_page < FLUFF_FIRST_MIN_OFFENDING and fluff_sections:
            # Not a pass. The pages are too short for the share to mean
            # anything, and saying "already answer-first" about them was
            # reporting the absence of a measurement as a good result.
            result.skip("answer-first-paragraphs",
                        "{} of {} sections open with warm-up prose, but no single page has as "
                        "many as {} of them, which is too few to call a pattern rather than a "
                        "paragraph or two".format(fluff_sections, total_sections,
                                                  FLUFF_FIRST_MIN_OFFENDING))
            return
        result.skip("answer-first-paragraphs",
                    "{} of {} sections on question-answering pages open with a sentence that "
                    "asserts something rather than one that greets the reader, sets a scene, "
                    "restates the question or says what the page is about to do, so they are "
                    "already answer-first".format(total_sections - fluff_sections,
                                                  total_sections))
        return

    result.add(
        id_hint="sections-do-not-answer-first",
        # `plural(n, "page opens", "pages open")` followed by " their sections"
        # printed "1 page opens their sections". The possessive belongs in the
        # part that changes with the number.
        title="{} sections with warm-up prose instead of the answer".format(
            plural(len(offenders), "page opens its", "pages open their")),
        severity="medium", confidence="medium",
        evidence="{} of {} sections on pages a reader arrives at with a specific question ({}%) open "
                 "by deferring - greeting the reader, setting a scene, restating the question, "
                 "announcing what the page will do, or stating a belief - rather than by "
                 "asserting anything about the heading above them. {}".format(
                     fluff_sections, total_sections, pct(fluff_sections, total_sections),
                     " ".join(examples)),
        # One source: the first sentence of each section, read once. And it
        # says which sentences were counted, because the count is the whole
        # claim: a page whose openings this reading does not recognise as
        # warm-up is not in the numerator.
        checked=("the first sentence of each non-navigational H2 section on the pricing, "
                 "FAQ, product, service, location and comparison pages that were crawled, "
                 "counted as warm-up only where it asks a question back or opens with one of "
                 "the deferring phrases this check recognises in English",),
        mechanism="B", root_cause="fluff-first",
        summary="Put the concrete answer in the first sentence of every section, then explain it.",
        how_to_fix=[
            "For each section, delete the opening sentence if it only greets, sets a scene or "
            "says what the section is about to do, and start with the claim instead.",
            "Keep the context and the persuasion; they work better after the fact than before it.",
            'Test each section by reading only its first sentence: does it assert something a '
            "reader could disagree with? If not, it is not answer-first yet.",
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


def _opens_with_warm_up(section):
    """Does this section's first sentence defer, or does it answer?

    Warm-up was operationalised as "the first sentence carries no digit, date
    or price and does not repeat a word from its own heading", and neither
    half of that is what warm-up is. A coffee roaster's FAQ section headed
    "Website" opens "Discount codes cannot combine with other discounts." -
    the answer, first sentence, nothing in front of it - and was counted as
    throat-clearing because it holds no number and because an answer does not
    repeat a category label like "Website" or "Shipping". Five of five
    sections on that page were reported as warm-up; one of them was.

    What actually separates the two is the job the sentence does. Warm-up
    defers: it greets, it sets a scene, it says what the page is about to do,
    it restates the question, or it announces a belief. An answer asserts
    something about the subject, with or without a number in it. So warm-up is
    recognised by what it says rather than inferred from what an answer failed
    to contain, and a sentence this pattern does not recognise counts as an
    answer - a check that reports a page for warm-up prose has to be able to
    show the warm-up.
    """
    opening = _first_sentence(section.get("first_paragraph") or "").strip()
    if not opening:
        return False
    # A section that opens by asking the question again has not answered it.
    # This is the one shape with no vocabulary of its own.
    if opening.endswith("?"):
        return True
    return bool(DEFERS_RE.search(opening))


# Names that are not a party: a day and a month are proper nouns and neither
# of them is somebody. Without this, "Open Tuesday to Sunday." would be read as
# introducing a stranger, and every sentence after it on the page would lose
# its attribution to the site.
_A_DAY_OR_A_MONTH = frozenset("""
monday tuesday wednesday thursday friday saturday sunday
mon tue tues wed weds thu thur thurs fri sat sun
january february march april may june july august september october november
december jan feb mar apr jun jul aug sep sept oct nov dec
""".split())


def _names_another_party(sentence, brand_name=""):
    """Does this sentence put a name in it that is not the brand's?

    A capitalised word that is not the first word of the sentence is a proper
    noun, and a proper noun is how a sentence says it is about somebody in
    particular: "Bombay has been his home since 1917" and "spending on data
    centers in the US" were both printed as facts about the audited site.

    The first word is skipped because every sentence capitalises it, and days
    and months are skipped because they name nobody. The brand's own words are
    skipped because a sentence naming the brand is the site's - though that
    case is already settled by `speaks_about_itself` before this is asked.
    """
    brand_words = {w.lower() for w in letter_runs(brand_name or "", 2)}
    for index, word in enumerate(re.findall(r"[^\W\d_]+", sentence or "")):
        if index == 0 or not word[:1].isupper():
            continue
        lowered = word.lower()
        if lowered in brand_words or lowered in _A_DAY_OR_A_MONTH:
            continue
        return True
    return False


def _own_sentence(text, pattern, brand_name, states_the_fact=None,
                  minimum=EVIDENCE_SENTENCE_MIN, attributed=True):
    """The first sentence matching `pattern` that the page says about itself.

    `sentence_with` returns the first match on the page whatever it is about,
    and this check printed that as the site's own fact. Every sentence is tried
    here instead, so a page whose first match belongs to somebody else can
    still supply the one that belongs to it.

    Whose sentence it is is `speaks_about_itself`'s question and is answered
    there, including the case that made a partner organisation's founding date
    into this brand's: "The Association of ..." opens somebody else's proper
    name, not a reference to the site. Do not reimplement that test here - it
    was written twice once already, and the copies disagreed.

    The pattern is matched twice, and the second match is against the trimmed
    sentence the report will print rather than against the whole one. A page
    whose text arrives as one unpunctuated run - a filter control, a row of
    labels - is a single 4,000-character "sentence", and the match that
    justified the claim can sit well past the 220-character cut. One report
    printed "Any Dates This Weekend This Week This Month Next 7 Days ..." as a
    site's stated pricing, with the figure that matched nowhere in it. A claim
    whose evidence does not contain the evidence is not a claim.

    `states_the_fact(trimmed, match)` is the per-fact half of the same rule:
    the pattern says a candidate is here, this says whether the candidate is
    the fact. It is given the words that will be printed, not the originals.

    A sentence that names nobody is read as belonging to whoever the sentence
    in front of it was about, which on the site's own page is the site. Asked
    of one sentence at a time, `speaks_about_itself` says no to every sentence
    whose subject is a pronoun, and that is how English writes the second
    sentence of a paragraph: a museum's about page reads "<Brand> is unlike any
    other. Founded in 1884, it houses ... more than 500,000 objects", and the
    report said "no founding year appears in the page text or in the page
    markup" - while quoting the sentence directly before that one, from the
    same paragraph, as the brand's own description. The pronoun is the brand
    because the sentence in front of it is.

    The running attribution is dropped the moment a sentence names somebody
    else, which is what keeps a partners page from handing a partner's founding
    date to the site under the same rule.

    `minimum` is the floor on how short the quoted evidence may be. It exists
    to stop a fragment - a menu label, half a table row - being printed as a
    statement, and the default is right for a fact that needs a clause around
    it. One fact does not: "Entry is free." is a whole answer to what something
    costs in thirteen characters, and the caller that reads price statements
    says so.

    `attributed=False` is the same reading with the attribution taken out, and
    it exists for one purpose: to decide whether this check is entitled to say
    the fact is absent. Whose sentence it is rests on `brand_name`, which is a
    string `resolve_brand` picked, and on a name test that has been wrong on
    every kind of site this marketplace has been run on. One D2C shop's
    `/our-story` reads "<Brand>® was founded in 1998 in <city>, <country>", and
    the report said no founding year appears in the page text - the pattern
    matched, the attribution did not.

    So a caller about to publish a denial asks a second time without it. What
    comes back is never quoted as the site's own fact, because it may well be
    somebody else's; it only refuses the denial. See the rule in
    `audit_common.ABSENCE_DECIDED_BY_A_VOCABULARY`.
    """
    if not text:
        return None
    # Nobody has been named yet, and the opening words of a page are the
    # page's own.
    the_site_is_the_subject = True
    for sentence in sentences(text):
        if speaks_about_itself(sentence, brand_name):
            the_site_is_the_subject = True
        elif _names_another_party(sentence, brand_name):
            the_site_is_the_subject = False
        if not pattern.search(sentence):
            continue
        if attributed and not the_site_is_the_subject:
            continue
        trimmed = truncate(sentence.strip(), EVIDENCE_SENTENCE_MAX)
        if len(trimmed) < minimum:
            continue
        kept = pattern.search(trimmed)
        if kept is None:
            continue
        if states_the_fact is not None and not states_the_fact(trimmed, kept):
            continue
        return trimmed
    return None


# --------------------------------------------------------------------------
# What a title may claim from what was actually read
#
# "The site never states its founding or team facts" and "The site never states
# its location or service area" were the two items a report ranked as do-first,
# over evidence reading "Across the 1 of 2 page(s) this audit read". The site's
# own about page carries the founding year, the headquarters and the leadership
# team; the crawl never reached it, because the site's robots.txt asks for a
# 30-second delay between requests and that consumed the whole budget.
#
# The body text hedged honestly and the title did not, and a site owner reads
# the title. So a title saying "never" or "no page" has to be earned by the
# sample: below this share, the title says how much was read and the severity
# comes down to match.
#
# The denominator is what this crawl could ever have read, not what the site
# publishes. A documentation site declaring 32,820 sitemap URLs is not
# under-sampled by a 60-page crawl that hit its own ceiling - 60 pages is the
# whole instrument. It is under-sampled when it stopped at one.
ABSENCE_TITLE_MIN_COVERAGE = 0.5

# The floor on a quoted price statement, in characters. Below the general
# evidence floor, because a statement that something costs nothing is complete
# in three words - "Entry is free" - while the facts the general floor was set
# for need a clause around them to be readable.
PRICE_STATEMENT_MIN = 12


def _urls_reached(snapshot, pages_read):
    """How many page-shaped URLs the crawl has records for, for a sentence.

    The scale every absence finding states about itself. It was
    `crawl["pages_crawled"]`, which counts every record the crawl wrote - so a
    finding said it had examined "7 of the 16 pages this crawl read" about a
    crawl that had read seven pages and nine downloads. Falls back to the
    pages in hand rather than printing a zero denominator, which is what a
    snapshot with no crawl block would otherwise produce.
    """
    return len(page_shaped_urls(snapshot)) or pages_read


def _sample_share(snapshot, pages_read):
    """How much of what this crawl could have read it actually read, 0 to 1."""
    crawl = snapshot.get("crawl") or {}
    ceiling = int(crawl.get("max_pages") or MAX_PAGES) or MAX_PAGES
    declared = sitemap_scope(snapshot.get("sitemaps") or []).get("urls_counted") or 0
    # `crawl["pages_crawled"]` is every record the crawl wrote, downloads
    # included, so a site that answered nine archives looked nine URLs larger
    # than it is and every finding here was scaled down against a denominator
    # of things that were never pages. The count of page-shaped URLs is the
    # same number on a site with no such responses and the honest one on a site
    # with them.
    site_size = max(int(declared), len(page_shaped_urls(snapshot)), int(pages_read))
    # A crawl that stopped with work still queued is the crawl saying it did
    # not finish, which is the case a site with no sitemap would otherwise hide.
    if crawl.get("budget_exhausted"):
        site_size = max(site_size, ceiling)
    reachable = min(site_size, ceiling)
    return (pages_read / float(reachable)) if reachable else 1.0


# One step milder, for a claim the sample cannot carry at full strength.
_MILDER = {"critical": "high", "high": "medium", "medium": "low", "low": "low", "info": "info"}


def _scaled_to_the_sample(title, scoped_title, severity, share):
    """`(title, severity)`, cut down where the crawl read too little to claim."""
    if share >= ABSENCE_TITLE_MIN_COVERAGE:
        return title, severity
    return scoped_title, _MILDER.get(severity, severity)


# `git@github.com` is the SSH host in a `git clone` line, not a way to reach
# anyone. A documentation site was told its contact route was in place and
# handed that string as the evidence.
_NOT_A_CONTACT_EMAIL_RE = re.compile(
    r"^(?:git|hg|svn|noreply|no-reply|donotreply)@", re.I)


# What the page calls a run of digits, when it calls it anything at all. A
# Japanese university's report gave its only contact route as
# "9784788518797" - the ISBN of a book, printed under a caption reading "ISBN
# code" - and that barcode suppressed the true finding that no crawled page
# carries a way to reach anyone.
#
# A closed vocabulary, and an English and Japanese one. A code label written in
# any other language is not recognised here, and the two arithmetic tests below
# are what catch the number then.
# How far apart a street and a postal code may sit in the page text and still
# be one address line. Room for a locality and a county and no more - the same
# window the extractor uses when it decides which postal code belongs to which
# street.
ADDRESS_LINE_WINDOW = 48


def _address_line(text, street, postcode):
    """The words the page prints from the street to the postal code, or ''.

    Empty when the page does not print them together, which is the answer that
    stops two fragments being quoted as one line. See `_address_words`.
    """
    if not (text and street and postcode):
        return ""
    start = text.find(street)
    if start < 0:
        return ""
    postcode_at = text.find(postcode, start + len(street))
    if postcode_at < 0 or postcode_at - (start + len(street)) > ADDRESS_LINE_WINDOW:
        return ""
    return " ".join(text[start:postcode_at + len(postcode)].split())


def _only_inside_a_longer_token(text, run):
    """Does this run of digits appear on the page only inside a longer word?

    A documentation page's `curl` example carries the paper identifier
    `0f40b1f08821e22e859c6050916cec3667778613`, and ten digits out of the
    middle of it - `3667778613` - were recorded and printed as the site's way
    of getting in touch, which marked the contact fact present and suppressed
    the finding that the site publishes no way to reach anyone.

    The rule this enforces is the same one the whole check runs on: the words
    the report prints have to contain the fact. A telephone number is a token
    on the page, delimited by something that is not a letter or a digit; a
    slice of a hash is not a token at all, and no reader could find it. A run
    the page does not print verbatim is left alone - it is very often the same
    number printed with spaces or dashes in it, and this test has nothing to
    say about that.
    """
    if not (text and run):
        return False
    found = False
    at = text.find(run)
    while at >= 0:
        found = True
        before = text[at - 1] if at else ""
        after = text[at + len(run):at + len(run) + 1]
        if not (before.isalnum() or after.isalnum()):
            return False
        at = text.find(run, at + 1)
    return found


def _usable_phones(facts, page=None, key="phones"):
    """The telephone numbers on this page, minus the runs that are not numbers.

    A number the site put in a `tel:` href is the site saying it is a telephone
    number, and nothing here second-guesses that. Only the runs scraped out of
    the page text are tested.

    `key` selects which list is read - every number on the page, or only the
    ones in its header, footer and `<address>` element. The ISBN and
    code-label tests are the same either way, so they are written once.
    """
    phones = [p for p in (facts.get(key) or []) if p]
    if facts.get("declared_phones"):
        return phones
    text = (page or {}).get("body_text") or ""
    kept = []
    for phone in phones:
        index = text.find(phone)
        context = text[max(0, index - CODE_LABEL_WINDOW):index] if index > 0 else ""
        if not_a_telephone_number(phone, context):
            continue
        if _only_inside_a_longer_token(text, phone):
            continue
        if _beside_another_sites_address(text, phone, page):
            continue
        if _A_RUN_OF_YEARS_RE.fullmatch(phone.strip()):
            continue
        if _a_slice_of_a_figure(text, phone):
            continue
        if _given_as_another_businesss_number(text, phone, page):
            continue
        kept.append(phone)
    return kept


def _a_slice_of_a_figure(text, phone):
    """Does this run start inside a comma-grouped figure, like "2,006 777 813"?

    A shareholder letter's earnings table reads "1,771 2,006 777 813 665 419",
    and "006 777 813" was scraped out of it as a telephone number: its first
    group is the tail of "2,006". A number that starts straight after a digit
    and a thousands separator is the end of a figure, not a number on its own.
    """
    index = text.find(phone) if text and phone else -1
    return index >= 2 and text[index - 1] in ",." and text[index - 2].isdigit()


# "<Name>'s (number)" in a sentence on the site, where the name is not the
# site's. A holding company's message page reads "Call <Person> or <Person> at
# <Store>'s (800-...)" about a jeweller it owns, and that number was the next
# candidate for the holding company's own contact route. The site's own name
# is read from its host, so "call <Brand>'s front desk on ..." is kept.
_POSSESSIVE_BEFORE_A_NUMBER_RE = re.compile(
    r"\b([A-Z][\w&-]*(?:\s+[A-Z][\w&-]*){0,3})['’]s?\W{0,3}$")


def _given_as_another_businesss_number(text, phone, page):
    """Is this number printed as "<another business>'s (number)"?"""
    index = text.find(phone) if text and phone else -1
    if index < 0:
        return False
    before = text[max(0, index - 60):index]
    match = _POSSESSIVE_BEFORE_A_NUMBER_RE.search(before)
    if not match:
        return False
    owner = comparison_key(match.group(1))
    host = comparison_key(strip_www(urlsplit((page or {}).get("url") or "").netloc.lower())
                          .split(".")[0])
    return bool(owner) and not (host and (owner in host or host in owner))


# Three or more year-shaped groups and nothing else. A shareholder letter's
# table header "1978 1977 1978" was scraped as a telephone number and printed
# as the site's way of getting in touch. A column of years is a table.
_A_RUN_OF_YEARS_RE = re.compile(r"(?:(?:1[5-9]|20)\d{2}[\s./-]*){3,}")


# A number in somebody else's advert. A conglomerate's homepage carries its
# insurance subsidiary's block-capitals advert - "FOR A FREE CAR INSURANCE RATE
# QUOTE ... WWW.<SUBSIDIARY>.COM OR CALL 1-888-..." - and that number was
# recorded as the conglomerate's own way of getting in touch, which marked the
# contact fact present on a site that publishes no telephone number or email
# of its own. A number printed in the same sentence as another site's web
# address is that site's number.
#
# Only an address written as one: `www.` or a scheme in front, or one of the
# generic endings, so "Vue.js" or "Node.js" beside a number is not read as a
# website. The site's own host and its subdomains are its own, and a profile
# platform's address ("follow us at <platform>.com/<brand>") is the site's
# account, not a rival, so neither takes a number away.
from page_extract import SOCIAL_PLATFORMS  # noqa: E402

_ADVERT_WINDOW = 120
_WEB_ADDRESS_RE = re.compile(
    r"(?<![@\w.-])(?P<scheme>https?://|www\.)?(?P<host>(?:[a-z0-9-]+\.)+(?P<tld>[a-z]{2,}))"
    r"(?![\w-])", re.I)
_GENERIC_ENDINGS = frozenset({"com", "net", "org", "info", "biz", "io", "co", "app", "shop",
                              "store", "online", "site"})
_SENTENCE_BREAK_RE = re.compile(r"[.!?](?=\s)")


def _beside_another_sites_address(text, phone, page):
    """Is this number printed in one sentence with another website's address?"""
    index = text.find(phone) if text and phone else -1
    if index < 0:
        return False
    before = text[max(0, index - _ADVERT_WINDOW):index]
    breaks = list(_SENTENCE_BREAK_RE.finditer(before))
    if breaks:
        before = before[breaks[-1].end():]
    after = text[index + len(phone):index + len(phone) + _ADVERT_WINDOW]
    stop = _SENTENCE_BREAK_RE.search(after)
    if stop:
        after = after[:stop.start()]
    own = strip_www(urlsplit((page or {}).get("url") or "").netloc.lower())
    for match in _WEB_ADDRESS_RE.finditer(before + " " + after):
        if not (match.group("scheme") or match.group("tld").lower() in _GENERIC_ENDINGS):
            continue
        host = strip_www(match.group("host").lower())
        if own and (host == own or host.endswith("." + own) or own.endswith("." + host)):
            continue
        if any(host == known or host.endswith("." + known)
               for known in SOCIAL_PLATFORMS if "/" not in known):
            continue
        return True
    return False


def _contact_detail(facts, page=None, chrome_only=False):
    """An address or number a person could actually use, or ''.

    `chrome_only` reads the header, the footer and the `<address>` element and
    nothing else. A detail there is the site's own by construction, because
    the template puts it on every page; a detail in the body of a page belongs
    to whatever the page is about, and a partners page gave a partner's email
    as the audited site's own way of getting in touch. Both readings are made
    at the caller - a site that prints its address only in body copy has still
    printed it - and the caller says which one it used.
    """
    emails_key = "chrome_emails" if chrome_only else "emails"
    phones_key = "chrome_phones" if chrome_only else "phones"
    emails = [e for e in (facts.get(emails_key) or [])
              if e and not _NOT_A_CONTACT_EMAIL_RE.match(e.strip())]
    return next(iter(emails + _usable_phones(facts, page, phones_key)), "")


# --------------------------------------------------------------------------
# Which facts a site is expected to state, and which kind of site expects them
#
# The core-facts check asks four questions written for a business with premises
# and customers, and asked them of every site alike. A command-line JSON
# processor - a program, with a repository, a licence and a manual - was told
# "The site never states its founding facts in plain text", with the fix "Add a
# short paragraph on the about page: `<Brand>` was founded in `<year>` in
# `<place>`", and separately "The site never states its location or service
# area", with the fix "`<Brand>` serves customers across `<regions>`". A
# program has no premises, no service area and no founding ceremony; neither
# finding names anything its owner could act on, and a reader who meets one of
# them stops reading, which costs the report every true finding underneath it.
#
# `site_kind` answers what the site is, and `might_be` is the gate: it answers
# True for every site the classifier could not place and for every weak
# determination, so these two facts are asked for exactly as they are today
# unless the site's own structure confidently says otherwise.
#
# What is gated is the *expectation* of the fact, never the observation. A
# project that prints an address still has that address reported as found, and
# still gets asked to mark it up by `structured-data-audit`. What goes quiet is
# the claim that the absence is a defect.
_STATES_A_FOUNDING_YEAR = (LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION,
                           PUBLICATION, PUBLIC_BODY)
# `PUBLIC_BODY` is here for the same reason the other three are: a library, a
# museum, a town office and a school are places a person walks into, with an
# address and opening hours, and "where is it and when is it open" is the
# commonest thing anybody asks a machine about one. It was missing because the
# classifier could not previously reach that kind for a body whose address does
# not end `.gov` - a New Hampshire town library on a `.net` domain was read as a
# researcher and told to claim a researcher identifier - so nothing had ever
# tested the tuple against a public body that was correctly classified.
_STATES_WHERE_IT_IS = (LOCAL_BUSINESS, ONLINE_SELLER, ORGANISATION, PUBLIC_BODY)

# How the report names what it concluded. A skip that says only "gated" tells a
# reader nothing; the appendix exists so that a check going quiet is legible,
# so the noun and the evidence both go in the sentence. Every kind in the
# vocabulary is named, including the three that pass both gates above and so
# cannot reach a decline today - a half-filled map is how the next gate ships
# a report saying "this site reads as something other than a business".
_KIND_IN_WORDS = {
    LOCAL_BUSINESS: "a business with premises a visitor goes to",
    ONLINE_SELLER: "a shop with nowhere to visit",
    ORGANISATION: "an organisation with people",
    PROJECT: "a software project, which has maintainers rather than a leadership "
             "team and no premises",
    PUBLIC_BODY: "a public body",
    PERSONAL_OR_ACADEMIC: "a person, a university or a research group rather than "
                          "a company",
    PUBLICATION: "a publication, whose output is articles",
}


# The page types on which a business publishes where it is. The loose address
# reading below is run over these and nowhere else - a comma hierarchy in a
# product description is a specification, and a check that read one as an
# address would decline on every shop.
_PAGES_THAT_CARRY_AN_ADDRESS = ("home", "about", "contact", "location")


def _a_founding_year_this_audit_could_not_attribute(pages, brand_name):
    """`(page, sentence)` carrying a founding year this audit read as nobody's.

    The same pattern and the same "is this year an origin" test the check uses
    to report the fact found, with the one thing removed that decided the
    real false positive: whose sentence it is. See `_own_sentence`.
    """
    for page in pages:
        if is_listing_page(page):
            continue
        sentence = _own_sentence(_readable_text(page), FOUNDING_FACT_RE, brand_name,
                                 states_the_fact=_states_a_founding_year,
                                 attributed=False)
        if sentence:
            return page, sentence
    return None


def _an_address_this_audit_could_not_read(pages):
    """`(page, run)` where a page prints an address shape the extractor missed.

    `contact_facts` is built from a street matcher written around a house
    number and a British or American postal code. One real contact page prints

        <plus code>, RT.10/RW.11, <district>, <district>, <city> City, <province>

    which is how a very large number of Indonesian businesses say where they
    are, and the report answered "no postal address appears in the page text or
    in the page markup" with that line in its own citation table.

    `reads_like_an_address` recognises the shape rather than the country, and
    what it returns is never quoted - the quoted address stays `_address_words`,
    which is strict for the good reason that a quote is a claim.
    """
    for page in pages:
        if is_listing_page(page) or page.get("page_type") not in _PAGES_THAT_CARRY_AN_ADDRESS:
            continue
        run = reads_like_an_address(_readable_text(page))
        if run:
            return page, run
    return None


def _could_not_settle(fact, page, quote, because):
    """What the appendix prints where a denial was refused by a second reading.

    Deliberately not the sentence a finding would carry. This says what was
    seen and what could not be concluded from it, and it never says the site
    states the fact - the second reading is loose precisely so that it can be
    wrong in the direction that costs nothing.
    """
    return ('not settled - {} prints "{}", which {}. This audit cannot read it as {}, and '
            'saying the site states none would be false, so neither is claimed'.format(
                page["url"], truncate(quote, 200), because, fact))


def _not_expected_to_state(kind, fact, because):
    """The decline a gated fact prints: what was concluded, and on what evidence."""
    return ("this site reads as {} ({}), so {} is not a fact it is expected to "
            "state. {} Anything it does state is still read and reported: what is "
            "gated here is the expectation, not the observation".format(
                _KIND_IN_WORDS.get(kind.kind, "something other than a business"),
                kind.why(), fact, because))


def _the_site_calls_itself_this(subject, snapshot, pages, brand):
    """Is this the site naming itself, or is it naming something it sells?

    The guard on the reading above, and it was missing. That reading exists for
    one real failure: a bag maker's about page opens "Vinayak Enterprise is a
    leading manufacturer and supplier of eco-friendly packaging products", the
    audit had resolved the brand as "Best handcrafted Jute products Online" off
    the `<title>`, and the report answered "no sentence of the form '... is a
    ...' was recognised" with that line in its own citation table. The legal
    name was in the site's own title and was never tried.

    Without this guard the reading accepts any subject on any page, so a
    product page opening "Replenishment Autopilot is a paid add-on that ..."
    silenced the finding on a site that genuinely never says what the company
    is - a true absence claim deleted, which is a miss, and worse than the
    false positive this was written for.

    So the subject has to be a name the site uses for *itself*: one the crawl
    already resolved as a candidate, the words its own domain label spells, or
    a segment of its homepage title. All three are the site naming itself. What
    a product page calls a product is none of them.
    """
    key = comparison_key(subject)
    if not key:
        return False
    # `for_lookup()`, asserted forms only. This function returning True
    # *suppresses* the finding, so a permissive reading here buys misses,
    # and `NameForms` documents that split: the matching reader is for a
    # check whose failure mode is a miss, and this one's is the opposite.
    for candidate in name_candidates(brand or {}).for_lookup():
        if comparison_key(candidate) == key:
            return True
    host = str(((snapshot or {}).get("brand") or {}).get("host") or "")
    label = strip_www(host).split(".")[0] if host else ""
    if label and comparison_key(name_the_site_spells(label, [subject])) == key:
        return True
    home = next((p for p in pages if p.get("page_type") == "home"), None)
    return any(comparison_key(part) == key
               for part in title_segments((home or {}).get("title") or ""))


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
    # URLs that could have been pages, not every record in the snapshot. A
    # response whose own content type proved it was never a page - a release
    # archive, a patch file - is not a URL this crawler was refused a page at,
    # and counting nine of them as such is what pushed a 7-of-7 crawl to 7 of
    # 16 and silenced four true findings about the pages that *were* read.
    # A 403, a challenge, a body that would not decompress and an empty 200 all
    # stay in: those are the cases this share exists to catch.
    attempted = len(page_shaped_urls(snapshot))
    if attempted and len(pages) < attempted * CORE_FACTS_MIN_COVERAGE:
        result.skip("core-facts-present",
                    # "the rest were refused or challenged" named a cause with
                    # no test for either, on a crawl where all sixteen URLs
                    # answered 200. What the crawl recorded is counted instead,
                    # so a refusal shape nobody has met yet reads as accurately
                    # as the two that have already cost marks.
                    "only {} of the {} URLs the crawl reached could be read ({}) - so whether "
                    "the site states these facts could not be determined. What it publishes on "
                    "the pages this crawler was not allowed to see is unknown, and the access "
                    "finding above is the one to act on".format(
                        len(pages), attempted,
                        why_little_was_read(snapshot) or "no reason was recorded"))
        return
    by_type = {}
    for page in pages:
        by_type.setdefault(page["page_type"], []).append(page)

    # What kind of thing this site is, read once from its own structure. Used
    # to decide which of these four facts it is fair to call absent, and to
    # word the fix for the ones that stay. Undetermined answers True to every
    # `might_be`, so a site this cannot place is asked for all four exactly as
    # it was before this line existed.
    kind = site_kind(snapshot)
    result.signal("site_kind", kind.kind or "undetermined")
    result.signal("site_kind_confidence", kind.confidence)

    missing = []
    found = {}
    # Notes rather than verifications: a fact this check declined to look for.
    # They were counted as facts found, so a skip reading "all four core facts
    # are stated in plain text" was followed by three lines saying "not
    # checked" or "not applicable".
    unverified = set()

    def stated(pattern, among=None, states_the_fact=None,
               minimum=EVIDENCE_SENTENCE_MIN):
        """The page and the sentence on it that state this, or (None, None).

        The sentence is the point. A claim that the site states something has
        to be able to show the words, and a pattern that matches a fragment -
        a menu label, a word in a table cell - has not found a statement.

        Whose fact it is has to be decided, and `speaks_about_itself` is the
        shared test for it. Without it this check quoted, as facts about the
        site it was auditing: "Bombay has been his home since 1917." as a
        London restaurant's founding date, and "As this chart shows, between
        2018 and 2022, spending on building data centers in the US hovered
        between $500 million and $1 billion per month" as a statistics
        charity's own pricing. Neither sentence is about the site.

        The cost is a sentence like "The studio was founded in 2015 by Nell
        Achterberg", which is about the site and says so in no way the test can
        read. That is a fact reported missing rather than a stranger's fact
        reported as the brand's, and of the two errors it is the recoverable
        one: the whole page is searched sentence by sentence, so one
        unattributable sentence no longer hides an attributable one behind it.

        `states_the_fact` is the test that the sentence found says the thing
        the caller is looking for, rather than merely carrying the words the
        pattern reads. It is the fact's own test, so it lives with the fact.
        """
        for page in (among if among is not None else pages):
            if is_listing_page(page):
                continue
            sentence = _own_sentence(_readable_text(page), pattern, brand_name,
                                     states_the_fact=states_the_fact,
                                     minimum=minimum)
            if sentence is not None:
                return page, sentence
        return None, None

    def note(url, sentence, aside=""):
        """What the report prints for a fact it found: where, and the words."""
        where = "{}{}".format(url, " {}".format(aside) if aside else "")
        return '{} - "{}"'.format(where, sentence) if sentence else where

    # 1. Price, or an explicit statement that pricing is on request.
    #
    # "Does the site sell anything" is asked first, before "is there a currency
    # symbol anywhere". A global law firm's diversity page mentions a client's
    # US$60 million bond issuance; read as the firm's own pricing, it satisfied
    # this check and silently prevented the honest answer - that a law firm
    # publishes no prices and is not supposed to. A number in a case study is
    # not what anything costs, and no pattern will ever be able to tell the
    # difference. What the site is is knowable; what a stray figure means is
    # not.
    sells = sells_something(snapshot, pages)
    price_page = price_quote = None
    price_aside = ""
    listing_price_page = None
    listing_prices = []
    any_currency_figure = False
    # `(page, figure, aside)` for a figure shown only among interface labels.
    label_price = None
    if sells:
        for page in pages:
            prices = page.get("prices") or find_prices(page.get("body_text") or "")
            if not prices:
                continue
            any_currency_figure = True
            # A currency figure is a price only when it is what something
            # costs. A charity's homepage prints "Funds raised US$
            # 1,115,503,258 Nets funded 509,754,338 People protected
            # 917,557,808" - its lifetime total - and that line was recorded
            # and printed as the site's pricing, which marked the core facts
            # check passed and hid the fact that the site publishes no price
            # at all because it sells nothing.
            #
            # `quotes_its_own_price` reads the page rather than the figure: a
            # tally or an appeal sits among sentences that ask for a gift, a
            # price sits among sentences that say what something costs. It is
            # the same reading `sells_something` uses to decide whether this
            # site sells at all, so the two cannot disagree about one page.
            if not quotes_its_own_price(_readable_text(page)):
                continue
            # A listing page's text is a run of other pages' headlines, teasers
            # and chart labels, so a figure in it is not this page quoting a
            # price. A statistics charity's article index supplied "spending on
            # building data centers in the US hovered between $500 million and
            # $1 billion per month" as the charity's own pricing.
            #
            # That answers "is this figure one product's price". It does not
            # answer "does this site state its pricing at all", and the
            # exclusion was deciding both: a software vendor whose crawled shop
            # page carries five figures the extractor had already pulled out
            # was told that no price appears anywhere on 28 of 28 pages read. A
            # page listing things for sale is a price list, which is the site
            # stating its prices without any one of them being the price, so it
            # is kept for the site-wide fact and kept out of the quote.
            if is_listing_page(page):
                if listing_price_page is None and _is_price_list_page(page):
                    listing_price_page, listing_prices = page, list(prices)
                continue
            # Heading boundaries restored first, or the quoted price arrives
            # with the heading above it stuck to the front: "How much it costs
            # Plans start at $480 per month".
            quote = sentence_with(_readable_text(page), re.compile(re.escape(prices[0])))
            # The reader has to be able to see the price in the quote. An event
            # site's homepage arrives as one unpunctuated run of filter labels,
            # so the whole page is a single "sentence" and the figure that
            # matched sat past the 220-character cut: the report printed "Find
            # More Events Explore Any Dates This Weekend This Week This Month
            # Next 7 Days ..." as the site's stated pricing. Where the sentence
            # cannot show the figure, the figure is the quote.
            shown, aside = quote, ""
            if not _quote_shows_a_price(shown):
                shown, aside = prices[0], ("(the page states this figure, but not inside a "
                                           "sentence short enough to quote)")
            # A cart drawer is not a statement of price. Where the words
            # around the figure are a run of interface labels, the figure is
            # kept as evidence the site shows prices, and the search goes on
            # for a page that says one in a sentence. See
            # `_reads_as_interface_labels`.
            if quote and _reads_as_interface_labels(quote):
                if label_price is None:
                    label_price = (page, prices[0],
                                   "(the page shows this figure among interface labels - "
                                   "buttons, menus, a cart - rather than in a sentence)")
                continue
            # A figure on a page whose job is prices is the site's price. A
            # figure anywhere else has to say whose it is. A project-tracking
            # company's report recorded "pricing on /asks" - where the only
            # figure on the page is a customer saying "We save close to
            # $30,000 per year" - and that reading suppressed the finding,
            # while the real per-seat prices sat unexamined on /pricing.
            if page.get("page_type") in PRICE_BEARING_TYPES:
                price_page, price_quote, price_aside = page, shown, aside
                break
            if quote and speaks_about_itself(quote, brand_name):
                price_page, price_quote, price_aside = page, shown, aside
                break
    # A shorter floor than the other facts get. "Entry is free." is thirteen
    # characters and it is the whole answer; the default floor of fifteen threw
    # it away, and the museum that prints it was told at high severity that no
    # price statement appears anywhere on its site.
    #
    # On a page whose job is prices, "free" is the price of whatever the page
    # sells. Anywhere else it has to be the site that is free, and not a
    # template or a plugin for it listed on the site. See
    # `_free_is_said_of_something_built_on_the_brand`.
    #
    # Two more things "free" can be that are not a price: a phrase quoted in
    # order to be argued with, and a page filed under the year it was written.
    # See `_inside_quotation_marks` and `_a_dated_document`.
    undated = [p for p in pages if not _a_dated_document(p)]
    free_page, free_quote = stated(
        COSTS_NOTHING_RE, minimum=PRICE_STATEMENT_MIN,
        among=[p for p in undated if p.get("page_type") in PRICE_BEARING_TYPES],
        states_the_fact=lambda sentence, match: not _inside_quotation_marks(sentence, match))
    if free_page is None:
        free_page, free_quote = stated(
            COSTS_NOTHING_RE, minimum=PRICE_STATEMENT_MIN, among=undated,
            states_the_fact=lambda sentence, match: not (
                _inside_quotation_marks(sentence, match)
                or _free_is_said_of_something_built_on_the_brand(sentence, brand_name)))
    # A figure seen only among interface labels still says the site shows
    # prices; it is the last reading, after every sentence has been tried.
    if price_page is None and label_price is not None:
        price_page, price_quote, price_aside = label_price
    request_page, request_quote = stated(QUOTE_ON_REQUEST_RE)
    # A figure declared in `Offer` markup, which the text scan cannot see. A
    # shop whose prices are rendered into elements the extractor reads as
    # labels, and stated exactly once in its structured data, does publish its
    # prices, and this is the only reading that finds them.
    offer_page = offer_price = None
    if sells:
        for page in pages:
            if is_listing_page(page):
                continue
            declared = _declared_price(page)
            if declared:
                offer_page, offer_price = page, declared
                break
    if price_page:
        found["pricing"] = note(price_page["url"], price_quote, price_aside)
    elif offer_page:
        found["pricing"] = note(offer_page["url"], offer_price,
                                "(declared in Offer markup rather than written in the text)")
    elif listing_price_page:
        # The figures, not a sentence around one of them. On a page that lists
        # products each figure belongs to a different item, so quoting one with
        # the words either side of it attributes another product's price to
        # this page, which is the reason listing pages are kept out of the
        # quote in the first place.
        found["pricing"] = note(
            listing_price_page["url"], ", ".join(listing_prices[:5]),
            "(a page listing several items, so these are the prices it publishes rather "
            "than the price of any one thing)")
    elif free_page:
        found["pricing"] = note(free_page["url"], free_quote, "(states that it costs nothing)")
    elif request_page:
        found["pricing"] = note(request_page["url"], request_quote,
                                "(states pricing is on request)")
    elif not sells:
        # "This site never states its pricing" is only a defect if the site has
        # a price. A medical charity was told it, and handed a fix reading
        # "contact sales for a quote".
        unverified.add("pricing")
        found["pricing"] = ("not applicable - nothing on this site is for sale, so there is "
                            "no price for an assistant to be missing")
    else:
        # A `pricing` page type is a URL shape, not a transaction: `subscribe`,
        # `membership`, `plan`, `plans` and `rates` all resolve to it. A free
        # newsletter with `/subscribe`, a club with `/membership` and a public
        # library with `/plans` were each told at HIGH severity that they hide
        # their prices, and handed a fix reading "contact sales for a quote".
        # A currency figure somewhere on the site is the site itself saying it
        # has a price to publish, so nothing escalates without one.
        has_pricing_page = bool(by_type.get("pricing"))
        escalate = has_pricing_page and any_currency_figure
        missing.append(("pricing", "high" if escalate else "medium",
                        "a pricing page was crawled but shows no figure and no "
                        "'contact us for pricing' statement" if has_pricing_page
                        else "no price and no 'pricing on request' statement appears anywhere "
                             "on the crawled pages",
                        # Three detectors that share nothing: one pulls
                        # currency figures out of the text, one reads
                        # sentences, one reads markup. A free open-source
                        # project answers the question in words and publishes
                        # no figure, and was told it hides its prices until the
                        # second of the three was added.
                        ("currency figures pulled from the text of every crawled page, "
                         "including the product and collection pages that list several items "
                         "at once, excluding the blog, news and search indexes whose figures "
                         "belong to whatever they are linking to, and counting a figure only "
                         "where the sentences around it say what something costs rather than "
                         "ask for a gift or report a total raised",
                         "sentences saying the thing costs nothing - free to use, free "
                         "entry, admission is free, no charge - or that its pricing is "
                         "given on request. Both vocabularies are English, and neither "
                         "counts the other sense of the word, as in 'free to cancel'",
                         "`price`, `lowPrice` and `highPrice` in the Offer and "
                         "PriceSpecification markup of every crawled page")))

    # 2. Where the business is, or who it serves.
    #
    # Not from a listing page. A statistics charity's address was reported as
    # "5s Deaths in road 10258", which is two labels off a chart axis on a page
    # of charts.
    # The page that can show the address, ahead of the page that merely has
    # one. `has_address` is also true for an `<address>` element the extractor
    # read no street or postcode out of, and that page was recorded as the
    # site's stated location with nothing printed after the URL - a fact
    # asserted with no words behind it, which is the same defect as quoting
    # words that do not contain the fact.
    # A quoted fact is a string the page contains, not two strings joined.
    #
    # `street_hint` and `postcode_hint` are two separate readings of a page,
    # and printing them with a space between them writes a line that may exist
    # nowhere. A coffee retailer's report printed `Location: / - "27 Monmouth
    # Street SE16 4RA"`: the street is one shop and the postal code belongs to
    # a different shop four miles away, and the join was presented to the
    # reader in quotation marks as something the site says.
    #
    # So the page has to show the join. Where it does, the words in between go
    # in too, because they are what the page prints between them; where it
    # does not, one half is quoted rather than a join of both - each half on
    # its own is a run the extractor read off this page.
    def _address_words(page):
        facts = page.get("contact_facts") or {}
        street = (facts.get("street_hint") or "").strip()
        postcode = (facts.get("postcode_hint") or "").strip()
        return (_address_line(page.get("body_text") or "", street, postcode)
                or street or postcode)

    with_an_address = [p for p in pages
                       if (p.get("contact_facts") or {}).get("has_address")
                       and not is_listing_page(p)]
    address_page = next((p for p in with_an_address if _address_words(p)),
                        next(iter(with_an_address), None))
    # A `PostalAddress` in JSON-LD, which the text scan above never reads and
    # no language gates. `contact_facts` looks at the visible text and the
    # `<address>` element, so a site whose only address is in its markup - a
    # common pattern where the footer address is rendered from structured data
    # - was told it publishes no address at all.
    declared_address_page = declared_address = None
    for page in pages:
        if is_listing_page(page):
            continue
        line = _declared_address(page, brand_name)
        if line:
            declared_address_page, declared_address = page, line
            break
    # Also an English pattern ("serving", "based in", "available across"), so
    # it only speaks where it can read the language.
    area_page, area_quote = (stated(SERVICE_AREA_RE, states_the_fact=_states_a_service_area)
                             if english else (None, None))
    local_signals = bool(by_type.get("location")) or any(
        "localbusiness" in {t.lower() for t in p.get("jsonld_types") or []} for p in pages)
    location_checked = (
        "the postal addresses the extractor read from the visible text and the `<address>` "
        "element of every crawled page that is not a listing page - a street with a house "
        "number, and a named street with no house number where a postal code sits within a "
        "few words of it. A number a caption names as a ticket, a version or an advisory "
        "identifier is not read as a house number. Before reporting none, the same text is "
        "read again for a comma-separated hierarchy of place names carrying a code, which "
        "is how an address is written where it is not a street and a number",
        "the `PostalAddress` node in the JSON-LD of every crawled page, excluding one that "
        "hangs off a node naming a different organisation",
        "sentences saying where the business is based or which places it serves, counted only "
        "where the sentence names somewhere and is not a statement of what the site declines "
        "to do",
    )
    if address_page:
        words = _address_words(address_page)
        found["location"] = note(
            address_page["url"], truncate(words, 160),
            "" if words else "(an `<address>` element with no street or postcode in it)")
    elif declared_address_page:
        found["location"] = note(declared_address_page["url"],
                                 truncate(declared_address, 160),
                                 "(declared in JSON-LD rather than written in the text)")
    elif area_page:
        found["service area"] = note(area_page["url"], area_quote)
    elif not kind.might_be(*_STATES_WHERE_IT_IS):
        # A program is not somewhere, and it has no customers to serve across
        # regions. Asked for its "location or service area" it can only answer
        # by inventing one, which is what "`<Brand>` serves customers across
        # `<regions>`" asked a command-line tool to do.
        reason = _not_expected_to_state(
            kind, "a postal address or a service area",
            "It has no premises to publish and no service area to name.")
        unverified.add("location")
        found["location"] = "not applicable - {}".format(reason)
        result.skip("core-fact-location-or-service-area", reason)
    elif not english:
        # The markup address is language-neutral and was looked for, but the
        # service-area half of this fact is an English pattern. Two halves of
        # one question, one of them unasked, is not grounds for saying the site
        # states neither.
        unverified.add("location")
        found["location"] = ("not checked - no postal address was found in the text or in the "
                             "JSON-LD, and the pattern for a stated service area is English "
                             "while this site is not")
    else:
        # The second reading, asked before the denial is published. See
        # `_an_address_this_audit_could_not_read`, and the rule it belongs to
        # in `audit_common.ABSENCE_DECIDED_BY_A_VOCABULARY`.
        unread = _an_address_this_audit_could_not_read(pages)
        if unread:
            unread_page, run = unread
            reason = _could_not_settle(
                "a postal address", unread_page, run,
                "is a comma-separated hierarchy of place names with a code in it - the shape "
                "an address takes where it is not written as a house number and a postal code",
                )
            unverified.add("location")
            found["location"] = reason
            result.skip("core-fact-location-or-service-area", reason)
        else:
            # "no postal address appears" is a statement about the site.
            # "was recognised" is a statement about the reading, and the
            # reading is the only thing this check has. The first wording was
            # printed beside a contact page showing an address in a shape the
            # street matcher cannot read.
            missing.append(("location or service area", "high" if local_signals else "medium",
                            "no postal address was recognised in the page text or in the page "
                            "markup, and no sentence stating where the business operates was "
                            "recognised in the page text",
                            location_checked))

    # 3. A way to make contact.
    #
    # The detail decides the page, not the other way round. Taking the first
    # page with any email on it and then printing whatever that page's first
    # email was reported `git@github.com` as a documentation site's contact
    # route, while an address further down the crawl went unread.
    #
    # The site's own chrome first, and every page's chrome before any page's
    # body. A partners page prints a partner's email in `<main>` and the
    # site's own in the footer; read page by page, whichever came first in the
    # crawl won, and a partner's address was recorded as the audited site's
    # contact route. A detail in the header, the footer or an `<address>`
    # element is the site's by construction - the template puts it on every
    # page - so it is the stronger reading and it is tried first.
    contact_page, contact_detail = None, ""
    contact_read_from = "the site's header, footer or address block"
    for page in pages:
        detail = _contact_detail(page.get("contact_facts") or {}, page, chrome_only=True)
        if detail:
            contact_page, contact_detail = page, detail
            break
    # Body copy second, and it still counts: a site that prints its email only
    # in a paragraph has printed its email, and demoting that to "no contact
    # method was found" would be a false finding of exactly the kind this
    # branch exists to avoid. What changes is what the report says about it.
    if contact_page is None:
        for page in pages:
            # A number in the body of a letter filed under its year is the
            # number printed that year, and on one conglomerate's site every
            # candidate was a figure out of a letter's earnings table. The
            # chrome reading above still counts on every page.
            if _a_dated_document(page):
                continue
            facts = page.get("contact_facts") or {}
            detail = _contact_detail(facts, page)
            if detail:
                contact_page, contact_detail = page, detail
                contact_read_from = ("the body of the page rather than its header, footer or "
                                     "address block, so it may belong to an organisation the "
                                     "page is about")
                break
            # `has_email` is also true for a `mailto:` link whose address is not in
            # the visible text, and that is still a contact route. It just has no
            # words to quote, which is what the URL-only note says. A page whose
            # only address was `git@github.com` is a different case: it captured an
            # address and the address is not one, so it is not a fallback either.
            # The raw lists, deliberately. This fallback is for a page that
            # declares a route it prints no words for, and a page whose only run of
            # digits was rejected as a book number has printed words - they are
            # just not a telephone number. Filtering here made the ISBN page look
            # like a page with a bare `tel:` link and handed the site a contact
            # route again through the back door.
            captured = list(facts.get("emails") or []) + list(facts.get("phones") or [])
            if contact_page is None and not captured and (
                    facts.get("has_email") or facts.get("has_phone")):
                contact_page = page
    form_page = next((p for p in pages if (p.get("contact_facts") or {}).get("has_contact_form")), None)
    # Two routes the text scan above cannot see. `ContactPoint`, `email` and
    # `telephone` in JSON-LD are the same fact declared in markup, and a
    # `mailto:` or `tel:` link is a working route whose address is never
    # written out. Read for visible text alone, this check told a site that
    # publishes both that no way to reach anyone exists on it.
    declared_contact_page = declared_contact = None
    for page in pages:
        detail = _declared_contact(page, brand_name)
        if detail:
            declared_contact_page, declared_contact = page, detail
            break
    # `click_to_chat_count` beside the other two. A `wa.me` link is how a very
    # large share of businesses in South and South-East Asia publish their
    # number, and for many of them it is the only place it appears; read for
    # `mailto:` and `tel:` alone, this branch called an Indonesian shoe brand
    # a site with no contact route while its homepage carried one.
    link_page = next((p for p in pages
                      if (p.get("links") or {}).get("mailto_count")
                      or (p.get("links") or {}).get("tel_count")
                      or (p.get("links") or {}).get("click_to_chat_count")), None)
    if contact_page:
        # The address or the number itself, not a sentence: a contact detail
        # is usually printed on its own line and has no sentence to sit in.
        # Which of the two readings found it travels with it, because they are
        # not the same claim.
        found["contact"] = note(contact_page["url"], truncate(contact_detail, 80),
                                "(from {})".format(contact_read_from)
                                if contact_detail else "")
        result.signal("contact_detail_source",
                      "chrome" if contact_detail and contact_read_from.startswith("the site's")
                      else "body" if contact_detail else "declared-only")
    elif declared_contact_page:
        found["contact"] = note(declared_contact_page["url"], truncate(declared_contact, 80),
                                "(declared in JSON-LD rather than written in the text)")
    elif link_page:
        found["contact"] = "{} (a {} link, with no address in the text)".format(
            link_page["url"],
            "mailto:, tel: or click-to-chat"
            if (link_page.get("links") or {}).get("click_to_chat_count")
            else "mailto: or tel:")
    elif form_page:
        found["contact"] = "{} (form only, no email or phone in text)".format(form_page["url"])
    elif address_page or declared_address_page:
        # A postal address is a contact route. A holding company's site prints
        # its street address on the homepage and tells readers to "write us at
        # the address shown above", and publishes no telephone number or email
        # of its own - so "the site never states its contact method" was false
        # about the one route it deliberately offers.
        where = address_page or declared_address_page
        found["contact"] = "{} (a postal address - the site gives no email, telephone or " \
                           "form)".format(where["url"])
    else:
        # Whether a page whose whole job is contact was reached at all. Saying
        # "no contact method was found" over a crawl that never got to the
        # contact page is a claim about the crawl, not about the site.
        reached_contact_page = bool(by_type.get("contact"))
        missing.append(("contact method", "medium",
                        "no email address, telephone number, contact form, mailto:, tel: or "
                        "click-to-chat link and no ContactPoint markup was found on any "
                        "crawled page" +
                        (", the contact page included" if reached_contact_page
                         else ", and no contact page was among the pages crawled"),
                        ("the email addresses, telephone numbers and contact forms the "
                         "extractor read from every crawled page - first from the header, "
                         "footer and `<address>` element, which speak for the site, then "
                         "from the whole page text. A number is read out of a link as well "
                         "as out of the text: a `tel:` href, and a click-to-chat link whose "
                         "URL slot is defined to hold a number in international format - "
                         "`wa.me/<number>`, a `phone` or `number` parameter, `sms:`. A chat "
                         "link that addresses a handle rather than a number states no number "
                         "and is not counted, and neither is a share button. A run of "
                         "digits scraped from the text is not counted as a telephone "
                         "number when its ISBN check digit computes, when it is thirteen "
                         "digits with no separator, or when the words in front of it name "
                         "it as another kind of code - that last test reads a closed "
                         "English and Japanese label vocabulary, so a code label written "
                         "in another language would not be recognised",
                         "`ContactPoint`, `email` and `telephone` in the JSON-LD of every "
                         "crawled page, excluding nodes that name, or hang off a node that "
                         "names, a different organisation",
                         "the mailto:, tel: and click-to-chat links counted on every "
                         "crawled page")))

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
    fact_page = fact_quote = team_page = team_quote = None
    if english:
        def founding(sentence, match):
            return (_states_a_founding_year(sentence, match)
                    and not _someone_elses_founding(sentence, match, brand_name))

        # A page filed under a year speaks for that year. "Since 1980" in a
        # 1985 shareholder letter is how long a holding had been held, and
        # every elapsed-time "founding" one conglomerate's report printed came
        # out of letters like it. So undated pages are read first, and a dated
        # one supplies a founding year only through a founding verb - "was
        # founded in 2014" in a press release is still the founding. See
        # `_a_dated_document`.
        fact_page, fact_quote = stated(
            FOUNDING_FACT_RE, among=[p for p in pages if not _a_dated_document(p)],
            states_the_fact=founding)
        if fact_page is None:
            fact_page, fact_quote = stated(
                FOUNDING_FACT_RE, among=[p for p in pages if _a_dated_document(p)],
                states_the_fact=lambda sentence, match: (
                    bool(_STATES_AN_ORIGIN_RE.search(match.group(0)))
                    and founding(sentence, match)))
        # Read from every page, not from the page a candidate was found on. A
        # shop's menu is on all of them and the check should recognise it as
        # furniture wherever it meets it, including on the one page whose own
        # markup happens to render it differently.
        labels = site_labels(pages)
        team_page, team_quote = stated(
            TEAM_FACT_RE,
            states_the_fact=lambda sentence, match: _states_a_named_person(
                sentence, match, brand_name, labels))
    # `foundingDate`, `founder`, `employee` and `member` in JSON-LD say the
    # same thing in a form that has no language, so this one runs whatever the
    # site is written in. It is what would have saved the French bakery whose
    # homepage reads "Creee en 1932 par Pierre Poilane" from being told no
    # founding year appears anywhere on it.
    origin_page = origin_fact = None
    for page in pages:
        if is_listing_page(page):
            continue
        declared = _declared_origin(page, brand_name)
        if declared:
            origin_page, origin_fact = page, declared
            break
    # Two facts, and only one of them may be reported absent.
    #
    # Read as a single "founding or team" fact, either half found silenced
    # both, and the sentence claimed both were missing whenever it fired: one
    # report said "no founding year and no named team or leadership detail
    # appears in the page text" about a page that names the person who
    # maintains the project twice. Half that sentence was true, and a site
    # owner who reads the false half discards the true one with it.
    #
    # So the halves are separated, and they are not symmetrical, because the
    # two absences are not equally knowable. A founding year is a four-digit
    # number beside a founding word: the check can enumerate what it is looking
    # for, so it can say it is not there. A named person is any capitalised run
    # in a sentence written any of a dozen ways, and this check has been shown
    # getting that wrong in both directions in the same batch - a privacy
    # notice's "individuals covered by this system include <Brand> employees"
    # recorded as a leadership fact, and "<Person>'s mail server ... <Person>
    # continues to maintain <Brand>" read as naming nobody. A check may report
    # the absence of something it can recognise. It may not report the absence
    # of something it cannot, so a named person found is reported and a named
    # person missing only lowers the severity of the year - a site that names
    # who runs it is not in the position of a site that names nothing at all.
    declared_origin_is_a_date = origin_fact.startswith("foundingDate") if origin_fact else False
    # Asked once, before the branch that would deny the fact, so the denial and
    # the second reading cannot disagree about one site.
    unattributed_year = (_a_founding_year_this_audit_could_not_attribute(pages, brand_name)
                         if english else None)
    if fact_page:
        found["founding facts"] = note(fact_page["url"], fact_quote)
    elif origin_page and declared_origin_is_a_date:
        found["founding facts"] = note(origin_page["url"], origin_fact,
                                       "(declared in JSON-LD rather than written in the text)")
    if team_page:
        found["team facts"] = note(team_page["url"], team_quote)
    elif origin_page and not declared_origin_is_a_date:
        found["team facts"] = note(origin_page["url"], origin_fact,
                                   "(declared in JSON-LD rather than written in the text)")

    if "founding facts" not in found and not kind.might_be(*_STATES_A_FOUNDING_YEAR):
        # A founding year is a fact about a body that was founded. A program, a
        # personal site and a research group have a first release, a person and
        # a grant respectively, and none of the three is what "was founded in
        # `<year>` in `<place>`" asks for. The site is still expected to say
        # what it is - that finding is `entity-definition`, and it is untouched.
        reason = _not_expected_to_state(
            kind, "a founding year in a place",
            "What it is, and who maintains it, are still asked for above.")
        unverified.add("founding facts")
        found["founding facts"] = "not applicable - {}".format(reason)
        result.skip("core-fact-founding-facts", reason)
    elif not english and not origin_page:
        unverified.add("founding facts")
        found["founding facts"] = ("not checked - the JSON-LD declares no founding date and no "
                                   "named person, and the patterns for a founding year and a "
                                   "named team are English while this site is not")
    elif english and "founding facts" not in found and unattributed_year:
        # The same pattern, read again without deciding whose sentence it is.
        # See `_a_founding_year_this_audit_could_not_attribute`.
        unattributed_page, unattributed = unattributed_year
        reason = _could_not_settle(
            "this site's own founding year", unattributed_page, unattributed,
            "names a year beside a word that puts something into existence")
        unverified.add("founding facts")
        found["founding facts"] = reason
        result.skip("core-fact-founding-facts", reason)
    elif english and "founding facts" not in found:
        missing.append((
            "founding facts",
            # A site that names who runs it has published the corroborating
            # detail this fact exists for, and is only missing the date.
            "low" if "team facts" in found else "medium",
            # A statement about the reading rather than about the site, for
            # the reason above the location branch: what this check has is a
            # pattern that did not fire, and "appears" claims more than that.
            "no sentence naming a founding year was recognised in the page text, and no "
            "`foundingDate` is declared in the page markup",
            ("sentences naming a founding year, counted only where the sentence is about this "
             "site rather than about an organisation it mentions, and where the year is the "
             "year the site began rather than the far end of a count or a comparison. Before "
             "reporting none, the same sentences are read again without deciding whose they "
             "are, so a founding sentence this audit could not attribute stops the claim",
             "`foundingDate` in the JSON-LD of every crawled page, on nodes that do not name a "
             "different organisation")))

    verified = sorted(k for k in found if k not in unverified)
    result.signal("core_facts_found", verified)
    result.signal("core_facts_not_checked", sorted(unverified))
    result.signal("core_facts_missing", [entry[0] for entry in missing])

    if not missing:
        # The number actually verified, counted in facts rather than in keys.
        # There are four facts and six ways of naming them - a location and a
        # service area are one fact, and so are a founding year and a named
        # team - so counting keys could print "5 of the 4 core facts".
        slots = {found_key: _CORE_FACT_SLOTS.get(found_key, found_key) for found_key in verified}
        verified_facts = len(set(slots.values()))
        detail = " ".join("{}: {}.".format(k.capitalize(), v)
                          for k, v in sorted(found.items()))
        if verified_facts == 4:
            head = "all 4 core facts are stated in plain text."
        elif verified:
            head = "{} of the 4 core facts {} stated in plain text.".format(
                verified_facts, "is" if verified_facts == 1 else "are")
        else:
            head = "none of the 4 core facts was verifiable here."
        result.skip("core-facts-present", "{} {}".format(head, detail))
        return

    # `checked` travels with each fact rather than being written once here.
    # Four facts, four different sets of sources: a price is a number and a
    # sentence, a location is text, markup and a service-area sentence, and
    # writing one sentence covering all four would name sources that were not
    # consulted for the fact it is attached to.
    # How much of the site this claim rests on. The title says "never", and a
    # site owner reads the title: over a sample of one page it has to say so.
    share = _sample_share(snapshot, len(pages))
    result.signal("pages_read_share", round(share, 3))
    for name, severity, why, checked in missing:
        title, severity = _scaled_to_the_sample(
            "The site never states its {} in plain text".format(name),
            "The {} this audit could read {} not state the {}".format(
                plural(len(pages), "page"), "does" if len(pages) == 1 else "do", name),
            severity, share)
        result.add(
            id_hint="core-fact-missing-{}".format(re.sub(r"[^a-z]+", "-", name.lower()).strip("-")),
            title=title,
            severity=severity, confidence="medium",
            # Say what was searched. "anywhere on the crawled pages" read as a
            # whole-site claim built from a sample the reader could not see.
            evidence="Across the {} of {} page(s) this audit read, {}. A crawl is a "
                     "sample: a page it did not reach may say it.".format(
                         len(pages), _urls_reached(snapshot, len(pages)), why),
            checked=checked,
            mechanism="B", root_cause="missing-core-fact",
            summary="State the {} explicitly, in a sentence, on the page where a visitor would "
                    "look for it.".format(name),
            how_to_fix=_core_fact_steps(name, brand_name, any_currency_figure, kind),
            effort="low", owner="content owner",
            rationale="An assistant answers with facts it can quote. A fact that "
                      "is implied, shown only in an image, or held only in a form nobody fills "
                      "in is a fact it will not state, so the brand loses that question to "
                      "whoever did write it down.",
            affected_pages=sorted({p["url"] for p in pages if p["page_type"] in
                                   ("home", "about", "contact", "pricing")})[:5],
        )


# Which of the four facts each name in `found` belongs to. A location and a
# service area answer "where"; a founding year and a named team answer "who".
_CORE_FACT_SLOTS = {
    "pricing": "pricing",
    "location": "where", "service area": "where",
    "contact": "contact",
    "founding facts": "who", "team facts": "who",
}


def _core_fact_steps(name, brand_name, any_currency_figure=True, kind=None):
    """The paste-ready steps for one missing fact, worded for what the site is.

    This is where the damage lands. A finding a reader disagrees with costs one
    finding; a fix that tells them to write a sentence that is not true about
    them costs the report. `kind` is a `SiteKind` or None, and only
    `is_certainly` is read here - a wording change is an assertion about the
    site, so it is made on a determination this would defend and never on a
    guess. The gate above has already removed the facts a kind is not expected
    to state at all, so what is left is a fact that is genuinely missing and a
    choice of words for asking for it.
    """
    brand = brand_name or "the brand"

    def certainly(*kinds):
        return kind is not None and kind.is_certainly(*kinds)

    def because(what):
        """Why the report tailored itself, in the report, where it can be checked."""
        return "This wording is for {}: {}. If that is wrong, the company wording " \
               "applies instead.".format(what, kind.why())
    if name == "pricing":
        # "Contact sales for a quote" presumes a sales team and a price. A free
        # newsletter, a members club and a public library all reached this
        # advice through a `/subscribe`, `/membership` or `/plans` URL, and a
        # reader who charges nothing cannot act on any of it. Without a
        # currency figure anywhere on the site, the honest instruction is to
        # write down whichever is true.
        if not any_currency_figure:
            return [
                'If nothing here costs anything, write that sentence: "{} is free to use." '
                "That is the answer to the question, and it is quotable.".format(brand),
                "If there is a charge, publish the actual figures on a pricing page, even as "
                "a starting-from number.",
                "Either way, say it in plain text rather than leaving it to be inferred.",
            ]
        return [
            "Publish the actual figures on a pricing page, even as a starting-from number.",
            'If you genuinely cannot publish prices, write the sentence explicitly: "{} does '
            'not publish prices; contact sales for a quote, typically returned within N days." '
            "An explicit statement is quotable; silence is not.".format(brand),
            "Add the same figure to Product/Offer structured data so it is unambiguous.",
        ]
    if name.startswith("location"):
        # "Serves customers across `<regions>`" is a sentence a public body and
        # a charity cannot write: one has a jurisdiction and the other has the
        # places it works in, and neither calls the people it exists for
        # customers. The address half of the advice is the same for all three,
        # so only the second step moves.
        if certainly(PUBLIC_BODY):
            return [
                "Put the full postal address of the office in text on the contact page, "
                "inside an `<address>` element.",
                'If there is no public counter, state the area covered instead: '
                '"{} covers `<the area or population it serves>`."'.format(brand),
                "Repeat the address in the footer of every page and in Organization "
                "structured data.",
                because("a public body rather than a company"),
            ]
        if certainly(ORGANISATION):
            return [
                "Put the full postal address in text on the contact page, inside an "
                "`<address>` element.",
                'If there are no public premises, state where the work happens instead: '
                '"{} works across `<regions>`."'.format(brand),
                "Repeat the address in the footer of every page and in Organization "
                "structured data.",
                because("an organisation rather than a shop with customers"),
            ]
        # The fall-through: a shop, a local business, and every site the
        # classifier could not place. The two branches above already moved the
        # sentence for a public body and an organisation, so what was left here
        # was the shop wording reaching undetermined sites - a library the
        # classifier declined to place was told to say it serves customers.
        # "Serves `<regions>`" is true of a shop as well, so one neutral
        # sentence replaces the branch rather than a fourth branch being added.
        return [
            "Put the full postal address in text on the contact page, inside an `<address>` "
            "element.",
            'If there are no public premises, state the area served instead: '
            '"{} serves `<regions>`."'.format(brand),
            "Repeat the address in the footer of every page and in Organization structured data.",
        ]
    if name == "contact method":
        return [
            "Put an email address and a telephone number in text, not only behind a form.",
            "Use mailto: and tel: links so both a person and a machine can act on them.",
            "State response times: an answer time is itself a quotable fact.",
        ]
    # The year, and only the year. This used to end "Name the leadership team
    # with their roles", which is advice attached to an absence this check no
    # longer claims - a page can name its maintainer in a possessive or an
    # attribution that no pattern here recognises, and telling its owner to
    # write what they have written is how a report loses a reader.
    #
    # A public body is not founded in a place by anybody: it is established, by
    # a law or by the body above it, and that is the fact a reader of its site
    # wants. Same finding, same severity, a sentence its owner can actually
    # write.
    if certainly(PUBLIC_BODY):
        return [
            'Add a short paragraph on the about page: "{} was established in `<year>` '
            'under `<the law or the body that created it>`."'.format(brand),
            "Put the same year in the `foundingDate` property of your Organization markup, "
            "so a machine reads the same answer a person does.",
            "The year and the authority behind it are what distinguish you from another "
            "body with a similar name.",
            because("a public body rather than a company"),
        ]
    return [
        'Add a short paragraph on the about page: "{} was founded in `<year>` in '
        '`<place>`."'.format(brand),
        "Put the same year in the `foundingDate` property of your Organization markup, so a "
        "machine reads the same answer a person does.",
        "A year is what distinguishes you from another organisation with a similar name.",
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
        # The count came from `authoritative_variants`, which is deduplicated
        # names, while the list came from `authoritative_sources`, which is
        # sources - so it read "declares its name in 1 authoritative place
        # (jsonld:Organization, og:site_name, og:site_name-off-home)", a number
        # that could never match its own list. One name, in however many
        # places, is the thing being said.
        sources = brand.get("authoritative_sources") or []
        result.skip("brand-naming-consistency",
                    "the site declares one name, in {} ({}), so there is nothing to disagree "
                    "with. Names guessed from page titles were deliberately not "
                    "compared.".format(plural(len(sources), "authoritative place"),
                                       ", ".join(sources) or "none"))
        return

    declared_on = brand.get("declared_on") or {}
    home_urls = {p.get("url") for p in pages if p.get("page_type") == "home"}
    # `snapshot["pages"]`, not the content-typed subset: a name can be declared
    # on a page this check was not handed, and the language of that page is
    # what decides whether the name is a translation.
    lang_by_url = {p.get("url"): primary_subtag(p.get("lang"))
                   for p in (snapshot.get("pages") or pages) if p.get("url")}

    sections = _names_confined_to_one_section(variants, declared_on, home_urls)
    compared = [v for v in variants if v not in sections]
    editions = _names_by_language(compared, declared_on, lang_by_url)

    # One comparison per language. A name declared on a page that declares
    # `lang="en"` and a name declared on a page that declares `lang="ja"` are
    # one name written twice, once in each language - which is what a bilingual
    # site is for - and comparing across the two told a Japanese university
    # that three distinct names are asserted as its identity, then advised it
    # to pick one written form and use it character for character. Following
    # that would delete its English name from its English pages.
    found = None
    for code in sorted(editions):
        names = editions[code]
        if len(names) < 2:
            continue
        spelling_conflicts, undeclared = _conflicting_names(names, brand)
        if spelling_conflicts or len(undeclared) >= 2:
            found = (code, names, spelling_conflicts, undeclared)
            break

    if found is None:
        result.skip("brand-naming-consistency", _naming_agreement_reason(
            variants, compared, editions, sections))
        return

    language, compared, spelling_conflicts, undeclared = found

    detail = []
    if spelling_conflicts:
        detail.append("the same name is written as {}".format(
            " / ".join('"{}"'.format(x) for group in spelling_conflicts for x in sorted(group)[:3])))
    if len(undeclared) >= 2:
        # The fullest spelling in each group, so the report quotes names the
        # site actually declared rather than the folded keys it compared.
        names = sorted(max(sorted(g["variants"]), key=len) for g in undeclared)
        detail.append("{} distinct names are asserted as the site's identity: {}".format(
            len(undeclared), ", ".join('"{}"'.format(v) for v in names[:4])))
    if language:
        # Which comparison this was, not a claim about where each name appears:
        # a name declared on pages that state no language is compared in every
        # language, because nothing says it belongs to one.
        detail.append("compared within {}, so a name declared on a page in another language "
                      "was treated as a translation and not compared against "
                      "these".format(language.upper()))

    # The pages that actually declared a name. A finding that lists none is
    # scored as affecting the whole site, and this one was: a disagreement
    # between two names declared on a single store page out of sixty scored
    # higher than a confirmed crawler block on the same report, because
    # "no pages listed" is meant to mean robots.txt, not "we did not look".
    pages_declaring = sorted({url for name in compared
                              for url in declared_on.get(name, []) if url})
    pages_seen = brand.get("pages_seen") or 0
    scope = _naming_scope(pages_declaring, pages_seen)

    result.add(
        id_hint="brand-name-written-inconsistently",
        affected_pages=pages_declaring,
        title="The brand name is written more than one way",
        severity="medium", confidence="medium",
        # Where each name came from, not a fixed sentence naming two sources.
        # A report ranked this finding first on a site whose JSON-LD says
        # "Kestrel" on all sixteen pages that carry it and whose og:site_name is
        # absent on all sixty - and quoted a variant "Fly" that neither named
        # source produced. A finding about unverifiable claims may not make
        # one, so the sources are read from the snapshot and each name is shown
        # with the pages that declared it.
        evidence="Sources read: {}. Result: {}. The primary form was taken to be \"{}\" "
                 "(from {}). {}{}{}".format(
                     ", ".join(brand.get("authoritative_sources") or []) or "none recorded",
                     "; ".join(detail), brand.get("name"), brand.get("source"),
                     _declaration_sources(compared, declared_on), scope,
                     _set_aside_note(sections)),
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


def _first_path_segment(url):
    """`/help/teamcity/build-log.html` -> `help`. The site root gives ''."""
    parts = [p for p in (urlsplit(url).path or "/").split("/") if p]
    return parts[0].lower() if parts else ""


def _names_confined_to_one_section(variants, declared_on, home_urls=()):
    """Names declared only under one path section, mapped to that section.

    A documentation subsite and a campaign microsite each set their own
    `og:site_name`, and that string is the name of the section, not a second
    spelling of the brand's. A software vendor was told two distinct names are
    asserted as its identity - its own, and "<product> On-Premises Help" - and
    advised to pick one and use it character for character, which would rename
    its documentation. A Japanese university got the same advice about the
    microsite for its 150th anniversary, declared only under one path prefix.
    The two names in each pair share no token, which is the tell.

    The brand's own name is the one the homepage declares, or the one declared
    across more than one section. Nothing is set aside unless something is
    left: where every declared name sits in its own section there is no
    site-wide name to measure the others against, and dropping them all would
    turn a real disagreement into silence.
    """
    scoped = {}
    for name in variants:
        urls = [u for u in (declared_on.get(name) or []) if u]
        if not urls or any(u in (home_urls or ()) for u in urls):
            continue
        section = _one_section_holding(urls)
        if section:
            scoped[name] = section
    return {} if len(scoped) >= len(variants) else scoped


def _one_section_holding(urls):
    """The section every one of these URLs sits under, or "" if there is none.

    A section is a directory. `/about.html` is a page, and reading its first
    path segment as a section name meant any name declared on exactly one
    top-level page was set aside as that page's own - so a site whose
    `/about`, `/pricing` and `/faq` each spell the brand differently was told
    its one declared name is consistent, having compared one name with
    itself. That is the whole defect this check exists to catch.

    What separates the two is depth: a documentation subsite or a campaign
    microsite has pages beneath its prefix, or is addressed at the prefix
    itself with a trailing slash. A single `.html` file has neither.
    """
    sections = {_first_path_segment(u) for u in urls}
    if len(sections) != 1 or "" in sections:
        return ""
    for url in urls:
        path = urlsplit(url).path or "/"
        if len([p for p in path.split("/") if p]) >= 2 or path.endswith("/"):
            return sections.pop()
    return ""


def _names_by_language(variants, declared_on, lang_by_url):
    """The names to compare, grouped by the language they were declared in.

    A name declared on a page that declares `lang="ja"` and a name declared on
    a page that declares `lang="en"` are one name written twice, once in each
    language. A name on pages that declare no language at all belongs to every
    group, because there is nothing to say it does not.
    """
    by_name = {}
    for name in variants:
        codes = {lang_by_url.get(url) or "" for url in (declared_on.get(name) or [])}
        codes.discard("")
        by_name[name] = codes
    spoken = sorted({code for codes in by_name.values() for code in codes})
    if not spoken:
        return {"": list(variants)}
    return {code: [n for n in variants if not by_name[n] or code in by_name[n]]
            for code in spoken}


def _conflicting_names(variants, brand):
    """Which of these declared names disagree: (spelling conflicts, groups).

    Folded through `name_forms` first, so a legal name and a trading name are
    one name. "Harrowgate Law LLP" in `Organization.name` with "Harrowgate
    Law" in `og:site_name` is correct and near-universal practice, and
    comparing the raw strings made it "the brand name is written more than
    one way" - a medium finding whose fix asks the owner to pick one, on a
    site doing the right thing.

    `comparison_key`, not `[^a-z0-9]+`. That expression is the empty string
    for every name written in Hangul, Devanagari, Arabic, Greek or Cyrillic,
    the `len(k) >= 3` filter below then emptied `keys`, and the loop skipped
    the variant - so a museum declaring two genuinely different Korean names
    was told its name is written consistently, having compared nothing.
    """
    groups = []
    for variant in variants:
        keys = {comparison_key(form) for form in (name_forms(variant) or [variant])}
        keys = {k for k in keys if k}
        # Three letters is a preference, not a requirement: dropping every
        # short key outright loses two-letter names entirely.
        keys = {k for k in keys if len(k) >= 3} or keys
        if not keys:
            continue
        # Symmetric, and it merges every group the new variant reaches rather
        # than the first. The old test asked only whether an existing group key
        # appeared in the new variant's key set, so "Harrowgate Law LLP" seen
        # first left "harrowgatelawllp" as the group key, "harrowgatelaw" did
        # not contain it, and the two names landed in separate groups - while
        # the same pair in the opposite order landed in one. Which of the two
        # findings a site got was decided by the order the crawler happened to
        # read its pages in.
        hits = [g for g in groups if g["keys"] & keys]
        if not hits:
            groups.append({"variants": {variant}, "keys": set(keys)})
            continue
        merged = hits[0]
        for other in hits[1:]:
            merged["variants"] |= other["variants"]
            merged["keys"] |= other["keys"]
            groups.remove(other)
        merged["variants"].add(variant)
        merged["keys"] |= keys

    # Two names in one group are the same name: `name_forms` derived one from
    # the other. What separates a misspelling from a legal suffix is what is
    # left after the punctuation and spacing come out. "AcmeTools" and "Acme
    # Brains" reduce to one string, so one of them is written wrong.
    # "Harrowgate Law LLP" and "Harrowgate Law" do not, and neither is wrong.
    spelling_conflicts = []
    for group in groups:
        by_key = {}
        for variant in group["variants"]:
            by_key.setdefault(comparison_key(variant), set()).add(variant)
        spelling_conflicts.extend(
            spellings for spellings in by_key.values() if len(spellings) > 1)

    declared_alternates = {comparison_key(a) for a in brand.get("alternate_names") or []}
    declared_alternates.discard("")
    # A second name already declared as `alternateName` is not a contradiction:
    # the site has explicitly said the two refer to one entity.
    undeclared = [g for g in groups if not (g["keys"] & declared_alternates)]
    return spelling_conflicts, undeclared


def _set_aside_note(sections):
    """The section names this check refused to read as spellings of the brand."""
    if not sections:
        return ""
    return (" Not compared: {}, each declared only under /{}/ and so read as the name of "
            "that section rather than of the brand.".format(
                ", ".join('"{}"'.format(name) for name in sorted(sections)[:3]),
                "/, /".join(sorted({section for section in sections.values()})[:3])))


def _naming_agreement_reason(variants, compared, editions, sections):
    """Why the declared names were taken to agree, including what was set aside.

    Every filter says so out loud. This check used to print "the N declared
    names fold to one name" after silently comparing a documentation subsite's
    name against a brand's, and after comparing a Japanese name against its own
    English translation; a reader had no way to tell which names the sentence
    was about.
    """
    count = len(compared) or len(variants)
    reason = ["the {} {} to one name within each language {} declared in, or the alternative "
              "forms are declared in Organization `alternateName`".format(
                  plural(count, "declared name"), "folds" if count == 1 else "fold",
                  "it is" if count == 1 else "they are")]
    languages = sorted(code for code in editions if code)
    if len(languages) > 1:
        reason.append("names declared on pages in {} were compared separately, because a name "
                      "on a page declaring one language and a name on a page declaring another "
                      "are a translation rather than a second spelling".format(
                          " and ".join(code.upper() for code in languages[:4])))
    if sections:
        reason.append("{} was set aside as the name of one section of the site: it is declared "
                      "only under /{}/ and never on the homepage".format(
                          ", ".join('"{}"'.format(name) for name in sorted(sections)[:3]),
                          "/, /".join(sorted({s for s in sections.values()})[:3])))
    return ". ".join(reason)


def _declaration_sources(variants, declared_on):
    """Where each declared name was actually seen, name by name."""
    parts = []
    for variant in sorted(variants)[:4]:
        urls = [u for u in (declared_on.get(variant) or []) if u]
        if urls:
            parts.append('"{}" on {}{}'.format(
                variant, urls[0], " and {} more".format(len(urls) - 1) if len(urls) > 1 else ""))
        else:
            parts.append('"{}" on no page this crawl recorded'.format(variant))
    return "Declared: {}.".format("; ".join(parts)) if parts else ""


def _naming_scope(pages_declaring, pages_seen):
    """How much of the crawl declared a name at all.

    Three sentences, because one sentence was printed for three situations it
    is false in. "Both forms were found on 1 of the 3 pages crawled" was
    printed for two names declared on the same single page, "Both" was printed
    for however many names there were, and "the rest declare no name at all"
    was printed after "28 of the 28 pages crawled", where there is no rest.
    """
    if not pages_declaring or not pages_seen:
        return ""
    # Each shape states its own scale, in the "N of the M" wording the report
    # composer recognises, so it does not append a second sentence saying it
    # again in different words.
    if len(pages_declaring) == 1:
        return (" Every form quoted here is declared on the same page, 1 of the {} crawled: {}."
                .format(pages_seen, pages_declaring[0]))
    if len(pages_declaring) >= pages_seen:
        return (" {} of the {} pages crawled declare a name, so this disagreement runs across "
                "the whole site.".format(pages_seen, pages_seen))
    return (" Names were declared on {} of the {} pages crawled; the rest declare none, so "
            "this is a disagreement between the pages that do."
            .format(len(pages_declaring), pages_seen))


def _holds_prose(page):
    """Does this page contain sentences, before anything is measured about them?

    A sentence-length average is only a fact about writing if the text was
    written in sentences. Two pages were reported as "written in sentences too
    long to quote" on the strength of averages of 92.5 and 85.1 words: one is
    a credits page listing several thousand contributor names, the other an
    alphabetical catalogue of authors and titles. Neither contains a sentence.
    The extractor joins block elements with a space, so a list with no full
    stops in it arrives as one enormous "sentence", and the average measures
    the length of the list.

    `is_listing_page` catches the pages whose job is to point at other pages,
    and it does not catch either of these: a credits page is a listing by
    nobody's URL, page type or headings.

    So the load-bearing test is the second one, `list_text_share`: the share
    of the page's own words sitting inside `li`, `td`, `th`, `dt` and `dd`.
    Both pages are built that way, and it says directly what the other tests
    can only infer - this text is a list. It is measured in the extractor, on
    the same region the sentence statistics come from, because the consumer
    cannot compute it: `paragraphs` is emptied by the crawl's site-wide
    boilerplate strip, so a page of pure prose can report none of them.

    The average is a backstop for the one shape the share cannot see: a list
    written as one paragraph with line breaks in it, which the markup calls
    prose. It sits at 80 words between full stops, against 92.5 and 85.1 on
    the two pages that produced the false finding and against 60 on the
    longest-winded real writing this audit has measured. Legal and academic
    prose runs to 40 words a sentence and reporting that is the whole point of
    the check, so this line falls where the text has stopped having sentence
    boundaries at all rather than where its sentences got long.
    """
    if is_listing_page(page):
        return False
    readability = page.get("readability") or {}
    if readability.get("list_text_share", 0.0) > PROSE_MAX_LIST_TEXT_SHARE:
        return False
    return readability.get("avg_sentence_words", 0) <= PROSE_MAX_AVG_SENTENCE_WORDS


def _prose_readability(page):
    """The page's sentence statistics, counted without its runs of interface labels.

    The extractor's numbers are kept exactly as they are unless a run was set
    aside here, so a page with nothing to set aside reports what it always
    reported. Where one was, the four numbers that come from sentence lengths
    are recounted from the same text with the same splitter, and the count of
    runs set aside travels with them. See `_reads_as_interface_labels`.
    """
    readability = page.get("readability") or {}
    text = page.get("body_text") or ""
    if not text:
        return readability
    found, _ = prose_sentences(text)
    kept = [s for s in found if not _reads_as_interface_labels(s)]
    if len(kept) == len(found):
        return readability
    lengths = [extractor_word_count(s) for s in kept]
    long_ones = [n for n in lengths if n > LONG_SENTENCE_WORDS]
    recounted = dict(readability)
    recounted.update({
        "sentence_count": len(kept),
        "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_sentence_count": len(long_ones),
        "long_sentence_share": round(len(long_ones) / len(lengths), 3) if lengths else 0.0,
        "interface_label_runs_set_aside": len(found) - len(kept),
    })
    return recounted


def _check_long_sentences(result, pages):
    result.check("long-sentences")
    # A cart drawer, a variant picker and a spec table are not sentences, and
    # the extractor's count keeps the ones carrying a stray comma.
    pages = [dict(p, readability=_prose_readability(p)) for p in pages]
    prose_pages = [p for p in pages if _holds_prose(p)]
    measurable = [p for p in prose_pages
                  if (p.get("readability") or {}).get("sentence_count", 0) >= 8]
    if not measurable:
        # Which of the two reasons it was, because they call for different
        # things: a site with no long-form pages is not a site whose pages are
        # lists of names.
        skipped = len(pages) - len(prose_pages)
        result.skip("long-sentences",
                    "no crawled page has enough prose (8+ sentences) to measure sentence "
                    "length{}".format(
                        "; {} page(s) were set aside first as lists rather than prose - a "
                        "page that lists other pages, one with over {}% of its words inside "
                        "list items or table cells, or one averaging over {} words between "
                        "full stops".format(
                            skipped, int(PROSE_MAX_LIST_TEXT_SHARE * 100),
                            PROSE_MAX_AVG_SENTENCE_WORDS)
                        if skipped else ""))
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
