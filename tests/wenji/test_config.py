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
""",
    )
    cfg = load_config(cfg_path)
    assert cfg.directory_map == {"sermons": "sermon", "articles": "article"}
    assert cfg.chunk_strategies["sermon"]["max_chars"] == 1500
    assert cfg.search.alpha == 0.4
    assert cfg.search.candidate_pool == 30


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


# ---- web section (0.5.2) ----


def test_web_defaults_when_unset(tmp_path):
    cfg = load_config(None)
    assert cfg.web.hero_title == "UNCOVER DEEPER TRUTH."
    assert cfg.web.hero_subtitle is None
    assert "屬靈操練" in cfg.web.search_placeholder
    assert len(cfg.web.topic_shortcuts) == 2
    assert cfg.web.topic_shortcuts[0]["icon"] == "🧘"
    # yaml without a web: key behaves identically
    cfg2 = load_config(write_yaml(tmp_path / "w.yaml", "search: {alpha: 0.3}\n"))
    assert cfg2.web == cfg.web


def test_web_custom_values(tmp_path):
    cfg = load_config(
        write_yaml(
            tmp_path / "w.yaml",
            """
web:
  hero_title: 教會規章搜尋
  hero_subtitle: 內部規章知識庫
  search_placeholder: 例如：會議室怎麼借？
  topic_shortcuts:
    - category: 行政庶務
      icon: "🏢"
      topics: [場地借用, 車馬費]
""",
        )
    )
    assert cfg.web.hero_title == "教會規章搜尋"
    assert cfg.web.hero_subtitle == "內部規章知識庫"
    assert cfg.web.search_placeholder == "例如：會議室怎麼借？"
    assert cfg.web.topic_shortcuts == (
        {"category": "行政庶務", "icon": "🏢", "topics": ["場地借用", "車馬費"]},
    )


def test_web_empty_shortcuts_is_explicit_hide(tmp_path):
    cfg = load_config(write_yaml(tmp_path / "w.yaml", "web:\n  topic_shortcuts: []\n"))
    assert cfg.web.topic_shortcuts == ()


def test_web_shortcut_missing_category_raises(tmp_path):
    p = write_yaml(tmp_path / "w.yaml", "web:\n  topic_shortcuts:\n    - topics: [a]\n")
    with pytest.raises(ConfigError, match="category"):
        load_config(p)


def test_web_shortcut_bad_topics_raises(tmp_path):
    p = write_yaml(
        tmp_path / "w.yaml",
        "web:\n  topic_shortcuts:\n    - category: x\n      topics: 場地\n",
    )
    with pytest.raises(ConfigError, match="topics"):
        load_config(p)


def test_web_must_be_mapping(tmp_path):
    p = write_yaml(tmp_path / "w.yaml", "web: [1, 2]\n")
    with pytest.raises(ConfigError, match="'web' must be a mapping"):
        load_config(p)


def test_web_ask_defaults_reproduce_pre_0_6_hardcoded_copy():
    """An unconfigured deployment must render the strings 0.5.2 hardcoded."""
    cfg = load_config(None)
    assert cfg.web.ask_hint == "直接輸入問題，由 AI 從語料中檢索並總結回答。"
    assert cfg.web.ask_placeholder == "例如：靈命成長的關鍵是什麼？"
    assert cfg.web.ask_examples == ()


def test_web_ask_custom_values(tmp_path):
    cfg = load_config(
        write_yaml(
            tmp_path / "w.yaml",
            """
web:
  ask_hint: 問一句，我幫你查規章
  ask_placeholder: 例如：婚假可以請幾天？
  ask_examples:
    - 婚假可以請幾天
    - 借六樓會議室要提前多久
""",
        )
    )
    assert cfg.web.ask_hint == "問一句，我幫你查規章"
    assert cfg.web.ask_placeholder == "例如：婚假可以請幾天？"
    assert cfg.web.ask_examples == ("婚假可以請幾天", "借六樓會議室要提前多久")


def test_web_ask_examples_must_be_a_list(tmp_path):
    p = write_yaml(tmp_path / "w.yaml", "web:\n  ask_examples: 婚假幾天\n")
    with pytest.raises(ConfigError, match="ask_examples"):
        load_config(p)


def test_web_ask_examples_reject_blank_entries(tmp_path):
    p = write_yaml(tmp_path / "w.yaml", 'web:\n  ask_examples: ["婚假幾天", "  "]\n')
    with pytest.raises(ConfigError, match="non-empty"):
        load_config(p)


def test_web_ask_partial_override_keeps_other_defaults(tmp_path):
    cfg = load_config(write_yaml(tmp_path / "w.yaml", "web:\n  ask_hint: 只改這一句\n"))
    assert cfg.web.ask_hint == "只改這一句"
    assert cfg.web.ask_placeholder == "例如：靈命成長的關鍵是什麼？"
    assert cfg.web.hero_title == "UNCOVER DEEPER TRUTH."
