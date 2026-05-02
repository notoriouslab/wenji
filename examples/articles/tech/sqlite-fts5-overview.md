---
title: SQLite FTS5 全文搜尋簡介
pubDate: 2024-04-15
tags: [sqlite, fts5, 全文搜尋, 教學]
description: 為什麼 FTS5 是中小型專案的好選擇，以及怎麼開始。
---

FTS5 是 SQLite 內建的全文搜尋擴充，從 SQLite 3.9 開始穩定可用。它把倒排索引存成虛擬表，查詢時走 BM25 排序，預設 unicode61 切詞器對英數字夠用，對中日韓字符會以字符為 token。

對中文而言，常見做法是在寫入前用 jieba 切詞 + 空格 join，讓 unicode61 把每個 token 視為獨立詞。也有 libsimple extension 提供原生 unigram 切分，避免額外編譯時可以選用 unicode61 路線。

最小的開始：

```sql
CREATE VIRTUAL TABLE docs USING fts5(title, body, tokenize='unicode61');
INSERT INTO docs VALUES ('hello', 'world');
SELECT * FROM docs WHERE docs MATCH 'world';
```

中型語料下，FTS5 通常 sub-second 回答查詢；超過數百萬篇之後，才需要考慮分片或 ANN。
