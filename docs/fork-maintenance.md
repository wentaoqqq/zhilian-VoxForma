# Maintaining the digital-human fork

Preserve the upstream VoiceStudio history and commit the fork's changes in
separate logical units: digital-human UI/library/API, shared GPU coordination,
bundled Student runtime/installer, Chinese terminology overlay and HTTP UUID
compatibility. Do not commit credentials, local environment files, downloaded
weights, runtimes, user voices/media, databases or generation history.

The original deployed VoiceStudio baseline is
`12804fd36beb70caf136a68c03092e085ed02eee`. The public integration branch
starts from fork main `fc154e767227f46f2297621b854bed8685e27247` and preserves
its newer setup diagnostics, analytics consent and translated strings.
The bundled TBDub source baseline is
`19e18e4ba8e2a19e6716cb38110ee2de1dae9bb3`.
These are independent upstreams; updating VoiceStudio does not automatically
upgrade TBDub, its requirements, weights or behavior.

Fetch VoiceStudio upstream and merge a selected revision on an upgrade branch.
Review conflicts and API changes, run `scripts/check_product_terminology.py`,
targeted backend tests, TypeScript/locale checks and the browser build. Test a
fresh installation with an empty model cache, then real native 25 fps and RIFE
60 fps jobs including TTS release, side-face/no-face pass-through, long-video
streaming and download/media ranges. Applying a patch cleanly is insufficient.

For a TBDub upgrade, compare the new upstream against the pinned source in
PROVENANCE.json, port its listed upstream-file changes and local helpers,
update dependency/model pins and increment the install recipe. Verify hashes
against the actual source releases. Build a new versioned installation beside
the old one; never replace an active engine's files in place.

Drain the queues before switching the backend release. Preserve persistent
data and old engine directories so rollback can restore the old backend and
active pointer. Desktop builds, if published, must use this fork's own release
and updater endpoints rather than allowing an upstream binary to replace the
customized backend. A public source fork is not itself a desktop binary release.

Existing local core/terminology patches are recovery artifacts. Normal Git
commits and branches become the primary maintenance record once the fork and
the owner's Git identity are configured; do not invent a commit identity.
