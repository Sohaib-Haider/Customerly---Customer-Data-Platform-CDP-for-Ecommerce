"""Shared schema validation, staging and provenance for all six sources.

No source file is modified. SQL is the column/type contract. A publication is
blocked on invalid canonical rows or losses reported by legacy transforms.
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
import shutil
import tempfile

import pandas as pd

from common import DEMO_STORE_ID, DEMO_STORE_NAME

ROOT = Path(__file__).resolve().parents[2]
SQL = ROOT / 'backend' / 'schema.sql'


def schema():
    text = re.sub(r'--[^\n]*', '', SQL.read_text(encoding='utf-8'))
    result = {}
    for table, body in re.findall(r'CREATE TABLE (\w+) \((.*?)\n\);', text, re.S):
        columns = {}
        for line in body.splitlines():
            match = re.match(r'\s*(\w+)\s+(UUID|VARCHAR\(\d+\)|TEXT|TIMESTAMPTZ|TIMESTAMP|DATE|BOOLEAN|SMALLINT|INTEGER|INT|NUMERIC(?:\(\d+,\d+\))?|JSONB)(?=\s|,|$)(.*)', line)
            if match:
                name, kind, rest = match.groups()
                columns[name] = {'type': kind, 'required': 'NOT NULL' in rest,
                                 'default': 'DEFAULT' in rest}
        result[table] = {
            'columns': columns,
            'unique': [x.replace(' ', '').split(',') for x in re.findall(r'(?:PRIMARY KEY|UNIQUE)\s*\(([^)]+)\)', body)],
            'foreign': re.findall(r'FOREIGN KEY \((\w+)\) REFERENCES (\w+) \((\w+)\)', body),
        }
    return result


SCHEMA = schema()


def canonical(table, frame, store_id=DEMO_STORE_ID):
    """Fill optional columns and platform metadata, never business facts."""
    out = frame.copy()
    stamp = datetime.now(timezone.utc).isoformat()
    for col, spec in SCHEMA[table]['columns'].items():
        if col not in out:
            out[col] = store_id if col == 'store_id' else None
        if col in ('created_at', 'updated_at') and spec['default']:
            out[col] = out[col].fillna(stamp)
        if spec['type'] in ('INT', 'INTEGER', 'SMALLINT'):
            # PostgreSQL COPY accepts "4", not "4.0", for integer columns.
            # Keep invalid nonintegral tokens intact for validation to reject.
            def integral_token(value):
                number = decimal_value(value)
                if number is not None and number == number.to_integral_value():
                    return int(number)
                return value
            out[col] = pd.Series([integral_token(v) for v in out[col]], index=out.index, dtype=object)
    return out[list(SCHEMA[table]['columns'])]


def store_frame(store_id=DEMO_STORE_ID):
    # Stable metadata across every dataset, not a fabricated business timestamp.
    return canonical('stores', pd.DataFrame([{
        'id': store_id, 'name': DEMO_STORE_NAME, 'plan': 'pro',
        'created_at': '2026-10-21T00:00:00+00:00',
        'updated_at': '2026-10-21T00:00:00+00:00',
    }]), store_id)


def decimal_value(value):
    if pd.isna(value) or str(value).strip() == '':
        return None
    try:
        result = Decimal(str(value).strip())
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def invalid_rows(table, frame):
    """Return per-row reasons; a row with several errors is counted once."""
    reasons = pd.Series('', index=frame.index, dtype=object)
    def mark(mask, message):
        reasons.loc[mask] += message + '; '
    for col, spec in SCHEMA[table]['columns'].items():
        if col not in frame:
            mark(pd.Series(True, index=frame.index), f'missing column {col}')
            continue
        values = frame[col]
        present = values.notna() & values.astype(str).str.strip().ne('')
        if spec['required']:
            mark(~present, f'{col}: required')
        kind = spec['type']
        if kind == 'UUID':
            def valid_uuid(v):
                try:
                    uuid.UUID(str(v)); return True
                except (ValueError, TypeError, AttributeError):
                    return False
            mark(present & ~values.map(valid_uuid), f'{col}: UUID')
        elif kind in ('INT', 'INTEGER', 'SMALLINT') or kind.startswith('NUMERIC'):
            parsed = values.map(decimal_value)
            bad = present & parsed.isna()
            if kind in ('INT', 'INTEGER', 'SMALLINT'):
                limit = 2**15 if kind == 'SMALLINT' else 2**31
                bad |= parsed.map(lambda v: v is not None and (v != v.to_integral_value() or not -limit <= v < limit))
            elif '(' in kind:
                precision, scale = map(int, re.findall(r'\d+', kind))
                quantum = Decimal(1).scaleb(-scale)
                def fits(value):
                    if value is None:
                        return True
                    try:
                        return abs(value) < Decimal(10) ** (precision - scale) and value == value.quantize(quantum)
                    except InvalidOperation:
                        return False
                bad |= ~parsed.map(fits)
            mark(bad, f'{col}: {kind}')
        elif kind == 'BOOLEAN':
            mark(present & ~values.astype(str).str.lower().isin(['true', 'false']), f'{col}: BOOLEAN')
        elif kind in ('DATE', 'TIMESTAMP', 'TIMESTAMPTZ'):
            mark(present & pd.to_datetime(values, errors='coerce', format='mixed', utc=True).isna(), f'{col}: {kind}')
        elif kind.startswith('VARCHAR'):
            limit = int(re.search(r'\d+', kind).group())
            mark(present & values.astype(str).str.len().gt(limit), f'{col}: exceeds {limit} characters')
    return reasons


def validate_frames(frames, only=None):
    failures, exceptions = [], {}
    for table, frame in frames.items():
        if only is not None and table not in only:
            continue
        if list(frame.columns) != list(SCHEMA[table]['columns']):
            failures.append(f'{table}: incorrect columns/order')
        reasons = invalid_rows(table, frame)
        for cols in SCHEMA[table]['unique']:
            bad = frame.duplicated(cols, keep=False)
            reasons.loc[bad] += f'duplicate {cols}; '
        for child_col, parent, parent_col in SCHEMA[table]['foreign']:
            present = frame[child_col].notna()
            if parent not in frames:
                bad = present
            else:
                bad = present & ~frame[child_col].isin(frames[parent][parent_col])
                if 'store_id' in frame and 'store_id' in frames[parent] and parent != 'stores':
                    owners = frames[parent].drop_duplicates(parent_col).set_index(parent_col)['store_id']
                    bad |= present & frame[child_col].map(owners).ne(frame['store_id'])
            reasons.loc[bad] += f'{child_col}: unresolved/cross-tenant {parent}; '
        bad = reasons.ne('')
        if bad.any():
            exceptions[table] = frame.loc[bad].assign(exception_reason=reasons[bad], source_output_row=frame.index[bad])
            failures.append(f'{table}: {int(bad.sum())} invalid rows')
    # order_items has tenant ownership through its two parents.
    if (only is None or 'order_items' in only) and all(t in frames for t in ('orders', 'products', 'order_items')):
        items = frames['order_items']
        orders = frames['orders'].drop_duplicates('id').set_index('id')['store_id']
        products = frames['products'].drop_duplicates('id').set_index('id')['store_id']
        if items['order_id'].map(orders).ne(items['product_id'].map(products)).any():
            failures.append('order_items: mismatched parent tenants')
    return failures, exceptions


def stage_directory(output):
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f'.{output.name}-staging-', dir=output.parent))


def file_manifest(paths):
    result = []
    for path in paths:
        path = Path(path)
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(8 * 1024 * 1024), b''):
                digest.update(block)
        result.append({'file': str(path), 'bytes': path.stat().st_size,
                       'sha256': digest.hexdigest()})
    return result


def validate_stream(table, path, dimensions, stage, chunk_size=250_000):
    """Validate every row, including global uniqueness, using partitioned Python key files."""
    errors, count = [], 0
    groups = SCHEMA[table]['unique']
    # UUID keys occupy 16 bytes each, without database indexes or journals.
    # General composite keys retain exact JSON values; hashes only route keys,
    # never determine equality, so hash collisions cannot create false duplicates.
    compact = [len(cols) == 1 and SCHEMA[table]['columns'][cols[0]]['type'] == 'UUID'
               for cols in groups]
    with tempfile.TemporaryDirectory(prefix=f'{table}-keys-', dir=stage) as workspace:
        workspace = Path(workspace)
        for chunk in pd.read_csv(path, dtype=str, chunksize=chunk_size, keep_default_na=False, na_values=['']):
            failures, exceptions = validate_frames({**dimensions, table: chunk}, only={table})
            for name, rejected in exceptions.items():
                if name == table:
                    target = Path(stage) / 'exceptions' / f'{table}.csv'
                    target.parent.mkdir(exist_ok=True)
                    rejected.to_csv(target, mode='a', header=not target.exists(), index=False)
            errors.extend(failures)
            for i, cols in enumerate(groups):
                partitions = {}
                for row in chunk[cols].itertuples(index=False, name=None):
                    if compact[i]:
                        try:
                            key = uuid.UUID(str(row[0])).bytes
                        except (ValueError, TypeError, AttributeError):
                            continue  # Already rejected by the row validator.
                        bucket = key[0]
                        record = key
                    else:
                        key = json.dumps(row, default=str).encode('utf-8')
                        bucket = hashlib.blake2b(key, digest_size=1).digest()[0]
                        record = key + b'\n'
                    partitions.setdefault(bucket, bytearray()).extend(record)
                for bucket, records in partitions.items():
                    with (workspace / f'{i}-{bucket}').open('ab') as stream:
                        stream.write(records)
            count += len(chunk)
        # Identical keys always land together, even across distant input chunks.
        # Inspect one partition at a time instead of retaining every source ID.
        for i, cols in enumerate(groups):
            for partition in workspace.glob(f'{i}-*'):
                seen = set()
                duplicate = False
                with partition.open('rb') as stream:
                    records = iter(lambda: stream.read(16), b'') if compact[i] else iter(stream)
                    for key in records:
                        if key in seen:
                            duplicate = True
                            break
                        seen.add(key)
                partition.unlink()
                if duplicate:
                    errors.append(f'{table}: duplicate global key {cols}')
        return list(dict.fromkeys(errors)), count


def publish(output, frames, sources, raw_counts, *, dropped=None, notes=None,
            stage=None, streams=None, sidecars=None, expected_counts=None, source_exceptions=None,
            accepted_source_exclusions=None, retain_previous=True):
    """Validate and stage a whole dataset; retain failed staging for diagnosis.

    Directory swap preserves the previous publication until validation passes.
    A same-volume backup is retained after replacement, never deleted here.
    """
    output = Path(output)
    stage = Path(stage) if stage else stage_directory(output)
    frames = {t: canonical(t, f) for t, f in frames.items()}
    frames['stores'] = store_frame()
    failures, exceptions = validate_frames(frames)
    counts = {t: len(f) for t, f in frames.items()}
    for table, path in (streams or {}).items():
        stream_errors, counts[table] = validate_stream(table, path, frames, stage)
        failures.extend(stream_errors)
    for table, expected in (expected_counts or {}).items():
        if counts.get(table) != expected:
            failures.append(f'{table}: expected {expected} rows, found {counts.get(table)}')
    # Explicitly accepted cleaning losses must have one retained exception row
    # per excluded source row. Type/FK/uniqueness failures still block publication.
    audited_exclusions = {}
    for source, expected in (accepted_source_exclusions or {}).items():
        candidates = [stage / 'exceptions' / Path(source).name]
        if source_exceptions and source in source_exceptions:
            actual = len(source_exceptions[source])
        elif candidates[0].is_file():
            actual = sum(len(c) for c in pd.read_csv(candidates[0], chunksize=250_000))
        else:
            actual = 0
        audited_exclusions[source] = actual
        if actual != expected:
            failures.append(f'{source}: expected {expected} audited exclusions, found {actual}')
    if dropped and any(dropped.values()) and not accepted_source_exclusions:
        failures.append('Source rows excluded: resolve the recorded exceptions before publication')
    for name, rejected in (source_exceptions or {}).items():
        if len(rejected):
            target = stage / "exceptions" / (Path(name).stem + "-source.csv")
            target.parent.mkdir(exist_ok=True)
            rejected.to_csv(target, index=False)
    for table, rejected in exceptions.items():
        target = stage / 'exceptions' / f'{table}.csv'
        target.parent.mkdir(exist_ok=True)
        rejected.to_csv(target, index=False)
    for name, frame in (sidecars or {}).items():
        target = stage / 'source_preservation' / f'{name}.csv'
        target.parent.mkdir(exist_ok=True)
        frame.to_csv(target, index=False)
    # Source versions are the KaggleHub completion-marker paths, when present.
    versions = sorted({str(p) for source in sources for p in Path(source).parent.glob('.complete/datasets/**/bundle.complete')})
    manifest = {'contract_version': 2, 'store_id': DEMO_STORE_ID,
                'sources': file_manifest(sources), 'kaggle_version_markers': versions,
                'raw_counts': raw_counts, 'output_counts': counts,
                'row_reconciliation': {table: {'expected': expected, 'actual': counts.get(table),
                                               'ok': counts.get(table) == expected}
                                       for table, expected in (expected_counts or {}).items()},
                'id_rule': 'UUID5(tenant | table | dataset namespace + source identity/physical row)',
                'date_policy': 'real source dates unchanged; Dunnhumby anchor 2016-01-01; no cross-source timeline shift',
                'currency_policy': 'source units unchanged; cross-source monetary totals unsupported until currencies verified',
                'dropped': dropped or {}, 'notes': notes or [],
                'accepted_source_exclusions': accepted_source_exclusions or {},
                'audited_source_exclusions': audited_exclusions,
                'source_exception_counts': {name: len(frame) for name, frame in (source_exceptions or {}).items()},
                'reason_count_policy': 'Reason counts may overlap; source_exception_counts count distinct source rows.',
                'validation': {'ok': not failures, 'failures': failures},
                'database_import_verified': False}
    (stage / 'manifest.json').write_text(json.dumps(manifest, indent=2, default=str), encoding='utf-8')
    if failures:
        raise ValueError(f'Publication blocked; details retained in {stage}: ' + '; '.join(failures[:10]))
    for table, frame in frames.items():
        folder = stage / table
        folder.mkdir(exist_ok=True)
        frame.to_csv(folder / f'{table}.csv', index=False, na_rep='')
    for table, source in (streams or {}).items():
        target = stage / table / f'{table}.csv'
        target.parent.mkdir(exist_ok=True)
        if Path(source).resolve() != target.resolve():
            shutil.copyfile(source, target)
    backup = None
    if output.exists():
        backup = output.with_name(f'.{output.name}-previous-{uuid.uuid4().hex}')
        output.rename(backup)
    try:
        stage.rename(output)
    except BaseException:
        if backup is not None:
            backup.rename(output)
        raise
    if backup is not None and not retain_previous:
        # Only delete the backup created by this publication, after a successful
        # swap. Resolve and check its parent before any recursive removal.
        resolved_backup = backup.resolve()
        if resolved_backup.parent != output.resolve().parent or not backup.name.startswith(f'.{output.name}-previous-'):
            raise ValueError('Refusing to remove a backup outside the output directory')
        shutil.rmtree(resolved_backup)
    return manifest
