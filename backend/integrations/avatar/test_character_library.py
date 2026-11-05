"""Real-video regressions: CRUD, immutable versions, streaming and preview ranges."""
import asyncio,json,os,shutil,subprocess,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
import httpx
from fastapi import FastAPI,HTTPException
import character_library as mod

fixture=tempfile.TemporaryDirectory();video=Path(fixture.name)/'source.mp4'
subprocess.run(['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-f','lavfi','-i','color=c=navy:s=96x128:r=25:d=0.8','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(video)],check=True)

class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.assets=self.root/'assets';self.assets.mkdir()
        self.source=self.assets/'person.mp4';shutil.copy2(video,self.source);self.lib=mod.CharacterLibrary(self.root/'data',self.assets)
    def tearDown(self):self.temp.cleanup()
    def test_create_search_rename_and_reload(self):
        item=self.lib.create('张三','正面素材','person.mp4');cid=item['id']
        self.assertEqual(len(self.lib.list(query='张三')),1);self.assertTrue(item['available']);self.assertEqual(item['width'],96)
        self.lib.update(cid,{'name':'李四','notes':'新备注'})
        again=mod.CharacterLibrary(self.root/'data',self.assets)
        self.assertEqual(again.get(cid)['name'],'李四');self.assertFalse(again.list(query='张三'));self.assertEqual(len(again.list(query='新备注')),1)
        self.assertEqual(list(self.assets.glob('*.mp4')),[self.source]) # importing does not duplicate a large MP4
    def test_replacement_and_deletion_preserve_submitted_video_version(self):
        item=self.lib.create('张三','','person.mp4');snapshot=self.lib.selection(item['id'])
        replacement=self.assets/'replacement.mp4';shutil.copy2(video,replacement);rid=uuid.uuid4().hex
        changed=self.lib.replace(item['id'],'replacement.mp4',rid,'new.mp4',name='张三新版',notes='新版本')
        self.assertNotEqual(changed['revision'],snapshot['character_revision'])
        self.lib.remove(item['id'])
        with self.assertRaises(HTTPException):self.lib.selection(item['id'])
        self.assertTrue(self.lib.source(snapshot['video']).is_file())
        self.assertEqual(self.lib.get(item['id'],deleted=True,revision=snapshot['character_revision'])['video'],'person.mp4')
        self.assertTrue((self.lib.posters/(snapshot['character_revision']+'.jpg')).is_file())
        self.assertEqual(len(self.lib.list(deleted=True)),1);self.assertFalse(self.lib.list())
        self.lib.remove(item['id'],True);self.assertEqual(self.lib.selection(item['id'])['video'],'replacement.mp4')
    def test_invalid_sources_and_missing_files_cannot_be_submitted(self):
        for value in ('../outside.mp4','/etc/passwd','missing.mp4',None):
            with self.assertRaises(HTTPException):self.lib.create('name','',value)
        bad=self.assets/'bad.mp4';bad.write_bytes(b'not-a-video')
        with self.assertRaises(HTTPException):self.lib.create('name','','bad.mp4')
        self.assertFalse(self.lib.list())
        item=self.lib.create('name','','person.mp4');self.source.unlink()
        self.assertFalse(self.lib.list()[0]['available'])
        with self.assertRaises(HTTPException):self.lib.selection(item['id'])
    def test_cross_character_version_and_invalid_edit_are_rejected(self):
        one=self.lib.create('one','','person.mp4');two=self.lib.create('two','','person.mp4')
        with self.assertRaises(HTTPException):self.lib.get(one['id'],revision=two['revision'])
        with self.assertRaises(HTTPException):self.lib.update(one['id'],{'name':' '})
        self.assertEqual(self.lib.get(one['id'])['name'],'one')

class HttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.assets=self.root/'assets';self.assets.mkdir()
        self.env=patch.dict(os.environ,{'AVATAR_LIBRARY_DATA':str(self.root/'data'),'AVATAR_ASSETS_ROOT':str(self.assets)});self.env.start();mod.get_library.cache_clear()
        app=FastAPI();app.include_router(mod.character_router(lambda:None));self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test')
    async def asyncTearDown(self):
        await self.client.aclose();mod.get_library.cache_clear();self.env.stop();self.temp.cleanup()
    async def test_uploaded_character_range_preview_edit_and_restore(self):
        response=await self.client.post('/api/avatar/characters',params={'name':'张三','filename':'张三.mp4'},content=video.read_bytes());self.assertEqual(response.status_code,201,response.text)
        c=response.json();root='/api/avatar/characters/'+c['id'];source=mod.get_library().source(mod.get_library().get(c['id'])['video'])
        response=await self.client.get(c['preview_url'],headers={'Range':'bytes=0-99'});self.assertEqual(response.status_code,206);self.assertEqual(response.content,video.read_bytes()[:100])
        self.assertEqual((await self.client.get(c['poster_url'])).status_code,200)
        response=await self.client.patch(root,json={'name':'李四','notes':'测试备注'});self.assertEqual(response.json()['name'],'李四')
        response=await self.client.put(root+'/video',params={'filename':'updated.mp4','name':'新版李四','notes':'新视频'},content=video.read_bytes());self.assertEqual(response.status_code,200,response.text)
        self.assertNotEqual(response.json()['revision'],c['revision']);self.assertTrue(source.is_file())
        self.assertEqual((await self.client.get(c['preview_url'],headers={'Range':'bytes=0-19'})).status_code,206)
        await self.client.delete(root);self.assertEqual((await self.client.get(root)).status_code,404)
        self.assertEqual(len((await self.client.get('/api/avatar/characters?deleted=true')).json()),1)
        self.assertTrue(source.is_file());await self.client.post(root+'/restore');self.assertEqual((await self.client.get(root)).status_code,200)
    async def test_bad_or_incomplete_upload_is_never_added(self):
        for params,raw,headers in [({'name':'x','filename':'x.txt'},b'abc',{}),({'name':'x','filename':'x.mp4'},b'bad',{}),({'name':'x','filename':'x.mp4'},b'ab',{'Content-Length':'10'}),({'name':'x','filename':'x.mp4'},b'',{})]:
            response=await self.client.post('/api/avatar/characters',params=params,content=raw,headers=headers);self.assertIn(response.status_code,(400,413,422),response.text)
        self.assertEqual((await self.client.get('/api/avatar/characters')).json(),[])
        self.assertFalse(list(self.assets.rglob('*.part')));self.assertFalse(list(self.assets.rglob('*.mp4')))

if __name__=='__main__':unittest.main(verbosity=2)
