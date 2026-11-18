# Existing local deployment

Existing path overrides and local models are preserved. New installations use the bundled one-click Student installer; see [TBDub Student](tbdub-student.md).

Use the integration launcher, keep user data and completed jobs, and drain queues before switching a deployment. See [fork maintenance](../fork-maintenance.md).

## Temporary ComfyUI preemption

This local opt-in is enabled only while `${OMNIVOICE_DATA_DIR}/avatar-runtime/temporary-comfy-reclaim.enabled` exists. Remove that marker to disable it immediately; no service restart is required.

When Student's existing free-VRAM check fails, the integration stops only the same-user processes for `/home/dministrator/minimax-h3/ComfyUI/main.py` and its `scripts/observed_server.py` watcher, with the matching project working directory and virtualenv interpreter. Running ComfyUI work is interrupted. PID handles prevent signals from reaching a recycled PID. The model environment's Python may delegate this operation to `/usr/bin/python3` for pidfd support.

After cleanup, Student waits up to 20 seconds for VRAM to return and keeps the original memory requirement. Other applications are outside this allowlist. This option does not change model weights, image quality, or inference settings and is disabled by default on new installations.
