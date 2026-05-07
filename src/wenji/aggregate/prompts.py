"""Prompt templates for Aggregator LLM calls.

Templates are populated in tasks 5.x (TOPIC_PROMPT) and 6.x (CONCEPT_PROMPT).
Kept as module-level string constants so they can be diffed and overridden by
downstream consumers without monkey-patching method bodies.
"""

from __future__ import annotations

TOPIC_PROMPT = """你是基督教神學文獻彙整助手。請根據以下與「<tag>{tag}</tag>」主題相關的來源，撰寫一段繁體中文 Markdown 摘要：

- 第一段：用 2-3 句話總結這些來源對「<tag>{tag}</tag>」的核心觀點。
- 第二段：列出 3-5 個關鍵發現（bullet list），每點 1-2 句話。
- 不要編造來源沒提到的細節。如果來源彼此立場不同，請明確指出。
- 不要重複來源原文，用你自己的話歸納。

來源：

<sources>{sources}</sources>

請直接回傳 Markdown 內容，不要包 ```markdown ``` 圍欄。
"""

CONCEPT_PROMPT = """你是基督教神學文獻彙整助手。請根據以下圍繞概念「<concept>{concept}</concept>」的多來源觀點，回傳一段繁體中文分析：

- 第一段：用 2-3 句話總結這個概念在這些來源中的核心輪廓。
- 接著三個小節，分別以 `## 共識` / `## 分歧` / `## 整體 narrative` 為標題：
  - 共識：列 2-3 條（bullet）所有來源都認同的論點。
  - 分歧：列 2-3 條（bullet）來源彼此不一致的地方，明確指出哪個來源持哪個立場。
  - 整體 narrative：1-2 段散文，把上面內容串成可讀的論述。
- 不要編造來源沒提到的細節。

來源觀點：

<per_source_views>{per_source_views}</per_source_views>

請直接回傳 Markdown，不要包圍欄。
"""
