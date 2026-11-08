import json
import time
import uuid
from core.db import db_conn
from production.batch_models import Brand


def list_brands():
    with db_conn() as db:
        rows = db.execute('SELECT * FROM production_brands ORDER BY updated_at DESC').fetchall()
    return [{'id': row['id'], **json.loads(row['config_json'])} for row in rows]


def get_brand(brand_id):
    with db_conn() as db:
        row = db.execute('SELECT config_json FROM production_brands WHERE id=?', (brand_id,)).fetchone()
    if not row:
        raise ValueError('Brand not found. Configure a brand once before starting production.')
    return {'id': brand_id, **json.loads(row[0])}


def save_brand(brand: Brand, brand_id=None):
    brand_id = brand_id or uuid.uuid4().hex
    with db_conn() as db:
        db.execute('INSERT INTO production_brands(id,config_json,created_at,updated_at) VALUES (?,?,?,?) ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at', (brand_id, brand.model_dump_json(), time.time(), time.time()))
    return get_brand(brand_id)


def get_batch(batch_id):
    with db_conn() as db:
        row = db.execute('SELECT b.*,j.status,j.error,j.created_at,j.updated_at FROM production_batches b JOIN jobs j ON j.id=b.id WHERE b.id=?', (batch_id,)).fetchone()
    if not row:
        raise ValueError('Batch not found.')
    data = dict(row)
    for key in ['request', 'items', 'log']:
        data[key] = json.loads(data.pop(key + '_json'))
    return data


def update_items(batch_id, items):
    with db_conn() as db:
        db.execute('UPDATE production_batches SET items_json=? WHERE id=?', (json.dumps(items, ensure_ascii=False), batch_id))


def log(batch_id, message, kind='agent'):
    from core.scrub import scrub_text
    with db_conn() as db:
        row = db.execute('SELECT log_json FROM production_batches WHERE id=?', (batch_id,)).fetchone()
        events = json.loads(row[0])
        events.append({'time': time.time(), 'kind': kind, 'text': scrub_text(str(message))[:12000]})
        db.execute('UPDATE production_batches SET log_json=? WHERE id=?', (json.dumps(events[-500:], ensure_ascii=False), batch_id))
        db.execute('UPDATE jobs SET updated_at=? WHERE id=?', (time.time(), batch_id))


def update_item(batch_id, source_id, changes):
    with db_conn() as db:
        items = json.loads(db.execute('SELECT items_json FROM production_batches WHERE id=?', (batch_id,)).fetchone()[0])
        item = next(i for i in items if i['source_id'] == source_id)
        item.update(changes)
        db.execute('UPDATE production_batches SET items_json=? WHERE id=?', (json.dumps(items, ensure_ascii=False), batch_id))
