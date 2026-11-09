"""Product-scoped library and API business regressions; no model downloads."""
import json
from pathlib import Path
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.dependencies import require_consumer
from api.routers import production as api
from core import db
from production import store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(db, 'DB_PATH', str(tmp_path / 'studio.db'))
    monkeypatch.setattr(store, 'ROOT', tmp_path / 'media')
    db.ensure_schema()
    app = FastAPI()
    app.dependency_overrides[require_consumer] = lambda: None
    app.include_router(api.router)
    # No app lifespan/model startup is needed for library requests.
    with TestClient(app) as client:
        yield client


def product(client, **fields):
    response = client.post('/production/products', json={'name': 'Product', **fields})
    assert response.status_code == 200, response.text
    return response.json()


def upload(client, product, text=b'A complete script.', **params):
    response = client.post('/production/assets', params={
        'filename': 'script.txt', 'product_id': product['id'], **params,
    }, content=text)
    assert response.status_code == 200, response.text
    return response.json()


def test_product_crud_and_custom_taxonomy(client):
    p = product(client)
    assert p['categories'] == []
    assert [(t['name'], t['workflow']) for t in p['video_types']] == [
        ('Product', 'product'), ('Talking head', 'talking_head'), ('Narration', 'narration'),
    ]
    p['categories'] = [{'id': 'details', 'name': 'Details', 'description': 'Close views'}]
    p['video_types'] = [{'id': 'launch', 'name': 'Launch', 'workflow': 'product', 'instruction': 'Show details.'}]
    updated = client.put('/production/products/' + p['id'], json=p)
    assert updated.status_code == 200, updated.text
    assert updated.json()['categories'] == p['categories']
    assert client.get('/production/products').json() == [updated.json()]
    assert client.delete('/production/products/' + p['id']).status_code == 200
    assert client.get('/production/products').json() == []
    assert client.put('/production/products/missing', json={'name': 'No'}).status_code == 404


def test_same_hash_has_independent_product_assignments(client):
    a = product(client, categories=[{'id': 'a', 'name': 'A'}])
    b = product(client, categories=[{'id': 'b', 'name': 'B'}])
    asset = upload(client, a, video_type_id='product')
    other = upload(client, b, video_type_id='narration')
    assert asset['id'] == other['id']
    assert asset['product_id'] == a['id'] and other['product_id'] == b['id']
    changed = client.put(f"/production/products/{a['id']}/assets/{asset['id']}", json={'category_id': 'a'})
    assert changed.status_code == 200, changed.text
    assert changed.json()['category_id'] == 'a'
    assert client.get('/production/assets', params={'product_id': b['id']}).json()[0]['category_id'] is None
    assert client.get('/production/assets', params={'product_id': a['id'], 'video_type_id': 'narration'}).json() == []
    assert client.get('/production/assets', params={'product_id': a['id'], 'category_id': 'a'}).json()[0]['id'] == asset['id']
    assert client.delete('/production/products/' + a['id']).status_code == 200
    assert store.asset_path(asset['id']).is_file()
    assert client.get('/production/assets', params={'product_id': b['id']}).json()[0]['id'] == asset['id']


def test_deleting_category_preserves_file_and_returns_to_pending_classification(client):
    from production import product_store
    p = product(client, categories=[{'id': 'detail', 'name': 'Details'}])
    asset = upload(client, p, video_type_id='product')
    classified = product_store.classify_asset(p['id'], asset['id'], 'detail', 'A detail', ['close'])
    assert classified['classification_status'] == 'classified'
    assert product_store.pending_assets(p['id'], 'product')[0]['id'] == asset['id']
    p['categories'] = []
    assert client.put('/production/products/' + p['id'], json=p).status_code == 200
    remaining = product_store.get_asset(p['id'], asset['id'])
    assert remaining['category_id'] is None and remaining['classification_status'] == 'pending'
    assert store.asset_path(asset['id']).is_file()


