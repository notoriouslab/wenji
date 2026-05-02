"""Tests for wenji.classify.rules."""

from __future__ import annotations

import json
import re

from wenji.classify.axes_loader import Rule
from wenji.classify.rules import Article, _parse_tags, rule_matches


def _rule(**overrides) -> Rule:
    base = {
        "source_type": "sermon",
        "primary": True,
        "title_regex": None,
        "subtype": None,
        "tag": None,
        "retag_source_type_to": None,
        "_compiled_regex": None,
    }
    base.update(overrides)
    return Rule(**base)


def _article(**overrides) -> Article:
    base = {
        "article_id": "a1",
        "source_type": "sermon",
        "subtype": None,
        "title": "Test",
        "tags_json": None,
    }
    base.update(overrides)
    return Article(**base)


def test_source_type_must_match():
    assert rule_matches(_rule(source_type="sermon"), _article(source_type="sermon"))
    assert not rule_matches(_rule(source_type="sermon"), _article(source_type="other"))


def test_subtype_optional_match():
    r = _rule(subtype="weekly")
    assert rule_matches(r, _article(subtype="weekly"))
    assert not rule_matches(r, _article(subtype="quarterly"))
    assert not rule_matches(r, _article(subtype=None))


def test_title_regex_optional():
    pattern = re.compile(r"^第\d+章")
    r = _rule(title_regex="^第\\d+章", _compiled_regex=pattern)
    assert rule_matches(r, _article(title="第3章 引言"))
    assert not rule_matches(r, _article(title="第三章"))
    assert not rule_matches(r, _article(title=None))


def test_tag_match_against_json_list():
    r = _rule(tag="禱告")
    a = _article(tags_json=json.dumps(["禱告", "信心"]))
    assert rule_matches(r, a)
    a_no = _article(tags_json=json.dumps(["其他"]))
    assert not rule_matches(r, a_no)


def test_tag_match_with_empty_tags():
    r = _rule(tag="禱告")
    assert not rule_matches(r, _article(tags_json=None))
    assert not rule_matches(r, _article(tags_json="[]"))


def test_all_fields_combine_with_and():
    pattern = re.compile(r"恩典")
    r = _rule(
        subtype="weekly",
        title_regex=r"恩典",
        _compiled_regex=pattern,
        tag="theology",
    )
    a_full = _article(
        subtype="weekly",
        title="論恩典",
        tags_json=json.dumps(["theology"]),
    )
    assert rule_matches(r, a_full)
    a_partial = _article(
        subtype="weekly",
        title="論恩典",
        tags_json=json.dumps(["other"]),
    )
    assert not rule_matches(r, a_partial)


def test_parse_tags_handles_invalid_json():
    assert _parse_tags("not json") == []
    assert _parse_tags(None) == []
    assert _parse_tags("") == []


def test_parse_tags_handles_string_value():
    assert _parse_tags('"single"') == ["single"]


def test_parse_tags_list_of_non_strings_coerced():
    assert _parse_tags("[1, 2]") == ["1", "2"]
