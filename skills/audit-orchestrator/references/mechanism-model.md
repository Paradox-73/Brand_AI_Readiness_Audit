# The mechanism model

Every check in this marketplace traces back to one of the mechanisms below, and every
finding names the letter it repairs in its `rationale` field. This is not decoration. It is
the thing that keeps the audit from becoming a checklist: if a proposed check cannot be
traced to a mechanism, it does not go in.

---

## A. Three gates, in order

A page is visible to a machine only if three things succeed, in sequence:

1. **The crawler is let in** — robots.txt permits it, the status code is 200, and no WAF or
   bot manager returns a challenge page.
2. **The crawler can read it** — the HTML delivered in the first response contains the
   content as text, rather than assembling it with JavaScript or locking it in images.
3. **The crawler can extract the specific fact** — the thing it needs is stated explicitly
   and unambiguously in plain text.

Fail any one and the page does not exist for that system, however good the rest is. This
ordering is why the sub-skills run in the order they do, and why a critical finding at gate 1
makes conclusions about gate 3 unreliable.

**Owned by:** `crawl-access-audit` (gate 1), `render-readability-audit` (gates 1–2).

---

## B. Assistants quote what is easy to reach, read and lift

When an assistant answers a question it often fetches live pages and builds the answer from
what those pages happen to say. What ends up in the answer is not the best content; it is the
content that was **easiest to reach, easiest to read, and easiest to quote a clear fact
from**.

This is the mechanism most sites fail without knowing it. A page can be crawlable, fast and
correctly marked up, and still lose, because nothing on it survives being lifted out of its
paragraph. Confident prose that never states a number is unquotable.

The practical test: read only the first sentence of each section. Does it answer the heading?

**Owned by:** `fact-extractability-audit`, with `structured-data-audit` covering the metadata
that gets quoted directly.

---

## C. A page complete to a human can be empty to a machine

The gap between what a person sees and what a machine receives is the single largest source
of invisible failure:

- content assembled client-side after the first response;
- text baked into images, so it is pixels rather than characters;
- facts that exist only inside a PDF, or only spoken aloud in a video;
- content inside an iframe, which is a separate document the parent does not contain;
- facts that are *implied* by design and layout rather than *stated* in a sentence.

In each case the page looks finished on screen and arrives empty. The more explicitly and
unambiguously a fact is stated in plain, readable text, the more likely it is extracted
correctly; the more it is implied, buried, or locked inside something non-textual, the more
likely it is missed.

**Owned by:** `render-readability-audit` (non-text), `structured-data-audit` (explicitness),
`fact-extractability-audit` (unambiguity).

---

## D. Trust comes from agreement across independent sources

Machines treat a fact as more trustworthy when many independent places say the same thing. A
claim that lives in only one spot is fragile. The same claim repeated consistently across
unrelated sources is far more likely to be believed and repeated back.

Two consequences the audit acts on:

- **Freshness is a tiebreaker.** Where two sources disagree, the more recent usually wins. A
  brand whose new facts exist only on its own site, while directories and cached pages still
  carry the old ones, loses that comparison by default.
- **Name collisions cause mistaken identity.** When several different things share a name, a
  system can blend them unless something clearly distinguishes one from the others. Without
  explicit markers, a brand's facts get attributed to whichever entity is better documented —
  usually not the smaller one.

The corollary that surprises people: **a site that contradicts itself is worse than one that
says nothing**, because an internal contradiction undermines every other fact on it too.

**Owned by:** `freshness-corroboration-audit`, with `structured-data-audit` covering
`sameAs` completeness.

---

## E. Assistants personalise by context

These systems do not answer every user identically. They draw on context they already hold —
earlier messages, past questions, stated preferences, location — and weight the answer toward
what seems most relevant to *this* asker. Two people can ask the same question and get
different brands, framings and emphasis.

So a brand with content written for only one framing is surfaced for only that framing. It is
absent from every "X vs Y" question, every "best X for <use case>" question, and every
location-qualified question, regardless of how good its single page is.

This mechanism produces recommendations rather than defects: comparison pages, use-case
pages, location pages. There is no way to look at a site and call the absence of a comparison
page a *bug*.

**Owned by:** the orchestrator's proactive recommendations.

---

## F. AI email summaries are built from readable text

Inboxes increasingly show short AI-generated summaries of long messages, and those summaries
are built from the **text** of the message. When the real substance of an email is not
available as readable text — carried in an image a summariser cannot read, or surrounded by
low-value filler — the summary has little to work with and the important part simply
disappears.

This applies to a brand's newsletter and transactional email, not to the website crawl. The
audit only detects whether an email capture exists and, if so, attaches the guidance.

**Owned by:** `engagement-audit` (detection only) and the proactive recommendations.

---

## G. A visitor arriving mid-journey carries context the site never sees

*This letter is this marketplace's own extension to the model.*

Mechanisms A–F describe how machines find, read and repeat content. None of them describes
what happens after a person clicks through, and Round 2's third symptom — bouncing — is
exactly that. Folding it into E would misdescribe it: E is about how the *assistant*
personalises, G is about what the *visitor* arrives holding.

Someone sent by an AI answer is not a normal visitor. They arrive:

- **mid-journey**, having already had the explaining done for them elsewhere;
- **with specific intent**, checking whether this is the right place rather than browsing;
- **deep in the site**, often on a page that is not the homepage, with no navigation history
  behind them;
- **carrying context the site cannot see** — it does not know which question was asked, so it
  greets them exactly as it greets everyone.

That mismatch is the root cause of the bounce. The fixes are orientation (say what this is
immediately), a next step (never end a page with nothing), and a way back (breadcrumbs,
consistent chrome, related links).

**Owned by:** `engagement-audit`.

---

## Round 2's three symptoms, mapped

| Symptom | Fails | Where it shows up |
|---|---|---|
| **Invisible** — the brand is not found or cited | A, B, C | Blocked crawlers, JS shells, no quotable definition |
| **Stale** — the brand is described with old or wrong facts | D | Old dates, no corroboration, self-contradiction, name collisions |
| **Bouncing** — visitors arrive and leave | G, F | No orientation, dead ends, no way back |

See `round2-failure-modes.md` for the full failure-mode list and the fix each one maps to.
