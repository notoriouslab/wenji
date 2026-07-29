# Capability: search-api-surface

## ADDED Requirements

### Requirement: FTS query builder offers an OR-combined variant for natural-language input

`wenji.search.bm25` SHALL export `build_fts_query_or(raw, *, column=None)`. It SHALL segment `raw` with `wenji.ingest.jieba_setup.jieba_cut_pos`, drop tokens whose POS tag is in `{x, r, p, c, u, d, w, y, uj, ul, zg}`, drop tokens present in the module-level interrogative stopword set, drop tokens that are entirely non-word characters, expand each surviving token to a char-level phrase, and combine the phrases with FTS5 `OR`, keeping at most `MAX_OR_TERMS` (64) phrases in input order. The existing `build_fts_query` SHALL retain its current AND semantics, signature, and behaviour.

#### Scenario: Chinese question yields a matching OR query

- **WHEN** `build_fts_query_or("開公務車出車禍，我自己要付多少錢？", column="chunk_text")` is used in a `chunks_fts MATCH` against the policy corpus
- **THEN** the match returns at least one row (the current `build_fts_query` returns zero rows for the same input)

#### Scenario: interrogative tokens are dropped

- **WHEN** `build_fts_query_or("補助最多可以拿多少？")` is called
- **THEN** the returned query contains no phrase for `多少`, `可以`, or `拿`

#### Scenario: pathologically long input is capped

- **WHEN** `build_fts_query_or` is called with 500 distinct two-word terms
- **THEN** the returned query contains exactly 64 OR-combined phrases, so one request cannot become a multi-thousand-term FTS5 expression

#### Scenario: AND builder is untouched

- **WHEN** `build_fts_query("因信稱義")` is called
- **THEN** it returns `"因 信 稱 義"` exactly as before, with no `OR` operator

### Requirement: `/api/ask` citations carry the source text that grounded the answer

Each citation object returned by `POST /api/ask` SHALL include `chunk_texts`: an ordered list of the `chunk_text_raw` values (markdown-stripped) for the top-3 chunks of that article as ranked by `bm25()` under `build_fts_query_or`, plus `chunk_indexes` listing their indexes in the same order. `chunk_index` SHALL remain present and SHALL equal the first entry of `chunk_indexes`. When no chunk matches, `chunk_texts` SHALL be an empty list and `chunk_index` SHALL be `0`. Both new fields SHALL declare list defaults so that cached answers serialised before this change deserialise without error.

#### Scenario: citation points at the clause that contains the answer

- **WHEN** `POST /api/ask` is called with `{"q": "開公務車出車禍，我自己要付多少錢？"}` against the policy corpus
- **THEN** the citation for 公務車輛管理辦法 has `chunk_indexes` containing the chunk whose text includes `8000`, and `chunk_texts` contains that clause text

#### Scenario: additive shape preserves existing readers

- **WHEN** an existing consumer reads `citations[i].article_id`, `.chunk_index`, `.title`, `.snippet`, `.bm25_score`
- **THEN** all five fields are present with unchanged meaning

### Requirement: `/api/ask` accepts caller-held conversation history

`POST /api/ask` SHALL accept an optional `history` field: a list of `{role, content}` objects where `role` is `"user"` or `"assistant"`. When `history` is non-empty, the server SHALL first call the LLM with the rewrite prompt to condense history plus the new question into one self-contained retrieval query, use that query for retrieval only, and SHALL NOT include the rewritten text in the response `answer`. When the rewrite call fails, the server SHALL fall back to concatenating the last user turn with the new question and SHALL still answer. The conversation turns SHALL be part of the answer cache key. The key SHALL NOT depend on the rewritten query, so that a cache hit costs no LLM call at all. Single-turn requests SHALL produce the same key as before this change.

#### Scenario: follow-up with elided subject retrieves the right document

- **WHEN** `POST /api/ask` is called with `q = "那病假呢？"` and `history` whose last user turn is `"結婚可以請幾天婚假？"`
- **THEN** retrieval runs on a rewritten query that names 病假 explicitly, and the response `answer` contains no rewrite scaffolding text

#### Scenario: rewrite failure degrades instead of erroring

- **WHEN** the rewrite LLM call raises `LLMClientError` and `history` is non-empty
- **THEN** the endpoint returns HTTP 200 with an answer produced from the concatenated-query retrieval

#### Scenario: repeated follow-up costs no LLM call

- **WHEN** the same `q` and `history` are posted twice
- **THEN** the second request makes zero LLM calls (neither rewrite nor answer) and returns the cached answer

#### Scenario: history is optional

- **WHEN** `POST /api/ask` is called without `history`
- **THEN** no rewrite call is made and behaviour matches the single-turn path

### Requirement: Streaming ask endpoint

`GET /api/ask/stream` SHALL exist and return `text/event-stream`. It SHALL accept `q` (required), `k`, `axis`, and `history_b64` (base64-encoded JSON of the history list). The event sequence SHALL be: one `meta` event carrying the citations as soon as retrieval completes, then one or more `delta` events carrying answer fragments, then one `done` event. The answer SHALL be written to the cache only after the stream completes. When the answer is already cached, the endpoint SHALL emit `meta`, a single `delta` with the full text, then `done`. When no LLM is configured, it SHALL return HTTP 503.

#### Scenario: citations arrive before answer text

- **WHEN** a client consumes `GET /api/ask/stream?q=...`
- **THEN** the first event received is `meta` with a non-empty citation list, and answer `delta` events follow it

#### Scenario: cached answer replays as one delta

- **WHEN** the same `q` is requested a second time
- **THEN** the stream emits exactly one `delta` containing the whole answer, followed by `done`, and no new LLM call is made

#### Scenario: no LLM configured

- **WHEN** `WENJI_LLM_BASE_URL` is unset and `GET /api/ask/stream` is called
- **THEN** the response status is 503

### Requirement: Ranking-relevant chunk signals use the OR builder

`chunk_bm25_search` in `wenji.search.rrf` SHALL build its `chunks_fts` MATCH with `build_fts_query_or`. The chunk-hit hydration path in `wenji.search` SHALL do the same.

#### Scenario: chunk signals are non-empty for a natural-language question

- **WHEN** `Searcher.search("開公務車出車禍，我自己要付多少錢？")` runs against the policy corpus
- **THEN** the `chunk_signals` dict passed to `rrf_merge` is non-empty (before this change it was empty, so RRF degraded to main-only)

### Requirement: Demo-mode search over-fetches before post-filtering

When `WENJI_DEMO_SOURCE` is set, `GET /api/search` SHALL retrieve `max(limit * 5, 50)` candidates before applying the demo source post-filter, then truncate the filtered list to `limit`.

#### Scenario: small limit no longer returns an empty list

- **WHEN** `GET /api/search?q=禱告&limit=3` is called on a deployment with `WENJI_DEMO_SOURCE` set and the corpus contains at least 3 matching articles of that source type
- **THEN** the response contains 3 results (before this change it returned 0)

#### Scenario: limit is still respected

- **WHEN** `GET /api/search?q=禱告&limit=3` returns
- **THEN** `len(results) <= 3`
