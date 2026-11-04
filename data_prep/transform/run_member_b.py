"""Transform Member B with source dates, full validation and staged exports."""
import argparse
from pathlib import Path
import behavior
import retail
from common import DEMO_STORE_ID
from contract import publish, stage_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--behavior-raw', default='data_prep/raw/ecommerce-behavior-data-from-multi-category-store')
    parser.add_argument('--retail-raw', default='data_prep/raw/online-retail-ii-uci')
    parser.add_argument('--out', default='data_prep/processed')
    parser.add_argument('--chunk-size', type=int, default=250000)
    args = parser.parse_args()
    output = Path(args.out)
    paths = behavior.resolve_raw_files(args.behavior_raw)
    target = output / 'ecommerce-behavior-data-from-multi-category-store'
    stage = stage_directory(target)
    events = stage / 'events' / 'events.csv'
    result = behavior.TransformResult()
    frames = behavior.transform_stream(
        paths, DEMO_STORE_ID, result, events, 0, args.chunk_size,
        sample_fraction=0.40, sample_seed=42,
    )
    publish(target, frames, paths, result.raw_counts, stage=stage,
            streams={'events': events}, dropped=result.dropped, notes=result.notes,
            expected_counts={'events': sum(result.sampled_counts.values()) - sum(result.dropped_by_source.values())},
            accepted_source_exclusions={k: v for k, v in result.dropped_by_source.items() if v},
            retain_previous=False)
    result = retail.transform(retail.load_raw(args.retail_raw), DEMO_STORE_ID)
    publish(output / 'online-retail-ii-uci', result.frames,
            [retail.resolve_raw_file(args.retail_raw)], result.raw_counts,
            dropped=result.dropped, notes=result.notes, source_exceptions=result.exceptions,
            expected_counts={'order_items': sum(result.raw_counts.values()) - sum(result.dropped_by_source.values())},
            accepted_source_exclusions={k: v for k, v in result.dropped_by_source.items() if v},
            retain_previous=False)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
