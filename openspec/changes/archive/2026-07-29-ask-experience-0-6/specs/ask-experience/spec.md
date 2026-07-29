# Capability: ask-experience

## ADDED Requirements

### Requirement: The LLM is grounded on clause text, not document summaries

`wenji.ask.Asker._compose_prompt` SHALL build each source block from the article title plus the markdown-stripped text of that article's top-3 matching chunks, wrapped in `<條文>` tags, one block per retrieved article. It SHALL NOT fall back to the document-level snippet when chunk text is available.

#### Scenario: numeric lookup question is answered from the clause

- **WHEN** `Asker.ask("開公務車出車禍，我自己要付多少錢？")` runs against the policy corpus with an LLM configured
- **THEN** the prompt sent to the LLM contains the clause text including `8000`, and the answer is not `資料中未提及`

#### Scenario: no matching chunk keeps the document snippet

- **WHEN** an article is retrieved but no chunk matches the OR query
- **THEN** that source block falls back to the document-level snippet and the request still succeeds

##### Example: every content token is filtered out

- **GIVEN** the question `這個可以嗎？`, whose tokens are all dropped by `DROP_POS` or the interrogative stopword set, so `build_fts_query_or` returns `""`
- **WHEN** `Asker.ask("這個可以嗎？")` runs
- **THEN** each citation has `chunk_texts == []` and `chunk_index == 0`, each prompt source block carries the document-level snippet, and the response status is 200

### Requirement: Ask prompt requires verbatim numbers

`ASK_PROMPT` SHALL retain its four existing rules and SHALL add a rule requiring that numbers, amounts, day counts, and percentages be quoted verbatim from the source clause without conversion or paraphrase.

#### Scenario: prompt shape is locked by test

- **WHEN** the regression test for prompt shape runs
- **THEN** it asserts the presence of the four existing rule markers plus the verbatim-number rule marker, and fails if any marker is absent

##### Example: markers asserted by `test_ask_prompt_template_has_required_clauses`

- **GIVEN** the marker list `["只能引用來源內容", "資料中未提及", "[1]", "繁體中文", "照抄"]` and the structural markers `["<query>", "<sources>", "<條文>"]`
- **WHEN** the test asserts every marker is a substring of `ASK_PROMPT` (and of `_compose_prompt` output for `<條文>`)
- **THEN** all assertions pass on the shipped template; deleting the `照抄` rule makes the test fail

### Requirement: Transient LLM failures are not cached

When the LLM call raises `LLMClientError`, `Asker.ask` SHALL return the retrieval-only answer without writing it to the cache. Results where no LLM call was attempted (empty retrieval) SHALL still be cached.

#### Scenario: a rate-limited question stays answerable

- **WHEN** the LLM raises `LLMClientError` for a question and the same question is asked again with a working LLM
- **THEN** the second call reaches the LLM and returns a real answer (before this change `cache_put` ran unconditionally, so `answer=None` persisted for the 30-day TTL)

##### Example: Groq 429 during the Phase 2 acceptance run

- **GIVEN** question n5 of the policy exam hit a Groq `429 Too Many Requests`
- **WHEN** the same question was re-asked minutes later
- **THEN** the cached `answer=None` was replayed with no LLM call at all — the observation that produced this requirement

### Requirement: Dedicated ask page

`GET /ask` SHALL render a standalone page. It SHALL prefill the question box from the `q` query parameter, emit `<meta name="robots" content="noindex, nofollow">`, and lay out answer and citations in two columns that collapse to one column below 900px viewport width. `robots.txt` SHALL include `Disallow: /ask`. The page SHALL support follow-up questions by keeping the conversation turns client-side and passing them as `history` on each request.

#### Scenario: shareable link prefills the question

- **WHEN** a user opens `/ask?q=特休假怎麼算`
- **THEN** the question box contains `特休假怎麼算` and the answer request fires for that question

#### Scenario: page is excluded from indexing

- **WHEN** `GET /ask` and `GET /robots.txt` are fetched
- **THEN** the page carries a noindex robots meta tag and `robots.txt` disallows `/ask`

#### Scenario: follow-up keeps prior turns

- **WHEN** a user asks a second question on the same page
- **THEN** the request carries the prior turns in `history` and the rendered transcript shows both exchanges

### Requirement: Ask copy is configurable

`WebConfig` SHALL gain `ask_hint`, `ask_placeholder`, and `ask_examples`. `ask_examples` SHALL be a list of strings rendered as clickable example questions; an empty list SHALL hide that section entirely. Defaults SHALL reproduce the 0.5.2 hardcoded strings so that an unconfigured deployment renders identically.

#### Scenario: unconfigured deployment is unchanged

- **WHEN** a config file has no `web:` section
- **THEN** the ask hint and placeholder render the same strings as 0.5.2

#### Scenario: example list hides when empty

- **WHEN** `web.ask_examples` is `[]`
- **THEN** the examples section is absent from the rendered HTML

### Requirement: Ask input controls have explicit styling

`static/style.css` SHALL define rules for `.chat-input-area`, `.chat-input-area textarea`, `.chat-submit`, and `.chat-select`. The submit control SHALL meet WCAG AA contrast (4.5:1) against its background. The textarea SHALL fill the available panel width. Citation snippets SHALL be clamped to at most 3 lines with an expand affordance.

#### Scenario: submit control is legible

- **WHEN** the computed styles of `.chat-submit` are inspected in a browser
- **THEN** the foreground/background contrast ratio is at least 4.5:1 (before this change the pair was `rgb(0,0,0)` on `rgb(2,48,71)`)

#### Scenario: textarea uses the panel width

- **WHEN** the ask panel is open at 1440px viewport in a 500px panel
- **THEN** the textarea offsetWidth is at least 90% of the panel's inner content width (before this change it was 182px)

#### Scenario: long citation collapses

- **WHEN** a citation snippet exceeds three lines
- **THEN** it renders clamped with an expand control that reveals the full text

##### Example: 公務車肇事條文 clamped at three lines

- **GIVEN** a citation whose `chunk_texts[0]` is the 384-character 第卅條 clause text rendered in a 640px panel
- **WHEN** the page renders it
- **THEN** the element computed style has `-webkit-line-clamp: 3` with `overflow: hidden`, its `scrollHeight` exceeds its `clientHeight`, and an expand control is present; activating the control removes the clamp and `scrollHeight` equals `clientHeight`
