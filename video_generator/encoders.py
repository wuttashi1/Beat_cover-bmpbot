import functools
import subprocess
from pathlib import Path


@functools.lru_cache(maxsize=2)
def available_encoders(codec="H.264"):
    stem = "h264" if codec == "H.264" else "hevc"
    found = {}
    for label, suffix in [("NVENC", "nvenc"), ("QuickSync", "qsv"), ("VAAPI", "vaapi")]:
        command = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y"]
        device = []
        filters = []
        if label == "VAAPI":
            render_device = next(iter(sorted(Path("/dev/dri").glob("renderD*"))), None)
            if not render_device:
                continue
            device = ["-vaapi_device", str(render_device)]
            filters = ["-vf", "format=nv12,hwupload"]
        command += (
            device
            + ["-f", "lavfi", "-i", "color=s=128x128:r=24", "-frames:v", "1"]
            + filters
            + ["-c:v", f"{stem}_{suffix}", "-f", "null", "-"]
        )
        try:
            result = subprocess.run(command, capture_output=True, timeout=8)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0:
            found[label] = {"encoder": f"{stem}_{suffix}", "device": device}
    return found


def encoder_options(export, preview=False):
    if export.encoder == "CPU" or preview:
        return None
    available = available_encoders(export.codec)
    if export.encoder == "Auto":
        return next(iter(available.values()), None)
    return available.get(export.encoder)
