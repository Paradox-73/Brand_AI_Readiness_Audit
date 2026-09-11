# Which disallowed paths are real content, and what the fix may open

Two questions this skill has to answer about a `Disallow:` line, and they are separate. Is
the path it closes something a person reads, or plumbing? And, once a path worth reopening
has been found, what exactly does the paste-ready block open?

Both have been got wrong on real sites, in the same finding, on the same day.

---

## The vocabulary: a class, never the name that caught us

`benign_disallow` in `robots_parser.py` is the shared list, and every entry in it is a class
of path with a boundary. It is not a list of the paths this audit has been embarrassed by:
the next site uses a name nobody wrote down, and a list of names cannot be finished.

What was already there: admin, cart, checkout, account, login, search and their plurals;
query-string and wildcard filters; asset and build directories; error, maintenance and
staging copies; files shipped with the software rather than written by the site.

Three classes were missing, and a media site's report was made of all three.

**Signed-in account screens.** `/dashboard`, `/settings`, `/preferences`, beside `/account`
and `/login` which were already excused. A page nobody can reach without an account holds
nothing an answer engine could quote, and the site is right to keep it out of an index. The
report instead said "robots.txt closes 2 sections of the site to 9 AI answer crawlers it
names" and asked the owner to reopen them.

Matched only where the rule ends or a `/`, `?`, `*` or digit follows — not where a hyphen
does. `/dashboard-cameras` is a shop's product listing, and excusing it would hide a real
block on real content.

**A rule naming a file by its extension rather than a page.** `/me.json` answers 401 to
anyone not signed in; `/follows.json` is a reader's own follow list; `/translations.json` is
a bundle of interface strings. None is a page and none can be quoted, and all three were
listed as "paths that look like real content". The shared list already named `json` and
`xml` as words and could not reach any of them, because the boundary it matched them on has
no full stop in it: it read `/me.json` as the word `me`. The suffix is what says "machine" —
`.json`, `.ndjson`, `.geojson`, `.xml`, `.yaml`, `.yml`, `.map`, `.lock`.

**The metadata directory of a version-control working copy.** `/RCS/`, `/CVS/`, `/SCCS/`
beside `/.git` and `/.svn`, which were already there. One site's robots.txt closes `/RCS/` in
a block its own comment introduces as "big or useless directories"; the report named it as
real content. It holds file histories.

These three are matched **case-sensitively**, and that is the whole of the rule's safety.
The tools create their directories in capitals, while the same letters in lower case are
ordinary words: a recruitment agency's `/cvs` is a page of curricula vitae. The dot-prefixed
and underscore-prefixed forms need no such care, because no word begins with a full stop.

Two shapes are deliberately **not** in the vocabulary:

- **A path that happens to 404.** One report listed `/latest`, which answers 404. Nothing in
  a robots.txt says so, and `latest` is a real section name on a great many sites. A rule
  about what a name means cannot be inferred from one site's response.
- **`bag`, `bags`, `testing`, `demo`, `legacy`, `development`, `mirror`.** Each has a second
  meaning that is somebody's product listing or service page. Swallowing real content is the
  more expensive of the two mistakes.

---

## The fix: open the paths named, and nothing else

The paste-ready block under `robots-blocks-answer-crawler-sections` used to be the crawler
names followed by `Allow: /`:

```
# Answer and search crawlers: allowed everywhere.
User-agent: <nine names>
Allow: /
```

Pasted over the group it names, that reopens every other path the group closes. On one media
site those were `/api/`, `/graphql/`, `/_next/data/`, `/me.json` and `/translations.json` —
none of which the finding asked anyone to open, and all of which the finding's own
`how_to_fix` prose told the owner to leave alone. The snippet contradicted the instruction
printed directly above it.

The correct block is scoped to the paths at issue:

```
# Answer and search crawlers: allowed into the sections below.
# Add these lines to the existing group; do not replace it. Every other
# Disallow line in that group stays exactly as it is.
User-agent: <each named agent>
Allow: /store/
Allow: /research
```

Why this works, and why the `Allow:` line has to be spelt the way the `Disallow:` line is
spelt: longest matching rule wins, and Allow beats Disallow at equal length. An `Allow` of
the identical path is therefore a tie the path wins, while a shorter one loses — `Allow:
/dashboard` does not reopen `Disallow: /dashboard/`. Records naming the same product token
are combined (RFC 9309 §2.2.1), so the block joins the group already in the file rather than
replacing it, and every rule that group carries goes on working.

The steps beside it name the rules the block does not touch, so a reader can see that it did
not touch them, and the training-crawler half of a mixed group keeps its own opt-out
unchanged.

---

## A check that reaches no outcome is printed as a pass

Not a path question, but the same class of harm: a report that certifies.

A check registered with `result.check(...)` and then answered by neither a finding nor a
decline is rendered as a check that ran and was clean. On a store answering HTTP 402 at every
address — nothing crawled, nothing read — `homepage-reachable` and `non-200-rate` appeared
under "These ran against this site and were clean — they are not omissions". The homepage
branch found no record to read and returned; the rate had no denominator and fell off the end
of its chain of `elif`s.

Every branch where the data is missing declines, and says what was missing. Where the
snapshot carries `site_not_serving` — set by the crawl when every address it reached answered
the same status and that status speaks for the service rather than for the address (402, 410,
451, 503) — that is the reason, and it is a different sentence from "clean" and a different
one again from "the crawler was refused", which is what 401, 403 and 429 mean and which the
bot-manager checks own.

The homepage is found by the address requested, then by the address that answered, then by
the page type the crawl assigned. A front page reached through a redirect or a meta refresh
is recorded under where it landed, and looking only for the origin's own URL found nothing
and then said nothing at all.
