# Local AI Images

Open **Settings → Models → AI Images** and click **Install image module**. The
installer runs on the backend and requires x86-64 Linux or WSL with an NVIDIA
driver that supports CUDA 13. It prepares a dedicated Python 3.12 environment
for Diffusers and installs about 30.9 GiB of the official Qwen Image 2.1 BF16
model files. Allow extra disk space for the Python environment. Installation
progress and errors appear in Settings.

The installer can reuse matching files from the local Hugging Face cache.
Set VOXFORMA_QWEN_MODELS_DIR on the backend to point to another local
Diffusers-format Qwen Image 2.1 folder. Matching files are verified and hard
linked where possible. The installer copies them across filesystems. Missing
files are downloaded from the configured Hugging Face mirror or Hugging Face
directly. Retry preserves completed downloads. The official Diffusers pack
uses a different file layout from the ComfyUI weight pack.

In **AI Images**, enter a prompt and select a size for text-to-image. For an
edit, attach one PNG, JPEG or WebP reference and describe the changes. Edits
use the reference aspect ratio at approximately 1K. Completed PNGs can be
viewed and downloaded from history.

In the integrated VoxForma backend, image jobs share the existing GPU lease
with TTS and digital-human jobs. Each image job starts its own Diffusers
worker. The text encoder and denoiser move layers between RAM and the GPU.
The process exits before the lease is released. The worker records setup
time, generation time and peak PyTorch GPU memory in diffusers.log.

Installation logs are in install.log under the engine directory. Job history,
PNGs and generation logs are under image-generation/jobs in the backend data
directory.

[Official model files and license](https://huggingface.co/Qwen/Qwen-Image-2.1/tree/d26bb61231c349cf6b7896fa83353113880e1ba3)
