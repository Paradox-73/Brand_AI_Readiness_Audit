"""Who each AI user agent actually is, and why the audit may not guess.

A round-4 verification agent called this the single most consequential wrong
finding in the whole report. It read, at rank two of "Start here", marked high
and "do first":

    Allow-list GPTBot, ClaudeBot, Amazonbot, Bytespider and Meta-ExternalAgent,
    because they fetch live pages to build cited answers.

Five of those five are training crawlers by their operators' own published
descriptions, and the sixth error was the reverse: Meta-ExternalFetcher, which
really does fetch pages for a person asking a question, sat in the training
group. An owner who did as instructed would have reopened their site to
training collection they had deliberately opted out of, in order to fix a
citation problem that did not exist.

The fix is not a corrected list. A corrected list rots the same way. It is:

  1. one record per agent carrying the operator's own words, the URL that says
     so and the date it was read;
  2. two roles where there was one, because a search index and a live fetch
     fail differently;
  3. the prose in the finding built from that record rather than written
     alongside it;
  4. these tests, which fail if the two ever disagree.
"""

from __future__ import annotations

import ast
import glob
import importlib.util
import io
import os
import re
import sys

import pytest

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CA = _module(os.path.join(ROOT, "skills", "crawl-access-audit", "scripts", "check.py"),
             "crawl_access_for_roles")

REFERENCE = os.path.join(ROOT, "skills", "crawl-access-audit", "references",
                         "ai-crawler-user-agents.md")


# --------------------------------------------------------------------------
# The table itself
# --------------------------------------------------------------------------

def test_every_agent_carries_a_role_this_code_understands():
    allowed = {CA.SEARCH, CA.USER_FETCH, CA.TRAINING, CA.CONTROL}
    for agent in CA.AI_CRAWLER_AGENTS:
        assert agent.role in allowed, "{} has role {!r}".format(agent.token, agent.role)


def test_no_agent_is_listed_twice():
    tokens = [a.token.lower() for a in CA.AI_CRAWLER_AGENTS]
    assert len(tokens) == len(set(tokens)), "duplicate token in AI_CRAWLER_AGENTS"


def test_the_two_groups_the_checks_use_cannot_overlap():
    """The old lists could, and did: Meta's two agents sat one in each group
    while the prose described them the other way round."""
    assert not set(CA.ANSWER_CRAWLERS) & set(CA.TRAINING_CRAWLERS)


def test_every_agent_reaches_exactly_one_of_the_two_groups():
    covered = set(CA.ANSWER_CRAWLERS) | set(CA.TRAINING_CRAWLERS)
    assert covered == {a.token for a in CA.AI_CRAWLER_AGENTS}


def test_every_agent_states_a_purpose():
    for agent in CA.AI_CRAWLER_AGENTS:
        assert len(agent.purpose) > 20, "{} has no purpose sentence".format(agent.token)


def test_a_vendor_documented_agent_names_the_page_that_documents_it():
    """The failure this replaces was a claim with no source. A role asserted as
    the operator's own can only be checked if the URL is there."""
    for agent in CA.AI_CRAWLER_AGENTS:
        if agent.vendor_documented:
            assert agent.source.startswith("https://"), (
                "{} claims vendor documentation but names no page".format(agent.token))
            assert re.match(r"^\d{4}-\d{2}-\d{2}$", agent.checked), agent.token


def test_an_agent_with_no_source_is_marked_as_inferred():
    for agent in CA.AI_CRAWLER_AGENTS:
        if not agent.source:
            assert not agent.vendor_documented, (
                "{} has no source but is marked vendor-documented".format(agent.token))


# --------------------------------------------------------------------------
# The six roles that were wrong, named one at a time
# --------------------------------------------------------------------------

