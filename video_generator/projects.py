import copy
import json
import secrets

from database import video_execute, video_rows
from .config import VideoRenderConfig
from .media import MediaError, safe_name


def create_project(uid, name="Untitled"):
    presets = video_rows(
        "SELECT payload FROM video_presets WHERE user_id=? AND is_default=1", (uid,)
    )
    settings = (
        VideoRenderConfig.from_dict(json.loads(presets[0]["payload"])).to_dict()
        if presets
        else VideoRenderConfig().to_dict()
    )
    data = {
        "project_version": 1,
        "name": safe_name(name),
        "beat": None,
        "bpm_hint": None,
        "video_mode": "single",
        "video_id": None,
        "folder_ids": [],
        "tags": [],
        "seed": secrets.randbits(32),
        "settings": settings,
        "preset_settings": copy.deepcopy(settings),
        "analysis": None,
        "sections_confirmed": False,
        "advanced": False,
        "metadata": {},
        "preview_range": None,
    }
    pid = video_execute(
        "INSERT INTO video_projects(user_id,name,payload) VALUES (?,?,?)",
        (uid, data["name"], json.dumps(data)),
    )
    return load_project(uid, pid)


def list_projects(uid):
    return video_rows(
        "SELECT id,name,updated_at FROM video_projects WHERE user_id=? ORDER BY updated_at DESC,id DESC",
        (uid,),
    )


def load_project(uid, pid):
    rows = video_rows(
        "SELECT * FROM video_projects WHERE user_id=? AND id=?", (uid, pid)
    )
    if not rows:
        raise MediaError("Project не найден. Откройте /video.")
    data = json.loads(rows[0]["payload"])
    if data.get("project_version") != 1:
        raise MediaError("Неподдерживаемая версия project.")
    data["settings"] = VideoRenderConfig.from_dict(data["settings"]).to_dict()
    data["id"] = pid
    data["user_id"] = uid
    return data


def save_project(project):
    data = {k: v for k, v in project.items() if k not in ("id", "user_id")}
    VideoRenderConfig.from_dict(data["settings"])
    video_execute(
        "UPDATE video_projects SET name=?,payload=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?",
        (
            safe_name(data["name"]),
            json.dumps(data, ensure_ascii=False),
            project["user_id"],
            project["id"],
        ),
    )


def duplicate_project(uid, pid):
    data = load_project(uid, pid)
    new = create_project(uid, data["name"] + " copy")
    data["id"] = new["id"]
    data["name"] = new["name"]
    save_project(data)
    return data


def delete_project(uid, pid):
    load_project(uid, pid)
    active = video_rows(
        "SELECT id FROM video_render_jobs WHERE user_id=? AND project_id=? AND status IN ('queued','analyzing','rendering')",
        (uid, pid),
    )
    if active:
        raise MediaError("Сначала отмените активный render.")
    video_execute("DELETE FROM video_projects WHERE user_id=? AND id=?", (uid, pid))


def presets(uid):
    return [
        {
            "id": 0,
            "name": "WUTSHY_CLASSIC",
            "payload": json.dumps(VideoRenderConfig().to_dict()),
            "is_default": 0,
        }
    ] + video_rows("SELECT * FROM video_presets WHERE user_id=? ORDER BY name", (uid,))


def preset(uid, pid):
    found = next((x for x in presets(uid) if x["id"] == pid), None)
    if not found:
        raise MediaError("Preset не найден.")
    return found


def save_preset(uid, name, settings, overwrite=False):
    settings = VideoRenderConfig.from_dict(settings).to_dict()
    name = safe_name(name)
    if name == "WUTSHY_CLASSIC":
        raise MediaError("Built-in preset нельзя изменить. Используйте Save As.")
    settings["name"] = name
    if overwrite:
        video_execute(
            """INSERT INTO video_presets(user_id,name,payload) VALUES (?,?,?)
                         ON CONFLICT(user_id,name) DO UPDATE SET payload=excluded.payload""",
            (uid, name, json.dumps(settings)),
        )
    else:
        video_execute(
            "INSERT INTO video_presets(user_id,name,payload) VALUES (?,?,?)",
            (uid, name, json.dumps(settings)),
        )


def delete_preset(uid, pid):
    if pid == 0:
        raise MediaError("Built-in preset нельзя удалить.")
    preset(uid, pid)
    video_execute("DELETE FROM video_presets WHERE user_id=? AND id=?", (uid, pid))


def rename_preset(uid, pid, name):
    if not pid:
        raise MediaError("Built-in preset нельзя переименовать.")
    item = preset(uid, pid)
    settings = json.loads(item["payload"])
    settings["name"] = safe_name(name)
    video_execute(
        "UPDATE video_presets SET name=?,payload=? WHERE user_id=? AND id=?",
        (settings["name"], json.dumps(settings), uid, pid),
    )


def default_preset(uid, pid):
    preset(uid, pid)
    video_execute("UPDATE video_presets SET is_default=0 WHERE user_id=?", (uid,))
    if pid:
        video_execute(
            "UPDATE video_presets SET is_default=1 WHERE user_id=? AND id=?", (uid, pid)
        )


def analysis_get(key):
    rows = video_rows(
        "SELECT payload FROM video_analysis_cache WHERE cache_key=?", (key,)
    )
    return json.loads(rows[0]["payload"]) if rows else None


def analysis_put(key, data):
    video_execute(
        "INSERT OR REPLACE INTO video_analysis_cache(cache_key,payload) VALUES (?,?)",
        (key, json.dumps(data)),
    )


def job(uid, jid):
    rows = video_rows(
        "SELECT * FROM video_render_jobs WHERE user_id=? AND id=?", (uid, jid)
    )
    if not rows:
        raise MediaError("Render job не найден.")
    return rows[0]


TRANSITIONS = {
    "queued": {"analyzing", "rendering", "cancelled", "failed"},
    "analyzing": {"rendering", "done", "failed", "cancelled"},
    "rendering": {"done", "failed", "cancelled"},
    "done": set(),
    "failed": set(),
    "cancelled": set(),
}


def job_update(uid, jid, status=None, progress=None, output=None, error=None):
    current = job(uid, jid)
    if (
        status
        and status != current["status"]
        and status not in TRANSITIONS[current["status"]]
    ):
        raise ValueError(f"Invalid job transition {current['status']} -> {status}")
    video_execute(
        "UPDATE video_render_jobs SET status=?,progress=?,output=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE user_id=? AND id=?",
        (
            status or current["status"],
            current["progress"] if progress is None else progress,
            output if output is not None else current["output"],
            error if error is not None else current["error"],
            uid,
            jid,
        ),
    )
