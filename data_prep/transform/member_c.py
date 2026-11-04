"""Coupon, churn and messaging mappings; populations never share identities."""
from pathlib import Path
import json

import pandas as pd

from common import DEMO_STORE_ID, set_tenant, source_id, to_bool, uid
from contract import canonical, decimal_value, store_frame


def read(path):
    return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[''], low_memory=False)


def keys(values, namespace):
    return values.map(lambda v: source_id(v, namespace)).replace('', None)


def ids(values, table):
    # Factorize entity keys to avoid repeatedly hashing the same customer.
    mapping = {k: uid(table, k) for k in values.dropna().unique()}
    return values.map(mapping)


def campaign_keys(identifiers, types):
    """Source IDs are reused between bulk and transactional campaigns."""
    return pd.Series([
        'msg:' + json.dumps([source_id(kind, ''), source_id(key, '')], separators=(',', ':'))
        if source_id(key, '') and source_id(kind, '') else None
        for key, kind in zip(identifiers, types)
    ], index=identifiers.index)


def dates(values, *, dayfirst=False, date_only=False):
    parsed = pd.to_datetime(values, errors='coerce', format='mixed', dayfirst=dayfirst)
    invalid = values.notna() & parsed.isna()
    if invalid.any():
        raise ValueError(f'Unparseable source dates at rows {list(values.index[invalid][:10])}; no publication')
    return parsed.dt.date if date_only else parsed


def bools(values):
    parsed = values.map(to_bool)
    if (values.notna() & parsed.isna()).any():
        raise ValueError('Unknown boolean token; refusing truthiness conversion')
    return parsed


def customers(rosters, namespace):
    all_keys = pd.concat([keys(s, namespace) for s in rosters], ignore_index=True)
    out = pd.DataFrame({'source_customer_id': sorted(set(all_keys.dropna()))})
    out['id'] = ids(out['source_customer_id'], 'customers')
    return out


def finish(frames):
    return {t: canonical(t, f) for t, f in {'stores': store_frame(), **frames}.items()}


def transform_coupon(raw_dir):
    set_tenant(DEMO_STORE_ID)
    raw_dir = Path(raw_dir)
    names = ['train.csv', 'campaign_data.csv', 'coupon_item_mapping.csv',
             'customer_demographics.csv', 'customer_transaction_data.csv', 'item_data.csv']
    raw = {n: read(raw_dir / n) for n in names}
    train, campaign, tx, items = (raw[n] for n in ['train.csv', 'campaign_data.csv', 'customer_transaction_data.csv', 'item_data.csv'])
    cust = customers([train.customer_id, tx.customer_id, raw['customer_demographics.csv'].customer_id], 'coupon')
    campaigns = pd.DataFrame({'source_campaign_id': keys(campaign.campaign_id, 'coupon'),
        'campaign_type': campaign.campaign_type,
        'start_date': dates(campaign.start_date, dayfirst=True, date_only=True),
        'end_date': dates(campaign.end_date, dayfirst=True, date_only=True)})
    campaigns['id'] = ids(campaigns.source_campaign_id, 'coupon_campaigns')
    redemptions = pd.DataFrame({
        'id': ids(keys(train['id'], 'coupon'), 'coupon_redemptions'),
        'coupon_id': keys(train.coupon_id, 'coupon'),
        'campaign_id': ids(keys(train.campaign_id, 'coupon'), 'coupon_campaigns'),
        'customer_id': ids(keys(train.customer_id, 'coupon'), 'customers'),
        'redemption_status': bools(train.redemption_status)})
    products = pd.DataFrame({'source_product_id': keys(items.item_id, 'coupon'),
                             'brand': items.brand, 'category': items.category})
    products['id'] = ids(products.source_product_id, 'products')
    products['name'] = products.source_product_id  # explicit identifier label; no invented product name
    row_keys = pd.Series([f'cpn:customer_transaction_data.csv|{i}' for i in tx.index], index=tx.index)
    orders = pd.DataFrame({'source_order_id': row_keys,
        'id': ids(row_keys, 'orders'), 'customer_id': ids(keys(tx.customer_id, 'coupon'), 'customers'),
        'order_date': dates(tx.date, date_only=True)})
    lines = pd.DataFrame({'id': ids(row_keys, 'order_items'), 'order_id': orders.id,
        'product_id': ids(keys(tx.item_id, 'coupon'), 'products'), 'quantity': tx.quantity,
        'sales_value': tx.selling_price, 'coupon_disc': tx.coupon_discount,
        'other_discount': tx.other_discount, 'retail_disc': 0, 'coupon_match_disc': 0})
    # Only derive per-unit source selling price. The source amount remains authoritative.
    lines['unit_price'] = [s / q if s is not None and q not in (None, 0) else None
                          for s, q in zip(tx.selling_price.map(decimal_value), tx.quantity.map(decimal_value))]
    frames = finish({'customers': cust, 'products': products, 'orders': orders,
                     'order_items': lines, 'coupon_campaigns': campaigns, 'coupon_redemptions': redemptions})
    return frames, raw, {
        'coupon_item_mapping': raw['coupon_item_mapping.csv'],
        'transaction_source': tx.assign(source_row=tx.index, canonical_order_id=orders.id, canonical_item_id=lines.id),
    }


