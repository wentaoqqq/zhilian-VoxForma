"""Thin process boundary: request VoiceStudio speech, then return to original queue."""
import argparse,json,os,urllib.request,urllib.error
from pathlib import Path
parser=argparse.ArgumentParser();parser.add_argument('--request',required=True);args=parser.parse_args()
params=json.loads(Path(args.request).read_text())
status=Path(params['status_file']);status.write_text(json.dumps({'stage':'synthesizing'}))
lease=os.environ.get('AVATAR_GPU_DELEGATE','')
if not lease:raise RuntimeError('Missing delegated GPU lease')
request=urllib.request.Request('http://127.0.0.1:3901/api/avatar/internal/speech',data=json.dumps({'request':args.request}).encode(),headers={'Content-Type':'application/json','X-Avatar-Lease':lease})
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
try:
    with opener.open(request,timeout=3600) as response:result=json.load(response)
except urllib.error.HTTPError as exc:
    raise RuntimeError('VoiceStudio speech HTTP '+str(exc.code)+': '+exc.read(6000).decode(errors='replace')) from None
if not result.get('saved'):raise RuntimeError('VoiceStudio did not save audio')
status.write_text(json.dumps({'stage':'saved'}))
print(json.dumps(result),flush=True)
