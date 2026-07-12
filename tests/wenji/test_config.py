"""Tests for wenji.config loader + defaults."""

from __future__ import annotations

import pytest

from wenji.config import (
    DEFAULT_SEARCH_CONFIG,
    SearchConfig,
    WenjiConfig,
    load_config,
)
from wenji.core.errors import ConfigError


def write_yaml(p, body: str):
    p.write_text(body, encoding="utf-8")
    return p


def test_load_none_returns_defaults():
    cfg = load_config(None)
    assert isinstance(cfg, WenjiConfig)
    assert cfg.directory_map == {}
    assert cfg.chunk_strategies == {}
    assert cfg.search.alpha == DEFAULT_SEARCH_CONFIG["alpha"]


def test_load_full_yaml(tmp_path):
    cfg_path = write_yaml(
        tmp_path / "wenji.yaml",
        """
directory_map:
  sermons: sermon
  articles: article

chunk_strategies:
  sermon:
    strategy: paragraph
    min_chars: 200
    max_chars: 1500

search:
  alpha: 0.4
  candidate_pool: 30
  rerank:
    enabled: true
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.directory_map == {"sermons": "sermon", "articles": "article"}
    assert cfg.chunk_strategies["sermon"]["max_chars"] == 1500
    assert cfg.search.alpha == 0.4
    assert cfg.search.candidate_pool == 30
    assert cfg.search.rerank.enabled is True


def test_partial_search_config_merges_defaults(tmp_path):
    cfg_path = write_yaml(tmp_path / "w.yaml", "search:\n  alpha: 0.6\n")
    cfg = load_config(cfg_path)
    assert cfg.search.alpha == 0.6
    # other defaults preserved
    assert cfg.search.candidate_pool == 50


def test_alpha_out_of_range_raises(tmp_path):
    cfg_path = write_yaml(tmp_path / "w.yaml", "search:\n  alpha: 1.5\n")
    with pytest.raises(ConfigError, match="alpha"):
        load_config(cfg_path)


def test_directory_map_must_be_mapping(tmp_path):
    cfg_path = write_yaml(tmp_path / "w.yaml", "directory_map: [a, b]\n")
    with pytest.raises(ConfigError, match="directory_map"):
        load_config(cfg_path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.yaml")


def test_invalid_yaml_raises(tmp_path):
    cfg_path = tmp_path / "bad.yaml"
    cfg_path.write_text("directory_map: [unclosed", encoding="utf-8")
    with pytest.raises(ConfigError, match="YAML parse"):
        load_config(cfg_path)


def test_empty_yaml_returns_defaults(tmp_path):
    cfg_path = write_yaml(tmp_path / "w.yaml", "")
    cfg = load_config(cfg_path)
    assert cfg.search.alpha == 0.25


def test_search_config_defaults_match_module():
    sc = SearchConfig()
    assert sc.alpha == 0.25
    assert sc.candidate_pool == 50
    assert sc.rerank.enabled is False
