"""Enable/disable or restore the exact backed-up pre-recovery source files."""
from pathlib import Path
import argparse,fcntl,hashlib,json,shutil,time

root=Path(__file__).resolve().parent
parser=argparse.ArgumentParser();parser.add_argument('action',choices=['status','enable','disable','rollback'])
args=parser.parse_args();config=root/'face_recovery.json'
if args.action=='status':
 print(config.read_text());raise SystemExit(0)
with (root/'.run.lock').open('a') as lock:
 try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
 except BlockingIOError:raise SystemExit('A generation is running. Wait for it to finish; nothing was changed.')
 manifest=json.loads((root/'face_recovery_install.json').read_text())
 if args.action=='rollback':
  for name,expected in manifest['installed_hashes'].items():
   if hashlib.sha256((root/name).read_bytes()).hexdigest()!=expected:
    raise SystemExit('Source changed after installation; refusing to overwrite: '+name)
  for name in manifest['original_hashes']:
   source=Path(manifest['backup'])/name
   assert hashlib.sha256(source.read_bytes()).hexdigest()==manifest['original_hashes'][name]
   temp=root/(name+'.restore');shutil.copy2(source,temp);temp.replace(root/name)
 enabled=args.action=='enable'
 temp=config.with_suffix('.tmp');temp.write_text(json.dumps({'enabled':enabled,'version':'bbox-recovery-v1','updated':time.time()},indent=2));temp.replace(config)
 print(args.action+' complete. Applies to subsequent jobs; no UI/WSL restart needed.')
