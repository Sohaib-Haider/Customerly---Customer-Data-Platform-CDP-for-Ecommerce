---
name: kaggle-dataset-files
description: Use when you need the exact file list and exact column headers of a Kaggle dataset's data files (CSV). Triggers: "get files", "list columns", "attributes", "column names", "headers" for a Kaggle URL.
---

# Kaggle Dataset Files

Extract the exact list of data files (usually CSV) in a Kaggle dataset and,
for each file, its exact column headers in source order.

## When to Invoke

- User asks for the file list of a Kaggle dataset.
- User asks for column names, headers, or attributes of a Kaggle dataset.
- User wants to know the schema/structure of a Kaggle dataset's CSV files.
- User provides a Kaggle URL and wants to know what data is inside.

## When NOT to Invoke

- User needs the dataset's prose description or attribute descriptions —
  use `kaggle-dataset-descriptions`.
- User wants a segment doc file generated — use `kaggle-segment-docs`.
- User wants to download or inspect actual data rows — out of scope.

## Workflow

Stage logic is explicit and sequential.

### Stage 1 — Fetch file list from Kaggle API view endpoint

`curl -s https://www.kaggle.com/api/v1/datasets/view/<owner>/<slug>`
The `description` field names the dataset files. The `files` array is EMPTY
and downloads need auth — never rely on them.

### Stage 2 — Get real CSV headers from mirrors

Fetch headers from a downloadable mirror of the same dataset:
- mkechinov datasets → REES46 (data.rees46.com); small CSVs fetch whole,
  large .gz via header-only stream:
  `curl -s -r 0-499999 <url> | gzip -dc 2>/dev/null | head -1`
- Online Retail II → UCI ML Archive (dataset 502)
- Others → GitHub mirrors / Hugging Face datasets server schema endpoints

### Stage 3 — Websearch fallback

Websearch (deep) citing the exact dataset/columns, cross-checked against a
real header when found.

## Output Format

```
<Dataset title as shown on Kaggle>

### <filename>.csv (N columns)
- <column>
- <column>
...

### <filename>.csv (N columns)
- <column>
...
```

## Output Rules

- Trust the real CSV header over ANY secondary source (a thesis/notebook
  may rename columns, e.g. campaign_id → campaigns_id; the actual header
  wins).
- Never download full datasets — headers only.
- Report: dataset title, file list, and per file the ordered column names
  + count.