@pytest.mark.parametrize("token,role,why", [
    ("GPTBot", CA.TRAINING,
     "OpenAI: crawls content that may be used in training our foundation models. "
     "OAI-SearchBot and ChatGPT-User are the retrieval-side agents."),
    ("ClaudeBot", CA.TRAINING,
     "Anthropic: collects web content that could contribute to training. "
     "Claude-User and Claude-SearchBot are the retrieval-side agents."),
    ("Amazonbot", CA.TRAINING,
     "Amazon: may be used to train Amazon AI models. Amzn-SearchBot and Amzn-User "
     "are documented as not crawling for generative AI training."),
    ("Bytespider", CA.TRAINING,
     "ByteDance publishes nothing, and the observed behaviour is corpus collection."),
    ("Meta-ExternalAgent", CA.TRAINING,
     "Meta: crawls for use cases such as training foundation AI models."),
    ("Meta-ExternalFetcher", CA.USER_FETCH,
     "Meta: fetches individual links at a user's request. This is the one that was "
     "in the training group while the five above were called answer crawlers."),
])
def test_the_role_matches_the_operators_own_description(token, role, why):
    assert CA.AGENT_BY_TOKEN[token.lower()].role == role, why


def test_the_findings_never_ask_an_owner_to_allow_a_training_crawler():
    """The harm was not the misfiling. It was the instruction that followed
    from it: allow-list these to be cited."""
    for token in ("GPTBot", "ClaudeBot", "Amazonbot", "Bytespider",
                  "Meta-ExternalAgent"):
        assert token not in CA.ANSWER_CRAWLERS


def test_an_opt_out_token_is_never_treated_as_something_that_fetches_pages():
    for token in ("Google-Extended", "Applebot-Extended"):
        assert CA.AGENT_BY_TOKEN[token.lower()].role == CA.CONTROL
        assert token not in CA.ANSWER_CRAWLERS


# --------------------------------------------------------------------------
# The prose is built from the table, so it cannot outrun it
# --------------------------------------------------------------------------

def test_a_named_agent_is_printed_with_its_operator_and_role():
    line = CA.describe_agents(["OAI-SearchBot", "GPTBot"])
    assert "OAI-SearchBot" in line and "OpenAI" in line and "search index" in line
    assert "model training" in line


def test_an_agent_the_table_does_not_know_is_printed_plainly():
    assert CA.describe_agents(["SomeNewBot"]) == "`SomeNewBot`"


def test_the_counterpart_list_names_the_same_operators_answer_agents():
    """Blocking GPTBot is a rights decision. What the owner needs told is which
    OpenAI agents decide citation, so they do not undo the opt-out by mistake."""
    counterparts = CA.answer_side_counterparts(["GPTBot"])
    assert "OAI-SearchBot" in counterparts and "ChatGPT-User" in counterparts
    assert "GPTBot" not in counterparts
    assert all(CA.AGENT_BY_TOKEN[c.lower()].operator == "OpenAI" for c in counterparts)


def test_counterparts_of_an_unknown_token_are_empty_not_everything():
    assert CA.answer_side_counterparts(["SomeNewBot"]) == []


def test_an_inferred_role_is_flagged_and_a_documented_one_is_not():
    assert CA.undocumented_among(["Bytespider", "GPTBot"]) == ["Bytespider"]


# --------------------------------------------------------------------------
# The reference file is generated, so it cannot drift
# --------------------------------------------------------------------------

BEGIN = "<!-- BEGIN GENERATED: agent table, rendered from AI_CRAWLER_AGENTS in check.py -->"
END = "<!-- END GENERATED -->"

ROLE_SECTIONS = [
    ("SEARCH", "Search index agents",
     "These index pages so the brand can be surfaced and linked in an answer. "
     "Blocking one costs citations."),
    ("USER_FETCH", "Live-fetch agents",
     "These fetch one page because a person just asked a question about it. "
     "Blocking one costs that answer."),
    ("TRAINING", "Training crawlers",
     "These collect corpora for model training. Blocking is a rights decision plenty "
     "of publishers make on purpose, and this audit reports it at `info` severity, "
     "never as a defect."),
    ("CONTROL", "Opt-out tokens, which are not crawlers",
     "These fetch nothing. The token exists only so a site can express a training "
     "opt-out, so blocking one cannot cost a citation."),
]


