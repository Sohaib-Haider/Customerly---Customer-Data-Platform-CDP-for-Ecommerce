---
name: kaggle-dataset-descriptions
description: Use when you need the written description of a Kaggle dataset ("About Dataset") and/or descriptions of its individual attributes/columns. Triggers: "description", "About Dataset", "what does each column mean", "data dictionary".
---

# Kaggle Dataset Descriptions

Retrieve the dataset's written description ("About Dataset" text) and, when
Kaggle provides them, descriptions of its individual attributes/columns.
Include per-attribute descriptions ONLY IF found — never invent them.

## When to Invoke

- User asks for the "About Dataset" text of a Kaggle dataset.
- User asks what each column/attribute means in a Kaggle dataset.
- User requests a data dictionary for a Kaggle dataset.
- User asks for the description or context of a specific Kaggle dataset.

## When NOT to Invoke

- User needs file names or column headers — use `kaggle-dataset-files`.
- User wants a segment doc file generated — use `kaggle-segment-docs`.
- User wants to download or inspect actual data rows — out of scope.

## Workflow

Stage logic is explicit and sequential.

### Stage 1 — Fetch from Kaggle API view endpoint

`curl -s https://www.kaggle.com/api/v1/datasets/view/<owner>/<slug>`
Extract the full `description` field (About / Context / Content / per-file
notes). This is the authoritative About text.

### Stage 2 — Check metadata endpoint for column descriptions

`curl -s https://www.kaggle.com/api/v1/datasets/metadata/<owner>/<slug>`
Look at `info.resources[].schema.fields[].name/description/type`. Usually
empty (Kaggle strips it); still try — if populated, it's the official
attribute descriptions.

### Stage 3 — Check creator's overview/EDA notebook

Search Kaggle for the dataset creator's overview or EDA notebook (e.g.,
REES46's "direct-messaging-campaigns-dataset-overview" documents every
property). Extract per-attribute descriptions if present.

### Stage 4 — Websearch fallback

Websearch (deep) for a data dictionary of that specific dataset. Use only
if stages 1–3 yielded nothing.

## Output Format

```
<About text from Stage 1, verbatim>

### Attribute Descriptions

- <column>: <description> (or "No attribute descriptions provided by Kaggle for this dataset.")
- <column>: <description>
...
```

## Output Rules

- The Kaggle Data tab is a JS shell — not scrapeable; skip it.
- Attribute descriptions live uploader-side; if none of the sources yield
  them, say "No attribute descriptions provided by Kaggle for this
  dataset."
- Never invent or guess attribute descriptions.
- Report: the About text (verbatim) and, per attribute, description or a
  clear "not provided" note.
