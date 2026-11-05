# TBDub Student in the VoiceStudio fork

This is the fork's optional visual-dubbing engine. It is separate from speech
synthesis. The initial supported host is x86-64 Linux or Ubuntu WSL2 with an
NVIDIA GPU; the tested target is RTX 5080 16 GB with 64 GB system RAM.

## Install and use

Start the fork's browser backend from the source checkout after completing
VoiceStudio's normal dependency and frontend setup:

```bash
uv run python -m backend.integrations.avatar.launcher --host 0.0.0.0 --port 3900
```

Open Settings > Models > Digital human and click **Download and install Student**.
This creates an isolated Python 3.10 environment, installs PyTorch 2.9.0 with
CUDA 12.8 and the pinned engine dependencies, prepares FFmpeg through the app's
existing media-tool manager, downloads the required weights, verifies each
file's size and SHA-256, and probes the runtime before activating it.

The required weights total about 15.27 decimal GB. Keep at least 30 GiB free
for packages, weights and installation work. Generated videos need additional
space. The Python environment is independent of the TTS environment.

The optional **Download 60 fps component** button installs the matching RIFE
v4.25 weight and source. Missing RIFE does not prevent native 25 fps generation.

Installation status and logs persist across page reloads. Pause keeps partial
downloads. Retry resumes them; an application restart marks an unfinished job
as interrupted. A failed install does not change the previous active engine.
Only administrator-authorized clients can install or pause installation.

After installation, create a character by uploading its video, then select it
on the digital-human page with uploaded audio or native VoiceStudio TTS.
The queue and GPU arbiter start automatically when needed if external services
are not already running. A healthy external queue must report the expected
engine and asset paths; conflicting services are not killed.

## Data and configuration

Default locations follow VoiceStudio's data and engine settings:

| Content | Location |
| --- | --- |
| Engine source, checkpoints and dedicated Python | `ENGINES_DIR/tbdub-student/versions/student-wsl-v1/` |
| Install state and active pointer | `DATA_DIR/video-install/` |
| Uploaded media | `DATA_DIR/avatar-assets/` |
| Character database and posters | `DATA_DIR/characters/` |
| Scheduler state and control-process logs | `DATA_DIR/avatar-runtime/` |
| Video jobs/results | active engine's `results/webui/` |

`OMNIVOICE_DATA_DIR` and `OMNIVOICE_ENGINES_DIR` select writable storage.
`AVATAR_ASSETS_ROOT` and `AVATAR_LIBRARY_DATA` may preserve existing libraries.
Explicit `OMNIVOICE_TBDUB_DIR` / `OMNIVOICE_TBDUB_PYTHON` paths override managed
installation selection; existing deployments may keep these pins.
`AVATAR_QUEUE_URL` defaults to `http://127.0.0.1:8765` and managed queue startup
accepts only a loopback URL. `AVATAR_VOICE_URL` must identify the main backend's
loopback address. The launcher sets it to its own port.

The installer uses the app's configured Hugging Face endpoint/proxy policy.
Model versions and hashes are in the bundled `install-manifest.json`. It never
downloads arbitrary repositories supplied by a browser request. No download
runs merely because a settings page is opened.

The web backend must use the integration launcher or the existing
`avatar_server:app` deployment entrypoint. Running plain `backend.main:app`
does not enable the shared TTS/video GPU admission layer. This change does not
claim a newly built desktop installer or configure Windows port forwarding;
WSL/driver installation and host network access remain initial host setup.

## Model behavior and maintenance

Student uses the original BF16 checkpoint, two inference steps and a 512x512
face crop. Existing streaming VAE/video handling, source timebase conversion,
face-track recovery/pass-through and optional post-generation RIFE are bundled.
No quantization, reduced face resolution or reduced step count is introduced.
Face recovery can be disabled with `TBDUB_FACE_RECOVERY=0`.

The versioned candidate is activated by one atomic pointer file after checks
pass. Keep previous versions and persistent data when upgrading. Do not replace
live environments while jobs are running. Explicit operator path overrides
continue to take precedence.

The vendored inference source records its upstream commit and modifications in
`backend/integrations/avatar/vendor/tbdub_student/PROVENANCE.json`. It contains
no model weights, personal media, database, runtime, result or backup directory.
Third-party LICENSE/NOTICE files remain alongside their source. VoiceStudio's
AGPL terms and third-party model terms remain separate.