def transform_churn(raw_dir):
    set_tenant(DEMO_STORE_ID)
    src = read(Path(raw_dir) / 'data_ecommerce_customer_churn.csv')
    out = pd.DataFrame({'source_customer_id': [f'chr:data_ecommerce_customer_churn.csv|{i}' for i in src.index]})
    out['id'] = ids(out.source_customer_id, 'customers')
    mapping = {'Tenure': 'tenure', 'WarehouseToHome': 'warehouse_to_home',
        'NumberOfDeviceRegistered': 'number_of_devices_registered', 'PreferedOrderCat': 'preferred_order_category',
        'SatisfactionScore': 'satisfaction_score', 'DaySinceLastOrder': 'days_since_last_order',
        'CashbackAmount': 'cashback_amount'}
    for source, target in mapping.items():
        out[target] = src[source]
    out['complain'] = bools(src.Complain)
    out['churn'] = bools(src.Churn)
    # No source identity or observation date exists. Stable physical row identity,
    # native snapshot features, and NULL first_purchase_date preserve that limitation.
    return finish({'customers': out}), src


def messaging_dimensions(raw_dir):
    set_tenant(DEMO_STORE_ID)
    raw_dir = Path(raw_dir)
    campaign = read(raw_dir / 'campaigns.csv')
    first = read(raw_dir / 'client_first_purchase_date.csv')
    holiday = read(raw_dir / 'holidays.csv')
    campaigns = pd.DataFrame({'source_campaign_id': campaign_keys(campaign.id, campaign.campaign_type)})
    campaigns['id'] = ids(campaigns.source_campaign_id, 'marketing_campaigns')
    for col in ['campaign_type', 'channel', 'topic', 'total_count']:
        campaigns[col] = campaign[col]
    for col in ['started_at', 'finished_at']:
        campaigns[col] = dates(campaign[col])
    cust = pd.DataFrame({'source_customer_id': keys(first.client_id, 'messaging'),
                         'first_purchase_date': dates(first.first_purchase_date, date_only=True)})
    cust['id'] = ids(cust.source_customer_id, 'customers')
    # Duplicate source identities must be investigated, not arbitrarily collapsed.
    holidays = pd.DataFrame({'date': dates(holiday.date, date_only=True), 'holiday': holiday.holiday})
    holidays['id'] = [uid('holidays', f'msg:holidays.csv|{i}') for i in holiday.index]
    return finish({'customers': cust, 'marketing_campaigns': campaigns, 'holidays': holidays}), {
        'campaigns.csv': len(campaign), 'client_first_purchase_date.csv': len(first), 'holidays.csv': len(holiday)}


def map_messages(src):
    out = pd.DataFrame({'source_message_id': keys(src.message_id, 'messaging'),
        'campaign_id': ids(campaign_keys(src.campaign_id, src.message_type), 'marketing_campaigns'),
        'customer_id': ids(keys(src.client_id, 'messaging'), 'customers')})
    out['id'] = ids(out.source_message_id, 'message_sends')
    for col in ['message_type', 'channel']:
        out[col] = src[col]
    out['channel_norm'] = src.channel.map({'email': 'email', 'sms': 'sms',
        'web_push': 'push', 'mobile_push': 'push', 'whatsapp': 'whatsapp'})
    for col in ['sent_at', 'purchased_at']:
        out[col] = dates(src[col])
    for col in ['is_opened', 'is_clicked', 'is_unsubscribed', 'is_hard_bounced',
                'is_soft_bounced', 'is_complained', 'is_blocked', 'is_purchased']:
        out[col] = bools(src[col])
    return canonical('message_sends', out)


def transform_messaging(raw_dir, stage, chunk_size=250_000):
    frames, counts = messaging_dimensions(raw_dir)
    target = Path(stage) / 'message_sends' / 'message_sends.csv'
    target.parent.mkdir(parents=True, exist_ok=True)
    known = set(frames['customers'].source_customer_id.dropna())
    added = set()
    count = 0
    for chunk in pd.read_csv(Path(raw_dir) / 'messages-demo.csv', dtype=str,
                             keep_default_na=False, na_values=[''], chunksize=chunk_size):
        added.update(set(keys(chunk.client_id, 'messaging').dropna()) - known)
        mapped = map_messages(chunk)
        mapped.to_csv(target, mode='w' if count == 0 else 'a', header=count == 0, index=False)
        count += len(chunk)
    if count == 0:
        canonical('message_sends', pd.DataFrame()).to_csv(target, index=False)
    extra = pd.DataFrame({'source_customer_id': sorted(added)})
    extra['id'] = ids(extra.source_customer_id, 'customers')
    frames['customers'] = pd.concat([frames['customers'], canonical('customers', extra)], ignore_index=True)
    counts['messages-demo.csv'] = count
    return frames, counts, target
