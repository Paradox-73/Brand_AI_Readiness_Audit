"""Where the Organization snippet's identity values are allowed to come from.

The snippet is pasted into a site-wide template and then read as the company's
identity by every machine that visits. Two values in it were real strings,
published on the audited site, and about something other than the company:

  description  the homepage's top news item - a point release announcement -
               published as what the organisation *is*
  logo         another page's og:image, a photograph of an event, published as
               the mark a knowledge panel renders beside the company's name

Both are the same class: a value that comes from no JSON-LD node, so
`speaks_for_the_brand` never sees it and nothing else asked whose value it was.
Every case here is that class, on a made-up site.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys

from conftest import ROOT, SCRIPTS

sys.path.insert(0, SCRIPTS)


def _module(skill):
    path = os.path.join(ROOT, "skills", skill, "scripts", "check.py")
    spec = importlib.util.spec_from_file_location("identity_" + skill.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SD = _module("structured-data-audit")
FACTS = _module("fact-extractability-audit")

SITE = "https://example.test"
BRAND = {"name": "Example"}


def _page(path, page_type="other", **extra):
    """A snapshot page record with only the keys these functions read."""
    page = {
        "url": SITE + path,
        "page_type": page_type,
        "status": 200,
        "title": "",
        "headings": {},
        "paragraphs": [],
        "sections": [],
        "links": {"internal": []},
        "jsonld": [],
        "jsonld_types": [],
        "og": {},
    }
    page.update(extra)
    return page


def _snapshot(pages):
    return {"origin": SITE, "site": "example.test", "pages": pages,
            "brand": dict(BRAND)}


def _payload(pages, brand=None, node=None):
    """The JSON object out of the paste-ready Organization block."""
    snapshot = _snapshot(pages)
    snippet = SD._org_snippet(snapshot, pages, brand or dict(BRAND), node=node)
    return json.loads(snippet.split(">\n", 1)[1].rsplit("\n</script>", 1)[0])


# --------------------------------------------------------------------------
# The description is the sentence that says what the brand is
# --------------------------------------------------------------------------

# The homepage's top news item, and the about page's opening line. Both name
# the brand; both contain the word "is"; only one of them is an identity.
NEWS_ITEM = 'A new minor release, Example 8.1 "Hoare", is now available for download.'
DEFINITION = ("Example is the leading multimedia framework, able to decode, encode, "
              "transcode, mux, demux, stream, filter and play pretty much anything "
              "that humans and machines have created.")


def _project_site():
    return [
        _page("/", "home", paragraphs=[NEWS_ITEM]),
        _page("/about", "about", paragraphs=[DEFINITION]),
    ]


def test_a_release_announcement_is_not_the_organisations_identity():
    """The block told every assistant that the organisation *is* a point
    release. The rule it passed was "a 40-to-300-character sentence holding the
    brand name and the word is", which a news item satisfies and which goes
    stale the week the next release ships."""
    definition = SD._brand_definition(_project_site(), dict(BRAND))
    assert "8.1" not in definition
    assert "leading multimedia framework" in definition


def test_the_snippet_and_the_report_now_answer_with_the_same_sentence():
    """`fact-extractability-audit` prints the definition it found and this
    skill publishes the one it found, and the two used to disagree inside one
    report. Read by both rules, the two sentences on this site have to sort the
    same way round."""
    text = " ".join([NEWS_ITEM, DEFINITION])
    theirs = FACTS._find_definition(text, "Example", dict(BRAND))
    ours = SD._defining_sentence(text, "Example", dict(BRAND))
    assert theirs and ours
    assert "8.1" not in theirs and "8.1" not in ours
    assert ours.startswith("Example is the leading multimedia framework")


def test_a_sentence_that_only_mentions_the_brand_is_not_a_definition():
    """"Billing for Example is monthly per organisation." names the brand and
    carries a copula, and the old rule quoted it as the site's definition of
    itself - a claim about invoicing published as a claim about identity."""
    pages = [_page("/", "home",
                   paragraphs=["Billing for Example is monthly per organisation."])]
    assert SD._brand_definition(pages, dict(BRAND)) == ""


def test_a_sentence_naming_no_category_is_not_a_definition():
    """"Example is a leading global company" has the shape of a definition and
    tells a reader nothing, so publishing it as the description spends the one
    field an assistant reads for what this business does."""
    pages = [_page("/", "home",
                   paragraphs=["Example is a leading global company."])]
    assert SD._brand_definition(pages, dict(BRAND)) == ""


def test_a_site_that_writes_in_the_first_person_still_has_a_definition():
    """A shop writes "We are a bakery on Fossgate" and never names itself as
    the subject of a sentence. That is an identity as plainly as any other
    shape, and no pattern requiring the brand string can match it."""
    pages = [_page("/", "home",
                   paragraphs=["We are a bakery and coffee roastery on the market square."])]
    assert "bakery" in SD._brand_definition(pages, dict(BRAND))


def test_a_definition_carrying_a_version_number_is_skipped_for_a_lasting_one():
    """"Example 3.0 is a major new release" passes every test of the shared
    rule: the brand opens the sentence, the predicate names a category, it
    reads as prose. It is still news, and a description pasted into a site-wide
    template outlives the release it describes."""
    pages = [_page("/", "home", paragraphs=[
        "Example 3.0 is a major new release of the toolkit.",
        "Example is a typesetting system for scientific documents.",
    ])]
    assert SD._brand_definition(pages, dict(BRAND)) == \
        "Example is a typesetting system for scientific documents"


def test_a_founding_year_is_not_news():
    """The test is whether the sentence is pinned to a moment, not whether it
    holds a number. "Since 1934" names a founding, which is as true next year
    as this one, and a rule that rejected every four-digit year would throw
    away the most identity-bearing sentence a long-lived business writes."""
    pages = [_page("/", "home", paragraphs=[
        "Example is an independent record office for the county since 1934."])]
    assert "since 1934" in SD._brand_definition(pages, dict(BRAND))


def test_a_news_item_in_the_meta_description_is_not_published_either():
    """The defining sentence is not the only way a news item reaches the
    block: a great many templates write the top story into the homepage's own
    meta description, and that source is read first."""
    pages = [
        _page("/", "home", meta_description=NEWS_ITEM),
        _page("/about", "about", paragraphs=[DEFINITION]),
    ]
    assert "8.1" not in _payload(pages)["description"]


def test_a_description_the_site_declared_itself_is_never_dropped():
    """A `description` on the site's own Organization block is the site's
    deliberate statement of who it is. Judging that one by our news rule would
    delete real data from the block the owner is told to paste, which is the
    mistake this snippet makes in the other direction."""
    home = _page("/", "home")
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "description": NEWS_ITEM}]
    home["jsonld_types"] = ["Organization"]
    assert _payload([home])["description"] == NEWS_ITEM


# --------------------------------------------------------------------------
# The logo is the mark the site presents as its own
# --------------------------------------------------------------------------

EVENT_CARD = SITE + "/sites/default/files/images/event/talk.jpg"
HOME_CARD = SITE + "/sites/default/files/site-logo.png"


def _museum_site():
    """A homepage whose own social card is the site's mark, and an events page
    whose social card is a photograph of a talk."""
    return [
        _page("/", "home", og={"og:image": HOME_CARD}),
        _page("/events", "other", og={"og:image": EVENT_CARD}),
    ]


def test_another_pages_social_card_is_never_the_brands_logo():
    """The block published a 2.1 MB photograph of an event as the logo. An
    og:image is the picture *that page* wants shown when it is shared, so on
    any page but the front door it is a picture of the story rather than of the
    publisher.

    Both orders, because the reader that used to sit here took the first
    og:image in the list and the list's order is not a property of the site.
    Pages are sorted by the URL the crawl queued, so an inner page comes first
    whenever the homepage was queued under a different host spelling than the
    rest of the crawl - a bare address that redirects to the `www.` one, which
    is a great many sites."""
    for pages in (_museum_site(), list(reversed(_museum_site()))):
        assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) == HOME_CARD
        assert _payload(pages)["logo"] == HOME_CARD


def test_the_events_card_loses_even_when_the_homepage_offers_nothing():
    """With the homepage silent there is no logo on this site, and the honest
    answer is the placeholder. `logo` is what a knowledge panel renders beside
    the company's name, so a wrong URL here is visible to every user of every
    assistant that reads the markup - and a placeholder costs the owner a
    minute."""
    pages = [_page("/", "home"), _page("/events", "other", og={"og:image": EVENT_CARD})]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) is None
    logo = _payload(pages)["logo"]
    assert EVENT_CARD not in logo
    assert logo.startswith("<") and "fill this in" in logo


def test_a_logo_the_site_declared_beats_every_reading_of_the_pages():
    """The site saying "this is my logo" in its own markup is the strongest
    evidence there is, and an `ImageObject` is how schema.org lets it say so."""
    home = _page("/", "home", og={"og:image": HOME_CARD})
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "logo": {"@type": "ImageObject",
                                "name": "Logo",
                                "url": SITE + "/brand/mark.svg"}}]
    home["jsonld_types"] = ["Organization"]
    pages = [home]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) == SITE + "/brand/mark.svg"
    assert _payload(pages)["logo"] == SITE + "/brand/mark.svg"


def test_a_logo_declared_on_somebody_elses_node_is_not_the_brands():
    """An Organization lifted out of an Event's `performer` is a claim about a
    band. Every value on it - its name, its URL, its logo - would go into a
    block a developer is told to paste as the audited company's identity."""
    home = _page("/", "home")
    home["jsonld"] = [
        {"@type": "Event", "name": "A Quartet Live"},
        {"@type": "Organization", "name": "A Quartet",
         "logo": SITE + "/bands/quartet-logo.png",
         "_nested_in": "performer", "_parent_name": "A Quartet Live"},
    ]
    home["jsonld_types"] = ["Event", "Organization"]
    pages = [home]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) is None


