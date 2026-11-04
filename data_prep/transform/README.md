# Data transformation

Use the three member notebooks. Their initial cells reuse `data_prep/raw/<dataset-slug>/` and download missing datasets only. Run later cells in order after restarting the kernel when code changes.

The [shared contract](../../docs/notes/data_transformation_notes/transformation-contract.md) defines one demo tenant, six distinct source populations, source-preserving dates and IDs, source precision, schema changes, exceptions, manifests and publication. `backend/schema.sql` is the executable column/type contract.

- `common.py`: shared tenant, namespaces, UUIDs and parsing.
- `dunnhumby.py`: Member A, including streamed causal promotions.
- `behavior.py`, `retail.py`: Member B.
- `member_c.py`: coupon, churn and streamed messaging.
- `contract.py`: schema validation, streaming uniqueness, staging and publication.
- `test_preservation.py`: regression tests using temporary inputs only.

Final CSVs are `data_prep/processed/<dataset-slug>/<table>/<table>.csv`. Failed runs retain hidden staging/exception folders and leave existing final datasets intact. Do not ingest hidden staging/backup folders or noncanonical source-preservation CSVs.

ML training remains separate and consumes raw source datasets. These transforms do not compute customer features or predictions.

Member B's notebook and command-line runner sample only October/November behavior
data: approximately 40% of rows per file, keeping whole customer sessions with
seed 42 before preprocessing. Both files are scanned across their full periods;
raw files stay unchanged. Other datasets are not downsampled. Actual selected
counts and sampling settings are recorded in the publication manifest notes.

Run checks: `python -m unittest discover -s data_prep/transform -p "test_*.py"`.

See [implementation validation](../../docs/notes/data_transformation_notes/implementation-validation.md) for measured full-source checks, representative checks, and remaining full-run/database verification.
