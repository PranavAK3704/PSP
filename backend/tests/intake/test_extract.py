"""Stage 4. EVERY ASSERTION HERE IS A TOKEN SET, NEVER A MATCH COUNT.

That is not a style preference. Every regex error in this project produced a plausible COUNT
and was caught only by printing the tokens — the (?i) leak that made "DC landing time" yield
LAN scored 18 hits where 10 were expected, and looked like good recall until someone looked.
An assertion of `len(got) == 3` would have passed for all four of the bugs below.
"""
from __future__ import annotations

import pytest

from app.engine.algo.entities import (
    dc_tier_b_candidates, extract, extract_dc_tier_a, extract_dc_tier_b,
)
from app.intake import extract as istage

REGISTRY = istage.load_code_list(istage.DC_CODES)
DENYLIST = istage.load_code_list(istage.DC_DENYLIST)


def tier_a(text):
    return set(extract_dc_tier_a(text, DENYLIST))


def tier_b(text, registry=None):
    reg = REGISTRY if registry is None else registry
    return set(extract_dc_tier_b(text, reg, DENYLIST,
                                 exclude=set(extract_dc_tier_a(text, DENYLIST))))


# ── Tier A: label-anchored, no registry ─────────────────────────────────────────────────────
@pytest.mark.parametrize("text,expected", [
    # the three real shapes from the channels — code after, before, and a bare list
    ("DC Code: NXG",                             {"NXG"}),
    ("The *NQS DC landing time",                 {"NQS"}),
    ("HY9, IAM, VN4 hub not recive",             {"HY9", "IAM", "VN4"}),
    # every label form
    ("LMDC PJ2/PJR",                             {"PJ2", "PJR"}),
    ("FMH MFC issue",                            {"MFC"}),
    ("Hubs CKH and RW3",                         {"CKH", "RW3"}),
    ("DC Codes:\nNQS\nIQU\nUB1",                 {"NQS", "IQU", "UB1"}),
    # Slack emphasis as a separator — IQU and UB1 were missed in the real data purely because
    # their authors placed the bold marker differently
    ("*IQU* DC issue",                           {"IQU"}),
    ("DC _UB1_ pending",                         {"UB1"}),
    ("DC ~PJ2~ and `J93`",                       {"PJ2", "J93"}),
    # a digit in the MIDDLE — invisible to the old [A-Z]{2,4}\d{0,4} class
    ("hub L9D and J93 down",                     {"L9D", "J93"}),
    ("DC R2F, K6L & T5X",                        {"R2F", "K6L", "T5X"}),
])
def test_tier_a_extracts_exactly(text, expected):
    assert tier_a(text) == expected


@pytest.mark.parametrize("text", [
    "DC landing time is late",     # THE (?i) BUG: a case-insensitive token class yields LAN
    "dc landing time",
    "DC CODE IS MISSING",          # _NOT_AN_ID: the capture group would otherwise yield CODE
    "DC code 3531 issue",          # 353 must not match inside 3531
    "hub NQSX down",               # 4 chars is not a 3-char code
    "DC AB1234 issue",
    "Sir, FYI, DC issue",          # no token adjacent to the label
    "342 Tids are coming in Hardstop loss",      # no label at all -> Tier A is silent
])
def test_tier_a_extracts_nothing(text):
    assert tier_a(text) == set()


def test_tier_a_case_sensitivity_is_the_whole_game():
    """The token class must stay case-sensitive while the LABEL is not. Scoping (?i) to the
    label alone is what separates these two lines."""
    assert tier_a("dc code: NXG") == {"NXG"}        # lowercase label still anchors
    assert tier_a("DC code: nxg") == set()          # lowercase token is not a code


def test_tier_a_needs_no_registry():
    assert set(extract_dc_tier_a("DC Code: ZZZ", DENYLIST)) == {"ZZZ"}
    assert "ZZZ" not in REGISTRY


# ── Tier B: bare tokens, registry REQUIRED ──────────────────────────────────────────────────
def test_tier_b_finds_bare_registry_codes():
    assert tier_b("MX1 captain panel not working") == {"MX1"}      # the cross-post dedupe case
    assert tier_b("R2F K6L pending") >= {"K6L"}