def test_an_image_the_template_calls_a_logo_is_read_only_when_it_is_site_wide():
    """A filename saying logo is weak evidence on its own: a partner strip and
    a diagram inside one article both carry them. A mark the template renders
    is on every page, which is what separates the two."""
    mark = SITE + "/theme/example-logo.svg"
    partner = SITE + "/uploads/partner-logo.png"
    pages = [
        _page("/", "home", images={"undescribed_sample": [mark, partner]}),
        _page("/about", "about", images={"undescribed_sample": [mark]}),
    ]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) == mark


def test_a_url_that_only_says_logout_is_not_a_logo():
    """"logo" as a substring matches `/account/logout`, and the snippet would
    then publish a sign-out link as the brand's mark."""
    pages = [_page("/", "home",
                   images={"undescribed_sample": [SITE + "/account/logout-icon.png"]}),
             _page("/about", "about",
                   images={"undescribed_sample": [SITE + "/account/logout-icon.png"]})]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) is None


def test_a_relative_logo_path_the_site_published_still_survives():
    """The site's own value wins wherever it published one, and plenty of
    templates write `logo` as a path rather than an absolute address. Replacing
    a real value with our reading of the site is the regression this snippet
    was already fixed for once."""
    home = _page("/", "home", og={"og:image": HOME_CARD})
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "logo": "/theme/mark.png"}]
    home["jsonld_types"] = ["Organization"]
    assert _payload([home])["logo"] == "/theme/mark.png"


