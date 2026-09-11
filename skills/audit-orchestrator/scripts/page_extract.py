"""Turn one fetched HTML response into the page record stored in snapshot.json.

Extraction happens exactly once, here, during the single shared crawl. The six
sub-skills then read fields off the record instead of each re-parsing the same
HTML - that is what makes the marketplace one crawl rather than six.
"""

from __future__ import annotations

import json
import re
from urllib.parse import parse_qs, urljoin, urlparse

from audit_common import (
    BUDDHIST_ERA_DATE_RE, CODE_LABEL_WINDOW, CURRENCY_SYMBOLS,
    DISTINCT_PRICE_CEILING, JAPANESE_ERA_DATE_RE, THAI_ERA_YEAR_RE,
    content_soup, counts_as_off_site_profile,
    detect_challenge, detect_page_type, dominant_script,
    non_gregorian_dates, reads_in_a_buddhist_era_calendar,
    the_address_names_one_item, the_item_grouping_word,
    find_prices, host_name_forms, is_faceted_listing, is_infrastructure_url,
    is_listing_page, is_search_result_page, jsonld_type_names, listing_key,
    main_text_and_region, make_soup,
    normalise_url, not_a_telephone_number, price_value,
    profile_is_opaque, profile_names_brand,
    same_site, sentences, speaks_about_itself, strip_www,
    tidy_spacing, title_segments, truncate, visible_soup, visible_text,
    without_other_peoples_words, word_count,
)

# Root containers frameworks mount into. An empty one means the delivered HTML
# is a shell and the content only exists after JavaScript runs.
SPA_ROOT_SELECTORS = (
    "#root", "#app", "#__next", "#__nuxt", "#gatsby-focus-wrapper",
    "[data-reactroot]", "[data-server-rendered]", "[ng-app]", "#ember-app",
    "#svelte", "#q-app",
)

# Names a page uses for the payload its script hydrates from. A page carrying
# one is telling us its text arrives after the script runs.
#
# The list was seven names, all of them from JavaScript frameworks a developer
# chooses. That misses the shape this marketplace meets most: a hosted site
# builder, whose payload names are the builder's and never the framework's. A
# candle shop's homepage held `viewerModel` fifteen times, `wixBiSession` nine
# times, `warmupData` and `siteAssets`, and 558,468 characters of inline script
# - 42.9% of a 1,300,857-character document - and this list recognised none of
# them. The page was reported as thin HTML at high severity, ranked "do first",
# and its owner was told to write two or three sentences of body copy for a
# document whose copy arrives when the script runs. The report named the
# platform in its own fix steps two sections later.
#
# Every name here is a camel-cased or double-underscored identifier that
# appears in a script, not a word that appears in prose, which is what makes a
# substring test over the HTML safe. The cost of a wrong match is small in one
# direction only: a page carrying a state blob is set aside from the thin-page
# finding and needs a second signal before a shell finding fires, so a false
# match makes this audit quieter rather than louder.
STATE_BLOB_PATTERNS = (
    "__NEXT_DATA__", "__NUXT__", "__NUXT_DATA__", "__INITIAL_STATE__",
    "__APOLLO_STATE__", "__REDUX_STATE__", "__remixContext", "self.__next_f",
    "__sveltekit_", "__staticRouterHydrationData",
    # Hosted site builders, whose payload is the builder's own and carries no
    # framework name at all.
    "viewerModel", "wixBiSession", "warmupData", "siteAssets",
    "__PRELOADED_STATE__", "__SERVER_DATA__",
)

COOKIE_BANNER_HINTS = (
    "cookie-banner", "cookie-consent", "cookiebanner", "cookieconsent",
    "gdpr-banner", "consent-manager", "onetrust", "cookiebot", "cky-consent",
    "truste-banner", "privacy-banner", "cc-banner", "cmp-container",
)

MODAL_HINTS = (
    "newsletter-modal", "popup-overlay", "exit-intent", "interstitial",
    "subscribe-modal", "signup-modal", "welcome-mat",
)

VIDEO_HOSTS = ("youtube.com", "youtu.be", "vimeo.com", "wistia", "loom.com", "brightcove")

# The extractor recorded video and never looked at audio, so a podcast episode
# page - the clearest case there is of content a machine cannot read without a
# transcript - was invisible to the transcript check.
AUDIO_HOSTS = (
    "soundcloud.com", "spotify.com/embed", "podbean.com", "buzzsprout.com",
    "libsyn.com", "megaphone.fm", "simplecast.com", "acast.com", "anchor.fm",
    "captivate.fm", "transistor.fm", "art19.com", "omnystudio.com", "audioboom.com",
)

TRANSCRIPT_HINTS = ("transcript", "captions", "subtitle", "read the transcript", "full text")

# What a report is allowed to call a platform, and which hosts hand out
# accounts at the root of their path.
#
# This is no longer the test for whether a link is a profile. It was, and being
# a closed list it could only ever be wrong in one direction: a footwear maker
# linked three brand-named storefronts on the three largest shopping platforms
# in its own country from its own home page, all three sat in this crawl's own
# `links.external`, and the report's headline finding read "No off-site profile
# is linked from the crawled pages or declared in sameAs" at high severity. The
# list held about thirty Western hosts and not one Asian one. It had already
# been "completed" twice before that, which is the argument against completing
# it a third time and calling the job done: `_account_shape` decides what a
# profile is, from the shape of the path, on any host on earth.
#
# Two jobs are left, and both of them need names rather than rules:
#
#   a name to print      "Instagram" reads better than "instagram.com" in a
#                        finding, and three readers downstream key off these
#                        exact strings - the corroboration check's list of
#                        authoritative platforms, its list of platforms whose
#                        404 means 404, and the one platform whose URL states
#                        its own subject (an encyclopedia article title, which
#                        is why a 15th-century printer's article must keep
#                        being recognised as an encyclopedia article).
#   a root namespace     on these hosts the first path segment is somebody's
#                        account and nothing else, so `platform.example/handle`
#                        with no role word in it is still a profile. On an
#                        arbitrary host it is a page, so it is not.
#
# Hosts are added here to be *named*, never to be *counted*: an account on a
# host absent from this dict counts exactly as much, as long as its path says
# it is an account.
SOCIAL_PLATFORMS = {
    "linkedin.com": "LinkedIn",
    "wikipedia.org": "Wikipedia",
    "wikidata.org": "Wikidata",
    "crunchbase.com": "Crunchbase",
    "github.com": "GitHub",
    "twitter.com": "X",
    "x.com": "X",
    "instagram.com": "Instagram",
    "facebook.com": "Facebook",
    "youtube.com": "YouTube",
    "tiktok.com": "TikTok",
    "g.page": "Google Business",
    "goo.gl/maps": "Google Business",
    # Two entries here named neither a host nor a host suffix - one was a
    # hostname with its top-level domain missing, the other a bare word - and
    # once the substring test that used to back them was replaced by a host
    # test, neither could ever match again. They were dead lines that read as
    # live ones. A country's own address on a review site still counts; it is
    # keyed by its host rather than by the name printed here.
    "maps.google.com": "Google Business",
    "yelp.com": "Yelp",
    "glassdoor.com": "Glassdoor",
    "trustpilot.com": "Trustpilot",
    "medium.com": "Medium",
    "pinterest.com": "Pinterest",
    "threads.net": "Threads",
    "bsky.app": "Bluesky",
    # Platforms found in real footers that this list could not see.
    # A browser-compatibility database publishes its maintainer's Mastodon
    # account and a Patreon as its only off-site presence besides GitHub, and
    # the report said it links one profile. Developer tools, open-source
    # projects and creators routinely put their whole off-site footprint on
    # these and nowhere else.
    "patreon.com": "Patreon",
    "reddit.com": "Reddit",
    "stackoverflow.com": "Stack Overflow",
    "gitlab.com": "GitLab",
    "substack.com": "Substack",
    "discord.gg": "Discord",
    "discord.com": "Discord",
    "opencollective.com": "Open Collective",
    "twitch.tv": "Twitch",
    "bcorporation.net": "B Corp",
    # The half of the world the list had no entry for at all. Not one of the
    # thirty hosts above is used by most people in Asia, so a brand whose whole
    # off-site presence is a messaging channel, a portal blog and two
    # marketplace storefronts was read as having none - and the finding that
    # followed told it to go and open accounts it already had, on platforms its
    # customers do not use. `_account_shape` is what actually counts these now;
    # they are here so the report can call them by name and so a storefront
    # addressed as `<platform>/<brand>` at the root is read as the account it
    # is. The list is still not complete and cannot be, which is the point.
    "line.me": "LINE",
    "t.me": "Telegram",
    "telegram.me": "Telegram",
    "whatsapp.com": "WhatsApp",
    "naver.com": "Naver",
    "kakao.com": "Kakao",
    "weibo.com": "Weibo",
    "xiaohongshu.com": "Xiaohongshu",
    "bilibili.com": "Bilibili",
    "douyin.com": "Douyin",
    "rakuten.co.jp": "Rakuten",
    "rakuten.com": "Rakuten",
    "zozo.jp": "ZOZOTOWN",
    "shopping.yahoo.co.jp": "Yahoo Shopping",
    "shopee.com": "Shopee",
    "lazada.com": "Lazada",
    "vk.com": "VK",
}

# Ordinal suffixes are allowed on the day: "3rd November 2025" is how a great
# many real sites write a date, and requiring a bare digit reported those pages
# as carrying no date at all.
DATE_TEXT_RE = re.compile(
    r"\b(?:(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+"
    r"\d{1,2}(?:st|nd|rd|th)?,?\s+(?:19|20)\d{2}"
    r"|\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"\s+(?:19|20)\d{2}"
    r"|(?:19|20)\d{2}-\d{2}-\d{2}"
    r"|\d{1,2}/\d{1,2}/(?:19|20)\d{2}"
    # Year first with a full stop or a slash, and day first with full stops.
    # A national library prints `2026.09.10` beside every notice and a Nordic
    # site prints `10.09.2026`; neither shape was read, so both were reported
    # as pages carrying no date. The month and day are bounded so a version
    # number such as `2026.1.300` is not a date.
    r"|(?:19|20)\d{2}[./](?:0?[1-9]|1[0-2])[./](?:0?[1-9]|[12]\d|3[01])(?![./]?\d)"
    r"|(?:0?[1-9]|[12]\d|3[01])\.(?:0?[1-9]|1[0-2])\.(?:19|20)\d{2})\b"
    # Year-month-day with the units named, which is how Japanese and Chinese
    # pages write a date. A national postal group's article showed
    # "2015年4月6日" directly under its headline and the report said the page
    # carried no date at all - a finding about our own patterns, printed as a
    # finding about the site. Korean uses the same shape with 년 월 일.
    r"|(?:19|20)\d{2}\s?[\u5e74\ub144]\s?\d{1,2}\s?[\u6708\uc6d4]\s?\d{1,2}\s?[\u65e5\uc77c]"
    # Month and year with no day. Every press page, bibliography, citation
    # list and changelog on the web writes dates this way - "December 1998",
    # "September 2000", "January 22-29, 2001" - and requiring a day meant a
    # project's press page, whose own snapshot text this crawl had captured
    # with six such dates in it, was reported as carrying "nothing in the
    # visible text, and no usable datePublished, dateModified or <time> value
    # in the markup". A month and a year is a date a reader and a machine can
    # both use; a bare year on its own is not, and is deliberately still not
    # matched here, because "in 2019" is usually history rather than a
    # statement about when the page was written.
    r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?"
    r"(?:\s+\d{1,2}(?:st|nd|rd|th)?(?:\s?[-\u2013]\s?\d{1,2})?,?)?"
    r"\s+(?:of\s+)?(?:19|20)\d{2}\b"
    r"|\b(?:19|20)\d{2}\s?[\u5e74\ub144]\s?\d{1,2}\s?[\u6708\uc6d4]"
    # The calendars that are not the Gregorian one. Every alternative above
    # anchors the year on `(?:19|20)\d{2}`, so a Buddhist-era or Japanese-era
    # date could not be captured as a candidate at all - and a shape nothing
    # captures is a shape nothing downstream can convert, however good the
    # reader is. `audit_common` has held working readers for these for some
    # time and this is the pattern half they were waiting on; the
    # three patterns are imported from there rather than rewritten, so the
    # shape that decides a candidate is the shape that converts it.
    #
    # Only the numeric one needs the page's own evidence - `31/03/2563` is a
    # year only where the page says which calendar it is in, and `_dates`
    # drops it where the page does not. The other two carry their script in
    # the match.
    "|" + BUDDHIST_ERA_DATE_RE.pattern +
    "|" + THAI_ERA_YEAR_RE.pattern +
    "|" + JAPANESE_ERA_DATE_RE.pattern,
    re.I,
)
COPYRIGHT_YEAR_RE = re.compile(r"(?:©|\(c\)|copyright)\s*(?:\d{4}\s*[-–—]\s*)?((?:19|20)\d{2})", re.I)
# Only phrases that assert the copy is *current* as of a year. A bare "in 2019"
# is usually a founding date - history, not a staleness claim - and matching it
# turned every about page into a false positive.
AS_OF_YEAR_RE = re.compile(
    r"\b(?:as of|current as of|accurate as of|correct as of|last updated|updated (?:in|on|for)"
    r"|valid (?:for|until|through)|figures? for|data (?:from|for)|prices? (?:as of|for)"
    r"|edition|guide for|report for)\s+(?:\w+\s+){0,2}((?:19|20)\d{2})\b", re.I)

# `\b` before a leading zero used to leave it behind: "01904 555 812" came out
# as "1904 555 812", a different number, which then failed to match the same
# number written elsewhere on the site.
# The middle group takes two digits as well as three or four. A Japanese
# number is written 0467-23-3000 and a German one 030-12 34 56 78, and
# requiring three in the middle missed both - so a city hall's report quoted
# its thirteen-digit corporate registry number as the telephone number while
# the real one, in the footer of all 57 crawled pages behind the word for
# "telephone", was never matched at all.
PHONE_RE = re.compile(
    r"(?<![\d/])"
    r"(?:\+\d{1,3}[\s.-]?)?"
    r"(?:\(0?\d{2,5}\)|0?\d{2,5})"
    r"[\s.-]?\d{2,4}[\s.-]?\d{2,4}"
    r"(?![\d/])")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]{2,}\b")

# Alphanumeric formats first. A bare five- or six-digit run is also what a
# phone number looks like, and searching left to right on "YO1 9TT. 01904 555
# 812." with the US branch first returned the area code as the postcode, so the
# numeric branches refuse a run sitting inside a longer run of digits.
#
# A decimal fraction is also a run of five or six digits. A pricing page
# reading "$0.00005 / event" handed back postal code "00005", the audit then
# reported the site as contradicting its own address, and the generated
# Organization snippet offered "00005" as the postalCode to publish. So the
# numeric branches now refuse a run that a decimal point leads into or that a
# decimal point continues.
#
# The two letters that finish a UK postcode are drawn from a restricted
# alphabet - C, I, K, M, O and V are never used in that position - and the
# unrestricted class read product codes as postcodes. A footwear brand's
# Japanese site prints a shoe reference beside its price, "<COLOUR> CR B169HI",
# and `B169HI` satisfied the shape exactly: area B, district 16, then 9HI. Four
# pages were reported as printing four different street addresses, "which is
# what a business with separate branches looks like", and told to publish a
# LocalBusiness block each.
_UK_UNIT_LETTER = "[ABD-HJLNP-UW-Z]"
POSTCODE_HINT_RE = re.compile(
    r"\b(?:[A-Z]{1,2}\d[A-Z\d]?\s*\d" + _UK_UNIT_LETTER + r"{2}"   # UK
    r"|[A-Z]\d[A-Z]\s*\d[A-Z]\d"                    # CA
    r"|(?<![\d.])\d{5}(?:-\d{4})?(?![\s.-]?\d{3})(?![\d.]\d)"   # US, not a phone or a decimal
    # A six-digit code split down the middle, which is how a South Asian
    # postal code is printed as often as not: "<state> - 686 001". A footer
    # carrying it on all 40 crawled pages recorded `has_address: false`, and
    # the report's first "start here" item was that no postal address appears
    # in the page text or in the page markup - while its own "what an
    # assistant would quote" table quoted the address underneath.
    #
    # The guard is the one the numeric branches already use, widened by one
    # character: a run preceded by a digit and a separator is the tail of a
    # telephone number ("0484 566 001"), not a postal code.
    r"|(?<!\d[\s.-])(?<![\d.])\d{3}\s\d{3}(?![\s.-]?\d)"
    r"|(?<![\d.])\d{6}(?![\s.-]?\d{3})(?![\d.]\d))\b"           # IN / SG, likewise
)

# Where a postal code is allowed to be read from.
#
# A five- or six-digit number means "postal code" only in a place that is
# describing an address. Read off a whole page it means whatever the page
# happens to be about: a retailer's "Item ID 374819" was reported as the
# postal code its structured data disagreed with, and that finding was ranked
# third in what to fix first. So an address-bearing region is preferred, and
# the whole page is used only as a last resort, where a street hint has to
# corroborate it.
ADDRESS_REGION_SELECTORS = ("address", "[itemtype*=PostalAddress]",
                            "[class*=address]", "[id*=address]", "footer")

# The same regions without the footer: markup that says "this run is an
# address" about the place it sits in, rather than furniture that happens to
# carry one. The two are separated because a footer is site-wide and the rest
# are not, and a reader that wants the address *this page* declares must not
# be handed the head office that every page carries.
ADDRESS_MARKUP_SELECTORS = ADDRESS_REGION_SELECTORS[:-1]

# The street-type word is optional: "41 Walmgate, York" has none, and neither
# does most of the UK, where the type is part of the name (gate, row, mews,
# close) or absent. The second branch accepts a number followed by capitalised
# words when a postcode follows close behind.
#
# Two patterns, and they must not share a flag.
#
# The second branch says "a number, then capitalised words, then a postcode",
# and capitalisation is the only thing separating a street name from any other
# phrase that starts with a number. Both branches were compiled with `re.I`,
# which switches that requirement off: on a pricing page, "1 million rows"
# followed later by a five-digit run matched, and "1 million row" was published
# as the streetAddress in a paste-ready Organization snippet. The street-type
# branch does want to ignore case, because "Road" and "road" are both a road.
# So they are compiled separately.
# Two tiers, because half of these words are ordinary English.
#
# "Road", "Avenue" and "Boulevard" follow a number in an address and almost
# nowhere else. "Way", "Suite", "Loop", "Place", "Court" and "Park" are
# everyday nouns, and one pattern covering both told an open-source project
# that three of its pages print a street address: "5204 libssh blocking and
# infinite loop" and "6265 test suite" (an issue number and an RFC number) and
# "2 the standard way". The report then read those as three different branch
# addresses and recommended a LocalBusiness block per branch, with opening
# hours, for a software project with no premises.
#
# What separates the two cases is that a street has a name and names are
# capitalised: "1 Embankment Place" against "2 the standard way". So the
# ambiguous tier requires a capitalised word between the number and the type,
# and the unambiguous tier does not. The type word itself stays
# case-insensitive in both, because "Road" and "road" are both a road.
_UNAMBIGUOUS_STREET_TYPES = (
    r"street|st\.?|road|rd\.?|avenue|ave\.?|lane|ln\.?|drive|dr\.?|boulevard|blvd\.?|"
    r"marg|nagar|colony|mews|terrace|crescent|parade|parkway|pkwy\.?|highway|hwy\.?|"
    r"alley|esplanade|quay|wharf")
_AMBIGUOUS_STREET_TYPES = (
    r"way|court|ct\.?|place|pl\.?|suite|ste\.?|floor|gate|row|close|walk|green|hill|"
    r"square|sq\.?|circle|cir\.?|trail|loop|plaza|park|gardens|grove")

# What a street name is made of.
#
# Words, not identifiers. `[\w.'-]+` accepts any run of word characters, and a
# security advisory table is nothing but runs of word characters: an
# open-source project's report printed "11418 CVE-2025-1816 59733" as its
# postal address, with `has_address: true`, which marked the core-facts check
# passed and suppressed the true finding that the project states no location
# anywhere. Two ticket numbers and a vulnerability identifier read as a house
# number, a street name and a postal code.
#
# A name word is letters, with the apostrophes, hyphens and abbreviating full
# stops a place name carries inside it - or a bare ordinal, because "350 5th
# Avenue" is a real street and "5th" is a real name word. A token that mixes
# letters and digits any other way is a record identifier, not a word:
# `CVE-2025-1816`, `v2.1.3`, `SC-2024`.
_STREET_NAME_WORD = (
    r"(?:\d{1,3}(?:st|nd|rd|th)|[^\W\d_]+(?:['’.-][^\W\d_]+)*\.?)")
# The same, where capitalisation is the thing being tested.
_CAPITALISED_STREET_WORD = (
    r"(?:[A-Z][^\W\d_]*(?:['’.-][^\W\d_]+)*\.?)")

# The postal-code shapes a street may be corroborated by. The same shapes the
# lookaheads below and `POSTCODE_HINT_RE` read, written once so a street's idea
# of a postcode cannot drift from the extractor's.
_POSTCODE_SHAPE = (r"[A-Z]{1,2}\d[A-Z\d]?\s*\d" + _UK_UNIT_LETTER + r"{2}"   # UK
                   r"|[A-Z]\d[A-Z]\s*\d[A-Z]\d"             # CA
                   r"|(?<!\d)\d{3}\s\d{3}(?!\d)"            # a six-digit code in two halves
                   r"|\d{5}")                               # US and the rest

# How far past the street a postal code may sit and still belong to it: room
# for a locality and a region - "12 MG Road, Bengaluru 560001", "200 Queen
# Street West, Toronto, M5V 3K9" - and no digits in between, because the next
# digit is either the code itself or the start of something else. Ninety
# characters, so a directory page's next address is out of reach.
_POSTCODE_FOLLOWS = r"(?=[^0-9]{0,90}(?:" + _POSTCODE_SHAPE + r"))"

# A postal code has to follow this one too.
#
# "Street", "Road" and "Avenue" are strong words and they are still not proof
# on a shop. A footwear brand sells a model called "The Street", so its
# collection pages read "8 New The Street" and "11 Street Collection The
# Street" - which matched, were paired with the one postal code in the
# site-wide footer, and became separate branch addresses in a high-severity
# finding telling a single-office brand to publish LocalBusiness markup per
# branch. What makes a street an address is the code beside it, and the
# `has_address` signal downstream already requires both, so requiring it here
# only makes the two agree.
STREET_TYPE_RE = re.compile(
    r"\b\d+[A-Za-z]?\s+(?:" + _STREET_NAME_WORD + r"\s+){0,3}"
    r"(?i:" + _UNAMBIGUOUS_STREET_TYPES + r")\b"
    + _POSTCODE_FOLLOWS
)
# The number, then up to four words of which at least one is capitalised, then
# the ambiguous type word.
# No `re.I` on this one, and the type word carries its own inline
# case-insensitive group instead. A flag applies to the whole pattern, so
# compiling this with `re.I` would make `[A-Z]` match a lowercase letter and
# quietly delete the capitalisation requirement that is the entire point of
# the tier - which is how "2 the standard way" became a street address the
# first time.
# The ambiguous tier needs a postal code near it, the way the named tier does.
#
# A capitalised word before "Walk", "Way", "Place" or "Park" is not enough on
# its own, because a shop's product listing is full of exactly that shape. On
# a footwear brand's collection pages the extractor read "11 New Begin Walk",
# "12 New Begin Walk" and "11 Best Seller Begin Walk" as three street
# addresses - a badge, a price and a shoe model - and paired each with the one
# postal code in the site-wide footer. The report then said eleven pages print
# their own street address, that each address is different from the others,
# "which is what a business with separate branches looks like", and told a
# single-office direct-to-consumer brand at high severity to publish a
# LocalBusiness block per branch with opening hours.
#
# A real street on a real page is written near its postal code. That is the
# corroboration the everyday-noun tier was missing, and requiring it costs the
# genuine cases nothing.
STREET_AMBIGUOUS_TYPE_RE = re.compile(
    r"\b\d+[A-Za-z]?\s+(?:" + _STREET_NAME_WORD + r"\s+){0,2}"
    + _CAPITALISED_STREET_WORD + r"(?:\s+" + _STREET_NAME_WORD + r")?\s+"
    r"(?i:" + _AMBIGUOUS_STREET_TYPES + r")\b"
    + _POSTCODE_FOLLOWS
)
# What sits between the end of a street line and the code that finishes it.
#
# A comma and a space were the whole class, and a dash is the third way the
# join is written: "<city>, <state> - 686 001", "<city> - 560 058",
# "Berlin - 10115". Two sites' footers printed their registered and corporate
# offices in exactly that shape on every crawled page and both were told, as
# the first item in what to fix, that no postal address appears in the page
# text or in the page markup.
_ADDRESS_LINE_JOIN = r"[,\s\u2013\u2014-]"
STREET_CAPITALISED_RE = re.compile(
    r"\b\d+[A-Za-z]?\s+" + _CAPITALISED_STREET_WORD +
    r"(?:,?\s+" + _CAPITALISED_STREET_WORD + r"){0,3}"
    r"(?=" + _ADDRESS_LINE_JOIN + r"+(?:" + _POSTCODE_SHAPE + r"))"
)

# A street with no house number.
#
# Every pattern above opens `\b\d+`, so a house number was mandatory, and most
# named buildings do not have one: a museum's contact page reads "Address:
# <Brand>, Thornbury Road, <Town> KT7 4LP" and a second page reads "Address
# <Brand> Thornbury Rd <Town> KT7 4LP". Both recorded `street_hint: ""` and
# `has_address: false`, and the report's single high-severity finding - its
# first "start here" item - was that the site never states its location, over
# two pages the crawl had read and stored the address from. Universities,
# colleges, hospitals, galleries and most civic buildings address themselves
# the same way.
#
# What carries the weight here is the postcode, not the street type. Dropping
# the house number removes the strongest signal the numbered patterns have, so
# something else has to say this is an address rather than a phrase: a postal
# code within a few words of the type word, with room for the locality between
# them ("Thornbury Road, <Town> KT7 4LP"). Without that corroboration this
# branch would match "Harbour Lane" in any sentence that mentions one.
#
# Both remaining requirements are strict for the same reason. The type word
# must be an unambiguous one - "Road", "Avenue", "Lane" - because the
# everyday-noun tier ("Way", "Park", "Green", "Close") has no house number left
# to anchor it and would match half the English language. And every word of the
# name must be capitalised, because a street has a name.
STREET_NAMED_RE = re.compile(
    _CAPITALISED_STREET_WORD +
    r"(?:\s+" + _CAPITALISED_STREET_WORD + r"){0,3}\s+"
    r"(?i:" + _UNAMBIGUOUS_STREET_TYPES + r")\b"
    r"(?=(?:[,\s]+" + _STREET_NAME_WORD + r"){0,3}"
    + _ADDRESS_LINE_JOIN + r"+(?:" + _POSTCODE_SHAPE + r"))"
)


# An address whose building number is labelled rather than leading, and which
# names no street type at all.
#
# Every pattern above needs either a Western street-type word or a bare number
# at the head of the line, and a South Asian address routinely has neither:
# "No. 45-A, 2nd Phase, <name> Industrial Area, <city> - 560 058" and
# "Godown #A5, <name> Logistic Hub, ... <city>, <state>, India, 421302"
# are both complete postal addresses in which "Area", "Phase" and "Hub" are
# the type words and none of them is in any list here. Both sites recorded
# `has_address: false` on pages whose text the crawl had stored.
#
# What replaces the street-type word is the label in front of the number.
# "No." and "#" are written to say "this run of characters is a building", and
# they are the same mark whatever language the rest of the line is in - which
# is why `_STREET_NUMBER_LABELLED_OTHERWISE_RE` deliberately excludes "no."
# from the words that disown a house number. Everything after it has to be a
# name: capitalised words, or an ordinal, because "2nd Phase" is a place.
#
# The postal code is required, as it is on every other tier here. Without it
# this would read "#1 Choice For Quality" as an address on any shop that
# writes a badge that way.
_BUILDING_NUMBER = (r"(?:\b(?i:no)\.?\s?|#\s?)"
                    r"(?:[A-Za-z]{1,2}[-/]?)?\d{1,5}(?:[-/][A-Za-z\d]{1,4})?")
_PLACE_NAME_WORD = r"(?:\d{1,3}(?:st|nd|rd|th)|" + _CAPITALISED_STREET_WORD + r")"
STREET_LABELLED_NUMBER_RE = re.compile(
    _BUILDING_NUMBER + r"(?:,?\s+" + _PLACE_NAME_WORD + r"){2,8}"
    + _POSTCODE_FOLLOWS
)

# The words that name a run of digits as a record rather than as a house
# number. Same discipline as `not_a_telephone_number`: a rule about what the
# digits are, plus a check on the words in front of them.
#
# `_NUMBER_LABELLED_OTHERWISE_RE`, which does this job for postal codes, is
# deliberately not reused. It disowns a number introduced by "no.", "nr." or
# "number", and in front of a house number those three words are how half of
# Europe writes an address - "No. 12 High Street". The vocabulary that matters
# here is the one an issue tracker, a changelog and a security advisory use.
_STREET_NUMBER_LABELLED_OTHERWISE_RE = re.compile(
    r"(?:bug|issue|ticket|case|advisory|cve|cwe|defect|vulnerability|"
    r"patch|build|version|release|revision|rev|commit|changeset|pull request|"
    r"invoice|order|account|item|sku|isbn|reference|ref|id|figure|table|row)"
    r"\W{0,12}$", re.I)


# A Japanese address, which is ordered the other way round from the two
# patterns above: prefecture first, then ward or city, then the block numbers.
# There is no word for "street" in it to match on, so the units themselves are
# the pattern - 都/道/府/県 for the prefecture, 市/区/町/村 for the
# municipality, 丁目/番地/番/号 for the block.
#
# A national postal group's contact page carries
# "所在地 〒100-8791 東京都千代田区大手町二丁目3番1号", and the report said no
# postal address was found anywhere on the site.
#
# The block numbers are written with hyphens as often as with the unit words -
# "<prefecture>\u5e9c<county>\u90e1<town>\u753a<district>8-1-3" - and a library whose
# access page printed three addresses that way recorded a postcode and no
# street on fifty pages. A county (\u90e1) is a municipality's parent the way a
# prefecture is, so it closes the municipality too.
_JP_KANA_KANJI = r"\u3040-\u30ff\u4e00-\u9fff"
_JP_BLOCK_NUMBERS = (r"[0-9\uff10-\uff19]{1,4}"
                     r"(?:[-\u2010\u2212\uff0d\u30fc][0-9\uff10-\uff19]{1,4}){1,3}"
                     r"(?![-0-9\uff10-\uff19])")
_JP_MUNICIPALITY_AND_BLOCK = (
    r"[" + _JP_KANA_KANJI + r"]{1,12}[\u5e02\u533a\u753a\u6751\u90e1]"
    r"(?:[^\s]{0,24}?(?:\u4e01\u76ee|\u756a\u5730|\u756a|\u53f7)"
    r"|[" + _JP_KANA_KANJI + r"]{0,12}?" + _JP_BLOCK_NUMBERS + r")")
JP_STREET_RE = re.compile(
    r"[" + _JP_KANA_KANJI + r"]{2,12}[\u90fd\u9053\u5e9c\u770c]"
    + _JP_MUNICIPALITY_AND_BLOCK)

# Inside its own prefecture a postmarked address routinely leaves the
# prefecture off: "\u3012100-0000 <ward>\u533a<town>1-2-3". The postal mark directly in
# front is what says this is an address, so here it stands in for the
# prefecture - and only here, because a ward and a number with nothing in front
# of them are just as often a sentence.
JP_POSTMARKED_STREET_RE = re.compile(
    r"(?:(?<=\u3012\d{3}-\d{4})|(?<=\u3012\d{3}-\d{4}\s)"
    r"|(?<=\u3012\s\d{3}-\d{4})|(?<=\u3012\s\d{3}-\d{4}\s))"
    + _JP_MUNICIPALITY_AND_BLOCK)

# A Korean road-name address, largest unit first like the Japanese one: a city
# or province (\uc2dc/\ub3c4, or one of the special and metropolitan forms), a district
# (\uad6c/\uad70, or \uc2dc inside a province), a road whose name ends \ub85c or \uae38, and the
# building number. A national museum's footer printed "<5-digit code> <city>\uc2dc
# <district>\uad6c <road>\ub85c 137(<neighbourhood> 168-6)" on every page, the extractor
# found the postcode and no street, and the report said no postal address was
# found anywhere.
#
# The hierarchy is the guard. \ub85c is also one of the commonest particles in the
# language ("by", "to", "as"), so a road name on its own would read half of
# every sentence as a street. A city, then a district, then a road, then a
# number that is not followed by a unit of time, money or count, is an address
# and nothing else. Where the city is left off, the five-digit postcode
# directly in front stands in for it, the way the postal mark does above.
_HANGUL = r"\uac00-\ud7a3"
_KR_DISTRICT_ROAD_NUMBER = (
    r"(?:[" + _HANGUL + r"]{1,8}(?:\uc2dc|\uad70|\uad6c)\s?){1,2}"
    r"(?:[" + _HANGUL + r"]{1,8}(?:\uc74d|\uba74)\s?)?"
    r"[" + _HANGUL + r"0-9]{1,15}(?:\ub85c|\uae38)\s?[0-9]{1,5}(?:-[0-9]{1,5})?"
    r"(?![0-9.,%])(?!\s?(?:\ub144|\uc6d4|\uc77c|\uc6d0|\uba85|\uac1c|\uce35))")
