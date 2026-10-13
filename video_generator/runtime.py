import json
from dataclasses import asdict, dataclass

from database import video_execute, video_rows
from .config import number, validate_model


@dataclass
class RuntimeSettings:
    concurrent_renders: int = number(1, 1, 8)
    download_limit_mb: int = number(20, 1, 2000)
    upload_limit_mb: int = number(50, 1, 2000)


def load():
    rows = video_rows("SELECT payload FROM video_runtime_settings WHERE id=1")
    result = (
        RuntimeSettings(**json.loads(rows[0]["payload"])) if rows else RuntimeSettings()
    )
    validate_model(result, "runtime")
    return result


def save(settings):
    validate_model(settings, "runtime")
    video_execute(
        "INSERT OR REPLACE INTO video_runtime_settings(id,payload) VALUES (1,?)",
        (json.dumps(asdict(settings)),),
    )
