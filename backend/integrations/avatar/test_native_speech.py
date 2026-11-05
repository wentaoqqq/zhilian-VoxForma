"""Regression: native form transport keeps controls, reference and GPU context."""
import ast,asyncio,io,sys,types,tempfile,unittest,wave
from pathlib import Path
import httpx
from fastapi import FastAPI,APIRouter,Form,File,UploadFile,HTTPException
from fastapi.responses import Response
import gpu_client
from avatar_conditioning import emotion_audio

source=ast.parse(Path(__file__).with_name('avatar_server.py').read_text())
fn=next(n for n in source.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='native_speech')
scope=dict(FastAPI=FastAPI,HTTPException=HTTPException,httpx=httpx)
exec(compile(ast.Module(body=[fn],type_ignores=[]),'<native_speech>','exec'),scope)
native_speech=scope['native_speech']

class NativeSpeechTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_controls_reference_and_lease_survive_transport(self):
        router=APIRouter();received={}
        @router.post('/generate')
        async def render(text:str=Form(...),engine:str=Form(...),speed:float=Form(...),ref_audio:UploadFile=File(...)):
            received.update(text=text,engine=engine,speed=speed,audio=await ref_audio.read(),lease=gpu_client.current_lease.get(),emotion=emotion_audio.get())
            b=io.BytesIO()
            with wave.open(b,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(24000);w.writeframes(b'\0\0'*240)
            return Response(b.getvalue(),media_type='audio/wav',headers={'X-Audio-Id':'test0001'})
        module=types.ModuleType('api.routers.generation');module.router=router
        previous=sys.modules.get(module.__name__);sys.modules[module.__name__]=module
        lease=gpu_client.current_lease.set({'test_lease':True})
        try:
            with tempfile.TemporaryDirectory() as folder:
                ref=Path(folder)/'ref.mp3';ref.write_bytes(b'reference-snapshot')
                result=await native_speech({'text':'hello','engine':'native-engine','native_form':{'speed':'1.2'},'emotion_weight':.7},ref,'emotion.wav',Path(folder)/'speech.wav')
                self.assertTrue(result['saved']);self.assertEqual(received['speed'],1.2)
                self.assertEqual(received['audio'],b'reference-snapshot');self.assertEqual(received['lease'],{'test_lease':True})
                self.assertEqual(received['emotion'],('emotion.wav',.7));self.assertIsNone(emotion_audio.get())
        finally:
            gpu_client.current_lease.reset(lease)
            if previous is None:sys.modules.pop(module.__name__,None)
            else:sys.modules[module.__name__]=previous
    async def test_native_validation_error_is_not_reported_as_saved_audio(self):
        router=APIRouter()
        @router.post('/generate')
        async def render():raise HTTPException(422,'native validation refused')
        module=types.ModuleType('api.routers.generation');module.router=router
        previous=sys.modules.get(module.__name__);sys.modules[module.__name__]=module
        try:
            with tempfile.TemporaryDirectory() as folder:
                output=Path(folder)/'speech.wav'
                with self.assertRaises(HTTPException) as exc:
                    await native_speech({'text':'hello','engine':'native-engine','native_form':{}},None,None,output)
                self.assertEqual(exc.exception.status_code,422);self.assertFalse(output.exists());self.assertIsNone(emotion_audio.get())
        finally:
            if previous is None:sys.modules.pop(module.__name__,None)
            else:sys.modules[module.__name__]=previous

if __name__=='__main__':unittest.main()
