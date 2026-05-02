"""Tests for wenji.core.errors."""

from __future__ import annotations

import pytest

from wenji.core.errors import (
    ClassifyError,
    ConfigError,
    IngestError,
    SchemaError,
    SearchError,
    WenjiError,
)


@pytest.mark.parametrize(
    "exc_cls",
    [IngestError, SchemaError, ConfigError, ClassifyError, SearchError],
)
def test_subclass_of_wenji_error(exc_cls):
    assert issubclass(exc_cls, WenjiError)


def test_can_raise_and_catch_via_base():
    with pytest.raises(WenjiError):
        raise IngestError("frontmatter parse failed")


def test_message_preserved():
    err = SchemaError("version mismatch")
    assert str(err) == "version mismatch"
