"""Configure only this child process for IndexTTS 2.0, then reuse the wire loop."""
import os
from pathlib import Path
import runpy
import sys


def configure() -> None:
    repo = Path(os.environ["OMNIVOICE_INDEXTTS20_DIR"]).expanduser().resolve()
    os.chdir(repo)
    # Resolve the old code before the interpreter's editable 2.5 installation.
    sys.path.insert(0, str(repo))
    os.environ["OMNIVOICE_INDEXTTS_DIR"] = str(repo)
    os.environ["OMNIVOICE_INDEXTTS_VERSION"] = "2"
    if "OMNIVOICE_INDEXTTS20_FP16" in os.environ:
        os.environ["OMNIVOICE_INDEXTTS_FP16"] = os.environ["OMNIVOICE_INDEXTTS20_FP16"]


if __name__ == "__main__":
    configure()
    runpy.run_path(str(Path(__file__).with_name("main.py")), run_name="__main__")
