"""CLI tools available to the production agent inside its editing workspace."""
import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import uuid

import httpx


def workspace():
    return Path(os.environ['VOXFORMA_WORKSPACE'])


def path(value):
    candidate = Path(value)
    return candidate if candidate.is_absolute() else workspace() / candidate


def call(action, data):
    with httpx.Client(timeout=21600, trust_env=False) as client:
        response = client.post(os.environ['VOXFORMA_AGENT_URL'] + '/production/agent/' + os.environ['VOXFORMA_BATCH_ID'] + '/' + action, headers={'Authorization': 'Bearer ' + os.environ['VOXFORMA_AGENT_TOKEN']}, json=data)
        response.raise_for_status()
        return response.json()


def trace(kind, data):
    with (workspace() / '.voxforma/tools.jsonl').open('a', encoding='utf-8') as output:
        output.write(json.dumps({'tool': kind, **data}, ensure_ascii=False) + '\n')


def asr(source, output):
    from services.asr_backend import load_active_asr_backend
    context = nullcontext()
    if os.environ.get('AVATAR_GPU_SOCKET') or os.environ.get('AVATAR_GPU_AUTOSTART') == '1':
        import gpu_client
        context = gpu_client.Lease('production-asr:' + uuid.uuid4().hex)
    with context as lease:
        if lease is not None:
            lease.clear_voice()
        backend = load_active_asr_backend(require_installed=True)
        try:
            result = backend.transcribe(str(source), word_timestamps=True)
        finally:
            backend.unload()
    if 'segments' not in result and result.get('chunks'):
        # Some native ASR adapters return timestamped chunks instead of segments.
        result['segments'] = [{'text': c.get('text', ''), 'start': c['timestamp'][0], 'end': c['timestamp'][1]} for c in result['chunks'] if c.get('timestamp') and all(t is not None for t in c['timestamp'])]
    rows = []
    for segment in result.get('segments', []):
        words = [{'w': w.get('word', w.get('text', '')), 's': w['start'], 'e': w['end']} for w in segment.get('words', []) if 'start' in w and 'end' in w]
        rows.append({'text': segment.get('text', ''), 'start': segment['start'], 'end': segment['end'], 'words': words})
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')
    trace('asr', {'input': str(source), 'output': str(output), 'segments': len(rows)})
    return {'file': str(output), 'segments': len(rows), 'speech': bool(rows)}


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ['asr', 'tts', 'analyze']:
        p = commands.add_parser(command)
        p.add_argument('input'); p.add_argument('output'); p.add_argument('model', nargs='?')
    p = commands.add_parser('vision'); p.add_argument('image'); p.add_argument('--prompt', required=True)
    p = commands.add_parser('audit'); p.add_argument('source_id'); p.add_argument('video'); p.add_argument('cover')
    p = commands.add_parser('publish'); p.add_argument('source_id'); p.add_argument('video'); p.add_argument('cover'); p.add_argument('publication'); p.add_argument('cover_title'); p.add_argument('--used', nargs='*', default=[])
    p = commands.add_parser('fail'); p.add_argument('source_id'); p.add_argument('reason')
    p = commands.add_parser('ingest'); p.add_argument('source_id'); p.add_argument('index')
    p = commands.add_parser('respond'); p.add_argument('input'); p.add_argument('--action', choices=['discuss', 'organize', 'produce'], required=True)
    p = commands.add_parser('select'); p.add_argument('source_ids', nargs='+')
    commands.add_parser('classify')
    args = parser.parse_args(argv)
    if args.command == 'asr':
        result = asr(path(args.input), path(args.output))
    elif args.command in ['tts', 'analyze']:
        result = call(args.command, {'input': str(path(args.input)), 'output': str(path(args.output))})
    elif args.command == 'respond':
        result = call('respond', {'text': path(args.input).read_text(encoding='utf-8-sig'), 'action': args.action})
    elif args.command == 'vision':
        result = call('vision', {'image': str(path(args.image)), 'prompt': args.prompt})
    else:
        result = call(args.command, {key: (str(path(value)) if key in ['video', 'cover', 'publication', 'cover_title', 'index'] else value) for key, value in vars(args).items() if key != 'command'})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
