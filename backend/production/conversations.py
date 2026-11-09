"""Durable product/video-type conversations backed by the execution agent."""
import asyncio
import hashlib
import time
import uuid

from core.db import db_conn
from production import batch_store

_locks = {}


def lock(conversation_id):
    return _locks.setdefault(conversation_id, asyncio.Lock())


def create(product_id, video_type_id, title=''):
    from production import product_store
    product = product_store.get_product(product_id)
    if video_type_id not in {t['id'] for t in product['video_types']}:
        raise ValueError('This video type does not belong to the product.')
    cid = uuid.uuid4().hex
    now = time.time()
    with db_conn() as db:
        db.execute('INSERT INTO production_conversations(id,product_id,video_type_id,title,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                   (cid, product_id, video_type_id, title.strip()[:200] or product['name'], now, now))
    return get(cid)


def list_conversations(product_id=None, video_type_id=None):
    where, values = [], []
    for key, value in [('product_id', product_id), ('video_type_id', video_type_id)]:
        if value:
            where.append(key + '=?')
            values.append(value)
    query = 'SELECT * FROM production_conversations'
    if where:
        query += ' WHERE ' + ' AND '.join(where)
    with db_conn() as db:
        return [dict(r) for r in db.execute(query + ' ORDER BY updated_at DESC LIMIT 100', values).fetchall()]


def get(cid):
    with db_conn() as db:
        row = db.execute('SELECT * FROM production_conversations WHERE id=?', (cid,)).fetchone()
        if not row:
            raise ValueError('Conversation not found.')
        messages = [dict(r) for r in db.execute('SELECT * FROM production_messages WHERE conversation_id=? ORDER BY created_at,id', (cid,)).fetchall()]
        ids = db.execute('SELECT DISTINCT batch_id FROM production_messages WHERE conversation_id=? AND batch_id IS NOT NULL ORDER BY created_at', (cid,)).fetchall()
    result = dict(row)
    result['messages'] = messages
    result['batches'] = [batch_store.get_batch(r[0]) for r in ids]
    return result


def append(cid, role, text, batch_id=None, request_key=None, message_id=None):
    from core.scrub import scrub_text
    mid = message_id or uuid.uuid4().hex
    now = time.time()
    with db_conn() as db:
        db.execute('INSERT INTO production_messages(id,conversation_id,role,text,batch_id,request_key,created_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET text=excluded.text',
                   (mid, cid, role, scrub_text(str(text)) if role != 'user' else text, batch_id, request_key, now))
        db.execute('UPDATE production_conversations SET updated_at=? WHERE id=?', (now, cid))
    return mid


async def send(cid, text, source_ids=None, request_id=None):
    from production import agent, product_store
    from production.batch_models import BatchRequest
    async with lock(cid):
        current = get(cid)
        key = hashlib.sha256((cid + ':' + request_id).encode()).hexdigest() if request_id else None
        if key:
            with db_conn() as db:
                if db.execute('SELECT 1 FROM production_messages WHERE request_key=?', (key,)).fetchone():
                    return get(cid)
        product = product_store.get_product(current['product_id'])
        video_type = next((t for t in product['video_types'] if t['id'] == current['video_type_id']), None)
        if not video_type:
            raise ValueError('This video type was removed. Choose another video type and start a new conversation.')
        if source_ids is None:
            source_ids = [a['id'] for a in product_store.pending_assets(product['id'], video_type['id'])]
        sources = [product_store.get_asset(product['id'], aid) for aid in dict.fromkeys(source_ids)]
        if any(a.get('video_type_id') != video_type['id'] or a.get('role') != 'input' for a in sources):
            raise ValueError('Choose pending inputs from this product and video type.')
        # Keep idempotency scoped to the conversation, never to another client's turn.
        request = BatchRequest(product_id=product['id'], video_type_id=video_type['id'], conversation_id=cid,
                               workflow=video_type['workflow'], source_ids=[a['id'] for a in sources],
                               instruction=text, request_id=key)
        # Validation occurs before the visible user turn is saved. A failed enqueue
        # removes only this turn, so retry cannot become a phantom success.
        agent.preflight(request, product)
        batch_id = uuid.uuid4().hex
        mid = append(cid, 'user', text, batch_id=batch_id, request_key=key)
        try:
            await agent.submit(request, product_snapshot=product, batch_id=batch_id)
        except Exception:
            with db_conn() as db:
                db.execute('DELETE FROM production_messages WHERE id=?', (mid,))
            raise
        return get(cid)


def history(cid, batch_id):
    """Only preceding turns; queued future messages must not leak into this run."""
    current = get(cid)
    turns = []
    for message in current['messages']:
        if message['role'] != 'user':
            continue
        turns.append(message['batch_id'])
        if message['batch_id'] == batch_id:
            break
    messages = []
    for bid in turns:
        for message in current['messages']:
            if message['batch_id'] == bid and (bid != batch_id or message['role'] == 'user'):
                messages.append({k: message[k] for k in ['role', 'text', 'batch_id']})
    return messages


def previous_session(cid, batch_id):
    current = get(cid)
    previous = []
    for message in current['messages']:
        if message['role'] != 'user':
            continue
        if message['batch_id'] == batch_id:
            break
        previous.append(message['batch_id'])
    with db_conn() as db:
        for bid in reversed(previous):
            row = db.execute('SELECT session_id FROM production_batches WHERE id=?', (bid,)).fetchone()
            if row and row[0]:
                return row[0]
    return None