def test_342_is_rejected_and_only_the_registry_rejects_it():
    """The canonical case. 342 is word-bounded, exactly three characters and digit-bearing, so
    a digit-bearing heuristic extracts it with confidence. Nothing about its SHAPE is wrong."""
    text = "342 Tids are coming in Hardstop loss"
    assert tier_b(text) == set()
    assert "342" not in REGISTRY
    # and the shape-based heuristic really would have taken it — this is what we avoided
    assert "342" in dc_tier_b_candidates(text, DENYLIST)


def test_a_digit_bearing_heuristic_is_not_a_registry_substitute():
    """897 IS a real hub code in the registry, so membership alone would accept '897 Tids'.
    The purely-numeric denylist entry is what separates it from 342 — and the fact that both
    sentences are identical in shape is exactly why shape cannot be the test."""
    assert "897" in REGISTRY
    assert tier_b("897 Tids are coming in Hardstop loss") == set()


@pytest.mark.parametrize("token,text", [
    ("SIR", "SIR PLEASE CHECK"),
    ("RVP", "RVP pending"),
    ("TID", "TID count is high"),
])
def test_registry_contaminants_are_denied(token, text):
    """These are all in the registry as GENUINE hub codes. Registry membership is therefore
    not sufficient evidence, which is where the brief's Tier B premise was half right.
    entities.py had already measured this: OLD and SIR once produced 16.7% pure noise."""
    assert token in REGISTRY, "precondition: the contaminant really is in the registry"
    assert token in DENYLIST
    assert tier_b(text) == set()


def test_tier_b_is_skipped_entirely_without_a_registry():
    """No silent fallback. An empty registry means no Tier B, not a bare-token pass."""
    assert extract_dc_tier_b("MX1 captain panel not working", set(), DENYLIST) == {}
    assert extract_dc_tier_b("342 Tids are coming in Hardstop loss", set(), DENYLIST) == {}


def test_tier_a_hits_are_not_double_counted_in_tier_b():
    text = "DC Code: MX1"
    assert tier_a(text) == {"MX1"}
    assert tier_b(text) == set()


# ── the other identifiers ───────────────────────────────────────────────────────────────────
FIXTURE_LINE = ("Registered mobile 9900000001, ticket 4788325630026, "
                "waybill VL0084870753799.")


@pytest.mark.parametrize("kind,expected", [
    ("mobile",     {"9900000001"}),
    ("kapture_id", {"4788325630026"}),
    ("waybill",    {"VL0084870753799"}),
])
def test_numeric_ids_do_not_match_inside_each_other(kind, expected):
    """These are distinguished by EXACT LENGTH, so an unguarded pattern finds one inside
    another: a bare [6-9]\\d{9} finds a 'mobile' (7883256300) inside the 13-digit Kapture id,
    and a bare \\d{12,13} finds a 'Kapture id' inside the waybill. Both produce a plausible
    count. The alphanumeric lookarounds in config/entities.yaml are what make them exclusive."""
    pats = istage.load_patterns()
    got = {v for k, v, _, _ in istage.extract_text(FIXTURE_LINE, pats, set(), set())
           if k == kind}
    assert got == expected


def test_pilot_id_requires_a_cue():
    """A bare 8-digit run is a date or an amount far more often than a pilot."""
    pats = istage.load_patterns()

    def got(t):
        return {v for k, v, _, _ in istage.extract_text(t, pats, set(), set()) if k == "pilot_id"}

    assert got("pilot id 12345678 not paid") == {"12345678"}
    assert got("FE 87654321 inactive") == {"87654321"}
    assert got("order 12345678 delayed") == set()


def test_tel_markup_is_not_required_for_a_mobile():
    """<tel:> wrapping exists but is rare — 4 of 467 records — so bare digits are the case
    that must work."""
    pats = istage.load_patterns()

    def got(t):
        return {v for k, v, _, _ in istage.extract_text(t, pats, set(), set()) if k == "mobile"}

    assert got("call 9900000001") == {"9900000001"}
    assert got("<tel:9900000001|9900000001>") == {"9900000001"}


# ── the live path must not have moved ───────────────────────────────────────────────────────
def test_the_live_conversation_path_is_unchanged():
    """entities.extract() serves the live money path. The intake tiers were ADDED beside it,
    and _ok_id's new kwarg defaults to the old behaviour, so these must still hold."""
    assert extract("hub code is missing")["hub_codes"] == []        # CODE still rejected
    assert extract("DC: BLR07 not working")["hub_codes"] == ["BLR07"]
    assert extract("hub NQS")["hub_codes"] == []                    # digit rule still applies
    assert extract("DC landing time is late")["hub_codes"] == []