def batch(product_id, video_type_id, assets, status='done', item_status='done', **options):
    import uuid
    batch_id = uuid.uuid4().hex
    now = time.time()
    with db.db_conn() as conn:
        conn.execute('INSERT INTO jobs(id,type,status,created_at,updated_at) VALUES (?,?,?,?,?)',
                     (batch_id, 'production-batch', status, now, now))
        conn.execute('INSERT INTO production_batches(id,request_json,items_json) VALUES (?,?,?)', (
            batch_id,
            json.dumps({'options': {'product_id': product_id, 'video_type_id': video_type_id, **options}}),
            json.dumps([{'source_id': a['id'], 'status': item_status} for a in assets]),
        ))
    return batch_id


def test_pending_excludes_deliveries_and_active_inputs_only_within_scope(client):
    from production import product_store
    p = product(client, categories=[{'id': 'detail', 'name': 'Details'}])
    a, b, c, d = [upload(client, p, text=f'Script {i}'.encode(), video_type_id='product') for i in range(4)]
    product_store.classify_asset(p['id'], c['id'], 'detail')
    batch(p['id'], 'product', [a])
    batch(p['id'], 'product', [b], 'running', 'pending')
    batch(p['id'], 'product', [c], 'failed', 'failed')
    batch('other-product', 'product', [d])
    batch(p['id'], 'narration', [d])
    assert {a['id'] for a in product_store.pending_assets(p['id'], 'product')} == {c['id'], d['id']}
    assert client.put(f"/production/products/{p['id']}/assets/{d['id']}", json={'role': 'material'}).status_code == 200
    assert [a['id'] for a in product_store.pending_assets(p['id'], 'product')] == [c['id']]
    assert product_store.get_product(p['id'])['material_ids'] == [d['id']]


def test_invalid_cross_product_references_and_partial_update(client):
    p = product(client, categories=[{'id': 'local', 'name': 'Local'}])
    q = product(client)
    asset = upload(client, p, video_type_id='product')
    path = f"/production/products/{p['id']}/assets/{asset['id']}"
    assert client.put(path, json={'category_id': 'foreign'}).status_code == 400
    assert client.put(path, json={'video_type_id': 'foreign'}).status_code == 400
    assert client.put(path, json={'role': 'foreign'}).status_code == 422
    assert client.put(f"/production/products/{q['id']}/assets/{asset['id']}", json={'role': 'input'}).status_code == 404
    assert client.put(path, json={'category_id': 'local'}).json()['video_type_id'] == 'product'
    assert client.put(path, json={'category_id': None}).json()['category_id'] is None
    assert client.post('/production/assets', params={'filename': 'x.txt', 'product_id': p['id'], 'video_type_id': 'foreign'}, content=b'Bad').status_code == 400
    assert client.get('/production/assets', params={'product_id': 'missing'}).status_code == 404


def test_legacy_brands_and_unscoped_uploads_stay_separate(client):
    brand = client.post('/production/brands', json={'name': 'Historical brand'})
    assert brand.status_code == 200
    assert client.get('/production/products').json() == []
    legacy = client.post('/production/assets', params={'filename': 'old.txt'}, content=b'Old script')
    assert legacy.status_code == 200
    p = product(client)
    assert client.get('/production/assets', params={'product_id': p['id']}).json() == []
    assert client.get('/production/brands').json()[0]['name'] == 'Historical brand'
    assert client.get('/production/assets').json()[0]['id'] == legacy.json()['id']


def test_resource_lists_and_roles_stay_consistent_and_save_is_atomic(client):
    from production import product_store
    p = product(client)
    asset = upload(client, p)
    p['material_ids'] = [asset['id']]
    assert client.put('/production/products/' + p['id'], json=p).status_code == 200
    assert product_store.get_asset(p['id'], asset['id'])['role'] == 'material'
    path = f"/production/products/{p['id']}/assets/{asset['id']}"
    assert client.put(path, json={'role': 'outro'}).status_code == 200
    current = product_store.get_product(p['id'])
    assert current['outro_ids'] == [asset['id']] and current['material_ids'] == []
    current['outro_ids'] = []
    assert client.put('/production/products/' + p['id'], json=current).status_code == 200
    assert product_store.get_asset(p['id'], asset['id'])['role'] == 'input'
    current['material_ids'] = ['missing']
    assert client.put('/production/products/' + p['id'], json=current).status_code == 400
    assert product_store.get_product(p['id'])['material_ids'] == []
    assert product_store.get_asset(p['id'], asset['id'])['role'] == 'input'
    # Saving a resource that exists globally also attaches it to this product.
    q = product(client, sticker_ids=[asset['id']])
    assert product_store.get_asset(q['id'], asset['id'])['role'] == 'sticker'


