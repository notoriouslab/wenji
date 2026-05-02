# wenji 5-minute walkthrough

A self-contained example: ingest a small mixed corpus (10 articles —
5 long-form sermons, 3 classical Chinese poems, 2 technical tutorials),
classify into 3 axes, run a few searches, and run an eval set.

The corpus under `examples/articles/` is:

- `sermon/` — 5 sermon transcripts (~10 K characters each), reproduced with
  permission from breadoflife.taipei. Long-form text exercises chunking +
  embedding truncation realistically.
- `classical/` — 3 Tang-dynasty poems in the public domain (Li Bai, Du Fu,
  Wang Wei).
- `tech/` — 2 wenji-authored short technical articles.

Replace any of these with your own markdown to bring up your corpus.

## 1. Install + download the embed model (~600 MB, one-off)

```bash
pip install wenji
wenji download-model embed
# resolves to ~/.cache/wenji/bge-m3-onnx-int8/
```

## 2. Ingest the example corpus

From the `examples/` directory:

```bash
wenji ingest articles/ \
  --db /tmp/wenji_demo.db \
  --config wenji.yaml
```

Expected output:

```
ingesting articles/ → /tmp/wenji_demo.db
{"ingested": 10}
```

## 3. Classify into the 3 demo axes

```bash
wenji classify \
  --db /tmp/wenji_demo.db \
  --config axes.yaml \
  --validate
```

Expected output (validation PASSes the permissive bounds in `axes.yaml`):

```
classifying with 3 axis rules → /tmp/wenji_demo.db
{"classified": 10}
{
  "validation": "PASS",
  "metrics": {...},
  "failures": []
}
```

## 4. Search interactively

Start the server (prints PID / url / db / Ctrl+C banner):

```bash
wenji serve --db /tmp/wenji_demo.db --port 8002
```

Open <http://localhost:8002> and try queries:

- `明月` — should return at least two Tang poems (verse axis)
- `復興` or `復活節` — finds the matching sermon transcript (sermon axis)
- `FTS5` — finds the SQLite tutorial (tutorial axis)

Click any result title to open `/article/<id>` for the full text.

You can also hit the JSON API:

```bash
curl 'http://localhost:8002/api/search?q=ONNX&limit=3' | jq .
```

## 5. Run the eval set

In another terminal (server still running):

```bash
wenji eval \
  --candidates eval.jsonl \
  --port 8002 \
  -o eval-result.json
```

Expected: most of the 10 demo queries auto-pass. The `summary` block reports
`pass_count`, `pass_rate_pct`, hit@1/3/5 + MRR@5 per predicate, and
breakdowns by category / source.

## 6. Bring your own corpus

Replace `articles/` with your own markdown directory. Each `.md` file SHALL
have YAML frontmatter:

```yaml
---
title: Your Title
pubDate: 2024-01-15        # ISO 8601 or YYYY-MM-DD
tags: [tag1, tag2]
source_type: my_type       # OR derived from parent dir via wenji.yaml
author: Optional
description: Optional
---
Body content here.
```

Adjust `wenji.yaml` `directory_map` to mirror your subdirectory names, and
`axes.yaml` rules to mirror your `source_type` values. That is the entire
configuration surface — no code changes required.
