---
name: kaggle-segment-docs
description: Use when generating or re-generating the segment markdown files in docs/notes/segments/ that document each segment's Kaggle dataset. It only assembles output from the kaggle-dataset-files and kaggle-dataset-descriptions skills into the file. Triggers: "generate segment file", "redo segment docs", "create the <segment>.md".
---

Assemble the documentation file for a customer segment. Take no other action — no analysis, no marketing actions, just fill the file from skill outputs.

Input (two valid modes):
1. URL from the repo: read `docs/notes/customer segmentation/segments-overview.md` (or a named segment file) for the segment name + its Kaggle URL.
2. Manual: user supplies segment name + Kaggle URL directly.
Never invent a URL; ask if ambiguous.

Steps:
1. Load the `kaggle-dataset-files` skill and run it for the dataset → file list + exact column headers per file.
2. Load the `kaggle-dataset-descriptions` skill and run it for the same dataset → About text + attribute descriptions (if provided).
3. Write `docs/notes/segments/<Segment Name>.md` in exactly this template:

```markdown
# <Segment Name>

## Dataset

<Dataset title as shown on Kaggle>

<Kaggle URL>

## About Dataset

<About text from skill 2, verbatim; or "No dataset description provided on Kaggle.">

## Files & Attributes

<filename>.csv (N columns)
- <column>
- <column>
...
```

   One block per file; filenames exactly as in the dataset; columns in source order; attribute descriptions (if found) as a short note under the respective column.
4. Report to the user: file written, per file the column count, and whether attribute descriptions were available.