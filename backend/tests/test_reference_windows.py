import hashlib,importlib.util,shutil,subprocess,wave
from pathlib import Path
import pytest


def test_long_reference_is_bounded_and_original_unchanged(tmp_path):
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:pytest.skip('ffmpeg needed for audio verification')
    path=Path(__file__).resolve().parents[1]/'integrations/avatar/reference_windows.py'
    spec=importlib.util.spec_from_file_location('windows_test',path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    original=tmp_path/'original.wav';out=tmp_path/'bounded.wav'
    subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','sine=frequency=400:duration=295.7','-ar','16000',str(original)],check=True)
    digest=hashlib.sha256(original.read_bytes()).digest()
    assert module.prepare_reference_windows(original,295.7,out,ffmpeg)==out
    with wave.open(str(out)) as f:assert f.getnframes()/f.getframerate()==60 and f.getnchannels()==1
    assert hashlib.sha256(original.read_bytes()).digest()==digest
    assert not out.with_suffix('.wav.part').exists()
