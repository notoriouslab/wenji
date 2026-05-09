# corpus-christian — wenji 中文基督教語料領域範例

這是 wenji 的**領域範例**，**不是** wenji framework 的必需組件。
非中文基督教 corpus 的使用者可以完全不載入此 example，wenji core
仍可獨立運作。

## 內容

| 檔案 | 用途 | 來源 |
|---|---|---|
| `entity_concepts.json` | 46 個中性教義 / 神學 / 教會運動概念詞，給 `EntityScorer` 做 subject 識別（concept vs person vs location disambiguation） | 來自上游 production 內部詞庫；過濾政治倫理議題後公開 |
| `intent_keywords.json` | 護教意圖（apologetics）keyword 清單，給 `IntentClassifier.detect_intent` 用 | 來自上游 production 內部詞庫 |

兩個 JSON 都是百科 / 神學辭典級的中性詞彙，**不反映**任何特定教派 /
神學派別的立場。

## 用法

### 一行載入（推薦）

```python
from wenji.search.entity import EntityScorer
from wenji.search.intent import IntentClassifier

# Load wenji-bundled christian example
scorer = EntityScorer.from_sources(["example:corpus-christian"])
classifier = IntentClassifier.from_sources(["example:corpus-christian"])
```

### 與私有詞庫合併（last-write-wins）

```python
# 合併公開 example 與私有 dict
scorer = EntityScorer.from_sources([
    "example:corpus-christian",       # public theological vocab
    "/private/my_aliases.json",       # corpus-specific aliases
])

# IntentClassifier 不從 example 載入 source_type 對映
# （那是 corpus-deployment-specific），需 caller 注入：
classifier = IntentClassifier.from_sources(
    sources=["example:corpus-christian"],
    intent_source_types={"apologetics": ["bol", "teaching"]},
)
```

### 不載入任何 example（純 framework 模式）

```python
from wenji.search import Searcher

# Searcher 預設不啟用 entity / intent layer
# 行為退化為 hybrid + chunk_signals RRF
searcher = Searcher(conn, embedder)
results = searcher.search("任何查詢", limit=10)
```

## 為什麼是「中性」詞庫

`entity_concepts.json` 的 46 個詞（如「因信稱義」、「三位一體」、
「宗教改革」）都符合：

1. 出現在 Wikipedia / 標準神學辭典 — 跨教派通用
2. 不反映 wenji-using corpus 處理哪些作者 / 哪些 source_type 為核心
3. 用途是 disambiguation（避免主詞被 location / person 搶走），
   **不是**重要性排序

我們**剔除**了上游私有 `entity_concepts.json` 中三個政治倫理議題詞
（同性婚姻 / 墮胎 / 安樂死），避免 example 透露 corpus 處理現代倫理議題
的 curation taste。其他上游私有資料（`aliases.json` 人名別名對映、
`INTENT_SOURCE_TYPES` source_type 對映）**不在本 example**，留 deployer
端 runtime 注入。

## 上游詞庫的使用方式

這些詞在上游 production 用於：

- **`EntityScorer.detect_query_entities`**：從 query 中辨識 concept
  entity，用 longest-match-first lookup
- **subject 提升**：query 同時有 concept 和 location（例：「耶路撒冷的
  救贖意義」）時，concept 優先升為 subject
- **`expand_query_with_aliases`**：concept entities 多半沒 alias，所以
  此檔案中各詞 aliases 為空（人名 alias 在上游私有 `aliases.json`，不公開）

## 修改本檔的時機

新增神學概念、教會運動、神學派別等中性詞時可直接 PR 加入本檔。
**請避免**：

- 加入只有特定教派使用的內部術語
- 加入反映立場的議題詞（如「真神 / 假神」、「正統 / 異端」這種預設立場的詞）
- 加入人名（人名屬 corpus-deployment-specific，請用私有 alias dict）

## 對應 Spec / Decision

- 規格：`openspec/specs/wenji-corpus-examples/spec.md`
- 設計：`openspec/specs/wenji-ranker-pipeline/spec.md` Decision 3（詞庫資料邊界）
- 提案：`wenji-ranker-port-v0-3-6` (v0.3.6)
