---
name: kaggle-segment-docs
description: Use when generating or re-generating the segment markdown files in docs/notes/segments/ that document each segment's Kaggle dataset. It only assembles output from the kaggle-dataset-files and kaggle-dataset-descriptions skills into the file. Triggers: "generate segment file", "redo segment docs", "create the <segment>.md".
---

# Kaggle Segment Docs

Assemble the documentation file for a customer segment. Take no other
action — no analysis, no marketing actions, just fill the file from skill
outputs.

## When to Invoke

- User asks to generate or regenerate a segment markdown file in
  `docs/notes/segments/`.
- User says "generate segment file", "redo segment docs", or "create the
  <segment>.md".
- A new segment has been identified and needs its dataset documented.

## When NOT to Invoke

- User only needs raw file/column info or descriptions without writing a
  doc file — use `kaggle-dataset-files` or `kaggle-dataset-descriptions`
  directly.
- User wants analysis, insights, or marketing actions — out of scope.
- Segment file already exists and user didn't ask to regenerate — skip.

## Workflow

Stage logic is explicit and sequential.

### Stage 1 — Get segment name + Kaggle URL

Two valid modes:
1. From the repo: read `docs/notes/customer segmentation/segments-overview.md`
   (or a named segment file) for the segment name + its Kaggle URL.
2. Manual: user supplies segment name + Kaggle URL directly.

Never invent a URL; ask if ambiguous.

### Stage 2 — Run `kaggle-dataset-files`

Load the `kaggle-dataset-files` skill and run it for the dataset → file
list + exact column headers per file.

### Stage 3 — Run `kaggle-dataset-descriptions`

Load the `kaggle-dataset-descriptions` skill and run it for the same
dataset → About text + attribute descriptions (if provided).

### Stage 4 — Write the markdown file

Write `docs/notes/segments/<Segment Name>.md` using the Output Format
below.

## Output Format

```markdown
# <Segment Name>

## Dataset

<Dataset title as shown on Kaggle>

<Kaggle URL>

## About Dataset

<About text from Stage 3, verbatim; or "No dataset description provided on Kaggle.">

## Files & Attributes

<filename>.csv (N columns)
- <column>
- <column>
...
```

One block per file; filenames exactly as in the dataset; columns in
source order; attribute descriptions (if found) as a short note under the
respective column.

## Output Rules

- Never invent a URL; ask if ambiguous.
- No analysis, no marketing actions — just fill the file from skill
  outputs.
- Report to the user: file written, per file the column count, and
  whether attribute descriptions were available.
