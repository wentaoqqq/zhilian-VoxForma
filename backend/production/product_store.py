"""Product-owned assignments over a shared, content-addressed media library.

Public store functions return dictionaries (or lists), and raise ValueError for
missing products/assets or invalid product-local references. Classification
and roles live exclusively on the association, never on media_assets metadata.
"""
from __future__ import annotations

import json
import time
import uuid

from core.db import db_conn
from production.product_models import Product

RESOURCE_ROLES = {'outro_ids': 'outro', 'material_ids': 'material', 'sticker_ids': 'sticker'}


class ProductBusyError(ValueError):
    """An active product task still needs its configuration and associations."""


def _product(db, product_id):
    row = db.execute('SELECT config_json FROM production_products WHERE id=?', (product_id,)).fetchone()
    if row is None:
        raise ValueError('Product not found.')
    data = {**json.loads(row['config_json']), 'id': product_id}
    for field, role in RESOURCE_ROLES.items():
        linked = [r['asset_id'] for r in db.execute(
            'SELECT asset_id FROM production_product_assets WHERE product_id=? AND role=? ORDER BY created_at,asset_id',
            (product_id, role),
        )]
        configured = data.get(field, [])
        data[field] = [asset_id for asset_id in configured if asset_id in linked]
        data[field].extend(asset_id for asset_id in linked if asset_id not in data[field])
    return data


def get_product(product_id: str) -> dict:
    with db_conn() as db:
        return _product(db, product_id)


def list_products() -> list[dict]:
    with db_conn() as db:
        return [_product(db, row['id']) for row in db.execute(
            'SELECT id FROM production_products ORDER BY updated_at DESC,id',
        ).fetchall()]


def _validate_assignment(product, video_type_id='', category_id=None, role='input'):
    if video_type_id and video_type_id not in {v['id'] for v in product['video_types']}:
        raise ValueError('Video type not found in this product.')
    if category_id is not None and category_id not in {c['id'] for c in product['categories']}:
        raise ValueError('Category not found in this product.')
    if role not in {'input', 'material', 'outro', 'sticker'}:
        raise ValueError('Invalid asset role.')


def validate_assignment(product_id: str, video_type_id='', category_id=None, role='input') -> None:
    _validate_assignment(get_product(product_id), video_type_id, category_id, role)


def save_product(product: Product | dict, product_id: str | None = None) -> dict:
    product = Product.model_validate(product)
    product_id = product_id or product.id or uuid.uuid4().hex
    config = product.model_dump(exclude={'id'})
    resources = {asset_id: role for field, role in RESOURCE_ROLES.items() for asset_id in config[field]}
    now = time.time()
    with db_conn() as db:
        # A failed configuration update must leave both config and roles intact.
        for asset_id in resources:
            if db.execute('SELECT 1 FROM media_assets WHERE id=?', (asset_id,)).fetchone() is None:
                raise ValueError('Media asset not found.')
        db.execute(
            'INSERT INTO production_products(id,config_json,created_at,updated_at) VALUES (?,?,?,?) '
            'ON CONFLICT(id) DO UPDATE SET config_json=excluded.config_json,updated_at=excluded.updated_at',
            (product_id, json.dumps(config), now, now),
        )
        categories = {c.id for c in product.categories}
        video_types = {v.id for v in product.video_types}
        for row in db.execute('SELECT * FROM production_product_assets WHERE product_id=?', (product_id,)).fetchall():
            category_id = row['category_id'] if row['category_id'] in categories else None
            classification = row['classification_status'] if row['category_id'] == category_id else 'pending'
            video_type_id = row['video_type_id'] if row['video_type_id'] in video_types else ''
            role = resources.get(row['asset_id'], 'input')
            db.execute(
                'UPDATE production_product_assets SET category_id=?,classification_status=?,video_type_id=?,role=?,updated_at=? '
                'WHERE product_id=? AND asset_id=?',
                (category_id, classification, video_type_id, role, now, product_id, row['asset_id']),
            )
        for asset_id, role in resources.items():
            db.execute(
                'INSERT OR IGNORE INTO production_product_assets(product_id,asset_id,role,created_at,updated_at) VALUES (?,?,?,?,?)',
                (product_id, asset_id, role, now, now),
            )
        return _product(db, product_id)


def delete_product(product_id: str) -> dict:
    with db_conn() as db:
        # Keep the active-task check and deletion in one write transaction.
        db.execute('BEGIN IMMEDIATE')
        _product(db, product_id)
        active = db.execute(
            "SELECT 1 FROM production_batches b JOIN jobs j ON j.id=b.id "
            "WHERE j.status IN ('pending','running') "
            "AND json_extract(b.request_json, '$.options.product_id')=? LIMIT 1",
            (product_id,),
        ).fetchone()
        if active:
            raise ProductBusyError('This product has active production tasks. Finish or cancel them before deleting it.')
        conversations = 'SELECT id FROM production_conversations WHERE product_id=?'
        db.execute(f'DELETE FROM production_messages WHERE conversation_id IN ({conversations})', (product_id,))
        db.execute('DELETE FROM production_conversations WHERE product_id=?', (product_id,))
        db.execute('DELETE FROM production_product_assets WHERE product_id=?', (product_id,))
        db.execute('DELETE FROM production_products WHERE id=?', (product_id,))
    # Shared media files and legacy brand/batch history remain intact.
    return {'deleted': product_id}