KR_STREET_RE = re.compile(
    r"(?:[" + _HANGUL + r"]{1,8}(?:\ud2b9\ubcc4\uc2dc|\uad11\uc5ed\uc2dc"
    r"|\ud2b9\ubcc4\uc790\uce58\uc2dc|\ud2b9\ubcc4\uc790\uce58\ub3c4|\uc2dc|\ub3c4)\s?"
    r"|(?<=(?<![0-9])[0-9]{5}\s))"
    + _KR_DISTRICT_ROAD_NUMBER)

# A Chinese address, largest unit first again: an optional province (\u7701 or
# \u81ea\u6cbb\u533a), a city (\u5e02), a district or county (\u533a/\u5340/\u53bf/\u7e23), a road
# (\u8def/\u8857/\u5927\u8857/\u5927\u9053/\u9053/\u5df7/\u5f04/\u80e1\u540c, with its \u6bb5 section where the road has
# them), and the number closed by \u53f7/\u865f. The closing mark is the guard: a
# sentence naming a city and a road never ends in a numbered \u53f7.
CN_STREET_RE = re.compile(
    r"(?:[\u4e00-\u9fff]{2,7}(?:\u7701|\u81ea\u6cbb\u533a))?"
    r"[\u4e00-\u9fff]{2,7}\u5e02"
    r"[\u4e00-\u9fff]{1,7}(?:\u533a|\u5340|\u53bf|\u7e23|\u5e02)"
    r"[\u4e00-\u9fff0-9]{0,12}?(?:\u8def|\u8857|\u5927\u9053|\u9053|\u5df7|\u5f04|\u80e1\u540c)"
    r"(?:[\u4e00\u4e8c\u4e09\u56db\u4e94\u516d\u4e03\u516b\u4e5d\u5341]{1,3}\u6bb5)?"
    r"[0-9\uff10-\uff19]{1,5}(?:-[0-9]{1,5})?(?:\u53f7|\u865f)")

# The postal mark. It exists to say "a postcode follows", so unlike a bare run
# of digits it needs no corroboration.
JP_POSTCODE_RE = re.compile(r"\u3012\s?\d{3}-\d{4}")


_STREET_PATTERNS = (STREET_TYPE_RE, STREET_AMBIGUOUS_TYPE_RE, STREET_CAPITALISED_RE,
                    STREET_NAMED_RE, STREET_LABELLED_NUMBER_RE, JP_STREET_RE,
                    JP_POSTMARKED_STREET_RE, KR_STREET_RE, CN_STREET_RE)


# A house number never continues a number that started before it.
#
# `\b` sits between a comma and a digit, so the tail of a grouped figure looks
# exactly like a house number at the head of a line. On a footwear brand's
# Japanese product listing the extractor read "950 <COLOUR> CR" as a street: the
# 950 is the last three digits of the price 4,950 printed immediately before
# it, and the two words in front of the code are the colourway. Four pages
# were then reported as printing four different street addresses and the site
# was told to publish LocalBusiness markup per branch.
#
# Two shapes, one rule. A digit and a grouping separator directly in front is
# a figure being continued, whichever separator the locale uses; a currency
# sign further back with nothing but digits and separators since is the same
# figure seen from further away, which is what catches a price written with a
# thin space instead of a comma.
_STREET_NUMBER_CONTINUES_A_FIGURE_RE = re.compile(
    r"\d[.,\u00a0\u202f]$"
    r"|[" + CURRENCY_SYMBOLS + r"]\s?[\d.,\u00a0\u202f ]*$")
# How far back that reading looks. A price and the code beside it are written
# on one line; anything further away is a different fact.
FIGURE_TAIL_WINDOW = 16


def _street_number_continues_a_figure(text, match):
    """Is the number this street opens with the tail of the figure before it?"""
    before = text[max(0, match.start() - FIGURE_TAIL_WINDOW):match.start()]
    return bool(_STREET_NUMBER_CONTINUES_A_FIGURE_RE.search(before))


def _street_disowned_by_its_label(text, match):
    """Do the words just before this say the leading number is a record?

    The window is the same one the telephone rule reads, because it is the same
    reading: a caption sits directly above the number it names and the
    extractor joins the two blocks with a space.
    """
    before = text[max(0, match.start() - CODE_LABEL_WINDOW):match.start()]
    return bool(_STREET_NUMBER_LABELLED_OTHERWISE_RE.search(before))


def _street_hints(text, limit=6):
    """Every street-shaped phrase in this text, earliest first.

    Every pattern is asked for all of its matches rather than its first,
    because a match the label test throws out must not hide a real address
    further down the same page - a release-notes page that lists ticket
    numbers before printing the publisher's address in its footer would
    otherwise report the ticket number and stop.

    More than one is returned because the shape test and the ownership test
    are different questions. `_street_the_site_states_as_its_own` throws out an
    address that belongs to somebody the page names, and the first
    street-shaped run on a page is routinely one of those: a legal disclosure
    naming a supplier prints the supplier's premises before the seller's. One
    rejected candidate must not hide the address underneath it, for the same
    reason a rejected ticket number must not.
    """
    matches = []
    for pattern in _STREET_PATTERNS:
        for match in pattern.finditer(text or ""):
            if _street_disowned_by_its_label(text, match):
                continue
            if _street_number_continues_a_figure(text, match):
                continue
            matches.append(match)
            break
    return sorted(matches, key=lambda m: m.start())[:limit]


def _street_hint(text):
    """The earliest street-shaped phrase, whichever pattern finds it."""
    matches = _street_hints(text, limit=1)
    return matches[0] if matches else None

# A call to action is a link that asks the visitor to do something next.
# Matching a fixed list of marketing phrases missed most real ones ("Browse the
# range", "Speak to the trade counter"), so this matches on the shape instead:
# an imperative opening verb, or markup that styles the link as a button.
CTA_VERBS = frozenset("""
get start begin book buy shop browse view see request contact call order try
download subscribe join apply explore discover find learn read watch schedule
reserve sign register talk speak ask enquire inquire compare choose select plan
build create send email message visit check claim take open configure estimate
quote hire arrange play listen search play tour
""".split())

# Link labels whose first word is in CTA_VERBS but is not being used as a verb.
#
# "Open source" is the one that mattered: on a free software project's
# homepage, a link reading "open source" inside a sentence about licensing was
# reported as the page's primary call to action, while the "Download" link in
# the main navigation was ignored - and the recommended replacements were "See
# pricing" and "Book a table", offered to a command-line tool that costs
# nothing. Each of these is the word being used as an adjective or a noun, in a
# phrase common enough on the open web to be worth naming.
_VERB_IS_AN_ADJECTIVE_HERE = re.compile(
    r"^(?:open source|open standards?|open data|open access|open government|"
    r"start(?:ing|er)s? (?:guide|kit|page)|get(?:ting)? started guide|"
    r"read(?:ing)? (?:list|time)|watch(?:es|list)|plan(?:s|ning)? (?:and|&)|"
    r"build(?:ing)?s?\b(?! )|create(?:s|d)|find(?:ing)?s|check(?:s|list)|"
    r"take(?:s|away)|order(?:s|ing)? (?:of|form)|"
    r"see (?:also|more) below)\b", re.I)

# Not a position. Sorts a call to action whose label is not in the body copy
# (an aria-label, or text inside an image) behind every located one.
UNLOCATED_CTA_OFFSET = 10 ** 6

CTA_MARKUP_RE = re.compile(r"\b(btn|button|cta|call-to-action|primary-action)\b", re.I)

NEWSLETTER_HINTS = ("newsletter", "subscribe", "mailing list", "email updates", "join our list")

GENERIC_H1 = {
    "welcome", "home", "homepage", "welcome to our website", "hello",
    "untitled", "index", "welcome!", "our website", "site",
}


# --------------------------------------------------------------------------
# What the page itself says it is, where its address says something else
# --------------------------------------------------------------------------

# The markup that puts one thing in a basket.
#
# `detect_page_type` reads the address first and then lets the copy correct
# it, and the copy it reads is `_PRODUCT_TEXT_SIGNALS` in `audit_common` -
# "add to cart", "in stock", "free shipping", "select size" - twelve English
# phrases. A shoe shop in Indonesia keeps its eleven product pages under
# `/shop/<handle>`; `shop` is a listing word, so the address said `category`,
# and not one of the twelve phrases appears anywhere on a page written in
# Bahasa Indonesia. 0 of 26 crawled pages were typed `product` and all eleven
# product pages were typed `category`. Two things followed. A listing-only
# finding fired on a single product page whose `<title>` names one shoe. And
# the recommendation that shop needed most - publish `Product` and `Offer`
# with a price and an availability, on a site with no JSON-LD at all - was
# never made, because the audit believed the site had no product page on it.
#
# What a shop writes in its copy is in its own language. What it writes in its
# markup is not: `add-to-cart`, `addToCart`, `single_add_to_cart_button`,
# `/cart/add` are identifiers a developer types, and a storefront serving
# Indonesian, Thai or Japanese ships the same ones. That is the same argument
# `STATE_BLOB_PATTERNS` above rests on, and the same reason a substring test
# over attribute values is safe here: these are not words that appear in
# prose, so a match is the markup and never the writing.
#
# Counted rather than merely detected, because the count is the whole
# distinction this has to keep. One control is one thing for sale; a grid with
# a quick-add button under every tile carries one per item, which is the shelf
# those items sit on and must never be asked for `Product` markup.
_ADD_TO_CART_RE = re.compile(
    r"add[-_.]?to[-_.]?(?:cart|bag|basket|order|quote)"
    r"|cart[-_.]?add|/cart/add"
    r"|buy[-_.]?(?:now|it[-_.]?now)"
    r"|product-form__submit|single_add_to_cart", re.I)

# The two control names that carry no verb at all. The commonest hosted store
# platform submits its buy form with `<button type="submit" name="add">`, and
# a plugin-based one with `<input name="add-to-cart" value="12">`; the first
# would match nothing above.
_CART_CONTROL_NAMES = frozenset({"add", "add-to-cart", "add_to_cart"})

# The element's own label, for the buy control that carries the identifier
# nowhere in its markup: `<form action="/en/cart" method="post"><button
# type="submit">Add to Cart</button></form>`. Nothing above can see that -
# `/en/cart` is not `/cart/add`, and `add[-_.]?to[-_.]?cart` cannot match
# "Add to Cart" because a space is not one of `[-_.]`.
#
# Read as a separate pattern, on the visible label only, and only on an
# element that is a control. That keeps the two routes honest about what they
# are: the identifier route is language-free and works on a shop trading in
# any language, and this one is a word list that fires only where the shop has
# localised into a language it is written in. It can add a control and never
# remove one, so a Bahasa Indonesia storefront is exactly as well read as
# before and an English-localised one is no longer read as having no buy
# control at all.
_CART_LABEL_RE = re.compile(
    r"^(?:add(?:ed)?\s+to\s+(?:cart|bag|basket|trolley|order)"
    r"|buy\s+(?:now|it\s+now)"
    r"|add\s+to\s+my\s+(?:cart|bag|basket))\b", re.I)

# How long a label may be before it stops being a button and starts being a
# sentence about buying. "Add to cart" is 11 characters; a paragraph opening
# "Add to cart to reserve your place in the next batch" is not a control.
CART_LABEL_MAX_CHARS = 32

# What counts as a control for the label reading. A `<div>` reading "add to
# cart" somewhere in a review is not one.
_CART_CONTROL_ELEMENTS = frozenset({"button", "input", "a"})

# The page declaring in machine-readable markup that it is one thing on sale.
#
# The second route in, and the one that reaches a storefront which renders its
# buy button after the script runs. Open Graph's product vocabulary is what a
# shop emits for a shopping-catalogue feed; it is one page one product by
# construction, and like the control names it is spelled the same way whatever
# language the shop trades in. It is not the same claim as `Product` JSON-LD -
# a page can carry this and still publish no structured data at all, which is
# exactly the shop this rule was written for.
_OFFER_META_KEYS = ("product:price:amount", "product:price:currency",
                    "product:availability", "product:retailer_item_id",
                    "og:price:amount", "og:availability")
_OFFER_OG_TYPES = frozenset({"product", "product.item", "og:product"})

# Types the address decided that the page's own content may overturn.
#
# `category` is the failure seen in the wild. `other` is here because it means "no word
# in this address was recognised", which is the same absence of evidence and
# the commoner one outside English. Every other type in the table was decided
# by a slug this audit does recognise - `pricing`, `contact`, `legal` - and a
# page that says what it is in its address is not overruled by its furniture.
_TYPES_THE_CONTENT_MAY_OVERTURN = frozenset({"category", "other"})

# The last path segment naming the section rather than a thing in it. `/shop`
# is the way into a catalogue and `/shop/<handle>` is one item in it, and the
# whole promotion below turns on that difference - so an address that ends at
# the grouping word is refused before anything is measured, however few prices
# it happens to print.
_SECTION_SEGMENTS = frozenset({
    "shop", "shops", "store", "stores", "browse", "catalog", "catalogue",
    "collection", "collections", "category", "categories", "product",
    "products", "items", "all", "new", "sale",
})


def _cart_control_evidence(tag):
    """What about this element makes it an add-to-basket control, or ""."""
    name = (tag.get("name") or "").strip().lower()
    if tag.name in ("button", "input") and name in _CART_CONTROL_NAMES:
        return '<{} name="{}">'.format(tag.name, name)
    # Every attribute, not a list of six.
    #
    # This read `action`, `href`, `class`, `id`, `name` and `value`, under a
    # comment saying the rest arrive "in a class, an id or a data attribute" -
    # and no data attribute was in the list, nor `onclick`. A server-rendered
    # skincare shop puts its whole buy control in one, and its product pages
    # recorded `add_to_cart_controls: {"count": 0}` with the button on the
    # screen. That is the same move `_names_a_breadcrumb` makes and rests on
    # the same fact: `add-to-cart`, `addToCart` and `/cart/add` are
    # identifiers a developer typed, so a substring match on one is the markup
    # and never the writing, wherever in the tag it sits.
    for attr, value in (tag.attrs or {}).items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        value = str(value).strip()
        if _ADD_TO_CART_RE.search(value):
            return '<{} {}="{}">'.format(tag.name, attr, truncate(value, 60))
    # The label, last, so a control that names itself in its markup is still
    # quoted by its markup and the evidence stays language-free wherever it
    # can be. See `_CART_LABEL_RE`.
    if tag.name in _CART_CONTROL_ELEMENTS:
        label = (tag.get("value") if tag.name == "input" else tag.get_text(" ")) or ""
        label = re.sub(r"\s+", " ", str(label)).strip()
        if len(label) <= CART_LABEL_MAX_CHARS and _CART_LABEL_RE.match(label):
            return '<{}> labelled "{}"'.format(tag.name, label)
    return ""


def _add_to_cart_controls(soup, limit=8):
    """How many add-to-basket controls this page's markup carries, and which.

    Outermost only. A buy control is routinely a `<form action="/cart/add">`
    with a `<button class="add-to-cart">` inside it, and counting both would
    make one control read as two - which matters, because one is a product
    page and two upwards starts to look like a shelf with quick-add.
    """
    matched, evidence, count = set(), [], 0
    for tag in soup.find_all(["form", "button", "a", "input"]):
        why = _cart_control_evidence(tag)
        if not why:
            continue
        nested = any(id(parent) in matched for parent in tag.parents)
        matched.add(id(tag))
        if nested:
            continue
        count += 1
        if len(evidence) < limit:
            evidence.append(why)
    return {"count": count, "evidence": evidence}


def _declares_one_offer(meta):
    """The page's own markup saying it is a single thing for sale, or ""."""
    if (meta.get("og:type") or "").strip().lower() in _OFFER_OG_TYPES:
        return 'its own `og:type` is "{}"'.format((meta.get("og:type") or "").strip())
    for key in _OFFER_META_KEYS:
        if (meta.get(key) or "").strip():
            return "it publishes `<meta property=\"{}\">`".format(key)
    return ""


def _the_one_subject(headings, links, title):
    """The single thing this page is about, or "" where it is about several.

    A product page names one thing at the top and a shelf names the shelf. Two
    ways a heading fails to be a single subject, and both were measured on
    real listing pages: there is more than one H1, or the H1 is also the label
    of a link leaving the page, which is what a tile's caption is.
    """
    h1s = [h.strip() for h in (headings.get("h1") or []) if (h or "").strip()]
    if len(h1s) > 1:
        return ""
    labels = {listing_key(entry.get("text"))
              for entry in ((links or {}).get("internal") or [])}
    labels.discard("")
    if h1s:
        subject = h1s[0]
        if subject.lower() in GENERIC_H1 or listing_key(subject) in labels:
            return ""
        return subject
    # No heading in the markup at all, which a client-rendered storefront
    # routinely delivers. The leading segment of the title is what the page
    # calls itself there - "Retrograde GD Black White | <brand>" - and it is
    # read only as a fallback, because a heading is the stronger statement.
    segments = title_segments(title)
    lead = segments[0] if segments else ""
    if not lead or lead.lower() in GENERIC_H1 or listing_key(lead) in labels:
        return ""
    return lead


def _distinct_prices(text):
    """How many different amounts of money this text prints.

    The same reading `structured-data-audit._distinct_prices_on` makes, and it
    has to be: that function is what sets a page aside again after this one
    promotes it, so a page promoted here on a price count and dropped there on
    the same count would be a page nothing ever grades.
    """
    values = {v for v in (price_value(p) for p in find_prices(text or "", limit=120))
              if v is not None}
    return len(values)


# How much visible text a delivered document has to hold before its content is
# allowed to answer for it. 300, the same floor `NOT_GRADABLE_TEXT_FLOOR` uses
# for the same reading: below it a document holds a line of chrome and no
# passage anything downstream would quote.
#
# Measured on the *whole delivered document*, never on `body_text`. That rule
# was learned expensively: a server-rendered storefront reading 173
# characters of body text against 3,517 visible ones produced a report whose
# critical finding, high finding, top-sheet line and first two Start-here items
# were all false. A page is thin or a container by what the server sent, not by
# what is left after the chrome is stripped out of it.
TYPE_FROM_THE_ADDRESS_TEXT_FLOOR = 300


def _delivered_text_length(evidence):
    """How many visible characters the server sent for this page.

    `body_text` is the last fallback rather than a zero, for the reason
    `render-readability-audit::_delivered_text_len` gives: a hand-written
    record can carry the extracted copy and no whole-document count, and
    reading that as zero would call a real page a container on the strength of
    a field nobody wrote. The copy is a subset of the visible text, so it is a
    floor.
    """
    delivered = (evidence.get("spa_shell") or {}).get("visible_text_len")
    if delivered is None:
        delivered = evidence.get("delivered_text_len")
    if delivered is None:
        delivered = len((evidence.get("body_text") or "").strip())
    return int(delivered)


# Types that describe the site rather than this page, so a block carrying one
# says nothing about which page it arrived on.
#
# `Organization`, `WebSite` and the `SearchAction` inside a `WebSite`'s
# `potentialAction` go in the template every page shares. Measured, that costs
# this much: `_nothing_was_delivered` returned `False` the moment
# `jsonld_types` held anything, and every shell page on the site under test
# shipped exactly `['organization', 'searchaction', 'website']` in its `<head>`,
# so the address-based promotion below - written for that site's shape - was
# dead code on it. Product markup was assessed on 2 of 13 product pages, and on
# a second storefront 0 of 3 `/product/` URLs typed `product` at all.
#
# A page that declares `Product`, `Article` or `FAQPage` has said what it is
# and this rule leaves it alone. A page that declares only its publisher has
# not.
_SITE_WIDE_JSONLD_TYPES = frozenset({
    "organization", "website", "searchaction", "webpage",
    "sitenavigationelement", "wpheader", "wpfooter", "wpsidebar",
})


def _page_own_jsonld_types(evidence):
    """The declared types that describe this page rather than the whole site.

    `site_wide_jsonld_types` is the measured set - the types every crawled page
    carries - for a caller that has seen the whole crawl. The extractor runs a
    page at a time and has not, so the constant above stands in for it: those
    are the types whose subject is the publisher by definition, which is the
    same reading arrived at without needing the site.
    """
    measured = {str(t).lower() for t in (evidence.get("site_wide_jsonld_types") or ())}
    return {str(t).lower() for t in (evidence.get("jsonld_types") or ())} - (
        _SITE_WIDE_JSONLD_TYPES | measured)


def _nothing_was_delivered(evidence):
    """Did this document arrive with nothing in it to decide a page type from?

    Three questions, and all three have to answer nothing, because each of them
    is a way a page can say what it is: prose, a heading, a declaration in
    markup. A shell answers none of them - which is the whole of the case for
    letting its address decide - and a shelf answers at least one, because 36
    tiles carry 36 link labels and a heading over them.

    The markup question is asked of the types that describe *this page*. See
    `_SITE_WIDE_JSONLD_TYPES`: a block naming the publisher rides in the
    template on every page a site has, so reading it as this page's own
    declaration made the whole rule unreachable on any site that ships one.
    """
    headings = evidence.get("headings") or {}
    if any(headings.get(level) for level in ("h1", "h2", "h3")):
        return False
    if _page_own_jsonld_types(evidence):
        return False
    return _delivered_text_length(evidence) < TYPE_FROM_THE_ADDRESS_TEXT_FLOOR


def retype_from_the_page_itself(page_type, url, evidence):
    """`(page_type, why)` - a listing the page's own content says is one item.

    The rule, in order: type on evidence in the page (an add-to-cart control,
    a single price, a single subject) before falling back to URL path. The
    address still leads, because on most sites it is the
    strongest signal there is and a slug that names a section is rarely wrong.
    What this answers is the case the classifier had no rule for - the address
    and the page disagreeing - and it answers it with the page.

    Four conditions, all of them required, and the shape of the failure this
    has to avoid is why. An earlier fix addressed the opposite defect: a grid of
    36 links to item pages was typed `product` and told to publish `Product`
    markup, which would declare a whole shelf to be one item at one price.

      one control, or an offer declared   a shelf with quick-add carries one
                                          control per tile, so the count is
                                          what separates them, not the presence
      fewer prices than a shelf shows     `DISTINCT_PRICE_CEILING`, the shared
                                          measured separator - 11 of 11 detail
                                          pages kept, 29 of 29 listings dropped
      it does not read as an index        `is_listing_page`, unchanged and
                                          shared, so this cannot disagree with
                                          the six checks that already ask it
      it names one subject                one H1, and not one that is also the
                                          label of a link leaving the page

    Nothing here needs a word in the site's language. The control is normally
    an identifier in the markup, the price is a currency reading that already
    covers the notations of the markets this audit meets, the index test reads
    link labels against headings, and the subject test reads how many headings
    there are rather than what they say. The one route that does read a word -
    a buy button whose only clue is the label on it, see `_CART_LABEL_RE` -
    can only add a control, so a shop trading in a language this audit cannot
    read is typed exactly as well as it was before.
    """
    if page_type not in _TYPES_THE_CONTENT_MAY_OVERTURN:
        return page_type, ""
    # The site's own address scheme saying "everything filed under this
    # heading", and the address of a typed query. Both are the site stating
    # that the page is a list, and both already outrank content everywhere
    # else in this marketplace - `/collections/<handle>` renders its items'
    # prices and their quick-buy buttons, which is exactly what a content
    # reading sees.
    if is_faceted_listing(url) or is_search_result_page(url):
        return page_type, ""
    path = (urlparse(url).path or "/").rstrip("/").lower()
    segments = [s for s in path.split("/") if s]
    if not segments or segments[-1] in _SECTION_SEGMENTS:
        return page_type, ""

    # The page that delivered nothing to type from.
    #
    # Everything below this line reads the page: a buy control, a price, a
    # heading, the links leaving it. A React storefront delivers a 21 KB shell
    # - no heading, no structured data, no prose - and there is nothing there
    # to read. `detect_page_type` demotes `/product/<slug>` to `category` on
    # that page because it finds no price on it, which is an argument from
    # absence: the page did not contradict its address, it said nothing at all.
    # Measured on the site that prompted this, 0 of 4 `/product/<slug>` URLs were typed
    # `product`, all four came out `category`, and two things followed - a
    # finding whose evidence read "Seen on 4 of the 4 category pages this crawl
    # read" while every URL it printed was a `/product/` one, and the
    # suppression of the most valuable recommendation that site could be given,
    # through a decline reading "no product detail pages were detected on this
    # site" in a report listing four `/product/` addresses.
    #
    # So: where the page delivers nothing, the address settles the type,
    # because it is the only evidence there is. This can only ever promote an
    # address that already names one item - see `the_address_names_one_item`,
    # which refuses the grouping word on its own, a faceted listing and a
    # search address - and it cannot reach a page with a grid on it, because a
    # grid of 36 tiles delivers 36 links and a heading.
    if _nothing_was_delivered(evidence) and the_address_names_one_item(url):
        return "product", (
            "its address names one item under `/{}/` and the page delivered "
            "nothing to read - {} visible characters, no heading and no "
            "structured data - so the address is the only evidence there "
            "is".format(the_item_grouping_word(url),
                        _delivered_text_length(evidence)))

    controls = (evidence.get("add_to_cart_controls") or {}).get("count", 0)
    if controls > 1:
        return page_type, ""
    declared = _declares_one_offer(evidence.get("meta") or {})
    if not controls and not declared:
        return page_type, ""

    priced = _distinct_prices(evidence.get("body_text") or "")
    if priced >= DISTINCT_PRICE_CEILING:
        return page_type, ""
    if is_listing_page({"url": url,
                        "headings": evidence.get("headings") or {},
                        "links": evidence.get("links") or {},
                        "jsonld_types": list(evidence.get("jsonld_types") or [])}):
        return page_type, ""
    subject = _the_one_subject(evidence.get("headings") or {},
                               evidence.get("links") or {},
                               evidence.get("title") or "")
    if not subject:
        return page_type, ""

    if controls:
        control = ((evidence.get("add_to_cart_controls") or {}).get("evidence") or [""])[0]
        said = "it carries one add-to-basket control in its markup ({})".format(control)
    else:
        said = declared
    return "product", ("its address reads as a listing, and {}, names one subject "
                       "(\"{}\") and prints {}".format(
                           said, truncate(subject, 80),
                           "no price" if not priced else
                           "{} price{}".format(priced, "" if priced == 1 else "s")))


# --------------------------------------------------------------------------

