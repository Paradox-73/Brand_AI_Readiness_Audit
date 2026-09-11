---
name: freshness-corroboration-audit
description: Check whether a brand's facts are current, agreed upon across independent sources, and distinguishable from other things sharing its name. Audits content staleness and missing date signals, counts how many authoritative off-site profiles corroborate the brand and confirms those profile links still resolve, queries Wikidata for name collisions, and detects where the site contradicts itself on telephone, address or boilerplate. Use when an AI assistant describes a brand with outdated prices, an old logo or a discontinued product, when it confuses the brand with a different company of the same name, or when a rebrand has not been picked up anywhere.
license: MIT
compatibility: Requires Python 3.10+ with requests. Makes up to 16 extra read-only requests, each only where that host's own robots.txt allows it - Wikidata's API included: robots.txt per host, up to 3 Wikidata lookups, one HEAD per off-site profile link and a GET to confirm any 404 or 410. A host whose robots.txt disallows the request is recorded unchecked, never probed. Pass --no-network for none.
allowed-tools: Bash(python3:*) Bash(python:*) Read WebSearch
metadata:
  author: brand-ai-readiness-audit
  version: "1.0.0"
  mechanism: "D"
  gate: "trusted"
---

# Freshness and corroboration audit (mechanism D: fresh, agreed-upon, unambiguous)

## When to use

Run after extractability. The previous gates ask whether a fact can be reached, read and
lifted. This one asks whether it will be **believed**. Machines treat a fact as trustworthy
when independent sources agree on it, and prefer the more recent of two sources that
disagree. A brand whose new facts exist only on its own site, while directories and cached
pages still repeat the old ones, loses that comparison every time.

Reach for it on its own after a rebrand, a price change, or a move — or when an assistant
confuses the brand with something else that shares its name.

## Inputs

- `--snapshot snapshot.json` produced by the orchestrator's `crawl.py`.
- `--out <path>` for the findings JSON.
- `--now YYYY-MM-DD` fixes the reference date so a run is reproducible. Defaults to today,
  UTC. **Always pass it in tests**, or "18 months old" changes meaning as time passes.
- `--no-network` skips the Wikidata lookup.

## Procedure

Ten checks in two halves. Each line is the question and the worst severity it can reach.
`references/corroboration-checks.md` lists every registered check id with what it reads, its
threshold and where that number came from, the severity it can reach, where it declines, and
the real report behind each exclusion.

**The date reader parses four Gregorian shapes:** an ISO date, an English month name with a
day and year, and a numeric slash date whose year is `19xx` or `20xx`. Four other calendars
are read by a second path — Thai Buddhist era, Japanese era names, and both Hijri calendars,
the lunar `هـ` and the solar `هـ.ش` — in any of three digit sets, and each needs a mark
the page printed itself. Each yields a **year alone**, taken as 1 January so the reading can
never overstate freshness. An unmarked number is read as no calendar at all. The reference
gives the marks, the digit sets and the conversion.

### Freshness

1. **Content staleness**, only over three or more article pages. **Medium** when 70% or more
   are older than 18 months *and* the newest is over 12 months old. Both conditions: an active
   blog with a deep archive is healthy, and only the second tells it from an abandoned one.
2. **Missing date signals**, a different problem from an old date. No `<time>`, no
   `datePublished`/`dateModified`, no visible date: **medium**. Not expected on evergreen
   pages, and never on a page whose job is to point at other pages.
3. **Footer copyright year.** More than one year behind is **low** — the cheapest liveness
   signal on a site. Only the site's own notice, and only where it actually sits.
4. **Stale year references in copy.** "As of 2023", "prices for 2021" — two or more years
   behind is **low**. Only phrases asserting the copy is current as of a year; a bare "in
   2019" is usually a founding date.
5. **Sitemap `lastmod` distribution.** Nothing modified in over a year is **low**: `lastmod`
   is what decides re-fetch frequency, so a frozen site is re-read rarely.

### Corroboration

6. **Off-site profile breadth**, counted across every recognised platform rather than a
   shortlist. Six or more is a pass, three to five **low**, under three **medium**, none at
   all **high**. A profile is an account, not a platform: a bare host, a code host and a
   role-named subdomain are infrastructure, and one hosted store counted 8 where the truth
   was 3.
7. **Do those profile links resolve?** One HEAD each, a GET to confirm any 404 or 410. One
   gone is **low**, more than one **medium**. Only six platforms answer honestly about whether
   a profile exists; the rest are recorded unchecked, never dead. Dead links are **not**
   subtracted from check 6's count, which is compared against a study of declared links.
8. **Entity ambiguity.** Wikidata's robots.txt first, read with the same reader the profile
   probes use; where it closes the API path (`Disallow: /w/`) no lookup is made and the check
   is recorded unchecked with that reason, never as a finding. Otherwise Wikidata's search API
   for the name, then one lookup asking which
   match declares this site as its own. Two or more others with no disambiguation on the site
   is **medium**, four or more **high** — and a single match declaring a *different* host is a
   collision confirmed, reported at a count of one.
