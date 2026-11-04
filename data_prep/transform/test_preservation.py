"""Regression tests for identity, relationships and publication guarantees.

All CSVs are temporary test inputs; no sample datasets are shipped or published.
Run: python -m unittest discover -s data_prep/transform -p 'test_*.py'
"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import behavior
import dunnhumby
import retail
from common import DEMO_STORE_ID, set_tenant, source_id, to_int, uid
from contract import canonical, publish, store_frame, validate_frames, validate_stream
from member_c import bools, transform_coupon, transform_churn, map_messages, campaign_keys, ids


class PreservationTests(unittest.TestCase):
    def setUp(self):
        set_tenant(DEMO_STORE_ID)

    def test_identity_and_integer_parsing(self):
        self.assertEqual(source_id('001', 'coupon'), 'cpn:001')
        self.assertNotEqual(uid('customers', 'cpn:001'), uid('customers', 'msg:001'))
        a = uid('customers', 'cpn:001')
        set_tenant('00000000-0000-0000-0000-000000000001')
        self.assertNotEqual(a, uid('customers', 'cpn:001'))
        self.assertIsNone(to_int('1.9'))
        self.assertEqual(to_int('2.0'), 2)
        self.assertEqual(list(bools(pd.Series(['false', '0', 'true', '1']))), [False, False, True, True])
        with self.assertRaises(ValueError):
            bools(pd.Series(['unknown']))

    def test_events_stable_across_chunks_and_rejections(self):
        src = pd.DataFrame({'user_id': ['01', None, '01', '02'],
            'user_session': ['x'] * 4, 'product_id': ['1'] * 4,
            'event_type': ['view', 'cart', 'purchase', 'remove_from_cart'],
            'event_time': ['2019-10-01 12:00:00 UTC'] * 4, 'price': ['1.234'] * 4,
            'category_id': ['5'] * 4, 'category_code': [None, None, 'c', 'c'],
            'brand': [None, None, 'b', 'b']})
        def run(size):
            result = behavior.TransformResult()
            acc = {'customers': set(), 'products': {}}
            chunks = [behavior._map_chunk(src.iloc[i:i+size], '2019-Oct.csv', i,
                        DEMO_STORE_ID, 0, acc, result) for i in range(0, len(src), size)]
            return pd.concat(chunks), acc, result
        a, aa, ar = run(4)
        b, ba, br = run(2)
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(aa, ba)
        self.assertEqual(ar.dropped_by_source['2019-Oct.csv'], 1)
        self.assertEqual(list(a.event_type), ['view', 'purchase', 'remove_from_cart'])
        self.assertEqual(str(a.event_timestamp.iloc[0]), '2019-10-01 12:00:00+00:00')
        self.assertEqual(a.price.iloc[0], '1.234')

    def test_missing_month_fails(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            (Path(d) / '2019-Oct.csv').touch()
            with self.assertRaises(FileNotFoundError):
                behavior.resolve_raw_files(d)

    def test_retail_returns_precision_and_header_consistency(self):
        src = pd.DataFrame({'Invoice': ['1', '1'], 'StockCode': ['02', '02'],
            'Customer ID': ['003', '003'], 'Quantity': ['-1', '0'],
            'Price': ['1.2345', '2.3456'], 'InvoiceDate': ['2010-01-02 03:00'] * 2,
            'Description': ['x'] * 2, 'Country': ['UK'] * 2})
        result = retail.transform({'online_retail_II.csv': src}, DEMO_STORE_ID)
        self.assertEqual(len(result.frames['order_items']), 2)
        self.assertEqual(str(result.frames['order_items'].sales_value.iloc[0]), '-1.2345')
        self.assertEqual(str(result.frames['orders'].order_date.iloc[0]), '2010-01-02')
        self.assertEqual(result.frames['orders'].region.iloc[0], 'UK')
        self.assertEqual(validate_frames(result.frames)[0], [])
        src.loc[1, 'Customer ID'] = 'other'
        with self.assertRaises(ValueError):
            retail.transform({'online_retail_II.csv': src}, DEMO_STORE_ID)

    def test_repeated_redemptions_and_promotions_survive(self):
        raw = {'coupon_redempt.csv': pd.DataFrame({'household_key': ['1', '1'],
            'COUPON_UPC': ['02', '02'], 'CAMPAIGN': ['3', '3'], 'DAY': ['1', '2']}),
            'causal_data.csv': pd.DataFrame({'PRODUCT_ID': ['1', '1'], 'STORE_ID': ['2', '2'],
            'WEEK_NO': ['1', '1'], 'display': ['0', '9'], 'mailer': ['A', 'D']})}
        result = dunnhumby.TransformResult()
        customers = pd.DataFrame({'source_customer_id': ['dnb:1'], 'id': [uid('customers', 'dnb:1')]})
        campaigns = pd.DataFrame({'source_campaign_id': ['dnb:3'], 'id': [uid('coupon_campaigns', 'dnb:3')]})
        products = pd.DataFrame({'source_product_id': ['dnb:1'], 'id': [uid('products', 'dnb:1')]})
        red = dunnhumby._coupon_redemptions(raw, campaigns, customers, result)
        causal = dunnhumby._causal_data(raw, products, result)
        self.assertEqual(red.id.nunique(), 2)
        self.assertEqual(causal.id.nunique(), 2)
        self.assertEqual(list(causal.mailer), ['A', 'D'])

    def test_failed_publication_keeps_previous_export(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            output = Path(d) / 'dataset'
            output.mkdir()
            previous = output / 'previous.txt'
            previous.write_text('untouched')
            bad = canonical('customers', pd.DataFrame([{'id': 'bad', 'source_customer_id': 'cpn:1'}]))
            with self.assertRaises(ValueError):
                publish(output, {'stores': store_frame(), 'customers': bad}, [], {})
            self.assertEqual(previous.read_text(), 'untouched')
            self.assertEqual(len(list(Path(d).glob('.dataset-staging-*/exceptions/customers.csv'))), 1)

    def test_coupon_roster_dates_and_repeated_lines(self):
        raw = {
            'train.csv': pd.DataFrame({'id': ['1', '2'], 'campaign_id': ['1']*2,
                'coupon_id': ['1']*2, 'customer_id': ['train-only']*2, 'redemption_status': ['0', '1']}),
            'campaign_data.csv': pd.DataFrame({'campaign_id': ['1'], 'campaign_type': ['X'],
                'start_date': ['02/01/2013'], 'end_date': ['10/01/2013']}),
            'customer_transaction_data.csv': pd.DataFrame({'customer_id': ['01']*2, 'item_id': ['2']*2,
                'date': ['2013-01-04']*2, 'quantity': ['1', '0'], 'selling_price': ['2.1234', '0'],
                'coupon_discount': ['-1', '0'], 'other_discount': ['0', '0']}),
            'item_data.csv': pd.DataFrame({'item_id': ['2'], 'brand': ['brand'], 'category': ['c']}),
            'customer_demographics.csv': pd.DataFrame({'customer_id': ['demo-only']}),
            'coupon_item_mapping.csv': pd.DataFrame({'coupon_id': ['1'], 'item_id': ['2']}),
        }
        with patch('member_c.read', side_effect=lambda path: raw[Path(path).name]):
            frames, _, _ = transform_coupon('unused')
        self.assertEqual(len(frames['customers']), 3)
        self.assertEqual(len(frames['order_items']), 2)
        self.assertTrue(pd.isna(frames['order_items'].unit_price.iloc[1]))
        self.assertEqual(str(frames['coupon_campaigns'].start_date.iloc[0]), '2013-01-02')
        self.assertEqual(validate_frames(frames)[0], [])

    def test_campaign_type_preserves_reused_campaign_id(self):
        src = pd.DataFrame({'campaign_id': ['64', '64'], 'message_type': ['bulk', 'transactional'],
            'message_id': ['1', '2'], 'client_id': ['001', '001'], 'channel': ['email', 'email'],
            'sent_at': ['2021-01-02 12:00:00'] * 2, 'purchased_at': [None, '2021-01-03 12:00:00']})
        for name in ['is_opened', 'is_clicked', 'is_unsubscribed', 'is_hard_bounced',
                     'is_soft_bounced', 'is_complained', 'is_blocked', 'is_purchased']:
            src[name] = ['false', 'true']
        mapped = map_messages(src)
        campaigns = ids(campaign_keys(pd.Series(['64', '64']), pd.Series(['bulk', 'transactional'])), 'marketing_campaigns')
        self.assertEqual(mapped.campaign_id.nunique(), 2)
        self.assertEqual(list(mapped.campaign_id), list(campaigns))
        self.assertEqual(list(mapped.is_opened), [False, True])
        self.assertEqual(str(mapped.purchased_at.iloc[1]), '2021-01-03 12:00:00')

    def test_global_stream_duplicates_across_chunks(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            stage = Path(d)
            customer_id = uid('customers', 'evb:1')
            dims = {'stores': store_frame(), 'customers': canonical('customers', pd.DataFrame([{
                'id': customer_id, 'source_customer_id': 'evb:1'}]))}
            event = canonical('events', pd.DataFrame([{
                'id': uid('events', 'evb:file|0'), 'customer_id': customer_id,
                'session_id': 'evb:s', 'event_type': 'view',
                'event_timestamp': '2019-10-01T00:00:00+00:00'}]))
            path = stage / 'events.csv'
            pd.concat([event, event], ignore_index=True).to_csv(path, index=False)
            failures, count = validate_stream('events', path, dims, stage, chunk_size=1)
            self.assertEqual(count, 2)
            self.assertTrue(any('global key' in x for x in failures))

    def test_successful_publication_layout_and_backup(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            output = Path(d) / 'dataset'
            frames = {'stores': store_frame(), 'customers': canonical('customers', pd.DataFrame([{
                'id': uid('customers', 'chr:1'), 'source_customer_id': 'chr:1'}]))}
            manifest = publish(output, frames, [], {}, expected_counts={'customers': 1})
            self.assertTrue(manifest['validation']['ok'])
            self.assertTrue((output / 'customers' / 'customers.csv').is_file())
            publish(output, frames, [], {})
            self.assertEqual(len(list(Path(d).glob('.dataset-previous-*'))), 1)

    def test_native_line_timestamps_and_zero_quantity(self):
        raw = {'transaction_data.csv': pd.DataFrame({
            'BASKET_ID': ['1', '1'], 'household_key': ['1', '1'], 'PRODUCT_ID': ['1', '1'],
            'DAY': ['1', '1'], 'WEEK_NO': ['1', '1'], 'STORE_ID': ['001', '001'],
            'TRANS_TIME': ['0055', '0056'], 'QUANTITY': ['0', '-1'], 'SALES_VALUE': ['0', '-2.1234'],
            'RETAIL_DISC': ['0', '-0.1'], 'COUPON_DISC': ['0', '-0.2'], 'COUPON_MATCH_DISC': ['0', '0']})}
        customers = pd.DataFrame({'source_customer_id': ['dnb:1'], 'id': [uid('customers', 'dnb:1')]})
        products = pd.DataFrame({'source_product_id': ['dnb:1']})
        orders, items = dunnhumby._orders_and_items(raw, customers, products, dunnhumby.TransformResult())
        self.assertEqual(len(items), 2)
        self.assertTrue(pd.isna(items.unit_price.iloc[0]))
        self.assertEqual(orders.source_store_id.iloc[0], '001')
        self.assertTrue(pd.isna(orders.order_timestamp.iloc[0]))
        self.assertEqual(str(items.source_timestamp.iloc[0]), '2016-01-01 00:55:00')
        self.assertEqual(str(items.sales_value.iloc[1]), '-2.1234')

    def test_integer_csv_tokens_are_loadable_without_truncation(self):
        from io import StringIO
        frame = canonical('customers', pd.DataFrame({
            'id': [uid('customers', 'chr:1'), uid('customers', 'chr:2')],
            'source_customer_id': ['chr:1', 'chr:2'], 'tenure': ['4.0', None]}))
        csv = pd.read_csv(StringIO(frame.to_csv(index=False)), dtype=str, keep_default_na=False)
        self.assertEqual(list(csv.tenure), ['4', ''])
        invalid = canonical('customers', frame.assign(tenure=['4.5', None]))
        self.assertTrue(validate_frames({'stores': store_frame(), 'customers': invalid})[0])

    def test_header_only_promotions_return_empty_canonical_frame(self):
        raw = {'causal_data.csv': pd.DataFrame(
            columns=['PRODUCT_ID', 'STORE_ID', 'WEEK_NO', 'display', 'mailer'], dtype=str)}
        result = dunnhumby.TransformResult()
        frame = dunnhumby._causal_data(raw, pd.DataFrame(), result)
        self.assertTrue(frame.empty)
        self.assertEqual(list(frame.columns),
                         [c for c in dunnhumby.COLUMNS['causal_data'] if c != 'store_id'])
        self.assertEqual(result.raw_counts['causal_data.csv'], 0)
        self.assertEqual(result.dropped, {})

    def test_streamed_promotions_preserve_rows_across_chunk_sizes(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent) as d:
            root = Path(d)
            src = pd.DataFrame({'PRODUCT_ID': ['1'] * 3, 'STORE_ID': ['001'] * 3,
                'WEEK_NO': ['1'] * 3, 'display': ['0', '9', '9'], 'mailer': ['A', 'D', 'D']})
            src.to_csv(root / 'causal_data.csv', index=False)
            products = pd.DataFrame({'source_product_id': ['dnb:1'], 'id': [uid('products', 'dnb:1')]})
            outputs = []
            for size in (1, 3):
                result = dunnhumby.TransformResult()
                target = root / str(size) / 'causal_data' / 'causal_data.csv'
                count = dunnhumby.stream_causal(root, products, DEMO_STORE_ID, target, result, size)
                self.assertEqual(count, 3)
                self.assertEqual(result.raw_counts['causal_data.csv'], 3)
                outputs.append(pd.read_csv(target, dtype=str))
            pd.testing.assert_frame_equal(*outputs)
            self.assertEqual(outputs[0].id.nunique(), 3)
            self.assertEqual(list(outputs[0].source_store_id), ['001'] * 3)


if __name__ == '__main__':
    unittest.main()
