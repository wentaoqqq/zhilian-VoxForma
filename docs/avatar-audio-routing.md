# Legacy audio requests in managed installations

An avatar queue started with `AVATAR_INTEGRATION_DIR` uses VoiceStudio for TTS
when an old page/RPA request omits `tts_provider`. This includes audio-only
requests; it must not try the retired `~/.omnivoice/engines/indextts2` runtime.
An explicit provider is preserved. Standalone queues without the integration
setting retain the legacy IndexTTS default.

The global voice-release endpoint unloads this service's registered models and
clears unused accelerator/allocator caches. It must not apply Student's video
memory budget to audio-only jobs or lease cleanup: another application's VRAM
would otherwise permanently block every queued request. Student still checks
its unchanged 13000 MiB budget immediately before video inference. Audio-only
jobs ignore unused character/video selections.

Saved-profile snapshots preserve engine reference-window semantics: for an
engine with `best_window`, do not promote the stored full-clip transcript into
an explicit request transcript when the reference exceeds `max_ref_seconds`.
Explicit user corrections remain explicit; short references retain their
stored transcript. Media GET/HEAD proxy requests must have no request body:
forwarding an empty async stream adds chunked transfer encoding that the
HTTP/1.0 queue does not consume, which can truncate the response on close.

OmniVoice's automatic reference selector also caps input at 75 seconds. For
longer transcript-free references in avatar speech jobs, prepare four 15-second
windows distributed over the original recording into a job-owned 60-second WAV.
The model then applies its normal best-window recognizer and pause alignment.
The source voice file remains intact; manually supplied transcripts retain the
native validation path. IndexTTS references are unchanged.

Before deployment, keep the old files, check both queues are idle, and update
both the managed engine's `webui` files and the matching repository vendor
files. Restart the idle queue to load the new request routing. Verify a real
legacy audio-only request produces a decodable WAV and the GPU lease releases.

Validation (2026-11-09): replayed the old audio-only request format with no
provider/model/native-form fields against the real managed queue. Task
`student_20261109_113618_e43863` used `voicestudio`, saved a 24 kHz WAV of
4.109958 seconds, exited TTS with code 0, and released the GPU lease. No Student
inference was launched. Previous source files are preserved under the server's
`deployments/audio-only-fix-20261109/` with a rollback script.