9. **Self-contradiction. High.** An `Organization` telephone or postal code that disagrees
   with the same page, or more than one distinct `Organization` description across the site.
   JSON-LD *name* and *price* mismatches belong to `structured-data-audit`; this skill owns
   telephone, postal address and the boilerplate.

Four rules govern all ten, and are what to carry over when working through this by hand.

- **A refusal is not an answer.** A 403, a timeout or a redirect to a sign-in page is
  **unchecked**. Instagram answers 200 to a profile that does not exist, so a 200 there proves
  nothing either.
- **Zero results is a fact about the string, not about the world.** Wikidata matches labels by
  prefix, so a name returning nothing has almost always been mis-taken from the page. Name the
  strings searched and never call it "no collision".
- **A name test that cannot pass must not be the thing that rejects.** A handle comparison
  that keeps only letters and digits reduces a Japanese, Korean, Arabic, Greek or Cyrillic
  name to nothing, and *no handle on earth* can match it. Abstain instead — a city
  government's five official accounts were dropped that way and the report led with
  "profiles linked: none".
- **`checked[]` states the reading the code performs**, not the one it was meant to feel like.
  A method claim the code does not implement is worse than a wrong count, because a reader
  cannot argue with an untold rule.

## Output

Standard skill JSON: `findings[]`, `checks_run[]`, `not_applicable[]` (each with a reason),
`extra_requests_made` (at most 16), and signals `authoritative_profile_count`,
`profile_platforms`, `profile_breadth`, `profile_links_checked`, `profile_links_alive`,
`profile_links_gone`, `profile_links_unchecked`, `has_wikidata_or_wikipedia`,
`wikidata_match_count`, `wikidata_matches`, `no_date_signals`, `newest_article_date`,
`press_page_present`, `distinct_org_descriptions`, `suggested_web_search`,
`profiles_declared_in_llms_txt`, `listing_pages_not_expected_to_carry_a_date`,
`reference_date`, `wikidata_item_exists`, `wikidata_own_entity`,
`wikidata_search_returned_nothing`, `wikidata_own_entity_lookup_failed` and
`wikidata_other_entity_sites` — the same-named items that publish an official website of
their own, each with the host it names, which is the evidence a collision rests on.

`wikidata_item_exists` has three states, and a consumer that reads it as a boolean tells a
brand to create the item it already has. `true` means an item is known to exist, `false`
that the lookup ran and named none, and **`null` that nothing was measured** — no network,
no label matched at all, or the request asking which item names this site never came back.
Only `false` is grounds for recommending that the owner create one.

All findings carry mechanism `D` and a `root_cause` of `stale-content`, `no-date-signal`,
`weak-corroboration`, `dead-profile-link`, `entity-ambiguity` or `nap-inconsistency`. The
headline fix is a **canonical boilerplate block** — one paragraph of identity, address and
contact details to reuse verbatim on the site, LinkedIn, the press kit, app stores and every
directory. Identical wording across independent sources is the strongest agreement signal
available and costs nothing but discipline.

A finding here that says something is **missing** carries `checked[]`, the independent
sources it consulted and found empty; one source caps it at medium confidence and the report
says so. Working through this skill by hand, do the same: say where you looked, and look in a
second place before stating at high confidence that something is not there. Every false
positive this marketplace has made on a real site was one detector with a finite list of
shapes, reported as a fact about the site.

## Not applicable when

- Fewer than three article pages were crawled — there is no publishing cadence to assess.
- No article carries a parsable date — reported as a missing date signal instead, not as
  staleness. The two have different fixes.
- No article or press pages exist — dates are not expected on evergreen pages.
- No copyright year appears anywhere in the text, or every copyright line found credits
  another party for material the site reproduces — the reason quotes the lines.
- Fewer than five sitemap entries carry a parsable `lastmod`.
- Six or more distinct off-site profiles are already linked — the reason names every one of
  them, and says the footprint is at or above the level typical of brands assistants name.
- No profile is linked on a platform that answers honestly about whether a profile exists, or
  every verifiable link resolved — the reason names each one that was checked.
- Wikidata returns fewer than two matches and none declares an official website on a different
  host, or the site already disambiguates itself. Where the count is one, the reason names it.
- Wikidata returns nothing at all for any form of the name that was searched — reported as a
  fact about the name string this audit was given, never as an absence of collisions.
- `--no-network` was passed, or Wikidata could not be reached: an audit-environment
  limitation, recorded as such and never as a site defect.
- Wikidata's robots.txt disallows automated clients on its API path: "Wikidata's robots.txt
  closes its API to automated clients, so no lookup was made". No request is sent to the API,
  and nothing is claimed about how many things share the name.
- A profile host answered 403, timed out, or redirected to a sign-in page. That is recorded
  as unchecked, never as a dead link.

`references/corroboration-checks.md` gives the wording each of these reasons has to reach —
including the 93%-stale library a bare pass filed under "nothing to report".

## References

- `references/corroboration-checks.md` — every one of the ten registered check ids: what each
  reads, its threshold and where that number came from, the severity it can reach, what the
  date reader can and cannot parse, where it declines, the platform-honesty table behind
  check 7, and the real report behind each exclusion.
- `references/entity-disambiguation.md` — what makes two same-named entities separable, which
  markers matter most, and how to create a Wikidata item that holds.