def extract_page(url, final_url, status, headers, html, redirect_chain, elapsed_ms,
                 depth, source, origin):
    """Build the snapshot record for one page."""
    soup = make_soup(html)
    body = soup.body or soup
    # The document with its `<select>` menus and its code samples taken out,
    # which is what every reading of the page *as prose* below starts from.
    # See `_without_form_choices`: a menu's labels are choices, and read as
    # sentences forty size codes are one unreadable block. The labels
    # themselves are kept, in `control_text`. See `_without_code_samples` for
    # the second removal: a dataframe library's ASCII output tables were
    # sampled out of `body_text` as the page's longest sentences, and a Python
    # comment inside a demo snippet was reported as evergreen copy claiming
    # currency as of 2024. The samples themselves are kept, in `code_text`.
    # Last of the three removals, and after the code samples deliberately: a
    # page that displays code has had it taken out by then, so the source test
    # cannot reach a documentation page's own writing. See
    # `_SCRIPT_SOURCE_MARKS` for what leaks and how - an unescaped `</script>`
    # inside a JavaScript string ends the element, and the rest of the source
    # becomes ordinary text that no removal of `<script>` elements can find.
    prose = _without_leaked_script(_without_code_samples(_without_form_choices(soup)))
    # One copy with the page's own hidden markup removed, for everything
    # that reads the page as prose. See `visible_soup` for what it cost not
    # to have this: thirteen rows of one report quoting a hidden modal.
    shown = visible_soup(prose)

    page_text = visible_text(soup)
    # The same document with reader comments, reviews and testimonials taken
    # out, but the chrome left in. Contact details and dates are read from
    # this: a footer telephone number is the site's, a commenter's is not.
    ours = without_other_peoples_words(soup)
    own_page_text = visible_text(ours) if ours is not soup else page_text
    # One cleaned copy, used for the body text and for the readability
    # statistics measured on it.
    content = content_soup(prose)
    content_text = tidy_spacing(re.sub(r"\s+", " ",
                                       content.get_text(separator=" ")).strip())
    body_text, body_region = main_text_and_region(prose, prepared=content_text)
    # What the words were counted in, so what is counted about them is counted
    # in the same place.
    measured = content if body_region is None else content_soup(body_region)
    jsonld, jsonld_errors = _extract_jsonld(soup)
    jsonld_types = _jsonld_types(jsonld)
    headings = _headings(shown)
    # The same headings read from the whole document, hidden ones included.
    #
    # `shown` is right for everything that reads the page as prose - a modal's
    # H2 is not a section of the article. It is wrong for asking whether the
    # markup carries a heading at all, because a search engine and an answer
    # engine read the DOM, and CMS themes routinely ship a screen-reader-only
    # `<h1 style="display:none">` naming the page exactly as a machine wants.
    # Two reports said "2 of 59 crawled pages have no H1" and "1 of 57 crawled
    # pages have no H1" about pages whose H1 is in the markup and hidden with
    # CSS, and the finding's `checked[]` said it had read "the h1 elements on
    # every crawled page" without disclosing that it had dropped those. The
    # `heading_sequence` note below records the same disagreement from the
    # other side: it reads the original tree and saw the H1 the count missed.
    headings_in_markup = _headings(soup)

    meta = _meta_tags(soup)
    og = {k: v for k, v in meta.items() if k.startswith("og:")}
    twitter = {k: v for k, v in meta.items() if k.startswith("twitter:")}

    links = _links(soup, final_url, origin)
    scripts = _scripts(soup, final_url)
    images = _images(soup, final_url)

    title = _document_title(soup)
    lang = (soup.html.get("lang") if soup.html else "") or ""
    # Read once and used twice: the record publishes it, and the platform
    # signals below take the framework's mount point and state blob out of it
    # rather than searching the document for them a second time.
    spa_shell = _spa_shell(soup, html, page_text)
    # The labels inside the page's `<select>` menus, read once for the same
    # reason: the record publishes them and the variant prices are read out of
    # them without a second pass over the document.
    control_text = _control_text(soup)
    # The code samples the page shows, read from the original document for the
    # same reason and published for the same one: a check that wants to know
    # whether this page carries code has to be able to see it, now that no
    # prose field does.
    code_text = _code_text(soup)
    # The hosts this page loads code from go into the profile reading, because
    # the host-keyed fallbacks in `_off_site_profiles` are the only way a host
    # no platform list recognises becomes a "profile" at all - and a host the
    # page only ever fetches a script from is the platform this site is built
    # on, not a place the brand keeps an account. `_scripts` already collected
    # them above, in this same call, so nothing extra is measured;
    # `subresource_domains` only reads them, and widens `cdn.<platform>` to
    # `<platform>` so the two names one platform goes by are one thing.
    off_site_profiles = _off_site_profiles(
        links["external"], jsonld, soup, final_url,
        subresource_domains(scripts["third_party_hosts"]))
    # Classified before the record is built rather than after it, because the
    # contact facts below need it: whether a street in the body text is the
    # site's own address or somebody else's turns on whether this page is
    # about the site. Same call on the same inputs as before, moved earlier.
    # The links the page's own content carries, for the two readings that ask
    # whether this page is a shelf. Both compare the page's headings against
    # the labels of the links leaving it, and the site's menu answers for the
    # whole site rather than for this page - see `links["main_internal_links"]`.
    # The record still publishes every link; only the typing question is asked
    # of the content region.
    typing_links = {"internal": links["main_internal_links"],
                    "external": links["external"]}
    page_type = detect_page_type(final_url, {
        "text": body_text,
        "headings": headings,
        "jsonld_types": jsonld_types,
        # So the classifier can ask whether the headings on this page are the
        # labels of links leaving it, which is what an index looks like from a
        # snapshot and what no list of URL words can be complete about.
        "links": typing_links,
        "title": title,
        # So a localised homepage at `/de/` can be recognised as one, on the
        # page's own declaration rather than on the shape of the path.
        "lang": lang,
    })
    # The buy controls in the markup, read once here because the retyping
    # below counts them and the record publishes them - a check that wants to
    # say "this page has a buy button and no Offer markup" needs to see them.
    add_to_cart_controls = _add_to_cart_controls(soup)
    # Where the address and the page disagree about which of the two this is,
    # the page settles it. See `retype_from_the_page_itself`: on a headless
    # storefront serving its items under `/shop/<handle>` this is the only
    # thing standing between eleven product pages and being typed `category`.
    page_type, page_type_evidence = retype_from_the_page_itself(
        page_type, final_url,
        {"add_to_cart_controls": add_to_cart_controls, "meta": meta,
         "body_text": body_text, "headings": headings, "links": typing_links,
         "jsonld_types": jsonld_types, "title": title,
         # The whole delivered document's own count, so the "did this page
         # deliver anything" question is answered on what the server sent and
         # not on what is left after the chrome is stripped out. See
         # `_delivered_text_length`.
         "spa_shell": spa_shell})

    record = {
        "url": url,
        "final_url": final_url,
        "status": status,
        "redirect_chain": redirect_chain,
        "elapsed_ms": elapsed_ms,
        "depth": depth,
        "source": source,
        "content_type": (headers.get("content-type") or "").split(";")[0].strip().lower(),
        "headers": {
            k: v for k, v in headers.items()
            if k.lower() in ("content-type", "x-robots-tag", "last-modified", "server",
                             "cache-control", "content-encoding", "cf-cache-status")
        },
        "x_robots_tag": headers.get("x-robots-tag", ""),
        "html_len": len(html or ""),
        "title": title,
        "page_type": page_type,
        # Empty unless the page's own content overturned what its address
        # said. A type is an assertion every downstream check is keyed to, and
        # a reader whose page was called something else is entitled to the
        # sentence saying which measurement did it.
        "page_type_evidence": page_type_evidence,
        # The add-to-basket controls in the markup, and how many. Read from
        # attribute values rather than from link labels, so it says the same
        # thing on a shop trading in any language - see `_ADD_TO_CART_RE`.
        "add_to_cart_controls": add_to_cart_controls,
        # Where the page says its own feed is. A school declares
        # `<link rel="alternate" type="application/rss+xml" href=".../blog.rss">`
        # and links it from nowhere else, so the crawl never fetched it - and
        # the staleness check, which reads a feed to avoid calling an active
        # blog dead, had nothing to read. It then reported a library whose
        # newest post was four months old as not updated in over a year.
        "feed_urls": _feed_links(soup, final_url),
        "meta_description": meta.get("description", ""),
        "meta_robots": meta.get("robots", ""),
        # Per-crawler directives, kept apart from the generic tag so the
        # finding can say which one it read rather than implying a `robots`
        # tag that is not there.
        "meta_robots_by_agent": {k: meta[k] for k in ROBOTS_META_KEYS[1:] if meta.get(k)},
        "canonical": _canonical(soup, final_url),
        # `<link rel="next">` - the pagination a crawler follows without
        # pressing a button. A shop's collection page declared it, and was
        # told to add it, because nothing recorded it.
        "rel_next": _rel_next(soup, final_url),
        "meta_refresh": _meta_refresh(soup, final_url),
        # The page's own `<link rel="alternate" hreflang>` declarations, and a
        # script that sends the page to a fixed address on the same site. The
        # crawl follows both - see `_hreflang_alternates` - and the `hreflang`
        # check reads the first as the page declaring its editions.
        "hreflang": _hreflang_alternates(soup, final_url),
        "script_redirect": _script_redirect(soup, final_url, origin),
        "lang": lang,
        "has_viewport": "viewport" in meta,
        "og": og,
        "twitter": twitter,
        "headings": headings,
        "headings_in_markup": headings_in_markup,
        # From the same document `headings` came from. Read from the original
        # tree, an `aria-hidden` H1 was absent from `headings["h1"]` - so the
        # page was reported as having no H1 - while `heading_sequence` opened
        # with a level 1 and reported the outline as correct. One page, two
        # answers, in one record.
        "heading_sequence": _heading_sequence(shown),
        "sections": _sections(shown),
        "jsonld": jsonld,
        "jsonld_errors": jsonld_errors,
        "jsonld_types": jsonld_types,
        "microdata_count": len(soup.select("[itemscope]")),
        # The types, not only the count. Five checks in structured-data-audit
        # could rule a schema type out from JSON-LD alone and had nothing to
        # rule it in with, so each of them asserted absence on one detector -
        # and a site that marks its facts up as Microdata rather than JSON-LD
        # was told at high confidence that it carries no Organization markup
        # at all. A typed second reading turns that into a measurement.
        "microdata_types": _typed_markup(soup),
        # The items themselves, not only their type names. A check that
        # grades properties needs the properties.
        "microdata_items": _inline_items(soup, final_url),
        # `[property]` matches every `<meta property="og:...">` tag, so this
        # count was above zero on any page carrying Open Graph tags and could
        # never distinguish RDFa from a social card. Only the attributes that
        # RDFa alone uses are counted.
        "rdfa_count": len(soup.select("[typeof], [vocab]")),
        "text": page_text,
        "text_len": len(page_text),
        "body_text": body_text,
        "body_text_len": len(body_text),
        "word_count": word_count(body_text),
        "above_fold_text": truncate(body_text, 1500),
        # From the content copy, not the whole visible document. The report's
        # "what an assistant would quote" table offered a documentation site
        # the sentence "Skip to main content ... get PyCharm for 30% off!" as
        # its best line: a skip link and a sponsor's banner, which is what the
        # page says to a reader who is not reading it.
        "paragraphs": _paragraphs(content),
        # Every passage the page writes, the chrome included and the site-wide
        # repeats kept. `paragraphs` is emptied by the crawl's boilerplate
        # strip, so a homepage whose only definition of itself is a footer
        # tagline reports none - see `_prose_blocks`. A definition reading uses
        # this as its second source and takes each entry as a sentence of its
        # own; `some_subject_is_defined` then says which subject the sentence
        # defines, so a sentence about something other than this site can be
        # declined rather than quoted back as the brand's identity.
        "prose_blocks": _prose_blocks(shown),
        "links": links,
        "scripts": scripts,
        "images": images,
        "forms": _forms(soup, origin),
        "iframes": _iframes(soup, final_url),
        "video": _video(soup, page_text, final_url),
        "pdf_links": _pdf_links(soup, final_url),
        "spa_shell": spa_shell,
        # The words the page would render, read out of its own state blob.
        # Only when the delivered document is thin: on an ordinary page the
        # body text is the answer and a script's strings are noise.
        "shell_state_text": (_shell_state_text(soup)
                             if len(body_text) < SHELL_STATE_TEXT_FLOOR else ""),
        # The choices a page offers in its `<select>` menus. Kept apart from
        # `body_text` for the same reason `shell_state_text` is - see
        # `_control_text`.
        "control_text": control_text,
        # The text of this page's code samples - block `<pre>`, and `<code>`
        # that stands on its own - kept out of `body_text`, `word_count`,
        # `above_fold_text`, `paragraphs`, `sections` and `readability`
        # exactly as `control_text` is, and for the same reason. A sample is
        # text the page shows, not text the page says: read as prose, a
        # dataframe library's ASCII output tables became the page's longest
        # sentences and a Python comment in a demo snippet became evergreen
        # copy claiming to be current as of 2024. A check that asks whether
        # the page carries code reads this field.
        "code_text": code_text,
        "code_text_len": len(code_text),
        "code_sample_count": len(_code_samples(soup)),
        # What this page is built with, for `audit_common.publishing_platform`.
        # Evidence only - hostnames, path shapes, header and cookie *names* -
        # and no reading of it happens here, so one page's signals stay
        # arguable in the snapshot rather than becoming a verdict nobody can
        # check.
        "platform_signals": _platform_signals(soup, meta, headers, final_url, spa_shell),
        "challenge": detect_challenge(html, page_text, status, headers),
        "noscript_text": truncate(" ".join(n.get_text(" ") for n in soup.select("noscript")), 400),
        "breadcrumb": _breadcrumb(soup, jsonld_types),
        "has_search": _has_search(soup, origin),
        "cta": _cta(soup, body_text, origin, final_url),
        # The declared language and the writing system the page's own letters
        # are in, both of them for one question: whether a 2400-2600 number is
        # a Buddhist-era year. Sampled at 4,000 characters, which is what
        # `written_in_the_same_script` samples - a page is in the script its
        # first screens are in, and walking sixty full page texts a second time
        # buys nothing.
        "dates": _dates(ours, own_page_text, jsonld, lang=lang,
                        script=dominant_script(own_page_text[:4000]) or "",
                        chrome_text=_site_chrome_text(ours)),
        "contact_facts": _contact_facts(own_page_text, ours, page_type),
        "prices": find_prices(body_text),
        # The prices written inside the page's `<select>` menus, and only
        # those. A separate field rather than a wider `prices`, because
        # `prices` answers "where in the copy is the price" - the landing-page
        # check locates it with `body_text.find` and the shelf-versus-detail
        # rule counts how many different amounts the copy prints. Folding forty
        # variant prices into either would drop the page from the first
        # measurement and turn every product detail page on a storefront into a
        # category listing in the second. `audit_common.prices_the_page_states`
        # is the reading that wants both, and it is the only place they meet.
        "control_prices": find_prices(control_text, limit=40),
        # The one list, and the two views of it. Building the views from it
        # rather than from two separate readings of the page is what stops one
        # page holding two answers to one question - see `_off_site_profiles`
        # for the report that stated two numbers for one fact and then told the
        # owner to go and find more accounts than it already had.
        "off_site_profiles": off_site_profiles,
        "social_profiles": _profiles_by_platform(off_site_profiles),
        "declared_profiles": _profiles_by_platform(off_site_profiles,
                                                   declared_only=True),
        "interstitial": _interstitial(soup),
        "newsletter_signup": _newsletter(soup, page_text),
        # The paragraph and subheading counts are about how the page is
        # written, so they are measured on what the page wrote. Counting the
        # `<p>` tags of the whole document made a recipe post's 549 reader
        # comments into five paragraphs over 200 words, and the page was
        # reported as written in blocks too large to skim.
        "readability": _readability(body_text, measured),
        "chrome_signature": _chrome_signature(soup, origin, final_url),
    }

    # Last, because it reads the finished record rather than the document: the
    # directives, the text, the links and the shell signals are all already
    # measured above and it only decides what they add up to.
    _mark_if_not_gradable(record)
    return record


# --------------------------------------------------------------------------
# Field extractors
# --------------------------------------------------------------------------

def _text_or_empty(node):
    # Same normalisation as `visible_text` and `main_text`, and it has to be
    # the same. When only those two tidied the separator spacing, a paragraph
    # still read "a small , fast , engine" while the page text read "a small,
    # fast, engine" - so the definition sentence drawn from the paragraph was
    # not found in the page, the invented-value guard called a real sentence a
    # guess, and a correct description became a placeholder. Two normalisers
    # for one string is one too many.
    return tidy_spacing(re.sub(r"\s+", " ", node.get_text(" ")).strip()) if node else ""


def collapse_whitespace(value):
    """One string with its line breaks and runs of spaces folded to one space.

    For the strings a page hands over as *attribute or JSON values* rather than
    as document text: a `<meta content>`, a JSON-LD `name`. A real title read
    `'\\n  <product> by <brand>\\n\\n\\n'` - a template writing a
    variable across three lines of its own source - and whatever reads that
    string gets the whitespace. The `<title>` element itself never could: it
    goes through `_text_or_empty` above, which folds it. The two other places a
    page states its own name did not, so the fault survived into the snapshot,
    into the comparisons every check makes on those strings, and into the
    paste-ready snippets that quote them back at the owner.

    Deliberately *not* `tidy_spacing`. That repairs the separator spaces
    `get_text(" ")` inserts around inline markup, and there are none here -
    running it over an attribute value would silently edit punctuation the site
    itself wrote, which is the opposite of what a record of what a site
    published is for. Folding whitespace changes no character the site chose;
    it only stops one string being two.
    """
    return re.sub(r"\s+", " ", value).strip() if isinstance(value, str) else value


# How deep into a JSON-LD block the fold above will walk. A block nested past
# this is a block `json.loads` itself nearly refused - see the RecursionError
# arm in `_extract_jsonld` - and the whole point of that arm is that one
# malformed block never ends a run, so this returns the value untouched rather
# than raising in the same place for a second reason.
_JSONLD_FOLD_MAX_DEPTH = 40


def _folded_jsonld(value, depth=0):
    """A parsed JSON-LD value with the whitespace folded in every string leaf.

    Keys are left exactly as the site wrote them: a property name carrying a
    newline is a broken property name, which is a fact `jsonld-validity` is
    entitled to see rather than one this function should quietly repair.
    """
    if depth > _JSONLD_FOLD_MAX_DEPTH:
        return value
    if isinstance(value, str):
        return collapse_whitespace(value)
    if isinstance(value, list):
        return [_folded_jsonld(item, depth + 1) for item in value]
    if isinstance(value, dict):
        return {key: _folded_jsonld(item, depth + 1) for key, item in value.items()}
    return value


# Crawler directives are the one meta family where a second tag must not be
# discarded. Google combines every robots tag on a page and applies the most
# restrictive directive it finds; first-wins hid exactly the shape that
# produces: a theme writes `index,follow` into the head and a plugin appends
# `noindex` further down. The page is not indexed, and a first-wins read
# reported it as indexable - a missed critical, and the one finding in this
# skill whose whole point is that the page will never be cited.
#
# The per-crawler names are read too. `<meta name="googlebot" content="noindex">`
# with no `robots` tag beside it was stored and consulted by nothing.
ROBOTS_META_KEYS = ("robots", "googlebot", "googlebot-news", "bingbot")

# The directives that say "do not keep this page". `none` is the shorthand for
# `noindex, nofollow` and is honoured identically, so a page carrying it is
# saying the same thing in fewer words. Bounded by a character class rather
# than `\b` on both sides so `noindexing` and `index-none` cannot match.
NOINDEX_DIRECTIVE_RE = re.compile(r"(?<![\w-])(?:noindex|none)(?![\w-])", re.I)


def declared_directives(record):
    """Every crawler directive this page carries, as one lower-case string.

    Three places one can live and a page needs only one of them: the generic
    `<meta name="robots">` tag, a per-crawler tag such as
    `<meta name="googlebot">`, and the `X-Robots-Tag` response header. Read
    together here so the four readers of this question - the not-gradable
    marking below, and `crawl-access-audit`'s noindex, canonical and
    meta-refresh checks - cannot disagree about which of the three they looked
    at.
    """
    by_agent = record.get("meta_robots_by_agent") or {}
    # The header is read twice on purpose. `x_robots_tag` is written by the
    # record builder below, and four of the records in a snapshot are not built
    # there: `crawl.py` returns early for a redirect off the origin, a
    # non-HTML content type and a body it could not decompress, and each of
    # those carries `headers` and no `x_robots_tag` key at all. A URL answering
    # 200 with `Content-Type: application/json` and `X-Robots-Tag: noindex` is
    # exactly the shape this question is asked about, and read through the
    # written field alone it answered no.
    headers = record.get("headers") or {}
    return " ".join([
        str(record.get("meta_robots") or ""),
        " ".join(str(v) for v in by_agent.values()),
        str(record.get("x_robots_tag") or headers.get("x-robots-tag") or ""),
    ]).lower()


def says_noindex(record):
    """Has this page told every crawler not to keep it?"""
    return bool(NOINDEX_DIRECTIVE_RE.search(declared_directives(record)))


def declares_noindex(record):
    """The one answer to "does this page tell crawlers to discard it".

    Call this rather than reading `record["declares_noindex"]`, and rather than
    re-deriving the answer from `meta_robots`, `meta_robots_by_agent` and
    `x_robots_tag`. Two readers deriving one fact is how this went wrong: a page
    shipping `<meta name="robots" content="noindex,nofollow">` was named in a
    finding about missing meta descriptions and counted in one about missing
    `og:title`, while the check whose whole purpose is to report the directive
    said "no crawled content page carries a noindex directive". One reader
    filtered on `page_type in CONTENT_TYPES` and the other did not, and the
    page was typed `other`, so it fell between the two rules.

    The record `extract_page` builds carries the flag already, stamped before
    anything else is decided about the page. The fallback measures it, so a
    record `crawl.py` returned early with - a redirect off the origin, a
    non-HTML content type, a body that would not decompress - answers the same
    question the same way instead of answering it by omission.
    """
    flag = record.get("declares_noindex")
    return says_noindex(record) if flag is None else bool(flag)


# Body text below which a page that also carries a noindex has nothing on it
# to grade.
#
# 300, which is the bar the thin-content finding itself fired at on the two
# URLs this whole rule exists for: "under 300 characters of body text" was one
# of the five findings that appeared about them. A page under it holds a
# heading and some chrome and no passage anything downstream would quote.
NOT_GRADABLE_TEXT_FLOOR = 300


def _mark_if_not_gradable(record):
    """Set `not_gradable` on a URL that answered 200 and is still not a page.

    Two shapes, both measured on one site. A town library runs an events
    calendar whose iCalendar export endpoints answer 200 with
    `Content-Type: text/html`, an empty body and
    `X-Robots-Tag: noindex, nofollow`. Two sibling exports that answered
    `text/calendar` were correctly dropped by the content-type rule; these two
    were not, were typed `category`, and five findings then fired on them -
    under 300 characters of body text, no navigation onward, no `lang`, no
    viewport, few of the site's own links. Six of the ten false positives
    counted across six sites in one validation pass came from this, five of
    them here.

    A page the owner tells every crawler to discard is not a page an audit of
    what assistants can quote should grade: nothing an assistant reads will
    ever come from it. That the directive is there at all is worth reporting,
    and `crawl-access-audit` reports it - see `pages_including_noindexed` in
    that skill, which reads these records back out. Nothing else counts them.

    `declares_noindex` is set on every record so that the reporting half and
    this half read one answer to one question. They did not: a fixture page
    shipping `<meta name="robots" content="noindex,nofollow">` was typed
    `other`, held enough text to keep it out of the arm below, and so was
    graded - named in a finding about missing meta descriptions and counted in
    one about missing `og:title` - while the check whose whole purpose is to
    report the directive said "no crawled content page carries a noindex
    directive". The hygiene checks read every page `pages_of` returns; the
    noindex check read `page_type in CONTENT_TYPES`, and `other` is in the
    first set and not the second, so one page fell between the two rules. The
    sentence above used to promise that the directive is reported whatever the
    page; it is a promise this file cannot keep on its own, and the field is
    what lets the check keep it - the reporting filter has to be the directive
    and gradability, never the page type.

    What the record guarantees, so that the two halves cannot drift again:

    - `declares_noindex` is on every record this function sees, set before any
      other decision and from the directives alone. `declares_noindex(record)`
      is the reader, and it measures the answer for a record `crawl.py`
      returned early with, so there is one answer whatever built the record.
    - `robots_directives` holds what the page actually said, joined from the
      three places a directive can live, so nothing downstream reassembles it.
    - Whether the page is set aside is decided by its directives, its text and
      its links. `page_type` is not read here and must not be: it is the field
      the two rules disagreed on.
    - So a page is either set aside - `skipped`, which `audit_common.pages_of`
      drops, so it leaves every check's population at once - or it is in every
      check's population. There is no third state, and the directive is on the
      record to be reported in both.

    `skipped` is what carries the exclusion, because `audit_common.pages_of`
    already drops a skipped record and every other check reads its pages
    through that one function. `not_gradable` and `not_gradable_reason` are
    beside it so a reader can tell this apart from a redirect off the origin
    or a body that would not decompress, which are the other two things
    `skipped` means.

    Why the noindex arm also asks whether the page holds anything: `skipped`
    is all-or-nothing, and it takes the page's *links* out of the site graph
    along with its content. Measured on this repository's own fixture, whose
    `/pricing.html` is the only page linking `/products/...`: marking a
    noindexed `/pricing.html` set aside made the product page "listed in the
    sitemap but linked from nowhere", a new false positive in place of the six
    removed. A `noindex` is not a `nofollow`, and a page with content on it and
    links out of it is still part of how the site is put together. So the arm
    covers the pages the finding was actually made about - the ones carrying
    the directive *and* nothing of their own - and a substantial noindexed page
    stays in the graph and is still reported by `noindex-on-content-pages`.
    """
    # First, and on every record including one already set aside: the question
    # "does this page tell crawlers to discard it" has one answer, and three
    # readers were each deriving it from the three raw fields themselves.
    #
    # Both fields are written before anything below can return, and neither
    # reads `page_type`, so the record's answer to "what did this page tell
    # crawlers" is the same whatever the classifier made of the page. That is
    # the half of the noindex disagreement this file owns: the two rules that
    # disagreed - one
    # keyed on the page type, one not - can only be made to agree by there
    # being one fact for both of them to read.
    record["declares_noindex"] = says_noindex(record)
    # And what it said, so a reader reporting it can quote the page rather
    # than the audit's word for it, and so no check has to reassemble the
    # string out of the three fields it is spread across. `crawl-access-audit`
    # was doing exactly that, in a private copy of the join above.
    record["robots_directives"] = declared_directives(record).strip()
    if record.get("status") != 200 or record.get("skipped"):
        return
    # A 200 that delivered nothing. An export endpoint, a tracking beacon or a
    # stub route answers this way, and every content check reads it as a page
    # the site shipped empty. The JavaScript-shell test is the guard that keeps
    # a real single-page application here: a shell delivers no text either, and
    # a shell is the site, reported as `js-shell` by `render-readability-audit`.
    shell = record.get("spa_shell") or {}
    links = record.get("links") or {}
    is_shell = bool(shell.get("root_selector") or shell.get("state_blobs")
                    or shell.get("noscript_demands_js"))
    delivered_nothing = (
        not (record.get("text") or "").strip()
        and not (record.get("images") or {}).get("count")
        and not links.get("internal")
        and not links.get("external")
    )
    if delivered_nothing and not is_shell:
        record["not_gradable"] = True
        record["not_gradable_reason"] = "empty-body"
        record["skipped"] = ("answered 200 with an empty body, so it is not a page this "
                             "audit grades")
        return
    if is_shell or not says_noindex(record):
        return
    holds_nothing = (
        len((record.get("body_text") or "").strip()) < NOT_GRADABLE_TEXT_FLOOR
        and not links.get("main_internal_count")
    )
    if holds_nothing:
        record["not_gradable"] = True
        record["not_gradable_reason"] = "noindex"
        record["skipped"] = ("carries a noindex directive and no content of its own, so it "
                             "is not a page this audit grades")


def _meta_tags(soup):
    """Every `<meta>` in the document, filed under every name it answers to.

    Every name, not the first attribute that happened to be present. One tag
    can carry two, and this one is ordinary:

        <meta name="twitter:description" property="og:description" content="...">

    Filed under the `name` alone, `og:description` was invisible to every check
    that reads it, and a report said "`og:description` is absent from 60 of 60
    pages" about a tag the reader can see in View Source. That is what Jekyll's
    seo-tag plugin emits on every page it renders, so the misfire covers a
    large slice of the static-site web rather than one site.

    A tag addressed to two consumers is a declaration to both of them, and both
    read it, so the record has to hold it under both keys. `setdefault` still
    means the first tag wins each key, which is what it meant before: two tags
    declaring one key is a fault for a check to report, not one to resolve
    here.
    """
    out = {}
    directives = {}
    for tag in soup.find_all("meta"):
        keys = []
        for attribute in ("name", "property", "http-equiv"):
            key = (tag.get(attribute) or "").strip().lower()
            if key and key not in keys:
                keys.append(key)
        if not keys:
            continue
        # Folded, not just stripped. `.strip()` alone cleared the ends and left
        # the middle, so a template writing its title variable across three
        # lines of its own source shipped the line breaks into `og:title`, into
        # `meta_description`, and into every comparison and snippet built on
        # them. See `collapse_whitespace`.
        content = collapse_whitespace(tag.get("content") or "")
        for key in keys:
            if key in ROBOTS_META_KEYS:
                if content and content not in directives.setdefault(key, []):
                    directives[key].append(content)
            out.setdefault(key, content)
    for key, values in directives.items():
        out[key] = ", ".join(values)
    return out


_META_REFRESH_RE = re.compile(r"^\s*(\d+)\s*;\s*url\s*=\s*(.+?)\s*$", re.I)


def _meta_refresh(soup, base):
    """A client-side redirect: `<meta http-equiv="refresh" content="0; url=...">`.

    Worth recording for two reasons. A server-side crawler that does not follow
    it audits a stub - one real site answered its homepage with 216 bytes and a
    refresh, and the audit dutifully reported that it had no heading, no
    navigation and no call to action, all of which were true of the stub and
    none of which were true of the site. And a consumer that does not follow it
    sees exactly what we saw, so it is a finding in its own right.
    """
    tag = soup.find("meta", attrs={"http-equiv": re.compile(r"^refresh$", re.I)})
    if not tag or not tag.get("content"):
        return None
    # A refresh inside `<noscript>` is the opposite of a redirect: it is what
    # the page does for the minority of consumers that cannot run JavaScript,
    # and following it means auditing the fallback instead of the site.
    #
    # A language-learning site ships one. The crawl followed it, collapsed six
    # distinct seed URLs into a single legacy stub, finished with three pages
    # instead of sixty, and the report then told the owner to make the redirect
    # permanent - which would send every visitor and every crawler to the stub
    # for good, degrading a homepage that works. Five of that report's eight
    # findings trace back to this one tag.
    if tag.find_parent("noscript") is not None:
        return None
    match = _META_REFRESH_RE.match(tag["content"])
    if not match:
        return None
    target = normalise_url(match.group(2).strip("'\""), base)
    if not target:
        return None
    return {"delay": int(match.group(1)), "url": target}


def _hreflang_alternates(soup, base):
    """`[{"hreflang": code, "url": address}]` from `<link rel="alternate" hreflang>`.

    The page's own statement of where its other language editions live. A
    one-page site answered `/` with an empty document holding eighteen of these
    and a script that sends the browser on; the crawl read neither, audited
    one empty page, and every edition the site declared went unread.
    """
    out, seen = [], set()
    for link in soup.find_all("link", hreflang=True):
        rel = link.get("rel") or []
        rel = [r.lower() for r in (rel if isinstance(rel, list) else [rel])]
        if "alternate" not in rel:
            continue
        url = normalise_url(link.get("href") or "", base)
        code = str(link.get("hreflang") or "").strip().lower()
        if not url or not code or (code, url) in seen:
            continue
        seen.add((code, url))
        out.append({"hreflang": code, "url": url})
        if len(out) >= HREFLANG_ALTERNATES_KEPT:
            break
    return out


# How many alternates a record keeps. Enough for a site in every language it
# is likely to publish; the record is not the place for a matrix.
HREFLANG_ALTERNATES_KEPT = 60

# A script sending the browser to a fixed address: `window.location = "/en/"`,
# `location.href = '/en/'`, `location.replace("/en/")`, `location.assign(...)`.
# Only a quoted literal - a destination computed at run time names nothing a
# crawler can follow, and guessing one would be inventing an address.
_SCRIPT_REDIRECT_RE = re.compile(
    r"""(?:window\.|document\.|self\.|top\.)?location"""
    r"""(?:\.href)?\s*(?:=(?!=)\s*|\.(?:replace|assign)\s*\(\s*)"""
    r"""(["'])([^"'\s<>]{1,300})\1""")

# The same assignment with a destination computed at run time - a variable or
# an expression rather than a quoted address. `location.href = location.href`
# style reloads are excluded by requiring the right-hand side not to be
# `location` itself.
_COMPUTED_REDIRECT_RE = re.compile(
    r"""(?:window\.|document\.|self\.|top\.)?location"""
    r"""(?:\.href)?\s*(?:=(?!=)\s*|\.(?:replace|assign)\s*\(\s*)"""
    r"""(?!(?:window\.|document\.)?location\b)[A-Za-z_$][\w$.\[\]]*""")


def _script_redirect(soup, base, origin):
    """`{"url": address}` where an inline script sends the page to a same-site literal.

    Recorded the way `meta_refresh` is, for the same two reasons: a crawler that
    does not run scripts audits the stub, and a consumer that does not run them
    sees exactly what that crawler saw. Same site only, so a script sending a
    reader to a payment provider or a sign-in host is never followed.
    """
    computed = False
    for script in soup.find_all("script"):
        if script.get("src"):
            continue
        source = script.string or script.get_text() or ""
        match = _SCRIPT_REDIRECT_RE.search(source)
        if match:
            url = normalise_url(match.group(2), base)
            if url and same_site(url, origin or base) and url.split("#")[0] != base.split("#")[0]:
                return {"url": url}
            continue
        if _COMPUTED_REDIRECT_RE.search(source):
            computed = True
    # The destination worked out at run time: a one-page site's root holds
    # eight characters of text and `window.location = destination`, where
    # `destination` is chosen from the reader's language. There is no address
    # to follow, but the page is still a redirect and not a page of thin copy,
    # and the checks reading this record have to be able to say so.
    return {"url": "", "computed": True} if computed else None


