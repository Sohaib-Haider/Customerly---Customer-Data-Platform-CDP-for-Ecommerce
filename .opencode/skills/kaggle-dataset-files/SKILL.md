---
name: kaggle-dataset-files
description: Use when you need the exact file list and exact column headers of a Kaggle dataset's data files (CSV). Triggers: "get files", "list columns", "attributes", "column names", "headers" for a Kaggle URL.
---

Extract the exact list of data files (usually CSV) in a Kaggle dataset and, for each file, its exact column headers in source order.

Sources of truth (priority order):
1. Kaggle API view endpoint (NOT the web page, it's a JS shell):
   `curl -s https://www.kaggle.com/api/v1/datasets/view/<owner>/<slug>`
   The `description` field names the dataset files. The `files` array is EMPTY and downloads need auth — never rely on them.
2. Real file headers from a downloadable mirror of the same dataset:
   - mkechinov datasets → REES46 (data.rees46.com); small CSVs fetch whole, large .gz via header-only stream: `curl -s -r 0-499999 <url> | gzip -dc 2>/dev/null | head -1`
   - Online Retail II → UCI ML Archive (dataset 502)
   - Others → GitHub mirrors / Hugging Face datasets server schema endpoints
3. Websearch (deep) citing the exact dataset/columns, cross-checked against a real header when found.

Rules:
- Trust the real CSV header over ANY secondary source (a thesis/notebook may rename columns, e.g. campaign_id → campaigns_id; the actual header wins).
- Never download full datasets — headers only.
- Report: dataset title, file list, and per file the ordered column names + count.