---
name: kaggle-dataset-descriptions
description: Use when you need the written description of a Kaggle dataset ("About Dataset") and/or descriptions of its individual attributes/columns. Triggers: "description", "About Dataset", "what does each column mean", "data dictionary".
---

Get the dataset's written description ("About Dataset" text) and, when Kaggle provides them, descriptions of its individual attributes/columns. Include per-attribute descriptions ONLY IF found ("IF MENTIONED ONLY") — never invent them.

Sources of truth (priority order):
1. Kaggle API view endpoint: `curl -s https://www.kaggle.com/api/v1/datasets/view/<owner>/<slug>` → the full `description` field (About / Context / Content / per-file notes). This is the authoritative About text.
2. Column/file descriptions in metadata: `curl -s https://www.kaggle.com/api/v1/datasets/metadata/<owner>/<slug>` → `info.resources[].schema.fields[].name/description/type`. Usually empty (Kaggle strips it); still try — if populated, it's the official attribute descriptions.
3. The dataset creator's overview/EDA notebook on Kaggle (e.g., REES46's "direct-messaging-campaigns-dataset-overview" documents every property).
4. Websearch (deep) for a data dictionary of that specific dataset.

Rules:
- The Kaggle Data tab is a JS shell — not scrapeable; skip it.
- Attribute descriptions live uploader-side; if none of the sources yield them, say "No attribute descriptions provided by Kaggle for this dataset."
- Report: the About text (verbatim) and, per attribute, description or a clear "not provided" note.