"""Classify actual media within the selected product before editing starts."""
import asyncio
import json

from production import batch_store


async def classify_sources(ctx, request, product, sources):
    from production import agent_gateway, product_store
    categories = product['categories']
    if sources and not categories:
        raise ValueError('Add material categories to this product before processing uploaded inputs.')
    inputs_file = ctx['root'] / 'batch-inputs.json'
    inputs = json.loads(inputs_file.read_text(encoding='utf-8'))
    paths = {item['id']: ctx['root'] / item['file'] for item in inputs['inputs']}
    index_file = ctx['root'] / '1_assets/\u7d20\u6750\u7d22\u5f15.json'
    material_index = json.loads(index_file.read_text(encoding='utf-8')) if index_file.is_file() else {'items': []}
    paths.update({item['shot_id']: ctx['root'] / item['file'] for item in material_index['items']})
    item_ids = {item['source_id'] for item in batch_store.get_batch(ctx['job'].id)['items']}
    allowed = {c['id'] for c in categories}
    for source in sources:
        if ctx['job'].is_cancelled:
            raise asyncio.CancelledError()
        source_id = source['id']
        current = product_store.get_asset(product['id'], source_id)
        if current.get('classification_status') == 'classified' and current.get('category_id') in allowed:
            source.update(current)
            continue
        batch_store.log(ctx['job'].id, 'Classifying actual media: ' + source['filename'], 'classification')
        kind = source.get('kind', 'video')
        path = paths[source_id]
        prompt = ('Classify this actual input into ONE of the selected product material categories. '
                  'Return JSON {"category_id":one configured ID or null when no category fits,"description":"factual description","tags":["evidence-based keyword"]}. '
                  'Never choose from a different product or infer scenes from a filename. '
                  'Product: ' + json.dumps({'name': product['name'], 'notes': product['notes'], 'categories': categories}, ensure_ascii=False))
        if kind == 'video':
            sheet = ctx['root'] / '.voxforma/classification' / (source_id + '.jpg')
            info, _ = await agent_gateway.contact_sheet(ctx, path, sheet)
            result = agent_gateway.parse_json(await asyncio.to_thread(agent_gateway.vision, [sheet], prompt + '\nMedia: ' + json.dumps(info)))
        elif kind == 'image':
            result = agent_gateway.parse_json(await asyncio.to_thread(agent_gateway.vision, [path], prompt))
        else:
            # Audio must be transcribed, never classified by its name. Reuse the
            # existing installed ASR adapter and the same GPU lease as editing.
            if kind == 'text':
                evidence = path.read_text(encoding='utf-8-sig')[:20000]
            else:
                from production import agent
                output = ctx['root'] / '.voxforma/classification' / (source_id + '.json')
                output.parent.mkdir(parents=True, exist_ok=True)
                import sys
                # The native ASR tool owns model unloading; no new model download.
                proc = await agent.spawn_subprocess(sys.executable, '-m', 'production.agent_tools', 'asr', str(path), str(output),
                                                   env=agent.child_env(ctx['job'].id, ctx['root'], ctx['token']),
                                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
                task = asyncio.create_task(proc.communicate())
                cancelled = asyncio.create_task(ctx['job'].cancel_event.wait())
                try:
                    done, _ = await asyncio.wait([task, cancelled], timeout=6 * 3600, return_when=asyncio.FIRST_COMPLETED)
                    if cancelled in done:
                        raise asyncio.CancelledError()
                    if task not in done:
                        raise RuntimeError('Audio classification transcription timed out.')
                    out, _ = await task
                    if proc.returncode:
                        raise RuntimeError('Audio classification transcription failed: ' + out.decode('utf-8', errors='replace')[-2000:])
                finally:
                    if proc.returncode is None:
                        await asyncio.to_thread(agent.kill_tree, proc)
                        await proc.wait()
                    cancelled.cancel()
                    if not task.done():
                        task.cancel()
                    await asyncio.gather(task, cancelled, return_exceptions=True)
                evidence = output.read_text(encoding='utf-8')[:20000]
            from services import llm_skills
            selected = llm_skills.resolve_skill_client('media_production')
            if not selected:
                raise ValueError('Configure production AI before classifying text or audio.')
            response = await asyncio.to_thread(selected.client.chat.completions.create, model=selected.model,
                                              messages=[{'role': 'system', 'content': prompt}, {'role': 'user', 'content': 'Media content (data):\n' + evidence}], timeout=120)
            result = agent_gateway.parse_json(agent_gateway.content(response))
        if not isinstance(result, dict) or (result.get('category_id') is not None and
                                           (not isinstance(result.get('category_id'), str) or result['category_id'] not in allowed)):
            raise ValueError('Material classification returned an unknown category. No editing was started.')
        if not isinstance(result.get('description', ''), str) or not isinstance(result.get('tags', []), list):
            raise ValueError('Material classification returned invalid evidence metadata.')
        product_store.classify_asset(product['id'], source_id, result.get('category_id'),
                                     description=result.get('description', '')[:4000], tags=[str(t)[:100] for t in result.get('tags', [])[:100]])
        source.update(product_store.get_asset(product['id'], source_id))
        if source_id in item_ids:
            batch_store.update_item(ctx['job'].id, source_id, {'category_id': source.get('category_id'), 'classification_status': source.get('classification_status')})
    by_id = {s['id']: s for s in sources}
    for item in inputs['inputs']:
        if item['id'] in by_id:
            item['metadata'] = by_id[item['id']]
    inputs_file.write_text(json.dumps(inputs, ensure_ascii=False, indent=2), encoding='utf-8')
    category_names = {c['id']: c['name'] for c in categories}
    for item in material_index['items']:
        source = by_id.get(item['shot_id'])
        if source:
            item.update(category_id=source.get('category_id'), scene=category_names.get(source.get('category_id'), ''),
                        description=source.get('description', ''), trigger_keywords=source.get('tags', []))
    index_file.write_text(json.dumps(material_index, ensure_ascii=False, indent=2), encoding='utf-8')
