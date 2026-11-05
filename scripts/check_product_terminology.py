"""Run after an upstream merge: reject removed/changed source keys or lost placeholders."""
from pathlib import Path
import json,re,sys

def check(ui):
    folder=ui/'i18n';terms=json.loads((folder/'product-terminology.json').read_text(encoding='utf-8'))
    locales={lang:json.loads((folder/'locales'/f'{lang}.json').read_text(encoding='utf-8')) for lang in ('en','zh-CN','zh-TW')}
    def get(d,key):
        for part in key.split('.'):d=d[part]
        return d
    def placeholders(s):return sorted(re.findall(r'{{\s*([^{}]+?)\s*}}',s))
    errors=[];seen=set()
    for entry in terms['entries']:
        key=entry['key']
        if key in seen:errors.append('Duplicate override: '+key)
        seen.add(key)
        for lang,data in locales.items():
            try:value=get(data,key)
            except (KeyError,TypeError):errors.append(f'{lang}: removed key {key}');continue
            if not isinstance(value,str):errors.append(f'{lang}: key is no longer a string: {key}')
            if lang=='en' and value!=entry['source_en']:errors.append('Review changed English meaning: '+key)
        for lang in ('zh-CN','zh-TW'):
            if placeholders(entry[lang])!=placeholders(entry['source_en']):errors.append(f'{lang}: changed variables in {key}')
    if errors:raise RuntimeError('\n'.join(errors))
    print(f'Terminology checked: {len(seen)} keys; upstream meaning and placeholders unchanged.')

if __name__=='__main__':
    root=Path(sys.argv[1]) if len(sys.argv)>1 else Path(__file__).resolve().parents[1]
    check(root/'electron/src/renderer/src')
