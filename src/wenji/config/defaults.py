"""Built-in defaults applied when YAML config keys are missing.

Each mapping is a dict literal so user can pull the canonical default and
diff against their override.
"""

from __future__ import annotations

DEFAULT_DIRECTORY_MAP: dict[str, str] = {}
"""Default directory_map is empty — user MUST set if relying on path-based source_type."""

DEFAULT_CHUNK_STRATEGIES: dict[str, dict] = {}
"""Default chunk_strategies is empty — articles not chunked unless source_type is configured."""

DEFAULT_SEARCH_CONFIG: dict = {
    "alpha": 0.25,
    "candidate_pool": 50,
    "default_limit": 10,
}

DEFAULT_WEB_CONFIG: dict = {
    "hero_title": "UNCOVER DEEPER TRUTH.",
    # None → template renders the built-in subtitle with site_name interpolated.
    "hero_subtitle": None,
    "search_placeholder": "搜尋關於屬靈操練、教會事工或神學的問題...",
    # Ask panel / page copy. Defaults reproduce the strings that were hardcoded
    # in base.html through 0.5.2, so an unconfigured deployment renders as before.
    "ask_hint": "直接輸入問題，由 AI 從語料中檢索並總結回答。",
    "ask_placeholder": "例如：靈命成長的關鍵是什麼？",
    "ask_examples": [],
    "topic_shortcuts": [
        {
            "category": "靈修與實踐",
            "icon": "🧘",
            "topics": ["禱告的意義", "禁食禱告", "靈命成長", "屬靈爭戰", "讀經方法"],
        },
        {
            "category": "教會與事工",
            "icon": "⛪",
            "topics": ["門訓落實", "小組事工", "宣教策略", "青年牧區", "領袖培育"],
        },
    ],
}
"""Homepage + ask strings/shortcuts. Deployers override via ``web:`` in
wenji.yaml; ``topic_shortcuts: []`` and ``ask_examples: []`` each hide their
section entirely."""