def test_classification_metadata_is_scoped_and_reuploads_preserve_assignments(client):
    from production import product_store
    p = product(client, categories=[{'id': 'detail', 'name': 'Details'}])
    q = product(client)
    asset = upload(client, p, video_type_id='product')
    upload(client, q, video_type_id='narration')
    with db.db_conn() as conn:
        # Historical metadata must never override current association fields.
        metadata = json.loads(conn.execute('SELECT metadata_json FROM media_assets WHERE id=?', (asset['id'],)).fetchone()[0])
        metadata.update(product_id='legacy', category_id='legacy', video_type_id='legacy', role='outro',
                        classification_status='classified', description='legacy', tags=['legacy'])
        conn.execute('UPDATE media_assets SET metadata_json=? WHERE id=?', (json.dumps(metadata), asset['id']))
    product_store.classify_asset(p['id'], asset['id'], 'detail', 'Specific detail', ['close'])
    repeated = upload(client, p, video_type_id='product')
    assert repeated['category_id'] == 'detail' and repeated['description'] == 'Specific detail'
    assert repeated['tags'] == ['close']
    other = product_store.get_asset(q['id'], asset['id'])
    assert other['category_id'] is None and other['role'] == 'input' and other['description'] == ''
    assert other['tags'] == [] and other['classification_status'] == 'pending'
    assert product_store.classify_asset(q['id'], asset['id'], None)['classification_status'] == 'unclassified'
    assert store.get_asset(asset['id'])['description'] == 'legacy'
    assert product_store.get_asset(p['id'], asset['id'])['classification_status'] == 'classified'


def test_every_video_type_accepts_all_supported_media_kinds(client, monkeypatch):
    import io
    from PIL import Image
    p = product(client)
    image = io.BytesIO()
    Image.new('RGB', (4, 4), 'blue').save(image, format='PNG')

    async def probe(*args, **kwargs):
        return {'kind': 'video', 'duration': 1, 'width': 4, 'height': 4, 'has_audio': True}

    monkeypatch.setattr(api, 'probe', probe)
    for video_type in p['video_types']:
        for filename, payload in [('clip.mp4', b'probe fixture'), ('frame.png', image.getvalue()), ('script.txt', b'Full script')]:
            result = client.post('/production/assets', params={
                'filename': filename, 'product_id': p['id'], 'video_type_id': video_type['id'],
            }, content=payload + (video_type['id'].encode() if filename == 'script.txt' else b''))
            assert result.status_code == 200, result.text


def test_validation_and_removed_video_type_preserve_assets(client):
    from production import product_store
    assert client.post('/production/products', json={'name': '   '}).status_code == 422
    assert client.post('/production/products', json={
        'name': 'P', 'categories': [{'id': 'x', 'name': 'A'}, {'id': 'x', 'name': 'B'}],
    }).status_code == 422
    assert client.post('/production/products', json={
        'name': 'P', 'video_types': [{'id': 'bad', 'name': 'A', 'workflow': 'intake'}],
    }).status_code == 422
    p = product(client)
    asset = upload(client, p, video_type_id='product')
    p['video_types'] = []
    assert client.put('/production/products/' + p['id'], json=p).status_code == 200
    assert product_store.get_asset(p['id'], asset['id'])['video_type_id'] == ''
    assert store.asset_path(asset['id']).is_file()