def _canonical(soup, base):
    link = soup.find("link", rel=lambda v: v and "canonical" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    if not link or not link.get("href"):
        return ""
    return normalise_url(link["href"], base) or ""


def _rel_next(soup, base):
    """The address a `<link rel="next">` in the head names, or ""."""
    link = soup.find("link", rel=lambda v: v and "next" in [x.lower() for x in (v if isinstance(v, list) else [v])])
    if not link or not link.get("href"):
        return ""
    return normalise_url(link["href"], base) or ""


def _heading_text(tag):
    """The words a heading contributes, including an image's alt text.

    A great many sites mark up the site name as `<h1><a><img alt="..."></a></h1>`
    on the homepage. Read for text alone, that is an empty heading, and a
    postal group's English homepage was listed among pages with no H1 while
    carrying `<h1><a href="/en/"><img alt="Japan Post Holdings"></a></h1>` -
    which is a heading, is machine-readable, and says what the page is.
    """
    text = _text_or_empty(tag)
    if text:
        return text
    alts = [(image.get("alt") or "").strip() for image in tag.find_all("img")]
    return tidy_spacing(" ".join(alt for alt in alts if alt))


def _headings(soup):
    out = {}
    for level in ("h1", "h2", "h3"):
        out[level] = [_heading_text(h) for h in soup.find_all(level) if _heading_text(h)]
    return out


def _heading_sequence(soup):
    """Ordered `(level, text)` pairs, used to spot skipped levels."""
    seq = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = _heading_text(tag)
        if text:
            seq.append([int(tag.name[1]), truncate(text, 120)])
    return seq


# Headings that introduce a list of links rather than an answer. Judging these
# for "does it open with a concrete value" would penalise good practice: a
# related-links block is supposed to be links.
NAVIGATIONAL_HEADING_RE = re.compile(
    r"^\s*(?:related|read next|readnext|see also|further reading|keep reading|more (?:from|on|about)?"
    r"|where to go next|what to read next|next steps?|explore|learn more|useful links?"
    r"|quick links?|elsewhere|other pages?|browse|in this section|on this page|contents?"
    r"|table of contents|share|follow us|categories|tags|archive)\b", re.I)


def _sections(soup):
    """For each H2, the first paragraph beneath it plus how link-heavy it is.

    fact-extractability uses this to ask whether a section opens with a
    concrete value or with warm-up prose. `navigational` marks the sections
    where that question does not apply.
    """
    out = []
    for heading in soup.find_all("h2"):
        title = _text_or_empty(heading)
        if not title:
            continue
        first = ""
        section_text = []
        link_text = []
        for sibling in heading.next_elements:
            name = getattr(sibling, "name", None)
            if name in ("h1", "h2"):
                break
            if name == "a":
                link_text.append(_text_or_empty(sibling))
            if name in ("p", "li", "dd", "td"):
                text = _text_or_empty(sibling)
                section_text.append(text)
                if not first and len(text) >= 40:
                    first = text
        body_len = len(" ".join(section_text))
        link_len = len(" ".join(link_text))
        link_ratio = round(link_len / float(body_len), 2) if body_len else 0.0
        out.append({
            "heading": truncate(title, 160),
            "first_paragraph": truncate(first, 500),
            "link_ratio": link_ratio,
            "navigational": bool(NAVIGATIONAL_HEADING_RE.match(title)) or link_ratio > 0.7,
        })
        if len(out) >= 40:
            break
    return out


def _extract_jsonld(soup):
    """Parse every `application/ld+json` block, recording parse failures.

    A block that fails to parse is worse than a missing one: the site believes
    it has structured data and no consumer can read it.
    """
    blocks, errors = [], []
    for index, tag in enumerate(soup.find_all("script", attrs={"type": re.compile(r"ld\+json", re.I)})):
        raw = tag.string or tag.get_text() or ""
        raw = raw.strip()
        if not raw:
            errors.append({"index": index, "error": "empty <script type=application/ld+json> block"})
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            errors.append({
                "index": index,
                "error": "{} (line {}, col {})".format(exc.msg, exc.lineno, exc.colno),
                "excerpt": truncate(raw, 160),
            })
            continue
        except (RecursionError, ValueError) as exc:
            # `json.loads` on three thousand nested brackets raises
            # RecursionError, which is not a JSONDecodeError, so one malformed
            # block anywhere on one page ended the whole audit with a traceback
            # and no report. A block we cannot read is a finding about that
            # block, never the end of the run.
            errors.append({
                "index": index,
                "error": "could not be parsed: {}".format(type(exc).__name__),
                "excerpt": truncate(raw, 160),
            })
            continue
        # Folded before flattening, so every reader of every node downstream
        # gets one shape of the string. A commerce template writing
        # `"name": "{{ product.title }}"` across three lines of its own source
        # put `'\n  <product> by <brand>\n\n\n'` into the record, and from
        # there into the name comparisons, the duplicate-title counting and
        # the paste-ready snippets that quote the value back at the owner.
        for node in _flatten_jsonld(_folded_jsonld(data)):
            blocks.append(node)
    return blocks, errors


def _flatten_jsonld(data):
    """Flatten `@graph` containers and top-level arrays into a node list."""
    out = []
    if isinstance(data, list):
        for item in data:
            out.extend(_flatten_jsonld(item))
    elif isinstance(data, dict):
        if "@graph" in data and isinstance(data["@graph"], list):
            for item in data["@graph"]:
                out.extend(_flatten_jsonld(item))
            leftovers = {k: v for k, v in data.items() if k != "@graph"}
            if len(leftovers) > 1:
                out.append(leftovers)
        else:
            out.append(data)
            # A node's own values can be nodes. `{"@type": "WebPage",
            # "publisher": {"@type": "Organization", ...}}` is valid and
            # common, and reading only the top level meant the Organization was
            # never seen - so brand detection missed the name and the site was
            # told it declares no Organization markup while declaring one. Same
            # for `Product` nodes inside an `ItemList`.
            for key, value in data.items():
                if key.startswith("@"):
                    continue
                for item in (value if isinstance(value, list) else [value]):
                    if isinstance(item, dict) and item.get("@type"):
                        for nested in _flatten_jsonld(item):
                            # Marked, because a nested node is a different kind
                            # of statement from a top-level one. An Article's
                            # `publisher` names who published the piece; it is
                            # not a claim that this page is about that
                            # organisation. Read as one, every blog post on a
                            # correctly marked-up site was reported at high
                            # severity as structured data contradicting the
                            # page, because the publisher's name appears in the
                            # footer and the footer is site-wide furniture.
                            #
                            # Presence checks want these; agreement checks must
                            # not have them.
                            nested = dict(nested)
                            nested.setdefault("_nested_in", key)
                            # Whose node this hung off. `_nested_in` records
                            # the property name and not the owner, so a
                            # `PostalAddress` inside a partner organisation's
                            # block and one inside the site's own arrive
                            # identical - and a charity's report published a
                            # partner's founding date and address as the
                            # audited brand's, marking the check that would
                            # have reported the real gap as passed. A fact
                            # belongs to whoever the node above it names.
                            parent_name = _prop_text(data.get("name"))
                            if parent_name:
                                nested.setdefault("_parent_name", parent_name)
                            parent_types = sorted(jsonld_type_names(data.get("@type")))
                            if parent_types:
                                nested.setdefault("_parent_type", parent_types[0])
                            out.append(nested)
    return out


def _prop_text(value):
    """A schema.org property read as a plain string, however it is written.

    A `name` can be a string, a list of strings, or an object carrying its own
    `name`; all three appear in real markup.
    """
    if isinstance(value, list):
        value = value[0] if value else ""
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value") or ""
    return str(value).strip() if isinstance(value, (str, int, float)) else ""


def _jsonld_types(blocks):
    """Type names for the snapshot, via the one reader that handles every form.

    This was a hand-rolled copy that split on `/` only, so `schema:Organization`
    - correct, and accepted by Google - came out as `schema:organization` and
    matched nothing downstream, and a numeric `@type` raised a TypeError that
    ended the run. The shared helper had both fixed; this call site never got
    them, and it is the one that writes `jsonld_types` into the snapshot that
    four other checks read.
    """
    types = set()
    for node in blocks:
        types |= jsonld_type_names(node.get("@type"))
    return sorted(types)


def _paragraphs(soup):
    out = []
    for tag in soup.find_all("p"):
        text = _text_or_empty(tag)
        if len(text) >= 40:
            out.append(truncate(text, 600))
        if len(out) >= 60:
            break
    return out


# Elements whose text belongs to the block around them rather than standing as
# a block of its own. A `<strong>` inside a sentence is part of that sentence;
# a `<div>` inside a `<div>` is a second block.
_INLINE_ELEMENTS = frozenset({
    "a", "abbr", "b", "bdi", "bdo", "big", "br", "cite", "code", "data", "del",
    "dfn", "em", "font", "i", "ins", "kbd", "label", "mark", "nobr", "q", "s",
    "samp", "small", "span", "strong", "sub", "sup", "time", "tt", "u", "var",
    "wbr",
})

# The same floor, cap and truncation `_paragraphs` uses, so the two lists are
# comparable and a block cannot be quotable in one and not the other.
PROSE_BLOCK_MIN_CHARS = 40
PROSE_BLOCK_MAX_CHARS = 600
PROSE_BLOCK_LIMIT = 60


def _own_text(tag):
    """The text written directly in this element, without its child blocks.

    Walking every element and taking `get_text` would return each passage once
    per ancestor it has. Only the strings that sit in this element, plus the
    inline markup wrapping words inside them, are this element's own writing.
    """
    parts = []
    for child in tag.children:
        name = getattr(child, "name", None)
        if name is None:
            parts.append(str(child))
        elif name in _INLINE_ELEMENTS:
            parts.append(child.get_text(" "))
    return tidy_spacing(re.sub(r"\s+", " ", " ".join(parts)).strip())


def _prose_blocks(soup):
    """Every passage this page writes, chrome included, in document order.

    The second source for a definition reading, and the reason it has to exist
    is measured. A bag maker's homepage carries
    `<p><brand> is a proudly Indian and PETA-approved lifestyle brand.</p>` and
    a footer line saying the same thing in other words. Both are in this
    audit's own snapshot under `text`; both are absent from `paragraphs` and
    from `body_text`, because the crawl's site-wide boilerplate strip removes
    any block that appears on half the site's pages - which a footer tagline
    does by construction, and which is exactly the sentence a definition check
    is looking for. The report printed "Nothing quotable" for a homepage with
    two quotable definitions of the brand, and put a false finding into "Start
    here" on two of five sites.

    Written to survive that strip rather than to undo it. The strip rewrites
    `paragraphs`, `sections`, `body_text` and everything derived from them by
    name, and the readability and thin-page checks depend on it doing so; this
    field is not one of those names and is left alone.

    Three differences from `paragraphs`, and each is one of the two failures:

      every block, not `<p>` alone    the footer line is a `<div>` on most
                                      sites, so a `<p>` scan never saw it
      the whole document, not the     the block that repeats site-wide is in
      content region                  the furniture, which is where a tagline
                                      lives
      one entry per block             a definition has to begin its own
                                      sentence, and the flattened `text` runs
                                      the block before it into the block after

    Source is already gone: this reads the copy `_without_leaked_script`
    returned. See `reads_as_source_not_prose` for the sentence-level test a
    consumer should still apply before quoting one of these as an identity.

    What it costs the snapshot, measured over this repository's six fixture
    sites: 889 to 14,500 characters per site against `paragraphs`' 889 to
    9,728 and `text`'s 1,396 to 16,608 - about 1.2 KB a page, on a record that
    already stores the whole page text. The caps are `paragraphs`' caps for
    that reason, and there is no total-character cap on top of them: the
    footer is the last block in the document, and a budget spent from the top
    would drop exactly the tagline this exists to keep.
    """
    out, seen = [], set()
    for tag in soup.find_all(True):
        if tag.name in _INLINE_ELEMENTS or tag.name in _NOT_PROSE_PARENTS:
            continue
        # Nothing from the `<head>`. The `<title>` is not a passage the page
        # writes, and because every page carries one it read as a line the
        # site repeats: "<Brand> | <what it does>" then passed for a tagline
        # definition on a site that states what it is nowhere.
        if tag.name in ("head", "title") or tag.find_parent("head") is not None:
            continue
        text = _own_text(tag)
        if len(text) < PROSE_BLOCK_MIN_CHARS or reads_as_source_not_prose(text):
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(truncate(text, PROSE_BLOCK_MAX_CHARS))
        if len(out) >= PROSE_BLOCK_LIMIT:
            break
    return out


MAIN_REGION_SELECTOR = "main, article, [role=main], #main, #content"


def main_region(soup):
    """The page's own content, excluding site-wide chrome.

    One definition, used by both the link count and the CTA scan. When they
    disagreed, a footer link counted as a page's call to action and the
    dead-end check could never fire on any site with a footer.
    """
    return soup.select_one(MAIN_REGION_SELECTOR) or soup.body or soup


# Semantic navigation first. Plenty of real sites - including ones that predate
# `<nav>` and plenty that simply never adopted it - put their menu in a div with
# a class saying so, and we reported those as having no navigation at all: an
# eight-item menu in `<div class="menu mainmenu">` was read as "the primary
# navigation has 0 items", at high severity, on a site anyone can see has one.
NAV_SELECTOR = (
    # `role=menu` and `role=menubar` are the ARIA menu roles, and a university
    # department's entire navigation lived in one: a `role=dialog` panel
    # containing a `role=menu` with thirty real destination links, exposed by
    # a JavaScript click and `aria-hidden="true"` until then. Neither this
    # selector nor the chrome fingerprint looked at either role, so the report
    # said "the primary navigation has 0 item(s). Labels found: none" at high
    # severity, and told them to "make sure the navigation is real HTML links"
    # - which it already was, thirty of them. Standards-defined roles, not
    # another class-name guess.
    "nav, header nav, [role=navigation], [role=menu], [role=menubar], "
    "[class*=mainmenu], [class*=main-menu], [class*=main-nav], [class*=mainnav], "
    "[class*=navbar], [class*=nav-menu], [class*=menu-main], [class*=site-nav], "
    "[class*=primary-nav], [class*=topnav], [class*=top-nav], "
    "[id*=mainmenu], [id*=main-menu], [id*=main-nav], [id*=navbar], [id*=site-nav]"
)

# Minimum links before an unlabelled block counts as navigation. Below this it
# is a group of related links, not a menu.
NAV_SHAPE_MIN_LINKS = 3


def _nav_by_shape(soup):
    """Blocks that behave like a menu on sites that never say `nav`.

    Only consulted when nothing semantic matched, and only near the top of the
    document, so a footer link list or a body paragraph full of links is not
    mistaken for the primary navigation.
    """
    body = soup.body or soup
    candidates = []
    # Tables and centred blocks as well as lists and divs. A digital library
    # built in table-era HTML puts all 106 of its links in `<td>` cells, has no
    # `<nav>`, no `<header>` and no menu class name, and this fallback searched
    # only `ul`, `div` and `header` - so it returned nothing, and the report's
    # first and highest-severity finding said "the primary navigation has 0
    # item(s). Labels found: none" about a homepage whose whole first screen is
    # a labelled index. A null measurement reported as a site defect is the
    # worst thing this marketplace can do, and the element name a site's
    # template happens to use is not evidence about its navigation.
    for node in body.find_all(
            ["ul", "ol", "div", "header", "table", "tbody", "tr", "center",
             "section", "aside", "p"], recursive=True)[:120]:
        anchors = node.find_all("a", href=True)
        if len(anchors) < NAV_SHAPE_MIN_LINKS:
            continue
        # A menu is mostly short labels, not sentences.
        labels = [_text_or_empty(a) for a in anchors]
        if not labels or sum(len(l) for l in labels) / float(len(labels)) > 40:
            continue
        if any(len(l) > 80 for l in labels):
            continue
        candidates.append((len(anchors), node))
        if len(candidates) >= 3:
            break
    return [node for _count, node in candidates[:1]]



def _within_hidden(node):
    """Is this element, or any ancestor, marked as not displayed?"""
    current = node
    while current is not None and getattr(current, "attrs", None) is not None:
        if _is_hidden(current):
            return True
        current = current.parent
    return False

def _fragment_of(href):
    """The `#...` part of an href, which `normalise_url` drops.

    Kept because for one family of links it is the whole destination, and a
    check that only sees the path requests something the site never published.
    """
    return urlparse((href or "").strip()).fragment or ""


# The smallest number of jump links that can be a menu rather than a skip
# link. One `#main` above the header is an accessibility affordance on every
# kind of site; two or more labelled sections is somebody's navigation.
IN_PAGE_MENU_MIN_ITEMS = 2


def _in_page_menu_anchors(soup, nav_nodes, base, origin):
    """The `#section` anchors in the navigation that are this page's menu.

    Returns `{id(anchor): url-with-its-fragment}`, empty when the page's jump
    links are not its menu.

    Every fragment anchor used to be discarded here, on the reasoning that
    `normalise_url("#about", base)` returns the page's own address and a link
    to yourself is not a link. That is right on a sixty-page site, where a
    fragment anchor is a skip link or an accordion toggle - and wrong on a
    one-page site, where it is the whole menu. A Polish agency's single-page
    site links `#hero`, `#services`, `#works` and `#contact`, labelled INTRO,
    USLUGI, PORTFOLIO and KONTAKT, beside two language toggles that are the
    only path-shaped hrefs on the page. The report read the two toggles as the
    site's navigation and said, in full: "the primary navigation has 2
    item(s). Labels found: "EN", "PL"."

    The test is a comparison rather than a threshold, because the page cannot
    know how large the site is and does not need to: inside the navigation
    itself, whichever kind of link there is more of is what that navigation is
    made of. A site with pages has a menu of paths and at most a skip link; a
    site that is one page has a menu of fragments and at most a language
    toggle. Two further bounds keep an accordion out: the fragment has to
    address an element that exists in the delivered document, and the anchor
    has to carry a label.
    """
    fragments, paths = {}, set()
    for node in nav_nodes:
        for anchor in node.find_all("a", href=True):
            href = anchor["href"].strip()
            if not href.startswith("#"):
                url = normalise_url(href, base)
                if url and same_site(url, origin) and url != normalise_url(base):
                    paths.add(url)
                continue
            fragment = _fragment_of(href)
            if not fragment or not _text_or_empty(anchor).strip():
                continue
            # An address the page does not answer at is a JavaScript hook, not
            # a section of this document.
            if soup.find(id=fragment) is None and soup.find(
                    "a", attrs={"name": fragment}) is None:
                continue
            fragments[id(anchor)] = "{}#{}".format(normalise_url(base) or base, fragment)
    distinct = set(fragments.values())
    if len(distinct) < IN_PAGE_MENU_MIN_ITEMS or len(distinct) <= len(paths):
        return {}
    return fragments


def _links(soup, base, origin):
    internal, external, nav, footer = [], [], [], []
    main_region_node = main_region(soup)
    nav_nodes = soup.select(NAV_SELECTOR)
    if not nav_nodes:
        nav_nodes = _nav_by_shape(soup)
    footer_nodes = soup.select("footer, [role=contentinfo]")
    # The menu of a site that is one page. See `_in_page_menu_anchors`.
    in_page_menu = _in_page_menu_anchors(soup, nav_nodes, base, origin)

    # Navigation the page ships but hides. Hidden is not absent, and the two
    # need completely different advice: one owner has to write a menu, the
    # other has to expose the one they have to a reader without JavaScript.
    # Counted separately, because the same link often appears in both a
    # visible bar and a hidden dropdown of the same menu. Comparing hidden
    # links against the deduplicated total then said "every navigation link is
    # hidden" about a site whose main bar is plainly visible - which is the
    # false positive this whole finding exists to avoid making in reverse.
    hidden_nav, visible_nav = [], []
    for node in nav_nodes:
        bucket = hidden_nav if _within_hidden(node) else visible_nav
        for anchor in node.find_all("a", href=True):
            if anchor["href"].strip().startswith("#"):
                if id(anchor) in in_page_menu:
                    bucket.append(in_page_menu[id(anchor)])
                continue
            url = normalise_url(anchor["href"], base)
            if url and same_site(url, origin):
                bucket.append(url)
    hidden_only = set(hidden_nav) - set(visible_nav)

    def collect(container, bucket, menu=None):
        for anchor in container.find_all("a", href=True):
            # `normalise_url("#about", base)` returns the page's own URL, so an
            # in-page anchor was counted as a link to somewhere. The main link
            # loop below skips `#` explicitly; nav, footer and main did not, so
            # a page whose only links are jump links reported one main link -
            # itself - and the dead-end check could never fire on it, while the
            # chrome signature picked up the page's own path as a nav target.
            #
            # `menu` is the exception, and only the navigation collector is
            # given one: on a single-page site the jump links *are* the menu.
            if anchor["href"].strip().startswith("#"):
                if menu and id(anchor) in menu:
                    bucket.append({"url": menu[id(anchor)],
                                   "text": truncate(_text_or_empty(anchor), 120),
                                   "fragment": _fragment_of(anchor["href"])})
                continue
            url = normalise_url(anchor["href"], base)
            if url:
                bucket.append({"url": url,
                               "text": truncate(_text_or_empty(anchor), 120),
                               "fragment": _fragment_of(anchor["href"])})

    for node in nav_nodes:
        collect(node, nav, in_page_menu)
    for node in footer_nodes:
        collect(node, footer)

    main_links = []
    collect(main_region_node, main_links)

    seen = set()
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url = normalise_url(href, base)
        if not url or url in seen:
            continue
        seen.add(url)
        entry = {"url": url, "text": truncate(_text_or_empty(anchor), 120),
                 "nofollow": "nofollow" in (anchor.get("rel") or []),
                 # What `normalise_url` threw away. A link whose real
                 # destination is carried in its fragment is not a link to the
                 # bare path: an email-obfuscation scheme ships
                 # `href="/cdn-cgi/l/email-protection#0e6f7d..."` and rewrites
                 # it into a `mailto:` in the browser, and the audit requested
                 # the bare path, got a 404, and reported a broken link to the
                 # owner on all sixteen pages carrying it.
                 "fragment": _fragment_of(href)}
        if same_site(url, origin):
            internal.append(entry)
        else:
            external.append(entry)

    # Links whose destination is only in the fragment, and which no anchor on
    # this page links plainly. `normalise_url` drops the fragment, so an
    # obfuscated-email anchor - `href="/cdn-cgi/l/email-protection#0e6f..."` -
    # is recorded as a link to a bare path the site never published. The
    # broken-link check now refuses to probe those; the count of onward links
    # still included them, so a page whose only main-region link was one of
    # these read as a page with somewhere to go, and a real dead end went
    # unreported. A page that links `/docs` plainly and also links
    # `/docs#install` is unaffected: only the addresses nothing links plainly
    # are set aside.
    plain = {l["url"] for l in main_links if not l.get("fragment")}
    unreadable = {l["url"] for l in main_links
                  if l.get("fragment") and l["url"] not in plain}
    main_internal = sorted({l["url"] for l in main_links
                            if same_site(l["url"], origin) and l["url"] not in unreadable})
    # A hosted form, a booking system or a donation platform is a next step
    # even though it is somebody else's host, and the dead-end check could not
    # see one: it knew how many internal links the main region carries and
    # nothing about the rest, so a page whose whole purpose is an embedded
    # third-party form read as a page offering nowhere to go.
    main_external = sorted({l["url"] for l in main_links
                            if not same_site(l["url"], origin)
                            and l["url"] not in unreadable})
    return {
        "internal": internal[:300],
        "external": external[:150],
        # The main region's own internal links, with their labels, deduplicated.
        #
        # `main_internal_sample` holds ten addresses and no labels, and the two
        # readings that decide whether a page is a shelf need the labels: they
        # compare this page's headings against the labels of the links leaving
        # it. Asked of the whole document, a mega-menu supplies a label for
        # every category a shop has, so any product page whose H1 or H2 repeats
        # a category name read as a listing - measured as Product markup
        # assessed on 2 of 13 product pages on one shop, and 0 of 3 `/product/`
        # URLs typed `product` on another. `main_region` is the same definition
        # the link count and the CTA scan already share, for the same reason.
        "main_internal_links": _dedupe_links(
            [l for l in main_links
             if same_site(l["url"], origin) and l["url"] not in unreadable])[:300],
        "nav": _dedupe_links(nav)[:40],
        "nav_hidden_count": len(hidden_only),
        "nav_visible_count": len(set(visible_nav)),
        "footer": _dedupe_links(footer)[:60],
        "main_internal_count": len(main_internal),
        "main_internal_sample": main_internal[:10],
        "main_external_count": len(main_external),
        "main_external_sample": main_external[:10],
        # Set aside from both counts above, and named so a check can say so
        # rather than a page quietly having fewer links than it appears to.
        "main_links_with_a_fragment_destination": sorted(unreadable)[:10],
        "internal_count": len(internal),
        "external_count": len(external),
        "mailto_count": len(soup.select('a[href^="mailto:"]')),
        "tel_count": len(soup.select('a[href^="tel:"]')),
        # Counted beside `tel_count` and not folded into it, because they are
        # not the same claim: a `tel:` link is a number a phone dials and a
        # click-to-chat link is a number a messaging app opens. Both are the
        # site stating a way to reach it, which is the only question the
        # contact-route check asks, and a report that says which one it found
        # can be checked against the page.
        "click_to_chat_count": sum(
            1 for a in soup.find_all("a", href=True)
            if _phone_from_click_to_chat(a.get("href"))),
    }


def _dedupe_links(links):
    seen, out = set(), []
    for link in links:
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        out.append(link)
    return out


def _scripts(soup, base):
    external, inline_bytes, hosts = [], 0, set()
    host = urlparse(base).netloc.lower()
    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            url = normalise_url(src, base)
            if url:
                external.append(url)
                script_host = urlparse(url).netloc.lower()
                if script_host and script_host != host:
                    hosts.add(script_host)
        else:
            inline_bytes += len(tag.string or tag.get_text() or "")
    style_bytes = sum(len(t.string or "") for t in soup.find_all("style"))
    return {
        "count": len(soup.find_all("script")),
        "external_count": len(external),
        "external_sample": sorted(set(external))[:10],
        "inline_bytes": inline_bytes,
        "style_bytes": style_bytes,
        "third_party_hosts": sorted(hosts),
        "third_party_host_count": len(hosts),
    }


# Labels a hostname's owner cannot have chosen. `co.uk`, `co.in`, `com.au`,
# `ac.uk`: a registry's own spelling of "a company here", not an organisation.
# Reading one of those as a platform would treat every site in a country as the
# same platform and silence their accounts together.
_REGISTRY_LABELS = frozenset({"co", "com", "net", "org", "edu", "gov", "govt",
                              "ac", "sch", "or", "ne", "go", "gob", "gouv"})


def _domains_above(host):
    """The domains a hostname sits under, narrowest first, never a registry's."""
    labels = [label for label in (host or "").split(".") if label]
    for cut in range(1, len(labels) - 1):
        parent = labels[cut:]
        if len(parent) == 2 and parent[0] in _REGISTRY_LABELS:
            continue
        yield ".".join(parent)


def subresource_domains(hosts):
    """The hosts a site loads its code from, plus the domains they sit under.

    A host the site only ever fetches a script from is something the site is
    built on, not somewhere the brand keeps an account. That is the strongest
    reading available of the difference between a profile and a platform, and
    `counts_as_off_site_profile` takes it as an argument because no URL can
    supply it on its own.

    The hostnames alone were not enough. A hosted storefront serves a shop's
    theme from `cdn.<platform>` and describes itself at `www.<platform>`, so the
    address a site declares and the address in its script tag are never the same
    string - and the marketing address was being counted as the shop's own
    off-site profile. Widened only where the script host's leading label names a
    machine's job, which is the reading `is_infrastructure_url` already applies:
    `cdn.<x>` and `assets.<x>` are roles and the domain beneath them belongs to
    the operator, while `checkout.<x>` and `pdp.<x>` name a product, and
    widening those would start discarding real accounts on real platforms.

    Read by `freshness-corroboration-audit` as well as here, so the two doors
    into the profile count - a declaration on the page and a declaration in the
    site's own agent file - cannot come apart on what a platform is.
    """
    out = set()
    for host in hosts or ():
        host = strip_www((host or "").strip().lower())
        if not host or "." not in host:
            continue
        out.add(host)
        if is_infrastructure_url("https://" + host + "/"):
            out.update(_domains_above(host))
    return sorted(out)


# --------------------------------------------------------------------------
# What the site is built with
#
# The evidence `audit_common.publishing_platform` reads, recorded here because
# a consumer cannot recompute any of it once the HTML and the response headers
# are gone - the same rule `chrome_emails`, `postcode_source` and
# `control_text` follow. Kept out of every prose field for the same reason
# `control_text` is: a cookie name is not something the page says.
#
# Four real reports quoted a storefront platform's CDN hostname back inside
# their own snippets and never concluded which platform the shop was on, so
# every markup fix in all four said "add the snippet to the site-wide
# template" and none said where that template is. The hostname was in the
# snapshot the whole time; nothing had ever been asked to look at it.
#
# Nothing here reads prose and nothing here reads `<a href>`. A page that says
# a platform's name, links to it, or shows its badge is a page mentioning a
# company; a page that fetches its stylesheet from that company's CDN, or whose
# origin sets that company's cookie, is a page that company rendered.
# --------------------------------------------------------------------------

# Response headers whose name is a name everybody's server sends, so its
# presence identifies nothing. Everything else prefixed `x-` is a name some
# vendor chose, which is what a signature is made of.
_ORDINARY_HEADER_NAMES = frozenset({
    "x-frame-options", "x-content-type-options", "x-xss-protection",
    "x-robots-tag", "x-ua-compatible", "x-cache", "x-cache-hits", "x-served-by",
    "x-timer", "x-request-id", "x-correlation-id", "x-response-time",
    "x-runtime", "x-download-options", "x-dns-prefetch-control",
    "x-permitted-cross-domain-policies", "x-powered-by", "x-varnish",
    "x-amz-cf-id", "x-amz-cf-pop", "x-amz-request-id", "x-content-duration",
})

# Headers whose *value* names the software. Read for their value; the value is
# a product name and a version, never anything about a visitor.
_STACK_HEADERS = ("server", "x-powered-by", "powered-by", "x-generator")

# A cookie's name is a fact about the platform; a cookie's value is a session
# belonging to whoever this audit fetched as. Only names are recorded, and the
# attribute keywords a Set-Cookie line carries are dropped so `path` and
# `expires` are never mistaken for cookies. Splitting on commas alone cannot
# work: `expires=Wed, 21 Oct 2025 ...` has one in the middle of it.
_COOKIE_PAIR_RE = re.compile(r"(?:^|[,;]\s*)([A-Za-z0-9_.~-]{1,64})\s*=")
_COOKIE_ATTRIBUTES = frozenset({
    "expires", "path", "domain", "max-age", "samesite", "secure", "httponly",
    "version", "comment", "priority", "partitioned",
})

# A path segment stable enough to be part of a platform's shape. It has to
# start with a letter, which is what keeps a content hash or a build number out
# of the recorded shape while `/cdn/shop` and `/wp-content/themes` stay in.
_STABLE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z][A-Za-z0-9._-]{0,31}$")

# The elements whose addresses are subresources: things the browser fetches
# because the page told it to, never things a reader clicks. Anchors are
# deliberately absent.
_SUBRESOURCE_SELECTOR = "script[src], link[href], img[src], iframe[src]"

# Ceilings, so one page carrying two hundred images cannot dominate the
# snapshot. A platform's signature repeats on every asset it serves, so the
# first few are the same evidence as all of them.
PLATFORM_SUBRESOURCE_LIMIT = 200
PLATFORM_LIST_LIMIT = 20
PLATFORM_VALUE_CHARS = 160


def _cookie_names(raw):
    """The names of the cookies a response set. Names only, never values."""
    out = []
    for match in _COOKIE_PAIR_RE.finditer(str(raw or "")):
        name = match.group(1)
        if name.lower() in _COOKIE_ATTRIBUTES or name in out:
            continue
        out.append(name)
        if len(out) >= PLATFORM_LIST_LIMIT:
            break
    return out


def _asset_path_shape(path):
    """`/cdn/shop/files/a.jpg` -> `/cdn/shop`. The shape, not the file."""
    parts = [p for p in str(path or "").split("/") if p]
    if not parts:
        return ""
    shape = "/" + parts[0]
    if len(parts) > 1 and _STABLE_PATH_SEGMENT_RE.match(parts[1]):
        shape += "/" + parts[1]
    return shape[:PLATFORM_VALUE_CHARS]


def _platform_signals(soup, meta, headers, base, spa_shell):
    """The six families of evidence for what rendered this page.

    One `select` over the document's subresource elements, capped, plus values
    already parsed by the caller - the meta tags, the response headers and the
    shell reading. Nothing is re-parsed and no element is walked twice, because
    this runs on every page of every crawl inside a five-minute budget.
    """
    headers = {str(k).lower(): v for k, v in (headers or {}).items()}
    own_host = urlparse(base).netloc.lower()

    hosts, paths = [], []
    for tag in soup.select(_SUBRESOURCE_SELECTOR)[:PLATFORM_SUBRESOURCE_LIMIT]:
        raw = tag.get("src") or tag.get("href") or ""
        url = normalise_url(raw, base)
        if not url:
            continue
        parsed = urlparse(url)
        host = parsed.netloc.lower()
        if host and host != own_host and host not in hosts:
            hosts.append(host)
        shape = _asset_path_shape(parsed.path)
        if shape and shape not in paths:
            paths.append(shape)

    root_attributes = []
    for node in (soup.html, soup.body):
        for name in sorted((node.attrs or {}) if node is not None else {}):
            name = str(name).lower()
            if name.startswith("data-") and name not in root_attributes:
                root_attributes.append(name)

    stack = "; ".join(str(headers[key]) for key in _STACK_HEADERS if headers.get(key))
    return {
        # The software naming itself, from the tag it writes and from the
        # headers that say the same thing a different way.
        "generator": truncate(meta.get("generator", ""), PLATFORM_VALUE_CHARS),
        "powered_by": truncate(stack, PLATFORM_VALUE_CHARS),
        "asset_hosts": sorted(hosts)[:PLATFORM_LIST_LIMIT],
        "asset_paths": sorted(paths)[:PLATFORM_LIST_LIMIT],
        "markup_attributes": root_attributes[:PLATFORM_LIST_LIMIT],
        # The root container and state blob `_spa_shell` already found. A
        # framework's mount point names the framework, and this costs nothing
        # because the reading has already happened.
        "framework_markers": sorted(
            [b for b in (spa_shell or {}).get("state_blobs") or []]
            + ([(spa_shell or {}).get("root_selector")]
               if (spa_shell or {}).get("root_selector") else []))[:PLATFORM_LIST_LIMIT],
        "header_names": sorted(
            name for name in headers
            if name.startswith("x-") and name not in _ORDINARY_HEADER_NAMES
        )[:PLATFORM_LIST_LIMIT],
        "cookie_names": _cookie_names(headers.get("set-cookie")),
    }


# The ways a framework writes an alt attribute it fills in at runtime. The
# rendered page has real alt text; the delivered HTML has the binding.
#
# A retailer's report listed 51 images with no alt attribute. Every one was a
# copy of the same three components - a mini-cart thumbnail, a quick-add modal
# and a country flag - each declaring `:alt="product.title"`, and their `src`
# was a JavaScript expression rather than an image. The advice was to write
# alt text for 51 distinct images that do not exist.
_DYNAMIC_ALT_ATTRS = (
    ":alt", "v-bind:alt", "x-bind:alt", "[alt]", "bind:alt", "data-alt",
    "*ngIf-alt", "alt.bind",
)


def _declares_alt_dynamically(tag):
    """Does this image bind its alt text from a template?"""
    for name in _DYNAMIC_ALT_ATTRS:
        if tag.get(name) is not None:
            return True
    return False


def _is_tracking_pixel(tag, width, height):
    """A beacon, not a picture.

    A pizza chain's report said "2 images have no alt attribute" and advised
    "write alt text that states what the image shows" and "prioritise product
    photos". Both images were 1x1 Facebook beacons with `style="display:none"`.
    Nothing shows, nobody can describe it, and no alt text belongs on it.

    Recognised by shape rather than by host, because the shape is the whole
    point of a beacon: it is sized so nobody sees it.
    """
    # The attributes have to be *there*. `_int_attr` returns 0 for a missing
    # one, so reading the parsed values alone made every image with no width
    # or height a beacon - which on the first fixture that exercised it was
    # every image on the page.
    declared = tag.get("width") is not None and tag.get("height") is not None
    if declared and (width or 0) <= 3 and (height or 0) <= 3:
        return True
    style = (tag.get("style") or "").replace(" ", "").lower()
    if "display:none" in style or "visibility:hidden" in style:
        return True
    # Sized in its style instead of its attributes: `width:1px;height:1px`.
    sized = dict(re.findall(r"(?<![\w-])(width|height):(\d+(?:\.\d+)?)px", style))
    if len(sized) == 2 and all(float(v) <= 3 for v in sized.values()):
        return True
    if _TRACKING_ENDPOINT_RE.search(tag.get("src") or tag.get("data-src") or ""):
        return True
    return _within_hidden(tag)


# The address of a beacon rather than a picture. A coffee roaster's pages carry
# `<img src=".../cde/eventTracking.htm?pixelId=...">` from an advertising
# network, and one such beacon written without a size or a hidden style was
# counted as an image with no alt text. Its address says what it is: an event
# endpoint - a page, not an image file - named for tracking, a spacer or pixel
# file, or a pixel identifier in the query. Nobody can describe it, and no alt
# text belongs on it.
_TRACKING_ENDPOINT_RE = re.compile(
    r"/[\w.-]*(?:track|pixel|beacon|collect|impression|event)[\w.-]*"
    r"\.(?:htm|html|php|aspx?|jsp|cgi)(?:[?#]|$)"
    r"|/(?:pixel|beacon|spacer|tracking?|1x1)\.gif(?:[?#]|$)"
    r"|[?&](?:pixel_?id|pxid)=", re.I)


_SCHEMA_TYPE_TAIL = re.compile(r"[/#:]([A-Za-z][A-Za-z0-9_]*)\s*$")


# `<title>` inside an `<svg>` is the accessible name of an icon, not the name
# of the page. A restaurant group ships three: two label icons, one is the real
# document title, and `soup.title` returned the first in document order - the
# icon's. The page title became "Dishoom", which then produced two of that
# site's thirteen findings, including its only high-severity one: structured
# data was reported as contradicting a page whose real title contains every
# word of the declared name, and two pages with different titles were reported
# as duplicates of each other.
#
# `soup.head.title` is not the answer either. On that site the SVG titles
# appear before the head is closed, and lxml relocates the real one into the
# body, so `soup.head.title` is None. What identifies it is where it is not:
# inside a drawing.
_NOT_A_DOCUMENT_TITLE = frozenset({"svg", "symbol", "defs", "g", "marker"})


_FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/feed+json",
               "application/json")


def _feed_links(soup, base):
    """Feeds the page declares in its head, by type rather than by filename."""
    out = []
    for element in soup.select('link[rel~="alternate"][href]'):
        if (element.get("type") or "").strip().lower() not in _FEED_TYPES:
            continue
        url = normalise_url(element.get("href") or "", base)
        if url and url not in out:
            out.append(url)
    return out


