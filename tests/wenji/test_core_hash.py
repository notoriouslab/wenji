"""Tests for wenji.core.hash."""

from __future__ import annotations

from wenji.core.hash import HASH_LENGTH, content_hash


def test_deterministic():
    assert content_hash("hello") == content_hash("hello")


def test_length():
    assert len(content_hash("anything")) == HASH_LENGTH
    assert len(content_hash("")) == HASH_LENGTH


def test_unicode_distinct():
    assert content_hash("中文") != content_hash("英文")


def test_empty_distinct_from_space():
    assert content_hash("") != content_hash(" ")


def test_known_value():
    # SHA256("hello") = 2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824
    assert content_hash("hello") == "2cf24dba5fb0a30e"