def test_chat_schema_and_product_delete_scope(client):
    p, q = product(client), product(client)
    with db.db_conn() as conn:
        for index, prod in enumerate((p, q)):
            conn.execute('INSERT INTO production_conversations VALUES (?,?,?,?,?,?)',
                         (str(index), prod['id'], 'product', 'Conversation', 1., 1.))
            conn.execute('INSERT INTO production_messages VALUES (?,?,?,?,?,?,?)',
                         (str(index), str(index), 'user', 'Start', None, 'key' + str(index), 1.))
    assert client.delete('/production/products/' + p['id']).status_code == 200
    with db.db_conn() as conn:
        assert [r['id'] for r in conn.execute('SELECT id FROM production_conversations')] == ['1']
        assert [r['id'] for r in conn.execute('SELECT id FROM production_messages')] == ['1']


def test_upgrade_from_current_head_preserves_history_and_matches_fresh_schema(tmp_path, monkeypatch):
    import sqlite3
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from production.store_schema import PRODUCT_WORKSPACE_SCHEMA

    path = tmp_path / 'legacy.db'
    monkeypatch.setattr(db, 'DB_PATH', str(path))
    # Exact previous runtime schema, stamped at its actual Alembic head.
    with sqlite3.connect(path) as conn:
        conn.executescript(db._BASE_SCHEMA.replace(PRODUCT_WORKSPACE_SCHEMA, ''))
        conn.execute('INSERT INTO production_brands VALUES (?,?,?,?)', ('legacy', '{"name":"Historical"}', 1., 1.))
        conn.execute('INSERT INTO media_assets VALUES (?,?,?,?,?)', ('a' * 64, 'a' * 64, 'old.txt', '{"kind":"text"}', 1.))
    cfg = Config(str(Path(__file__).resolve().parents[2] / 'alembic.ini'))
    cfg.set_main_option('sqlalchemy.url', 'sqlite:///' + str(path))
    cfg.attributes['configure_logger'] = False
    assert ScriptDirectory.from_config(cfg).get_heads() == ['0020_product_workspace']
    command.stamp(cfg, '0019_production_agent')
    command.upgrade(cfg, 'head')
    tables = ('production_products', 'production_product_assets', 'production_conversations', 'production_messages')
    with db.db_conn() as conn:
        upgraded = {table: [tuple(r) for r in conn.execute(f'PRAGMA table_info({table})')] for table in tables}
        assert conn.execute('SELECT COUNT(*) FROM production_products').fetchone()[0] == 0
        assert conn.execute('SELECT COUNT(*) FROM production_brands').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM media_assets').fetchone()[0] == 1
    fresh_path = tmp_path / 'fresh.db'
    monkeypatch.setattr(db, 'DB_PATH', str(fresh_path))
    db.ensure_schema()
    with db.db_conn() as conn:
        assert upgraded == {table: [tuple(r) for r in conn.execute(f'PRAGMA table_info({table})')] for table in tables}
    # Runtime creation followed by upgrade is idempotent too.
    cfg.set_main_option('sqlalchemy.url', 'sqlite:///' + str(fresh_path))
    command.stamp(cfg, '0019_production_agent')
    command.upgrade(cfg, 'head')
    cfg.set_main_option('sqlalchemy.url', 'sqlite:///' + str(path))
    command.downgrade(cfg, '0019_production_agent')
    with sqlite3.connect(path) as conn:
        assert conn.execute('SELECT COUNT(*) FROM media_assets').fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM production_brands').fetchone()[0] == 1
    command.upgrade(cfg, 'head')


def test_shared_hash_usage_counts_are_scoped_to_batch_product(client):
    from production import product_store
    p, q = product(client), product(client)
    asset = upload(client, p)
    assert upload(client, q)['id'] == asset['id']
    batches = [
        batch(p['id'], 'product', [asset]),
        batch(p['id'], 'narration', [asset]),
        batch(q['id'], 'product', [asset]),
        batch(None, 'product', [asset], brand_id=p['id']),
    ]
    with db.db_conn() as conn:
        for bid in batches:
            conn.execute('INSERT INTO production_asset_usage(batch_id,source_id,asset_id) VALUES (?,?,?)',
                         (bid, asset['id'], asset['id']))
    assert product_store.get_asset(p['id'], asset['id'])['use_count'] == 2
    assert product_store.get_asset(q['id'], asset['id'])['use_count'] == 1
    assert client.get('/production/assets', params={'product_id': p['id']}).json()[0]['use_count'] == 2
    assert client.get('/production/assets', params={'product_id': q['id']}).json()[0]['use_count'] == 1
    assert store.get_asset(asset['id'])['use_count'] == 4