def _document_title(soup):
    """The page's own title, not the accessible name of an icon."""
    for element in soup.find_all("title"):
        parents = {p.name for p in element.parents}
        if parents & _NOT_A_DOCUMENT_TITLE:
            continue
        return _text_or_empty(element)
    return ""


def _typed_markup(soup):
    """schema.org type names declared inline, as Microdata or RDFa.

    `itemtype="https://schema.org/Restaurant"` and `typeof="Product"` state the
    same thing a JSON-LD `@type` states. Reading only JSON-LD left every schema
    check with one detector, which is the shape of every false positive this
    marketplace has made on a real site.
    """
    names = set()
    for element in soup.select("[itemtype], [typeof]"):
        for attribute in ("itemtype", "typeof"):
            value = element.get(attribute)
            for token in (value if isinstance(value, list) else [value or ""]):
                for word in str(token).split():
                    match = _SCHEMA_TYPE_TAIL.search(word)
                    names.add((match.group(1) if match else word).lower())
    return sorted(n for n in names if n)


MICRODATA_ITEM_LIMIT = 20
MICRODATA_PROP_LIMIT = 30
MICRODATA_VALUE_LIMIT = 5
MICRODATA_VALUE_CHARS = 200
_MICRODATA_VALUE_ATTRS = ("content", "datetime", "href", "src", "data", "value")
_NOT_RDFA_PROPERTY = ("og:", "twitter:", "fb:", "article:", "profile:", "music:", "video:")


def _microdata_value(element, base):
    """What one `itemprop` element states, read the way the spec reads it.

    A `<meta itemprop="price" content="19.00">` has no text; a `<time
    itemprop="datePublished" datetime="2026-04-01">` states the machine form in
    an attribute and a human form in its text; an `<a itemprop="url">` states
    its `href`. Reading text alone made every one of these empty, which is the
    same failure as not reading the notation at all.
    """
    name = (element.name or "").lower()
    if name in ("a", "link", "area"):
        return urljoin(base, element.get("href") or "") or _text_or_empty(element)
    if name in ("img", "audio", "video", "embed", "iframe", "source", "track"):
        return urljoin(base, element.get("src") or "") or ""
    if name == "object":
        return urljoin(base, element.get("data") or "") or ""
    for attribute in _MICRODATA_VALUE_ATTRS:
        value = (element.get(attribute) or "").strip()
        if value:
            return value[:MICRODATA_VALUE_CHARS]
    return _text_or_empty(element)[:MICRODATA_VALUE_CHARS]


def _microdata_props(scope, base, depth=0):
    """The properties declared inside one `itemscope`, not inside a nested one.

    Microdata nests by scope, so an `itemprop` under a child `itemscope`
    belongs to the child. Collecting every descendant put a review author's
    name on the product, and a nested offer's price on the organization.
    """
    props = {}
    for element in scope.find_all(attrs={"itemprop": True}):
        nested_under_another_scope = False
        for parent in element.parents:
            if parent is scope:
                break
            if parent.has_attr("itemscope"):
                nested_under_another_scope = True
                break
        if nested_under_another_scope:
            continue
        for key in str(element.get("itemprop") or "").split():
            if len(props) >= MICRODATA_PROP_LIMIT and key not in props:
                continue
            if element.has_attr("itemscope") and depth < 1:
                value = _microdata_item(element, base, depth + 1)
            else:
                value = _microdata_value(element, base)
            if value in ("", None, {}):
                continue
            held = props.setdefault(key, [])
            if len(held) < MICRODATA_VALUE_LIMIT:
                held.append(value)
    return {k: (v[0] if len(v) == 1 else v) for k, v in props.items()}


def _type_as_written(value):
    """The type name the site wrote, not a folded key.

    `jsonld_type_names` lowercases, which is right for matching and wrong for
    quoting: a finding that reads "organization markup is present" is quoting
    this audit's index rather than the site's markup.
    """
    for token in str(value or "").split():
        name = token.split("/")[-1].split("#")[-1].split(":")[-1]
        if name:
            return name
    return ""


def _microdata_item(scope, base, depth=0):
    item = {"@type": _type_as_written(scope.get("itemtype"))}
    item.update(_microdata_props(scope, base, depth))
    return item


def _rdfa_item(scope, base):
    """One `typeof` scope, read as an item.

    `property=` is RDFa's `itemprop`, and Open Graph borrows the attribute for
    something else entirely: `<meta property="og:title">` is a social card, not
    a statement about a typed subject. Folding those in made every page with a
    social card declare an entity whose properties are its share preview.
    """
    item = {"@type": _type_as_written(scope.get("typeof"))}
    for element in scope.find_all(attrs={"property": True}):
        nested = False
        for parent in element.parents:
            if parent is scope:
                break
            if parent.has_attr("typeof"):
                nested = True
                break
        if nested:
            continue
        for key in str(element.get("property") or "").split():
            key = key.split(":")[-1] if not key.lower().startswith(_NOT_RDFA_PROPERTY) else ""
            if not key or key in item or len(item) > MICRODATA_PROP_LIMIT:
                continue
            value = _microdata_value(element, base)
            if value:
                item[key] = value
    return item


def _inline_items(soup, base):
    """Every Microdata or RDFa item on the page, shaped like a JSON-LD node.

    Shaped that way on purpose: the schema checks read `@type` and named
    properties, and a site that states its facts as Microdata has stated them.
    Reading only JSON-LD told a shop using its theme's `itemprop` markup that
    its Product block omits `price`, `availability` and `sku` - all three of
    which were on the page, in the notation this function reads.
    """
    items = []
    for scope in soup.select("[itemscope][itemtype]"):
        if any(p.has_attr("itemscope") for p in scope.parents):
            continue
        item = _microdata_item(scope, base)
        if item.get("@type") and len(item) > 1:
            item["_notation"] = "microdata"
            items.append(item)
        if len(items) >= MICRODATA_ITEM_LIMIT:
            return items
    for scope in soup.select("[typeof]"):
        if any(p.has_attr("typeof") for p in scope.parents):
            continue
        item = _rdfa_item(scope, base)
        if item.get("@type") and len(item) > 1:
            item["_notation"] = "rdfa"
            items.append(item)
        if len(items) >= MICRODATA_ITEM_LIMIT:
            break
    return items


def _described_some_other_way(tag):
    """Is this image named by something other than its `alt` attribute?

    `alt` is the usual way and not the only one. An image inside a `<figure>`
    with a `<figcaption>`, or carrying `aria-label`, `aria-labelledby` or a
    `title`, is described to a screen reader and to a crawler alike. Counting
    only `alt` gave the missing-alt check a single detector to speak from, and
    a single detector reported as a fact about the site is how every false
    positive in this marketplace has started.
    """
    for attribute in ("aria-label", "title"):
        if (tag.get(attribute) or "").strip():
            return True
    if (tag.get("aria-labelledby") or "").strip():
        return True
    if (tag.get("role") or "").strip().lower() == "presentation":
        return True
    parent = tag.find_parent("figure")
    return bool(parent is not None and parent.find("figcaption"))


# The word, wherever the markup puts it. A template names its brand mark in
# the file name, in the alt text, in a class, or in the link back to the
# homepage that wraps it - and any one of those is the site saying "this is
# our logo", which is the only thing that makes a picture a logo.
_LOGO_WORD_RE = re.compile(r"(?<![a-z])logos?(?![a-z])|brand[-_]?mark|wordmark", re.I)


def _calls_itself_a_logo(tag, raw_src):
    """Does the page present this image as the site's own logo?"""
    if _LOGO_WORD_RE.search(raw_src or ""):
        return True
    for attribute in ("alt", "title", "id", "itemprop"):
        if _LOGO_WORD_RE.search(str(tag.get(attribute) or "")):
            return True
    if _LOGO_WORD_RE.search(" ".join(tag.get("class") or [])):
        return True
    parent = tag.parent
    for _ in range(3):
        if parent is None or getattr(parent, "attrs", None) is None:
            break
        if _LOGO_WORD_RE.search(" ".join(parent.get("class") or [])):
            return True
        parent = parent.parent
    return False


def _images(soup, base):
    all_images = soup.find_all("img")
    missing_alt, content_images, described = [], 0, 0
    described_elsewhere = 0
    undescribed = []
    logo_candidates = []
    seen_srcs = []
    kept = 0
    for tag in all_images:
        # An `<img src="">` resolves to the page's own address, and one on a
        # charity's homepage was listed in a finding as an image with no
        # description - sending the owner to look for a picture at the site
        # root, which is not a picture. An element with no source is not an
        # image; the browser shows nothing for it either.
        raw_src = (tag.get("src") or tag.get("data-src") or "").strip()
        if not raw_src:
            continue
        src = normalise_url(raw_src, base) or ""
        kept += 1
        seen_srcs.append(src)
        if src and _calls_itself_a_logo(tag, raw_src):
            logo_candidates.append({"src": src,
                                    "alt": truncate(tag.get("alt") or "", 120)})
        alt = tag.get("alt")
        width = _int_attr(tag.get("width"))
        height = _int_attr(tag.get("height"))
        # A decorative image is correctly marked `alt=""`; only a *missing*
        # alt attribute hides content from a machine. And a tracking beacon
        # shows nothing to anybody, so it is not an image at all here.
        if alt is None and not _declares_alt_dynamically(tag) \
                and not _is_tracking_pixel(tag, width, height):
            missing_alt.append(src)
        # Images that actually say what they show. `alt=""` is not one of
        # them: it is a declaration that the image is decorative, which is
        # correct for a divider and a false statement on a 900x700 price
        # table. The "facts locked in images" check needs to tell the two
        # apart, and `missing_alt_count` cannot - it counts only a *missing*
        # attribute, so a page whose every fact sits in an image marked
        # `alt=""` looked fully described.
        if isinstance(alt, str) and alt.strip():
            described += 1
        elif _described_some_other_way(tag):
            described_elsewhere += 1
        elif alt is None and not _declares_alt_dynamically(tag)                 and not _is_tracking_pixel(tag, width, height):
            undescribed.append(src)
        if (width and height and width * height >= 120000) or _is_hero(tag):
            content_images += 1
    # Counted over every image on the page, not over the samples below. See
    # `distinct_count` in the returned record for what reading a count out of
    # a twenty-entry sample cost.
    distinct = {u for u in seen_srcs if u}
    distinct_missing_alt = {u for u in missing_alt if u}
    distinct_undescribed = {u for u in undescribed if u}
    return {
        # Elements that actually name an image. `len(all_images)` counted a
        # sourceless `<img>` too, and this number decides whether a page is
        # image-heavy enough to grade.
        "count": kept,
        "missing_alt_count": len(missing_alt),
        "described_count": described,
        # Images with no usable `alt` and no description anywhere else either.
        # `alt` is not the only way HTML names a picture, and the missing-alt
        # check had only the `alt` scan to go on - one detector, which is the
        # shape of every false positive this marketplace has made. An image
        # inside a `<figure>` with a `<figcaption>`, or carrying `aria-label`,
        # `aria-labelledby` or `title`, is described; a screen reader and a
        # crawler both get the words.
        #
        # Counted from the list itself. It was `len(missing_alt)` less the
        # images described some other way, but that second count includes
        # images carrying `alt=""`, which were never in the first - so a
        # coffee roaster's homepage recorded -1 undescribed images.
        "undescribed_count": len(undescribed),
        # The URLs behind that count, not behind `missing_alt_count`. A
        # finding about images nobody describes was quoting `missing_alt_sample`,
        # which includes every image named by a caption - so the evidence for
        # "these pictures say nothing to a machine" listed pictures that do.
        "undescribed_sample": [u for u in undescribed if u][:20],
        # How many *different* pictures those two counts are about, measured
        # over the whole page rather than over the sample beside it.
        #
        # The site-wide check counts distinct URLs, and it was counting them
        # out of `undescribed_sample`, which stops at twenty. One product page
        # carried 232 images with no alt and a homepage 25; the
        # report said "4 distinct image URL(s) with no alt attribute", because
        # a sample is not a population and a proportion cannot be computed from
        # one. A count that a sample cap can bound is not a measurement of the
        # site, so the counts are taken here, where every image on the page is
        # in front of us, and the samples stay samples.
        "distinct_count": len(distinct),
        "missing_alt_distinct_count": len(distinct_missing_alt),
        "undescribed_distinct_count": len(distinct_undescribed),
        # Images the page presents as a logo, with the words that say so.
        #
        # Only undescribed images had their URLs recorded, which biases every
        # consumer toward exactly the wrong pictures: a properly marked-up
        # header logo carries alt text and was therefore invisible, while a
        # partner-logo strip with no alt text was all a check could see. A
        # snippet builder looking for the brand mark had nothing to read and
        # fell back to whatever `og:image` the first crawled page carried -
        # which on one site was a 2 MB event photograph, published as the
        # organisation's logo in a block the report told a developer to paste.
        "logo_candidates": logo_candidates[:5],
        # Twenty, not five. The site-wide check counts distinct image URLs
        # from these samples, because one shared header icon repeated on
        # sixty pages is one fix, not sixty images. Five per page was too
        # few for that count to mean anything.
        "missing_alt_sample": [u for u in missing_alt if u][:20],
        "large_image_count": content_images,
        "svg_text_nodes": len(soup.select("svg text")),
        "canvas_count": len(soup.find_all("canvas")),
    }


def _int_attr(value):
    try:
        return int(str(value).strip().replace("px", ""))
    except (TypeError, ValueError):
        return 0


def _is_hero(tag):
    marker = " ".join(filter(None, [
        " ".join(tag.get("class") or []), tag.get("id") or "", tag.get("src") or "",
    ])).lower()
    return any(word in marker for word in ("hero", "banner", "masthead", "cover", "splash"))


# Input types a visitor types free text into. Everything else in a form is a
# control that picks from a fixed set - a menu, a checkbox, a date stepper - and
# its value is never the words someone searched for.
_TYPED_INPUT_TYPES = ("", "text", "search", "url", "tel", "email")


def _typed_field_name(visible):
    """The name of the box someone types words into, or "" if there is none.

    This was "the first named visible field", and a `<select>` is a visible
    field. A documentation site puts a scope menu in front of its search box:

        <form method="GET" action="search">
          <select name="s"><option value="d">...</option></select>
          <input type="text" name="q" id="searchbox">

    so the first named visible field was `s`, the menu that chooses which
    index to search, and the report told the site to publish
    `.../search?s={search_term_string}` as its SearchAction target. Pasting
    that ships a search URL that returns nothing: `s` carries a one-letter
    scope code, and the words go in `q`.

    A `<select>` and a `<textarea>` are dropped because neither is a search
    box. Among `<input>` elements only the types a search box is actually
    written as are considered, so a date picker or a number stepper standing
    before the real field cannot be mistaken for it either. Nothing is
    guessed: a form with no such field returns "", and the SearchAction check
    already refuses to assemble a target without one and says so in the
    report.
    """
    for field in visible:
        if field.name != "input":
            continue
        if (field.get("type") or "").strip().lower() not in _TYPED_INPUT_TYPES:
            continue
        name = (field.get("name") or "").strip()
        if name:
            return name
    return ""


def _forms(soup, origin=""):
    out = []
    for form in soup.find_all("form"):
        inputs = form.find_all(["input", "select", "textarea"])
        visible = [i for i in inputs if (i.get("type") or "text").lower()
                   not in ("hidden", "submit", "button", "image", "reset")]
        required = [i for i in visible if i.has_attr("required")]
        text = _text_or_empty(form).lower()
        out.append({
            "action": (form.get("action") or "").strip(),
            "method": (form.get("method") or "get").lower(),
            "field_count": len(visible),
            "required_count": len(required),
            "is_search": _form_is_search(form, text),
            # Whether this form leaves the site. A search box that posts to a
            # web search engine is that engine's search, not this site's, and
            # every consumer of `is_search` needs to be able to tell them
            # apart - the SearchAction check was telling two sites to declare
            # somebody else's search endpoint as their own.
            "submits_off_site": _submits_off_site(form, origin),
            "is_newsletter": any(hint in text for hint in NEWSLETTER_HINTS),
            # The name of the box someone types into. With the action above,
            # it is the whole of a SearchAction target - and the report was
            # guessing `search?q=` and `search_term_string` while the form
            # declaring `action="/search/"` and `name="query"` sat in the same
            # HTML the crawler had already read.
            "query_field": _typed_field_name(visible),
        })
        if len(out) >= 15:
            break
    return out


def _form_is_search(form, text):
    if form.get("role") == "search":
        return True
    if form.select('input[type="search"]'):
        return True
    action = (form.get("action") or "").lower()
    return "search" in action or "/s/" in action or "search" in text[:60]


# Frames that exist for machinery rather than for a reader.
_PLUMBING_FRAME_HOSTS = (
    "googletagmanager.com", "google-analytics.com", "doubleclick.net",
    "facebook.com/tr", "connect.facebook.net", "hotjar.com", "clarity.ms",
    "segment.com", "cdn.cookielaw.org", "onetrust.com",
)


def _invisible_frame(tag, src):
    """True for a frame with no size, no source, or a tracking source."""
    low = (src or "").strip().lower()
    if not low or low in ("about:blank", "javascript:false", "javascript:;"):
        return True
    if any(host in low for host in _PLUMBING_FRAME_HOSTS):
        return True
    if _is_hidden(tag) or tag.find_parent("noscript") is not None:
        return True
    style = (tag.get("style") or "").replace(" ", "").lower()
    if "width:0" in style or "height:0" in style:
        return True
    return _int_attr(tag.get("width")) == 0 or _int_attr(tag.get("height")) == 0


def _iframes(soup, base):
    out = []
    for tag in soup.find_all("iframe"):
        src = normalise_url(tag.get("src") or "", base) or (tag.get("src") or "")
        out.append({
            "src": truncate(src, 200),
            "title": truncate(tag.get("title") or "", 120),
            "is_video": any(host in src.lower() for host in VIDEO_HOSTS),
            "is_audio": any(host in src.lower() for host in AUDIO_HOSTS),
            "is_map": "map" in src.lower(),
            # An iframe nobody can see holds nobody's content. Two real sites
            # were told their main content was trapped in one: a museum's was
            # a tag manager's invisible tracking pixel, and a coffee roaster's
            # was a 0x0 form helper whose src the report printed as
            # `about:blank`. A blank, hidden, sizeless frame is plumbing.
            "is_invisible": _invisible_frame(tag, src),
        })
        if len(out) >= 12:
            break
    return out


def _video(soup, text, base):
    """Audio and video on the page, and whether a transcript sits beside it.

    Kept under one key because the transcript question is identical for both: a
    machine reading this page can quote the words only if the words are written
    down somewhere. `audio_count` exists because an episode page carrying a
    player and a two-line summary is the canonical instance of that problem and
    counting only `<video>` missed every one of them.
    """
    videos = soup.find_all("video")
    audios = soup.find_all("audio")
    frames = _iframes(soup, base)
    video_embeds = [f for f in frames if f["is_video"]]
    audio_embeds = [f for f in frames if f.get("is_audio")]
    lower = text.lower()
    return {
        "native_count": len(videos),
        "embed_count": len(video_embeds),
        "audio_count": len(audios) + len(audio_embeds),
        "autoplay_count": len([v for v in videos if v.has_attr("autoplay")]),
        # Clips a visitor can start, stop and scrub. A background loop is
        # wallpaper precisely because there is nothing to operate, so
        # `render-readability-audit` caps its count of background loops at
        # `native_count - controls_count`: a `<video autoplay muted loop
        # controls>` is a clip somebody may play deliberately and may hold
        # speech worth transcribing, and calling it decorative suppressed the
        # transcript finding on it. Only `controls`, deliberately - `muted`
        # alone must never mark a clip decorative, which
        # `tests/test_wallpaper_and_records.py` holds in place.
        "controls_count": len([v for v in videos if v.has_attr("controls")]),
        # `autoplay muted loop playsinline` is the standard way to ship a
        # silent background clip - the modern replacement for an animated GIF.
        # It does not interrupt anyone, and the fix offered for it ("mute it,
        # or require a click") was already done on every page it fired on.
        # Counted separately so the check can tell the two apart.
        "intrusive_autoplay_count": len([
            v for v in videos
            if v.has_attr("autoplay") and not (v.has_attr("muted") and v.has_attr("loop"))
        ]),
        "track_count": len(soup.select("video track, audio track")),
        "transcript_nearby": any(hint in lower for hint in TRANSCRIPT_HINTS),
    }


def _pdf_links(soup, base):
    out = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if not re.search(r"\.pdf(\?|$)", href, re.I):
            continue
        out.append({
            "url": normalise_url(href, base) or href,
            "text": truncate(_text_or_empty(anchor), 140),
        })
        if len(out) >= 20:
            break
    return out


# How much of a state blob is worth keeping. Enough to hold the sentences and
# figures a page renders from it; not enough for one page's data to dominate
# the snapshot every skill loads.
SHELL_STATE_TEXT_MAX = 20000

# Below this much delivered body text, the page is a container and its state
# blob is where its words are. Above it, the page speaks for itself.
SHELL_STATE_TEXT_FLOOR = 600

# Values in a state blob that are not the page's words: identifiers, hashes,
# file paths, and the framework's own bookkeeping.
_NOT_SHELL_PROSE = re.compile(
    r"^(?:[0-9a-f]{8,}|/[\w./-]*|https?://\S+|[\w-]+\.(?:js|css|json|png|jpg|svg|webp)"
    r"|true|false|null)$", re.I)


def _shell_state_text(soup):
    """The readable strings a client-rendered page ships inside its state blob.

    A shell delivers a container and a JSON payload, and the words a visitor
    sees are in the payload. Without a browser this audit reads the container
    and concludes the page states no price, no name, no address - which is
    what a naive crawler concludes too, and is worth reporting. But the audit
    also uses those same absences to decide whether the site sells anything at
    all, and getting that wrong silences whole checks: a design studio quoting
    its project rates only in `__NEXT_DATA__` was read as a site with nothing
    for sale, so the finding that its rate card is locked in a PDF could not
    fire, on the exact class of site where it matters most.

    Kept apart from `body_text` on purpose. This is evidence about what facts
    the site holds, never about how its prose reads: measuring sentence length
    or answer-first structure on a JSON dump would be meaningless, and the
    "what an assistant would quote" table must keep quoting what a machine can
    actually reach today.
    """
    out = []
    total = 0
    for script in soup.find_all("script"):
        kind = (script.get("type") or "").lower()
        if kind and "json" not in kind and "javascript" not in kind:
            continue
        raw = script.string or script.get_text() or ""
        if not raw or len(raw) < 40:
            continue
        for value in re.findall(r'"((?:[^"\\]|\\.){2,300})"', raw):
            value = value.replace(chr(92) + '"', '"').strip()
            if len(value) < 3 or _NOT_SHELL_PROSE.match(value):
                continue
            if not re.search(r"[^\W\d_]", value):
                continue
            out.append(value)
            total += len(value) + 1
            if total >= SHELL_STATE_TEXT_MAX:
                return " ".join(out)
    return " ".join(out)


# How much option text is worth keeping. A size menu is a handful of short
# labels; a country menu is 250 of them and says nothing about the business.
CONTROL_TEXT_MAX = 4000

# How much of a page's text may be removed as "a menu" before the conclusion
# becomes absurd. Same floor and same reasoning as `HIDDEN_STRIP_FLOOR`.
CONTROL_STRIP_FLOOR = 0.10

# How long one option's label may be before it stops being a label. A menu of
# whole paragraphs is a page using `<select>` as a layout device, and its text
# is not a fact about a product.
CONTROL_OPTION_MAX = 120


def _without_form_choices(soup):
    """A copy of this document with its `<select>` menus removed.

    The other half of `_control_text`, and the reason that field can exist at
    all. A menu's labels are choices, not writing: a size menu of forty
    options carries no sentence terminator, so read as prose it welds itself
    onto whatever sentence precedes it and reports a product page as one
    enormous unreadable block, and `above_fold_text` offers the run of size
    codes as what an assistant would quote from the page.

    Everything that reads the page as prose - the body text, the readability
    statistics measured on it, the paragraph and section lists - is taken from
    this copy, and the labels themselves are kept in `control_text`. Nothing is
    lost; the two readings are simply not the same reading.

    Returns the document itself when there is no menu to remove, because
    reparsing costs about as much as the first parse and most pages have none.
    Same guard, for the same reason, as `without_other_peoples_words`.

    And it stands down where the menus are the page. A store locator whose
    body is one `<select>` of two hundred town names has nothing else to read,
    and a removal that leaves almost nothing behind has stopped being about
    furniture and become a claim that the page is empty - which is the reading
    `visible_soup` refuses for the same reason at the same floor.
    """
    if soup.find("option") is None:
        return soup
    clone = make_soup(str(soup))
    for menu in clone.find_all("select"):
        menu.decompose()
    before = len(visible_text(soup))
    if before and len(visible_text(clone)) < before * CONTROL_STRIP_FLOOR:
        return soup
    return clone


def _control_text(soup):
    """The labels a page prints inside its `<select>` menus.

    On a storefront the variant row - size, colour, stock state and the price
    of each - is a `<select>` of `<option>` elements, and that is where the
    price of every variant but the default one is written. A product page
    declaring an `Offer` price of 569.00 carried
    `<option>6 - Sold Out - <price> 569.00</option>` and three more like it,
    and the only figure the audit could see was the 609 printed beside the
    heading, so correct markup was reported at high severity as "structured
    data contradicts what the page actually says".

    Read from the original document, not from any of the cleaned copies, which
    is the whole point of doing it here. Storefront themes deliver this menu in
    three shapes and every one of them is invisible to a copy that has been
    cleaned for prose: inside `<noscript>` as the no-script fallback, behind
    `style="display:none"` while a script paints swatches over it, or outside
    the region `main_text_and_region` picks as the page's content. One rule
    covers all three: the option is in the delivered HTML either way.

    Kept apart from `body_text`, exactly as `shell_state_text` is, and for the
    same reason. Forty size labels are not prose: measured as sentences they
    would report a product page as written in fragments, and offered to the
    "what an assistant would quote" table they would be quoted as the best
    line on the page. This is evidence about which facts the page holds - a
    price, a stock state, a variant name - and about nothing else.
    """
    out, total = [], 0
    for option in soup.find_all("option"):
        label = _text_or_empty(option)
        if not label or len(label) > CONTROL_OPTION_MAX:
            continue
        out.append(label)
        total += len(label) + 1
        if total >= CONTROL_TEXT_MAX:
            break
    return " ".join(out)


# How much of a page's text may be removed as "a code sample" before the
# conclusion becomes absurd. Same floor and same reasoning as
# `CONTROL_STRIP_FLOOR`: an API reference whose page is one long listing has
# nothing else to read, and a removal that leaves almost nothing behind has
# stopped being about samples and become a claim that the page is empty.
CODE_STRIP_FLOOR = 0.10

# How much sample text is worth keeping. A page ships a handful of snippets; a
# generated API reference ships hundreds, and past this the field is a dump
# rather than evidence.
CODE_TEXT_MAX = 8000

# Elements a sentence is written in. A `<code>` inside one of these is a span
# in that sentence - "set `--jobs` to 4" - and removing it would corrupt the
# prose rather than clean it, which is the boundary this whole reading turns
# on. A `<code>` whose parent is a plain container with no other words in it is
# a sample standing on its own.
_PROSE_PARENTS = frozenset({
    "p", "li", "td", "th", "dt", "dd", "a", "span", "em", "strong", "b", "i",
    "h1", "h2", "h3", "h4", "h5", "h6", "label", "figcaption", "caption",
    "summary", "blockquote", "small", "sup", "sub", "abbr", "cite", "q",
})


def _code_samples(soup):
    """The elements on this page that are a code sample rather than a sentence.

    `<pre>` is one by definition: the element means "this text is laid out, not
    written". A `<code>` is one only when it stands on its own - it carries a
    line break, or the block it sits in says nothing else - because the same
    tag is how every documentation site writes a flag name inside a sentence.
    """
    out = []
    for node in soup.find_all(["pre", "code"]):
        if node.name == "pre":
            out.append(node)
            continue
        if node.find_parent("pre") is not None:
            continue                      # goes with the block it sits in
        if "\n" in node.get_text("\n").strip():
            out.append(node)
            continue
        parent = node.parent
        if parent is None or parent.name in _PROSE_PARENTS:
            continue
        beside = word_count(parent.get_text(" ")) - sum(
            word_count(inner.get_text(" ")) for inner in parent.find_all(["pre", "code"]))
        if beside <= 0:
            out.append(node)
    return out


def _without_code_samples(soup):
    """A copy of this document with its code samples removed.

    The other half of `code_text`, and the same separation `_without_form_choices`
    makes for `<select>` menus. A code sample is text the page shows and not
    text the page says, and read as prose it is measured as writing:

      "8 pages are written in       the "sentences" quoted underneath were
      sentences too long to quote"  `Def <U+2506> Speed <U+2506> Generation ...` and
                                    `("{result}"); shape: (163, 3) ...` - the
                                    ASCII output tables of a dataframe library,
                                    sampled out of this file's own `body_text`
      "references 2024 in           the year was inside `# As of 14th October
      "DataFrame ({# As of 14th     2024, ~3pm UTC`, a Python comment in a demo
      October 2024, ~3pm UTC ..."   snippet, and the fix offered was a rewrite
                                    of copy nobody had written

    Everything that reads the page as prose - the body text, the readability
    statistics, the paragraph and section lists - is taken from this copy, and
    the samples themselves are kept in `code_text`. Nothing is lost; a check
    that wants to know whether a page carries code reads that field.

    Returns the document itself when there is no sample to remove, because
    reparsing costs about as much as the first parse.
    """
    if soup.find("pre") is None and soup.find("code") is None:
        return soup
    clone = make_soup(str(soup))
    samples = _code_samples(clone)
    if not samples:
        return soup
    for sample in samples:
        sample.decompose()
    before = len(visible_text(soup))
    if before and len(visible_text(clone)) < before * CODE_STRIP_FLOOR:
        return soup
    return clone


# Program source that is no longer inside a `<script>` element, and how it got
# there.
#
# A real report opened its identity sentence with, verbatim:
#
#   Its homepage says: "<br><br><hr width="80%" align="center"
#   color="#66CCFF"></div></body></html>年undefined月undefined日undefined時現在の貯水率"
#
# That is a city site's JavaScript. Removing `<script>` elements cannot reach
# it, because by the time the document is parsed there is no script element
# around it: an unescaped `</script>` inside a JavaScript string literal ends
# the element there, and every parser and every browser does the same thing
# with it. `document.write("<script src=...></script>")` is the commonest way
# to write one, and a page that ships it hands the rest of its own source to
# the document as text. Reproduced through this extractor: `body_text` came
# back as `city "); yyyymmddhh = data[0] + "年" + ... document.write("`.
#
# So the reading is the same separation `_code_text` and `_control_text`
# already make. Source is text a page shows by accident, never text a page
# says, and the field that carries the identity sentence is the one place it
# does the most damage: the whole entity-definition mechanism rests on that
# sentence, and source in it makes the report look broken to the one reader
# who matters.
#
# Marks, not one pattern: any single one of these appears in real prose
# somewhere ("undefined" in a dictionary, an angle bracket in a comparison, a
# semicolon after a quote), and two of them together in one block do not.
#
# `undefined` is the exception, and the rule names it on its own: reject any
# candidate sentence containing `<`, `>` or a bare `undefined`. It rejects a
# candidate sentence by itself in `reads_as_source_not_prose`, and counts as
# one mark among the others when a whole text node is being judged, because
# the word is a real English adjective a dictionary or a style guide may write
# on purpose.
_BARE_UNDEFINED_RE = re.compile(r"\bundefined\b")

_SCRIPT_SOURCE_MARKS = (
    # A call into the document or the browser.
    re.compile(r"\b(?:document|window|navigator|location)\s*\.\s*[A-Za-z_$]"),
    # A declaration.
    re.compile(r"\b(?:var|let|const|function)\s+[A-Za-z_$][\w$]*\s*[=(]"),
    # The tail of a call whose head was inside the element that ended: `");`
    re.compile(r"[\"']\s*\)\s*;"),
    # Subscripting or concatenating, which prose does not do.
    re.compile(r"[\w$]\s*\[\s*[\"'\d]"),
    re.compile(r"[\"']\s*\+\s*[\w$\"']"),
    _BARE_UNDEFINED_RE,
    # Markup arriving as text, which is what a written-out tag is.
    re.compile(r"</?[A-Za-z][\w-]*(?:\s[^<>]*)?>"),
    # The comment wrapper a dated page hides its script in.
    re.compile(r"<!--|//\s*-->"),
)

# How many marks make a block source rather than writing. Two: one is a
# coincidence a real sentence can produce, and the removal runs after
# `_without_code_samples`, so a page that displays code has already had it
# taken out and cannot be reached by this at all.
SCRIPT_SOURCE_MIN_MARKS = 2

# Below this a text node is too short to be judged by marks - a two-character
# node holding `;` says nothing either way.
_SCRIPT_SOURCE_MIN_CHARS = 20

# No floor here, unlike `_without_code_samples`, `_without_form_choices` and
# `visible_soup`, and the difference is measured. Those three stand down when
# they would remove most of a page, because each is a guess about markup
# conventions - a class name, a landmark, a `<select>` - and a guess that
# deletes a page has stopped being about furniture. This is not a guess: each
# node is read and identified as source. And on the page that raised the
# defect the source *is* most of the delivered text - "city" survives out of
# 110 characters - so a floor would stand the rule down on exactly the shape it
# was written for, which is what a first attempt did.
#
# Nothing downstream is put at risk by the page coming out short. `text` and
# `spa_shell.visible_text_len` are both measured on the delivered document
# before this runs, and an earlier fix made every thin-page and shell signal read those
# rather than `body_text`, so a page emptied here cannot become a false "this
# page delivers nothing" finding.

