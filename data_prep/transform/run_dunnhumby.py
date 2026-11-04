"""Transform Member A with the same contract as the notebook."""
import argparse
from pathlib import Path
from common import DEMO_STORE_ID
from dunnhumby import RAW_FILES, load_raw, transform, stream_causal
from contract import publish, stage_directory


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw', default='data_prep/raw/dunnhumby-the-complete-journey')
    parser.add_argument('--out', default='data_prep/processed/dunnhumby-the-complete-journey')
    args = parser.parse_args()
    raw = load_raw(args.raw, stream_causal=True)
    stage = stage_directory(Path(args.out))
    result = transform(raw, DEMO_STORE_ID)
    result.frames.pop('causal_data')
    causal_path = stage / 'causal_data' / 'causal_data.csv'
    stream_causal(args.raw, result.frames['products'], DEMO_STORE_ID, causal_path, result)
    publish(Path(args.out), result.frames, [Path(args.raw) / n for n in RAW_FILES],
            result.raw_counts, dropped=result.dropped, notes=result.notes,
            source_exceptions=result.exceptions, stage=stage, streams={'causal_data': causal_path},
            expected_counts={'order_items': len(raw['transaction_data.csv']),
                             'coupon_redemptions': len(raw['coupon_redempt.csv']),
                             'causal_data': result.raw_counts['causal_data.csv']},
            sidecars={'coupon_product_mapping': raw['coupon.csv'],
                      'campaign_exposure': raw['campaign_table.csv'],
                      'redemption_source': raw['coupon_redempt.csv'].assign(source_row=raw['coupon_redempt.csv'].index)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