def _asset(db, product_id, asset_id):
    row = db.execute(
        'SELECT a.*,p.product_id,p.category_id,p.video_type_id,p.role,p.classification_status,'
        'p.description,p.tags_json FROM production_product_assets p '
        'JOIN media_assets a ON a.id=p.asset_id WHERE p.product_id=? AND p.asset_id=?',
        (product_id, asset_id),
    ).fetchone()
    if row is None:
        raise ValueError('Media asset not found in this product.')
    data = dict(row)
    metadata = json.loads(data.pop('metadata_json'))
    data['tags'] = json.loads(data.pop('tags_json'))
    # Scoped columns override any historical global ownership/classification.
    data = {**metadata, **data}
    data['use_count'] = db.execute(
        'SELECT COUNT(*) FROM production_asset_usage u JOIN production_batches b ON b.id=u.batch_id '
        "WHERE u.asset_id=? AND json_extract(b.request_json, '$.options.product_id')=?",
        (asset_id, product_id),
    ).fetchone()[0]
    return data


def get_asset(product_id: str, asset_id: str) -> dict:
    with db_conn() as db:
        _product(db, product_id)
        return _asset(db, product_id, asset_id)


def attach_asset(product_id: str, asset_id: str, video_type_id: str = '', category_id=None, role='input') -> dict:
    """Attach once, preserving classification and allowing explicit resource roles.

    Re-uploading an existing input never clears its assignment. Ingesting a
    clip with the same hash may promote it to material/outro/sticker; callers
    use update_asset for other changes or to turn a resource back into input.
    """
    with db_conn() as db:
        _validate_assignment(_product(db, product_id), video_type_id, category_id, role)
        if db.execute('SELECT 1 FROM media_assets WHERE id=?', (asset_id,)).fetchone() is None:
            raise ValueError('Media asset not found.')
        now = time.time()
        db.execute(
            'INSERT INTO production_product_assets'
            '(product_id,asset_id,video_type_id,category_id,role,created_at,updated_at) VALUES (?,?,?,?,?,?,?) '
            'ON CONFLICT(product_id,asset_id) DO UPDATE SET role=excluded.role,updated_at=excluded.updated_at '
            "WHERE excluded.role != 'input'",
            (product_id, asset_id, video_type_id, category_id, role, now, now),
        )
        return _asset(db, product_id, asset_id)


def update_asset(product_id: str, asset_id: str, **changes) -> dict:
    if set(changes) - {'category_id', 'video_type_id', 'role'}:
        raise ValueError('Invalid asset assignment field.')
    with db_conn() as db:
        product = _product(db, product_id)
        asset = _asset(db, product_id, asset_id)
        assignment = {key: changes.get(key, asset[key]) for key in ('category_id', 'video_type_id', 'role')}
        _validate_assignment(product, **assignment)
        classification = asset['classification_status']
        if 'category_id' in changes and assignment['category_id'] != asset['category_id']:
            classification = 'classified' if assignment['category_id'] is not None else 'pending'
        db.execute(
            'UPDATE production_product_assets SET category_id=?,video_type_id=?,role=?,classification_status=?,updated_at=? '
            'WHERE product_id=? AND asset_id=?',
            (assignment['category_id'], assignment['video_type_id'], assignment['role'], classification, time.time(), product_id, asset_id),
        )
        return _asset(db, product_id, asset_id)


def list_assets(product_id: str, category_id=None, video_type_id=None) -> list[dict]:
    with db_conn() as db:
        _product(db, product_id)
        where, params = ['product_id=?'], [product_id]
        if category_id is not None:
            where.append('category_id IS NULL' if category_id == '' else 'category_id=?')
            if category_id != '':
                params.append(category_id)
        if video_type_id is not None:
            where.append('video_type_id=?')
            params.append(video_type_id)
        rows = db.execute(
            'SELECT asset_id FROM production_product_assets WHERE ' + ' AND '.join(where) + ' ORDER BY created_at DESC,asset_id',
            params,
        ).fetchall()
        return [_asset(db, product_id, row['asset_id']) for row in rows]


def classify_asset(product_id: str, asset_id: str, category_id, description='', tags=None) -> dict:
    with db_conn() as db:
        _validate_assignment(_product(db, product_id), category_id=category_id)
        _asset(db, product_id, asset_id)
        db.execute(
            'UPDATE production_product_assets SET category_id=?,description=?,tags_json=?,classification_status=?,updated_at=? '
            'WHERE product_id=? AND asset_id=?',
            (category_id, description, json.dumps(tags if tags is not None else []),
             'classified' if category_id is not None else 'unclassified', time.time(), product_id, asset_id),
        )
        return _asset(db, product_id, asset_id)


def pending_assets(product_id: str, video_type_id: str) -> list[dict]:
    """Inputs eligible for production, including already-classified inputs.

    Batch snapshots use request.options.product_id/video_type_id and item
    source_id/status. Legacy brand-only batches never claim product inputs.
    """
    validate_assignment(product_id, video_type_id)
    with db_conn() as db:
        batches = db.execute(
            'SELECT b.request_json,b.items_json,j.status FROM production_batches b JOIN jobs j ON j.id=b.id',
        ).fetchall()
    excluded = set()
    for batch in batches:
        options = json.loads(batch['request_json']).get('options', {})
        if options.get('product_id') != product_id or options.get('video_type_id') != video_type_id:
            continue
        for item in json.loads(batch['items_json']):
            if item['status'] == 'done' or (batch['status'] in {'pending', 'running'} and item['status'] in {'pending', 'running'}):
                excluded.add(item['source_id'])
    return [a for a in list_assets(product_id, video_type_id=video_type_id) if a['role'] == 'input' and a['id'] not in excluded]