# Where source may legitimately sit as text. `_without_code_samples` has
# already removed the block samples; these are the elements whose contents are
# not prose in the first place.
_NOT_PROSE_PARENTS = frozenset({"script", "style", "template", "noscript",
                                "pre", "code", "kbd", "samp", "textarea"})


def reads_as_source_not_prose(text):
    """True where this string is program source or markup rather than writing.

    Public, because the field this protects is read in three other skills and
    the sentence that reaches a report is chosen there. The first two lines of
    it are the rule: reject any candidate sentence containing `<`, `>` or a
    bare `undefined` before it can become the defining sentence. A sentence carrying an angle bracket is either markup written
    out as text or the residue of a script, and neither is a sentence a site
    wrote about itself; a sentence carrying `undefined` is a value a page never
    meant to print.
    """
    text = text or ""
    if "<" in text or ">" in text:
        return True
    if _BARE_UNDEFINED_RE.search(text):
        return True
    return _reads_as_script_source(text)


def _reads_as_script_source(text):
    """The marks test alone, without the angle-bracket rule above it."""
    if len(text or "") < _SCRIPT_SOURCE_MIN_CHARS:
        return False
    return sum(1 for rule in _SCRIPT_SOURCE_MARKS
               if rule.search(text)) >= SCRIPT_SOURCE_MIN_MARKS


def _leaked_source_nodes(soup):
    """The text nodes in this document that are program source."""
    out = []
    for node in soup.find_all(string=True):
        parent = getattr(node, "parent", None)
        if parent is not None and parent.name in _NOT_PROSE_PARENTS:
            continue
        if _reads_as_script_source(str(node).strip()):
            out.append(node)
    return out


def _without_leaked_script(soup):
    """A copy of this document with source that escaped its `<script>` removed.

    See `_SCRIPT_SOURCE_MARKS` for how source gets out of a script element and
    what it cost when it did. Everything that reads the page as prose is taken
    from this copy; `text` still holds the whole delivered document, so the
    residue is reattributed rather than lost - the same claim
    `_strip_sitewide_boilerplate` makes about the blocks it moves.

    Returns the document itself when there is nothing to remove, which is
    almost every page: the detection pass walks strings and parses nothing, and
    only a document that actually leaked is reparsed.
    """
    if not _leaked_source_nodes(soup):
        return soup
    clone = make_soup(str(soup))
    found = _leaked_source_nodes(clone)
    if not found:
        return soup
    for node in found:
        node.extract()
    return clone


def _code_text(soup):
    """The text of the code samples this page shows.

    Read from the original document for the same reason `_control_text` is:
    this is evidence about what the page carries, not about how it reads, and
    the cleaned copies have already had it taken out.
    """
    out, total = [], 0
    for sample in _code_samples(soup):
        text = re.sub(r"\s+", " ", sample.get_text(" ")).strip()
        if not text:
            continue
        out.append(text)
        total += len(text) + 1
        if total >= CODE_TEXT_MAX:
            break
    return " ".join(out)


def _state_blob_bytes(soup, blobs):
    """How many bytes of hydration payload this document carries.

    Counted per `<script>` element that holds one of the names, which is the
    element the payload is in. It used to be one regular expression matching
    `__NEXT_DATA__` up to the next `</script>`, so the field read zero on every
    document whose payload is not Next's - including the one it was written
    for. A candle shop's homepage carries four state blobs and this returned 0
    for all four, and the number then fed the shell verdict's script total and
    the evidence line that prints it.

    A name in an attribute counts as much as a name in the body: a payload is
    served either as `<script id="__NEXT_DATA__" type="application/json">` or
    as `window.viewerModel = {...}`, and both are the same fact.
    """
    if not blobs:
        return 0
    total = 0
    for script in soup.find_all("script"):
        attributes = " ".join(
            " ".join(v) if isinstance(v, list) else str(v)
            for v in (script.attrs or {}).values())
        body = script.string or script.get_text() or ""
        if any(name in body or name in attributes for name in blobs):
            total += len(body)
    return total


def _spa_shell(soup, html, page_text):
    """Signals that the delivered HTML is a container, not content."""
    root = None
    root_text_len = None
    for selector in SPA_ROOT_SELECTORS:
        node = soup.select_one(selector)
        if node is not None:
            root = selector
            root_text_len = len(re.sub(r"\s+", " ", node.get_text(" ")).strip())
            break
    blobs = [name for name in STATE_BLOB_PATTERNS if name in (html or "")]
    noscript = " ".join(n.get_text(" ") for n in soup.select("noscript")).lower()
    return {
        "root_selector": root,
        "root_text_len": root_text_len,
        "state_blobs": blobs,
        "state_blob_bytes": _state_blob_bytes(soup, blobs),
        "noscript_demands_js": bool(re.search(r"enable\s+javascript|requires?\s+javascript|javascript.{0,20}(is\s+)?(required|disabled)", noscript)),
        "visible_text_len": len(page_text),
    }


def _breadcrumb(soup, jsonld_types, url=""):
    """Whether this page shows a trail, marks one up, or both.

    Kept apart deliberately. A visitor who landed mid-journey needs the visible
    trail; a machine needs the markup. Treating JSON-LD as proof of a visible
    breadcrumb made the visible-trail check impossible to fail.
    """
    visible = bool(soup.select(
        '[class*="breadcrumb" i], [id*="breadcrumb" i], [aria-label*="breadcrumb" i]'))
    # `class` and `id` are not the only places the word lands. A restaurant
    # group marks its trail up as a plain `<ul>` of `<li>`, with the name only
    # in `data-cta-section="breadcrumbs"` on each link - so all 28 of its deep
    # pages were reported as showing "no breadcrumb trail", the advice was
    # "add a breadcrumb above the H1", and the work was already done. Any
    # attribute carrying the word is the site naming the thing.
    if not visible:
        visible = soup.find(_names_a_breadcrumb) is not None
    # Case-insensitive: `jsonld_types` is normalised to lower case by the
    # shared type reader, and a literal `"BreadcrumbList"` test against it
    # silently answered False for every site.
    markup = "breadcrumblist" in {str(t).lower() for t in jsonld_types or ()} or bool(
        soup.select('[itemtype*="BreadcrumbList" i]'))
    # The trail itself, whatever the container is called. Every test above is
    # a test on a name: the English word "breadcrumb" in a class, an id, an
    # aria-label or any other attribute. A Japanese tea grower's theme names
    # the element `class="pankuzu"` - Japanese for breadcrumb - and marks the
    # trail up in full RDFa, `property="itemListElement" typeof="ListItem"`
    # with a `property="name"` inside each link. All 15 of that site's deep
    # pages were reported as showing "no breadcrumb trail and no link up to
    # the section they sit in", and the trail is on every one of them, visible
    # and machine-readable. A word in one language is a hint about a
    # breadcrumb; a chain of typed list items is the breadcrumb.
    chain = _itemlist_trail(soup)
    # The fourth reading, and the one the three above cannot make: a trail
    # with no name, no typed items and no markup of any kind.
    #
    # An Indonesian skincare shop renders, on every product page, a plain
    # `<nav>` holding `<a href="/en">Home</a> / <a href="/en/face">Face</a> /
    # <a href="/en/face/serum">Serum</a> / <the product name>`. There is no
    # class, no id, no aria-label, no `itemListElement` and no word in any
    # language naming the element, so all three tests above answered no and
    # every deep page on that site was reported as showing "no breadcrumb
    # trail and no link up to the section they sit in" - with the trail on the
    # screen. What identifies a trail is not what it is called: it is that its
    # links are the prefixes of this page's own address, in order. See
    # `_ancestor_link_trail`, which reads exactly that.
    ancestors = _ancestor_link_trail(soup, url)
    visible = visible or chain or ancestors
    return {"visible": visible, "markup": markup or chain,
            # Which reading answered, so a check can say what it read rather
            # than implying a `class="breadcrumb"` that is not there.
            "itemlist_chain": chain,
            "ancestor_link_trail": ancestors,
            "any": visible or markup}


# Selectors for one link in a schema.org item list, in both syntaxes a page
# can write it in: RDFa (`property`, `typeof`) and microdata (`itemprop`,
# `itemtype`). `~=` is the whitespace-separated-word match, because both
# attributes hold a list.
_LIST_ITEM_SELECTOR = (
    '[property~="itemListElement"], [itemprop~="itemListElement"], '
    '[typeof~="ListItem"], [itemtype*="ListItem" i]'
)

# What separates a breadcrumb trail from the other thing `itemListElement`
# marks up, which is a carousel of products or articles. A trail is a line: a
# handful of entries, mostly links, whose whole container reads as one line of
# text. A carousel is a block - names, prices, summaries - so it fails the
# character bound long before the item bound.
BREADCRUMB_TRAIL_MAX_ITEMS = 8
BREADCRUMB_TRAIL_MAX_CHARS = 300
BREADCRUMB_TRAIL_MIN_LINKS = 2


def _itemlist_trail(soup):
    """Is there a chain of typed list items on this page shaped like a trail?

    Grouped by shared parent, because that is what makes a chain a chain: two
    unrelated `ListItem`s in different corners of a page are not a trail. The
    bounds above are the shape test, and the container's own name is not
    consulted at all - that is the point.
    """
    items = soup.select(_LIST_ITEM_SELECTOR)
    if len(items) < 2:
        return False
    groups = {}
    for item in items:
        parent = item.parent
        if parent is None:
            continue
        groups.setdefault(id(parent), []).append(item)
    for members in groups.values():
        if not BREADCRUMB_TRAIL_MIN_LINKS <= len(members) <= BREADCRUMB_TRAIL_MAX_ITEMS:
            continue
        parent = members[0].parent
        text = re.sub(r"\s+", " ", parent.get_text(" ")).strip()
        if not text or len(text) > BREADCRUMB_TRAIL_MAX_CHARS:
            continue
        # The last entry of a trail is the page you are on and is usually not
        # a link, so the bound is on how many of the entries lead somewhere,
        # not on all of them.
        linked = sum(1 for member in members
                     if member.name == "a" or member.find("a", href=True) is not None)
        if linked >= BREADCRUMB_TRAIL_MIN_LINKS:
            return True
    return False


def _names_a_breadcrumb(tag):
    """True when any of this tag's attribute values contains "breadcrumb"."""
    for value in (tag.attrs or {}).values():
        if isinstance(value, (list, tuple)):
            value = " ".join(str(v) for v in value)
        if "breadcrumb" in str(value).lower():
            return True
    return False


# What a theme puts between the steps of a trail. Evidence that a row of links
# is one line rather than a menu, so this is the punctuation the world writes
# trails with and never a word in any one language.
_TRAIL_DELIMITERS = u"/>›»→‹•·|\\❯⮞"

# The elements a trail is built out of when it carries no punctuation at all.
# A `<nav>`, an `<ol>` or a `<ul>` of ancestor links is a trail; the same links
# loose in a `<div>` are a menu, and with no delimiter there is nothing else to
# tell the two apart.
_TRAIL_CONTAINERS = frozenset({"nav", "ol", "ul"})

# How much of a container's own links have to be this page's ancestors. Half: a
# site-wide menu carries the section this page sits in among a dozen others,
# and a trail is made of nothing else.
BREADCRUMB_ANCESTOR_SHARE = 0.5

# How far above an ancestor link to look for the element holding the trail. The
# link is routinely wrapped in an `<li>` inside an `<ol>` inside a `<nav>`, and
# stopping at the first parent finds the `<li>` alone.
TRAIL_CONTAINER_LOOKUP = 4


def _ancestor_link_trail(soup, url):
    """Is there a short line of links on this page climbing its own path?

    The reading that needs no name and no markup, for the trail most
    hand-built sites actually ship. `_names_a_breadcrumb` asks what the
    container is called and `_itemlist_trail` asks what its items are typed
    as; a plain `<nav>` of ancestor links separated by a slash answers neither
    and is on the screen.

    Four bounds, and the shape they exist to refuse is why. A twelve-item
    product carousel is also a row of links, and an earlier fix widened this
    check once already, so:

      the links are this page's ancestors  a carousel links to items *below*
                                           the page or beside it, never to the
                                           prefixes of its own address. This is
                                           the bound doing nearly all the work
      they climb, in document order        `Home > Section > Page`, deepening
                                           left to right. A menu listing two
                                           unrelated sections does not
      most of the container's links do     `BREADCRUMB_ANCESTOR_SHARE`. A
                                           site-wide menu carries this page's
                                           section among a dozen others
      it reads as one line                 the same two bounds `_itemlist_trail`
                                           uses - at most eight entries, at
                                           most 300 characters of text

    The current page's own entry is deliberately not required to be a link:
    the last step of a trail is where the visitor already is, and most themes
    print it as plain text.
    """
    page_path = _trail_path(url)
    if page_path.count("/") < 2:
        # One segment deep, so the only ancestor is the homepage, and a single
        # link home is a header rather than a trail.
        return False
    anchors = []
    for anchor in soup.find_all("a", href=True):
        target = normalise_url(anchor.get("href") or "", url)
        if not target or not same_site(target, url):
            continue
        path = _trail_path(target)
        if path == page_path or not page_path.startswith(
                path + "/" if path != "/" else "/"):
            continue
        anchors.append((anchor, path))
    if len({path for _a, path in anchors}) < BREADCRUMB_TRAIL_MIN_LINKS:
        return False
    # Keyed on each anchor's identity rather than on its address, because the
    # same address is linked from the menu and from the trail on one page and
    # only one of those two elements is the trail.
    climbing = {id(anchor): path for anchor, path in anchors}
    tested = set()
    for anchor, _path in anchors:
        container, steps = anchor.parent, 0
        while container is not None and steps < TRAIL_CONTAINER_LOOKUP:
            if id(container) not in tested:
                tested.add(id(container))
                if _reads_as_a_trail(container, climbing,
                                     page_path.rsplit("/", 1)[0] or "/"):
                    return True
            container, steps = container.parent, steps + 1
    return False


def _reads_as_a_trail(container, climbing, parent_path):
    """Does this element hold a short line of links climbing the page's path?

    `climbing` maps the id of each anchor whose destination is an ancestor of
    the current page to that ancestor's path. `parent_path` is the section the
    page sits in directly.
    """
    links = container.find_all("a", href=True)
    if len(links) > BREADCRUMB_TRAIL_MAX_ITEMS:
        return False
    text = re.sub(r"\s+", " ", container.get_text(" ")).strip()
    if not text or len(text) > BREADCRUMB_TRAIL_MAX_CHARS:
        return False
    steps = [climbing[id(link)] for link in links if id(link) in climbing]
    if len(steps) < BREADCRUMB_TRAIL_MIN_LINKS:
        return False
    if len(steps) < len(links) * BREADCRUMB_ANCESTOR_SHARE:
        return False
    # In document order, deepening, and each step once. A footer linking
    # `/shoes` twice, or a menu naming a grandparent after its child, is not
    # anybody's path through the site.
    if len(set(steps)) != len(steps) or steps != sorted(steps,
                                                        key=lambda p: p.count("/")):
        return False
    delimited = any(ch in text for ch in _TRAIL_DELIMITERS)
    if not delimited and container.name not in _TRAIL_CONTAINERS:
        return False
    # Either the steps are separated by punctuation a reader sees as one line,
    # or the last of them is the section this page sits in directly. One of the
    # two has to hold: a row of ancestor links that stops two levels up, in a
    # `<div>` with no delimiter, is the site's menu agreeing with this page's
    # address by accident.
    return delimited or steps[-1] == parent_path


def _trail_path(url):
    """The path of `url`, with no trailing slash and never empty."""
    return (urlparse(url or "").path or "/").rstrip("/") or "/"


_SEARCH_HREF_RE = re.compile(r"/search(?![a-z])|[?&](?:q|s|query|search)=", re.I)


def _submits_off_site(form, origin):
    """Does this form post to a host that is not the site being audited?

    A relative action, or none at all, submits to the page it sits on.
    """
    action = (form.get("action") or "").strip()
    if not action or "//" not in action.split("?", 1)[0]:
        return False
    return not same_site(action if action.startswith("http") else "https:" + action,
                         origin or "")


def _has_search(soup, origin=""):
    """Does this page offer a way to search the site?

    A form is the obvious shape and not the only one. Two sites in one
    validation pass were told "the site has no on-site search, so SearchAction markup would
    describe a capability that does not exist" - one has a `/search` page
    linked from its own header, the other answers `?s=&q=` on every path. The
    finding was about a capability both of them have.
    """
    # Somebody else's search box is not this site's search. The link branch
    # below has said so since it was written - "a footer link to a search
    # engine is somebody else's search box" - and the form branch did not, so
    # a form posting to a web search engine set this boolean on two sites that
    # have no search index of their own. Both were then told to publish a
    # `SearchAction` whose `target` is that engine's URL, which declares
    # nothing about their own site and buys them nothing. The true and more
    # useful observation is the opposite one: these sites have no on-site
    # search.
    if any(f["is_search"] and not f["submits_off_site"] for f in _forms(soup, origin)):
        return True
    for node in soup.select('input[type="search"], form[role="search"], [role="searchbox"]'):
        form = node if node.name == "form" else node.find_parent("form")
        if form is None or not _submits_off_site(form, origin):
            return True
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        # Relative or same-document only. A footer link to a search engine is
        # somebody else's search box.
        if "//" in href.split("?", 1)[0]:
            continue
        if _SEARCH_HREF_RE.search(href):
            return True
    return False


def _cta(soup, body_text, origin, base):
    """The first call-to-action link and where it sits in the body text.

    Position matters: a CTA 4,000 characters down is not orientation.
    """
    lowered = body_text.lower()
    first_offset = None
    first_text = ""
    first_url = ""
    in_main = False
    first_located = False
    main_anchors = {id(a) for a in main_region(soup).find_all("a", href=True)}
    for anchor in soup.find_all("a", href=True):
        text = _text_or_empty(anchor)
        low = text.lower().strip()
        if not low or len(low) > 60:
            continue
        first_word = re.sub(r"[^a-z]", "", low.split()[0]) if low.split() else ""
        marker = " ".join(filter(None, [
            " ".join(anchor.get("class") or []), anchor.get("id") or "",
            anchor.get("role") or "",
        ]))
        is_cta = ((first_word in CTA_VERBS and not _VERB_IS_AN_ADJECTIVE_HERE.match(low))
                  or bool(CTA_MARKUP_RE.search(marker)))
        if is_cta:
            offset = lowered.find(low[:40])
            located = offset >= 0
            if not located:
                # Sorts last without pretending to be a position. Anything
                # printing this number must check `offset_known` first.
                offset = UNLOCATED_CTA_OFFSET
            if first_offset is None or offset < first_offset:
                first_offset = offset
                first_located = located
                first_text = text
                first_url = normalise_url(anchor["href"], base) or ""
                # Set here, with the rest of this anchor's facts. Set outside
                # the branch it described *any* qualifying anchor, so the
                # record could name a footer "Contact" link as the page's call
                # to action while asserting it sits in the main region - and
                # the dead-end check reads exactly that pair.
                in_main = id(anchor) in main_anchors
    buttons = len(soup.select('button, [role="button"], input[type="submit"], .btn, .button'))
    return {
        "found": first_offset is not None,
        "text": truncate(first_text, 80),
        "url": first_url,
        "offset": first_offset if first_offset is not None else -1,
        # False when the link exists but its label could not be found in the
        # body copy, so `offset` is a sort key and not a character position.
        "offset_known": bool(first_offset is not None and first_located),
        "within_first_1500": bool(first_offset is not None and first_offset <= 1500),
        # A "Contact" link in the global footer is not this page's next step.
        # Without this distinction the dead-end check was unfirable.
        "in_main": in_main,
        "button_count": buttons,
    }


# How far after a copyright mark a date still belongs to the copyright line.
# A footer reading "(c) 1448 AH / 2026 CE <site name>, network last updated on:
# 30/3/1448 AH" puts both dates within sixty characters of the mark.
COPYRIGHT_LINE_CHARS = 120
_COPYRIGHT_MARK_RE = re.compile(u"©|\\(c\\)|\\bcopyright\\b|all rights reserved", re.I)


def _site_chrome_text(soup):
    """The text of the site-wide header and footer, each region once.

    `CHROME_SELECTOR`, less two things that are not site chrome: an `address`
    block, which is contact detail wherever it sits, and a header or footer
    inside the article or the main region, which is the article's own - its
    byline and its date live exactly there.
    """
    picked = []
    for node in soup.select(CHROME_SELECTOR):
        if node.name == "address" or _within_hidden(node):
            continue
        if node.find_parent(["article", "main"]) or node.find_parent(
                attrs={"role": "main"}):
            continue
        if any(parent in picked for parent in node.parents):
            continue
        picked.append(node)
    return " ".join(_text_or_empty(node) for node in picked)


def _only_in_the_chrome(printed, text, chrome_text):
    """Is every appearance of this date in the site's header, footer or
    copyright line?"""
    positions = [m.start() for m in re.finditer(re.escape(printed), text or "")]
    if not positions:
        return False
    if chrome_text and text.count(printed) <= chrome_text.count(printed):
        return True
    return all(_COPYRIGHT_MARK_RE.search(text[max(0, at - COPYRIGHT_LINE_CHARS):at])
               for at in positions)


def _dates(soup, text, jsonld, lang="", script="", chrome_text=""):
    """Every freshness signal a machine could read off this page.

    `lang` is what the document declares and `script` is what its letters are
    in. Both are read for one question only - whether a four-digit number in
    the 2400-2600 range is a Buddhist-era year - and the answer is no wherever
    neither says Thai. A wrongly converted date is worse than an unread one.

    `chrome_text` is the site-wide header and footer (`_site_chrome_text`). A
    date printed only there, or only in the copyright line, is the template's
    and not the page's: a site's footer reads "copyright 1448 AH, network last
    updated on 30/3/1448 AH" on every page - its guestbook included - and the
    date moves on every load, so every page was recorded as carrying a date
    and none of them was dated by it. Those go to `chrome`, and neither
    `visible`, `non_gregorian` nor `has_any` counts them.
    """
    chrome = []
    machine = []
    for tag in soup.find_all("time"):
        value = tag.get("datetime") or _text_or_empty(tag)
        if value:
            machine.append(value.strip())
    for node in jsonld:
        for key in ("datePublished", "dateModified", "uploadDate", "dateCreated"):
            value = node.get(key)
            if isinstance(value, str) and value:
                machine.append(value.strip())
    # The one candidate shape that cannot prove its own calendar is dropped
    # where the page gives no evidence for it: `31/03/2563` on a page written
    # in a Latin script is not a date at all, and reading it as one would put
    # an invented year in a report as the site's own.
    visible = []
    for match in DATE_TEXT_RE.finditer(text):
        printed = match.group(0)
        if (BUDDHIST_ERA_DATE_RE.fullmatch(printed.strip())
                and not reads_in_a_buddhist_era_calendar(script, lang)):
            continue
        if _only_in_the_chrome(printed, text, chrome_text):
            if printed not in chrome:
                chrome.append(printed)
            continue
        visible.append(printed)
        if len(visible) == 10:
            break
    # What those dates say in the common era. Read through the shared readers,
    # which is the whole point of this change: they existed, they were tested,
    # and nothing called them, so 14 pages of one site and 16 of another
    # printing `พ.ศ. 2569` were recorded `has_any: false` and the report said
    # "48 of 48 pages where one is expected show no date on the page".
    non_gregorian = []
    for printed, year in non_gregorian_dates(text, script, lang):
        if _only_in_the_chrome(printed, text, chrome_text):
            if printed not in chrome:
                chrome.append(printed)
            continue
        non_gregorian.append((printed, year))
    copyright_years = []
    for match in COPYRIGHT_YEAR_RE.finditer(text):
        copyright_years.append(int(match.group(1)))
        # Some footers list every year rather than a range ("© 2002 2003 2004
        # … 2026"). Reading only the anchored year reported the site as 24
        # years stale, so take the whole run that follows.
        tail = text[match.end():match.end() + 160]
        run = re.match(r"(?:\s*[-–—,]?\s*(?:19|20)\d{2})+", tail)
        if run:
            copyright_years.extend(int(y) for y in re.findall(r"(?:19|20)\d{2}", run.group(0)))
    as_of_years = [int(m.group(1)) for m in AS_OF_YEAR_RE.finditer(text)]
    return {
        "machine_readable": sorted(set(machine))[:10],
        "visible": visible,
        # The dates above that are not in the Gregorian calendar, each beside
        # the year it states in the common era. Kept apart from `visible`
        # rather than folded into it, because `visible` is quoted in reports as
        # what the page prints and a converted date is a string no page
        # carries. A check that wants to date one of these pages reads the year
        # from here; `freshness-corroboration-audit::newest_date` still parses
        # `visible` only, so it dates these pages no more precisely than
        # before - it just no longer says they carry no date.
        "non_gregorian": [{"printed": printed, "gregorian_year": year}
                          for printed, year in non_gregorian],
        # Dates printed only in the site's header, footer or copyright line.
        # Kept, so a reader can see what was set aside; never a date of this
        # page. See the docstring.
        "chrome": chrome[:10],
        "copyright_years": sorted(set(copyright_years)),
        "as_of_years": sorted(set(as_of_years)),
        # A date signal has to be a date. `<time datetime="PT0S">` is a video
        # player's duration and `<time>Remaining time unknown</time>` is its
        # countdown, and both were counted as the page carrying a publication
        # date - so a page with no date on it was excluded from the "pages
        # with no date" list, and the reader who fixed exactly the pages named
        # would have left that one behind believing it was already done.
        # `_DATE_SHAPED_RE` anchors every year it knows on `(?:19|20)\d{2}`, so
        # a date read in another calendar can never satisfy it - and a date
        # captured, written into the snapshot and then reported absent is the
        # worse half of this defect, not the smaller one. The readers settle it
        # for the shapes they cover.
        "has_any": any(_looks_like_a_date(v) for v in list(machine) + list(visible)
                       ) or bool(non_gregorian),
        "jsonld_date_published": _first_jsonld_date(jsonld, "datePublished"),
        "jsonld_date_modified": _first_jsonld_date(jsonld, "dateModified"),
    }


# A year, in any of the shapes a date signal is written in. Deliberately loose
# - freshness-corroboration-audit does the real parsing, and this only has to
# separate "a date" from "a video duration".
# A CJK year marker follows the digits with no word boundary between them, so
# the last alternative below could never match a date written that way.
# `DATE_TEXT_RE` above has a CJK branch, added after a postal group's article
# showed such a date and the report said the page carried none; this pattern,
# which sets `has_any`, never got the same branch. The result was worse than
# the bug it was meant to fix: the date was captured, written into the
# snapshot, and then reported as absent. Every site writing dates this way was
# told at high confidence that its pages carry no date at all.
_DATE_SHAPED_RE = re.compile(
    r"(?:19|20)\d{2}-\d{1,2}-\d{1,2}"
    r"|\d{1,2}[/.]\d{1,2}[/.](?:19|20)\d{2}"
    "|(?:19|20)\\d{2}\\s*[\\u5e74\\ub144]"
    r"|\b(?:19|20)\d{2}\b"
)


def _looks_like_a_date(value):
    """True if this could be a date, false for `PT0S` and `--:--`."""
    return bool(_DATE_SHAPED_RE.search(str(value or "")))


def _first_jsonld_date(jsonld, key):
    for node in jsonld:
        value = node.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


# Words that name a number as something other than a postcode. A footer is an
# address region, and it is also where every regulator, registrar and tax
# authority requires a number to be printed - so shape alone read a bank's
# Financial Services Register number as its postcode and published it inside
# paste-ready `PostalAddress` markup.
#
# The rule generalises past that one case: a company registration number, a
# VAT number, a charity number, a licence number and an item ID are all
# five-to-six digit runs sitting in a footer behind a word that says what they
# are. Reading the label is the only thing that separates them, and it is the
# same read for all of them.
_NUMBER_LABELLED_OTHERWISE_RE = re.compile(
    r"(?:number|no\.?|nr\.?|reference|ref\.?|registration|registered|register|"
    r"vat|gst|abn|acn|company|charity|licen[cs]e|permit|firm|policy|invoice|"
    r"order|account|item|sku|id|code|isbn|ein|tin|duns)\W{0,12}$",
    re.I)

# ... unless the label says postcode, which settles it the other way.
_POSTCODE_LABEL_RE = re.compile(
    r"(?:post\s?code|postal\s?code|zip(?:\s?code)?|plz|cap|c\.?p\.?)\W{0,12}$", re.I)


def _labelled_as_another_kind_of_number(haystack, start):
    """Does the wording just before this number say it is not a postcode?"""
    before = haystack[max(0, start - 60):start]
    if _POSTCODE_LABEL_RE.search(before):
        return False
    return bool(_NUMBER_LABELLED_OTHERWISE_RE.search(before))


def _postcode_in(haystack):
    """The first postcode-shaped run the surrounding words do not disown."""
    declared = JP_POSTCODE_RE.search(haystack)
    if declared:
        return declared          # 〒 says what the number is; nothing to infer
    for match in POSTCODE_HINT_RE.finditer(haystack):
        if not _labelled_as_another_kind_of_number(haystack, match.start()):
            return match
    return None


# How far behind the street a postal code may sit and still belong to it. Room
# for a locality and a county - "South Parks Road, <City>, <County> OX1 3PP" -
# and no more.
POSTCODE_NEAR_STREET_WINDOW = 48


def _postcode_beside(text, street):
    """The postal code that finishes the street's own line, if there is one.

    Read from the window after the street rather than from anywhere on the
    page, so the two halves of a reported address are two halves of one line
    the page actually prints. A coffee retailer's report quoted `"27 Monmouth
    Street SE16 4RA"` as its location - a string appearing nowhere on the site,
    because the street came from one shop's page copy and the postal code from
    a different shop four miles away, and the report presented the join as a
    quotation.

    The match is against the window, so its offsets are the window's. Only the
    matched text is ever used.
    """
    if street is None:
        return None
    return _postcode_in(text[street.end():street.end() + POSTCODE_NEAR_STREET_WINDOW])


def _postcode_hint(text, soup, street, street_text=None):
    """A postal code, read from somewhere that is describing an address.

    Returns `(match, where)` - the match, and which of the three readings
    below produced it, because they are not equally the page's own.

    `street_text` is the string the street match indexes into, which is not
    always the page text: an address read out of a footer or an `<address>`
    element was matched against that element's own text. Slicing the page text
    at a footer offset lands in the middle of an unrelated sentence, so the
    two halves of the quoted line would come from two different places - the
    exact defect `_postcode_beside` was written to stop.

    A code sitting immediately behind the street is the strongest reading there
    is, and it is tried first: those two are one address line, so the pair can
    be quoted as the page wrote it.

    Failing that, an address region is authoritative: whatever postcode-shaped
    run sits in an `<address>` block or a footer is a postcode - unless the
    words in front of it name it as a different kind of number, which in a
    footer they often do. Outside an address region the shape is not enough on
    its own - it is also an item number, an order reference and a five-digit
    price - so the whole page counts only when a street-shaped phrase
    corroborates it.
    """
    beside = _postcode_beside(text if street_text is None else street_text, street)
    if beside:
        return beside, "beside the street"
    for selector in ADDRESS_REGION_SELECTORS:
        for node in soup.select(selector):
            match = _postcode_in(re.sub(r"\s+", " ", node.get_text(" ")))
            if match:
                # Named, because this one may be the template's rather than
                # the page's. `ADDRESS_REGION_SELECTORS` ends in `footer`, and
                # a site-wide footer carries the head office's code on every
                # page - so four campus pages, each printing its own street,
                # were all given the same postcode, the multi-location check
                # keyed them on the postcode alone, and a four-campus
                # university counted as one place. The whole multi-location
                # branch of that report went dark.
                return match, "in an address region or the site footer"
    return (_postcode_in(text), "elsewhere on the page") if street else (None, "")


# A URL is not prose, and the digits inside one are not facts about the
# business. A recipe blog's contact detail was reported as the telephone
# number 3922587923, which is the middle of a photo-sharing URL ending
# `/photos/<user>/39225879230/` - an image id, printed as the site's way of
# getting in touch, and enough on its own to suppress the finding that the
# site states no contact method at all. Filenames go too, for the same
# reason: `IMG_20240115_094433.jpg` is a date and a time, and neither is
# about anybody.
_URL_OR_FILENAME_RE = re.compile(
    r"(?:https?://|www\.)\S+"
    r"|\S+\.(?:com|org|net|edu|gov|io|co|uk|jp|de|fr)/\S*"
    r"|\S+\.(?:jpg|jpeg|png|gif|webp|svg|pdf|mp4|json|js|css)\b",
    re.I)


def text_without_urls(text):
    """Page text with web addresses and filenames taken out."""
    return _URL_OR_FILENAME_RE.sub(" ", text or "")


# Words that introduce a telephone number, in the languages this crawl has met
# them in. A positive signal, not a blocklist: where a page labels one of its
# runs of digits as a telephone number, that run is the telephone number and
# the others are not, whatever shape they happen to have.
_PHONE_LABEL_RE = re.compile(
    r"(?:tel|telephone|phone|mobile|cell|fax|call us|call"
    r"|t\u00e9l|telefon|tel\u00e9fono|telefone|telefoon"
    r"|\u96fb\u8a71|\uc804\ud654|\u7535\u8bdd"
    r"|\u0442\u0435\u043b\u0435\u0444\u043e\u043d)"
    r"[\s.:#\-\uff1a]*$", re.I)