def render_agent_tables():
    """The reference file's agent tables, rendered from the Python table.

    The old reference file was maintained by hand beside the code and had Meta's
    two crawlers reversed relative to it. Generating it removes the possibility.
    """
    out = [BEGIN, ""]
    for attribute, heading, blurb in ROLE_SECTIONS:
        role = getattr(CA, attribute)
        rows = [a for a in CA.AI_CRAWLER_AGENTS if a.role == role]
        out += ["## {} ({})".format(heading, len(rows)), "", blurb, "",
                "| User agent | Operator | What the operator says it does | Source |",
                "|---|---|---|---|"]
        for agent in rows:
            source = ("[operator docs]({}), read {}".format(agent.source, agent.checked)
                      if agent.source
                      else "**no published statement** - role inferred from behaviour")
            out.append("| `{}` | {} | {} | {} |".format(
                agent.token, agent.operator, agent.purpose, source))
        out.append("")
    out.append(END)
    return "\n".join(out)


def _generated_region():
    text = io.open(REFERENCE, encoding="utf-8").read()
    assert BEGIN in text and END in text, "the reference file lost its generated markers"
    return text[text.index(BEGIN):text.index(END) + len(END)]


def test_the_reference_file_says_exactly_what_the_code_says():
    assert _generated_region() == render_agent_tables(), (
        "skills/crawl-access-audit/references/ai-crawler-user-agents.md is out of date "
        "with AI_CRAWLER_AGENTS. Regenerate the region between the two markers with "
        "tests/test_crawler_roles.py:render_agent_tables().")


def test_the_reference_file_names_every_agent_the_code_checks():
    region = _generated_region()
    for agent in CA.AI_CRAWLER_AGENTS:
        assert "`{}`".format(agent.token) in region, agent.token


# --------------------------------------------------------------------------
# No crawler name may be typed into a sentence
# --------------------------------------------------------------------------

def test_no_skill_writes_a_crawler_name_into_a_string():
    """A crawler name in a string literal is a claim nothing checks.

    The robots.txt findings were rebuilt from the agent table and got the
    distinction right. Forty lines away, a second finding - the one that fires
    when a bot manager serves a challenge page instead of the page - still had
    "OAI-SearchBot, PerplexityBot, ClaudeBot and Google-Extended" typed into a
    remediation step, described as crawlers that "fetch a page to answer a
    question". Two verification agents found it independently on two different
    sites, and it survived the fix that was supposed to remove exactly that
    sentence, because nothing connected the string to the data.

    Now something does. Every crawler name in the report is rendered from the
    table; a literal one fails here.
    """
    tokens = [agent.token for agent in CA.AI_CRAWLER_AGENTS]
    offenders = []
    for path in sorted(glob.glob(os.path.join(ROOT, "skills", "*", "scripts", "*.py"))):
        source = io.open(path, encoding="utf-8").read()
        tree = ast.parse(source)

        # The table itself may say the names, and so may a docstring: a
        # docstring explains the code to whoever maintains it and never
        # reaches a report.
        exempt = []
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id in
                            ("AI_CRAWLER_AGENTS", "_ROLE_PHRASE")
                            for t in node.targets)):
                exempt.append((node.lineno, node.end_lineno))
        docstrings = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                 ast.AsyncFunctionDef)):
                body = getattr(node, "body", None) or []
                first = body[0] if body else None
                if (isinstance(first, ast.Expr)
                        and isinstance(first.value, ast.Constant)
                        and isinstance(first.value.value, str)):
                    docstrings.add(id(first.value))

        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
                continue
            if id(node) in docstrings:
                continue
            if any(low <= node.lineno <= high for low, high in exempt):
                continue
            for token in tokens:
                if token in node.value:
                    offenders.append("{}:{}: {!r} names {}".format(
                        os.path.relpath(path, ROOT), node.lineno,
                        node.value[:70], token))
    assert not offenders, (
        "crawler names must be rendered from AI_CRAWLER_AGENTS, never typed into a "
        "string:\n" + "\n".join(sorted(set(offenders))))


def test_a_truncated_agent_list_says_how_many_it_left_out():
    """A broadcaster blocks ten training crawlers. The evidence named eight and
    stopped, so an owner working through the list would have left two they
    never saw. It read as the whole list because nothing said otherwise."""
    ten = [agent.token for agent in CA.AI_CRAWLER_AGENTS
           if agent.role == CA.TRAINING][:10]
    assert len(ten) == 10
    assert CA.describe_agents(ten).endswith("and 2 more")


def test_a_complete_agent_list_says_nothing_extra():
    assert "more" not in CA.describe_agents(["GPTBot", "ClaudeBot"])