def test_an_image_object_carrying_only_a_caption_is_not_an_image():
    """`_named` reads `name` before `url`, so an `ImageObject` written
    `{"name": "Logo"}` came back as the word "Logo" - a caption pasted into the
    field a knowledge panel fetches a picture from."""
    home = _page("/", "home")
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "logo": {"@type": "ImageObject", "name": "Logo"}}]
    home["jsonld_types"] = ["Organization"]
    logo = _payload([home])["logo"]
    assert logo.startswith("<") and "fill this in" in logo


def test_the_image_summary_is_a_dict_and_the_reader_knows_it():
    """`page["images"]` is the summary `page_extract` writes, not a list of
    image elements. The reader that used to sit here iterated it, compared its
    own key names against the word logo, and could never match - so the
    og:image branch was the only one that ever answered."""
    pages = [_page("/", "home", images={"count": 4, "missing_alt_count": 1,
                                        "undescribed_sample": []})]
    assert SD._site_logo(_snapshot(pages), pages, dict(BRAND)) is None


# --------------------------------------------------------------------------
# The rest of the identity block
# --------------------------------------------------------------------------

def test_the_url_published_as_the_brands_home_is_this_site():
    """`url` says "this is my home on the web", and a node that speaks for the
    brand can still carry somebody else's address - a parent group's site, a
    directory listing, a campaign microsite."""
    home = _page("/", "home")
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "url": "https://parent-group.test/brands/example"}]
    home["jsonld_types"] = ["Organization"]
    assert _payload([home])["url"] == SITE + "/"