@pytest.mark.parametrize('status', ['pending', 'running'])
def test_delete_product_rejects_its_active_batches_without_removing_data(client, status):
    from production import product_store
    p, q = product(client), product(client)
    asset = upload(client, p)
    upload(client, q)
    # Include conversations without inputs: they still require product data.
    active = batch(p['id'], 'narration', [], status=status)
    with db.db_conn() as conn:
        conn.execute('INSERT INTO production_conversations VALUES (?,?,?,?,?,?)',
                     ('conversation', p['id'], 'narration', 'Discussion', 1., 1.))
        conn.execute('INSERT INTO production_messages VALUES (?,?,?,?,?,?,?)',
                     ('message', 'conversation', 'user', 'Discuss', active, None, 1.))
    with pytest.raises(ValueError, match='active'):
        product_store.delete_product(p['id'])
    response = client.delete('/production/products/' + p['id'])
    assert response.status_code == 409, response.text
    assert product_store.get_asset(p['id'], asset['id'])['id'] == asset['id']
    with db.db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM production_conversations WHERE product_id=?', (p['id'],)).fetchone()[0] == 1
        assert conn.execute('SELECT COUNT(*) FROM production_messages WHERE id=?', ('message',)).fetchone()[0] == 1
        conn.execute("UPDATE jobs SET status='done' WHERE id=?", (active,))
    assert client.delete('/production/products/' + p['id']).status_code == 200
    assert product_store.get_asset(q['id'], asset['id'])['id'] == asset['id']
    assert store.asset_path(asset['id']).is_file()


@pytest.mark.parametrize('status', ['done', 'failed', 'cancelled'])
def test_delete_product_allows_terminal_batches_and_ignores_other_product_activity(client, status):
    p, q = product(client), product(client)
    asset = upload(client, p)
    batch(p['id'], 'product', [asset], status=status)
    batch(q['id'], 'product', [], status='running')
    batch(None, 'product', [], status='running', brand_id=p['id'])
    assert client.delete('/production/products/' + p['id']).status_code == 200
    assert store.asset_path(asset['id']).is_file()


def test_existing_media_can_be_assigned_without_upload_copy_or_generation(client):
    legacy = client.post('/production/assets', params={'filename': 'legacy.txt'}, content=b'Existing content.').json()
    path = store.asset_path(legacy['id'])
    original = (path.stat().st_mtime_ns, path.read_bytes())
    assert [a['id'] for a in client.get('/production/assets?unassigned=true').json()] == [legacy['id']]
    p = product(client, categories=[{'id': 'detail', 'name': 'Details'}])
    response = client.post(f"/production/products/{p['id']}/assets/{legacy['id']}", json={'video_type_id': 'narration'})
    assert response.status_code == 200, response.text
    assert response.json()['classification_status'] == 'pending'
    assert response.json()['video_type_id'] == 'narration'
    assert client.get('/production/assets?unassigned=true').json() == []
    assert (path.stat().st_mtime_ns, path.read_bytes()) == original
    client.put(f"/production/products/{p['id']}/assets/{legacy['id']}", json={'category_id': 'detail'})
    repeated = client.post(f"/production/products/{p['id']}/assets/{legacy['id']}", json={'video_type_id': 'narration'})
    assert repeated.json()['category_id'] == 'detail'
    with db.db_conn() as conn:
        assert conn.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0


def test_existing_media_assignment_rejects_invalid_scope_without_claiming_file(client):
    legacy = client.post('/production/assets', params={'filename': 'legacy.txt'}, content=b'Existing content.').json()
    p = product(client)
    result = client.post(f"/production/products/{p['id']}/assets/{legacy['id']}", json={'video_type_id': 'missing'})
    assert result.status_code == 400
    assert len(client.get('/production/assets?unassigned=true').json()) == 1
    assert client.get('/production/assets', params={'unassigned': True, 'product_id': p['id']}).status_code == 400
