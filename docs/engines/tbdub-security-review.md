# Digital-human integration: first security review

Scope: PR #1, source/browser deployment on x86-64 Linux/WSL. The first
CodeQL run scans upstream VoiceStudio as well as the newly vendored TBDub
source. An alert in a touched file is not necessarily on a changed line.
Successful scan execution is not proof that all findings are resolved.

## Changes made before merging

- Make ordinary VoiceStudio imports and jobs independent of avatar GPU
  coordination; only the integration entrypoint enables its scheduler hooks.
- Keep scheduler exception details in server logs rather than HTTP responses.
- Build speech outputs from the validated job directory, and validate speech
  request/reference paths through the existing filesystem-boundary helper.
- Constrain queue media paths to the configured assets, results and preview
  directories, rejecting traversal and symlink escapes before file access.
- Default the internal queue listener to IPv4 loopback. The integrated
  workbench remains the authenticated, externally reachable frontend.
- Use a parameterized, static SQL query for active/deleted characters.
- Mark model-key MD5 fingerprints as `usedforsecurity=False`. Their bytes and
  model-identification behavior are unchanged; downloaded weights are verified
  separately with SHA-256 by the installer.

## Findings that require context

| Finding | Reviewed boundary / behavior |
| --- | --- |
| Queue preview DOM/XSS warning | Preview targets are fixed audio/video elements. Local uploads use browser-created `blob:` URLs; server previews use the fixed `/api/media` endpoint with encoded identifiers. Filenames and status text use `textContent`, not HTML parsing. |
| HuBERT `from_pretrained` revision warning | The argument resolves to a local checkpoint directory and both calls set `local_files_only=True`. The installer pins repository revisions, file sizes and SHA-256 hashes before activation. These calls do not fetch a moving remote revision. |
| Generic DiffSynth download helpers | Managed inference supplies local `ModelConfig(path=...)` files and does not invoke the generic remote-download branch. The install manifest owns production downloads. Generic helper APIs are not exposed as web requests. |
| Optional preprocessing pickle cache | This is a local CLI-only cache, not a web-upload type or a request-controlled inference option. A pickle can execute code: never load one obtained from another person. Existing local cache compatibility is retained. |
| Test `exec` warning | The test extracts a named function from this repository's own source AST to isolate its HTTP transport. It does not execute uploaded/request-provided text. |
| VAE inheritance warnings | The 38-channel subclasses deliberately bypass parent constructors that allocate a different architecture, then build their own modules. Replacing these `super` calls mechanically changes construction. |
| VAE cache-index default warning | The inference encode/decode paths pass and reset their own explicit mutable index. The warning concerns generic calls that omit it; this review does not change model math or its cache protocol. |
| Pipeline signature / DiT branch warnings | Pipeline units receive argument dictionaries selected by the runner; image-only tensors are used under the same image-input flag. A warning about generic inheritance/branch analysis is not evidence of a failure in the configured Student path. |

These explanations are not blanket alert suppressions. Alerts remain visible
for future upstream upgrades and changes to their trust boundaries. Changing
the queue's exposure, permitting arbitrary model/cache uploads, or making
generic download helpers reachable requires a new review.

## Verification boundary

Regression coverage checks disabled/enabled GPU hooks, release after failures,
filesystem traversal/symlink rejection, character CRUD/media ranges, and speech
callbacks. Checks use temporary application data and offline model caches.
They do not establish arbitrary-media quality or a fresh-host GPU deployment.