def test_the_sites_own_url_survives_when_it_names_this_site():
    home = _page("/", "home")
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "url": "https://www.example.test/"}]
    home["jsonld_types"] = ["Organization"]
    assert _payload([home])["url"] == "https://www.example.test/"


def test_a_phone_number_on_a_supplier_page_is_not_the_companys():
    """A `tel:` link is declared markup, which is why it is trusted at all, but
    nothing in it says whose line it is - the same hole as a logo taken from
    another page's social card. The number a visitor would call is in the
    template on the home, about and contact pages anyway."""
    pages = [
        _page("/", "home", contact_facts={"declared_phones": []}),
        _page("/suppliers", "other",
              contact_facts={"declared_phones": ["+44 20 7946 0100"]}),
    ]
    assert "telephone" not in SD._contact_facts_for_snippet(pages)
    assert "telephone" not in _payload(pages)


def test_the_companys_own_number_on_its_contact_page_still_reaches_the_block():
    pages = [
        _page("/", "home", contact_facts={"declared_phones": []}),
        _page("/contact", "contact",
              contact_facts={"declared_phones": ["+44 20 7946 0100"]}),
    ]
    assert _payload(pages)["telephone"] == "+44 20 7946 0100"


def test_every_value_in_the_block_is_the_brands_own():
    """The whole snippet at once, on a site that publishes one of each mistake:
    a news item on the homepage, an event photograph on an inner page, a
    supplier's phone number, and a parent group's address bar."""
    home = _page("/", "home", paragraphs=[NEWS_ITEM], og={"og:image": HOME_CARD},
                 contact_facts={"declared_phones": []})
    home["jsonld"] = [{"@type": "Organization", "name": "Example",
                       "url": "https://parent-group.test/brands/example"}]
    home["jsonld_types"] = ["Organization"]
    pages = [
        home,
        _page("/about", "about", paragraphs=[DEFINITION]),
        _page("/events", "other", og={"og:image": EVENT_CARD}),
        _page("/suppliers", "other",
              contact_facts={"declared_phones": ["+44 20 7946 0100"]}),
    ]
    payload = _payload(pages)
    assert payload["url"] == SITE + "/"
    assert payload["logo"] == HOME_CARD
    assert payload["description"].startswith("Example is the leading multimedia framework")
    assert "telephone" not in payload
    blob = json.dumps(payload)
    for wrong in (EVENT_CARD, "8.1", "7946 0100", "parent-group.test"):
        assert wrong not in blob, "{} reached the paste-ready block".format(wrong)
