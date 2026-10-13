import hashlib
import json
import math
import re
import shutil
import subprocess


class MediaError(ValueError):
    pass


def require_ffmpeg():
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise MediaError("Установите FFmpeg и ffprobe на сервере.")


def probe(path, kind=None):
    require_ffmpeg()
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode:
        raise MediaError(
            "Не удалось прочитать медиафайл: повреждённый файл или неизвестный codec."
        )
    data = json.loads(result.stdout)
    streams = data.get("streams", [])
    stream = next((s for s in streams if not kind or s.get("codec_type") == kind), None)
    if stream is None:
        raise MediaError(f"В файле отсутствует {kind or 'media'} stream.")
    duration = float(
        stream.get("duration") or data.get("format", {}).get("duration") or 0
    )
    if not math.isfinite(duration) or duration < 0:
        raise MediaError("Некорректная продолжительность медиа.")
    numerator, denominator = (stream.get("avg_frame_rate") or "0/1").split("/")
    fps = float(numerator) / float(denominator) if float(denominator) else 0
    return dict(
        duration=duration,
        width=stream.get("width", 0),
        height=stream.get("height", 0),
        fps=fps,
        codec=stream.get("codec_name", ""),
        tags=data.get("format", {}).get("tags", {}),
    )


def file_hash(path):
    digest = hashlib.sha256()
    with open(path, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_name(value):
    value = re.sub(r"[^\w .-]", "_", str(value), flags=re.UNICODE).strip(" .")[:100]
    return value or "beat"


def parse_time(value):
    parts = str(value).strip().split(":")
    if not 1 <= len(parts) <= 3:
        raise ValueError("Timestamp: 1:23, 01:23.500 или 83.5.")
    numbers = [float(x) for x in parts]
    if any(not math.isfinite(x) or x < 0 for x in numbers):
        raise ValueError("Timestamp должен быть положительным конечным числом.")
    if len(numbers) > 1 and any(x >= 60 for x in numbers[1:]):
        raise ValueError("Минуты/секунды после двоеточия должны быть меньше 60.")
    return sum(x * 60**i for i, x in enumerate(reversed(numbers)))


def format_time(seconds, precise=False):
    seconds = max(0, float(seconds))
    if precise:
        millis = round(seconds * 1000)
        minutes, rest = divmod(millis, 60000)
        return f"{minutes:02d}:{rest // 1000:02d}.{rest % 1000:03d}"
    hours, rest = divmod(int(seconds), 3600)
    minutes, sec = divmod(rest, 60)
    return (
        f"{hours:02d}:{minutes:02d}:{sec:02d}" if hours else f"{minutes:02d}:{sec:02d}"
    )


def square_crop(width, height):
    side = min(width, height)
    return (side, side, (width - side) // 2, (height - side) // 2)
