"""
Regression test for the timeline crash on entity-match `reasons`.

A case with any entity match 500'd the timeline (and SAR generation, which
builds the timeline) with `TypeError: unhashable type: 'slice'`. The cause: the
resolution pipeline stores `reasons` as a structured object
`{summary, positive, negative, not_applicable}`, but the timeline sliced it as
if it were a bare list -- `reasons[:3]` on a dict. Found by clicking Timeline on
a real case, not by a unit test; this is that missing unit test.
"""

from __future__ import annotations

import json

from app.casework.timeline import _reasons_summary


def test_object_shaped_reasons_uses_the_summary_field():
    # The exact shape the resolution pipeline writes today.
    raw = json.dumps(
        {
            "summary": "Confidence 0/100 -> AUTO_REJECTED.",
            "positive": ["Name similarity 40/100"],
            "negative": ["below the 70/100 floor"],
            "not_applicable": ["country absent"],
        }
    )
    # Must not raise (the bug was a TypeError here), and must return the summary.
    assert _reasons_summary(raw) == "Confidence 0/100 -> AUTO_REJECTED."


def test_object_without_summary_falls_back_to_reasons():
    raw = json.dumps({"positive": ["p1", "p2"], "negative": ["n1"]})
    assert _reasons_summary(raw) == "p1; p2; n1"


def test_legacy_list_shape_still_works():
    # Older matches stored a bare list; that path must keep working.
    assert _reasons_summary(json.dumps(["r1", "r2", "r3", "r4"])) == "r1; r2; r3"


def test_empty_and_malformed_are_none_not_crashes():
    assert _reasons_summary(None) is None
    assert _reasons_summary("") is None
    assert _reasons_summary("{not json") is None
    assert _reasons_summary(json.dumps({})) is None
