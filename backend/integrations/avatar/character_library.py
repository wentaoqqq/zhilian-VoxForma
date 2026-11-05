"""Named video characters, independent of VoiceStudio voices and job history."""
from __future__ import annotations
import asyncio, contextlib, json, math, mimetypes, os, re, shutil, sqlite3, subprocess, time, uuid
from functools import lru_cache
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
import httpx

MAX_BYTES=2*1024**3
EXTENSIONS={'.mp4','.mov','.mkv'}

class CharacterLibrary:
    def __init__(self, data:Path, assets:Path):
        self.data=Path(data);self.assets=Path(assets).resolve();self.data.mkdir(parents=True,exist_ok=True)
        self.posters=self.data/'posters';self.posters.mkdir(exist_ok=True)
        self.database=self.data/'characters.sqlite3'
        with self.connect() as db:
            db.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS characters (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, notes TEXT NOT NULL,
              revision TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, deleted REAL);
            CREATE TABLE IF NOT EXISTS versions (
              id TEXT PRIMARY KEY, character_id TEXT NOT NULL, video TEXT NOT NULL,
              metadata TEXT NOT NULL, created REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS character_versions ON versions(character_id);
            ''')

    @contextlib.contextmanager
    def connect(self):
        db=sqlite3.connect(self.database,timeout=15);db.row_factory=sqlite3.Row
        try:
            with db:yield db
        finally:db.close()

    @staticmethod
    def text(value, maximum, required=False):
        if not isinstance(value,str) or len(value)>maximum or (required and not value.strip()):
            raise HTTPException(422,'Invalid character name or notes')
        return value.strip()

    def source(self, relative):
        if not isinstance(relative,str):raise HTTPException(422,'Invalid video source')
        p=(self.assets/relative).resolve()
        if not p.is_relative_to(self.assets) or p.suffix.lower() not in EXTENSIONS or not p.is_file():
            raise HTTPException(404,'Character video is missing or unsupported')
        return p

    def get(self, cid, *, deleted=False, revision=None):
        with self.connect() as db:
            row=db.execute('SELECT * FROM characters WHERE id=?',(cid,)).fetchone()
            if not row or (row['deleted'] is not None and not deleted):raise HTTPException(404,'Character not found')
            version=db.execute('SELECT * FROM versions WHERE id=? AND character_id=?',(revision or row['revision'],cid)).fetchone()
        if not version:raise HTTPException(404,'Character version not found')
        result=dict(row);result.update(video=version['video'],metadata=json.loads(version['metadata']),revision=version['id'])
        return result

    def public(self, row):
        result={k:row[k] for k in ('id','name','notes','revision','created','updated','deleted')}
        result.update(row['metadata'])
        result['available']=(self.assets/row['video']).is_file()
        result['poster_url']=f"/api/avatar/characters/{row['id']}/poster?v={row['revision']}"
        result['preview_url']=f"/api/avatar/characters/{row['id']}/video?v={row['revision']}"
        return result

    def list(self, *, deleted=False, query=''):
        with self.connect() as db:
            rows=db.execute('SELECT c.*,v.video,v.metadata FROM characters c JOIN versions v ON v.id=c.revision WHERE '+('c.deleted IS NOT NULL' if deleted else 'c.deleted IS NULL')+' ORDER BY c.updated DESC,c.id').fetchall()
        needle=query.strip().casefold();result=[]
        for r in rows:
            row=dict(r);row['metadata']=json.loads(row['metadata'])
            if not needle or needle in (row['name']+' '+row['notes']).casefold():result.append(self.public(row))
        return result

    def probe(self, relative, revision, filename=None):
        source=self.source(relative)
        try:
            p=subprocess.run(['ffprobe','-v','error','-select_streams','v:0','-show_entries','stream=width,height,avg_frame_rate,duration:format=duration','-of','json',str(source)],capture_output=True,text=True,check=True,timeout=25)
            info=json.loads(p.stdout);stream=info['streams'][0]
            duration=float(info.get('format',{}).get('duration') or stream.get('duration') or 0)
            rate=stream.get('avg_frame_rate','0/1').split('/');fps=float(rate[0])/float(rate[1]) if len(rate)==2 and float(rate[1]) else 0
            if not math.isfinite(duration) or duration<=0 or stream['width']<=0 or stream['height']<=0:raise ValueError('Invalid video metadata')
            temp=self.posters/(revision+'.tmp.jpg');poster=self.posters/(revision+'.jpg')
            subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-threads','2','-ss',str(min(.5,duration/3)),'-i',str(source),'-frames:v','1','-an','-filter_threads','1','-vf','scale=384:512:force_original_aspect_ratio=decrease','-q:v','3',str(temp)],capture_output=True,check=True,timeout=45)
            temp.replace(poster)
        except (subprocess.SubprocessError,ValueError,KeyError,IndexError,ZeroDivisionError) as exc:
            (self.posters/(revision+'.tmp.jpg')).unlink(missing_ok=True)
            raise HTTPException(422,'Cannot read this video or create its preview') from exc
        return {'filename':filename or source.name,'duration':round(duration,3),'width':stream['width'],'height':stream['height'],'fps':round(fps,3),'bytes':source.stat().st_size}

    def create(self, name, notes, relative, *, cid=None, revision=None, filename=None):
        name=self.text(name,80,True);notes=self.text(notes,2000)
        cid=cid or uuid.uuid4().hex;revision=revision or uuid.uuid4().hex
        metadata=self.probe(relative,revision,filename)
        try:
            with self.connect() as db:
                now=time.time()
                db.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(revision,cid,relative,json.dumps(metadata),now))
                db.execute('INSERT INTO characters VALUES(?,?,?,?,?,?,NULL)',(cid,name,notes,revision,now,now))
        except BaseException:
            (self.posters/(revision+'.jpg')).unlink(missing_ok=True);raise
        return self.public(self.get(cid))

    def update(self,cid,body):
        if not isinstance(body,dict) or set(body)-{'name','notes'}:raise HTTPException(422,'Invalid character update')
        with self.connect() as db:
            row=db.execute('SELECT * FROM characters WHERE id=? AND deleted IS NULL',(cid,)).fetchone()
            if not row:raise HTTPException(404,'Character not found')
            name=self.text(body.get('name',row['name']),80,True);notes=self.text(body.get('notes',row['notes']),2000)
            db.execute('UPDATE characters SET name=?,notes=?,updated=? WHERE id=?',(name,notes,time.time(),cid))
        return self.public(self.get(cid))

    def replace(self,cid,relative,revision,filename,name=None,notes=None):
        self.get(cid)
        if name is not None:name=self.text(name,80,True)
        if notes is not None:notes=self.text(notes,2000)
        metadata=self.probe(relative,revision,filename)
        try:
            with self.connect() as db:
                row=db.execute('SELECT * FROM characters WHERE id=? AND deleted IS NULL',(cid,)).fetchone()
                if not row:raise HTTPException(409,'Character was deleted during upload')
                now=time.time();db.execute('INSERT INTO versions VALUES(?,?,?,?,?)',(revision,cid,relative,json.dumps(metadata),now))
                db.execute('UPDATE characters SET revision=?,name=?,notes=?,updated=? WHERE id=?',(revision,name if name is not None else row['name'],notes if notes is not None else row['notes'],now,cid))
        except BaseException:
            (self.posters/(revision+'.jpg')).unlink(missing_ok=True);raise
        return self.public(self.get(cid))

    def remove(self,cid,restore=False):
        with self.connect() as db:
            cursor=db.execute('UPDATE characters SET deleted=?,updated=? WHERE id=?',(None if restore else time.time(),time.time(),cid))
            if not cursor.rowcount:raise HTTPException(404,'Character not found')
        return self.public(self.get(cid,deleted=True))

    def selection(self,cid):
        row=self.get(cid);self.source(row['video'])
        return {'video':row['video'],'character_id':cid,'character_name':row['name'],'character_revision':row['revision']}

@lru_cache(maxsize=1)
def get_library():
    from avatar_paths import data_dir, assets_dir
    return CharacterLibrary(Path(os.environ.get('AVATAR_LIBRARY_DATA',str(data_dir()/'characters'))),assets_dir())

async def receive_video(request:Request,lib:CharacterLibrary,cid):
    filename=request.query_params.get('filename','').replace('\\','/').rsplit('/',1)[-1][:180]
    suffix=Path(filename).suffix.lower()
    if suffix not in EXTENSIONS:raise HTTPException(422,'Upload an MP4, MOV or MKV video')
    size=request.headers.get('content-length')
    if size is not None and (not size.isdigit() or not 0<int(size)<=MAX_BYTES):raise HTTPException(413,'Video must be between 1 byte and 2 GiB')
    revision=uuid.uuid4().hex;folder=lib.assets/'.avatar-library'/cid;folder.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(folder).free<(int(size) if size else 0)+128*1024**2:raise HTTPException(507,'Not enough space for this video')
    target=folder/(revision+suffix);temp=folder/(revision+'.part');total=0
    try:
        with temp.open('xb') as out:
            async for chunk in request.stream():
                total+=len(chunk)
                if total>MAX_BYTES:raise HTTPException(413,'Video exceeds 2 GiB')
                if chunk:await asyncio.to_thread(out.write,chunk)
        if not total or (size is not None and total!=int(size)):raise HTTPException(400,'Incomplete video upload')
        temp.replace(target)
    except BaseException:
        temp.unlink(missing_ok=True);raise
    return target,revision,filename

def character_router(require_consumer):
    router=APIRouter(prefix='/api/avatar/characters',dependencies=[Depends(require_consumer)])
    @router.get('')
    def list_characters(q:str='',deleted:bool=False):return get_library().list(query=q,deleted=deleted)
    @router.post('/import',status_code=201)
    async def import_video(request:Request):
        body=await request.json()
        if not isinstance(body,dict):raise HTTPException(422,'Invalid character import')
        return await asyncio.to_thread(get_library().create,body.get('name'),body.get('notes',''),body.get('video'))
    @router.post('',status_code=201)
    async def upload_character(request:Request):
        lib=get_library();name=lib.text(request.query_params.get('name',''),80,True);notes=lib.text(request.query_params.get('notes',''),2000)
        cid=uuid.uuid4().hex;target,revision,filename=await receive_video(request,lib,cid)
        try:return await asyncio.to_thread(lib.create,name,notes,target.relative_to(lib.assets).as_posix(),cid=cid,revision=revision,filename=filename)
        except BaseException:target.unlink(missing_ok=True);raise
    @router.get('/{cid}')
    def get_character(cid:str):return get_library().public(get_library().get(cid))
    @router.patch('/{cid}')
    async def update_character(cid:str,request:Request):return await asyncio.to_thread(get_library().update,cid,await request.json())
    @router.put('/{cid}/video')
    async def replace_video(cid:str,request:Request):
        lib=get_library();lib.get(cid);target,revision,filename=await receive_video(request,lib,cid)
        try:return await asyncio.to_thread(lib.replace,cid,target.relative_to(lib.assets).as_posix(),revision,filename,request.query_params.get('name'),request.query_params.get('notes'))
        except BaseException:target.unlink(missing_ok=True);raise
    @router.delete('/{cid}')
    def delete_character(cid:str):return get_library().remove(cid)
    @router.post('/{cid}/restore')
    def restore_character(cid:str):return get_library().remove(cid,True)
    @router.get('/{cid}/poster')
    def poster(cid:str,v:str|None=None):
        lib=get_library();row=lib.get(cid,deleted=True,revision=v);p=lib.posters/(row['revision']+'.jpg')
        if not p.is_file():raise HTTPException(404,'Preview image missing')
        return FileResponse(p,media_type='image/jpeg')
    @router.get('/{cid}/video')
    async def video(cid:str,request:Request,v:str|None=None,preview:bool=False):
        lib=get_library();row=lib.get(cid,deleted=True,revision=v);source=lib.source(row['video'])
        if not preview:return FileResponse(source,media_type=mimetypes.guess_type(source.name)[0] or 'video/mp4',filename=row['metadata']['filename'],content_disposition_type='inline')
        client=httpx.AsyncClient(timeout=120,trust_env=False)
        try:upstream=await client.send(client.build_request('GET','http://127.0.0.1:8765/api/media',params={'id':row['video'],'preview':'1'},headers={k:v for k,v in request.headers.items() if k.lower()=='range'}),stream=True)
        except BaseException:await client.aclose();raise
        async def stream():
            try:
                async for chunk in upstream.aiter_bytes():yield chunk
            finally:await upstream.aclose();await client.aclose()
        return StreamingResponse(stream(),status_code=upstream.status_code,headers={k:v for k,v in upstream.headers.items() if k.lower() in ('content-type','content-length','content-range','accept-ranges')})
    return router
