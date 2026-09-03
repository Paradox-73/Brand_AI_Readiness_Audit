# Evals

Three prompts a judge is likely to type, and what correct behaviour looks like for each.

These are behavioural expectations for the agent invoking the marketplace, not automated
tests — the automated suite lives in `tests/` and covers the deterministic parts. Run these
by hand against a site you control, or against any of the local fixtures.

Serve a fixture and audit it:

```bash
# Terminal 1 - serve a fixture. Use `python` on Windows, `python3` elsewhere.
python3 tests/serve_fixture.py js-shell-site
# prints e.g. http://127.0.0.1:54321 and stays up until you press Ctrl-C

# Terminal 2 - audit the URL it printed
python3 run_audit.py http://127.0.0.1:54321 --no-network
```

---

## Eval 1 — the direct request

> **"Audit https://example.com"**

**Should happen**

1. `audit-orchestrator` triggers. Nothing else is invoked directly — sub-skills are composed
   by the entrypoint, not chosen by the agent.
2. One crawl, then the six checks in gate order, then compose. Roughly 30–90 s on a small
   site.
3. The reply leads with the verdict sentence and the three "Start here" fixes, and points at
   `report.md`.

**Should not happen**

- Re-summarising every finding in chat. The report is the deliverable; the chat message is
  a pointer to it.
- Any attempt to fix the site. This marketplace is recommend-only.
- Fetching anything under `/cart`, `/checkout`, `/login`, `/admin`, or any path robots.txt
  disallows.

**Judge test.** Open `report.md` and read the appendix. Every check that stayed quiet should
say *why*. "No product detail pages were detected on this site, so Product and Offer markup
is not expected" is the shape to look for — it shows the audit considered the check and
declined it, rather than not looking.

---

## Eval 2 — the symptom, not the request

> **"Why is my brand not showing up in ChatGPT?"**

**Should happen**

1. The agent asks for the URL if it does not have one, then runs the full audit. The symptom
   spans all three discoverability gates, so a partial audit cannot answer it.
2. The answer is ordered by gate, because the gates are sequential:
   - **Gate A** — is anything blocked? robots.txt, bot manager, status codes, `noindex`.
   - **Gate C** — can a machine read and extract? JS shells, images, PDFs, structured data.
   - **Mechanism D** — is the brand corroborated and unambiguous?
3. If a critical gate-A finding exists, the answer says so first and says plainly that
   nothing downstream matters until it is fixed.

**Should not happen**

- Jumping straight to "add structured data". That is gate C, and it is wasted work if gate A
  is shut.
- Guessing without crawling.
- Claiming to know what ChatGPT currently says about the brand. The audit reads the site; it
  does not query any assistant. `citation_simulation[]` shows what is *available* to quote,
  which is a different and defensible claim.

**Judge test.** On `tests/fixtures/blocked-site` the answer must lead with the WAF block and
the **two** blocked answer crawlers — `OAI-SearchBot` and `PerplexityBot` — and must *not*
present the four blocked training crawlers as a problem, since that is `info` severity and a
rights decision.

That split used to read four and two, because `GPTBot` and `ClaudeBot` sat in the answer
group. They are training crawlers by their operators' own published descriptions, and the
report built on the old grouping told owners at high severity to allow-list them so their
pages could be cited. Following it would have reopened a site to training collection its
owner had deliberately opted out of, for no citation gain at all. Every agent named in the
report now carries its operator's own statement of what it does, and
`skills/crawl-access-audit/references/ai-crawler-user-agents.md` carries the URL and the
date that statement was read.

---

## Eval 3 — the other half

> **"Check if my site keeps visitors"**

**Should happen**

1. The full audit still runs — the crawl is shared, so there is nothing to save by skipping
   skills — but the answer is drawn from `engagement-audit`'s findings (mechanism `G`).
2. The answer covers the three things a visitor arriving mid-journey needs: orientation
   (does the page say what this is?), a next step (dead ends, CTAs), and a way back
   (breadcrumbs, consistent chrome, broken links).
3. Discoverability findings are mentioned only if they bear on engagement — a JS shell is
   both, since a visitor on a slow-hydrating page leaves too.

**Should not happen**

- Reporting bounce rate, session duration or any analytics figure. The audit has none. It
  finds *structural reasons* a visitor would leave, and should say so in those terms.
- Treating a cookie banner as a defect on its own.
- Reporting the newsletter signup as a problem. It is a trigger for the mechanism-F email
  recommendation, never a finding.

**Judge test.** On `tests/fixtures/dead-end-site` the answer should name the "Welcome" H1, the
single-item navigation, the 404 on the only onward link, and the three orphan pages — and
should recommend `R-EMAIL-TEXT-FIRST`, which fires on this fixture alone because it is the
only one with an email capture.

---

## What to look for across all three

| Property | How to check it |
|---|---|
| **Evidence, not adjectives** | Every finding cites counts and sampled URLs. "0/12 product pages contain schema.org markup", never "structured data could be improved". |
| **Fixes a non-expert can act on** | Every finding names an owner and a rough duration, and carries paste-ready code where code is the fix. |
| **Silence is explained** | `not_applicable[]` is populated and each entry gives a reason. |
| **Determinism** | Run twice on one site. `findings[]` must be byte-identical; only `audited_at` moves. |
| **No false positives** | Audit `tests/fixtures/good-site` **with `--no-network`**. It must report **zero** findings and still offer `R-BOILERPLATE`. Without the flag it reports one, correctly: the fixture's profile URLs use reserved `.example` names, so the profile-link check asks the real internet about them and is told they do not exist. That is the check working. |
| **Read-only** | Nothing is written to the target. Check the server log if you control the site. |
