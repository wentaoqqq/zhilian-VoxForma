"""Queue media rejects traversal and symlink escapes without loading models."""
import importlib.util
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1]/'integrations/avatar/vendor/tbdub_student/webui/safe_paths.py'
spec = importlib.util.spec_from_file_location('avatar_queue_safe_paths', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_valid_relative_and_absolute_media(tmp_path):
    video = tmp_path/'people'/'speaker.mp4'
    video.parent.mkdir()
    video.touch()
    assert module.safe_child(tmp_path, 'people/speaker.mp4') == video.resolve()
    assert module.safe_child(tmp_path, str(video)) == video.resolve()


@pytest.mark.parametrize('value', ['../outside.mp4', 'people/../../outside.mp4', '', '.', '/etc/passwd', r'..\outside.mp4'])
def test_rejects_escaping_or_empty_paths(tmp_path, value):
    with pytest.raises(ValueError):
        module.safe_child(tmp_path, value)


def test_rejects_symlink_escape(tmp_path):
    root = tmp_path/'assets'
    root.mkdir()
    outside = tmp_path/'outside.mp4'
    outside.touch()
    try:
        (root/'link.mp4').symlink_to(outside)
    except OSError:
        pytest.skip('This host does not permit creating symlinks')
    with pytest.raises(ValueError):
        module.safe_child(root, 'link.mp4')
