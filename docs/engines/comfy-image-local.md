# Local AI Images

Open **Settings → Models → AI Images** and click **Install image module**. The
installer runs on the backend and requires x86-64 Linux or WSL with an NVIDIA
driver that supports CUDA 13. The browser can run on another computer. The
first install prepares its own Python 3.12 environment, ComfyUI 0.37.0 and
about 30.2 GiB of Qwen Image 2.1 BF16 weights. The Python environment needs
additional space. Installation progress and errors appear on the same page.

On the backend, the installer checks
~/minimax-h3/ComfyUI/models for the three pinned files. Set
VOXFORMA_COMFY_MODELS_DIR on the backend to point it at a different existing
models directory. Matching files are verified by SHA-256 and hard linked
where possible. The installer copies them across filesystems. Missing files
are fetched from the configured Hugging Face endpoint or Hugging Face directly.
Retrying an interrupted install preserves completed downloads.

In **AI Images**, enter a prompt and select a size for text-to-image. For an
edit, attach one PNG, JPEG or WebP reference and describe what should change.
Edits use the original workflow's approximate 1K resolution and follow the
reference aspect ratio. Completed PNGs can be viewed or downloaded from
history.

The VoxForma WSL backend uses its shared GPU lease for image, TTS and
digital-human jobs. A managed ComfyUI process starts for each image job and
exits before the GPU lease is released. ComfyUI jobs submitted directly
through a separate ComfyUI service belong to that service's own queue.

The backend stores engine files under its configured engines directory and
history under image-generation/jobs in its data directory. Failed jobs keep a
comfy.log beside job.json. Installation command output is in install.log in
the module directory.