def _dialable_runs(text, limit=3):
    """Runs of digits on this page that are plausibly telephone numbers.

    Two failures this exists to stop, both of which put a junk number into a
    report as the site's way of getting in touch - and, worse, marked the
    "states a contact method" check as passed, so the finding that the site
    states none was suppressed.

      a hosted store's internal plan id, ten digits, printed as the shop's
      telephone number while the real one sat on its contact page

      a city hall's thirteen-digit corporate registry number, taken as the
      city's telephone number while the footer of every page carried the real
      one behind the word for "telephone"

    `not_a_telephone_number` already knew the second was a code and nothing
    called it here, so the rule was written down and never applied to the
    place the number enters the snapshot. And where a page labels a number,
    the label settles it: a labelled run beats an unlabelled one, which is the
    signal that separates the registry number from the telephone number on a
    page carrying both.
    """
    labelled, unlabelled = [], []
    for match in PHONE_RE.finditer(text):
        run = match.group(0).strip()
        if len(re.sub(r"[^0-9]", "", run)) < 9:
            continue
        before = text[max(0, match.start() - CODE_LABEL_WINDOW):match.start()]
        # Both sides. A run with letters glued to either end is a slice of one
        # longer token - a commit id, a build fingerprint, a serial - and only
        # the right-hand side can show that for a run that starts a token.
        after = text[match.end():match.end() + CODE_LABEL_WINDOW]
        if not_a_telephone_number(run, before, after):
            continue
        (labelled if _PHONE_LABEL_RE.search(before) else unlabelled).append(run)
    return (labelled or unlabelled)[:limit]


# --------------------------------------------------------------------------
# Click-to-chat links, which for a large share of businesses in South and
# South-East Asia are the only place the telephone number appears.
#
# An Indonesian shoe brand's homepage carries
# `<a href="https://wa.me/6282127323136">`, and the report said "no email
# address, telephone number, contact form, mailto: or tel: link and no
# ContactPoint markup was found on any crawled page" - over a number that is in
# the delivered bytes of a page this crawl fetched.
#
# Widening the text patterns could never have reached it. `text_without_urls`
# runs in front of every text scan in this file, deliberately, because the
# digits inside a URL are not facts about the business; a photo id in a
# `/photos/<user>/39225879230/` path was once published as a recipe blog's
# telephone number. The number here is in the href and nowhere else, so the
# href is where it has to be read - as a declared number, in the same class as
# a `tel:` link, not as a run of digits scraped out of prose.
#
# Which link shapes are read, and why the list stops there. Only the URL slots
# whose definition is "a telephone number in international format":
#
#   wa.me/<number>                     WhatsApp click-to-chat, the shape above
#   api|web.whatsapp.com/send?phone=   the same route written out
#   whatsapp://send?phone=             the app's own scheme
#   viber://chat?number=               Viber, same slot, same meaning
#   sms:/smsto: <number>               the messaging sibling of `tel:`
#
# Everything else a business publishes as a chat route addresses a *handle*,
# not a number, so there is no number in it to read and guessing one would be
# invention: `m.me/<page>`, `t.me/<name>`, LINE's `line.me/ti/p/~<id>`, Skype's
# `skype:<name>`, and WhatsApp's own `wa.me/message/<code>` short link and
# `chat.whatsapp.com/<invite>` group link. Reading any of those, or reading a
# bare host match instead of a defined number slot, is how a "share this on
# WhatsApp" button would start being published as the company's phone number.
#
# The two share shapes are excluded by construction rather than by a rule:
# `wa.me/?text=...` has an empty path and `api.whatsapp.com/send?text=...` has
# no `phone` parameter, so neither yields any digits.
_CLICK_TO_CHAT_HOSTS = ("wa.me", "api.whatsapp.com", "web.whatsapp.com")
_CLICK_TO_CHAT_SCHEMES = ("whatsapp", "viber")
_CLICK_TO_CHAT_NUMBER_KEYS = ("phone", "number")
_SMS_SCHEMES = ("sms", "smsto")

# E.164 bounds: a country code plus a subscriber number, fifteen digits at
# most. The floor is eight rather than the nine `_dialable_runs` applies to
# prose because a number in one of these links always carries its country code
# and is never a bare local run, so the short readings that floor guards
# against cannot occur here.
CLICK_TO_CHAT_MIN_DIGITS = 8
CLICK_TO_CHAT_MAX_DIGITS = 15

# What may sit in the number slot: digits, with the punctuation a person types
# into one. Anything with a letter in it is a handle - `wa.me/message/CJ7K2Q`
# is the shape this rejects - and a handle is not a number.
_NUMBER_SLOT_RE = re.compile(r"^\+?[\d][\d\s.() -]*$")


def _phone_from_click_to_chat(href):
    """The telephone number a click-to-chat link addresses, or ''.

    Returned in international format with the `+` the URL leaves off. The `+`
    is not decoration: `not_a_telephone_number` reads thirteen unbroken digits
    as a barcode, and 6282127323136 - the Indonesian number this was written
    for - is exactly thirteen. Written as `+6282127323136` it is what the site
    means and what a person would dial.
    """
    href = (href or "").strip()
    if not href or href.startswith("#"):
        return ""
    try:
        parsed = urlparse(href)
    except ValueError:
        return ""
    scheme = (parsed.scheme or "").lower()
    host = strip_www((parsed.netloc or "").lower().split(":")[0])
    raw = ""
    if scheme in _SMS_SCHEMES:
        # `sms:+62812...?body=...`, and the comma form `sms:+62812,+62813`.
        # The body is a message, not a number.
        raw = (parsed.path or "").split("?")[0].split(",")[0]
    elif scheme in _CLICK_TO_CHAT_SCHEMES or host in _CLICK_TO_CHAT_HOSTS:
        query = parse_qs(parsed.query or "")
        for key in _CLICK_TO_CHAT_NUMBER_KEYS:
            values = [v for v in (query.get(key) or []) if (v or "").strip()]
            if values:
                raw = values[0]
                break
        if not raw and host == "wa.me":
            raw = (parsed.path or "").strip("/")
    raw = raw.strip()
    if not raw or not _NUMBER_SLOT_RE.match(raw):
        return ""
    digits = re.sub(r"\D", "", raw)
    if not CLICK_TO_CHAT_MIN_DIGITS <= len(digits) <= CLICK_TO_CHAT_MAX_DIGITS:
        return ""
    number = "+" + digits
    # The same gate every other number in this file passes. A run that is a
    # book number or a barcode is not a telephone number because somebody put
    # it in a chat link.
    return "" if not_a_telephone_number(number) else number


def _looks_like_the_same_number(one, other):
    """Are these two writings of one telephone number?

    A site that carries both `tel:081227323136` and `wa.me/6281227323136` has
    stated one number twice: the national trunk zero in the first is what the
    country code in the second replaces. Recording it as two numbers is what
    turns one contact route into a self-contradiction downstream, where the
    numbers a site declares are compared between its pages.
    """
    left = re.sub(r"\D", "", one or "").lstrip("0")
    right = re.sub(r"\D", "", other or "").lstrip("0")
    if not left or not right:
        return False
    shorter, longer = sorted((left, right), key=len)
    return len(shorter) >= CLICK_TO_CHAT_MIN_DIGITS and longer.endswith(shorter)


def _declared_phones(node, limit=5):
    """Numbers this page states in a link rather than in its prose.

    `tel:` first, because it is the unambiguous declaration and the number is
    written the way the site writes it everywhere else. Click-to-chat links
    follow, and only where they add a number the `tel:` links do not already
    hold.
    """
    numbers = []
    for anchor in node.select('a[href^="tel:"]'):
        run = (anchor.get("href") or "")[4:].strip()
        if run and run not in numbers:
            numbers.append(run)
    for anchor in node.find_all("a", href=True):
        number = _phone_from_click_to_chat(anchor.get("href"))
        if not number:
            continue
        if any(_looks_like_the_same_number(number, seen) for seen in numbers):
            continue
        numbers.append(number)
    return numbers[:limit]


# The parts of a page that speak for the site rather than for its subject.
#
# The element names and the ARIA roles first, because they say so outright.
# Then the class and id names a template uses when it does not: a great many
# sites still write `<div class="footer">`, and reading only `<footer>` there
# means a site with an ordinary footer is treated as a site with none - so its
# own contact details fall to the weaker page-wide reading and the report
# hedges about a detail that is plainly the site's. Matched as a whole class
# word, so `footer-note` counts and `no-footer-margin` does not.
CHROME_SELECTOR = (
    "header, footer, address, [role=banner], [role=contentinfo], "
    "[class~=footer], [class~=site-footer], [class~=page-footer], [id=footer], "
    "[class~=site-header], [class~=page-header], [id=header]"
)


def _contact_in_the_chrome(soup):
    """Emails and telephone numbers in the parts of a page that speak for the site."""
    emails, phones = set(), []
    for node in soup.select(CHROME_SELECTOR):
        text = text_without_urls(_text_or_empty(node))
        emails.update(EMAIL_RE.findall(text))
        # A `tel:` or click-to-chat link in the footer is the site's own number
        # by construction, in the same way a `tel:` link there is: the template
        # puts it on every page.
        for run in _declared_phones(node):
            if run not in phones:
                phones.append(run)
        for run in _dialable_runs(text):
            if run not in phones:
                phones.append(run)
    return sorted(emails)[:5], phones[:5]


# Words a page writes in front of its own postal address.
#
# The same positive-label discipline as `_PHONE_LABEL_RE`: where a page names
# one of its runs as the address, that run is the address. It is what keeps a
# museum's contact line - "Address: <Brand>, Thornbury Road, <Town> KT7 4LP" -
# readable on a page that carries no `<address>` element and no footer, which
# is the shape whose loss produced a report whose single high-severity finding
# was that the site never states its location, over two pages carrying it.
#
# Unanchored, unlike the telephone label, because an address label is not
# adjacent to what it labels. A telephone number follows its label directly; a
# postal address has the building's name between the two - "Address:
# <Brand>, Thornbury Road" - so a rule requiring the label to end the window
# in front of the match reads "<Brand>," and finds nothing. The window that
# replaces it is the part of the address's own sentence before it, which is
# the run of words the label is written into.
_ADDRESS_LABEL_RE = re.compile(
    r"\b(?:address|registered office|corporate office|head office|"
    r"registered address|postal address"
    r"|adresse|direcci\u00f3n|endere\u00e7o|indirizzo|adresa|adres|osoite"
    r"|\u0430\u0434\u0440\u0435\u0441)\b"
    # No word boundary is available in a script that does not space its words.
    r"|\u4f4f\u6240|\u6240\u5728\u5730|\u5730\u5740|\uc8fc\uc18c", re.I)

# Page types whose subject is the site itself. On one of these the body text
# is the site describing where it is, which is the whole job of the page, so
# an address in it needs no further attribution. Everywhere else the body is
# about what the page is about, and an address in it may belong to anyone the
# page names.
PAGES_ABOUT_THE_SITE = ("home", "about", "contact", "location")


def _sentence_holding(text, phrase):
    """The sentence of `text` that contains `phrase`, or an empty string."""
    for sentence in sentences(text):
        if phrase in sentence:
            return sentence
    return ""


def _street_the_site_states_as_its_own(text, soup, page_type=""):
    """The postal street this page states as the site's own, and where from.

    A street-shaped run is not automatically the audited site's street. A
    footwear retailer's product pages carry the legal disclosure every such
    product must carry - who manufactured the item, who markets it, and the
    premises of each - and the manufacturer's factory was read off nine of
    them as one of the brand's own places. The report then said the site
    "describes a place to visit without saying so in markup", counted the
    factories as separate branches because their codes differ, and handed over
    a paste-ready per-branch `LocalBusiness` block: following it would publish
    a supplier's factory as a shop. The same runs were compared against the
    head office in the contradiction check, which reported thirteen conflicts
    of which one was real, and on an open-source project a consultancy listed
    on the support-vendors page was recorded as the project's address, which
    marked the location fact present and hid the true finding that the project
    publishes no address at all.

    Emails and telephone numbers already had this rule - see `chrome_emails`
    and `chrome_phones`, added when a partners page's contact details were
    recorded as the audited site's - and the street never did. It is the same
    question and it gets the same answer: a site states its own address where
    a site states things about itself.

    Five such places, tried in this order, and which one it was is returned
    beside the match so a consumer can weigh it the way `postcode_source` is:

      an address region    an `<address>` element, `PostalAddress` markup or a
                           region named `address` in its class or id. The page
                           has declared this run to be an address, so nothing
                           else has to be inferred.

      a page about itself  a home, about, contact or location page states
                           where the site is as its subject matter.

      an address label     the page names the run as the address, in the same
                           way `_PHONE_LABEL_RE` names a run as a telephone
                           number.

      first person         the sentence carrying it speaks for the site.
                           `speaks_about_itself` is asked with no brand name,
                           because the extractor does not know one, so this
                           means "we", "our", "us" or "the <organisation>".

      the header or footer the furniture the template puts on every page - the
                           same regions `chrome_emails` and `chrome_phones`
                           are read from.

    The furniture is tried LAST, not first, and that order is load-bearing.
    `CHROME_SELECTOR` ends in the footer, and a site-wide footer carries the
    head office on every page: read first, it would hand four campus pages one
    head-office street, collapse them to a single place and put the whole
    multi-location branch of that report back in the dark - which is the
    failure `postcode_source` exists to record. The page's own body comes
    first, so a branch page keeps its branch, and the head office is what a
    page falls back to when its body states no address of its own.

    The matching node comes back too. The street and the postal code beside it
    are two halves of one printed line, and `_postcode_beside` slices from the
    street's offset - which is an offset into whichever string the street was
    read from. Handing back only the match would have that slice cut into the
    whole page text at a footer offset and quote a code from a different line.

    A phrase list of disclosure wordings - "manufactured by", "marketed by" -
    was the other way to write this and is deliberately not used. This
    codebase has twice completed such a list and been wrong the next time:
    there are as many ways to introduce somebody else's premises as there are
    languages and trades, and none of them has to be recognised if the rule is
    what makes an address the site's own rather than what makes it a
    stranger's.
    """
    for selector in ADDRESS_MARKUP_SELECTORS:
        for node in soup.select(selector):
            match = _street_hint(text_without_urls(_text_or_empty(node)))
            if match:
                return match, "in an address region", node
    for match in _street_hints(text):
        if page_type in PAGES_ABOUT_THE_SITE:
            return match, "in the text of a page about the site", None
        sentence = _sentence_holding(text, match.group(0))
        if _ADDRESS_LABEL_RE.search(sentence.split(match.group(0))[0]):
            return match, "behind an address label in the page text", None
        if speaks_about_itself(sentence):
            return match, "in a sentence that speaks for the site", None
    for node in soup.select(CHROME_SELECTOR):
        match = _street_hint(text_without_urls(_text_or_empty(node)))
        if match:
            return match, "in the site header or footer", node
    return None, "", None


def _contact_facts(text, soup, page_type=""):
    """Plain-text facts an assistant would try to quote for "where/how" questions."""
    text = text_without_urls(text)
    emails = sorted(set(EMAIL_RE.findall(text)))[:5]
    tel_links = _declared_phones(soup)
    phones = tel_links or _dialable_runs(text)
    street, street_source, street_node = _street_the_site_states_as_its_own(
        text, soup, page_type)
    street_text = (text_without_urls(_text_or_empty(street_node))
                   if street_node is not None else text)
    postcode, postcode_source = _postcode_hint(text, soup, street, street_text)
    chrome_emails, chrome_phones = _contact_in_the_chrome(soup)
    return {
        "emails": emails,
        "phones": phones,
        # The same details, restricted to the header, the footer and any
        # `<address>` element - the parts of a page that speak for the site
        # rather than for what the page is about.
        #
        # A partners page lists partner organisations and their contact
        # details, and reading the whole page recorded one of those as the
        # audited site's own way of getting in touch. That is the shape that
        # does real damage: a contact detail recorded as present marks the
        # check passed, so a wrong one does not add a false line, it hides a
        # true one. A detail in the chrome is the site's by construction,
        # because the template puts it on every page.
        "chrome_emails": chrome_emails,
        "chrome_phones": chrome_phones,
        # Numbers the site put in a link - a `tel:` href, or a click-to-chat
        # link whose URL slot is defined to hold a number in international
        # format - so declared rather than inferred. Comparing these between
        # pages is safe; comparing the regex-scraped ones above is not.
        "declared_phones": tel_links,
        "has_email": bool(emails or soup.select('a[href^="mailto:"]')),
        "has_phone": bool(phones),
        "street_hint": truncate(street.group(0), 120) if street else "",
        # Which of the ways a page states its own address produced that
        # street. Recorded for the same reason `postcode_source` is: a
        # consumer deciding "is this page's address its own, or the one every
        # page carries" cannot tell from the string. A street from an address
        # region or from the page's own body identifies this page; one from
        # the site header or footer identifies the site and is the same on
        # every page, which is what tells a chain of branches apart from a
        # single business whose footer repeats.
        "street_source": street_source if street else "",
        "postcode_hint": postcode.group(0) if postcode else "",
        # Where that code was read from. A consumer asking "is this page's
        # address its own" needs to know whether the code came from beside the
        # street on this page or out of the furniture every page carries.
        "postcode_source": postcode_source if postcode else "",
        "has_address": bool(street and postcode) or bool(soup.select('address, [itemtype*="PostalAddress"]')),
        "has_contact_form": any(
            not f["is_search"] and not f["is_newsletter"] and f["field_count"] >= 2
            for f in _forms(soup)
        ),
    }


# A share button points at a social platform but is not a profile on it.
# Counting `facebook.com/sharer/sharer.php?u=...` as the brand's Facebook page
# inflated corroboration on every site with share widgets.
_SHARE_URL_RE = re.compile(
    r"/(?:sharer|share|shareArticle|intent|share_url|submit)\b"
    r"|share\.php|/intent/(?:tweet|post)|[?&](?:u|url|text|via|mini)=", re.I)

# What a profile URL looks like, on any host at all.
#
# Per-platform regexes lived here, one per recognised platform, and they failed
# the way per-platform rules fail: each was written for the commonest form of
# one platform's URL and rejected the next commonest. A volunteer non-profit
# links a brand-named group on a social platform and a brand-named channel on a
# video platform, both from its home page, both recorded in this crawl's own
# snapshot. The social rule allowed a single path segment, so a group address -
# a role word and then the name - could not match it. The video rule was
# anchored at the end of the handle, so a channel address with a view segment
# after it could not match either. The report said "Only 2 off-site profiles
# are linked", naming the two it could see; the real number was at least four,
# and the finding was ranked third in what the reader should do first.
#
# There is one reading underneath all of those regexes, and it needs no host to
# be named. A profile address has up to three parts:
#
#   a role word    the platform's own word for "an account follows" -
#                  `/company/`, `/channel/`, `/groups/`, `/shop/`. Whoever
#                  operates the platform chose it and anybody can read it, the
#                  same way the machine-role host labels in `audit_common` are
#                  readable. A class, never a list of vendors.
#   a handle       the account's name, or the number standing in for it.
#   a view         which tab of that account is linked - `/about`, `/videos`.
#                  Still the same account.
#
# Anything longer is content inside an account rather than the account itself:
# a post, a repository, a product, an article in somebody's archive. That is
# the reading the old table was reaching for when it anchored every regex, and
# writing it once is what stops the next platform needing its own line.
#
# What shape cannot say is *whose* account it is. This stage cannot ask - the
# brand name is decided after the crawl finishes - so it records candidates and
# `freshness-corroboration-audit` attributes them, which is the division of
# labour `_social_profiles` already documents.

# The segment before a handle, when the platform's own vocabulary says an
# account follows. Reading these instead of a host list is what lets a
# storefront on a marketplace nobody here has heard of count as a storefront.
_ACCOUNT_ROLE_SEGMENTS = frozenset({
    # An organisation, on the platforms that say so in the path.
    "company", "companies", "school", "showcase", "organization", "organisation",
    "org", "orgs", "institution", "agency", "team", "teams", "club",
    # A seller's storefront on a marketplace. This is the shape a footwear
    # maker's three storefronts had, and no rule in the old table could match
    # any of them because none of the three hosts was in the table.
    "shop", "shops", "store", "stores", "storefront", "seller", "sellers",
    "merchant", "vendor", "brand", "brands", "maker", "makers", "gold",
    # An account, however the platform spells it.
    "profile", "profiles", "channel", "channels", "user", "users", "u", "c",
    "account", "accounts", "member", "members", "creator", "creators", "artist",
    "page", "pages", "group", "groups", "community", "communities", "r", "id",
    # A directory's or an encyclopedia's page about an organisation. The URL is
    # the platform's entry for a named thing, which is what an account is on a
    # site nobody can sign up to.
    "wiki", "entity", "biz", "business", "review", "reviews", "overview",
    "maps", "place", "places", "listing", "directory",
    # An address written as an invitation to follow the account it names.
    "join", "invite", "add",
    # A registry's or an archive's own word for "the entry for a named thing".
    # A published package and a deposited dataset are the same kind of evidence
    # a directory listing is: a third party holding a record of this project
    # under its name. A documentation tree's report missed its package registry
    # entry and its archive deposit while the tool's own proactive
    # recommendation named "a package or registry entry" as a profile to go and
    # claim - advice to acquire what the site already had.
    "project", "package", "packages", "crate", "crates", "gem", "gems", "pkg",
    "module", "modules", "record", "records", "dataset", "datasets", "doi",
})

# A DOI: `10.<registrant>/<suffix>`. The registrant number is not a word, so no
# vocabulary of role segments can recognise one, and the suffix routinely
# carries further slashes - both of which put a deposit outside the shape test
# above. It is the same kind of address as a Wikidata Q-number: a permanent
# identifier a third party issued for a named thing, which `profile_is_opaque`
# already treats as an account that cannot be checked either way.
_DOI_PATH_RE = re.compile(r"^10\.\d{4,9}/\S+$")

# Role words that name an individual rather than the organisation. Named by
# name rather than merely left out of the set above, so that adding one to that
# set cannot quietly undo the reading: a customer story linking a named
# employee's page put that person's account into a company's own corroboration
# count.
#
# Reported as its own kind rather than thrown away, which is the fix. Discarding
# it here decided attribution in the one place that cannot ask - this stage does
# not know whose site this is - and it decided it wrongly for every site that is
# one person. A coaching site's whole off-site presence is a "Connect" list of
# two links, Instagram and LinkedIn side by side in one `<ul>`; the crawl stored
# both, `counts_as_off_site_profile` accepts both, and the report said the site
# links one profile because the second was addressed `/in/<a person>`. The
# decision now belongs to the caller, which can see whether the page publishes
# the account as its own - and a stranger's account inside a customer story
# still has nothing publishing it.
_PERSONAL_ACCOUNT_ROLES = frozenset({
    "in", "author", "authors", "people", "person", "staff", "employee",
    "contributor", "contributors", "byline",
})

# The trailing segment that says which tab of an account is linked. Dropping it
# before the shape test is the whole of the fix for a channel address that
# named its account and then said which of its views to open.
_ACCOUNT_VIEW_SEGMENTS = frozenset({
    "about", "home", "info", "overview", "timeline", "posts", "photos", "videos",
    "video", "featured", "streams", "playlists", "shorts", "community", "live",
    "reviews", "events", "jobs", "people", "menu", "shop", "store", "products",
    "items", "media", "highlights", "followers", "following", "likes",
    "repositories", "packages", "projects", "stars", "releases", "channels",
    "feed", "activity", "history", "gallery", "collections", "lists",
})

# A segment that is one of the platform's own pages rather than somebody's
# handle. The root namespace of a platform holds both, and telling them apart
# is what stops a video address being read as a channel: `/watch` sits in
# exactly the place a handle sits.
_NOT_A_HANDLE = frozenset({
    "watch", "results", "search", "playlist", "playlists", "feed", "feeds",
    "shorts", "embed", "live", "hashtag", "about", "t", "redirect", "account",
    "accounts", "premium", "gaming", "music", "movies", "channel", "channels",
    "user", "users", "c", "u", "r", "orgs", "sponsors", "topics", "explore",
    "discover", "trending", "popular", "browse", "start", "new", "home",
    "index", "login", "signin", "signup", "register", "logout", "help",
    "support", "faq", "contact", "privacy", "terms", "legal", "cookies",
    "security", "settings", "preferences", "download", "downloads", "docs",
    "doc", "documentation", "api", "developer", "developers", "blog", "news",
    "press", "media", "events", "event", "jobs", "careers", "pricing", "plans",
    "products", "product", "shop", "store", "cart", "checkout", "orders",
    "category", "categories", "collection", "collections", "tag", "tags",
    "topic", "post", "posts", "article", "articles", "page", "pages", "p",
    "pin", "photo", "photos", "video", "videos", "story", "stories", "status",
    "statuses", "sitemap", "share", "sharer", "intent", "business", "biz",
    "company", "companies", "wiki", "profile", "group", "groups",
})

# A handle is whatever its owner was allowed to type. Anchoring on letters and
# digits would drop the punctuation real handles carry - a dot, an underscore,
# the comma and the brackets an encyclopedia title has - so the reading is
# negative: one segment, nothing in it that could be a path, and at least one
# letter or digit, so a string of punctuation is not read as a name.
_HANDLE_RE = re.compile(r"^@?[^\s/]{1,120}$")
_HAS_A_LETTER_OR_DIGIT = re.compile(r"[^\W_]", re.UNICODE)


def _account_shape(bare):
    """("role" | "handle" | "personal" | "", handle) - how this path names an account.

    "role" is the path saying so itself, in a role word or a leading `@`, and
    it needs no host to be recognised. "handle" is a bare name at the root of
    the platform, which is an account only where the platform hands its root
    namespace out to accounts - true of the hosts `SOCIAL_PLATFORMS` names, and
    not true of an arbitrary site, where the first segment is a section of that
    site rather than somebody's name. "personal" is an account the path says
    belongs to an individual; it is still an account, and whether it is this
    site's is a question the caller can answer and this function cannot - see
    `_PERSONAL_ACCOUNT_ROLES`.

    The query string and the fragment are gone before this runs, so a tracking
    parameter cannot change the shape a path has.
    """
    without_scheme = bare.split("://")[-1]
    path = without_scheme.split("/", 1)[1] if "/" in without_scheme else ""
    segments = [segment for segment in without_scheme.split("/")[1:] if segment]
    if not segments:
        return "", ""
    # Before the depth test, because a DOI suffix carries slashes of its own.
    if _DOI_PATH_RE.match(path.strip("/")):
        return "role", path.strip("/")
    if len(segments) > 1 and segments[-1].lower() in _ACCOUNT_VIEW_SEGMENTS:
        segments = segments[:-1]
    # A role word and a handle, or a handle alone. Deeper than that is content
    # somebody posted inside an account, which is what a mention of another
    # project or an article citation looks like.
    if len(segments) > 2:
        return "", ""
    role = segments[0].lower() if len(segments) == 2 else ""
    handle = segments[-1]
    if role in _PERSONAL_ACCOUNT_ROLES:
        if not _HANDLE_RE.match(handle) or not _HAS_A_LETTER_OR_DIGIT.search(handle):
            return "", ""
        return "personal", handle
    if role and role not in _ACCOUNT_ROLE_SEGMENTS:
        return "", ""
    if not _HANDLE_RE.match(handle) or not _HAS_A_LETTER_OR_DIGIT.search(handle):
        return "", ""
    if handle.lstrip("@").lower() in _NOT_A_HANDLE:
        return "", ""
    if role or handle.startswith("@"):
        return "role", handle
    # A bare number at the root of a site is an article, an order or an item.
    # Behind a role word it is an account identifier - which is the only reason
    # this test lives here rather than above.
    if handle.isdigit():
        return "", ""
    return "handle", handle


def _platform_name(bare):
    """What a report may call the platform this address is on.

    Empty for a host this audit has no name for; that host is then keyed by its
    own name and counts exactly the same. The name is for printing, and for the
    readers downstream that key off these exact strings.
    """
    without_scheme = bare.split("://")[-1]
    host_part = without_scheme.split("/")[0]
    host = strip_www(host_part)
    path = without_scheme[len(host_part):].lstrip("/")
    for domain, platform in SOCIAL_PLATFORMS.items():
        # An entry may name a section of a platform rather than a whole host -
        # a mapping service's place links live under one path prefix and
        # nothing else on that host is a profile.
        domain_host, _, prefix = domain.partition("/")
        if not (host == domain_host or host.endswith("." + domain_host)):
            continue
        if prefix and not path.startswith(prefix):
            continue
        return platform
    return ""


# `rel="me"` is how a site declares an account as its own, and it is the
# mechanism Mastodon uses to verify a website link. A cloud host publishes a
# `<link rel="me">` to its Mastodon account in its head, and the report said it
# links two off-site profiles: the account was in neither the body, the footer,
# nor `sameAs`, so no source the finding named could see it. It is also the
# only way to find a Mastodon account at all, because Mastodon has no fixed
# domain and therefore cannot be on a platform list.
_REL_ME_SELECTOR = '[rel~="me"][href]'


def _rel_me_profiles(soup, base, origin):
    """Accounts the page declares as its own with `rel="me"`."""
    out = []
    for element in soup.select(_REL_ME_SELECTOR):
        url = normalise_url(element.get("href") or "", base)
        if not url or same_site(url, origin or base):
            continue
        out.append(url)
    return out


def _same_as_urls(jsonld):
    """Every `sameAs` value in this page's JSON-LD, as strings."""
    out = []
    for node in jsonld or ():
        same_as = node.get("sameAs") if hasattr(node, "get") else None
        if isinstance(same_as, str):
            out.append(same_as)
        elif isinstance(same_as, list):
            out.extend(str(value) for value in same_as)
    return [url for url in out if isinstance(url, str) and url.strip()]


# How many different hosts a container's links have to reach before that
# container is the page publishing its own accounts rather than a sentence with
# a couple of links in it.
PROFILE_ROW_MIN_HOSTS = 2

# Above this a container is a directory of other people's links - a blogroll, a
# partner list, a set of citations - rather than a site's own row of accounts.
PROFILE_ROW_MAX_LINKS = 20

# The containers a row of accounts is written in. Every one of them, rather
# than `<footer>` alone, because the block is as often a `<ul class="sns">` in
# the middle of a one-page site as it is a strip of icons at the bottom.
_PROFILE_ROW_CONTAINERS = ("ul", "ol", "nav", "footer", "header", "div",
                           "section", "aside", "p", "span", "li")


def _profiles_the_page_publishes(soup, base, origin):
    """The off-site accounts this page presents as its own, by where they sit.

    A container whose every link is an off-site account, on two or more
    different hosts, is the page saying "these are my accounts". It is the same
    assertion `rel="me"` makes, written in ordinary HTML, and it is the only
    evidence of ownership available before the crawl finishes and the brand
    name is decided.

    Two measured failures need it. A one-page coaching site's whole off-site
    presence is a "Connect" list of two links, Instagram and LinkedIn side by
    side in one `<ul>`; the second is addressed `/in/<a person>`, which the
    path-shape reader calls an individual's account, and the report said the
    site links one profile. And a city government's five official accounts,
    each linked from one page and none of them spelling a name any handle test
    can compare against, were all dropped downstream as somebody else's
    citations - the report then said "nothing off the site corroborates it"
    about a body whose own snapshot listed all five.

    A sentence with two links in it does not qualify: the container has to hold
    nothing but accounts. A customer story linking a named employee's page has
    prose and other links around it, which is exactly the case that must not
    get through.
    """
    published = set()
    if soup is None:
        return published

    def account_url(anchor):
        href = (anchor.get("href") or "").strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            return ""
        url = normalise_url(href, base)
        if not url or same_site(url, origin or base):
            return ""
        if not counts_as_off_site_profile(url):
            return ""
        bare = url.lower().split("?")[0].split("#")[0]
        if _SHARE_URL_RE.search(url.lower()) or not _account_shape(bare)[0]:
            return ""
        return url

    for node in soup.find_all(_PROFILE_ROW_CONTAINERS):
        anchors = node.find_all("a", href=True)
        if not PROFILE_ROW_MIN_HOSTS <= len(anchors) <= PROFILE_ROW_MAX_LINKS:
            continue
        urls, hosts = [], set()
        for anchor in anchors:
            url = account_url(anchor)
            if not url:
                urls = None
                break
            urls.append(url)
            hosts.add(strip_www(url.lower().split("://")[-1].split("/")[0]))
        if urls and len(hosts) >= PROFILE_ROW_MIN_HOSTS:
            published.update(urls)
    return published


# An account identified by a number rather than by a name: an archive's
# accession number, a repository deposit, an encyclopedia's item id.
_IDENTIFIED_BY_NUMBER_RE = re.compile(r"^[\W\d_]+$", re.UNICODE)


def _identifies_by_number(bare, path_segments):
    """Is this account named by an identifier no name test can be run against?

    A test that cannot pass must not be the thing that rejects. The attribution
    rule below asks whether the handle spells a name the site publishes about
    itself, and a handle that is `1234567` spells nothing however plainly the
    record belongs to this site - so applying the rule there would drop a
    documentation tree's archive deposit and its permanent identifier, which is
    the exact miss `_ACCOUNT_ROLE_SEGMENTS` was widened to close: the report
    recommended the owner go and get a registry entry the site already had.

    `profile_is_opaque` says the same thing about the two platforms whose
    account ids look like words. This adds the general shape and the permanent
    identifier, which no host list can be complete about.
    """
    if not path_segments:
        return False
    if _DOI_PATH_RE.match("/".join(path_segments)):
        return True
    if profile_is_opaque(bare):
        return True
    return bool(_IDENTIFIED_BY_NUMBER_RE.match(path_segments[-1]))


