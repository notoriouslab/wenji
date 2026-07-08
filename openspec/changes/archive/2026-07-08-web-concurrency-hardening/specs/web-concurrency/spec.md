# Capability: web-concurrency

## ADDED Requirements

### Requirement: Lazy singletons initialize exactly once under concurrency

The web app's lazily-constructed singletons (`Searcher`, `TagBrowser`) MUST be initialized at most once per process regardless of concurrent first requests. Losing initializers MUST NOT leak connections or duplicate model loads.

#### Scenario: concurrent cold-start requests build one searcher

- **WHEN** N threads concurrently invoke the searcher accessor on a cold process
- **THEN** the underlying constructor (including `Embedder` model load) MUST execute exactly once
- **AND** all N callers MUST receive the same instance

### Requirement: Shared SQLite connection calls are serialized

All concurrent access to the process-lifetime `Searcher` connection (including `QueryRewriter` cache writes) MUST be serialized by an in-process lock. Comments describing the concurrency model MUST NOT claim SQLite's file lock serializes same-connection concurrent calls.

#### Scenario: concurrent searches do not corrupt connection state

- **WHEN** multiple threads call the search endpoint concurrently with rewrite caching enabled
- **THEN** no `sqlite3.OperationalError` or torn cursor state arises from same-connection concurrency
- **AND** each request returns a complete result set

### Requirement: Tag browser data refreshes within a bounded interval

`TagBrowser` MUST re-read `articles_meta` when its cache is older than the refresh TTL (300 seconds), and cache replacement MUST be atomic (readers never observe a mixed pair of `_tag_to_articles` / `_article_to_meta`).

#### Scenario: article ingested while serve runs becomes visible

- **WHEN** an article is ingested by an external process and the TTL has elapsed
- **THEN** the next tags request MUST include the new article without a server restart

##### Example: post-TTL visibility

- **GIVEN** serve started at T0 with 10 articles, tag `禱告` count = 3
- **WHEN** `wenji ingest dir new/` adds an article tagged `禱告` at T0+1min, and `/api/tags` is requested at T0+6min（TTL 300s 已過）
- **THEN** the response lists `禱告` with count = 4

#### Scenario: concurrent tag reads never observe mixed state

- **WHEN** two threads trigger refresh and read concurrently
- **THEN** `get_tag_detail` MUST NOT raise `KeyError` due to mismatched internal maps

### Requirement: Invalid year filter degrades gracefully

Non-numeric `year` query parameters MUST be ignored (filter dropped) rather than raising an unhandled exception; numeric behavior is unchanged.

#### Scenario: bad year returns the page

- **WHEN** `GET /?year=abc` is requested
- **THEN** the response MUST be HTTP 200 with the unfiltered listing
