# Which strings are this site's name

Every matcher in `audit_common.py` answers "are these two names the same name". None of them
answered the question in front of it: **which strings should be offered as this site's name
in the first place.** That was decided once, by taking `og:site_name` or `<title>` verbatim
and the bare host label. Measured on sites that are not English-language shops, that costs
this much:

| Site | What was offered | What went wrong |
|---|---|---|
| a design firm | `"Premier Interior Designer, Harbour District"` and `"larkfielddesign"` | the homepage opens *"Larkfield Design is a full-service, client-focused design firm specializing in residential projects"* — a textbook definition sentence — and the report said no page states in one sentence what the brand is. The words the domain spells were never tried. |
| a dental clinic | the whole SEO title, three segments and two full-width separators | the report's opening sentence read *"A machine can reach &lt;31 characters of title&gt; and read it"*, and the same string was sent to an encyclopedia as a search term. |
| a town government | `"Town of …"` | nothing the town writes about itself repeats the prefix. |

Two failures with opposite cures, which is why one list cannot serve both. Missing "Larkfield
Design" made a check state a **falsehood about the site**, and the cure is more candidates.
Sending an SEO title to an encyclopedia would have **published somebody else's identifier**
under this brand's name, and the cure is fewer.

---

## Two tiers

```python
ASSERTED = "asserted"
DERIVED  = "derived"
```

**`ASSERTED`** — the site published this string as its name, or it is what is left of one
after *removing* something the site also wrote: a legal suffix, a leading article, a trailing
domain suffix, the tail of a title after its separator. Removing is safe.

A run of words the site printed whose letters are exactly its own domain label is asserted
too. The site wrote the words and the address confirms them, and no coincidence produces
that.

**`DERIVED`** — this audit built the string and nobody published it: the host label on its
own, a dropped civic prefix. Useful, and never allowed to be the reason a match succeeds
where a wrong match publishes a fact.

**A wrong alias is worse than no alias.** The danger is on this repository's record: a
substring test once counted fifteen unrelated encyclopedia articles as one brand's profiles,
and a one-word brand key credited a digital library with the article about the 15th-century
printer it is named after. So the tier a check may read is part of that check's contract,
not a matter of taste.

---

## The API

In `skills/audit-orchestrator/scripts/audit_common.py`.

```python
from audit_common import (
    NameForms, name_candidates, ASSERTED, DERIVED,
    title_segments, without_civic_prefix, looks_like_a_page_title,
    name_the_site_spells,
)

names = name_candidates(snapshot["brand"],
                        extra_asserted=(),        # a published string the record lacks
                        texts=(home_title, home_h1))
```

| Reader | Returns | For a check whose failure mode is |
|---|---|---|
| `names.for_matching()` | both tiers, longest first | **a miss** — the definition sentence, the name-appears-on-the-page test. Accepting a weak alias there suppresses a finding, and a suppressed finding is smaller harm than the falsehood printed in its place when the alias is withheld. |
| `names.for_lookup()` | asserted only, shortest usable first | **publishing somebody else's identity** — an encyclopedia or knowledge-base lookup, attributing an off-site account to this brand, filling the `name` of a snippet the owner will paste. A derived string may never be the reason one of those succeeds. |
| `names.display()` | one string | a sentence a person reads. Never a page title, never the flattened host label. |
| `names.tiers()` | `[(form, ASSERTED \| DERIVED)]` | the audit trail, so a finding can say which reading it accepted. |

`for_lookup()` is ordered **shortest-first**, the opposite of every other reader here. A
lookup sends one string to somebody else's index, and the string with the best chance of
naming the organisation there is the organisation's name — not its name with a legal suffix
and a tagline attached.

`for_lookup()` is a list of strings to **try**, and it is not permission to accept what comes
back. Whatever an index returns still has to survive `names_are_one_name` against the label
it returned — never a substring test — and a search string being in this list says nothing
about whether its first result is this organisation.

Measured on the three sites in the table above:

```
brand.name = "Premier Interior Designer, Harbour District", host = <label> "larkfielddesign"
  for_matching -> ('Premier Interior Designer, Harbour District', 'Larkfield Design')
  for_lookup   -> ('Larkfield Design', 'Premier Interior Designer, Harbour District')
  display      -> 'Larkfield Design'

brand.name = "Town of Ashcombe"
  for_matching -> ('Town of Ashcombe', 'Ashcombe', 'ashcombe')
  for_lookup   -> ('Town of Ashcombe',)          # 'Ashcombe' is derived; a village
  display      -> 'Town of Ashcombe'

brand.name = <a three-segment Japanese SEO title>
  display      -> the clinic's name alone, not the title
  for_lookup   -> the clinic's name first
```

---

## The three helpers, usable on their own

```python
title_segments(title) -> list[str]
```
The parts of a page title, in the order it writes them. Splits on `|`, the full-width bar,
en and em dashes, the middle dot, bullets and guillemets, and on `-` **only with space on
both sides** — otherwise a hyphenated brand becomes two names. Deliberately **not** a colon:
`"charity: shelter"` is a brand name and splitting it invents two.

```python
without_civic_prefix(name) -> str
```
`"Town of Ashcombe"` → `"Ashcombe"`. Kept to civic bodies — town, city, village, borough,
township, county, district, municipality, parish, commune. `"University of X"` and
`"Department of Y"` look like the same shape and are not: dropping the prefix there leaves
`"Computer Science"`, and a definition check handed that as a subject would accept a sentence
about the field.

```python
looks_like_a_page_title(value) -> bool
```
True when a string carries a separator, runs past 60 characters, or past 7 words. This is
what keeps the report's prose out of *"A machine can reach &lt;whole SEO title&gt; and read
it"*.

```python
name_the_site_spells(token, texts, max_words=4) -> str
```
The words the site prints that its own domain label spells: `larkfielddesign` →
`"Larkfield Design"`, `thelittlepaperco` → `"The Little Paper Co"`. Every run of up to four words in `texts`
is flattened and compared, and the comparison is **exact letter equality** — so this cannot
return a string the site did not print or a string the domain does not spell, which is what
makes its answer asserted rather than guessed. Four words is enough for `"The Little Paper Co"` and
short enough that no sentence matches one by accident; the four-letter floor stops a short
domain matching a common word.

`crawl.py` had this reading under the name `_site_spelling` and used it in exactly one
place — only when the domain was the last resort for the brand name. On the design firm the
brand name came from a title, so it never ran, and the name the whole site is built around
was never offered to any matcher. **`crawl.py::_site_spelling` is the copy to delete**, the
same way `structured-data-audit`'s `_DATED_ARCHIVE_PATH_RE` is being replaced by the public
`DATED_ARCHIVE_RE`. Two copies of one rule in two files that two people edit drift apart
without either of them noticing.

---

## What already changed inside this file

`brand_forms` now appends `name_the_site_spells(domain_token, [name] + fallback_candidates)`.
That is the whole Larkfield fix and it is one string: the words the domain spells, letter for
letter.

Only that one reading of `fallback_candidates`, and not the candidates themselves. They
include the long title, and letting a headline into the subject list would let a definition
check accept a sentence about something the site merely talks about.