# The words a site puts beside its own name in a handle when the plain name was
# taken, or when it keeps one account per market or per product: `<name>hq`,
# `<name>_official`, `get<name>`, `<name>app`. A handle that is the name and
# one of these is still the name.
#
# Anything else beside the name is somebody else's words. A documentation
# site's paste-ready `sameAs` listed another developer's package,
# `/package/<name>-wasm-http`, and a third-party blog's article,
# `/an-unscientific-benchmark-of-<name>-vs-the-file-system-btrfs/`, as the
# site's own accounts, because the name was one word inside each and the test
# asked only whether it was there. A name inside a longer slug is a mention.
_NAME_AFFIXES = frozenset({
    "hq", "official", "app", "apps", "inc", "global", "intl", "team", "online",
    "org", "labs", "dev", "project", "foundation", "community", "group",
    "corp", "ltd", "llc", "gmbh", "news", "support", "help", "status",
    "the", "get", "use", "try", "go", "join", "we",
})
# A country or language code beside the name, which is how one site names its
# accounts per market or per edition: `<name>_jp`, `<name>uk`, `<name>.en`.
_NAME_REGION_AFFIXES = frozenset(
    "us uk gb au ca nz ie in jp kr cn tw hk sg my id th vn ph de fr es it pt br "
    "mx ar cl co pe nl be ch at se no dk fi pl cz ru ua tr il ae sa eg za ng ke "
    "pk bd en ja ko zh he fa hi eng kor jpn chn esp fra deu ita por rus ara "
    "tha".split())


def _handle_of(bare):
    """The path segment that says whose account an address is.

    The first segment on a repository host, where everything after the owner
    is somebody's project; elsewhere the handle the shape reader finds, or the
    last segment where it finds none.
    """
    without_scheme = bare.split("://")[-1]
    host = strip_www(without_scheme.split("/")[0])
    segments = [s for s in without_scheme.split("/")[1:] if s]
    if not segments:
        return ""
    if any(host == repo or host.endswith("." + repo) for repo in _REPOSITORY_HOSTS):
        return segments[0]
    return _account_shape(bare)[1] or segments[-1]


def _handle_is_the_name(url, site_names):
    """Is this address's handle one of the site's own names, and no more?

    Equal once case and separators are gone, or the name with one affix from
    the two sets above - never the name as one word among several.
    """
    bare = (url or "").lower().split("?")[0].split("#")[0].rstrip("/")
    handle = re.sub(r"[^a-z0-9]", "", _handle_of(bare))
    if not handle:
        return False
    for name in site_names or ():
        key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        if len(key) < 3:
            continue
        if handle == key:
            return True
        if handle.startswith(key):
            affix = handle[len(key):]
        elif handle.endswith(key):
            affix = handle[:-len(key)]
        else:
            continue
        if affix in _NAME_AFFIXES or affix in _NAME_REGION_AFFIXES:
            return True
    return False


# The first path segment of a page that signs somebody up rather than showing
# an account. On a platform that gives each brand a subdomain, the host carries
# the brand's name and the path carries the programme -
# `<brand>.<an influencer platform>/join/<campaign>` - and that address was
# counted among a shop's own profiles. It is the platform's application form
# for the brand's influencer programme, which nobody keeps an account on.
_PROGRAMME_PATH_SEGMENTS = frozenset({
    "join", "apply", "application", "applications", "signup", "sign-up",
    "register", "registration", "enroll", "enrol", "onboarding", "referral",
    "refer", "invite", "login", "signin", "sign-in",
})


def _is_a_programme_page(bare):
    """Does this address open with a sign-up or application step?"""
    segments = [s for s in bare.split("://")[-1].split("/")[1:] if s]
    return bool(segments) and segments[0] in _PROGRAMME_PATH_SEGMENTS


def _names_the_site(url, site_names):
    """Is this address one of the names the site publishes about itself?

    Either the handle or the hostname. The host carries the name when a
    platform gives each customer a subdomain - `<the site's name>.<a hosted
    documentation host>` - and there the handle is `/en/stable/`, which spells
    nothing. Reading only the handle there dropped an address whose whole
    left-hand label is the site's own name.

    The handle has to be the name, not contain it - see `_NAME_AFFIXES`. And a
    brand's subdomain opening with a sign-up step is a programme page, not an
    account - see `_PROGRAMME_PATH_SEGMENTS`.
    """
    if _handle_is_the_name(url, site_names):
        return True
    bare = (url or "").lower().split("?")[0].split("#")[0]
    try:
        host = strip_www((urlparse(url or "").hostname or "").lower())
    except ValueError:
        return False
    if _is_a_programme_page(bare):
        return False
    return bool(_host_is_the_account(host, site_names))


def _own_hosts(base):
    """The hostnames that are this site rather than somewhere off it.

    `gis.<the audited domain>` was once published as a government site's
    own off-site profile: a subdomain of the site under audit, counted as
    third-party corroboration of it. `same_site` compares whole hostnames, so
    every subdomain of the audited host arrives in `links["external"]` and
    nothing between there and the profile list asked again.

    The audited host and the domain immediately above it, and no further. One
    label is peeled, never a chain: peeling two off `city.fukuoka.lg.jp` would
    leave `lg.jp` and silence the accounts of every local government in the
    country. Under-peeling costs one wrong entry on a site with a deep host;
    over-peeling drops real profiles by the thousand.
    """
    host = strip_www((urlparse(base or "").hostname or "").lower())
    if not host:
        return ()
    above = next(iter(_domains_above(host)), "")
    return tuple(h for h in (host, above) if h)


def _is_the_audited_site(url, own_hosts):
    """Is this address the site being audited, at any hostname it has?"""
    if not own_hosts:
        return False
    try:
        host = strip_www((urlparse(url or "").hostname or "").lower())
    except ValueError:
        return False
    if not host:
        return False
    return any(host == own or host.endswith("." + own) for own in own_hosts)


# A family of handles named after the site: how long the site's own domain
# word has to be before a shared opening means anything, and how many different
# accounts on one page have to share it. See the family rule in
# `_off_site_profiles`.
PROFILE_FAMILY_MIN_NAME = 3
PROFILE_FAMILY_MIN_ACCOUNTS = 2


def _account_handle_key(url):
    """The last path segment of an account address, letters and digits only."""
    bare = (url or "").lower().split("?")[0].split("#")[0].rstrip("/")
    segments = [s for s in bare.split("://")[-1].split("/")[1:] if s]
    return re.sub(r"[^a-z0-9]", "", segments[-1]) if segments else ""


def _off_site_profiles(external_links, jsonld, soup=None, base="",
                       subresource_hosts=()):
    """Every off-site account this page points at, once, with its evidence.

    This is the one list. `social_profiles` and `declared_profiles` are views
    of it and are built from it, so one page cannot hold two answers to one
    question - which is the defect this replaced. A municipal site's snapshot
    held seven profiles, its report's paste-ready `sameAs` block listed all
    seven, and the finding two lines above that block named two and told the
    town to go and find more. The two fields were built by two functions that
    read overlapping candidates through different tests: a `sameAs` entry on a
    host with no readable path was in `declared_profiles` and absent from
    `social_profiles`, so the count that says "linked from the crawled pages or
    listed in `sameAs`" could not see half of what the site had listed there.

    Each entry carries what this stage can establish and no more:

      platform   what a report may call it, or the bare host where this audit
                 has no name for it
      url        the account address
      declared   the site claims it, in `sameAs` or in `rel="me"`
      published  the page presents it as its own, in a row of account links -
                 see `_profiles_the_page_publishes`
      personal   the platform's own path says an individual rather than an
                 organisation
      names_the_site  the handle spells a name this site publishes about
                 itself, from its own address - see `host_name_forms`

    The first, second and fourth of those are the three ways an account can be
    attributed to this site before the brand name exists, and one of them is
    now required before a platform name is printed. Naming an unattributed
    link has a real cost: a city's report named a noodle shop's social
    account and a shop directory as that city's profiles, and a documentation
    site's named a package-registry entry for a different project. All three
    are ordinary links out of a page, all three have the shape of an account,
    and none is anybody's evidence about the site that links them. Shape is
    what says an address names an account; it cannot say whose. The mention
    stays in this list
    with `attributed` false, so a consumer can still see it; it is the views
    below - the ones a report counts and prints - that require attribution.

    Whose account it is still cannot be decided here, because the brand name is
    not known until the crawl finishes; `freshness-corroboration-audit`
    attributes them and now has the evidence to do it with.
    """
    # The romanised labels of the site's own address. They are not the brand
    # name - that is decided after the crawl - but they are a name this stage
    # has, and `_profiles_among` reads them only to keep a bare handle it would
    # otherwise drop. `host_name_forms` is the same reader that made a Japanese
    # city's accounts attributable when its name is not in Latin script.
    site_names = host_name_forms(urlparse(base or "").hostname or "")
    # A host under the audited registrable domain is this site, whatever its
    # path looks like. See `_own_hosts`.
    own_hosts = _own_hosts(base)
    rel_me = _rel_me_profiles(soup, base, base) if soup is not None else []
    declared_urls = [url for url in _same_as_urls(jsonld) + list(rel_me)
                     if not _is_the_audited_site(url, own_hosts)]
    # A `rel="me"` is a declaration and a publication at once: the page is
    # asserting the account is its own in the markup itself.
    published = _profiles_the_page_publishes(soup, base, base) | set(rel_me)

    found, order = {}, []

    def keep(platform, url, declared):
        entry = found.get(url)
        if entry is None:
            bare = url.lower().split("?")[0].split("#")[0]
            segments = [s for s in bare.split("://")[-1].split("/")[1:] if s]
            entry = {"platform": platform, "url": url, "declared": False,
                     "published": url in published,
                     "personal": _account_shape(bare)[0] == "personal",
                     # The address spells a name this site publishes about
                     # itself. The one attribution that works on a page which
                     # neither declares its accounts nor groups them.
                     "names_the_site": _names_the_site(url, site_names),
                     # The account is a number, so no name test can be run
                     # against it either way - see `_identifies_by_number`.
                     "identified_by_number": _identifies_by_number(bare, segments)}
            found[url] = entry
            order.append(entry)
        if declared:
            entry["declared"] = True

    linked = [link["url"] for link in external_links or ()
              if not _is_the_audited_site(link.get("url"), own_hosts)]
    for platform, url in _profiles_among(linked, site_names, published,
                                         subresource_hosts):
        keep(platform, url, False)
    for platform, url in _profiles_among(declared_urls, site_names, published,
                                         subresource_hosts, declared=True):
        keep(platform, url, True)

    # A declaration on a host whose path names no account is still the site
    # saying "this account is me". Filtering it through a platform list threw
    # away a museum's Spotify profile and its WhatsApp channel, and that museum
    # then passed the corroboration threshold by exactly one profile - a site
    # with the same real footprint and one fewer listed host would have been
    # told to go and claim accounts it already has.
    #
    # A declaration is evidence of identity; it is not evidence that the
    # address is somewhere anybody keeps an account. This fallback takes any
    # host at all, so it is the door a storefront platform, a payment processor
    # or a delivery firm walks through - and one consumer brand's count read 8
    # where the truth was 3 because hosts of exactly that kind were let in.
    # `counts_as_off_site_profile` keeps the declaration's power (a customer
    # subdomain has no path to read, and still counts) while refusing an
    # address the site only ever loads code from and an address that is a
    # machine's job rather than an organisation.
    for url in declared_urls:
        if url in found:
            continue
        if not counts_as_off_site_profile(url, declared=True,
                                          subresource_hosts=subresource_hosts):
            continue
        host = strip_www(url.split("://")[-1].split("/")[0].lower())
        if host and "." in host:
            keep(host, url, True)
    # A family of accounts named after the site. A national library whose name
    # is written in Japanese links twelve accounts from one of its own pages -
    # `NDLJP`, `NDLJP_en`, `NDLKODOMO`, `NDLexhibition`, `ndlimagebank` - and
    # each one alone fails the short-name rule, because a three-letter domain
    # word inside a longer handle could be a stranger's. Two or more different
    # handles on one page all opening with the site's own domain word are not
    # a coincidence, and no name the site asserts can be spelled in a Latin
    # handle to settle it another way. A noodle shop's account on a city page
    # does not open with the city's domain word, so it stays unattributed.
    #
    # On a platform this audit names, and nowhere else. Only there is the last
    # segment of an address a handle at all; on any other host it is a
    # package, a mailing-list archive or an article slug, and three of those
    # opening with a documentation site's name are not a family of accounts.
    recognised = frozenset(SOCIAL_PLATFORMS.values())
    for name in site_names:
        key = re.sub(r"[^a-z0-9]", "", (name or "").lower())
        if len(key) < PROFILE_FAMILY_MIN_NAME:
            continue
        family = [entry for entry in order
                  if entry["platform"] in recognised
                  and _account_handle_key(entry["url"]).startswith(key)]
        if len({_account_handle_key(e["url"]) for e in family}) >= PROFILE_FAMILY_MIN_ACCOUNTS:
            for entry in family:
                entry["names_the_site"] = True
    # One reading, applied where every entry is complete, so the three ways in
    # cannot drift apart. `declared` is set after an entry is created, which is
    # why this cannot live inside `keep`.
    for entry in order:
        entry["attributed"] = bool(entry["declared"] or entry["published"]
                                   or entry["names_the_site"]
                                   or entry["identified_by_number"])
    # Candidate order, which is document order and so is stable between two
    # runs of one snapshot. The views below take the first URL each platform
    # offers, and sorting here would silently change which account that is.
    return order


def _profiles_by_platform(profiles, declared_only=False):
    """{platform: url} - one attributed account per platform, from the one list.

    Attribution is required here and nowhere else. This is what a report counts
    and what it prints a platform name from, and an account nothing attributes
    to this site is a link out of a page: a ramen shop's Instagram on a city's
    tourism page, a dependency's registry entry in a project's documentation.
    Both were published as the audited site's own profiles, and this number
    leads the README and drives the top recommendation on most reports.

    `off_site_profiles` keeps the unattributed entries with `attributed` false,
    so nothing is hidden - only the claim that they are this site's is dropped.
    """
    found = {}
    for entry in profiles:
        if declared_only and not entry.get("declared"):
            continue
        if not entry.get("attributed"):
            continue
        found.setdefault(entry["platform"], entry["url"])
    return dict(sorted(found.items()))


def _declared_profiles(jsonld, subresource_hosts=()):
    """Profiles the site claims as its own, via `sameAs`.

    `sameAs` means "these accounts are me". That is the site asserting
    identity, and it needs no further attribution - which matters for the
    platforms whose profile URL is an opaque identifier and can never be
    checked against a name.

    A view of `_off_site_profiles`, not a second reading of the page.
    """
    return _profiles_by_platform(
        _off_site_profiles([], jsonld, None, "", subresource_hosts),
        declared_only=True)


def _social_profiles(external_links, jsonld, soup=None, base="", subresource_hosts=()):
    """Off-site profiles this page points at, one per platform.

    A view of `_off_site_profiles`, not a second reading of the page. Share
    widgets are excluded, because they would overstate how well corroborated a
    brand is.
    """
    return _profiles_by_platform(
        _off_site_profiles(external_links, jsonld, soup, base, subresource_hosts))


def _host_is_the_account(host, site_names):
    """The domain a subdomain names this site's account on, when it names one.

    `looks_like_an_account` refuses an address with nothing after the slash and
    says in its own docstring that "a platform that gives each customer a
    subdomain has no path to read ... so this returns False and the caller
    decides". Nothing decided, so every such address was dropped: a
    documentation tree's hosted API-docs address is its own name in front of a
    documentation host, and the report missed it while recommending the site go
    and get one.

    The name is the evidence, exactly as it is for a bare handle at the root of
    an unnamed host. Equality after dropping punctuation, not containment: a
    three-letter label inside a stranger's subdomain is not this site.
    """
    labels = [label for label in (host or "").split(".") if label]
    if len(labels) < 3 or labels[0] in _REGISTRY_LABELS:
        return ""
    leading = re.sub(r"[^a-z0-9]", "", labels[0].lower())
    if len(leading) < 3:
        return ""
    # A platform is somebody's domain, so at least one label between the name
    # and the top-level domain has to be a word rather than a registry's.
    # `<name>.or.<cc>` is an organisation's own website under a country
    # registry, not an account on anything: a museum's footer link to a
    # separate association at such an address was counted as its profile on
    # "platform or.<cc>", and the profile count rose by one.
    if all(label in _REGISTRY_LABELS for label in labels[1:-1]):
        return ""
    for name in site_names:
        if leading == re.sub(r"[^a-z0-9]", "", (name or "").lower()):
            return ".".join(labels[1:])
    return ""


def _profiles_among(candidates, site_names=(), published=(), subresource_hosts=(),
                    declared=False):
    """(platform, url) for every candidate whose path names an account.

    `site_names` are romanised forms of the audited site's own hostname, from
    `host_name_forms`. They are the only name this stage has - the brand name
    is decided after the crawl - and they are read in one direction only: a
    bare handle at the root of a host this audit cannot name gets through when
    the handle spells the site's own name. Nothing is ever dropped for failing
    that test, because a handle is a name somebody chose and a mismatch proves
    nothing about it. That is what makes a brand-named storefront on a
    marketplace this audit has never heard of count without anybody adding the
    marketplace to a list.

    `published` are the URLs the page presents as its own accounts and
    `declared` says the whole candidate list is the site's own claim. Between
    them they decide the one case shape cannot: an account whose path names an
    individual. A one-person business links its owner's account and means its
    own; a customer story links a named employee's and means somebody else's.
    """
    out = []
    repeated_owners = _repository_owners(candidates)
    for url in candidates:
        low = url.lower()
        if _SHARE_URL_RE.search(low):
            continue
        # The shape test must not be defeated by a tracking parameter or a
        # fragment, and neither carries any information about whose profile
        # this is.
        bare = low.split("?")[0].split("#")[0]
        host = strip_www(bare.split("://")[-1].split("/")[0])
        # The host, not the whole string. A share link carrying `?to=<a review
        # site>` contains that review site's name, and `https://myblog.page/`
        # contains a mapping service's short host; both were recorded as the
        # brand's own profiles while the substring test was in place.
        platform = _platform_name(bare)
        # A profile is an account: a handle inside a platform. Being on a
        # recognised platform's hostname is not enough, because the front door
        # of that platform sits on the same hostname - a "powered by" link, a
        # payment badge, a footer credit. Those are the operator's home page,
        # not this brand's profile on it, and they were being counted as one.
        # The account is sometimes the host rather than the path. See
        # `_host_is_the_account`; the subresource test still applies, so a host
        # the site only ever loads code from cannot arrive this way.
        domain = _host_is_the_account(host, site_names)
        # A brand's subdomain on a platform, opening with a sign-up step, is
        # that platform's programme page for the brand - see
        # `_PROGRAMME_PATH_SEGMENTS`. Neither the host nor the path is an
        # account there, whatever role word the path begins with.
        if domain and _is_a_programme_page(bare):
            continue
        named_host = bool(domain) and counts_as_off_site_profile(
            url, declared=True, subresource_hosts=subresource_hosts)
        if not counts_as_off_site_profile(url):
            if named_host:
                out.append((platform or domain, url))
            continue
        kind, _handle = _account_shape(bare)
        # A bare handle is only an account where the root of the path is the
        # account namespace. On an unnamed host it is a section of that site -
        # `/pricing`, `/catalogue` - unless it spells this site's own name, in
        # which case a page named after the audited site on somebody else's
        # host is the thing this check exists to find.
        # Spells it, not contains it: an article slug on a stranger's blog
        # contains the site's name and is still the stranger's article.
        if kind == "handle" and not platform and not _handle_is_the_name(url, site_names):
            kind = ""
        # An individual's account is this site's only where the site says so.
        if kind == "personal" and not (declared or url in published):
            kind = ""
        if not kind:
            if named_host:
                out.append((platform or domain, url))
                continue
            owner = _repository_owner(host, bare, repeated_owners, site_names)
            if not owner:
                continue
            out.append((platform or host, owner))
            continue
        out.append((platform or host, url))
    return out


# Hosts where the first path segment is an account and everything after it is
# that account's work. The same hosts `audit_common` reads the owner from, for
# the same reason: the last segment of a repository address is a file in
# somebody's tree, not an identity.
_REPOSITORY_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org",
                     "sourceforge.net", "gitea.com")


def _repository_owners(candidates):
    """How many times each repository account appears across these links.

    A brand's own organisation is linked from several places - the API docs,
    the SDK, the command-line tool. A dependency or a credit is linked once.
    That difference is available here, where the brand name is not.
    """
    counts = {}
    for url in candidates:
        bare = (url or "").lower().split("?")[0].split("#")[0]
        without_scheme = bare.split("://")[-1]
        host = strip_www(without_scheme.split("/")[0])
        if host not in _REPOSITORY_HOSTS:
            continue
        segments = [s for s in without_scheme.split("/")[1:] if s]
        if len(segments) >= 2:
            counts[(host, segments[0])] = counts.get((host, segments[0]), 0) + 1
    return counts


# How many links to one account make it the site's own rather than a mention.
_OWN_REPOSITORY_LINKS = 2


def _repository_owner(host, bare, repeated_owners, site_names=()):
    """The account URL behind a repository link, when it is the site's own.

    A project-hosting URL is a profile one level up from where the site links
    it. Requiring a single path segment meant an organisation linked only ever
    as `github.com/<org>/<repo>` was never counted - the exact case
    `audit_common` records as a measured error, fixed there for attribution and
    not here for detection. A software company linking six of its own
    repositories was reported, at high severity and first in what to fix, as
    linking to no off-site profile at all.

    A single link is not enough: `github.com/<somebody-else>/<library>` is a
    dependency or a credit, and counting it would put a stranger's account into
    the brand's corroboration total.

    Unless the owner segment spells this site's own name, in which case one
    link is the whole of the evidence and repetition adds nothing to it. The
    miss, measured: a link reading `<repository host>/<the site's
    name>-app/<the site's name>`, labelled "source", sits in the audited site's
    own footer; the crawl had already recorded it in `links.external`; and the
    report said the site links no repository at all - on a project whose source
    repository is the single most load-bearing thing about it. The owner
    segment is the site's name plus a word, and the count rule could never see
    that, because a link in the footer of a one-page site appears once.
    """
    if host not in _REPOSITORY_HOSTS:
        return ""
    segments = [s for s in bare.split("://")[-1].split("/")[1:] if s]
    # `_NOT_A_HANDLE` already holds every one of these - a repository host's own
    # pages sit in the same namespace its accounts do, which is the same reading
    # `_account_shape` applies everywhere else.
    if len(segments) < 2 or segments[0] in _NOT_A_HANDLE:
        return ""
    owner = "https://" + host + "/" + segments[0]
    if _handle_is_the_name(owner, site_names):
        return owner
    if repeated_owners.get((host, segments[0]), 0) < _OWN_REPOSITORY_LINKS:
        return ""
    return owner


def _is_hidden(node):
    """Markup that says this element is not displayed."""
    if node.get("hidden") is not None:
        return True
    if str(node.get("aria-hidden", "")).lower() == "true":
        return True
    style = (node.get("style") or "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style or "opacity:0" in style


def _interstitial(soup):
    # Script source is not evidence about what a visitor sees. A Hindi
    # newspaper's homepage contains the string "interstitial" 39 times, every
    # one of them inside a JavaScript identifier - `INTERSTITIAL_BLOCKED_BY_URLS`,
    # `interstitialAdsCheckByClasses()` - belonging to code whose purpose is to
    # *suppress* interstitial ads. The page was reported as covering its content
    # the moment a visitor arrives, on 43 pages, from the name of a variable.
    without_code = make_soup(str(soup)[:200000])
    for tag in without_code(["script", "style", "template", "noscript"]):
        tag.decompose()
    markup = str(without_code).lower()
    cookie = [hint for hint in COOKIE_BANNER_HINTS if hint in markup]
    modal = [hint for hint in MODAL_HINTS if hint in markup]
    # Substring class matching flagged 18 of 20 pages on a real retail site,
    # because `class="modal-opener"` contains both "modal" and "open". Match
    # whole class tokens, and require the element to be genuinely displayed.
    # Markup that states it is open.
    blocking = bool(soup.select(
        'dialog[open], '
        '[class~="modal"][class~="open"], [class~="modal"][class~="is-open"], '
        '[class~="overlay"][class~="active"], [class~="modal"][class~="show"]'
    ))

    # An `aria-modal` dialog is NOT evidence on its own. Every dialog component
    # in every component library carries `role="dialog" aria-modal="true"` in
    # the DOM while closed, so presence alone reported 22 pages of one real
    # site as covering their content the moment a visitor arrives. It counts
    # only when it is not hidden and something else says it is showing.
    #
    # The previous loop also cleared `blocking` outright when the *last* such
    # node was hidden, discarding a genuine overlay found by the selectors
    # above.
    for node in soup.select('[aria-modal="true"][role="dialog"], [role="dialog"]'):
        if _is_hidden(node):
            continue
        tokens = {c.lower() for c in (node.get("class") or [])}
        identity = " ".join(tokens | {str(node.get("id") or "").lower()})
        if tokens & {"open", "is-open", "active", "show", "shown", "visible", "is-visible"}:
            blocking = True
        # A consent wall or a newsletter interstitial is a different animal
        # from a component-library dialog: it is displayed on first paint by
        # design, which is the whole point of it. Named by its own class, and
        # not hidden, it counts.
        elif any(hint in identity for hint in COOKIE_BANNER_HINTS + MODAL_HINTS):
            blocking = True
        # An overlay is the backdrop that covers the page behind it, which is
        # exactly the claim this finding makes. A dialog that is not hidden by
        # any markup signal and whose own class calls itself an overlay is
        # showing.
        #
        # This is narrower than the rule that caused the false positives above:
        # those were `aria-modal` dialogs with ordinary component-library class
        # names, and a library ships its closed dialog hidden. It is also the
        # only thing that detects the plainest shape there is, `class="modal-
        # overlay" role="dialog"`, which the mutation suite has had a case for
        # and which nothing was catching - the check was listed, documented and
        # blind to its own headline example.
        elif any("overlay" in token for token in tokens):
            blocking = True
    return {
        "cookie_banner_hints": cookie[:5],
        "modal_hints": modal[:5],
        "blocking_overlay": blocking,
    }


def _newsletter(soup, text):
    lower = text.lower()
    if any(f["is_newsletter"] for f in _forms(soup)):
        return True
    if soup.select('input[type="email"]') and any(hint in lower for hint in NEWSLETTER_HINTS):
        return True
    return False


# Words above which a sentence is reported as too long to quote. Named
# because two things read it now: the count itself, and the rule below that
# decides what counts as a sentence at all.
LONG_SENTENCE_WORDS = 30

# Punctuation that separates one clause from the next, and punctuation that
# ends a sentence, in the scripts this audit has met. Both lists are needed by
# `_is_a_run_of_labels`, and neither may be Latin-only: an Arabic and a
# Japanese page were each once read as one sentence hundreds of words long
# because the sentence splitter knew only the Latin full stop.
_CLAUSE_PUNCTUATION = u",;:、，；：،؛"
_SENTENCE_END = u".!?。！？।॥۔؟"


def _is_a_run_of_labels(text, words):
    """Is this "sentence" a strip of menu labels rather than writing?

    A crawl of one site read a page whose longest sentence was 307 words:
    "login all Profile Orders Store Credit RM 0.00 Favourites Address Book
    Review Purchases Membership My Coupon Affiliate Log Out all shop
    categories signature market breakfast nuts snacks ...". Ten of the
    fourteen "sentences" on the page were that account menu and that category
    nav, and the finding read "47% long, average 67.6 words" about a page
    whose prose is neither.

    Two tests, both language-neutral, because a test for an English verb would
    read every Japanese, Polish and Arabic page as a list. A run of writing
    longer than `LONG_SENTENCE_WORDS` either ends in a terminator or carries a
    comma, a colon or a semicolon somewhere - clauses are what make a sentence
    that long. A strip of labels has neither: nothing terminates it, because
    the splitter had nothing to split on, and nothing separates its parts,
    because they were separate elements.

    The word bound is what keeps the rule off ordinary text. A heading, a
    caption or a short fragment with no full stop is under it and is untouched.
    """
    if words <= LONG_SENTENCE_WORDS:
        return False
    # Closing quotes and brackets sit outside the terminator: `(...like this.)`
    tail = text.rstrip(u" \t\"'’”)]}»")
    if tail and tail[-1] in _SENTENCE_END:
        return False
    return not any(character in _CLAUSE_PUNCTUATION for character in text)


def prose_sentences(body_text):
    """The sentences in this text, with the runs of labels taken out.

    Returned as `(sentences, how many runs were set aside)` so a consumer can
    see that the page held some and how many, rather than a count quietly
    getting smaller.
    """
    out, set_aside = [], 0
    for sentence in sentences(body_text):
        if _is_a_run_of_labels(sentence, word_count(sentence)):
            set_aside += 1
            continue
        out.append(sentence)
    return out, set_aside


def recount_readability(readability, body_text):
    """Redo the numbers that come from the text, after the text has changed.

    The crawl strips site furniture after every page has been fetched, because
    repetition across pages is the only thing that identifies a menu - so the
    strip necessarily runs after `_readability`. It already rewrites
    `body_text`, `word_count`, `above_fold_text` and `cta`, and it did not
    rewrite this block, so every readability number in the snapshot described
    the page with its account menu and its category nav still in the text.
    That is where "47% long, average 67.6 words" came from on a page whose
    crawl notes say twelve blocks of site furniture had been identified.

    Only the text-derived numbers. `wall_of_text_count`, `subheading_count`
    and `list_text_share` are measurements of the document, and the crawl no
    longer holds the document by the time the strip runs; they are left as
    `_readability` measured them and are unaffected by furniture, which is
    neither a `<p>` nor a heading of the page's own.
    """
    if not isinstance(readability, dict):
        return readability
    sents, set_aside = prose_sentences(body_text)
    lengths = [word_count(s) for s in sents]
    long_sentences = [n for n in lengths if n > LONG_SENTENCE_WORDS]
    words = word_count(body_text)
    subheadings = readability.get("subheading_count") or 0
    readability.update({
        "sentence_count": len(sents),
        "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_sentence_count": len(long_sentences),
        "long_sentence_share": round(len(long_sentences) / len(lengths), 3) if lengths else 0.0,
        "label_runs_set_aside": set_aside,
        "words_per_subheading": (round(words / subheadings, 1) if subheadings
                                 else float(words)),
    })
    return readability


def _readability(body_text, soup):
    sents, label_runs = prose_sentences(body_text)
    lengths = [word_count(s) for s in sents]
    long_sentences = [n for n in lengths if n > LONG_SENTENCE_WORDS]
    paragraphs = [_text_or_empty(p) for p in soup.find_all("p")]
    walls = [p for p in paragraphs if word_count(p) > 200]
    words = word_count(body_text)
    subheadings = len(soup.find_all(["h2", "h3", "h4"]))
    return {
        "sentence_count": len(sents),
        "avg_sentence_words": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        "long_sentence_count": len(long_sentences),
        "long_sentence_share": round(len(long_sentences) / len(lengths), 3) if lengths else 0.0,
        # Runs of menu labels the sentence splitter returned as one sentence,
        # taken out of every number above. See `_is_a_run_of_labels`: a page
        # whose account menu survived the furniture pass was reported as
        # writing in sentences 67 words long.
        "label_runs_set_aside": label_runs,
        "wall_of_text_count": len(walls),
        "subheading_count": subheadings,
        # With no subheadings at all the ratio is the whole page, which is
        # true but reads as a measurement. The consumer needs to be able to
        # tell "one subheading every 1,208 words" from "no subheadings".
        "words_per_subheading": round(words / subheadings, 1) if subheadings else float(words),
        "has_subheadings": bool(subheadings),
        # How much of this page is a list rather than writing.
        #
        # Every prose statistic here assumes the page contains prose, and a
        # catalogue defeats them all: a page of several thousand contributor
        # names, or an alphabetical index of authors and titles, has no full
        # stops, so the sentence splitter returns one sentence of 92 words and
        # the page is reported as written in sentences too long to quote. The
        # advice - break up your sentences - is not actionable about a list.
        #
        # The consumer cannot compute this: `paragraphs` is emptied by the
        # crawl's site-wide boilerplate strip, so a page of pure prose can
        # report none, and text length says nothing about shape. Measured
        # here, on the same region the other numbers come from.
        "list_text_share": _list_text_share(soup, words),
    }


def _list_text_share(soup, words):
    """The share of this region's words that sit inside list or table cells."""
    if not words:
        return 0.0
    inside = sum(word_count(_text_or_empty(node))
                 for node in soup.find_all(["li", "td", "th", "dt", "dd"]))
    return round(min(inside / words, 1.0), 3)


def _chrome_signature(soup, origin, base):
    """A stable fingerprint of the header/footer nav.

    Pages whose chrome differs from the rest of the site strand a visitor who
    arrived mid-journey with no way back.
    """
    urls = set()
    for node in soup.select("nav, header, footer, [role=navigation], [role=contentinfo]"):
        for anchor in node.find_all("a", href=True):
            url = normalise_url(anchor["href"], base)
            if url and same_site(url, origin):
                urls.add(urlparse(url).path.rstrip("/") or "/")
    return {"nav_paths": sorted(urls)[:40], "nav_path_count": len(urls)}
