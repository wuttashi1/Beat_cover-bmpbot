import json
import sqlite3
from contextlib import closing
from copy import deepcopy

DB_PATH = "users.db"

DEFAULT_PRESET = {
    "name": "YouTube Clean",
    "canvas_width": 1920,
    "canvas_height": 1080,
    "layout": "standard",
    "fit_mode": "cover",
    "position_x": 0.5,
    "position_y": 0.5,
    "background_mode": "none",
    "blur_strength": 14,
    "foreground_size": 820,
    "brightness": 0.0,
    "contrast": 1.0,
    "saturation": 1.0,
    "sharpness": 1.0,
    "overlay": "none",
    "overlay_width": 300,
    "overlay_position": "bottom_left",
    "output_format": "JPEG",
    "quality": 95,
    "send_mode": "file",
    "max_file_size_mb": 2.0,
}


def _connect():
    con = sqlite3.connect(DB_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    return con


def _legacy_user_settings(user_id):
    try:
        with closing(_connect()) as con:
            row = con.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            return dict(row) if row else {}
    except sqlite3.Error:
        return {}


def init_cover_preset_schema():
    with closing(_connect()) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS cover_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, name)
            )
            """
        )
        con.execute("CREATE INDEX IF NOT EXISTS idx_cover_presets_user ON cover_presets(user_id)")
        con.commit()


def _normalized(payload):
    result = deepcopy(DEFAULT_PRESET)
    if isinstance(payload, dict):
        result.update(payload)
    result["canvas_width"] = max(320, min(7680, int(result["canvas_width"])))
    result["canvas_height"] = max(180, min(4320, int(result["canvas_height"])))
    result["position_x"] = max(0.0, min(1.0, float(result["position_x"])))
    result["position_y"] = max(0.0, min(1.0, float(result["position_y"])))
    result["blur_strength"] = max(0, min(50, int(result["blur_strength"])))
    result["foreground_size"] = max(200, min(2160, int(result["foreground_size"])))
    result["brightness"] = max(-0.8, min(0.8, float(result["brightness"])))
    result["contrast"] = max(0.2, min(2.5, float(result["contrast"])))
    result["saturation"] = max(0.0, min(3.0, float(result["saturation"])))
    result["sharpness"] = max(0.0, min(4.0, float(result["sharpness"])))
    result["overlay_width"] = max(40, min(1200, int(result["overlay_width"])))
    result["quality"] = max(35, min(100, int(result["quality"])))
    result["max_file_size_mb"] = max(0.2, min(20.0, float(result["max_file_size_mb"])))
    return result


def _insert_preset(con, user_id, name, settings, active=False):
    payload = _normalized(settings)
    payload["name"] = name
    cur = con.execute(
        "INSERT OR IGNORE INTO cover_presets(user_id, name, settings_json, is_active) VALUES(?,?,?,?)",
        (user_id, name, json.dumps(payload, ensure_ascii=False), int(active)),
    )
    return cur.lastrowid


def ensure_user_cover_presets(user_id):
    init_cover_preset_schema()
    with closing(_connect()) as con:
        count = con.execute("SELECT COUNT(*) AS c FROM cover_presets WHERE user_id=?", (user_id,)).fetchone()["c"]
        if count:
            active = con.execute("SELECT id FROM cover_presets WHERE user_id=? AND is_active=1 LIMIT 1", (user_id,)).fetchone()
            if not active:
                first = con.execute("SELECT id FROM cover_presets WHERE user_id=? ORDER BY id LIMIT 1", (user_id,)).fetchone()
                if first:
                    con.execute("UPDATE cover_presets SET is_active=1 WHERE id=?", (first["id"],))
                    con.commit()
            return

        legacy = _legacy_user_settings(user_id)
        vevo = deepcopy(DEFAULT_PRESET)
        vevo.update({
            "name": "VEVO Legacy",
            "overlay": "vevo",
            "overlay_width": int(legacy.get("vevo_wm_size", 450) or 450),
            "send_mode": legacy.get("explicit_format", "photo") or "photo",
            "quality": 95,
        })
        explicit = deepcopy(DEFAULT_PRESET)
        explicit.update({
            "name": "EXPLICIT Legacy",
            "layout": "explicit",
            "background_mode": "spin",
            "blur_strength": int(legacy.get("explicit_blur", 10) or 10),
            "foreground_size": int(legacy.get("explicit_fg_size", 820) or 820),
            "overlay": "explicit",
            "overlay_width": int(legacy.get("explicit_wm_size", 300) or 300),
            "send_mode": legacy.get("explicit_format", "photo") or "photo",
            "output_format": "PNG" if legacy.get("explicit_quality") == "best" else "JPEG",
            "quality": 98 if legacy.get("explicit_quality") == "best" else 95,
        })
        active_style = legacy.get("style", "vevo")
        _insert_preset(con, user_id, "VEVO Legacy", vevo, active=active_style != "explicit")
        _insert_preset(con, user_id, "EXPLICIT Legacy", explicit, active=active_style == "explicit")
        clean = deepcopy(DEFAULT_PRESET)
        _insert_preset(con, user_id, "YouTube Clean", clean, active=False)
        con.commit()


def list_cover_presets(user_id):
    ensure_user_cover_presets(user_id)
    with closing(_connect()) as con:
        rows = con.execute(
            "SELECT id, name, settings_json, is_active FROM cover_presets WHERE user_id=? ORDER BY is_active DESC, id",
            (user_id,),
        ).fetchall()
        result = []
        for row in rows:
            settings = _normalized(json.loads(row["settings_json"]))
            settings["name"] = row["name"]
            result.append({"id": row["id"], "name": row["name"], "active": bool(row["is_active"]), "settings": settings})
        return result


def get_active_cover_preset(user_id):
    ensure_user_cover_presets(user_id)
    with closing(_connect()) as con:
        row = con.execute(
            "SELECT id, name, settings_json FROM cover_presets WHERE user_id=? AND is_active=1 ORDER BY id LIMIT 1",
            (user_id,),
        ).fetchone()
        if not row:
            raise RuntimeError("No active cover preset")
        settings = _normalized(json.loads(row["settings_json"]))
        settings["name"] = row["name"]
        settings["id"] = row["id"]
        return settings


def activate_cover_preset(user_id, preset_id):
    ensure_user_cover_presets(user_id)
    with closing(_connect()) as con:
        owned = con.execute("SELECT 1 FROM cover_presets WHERE id=? AND user_id=?", (preset_id, user_id)).fetchone()
        if not owned:
            return False
        con.execute("UPDATE cover_presets SET is_active=0 WHERE user_id=?", (user_id,))
        con.execute("UPDATE cover_presets SET is_active=1, updated_at=CURRENT_TIMESTAMP WHERE id=?", (preset_id,))
        con.commit()
        return True


def update_active_cover_preset(user_id, **changes):
    preset = get_active_cover_preset(user_id)
    preset_id = preset.pop("id")
    name = preset.get("name", "Preset")
    preset.update(changes)
    preset = _normalized(preset)
    preset["name"] = name
    with closing(_connect()) as con:
        con.execute(
            "UPDATE cover_presets SET settings_json=?, updated_at=CURRENT_TIMESTAMP WHERE id=? AND user_id=?",
            (json.dumps(preset, ensure_ascii=False), preset_id, user_id),
        )
        con.commit()
    return get_active_cover_preset(user_id)


def duplicate_active_cover_preset(user_id, name=None):
    source = get_active_cover_preset(user_id)
    source.pop("id", None)
    base = (name or f"{source.get('name', 'Preset')} Copy").strip()[:40]
    with closing(_connect()) as con:
        candidate = base
        suffix = 2
        while con.execute("SELECT 1 FROM cover_presets WHERE user_id=? AND name=?", (user_id, candidate)).fetchone():
            candidate = f"{base} {suffix}"
            suffix += 1
        con.execute("UPDATE cover_presets SET is_active=0 WHERE user_id=?", (user_id,))
        _insert_preset(con, user_id, candidate, source, active=True)
        con.commit()
    return get_active_cover_preset(user_id)


def reset_active_cover_preset(user_id):
    active = get_active_cover_preset(user_id)
    name = active.get("name", "YouTube Clean")
    if name == "VEVO Legacy":
        changes = deepcopy(DEFAULT_PRESET)
        changes.update({"overlay": "vevo", "overlay_width": 450})
    elif name == "EXPLICIT Legacy":
        changes = deepcopy(DEFAULT_PRESET)
        changes.update({"layout": "explicit", "background_mode": "spin", "blur_strength": 10, "foreground_size": 820, "overlay": "explicit", "overlay_width": 300})
    else:
        changes = deepcopy(DEFAULT_PRESET)
    changes["name"] = name
    return update_active_cover_preset(user_id, **changes)
