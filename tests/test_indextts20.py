"""2.0 remains a real independent model choice when 2.5 is installed."""
import importlib.util
import io
from pathlib import Path
import sys
import types
from contextlib import nullcontext

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_script(name):
    path = ROOT / 'backend/engines/indextts' / name
    spec = importlib.util.spec_from_file_location('_test_' + path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('requested, expected', [('2', '2'), ('auto', '2.5')])
def test_explicit_20_never_loads_25_weights(monkeypatch, tmp_path, requested, expected):
    sidecar = load_script('main.py')
    seen = []
    package = types.ModuleType('indextts'); package.__path__ = []
    monkeypatch.setitem(sys.modules, 'indextts', package)
    for version, name in [('2', 'infer_v2'), ('2.5', 'infer_v2_5')]:
        module = types.ModuleType('indextts.' + name)
        def construct(_version=version, **kwargs):
            seen.append((_version, kwargs)); return object()
        module.IndexTTS2 = construct
        monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_VERSION', requested)
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_DIR', str(tmp_path))
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_FP16', '0')
    monkeypatch.setattr(sidecar, '_heartbeat', lambda *args: nullcontext())
    monkeypatch.setattr(sidecar, '_local_checkpoint_config', lambda cfg, model: nullcontext(cfg))
    sidecar._load_model(io.BytesIO())
    assert seen[0][0] == expected
    assert seen[0][1]['model_dir'] == str(tmp_path / 'checkpoints')
    assert ('use_fp16' in seen[0][1]) is (expected == '2')


def test_20_launcher_selects_only_its_own_repo(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.setenv('OMNIVOICE_INDEXTTS20_DIR', str(tmp_path))
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_DIR', '/other/2.5')
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_VERSION', 'auto')
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_FP16', '0')
    monkeypatch.setenv('OMNIVOICE_INDEXTTS20_FP16', '1')
    monkeypatch.setattr(sys, 'path', list(sys.path))
    load_script('main_v20.py').configure()
    import os
    assert os.environ['OMNIVOICE_INDEXTTS_VERSION'] == '2'
    assert os.environ['OMNIVOICE_INDEXTTS_DIR'] == str(tmp_path.resolve())
    assert os.environ['OMNIVOICE_INDEXTTS_FP16'] == '1'
    assert sys.path[0] == str(tmp_path.resolve())
    assert Path.cwd() == tmp_path.resolve()


def test_20_strips_25_only_inference_arguments():
    sidecar = load_script('main.py')
    params = sidecar._build_infer_kwargs({'text': 'test', 'lang': 'zh', 'duration_factor': .8,
                                       'target_tokens': 42, 'emo_audio_prompt': 'emotion.wav',
                                       'emo_alpha': .7}, 'voice.wav', is_v25=False)
    assert 'lang' not in params and 'duration_factor' not in params
    assert params['target_tokens'] == 42
    assert params['emo_audio_prompt'] == 'emotion.wav' and params['emo_alpha'] == .7


def test_registry_exposes_20_independently_with_existing_models(monkeypatch, tmp_path):
    pytest.importorskip('torch')
    from services.tts_backend import get_backend_class
    from engines.indextts.v20 import python_path
    import os
    monkeypatch.setenv('OMNIVOICE_INDEXTTS20_DIR', str(tmp_path))
    monkeypatch.setenv('OMNIVOICE_INDEXTTS_DIR', str(tmp_path / 'other-2.5'))
    cls = get_backend_class('indextts20')
    assert cls.id != get_backend_class('indextts2').id
    assert cls.supports_emotion and cls.supports_cloning
    assert not cls.is_available()[0]
    for path in [python_path(tmp_path), tmp_path / 'indextts/infer_v2.py'] + [
        tmp_path / 'checkpoints' / name for name in ['config.yaml', 'gpt.pth', 's2mel.pth']
    ]:
        path.parent.mkdir(parents=True, exist_ok=True);path.touch()
    assert cls.is_available()[0]
    assert cls.venv_python() == python_path(tmp_path)
    assert cls.__new__(cls).supported_languages == ['zh', 'en']
    assert os.environ['OMNIVOICE_INDEXTTS_DIR'].endswith('other-2.5')


def test_20_rate_does_not_leak_unsupported_tokens_into_old_model(monkeypatch):
    torch = pytest.importorskip('torch')
    from engines.indextts import IndexTTS2Backend
    from engines.indextts.v20 import IndexTTS20Backend
    from services import ffmpeg_utils
    seen = {}
    def generate(self, text, **kw):
        seen.update(kw);return torch.zeros(24000)
    async def stretch(audio, target_samples, sr):
        seen['target'] = target_samples;return torch.zeros(1, target_samples)
    monkeypatch.setattr(IndexTTS2Backend, 'generate', generate)
    monkeypatch.setattr(ffmpeg_utils, '_pitch_preserving_stretch', stretch)
    backend = IndexTTS20Backend.__new__(IndexTTS20Backend)
    audio = backend.generate('test', ref_audio='ref.wav', speed=1.25, emo_alpha=.6)
    assert audio.shape[-1] == 19200
    assert seen == {'ref_audio': 'ref.wav', 'emo_alpha': .6, 'target': 19200}
