import json
import os
import shutil
from pathlib import Path

from database import video_execute, video_rows
from .media import MediaError, file_hash, probe, safe_name

DATA_ROOT = Path(os.getenv("VIDEO_DATA_DIR", "data")).resolve()


def storage():
    for name in ("video_library", "beats", "overlays", "renders", "previews", "temp"):
        (DATA_ROOT / name).mkdir(parents=True, exist_ok=True)
    return DATA_ROOT


def folders(uid):
    return video_rows(
        "SELECT * FROM video_folders WHERE user_id=? ORDER BY name", (uid,)
    )


def folder(uid, fid):
    rows = video_rows(
        "SELECT * FROM video_folders WHERE user_id=? AND id=?", (uid, fid)
    )
    if not rows:
        raise MediaError("Папка не найдена.")
    return rows[0]


def create_folder(uid, name):
    name = safe_name(name)
    fid = video_execute(
        "INSERT INTO video_folders(user_id,name) VALUES (?,?)", (uid, name)
    )
    (storage() / "video_library" / str(uid) / str(fid)).mkdir(
        parents=True, exist_ok=True
    )
    return fid


def rename_folder(uid, fid, name):
    folder(uid, fid)
    video_execute(
        "UPDATE video_folders SET name=? WHERE user_id=? AND id=?",
        (safe_name(name), uid, fid),
    )


def delete_folder(uid, fid):
    folder(uid, fid)
    if assets(uid, [fid]):
        raise MediaError("Сначала переместите или удалите видео из папки.")
    shutil.rmtree(storage() / "video_library" / str(uid) / str(fid), ignore_errors=True)
    video_execute("DELETE FROM video_folders WHERE user_id=? AND id=?", (uid, fid))


def assets(uid, folder_ids=None, tags=None):
    rows = video_rows(
        "SELECT a.*, f.name AS folder FROM video_assets a JOIN video_folders f ON f.id=a.folder_id WHERE a.user_id=? ORDER BY a.id",
        (uid,),
    )
    return [
        r
        for r in rows
        if (not folder_ids or r["folder_id"] in folder_ids)
        and (not tags or set(tags).issubset(json.loads(r["tags"])))
        and Path(r["path"]).is_file()
    ]


def asset(uid, aid):
    rows = video_rows("SELECT * FROM video_assets WHERE user_id=? AND id=?", (uid, aid))
    if not rows or not Path(rows[0]["path"]).is_file():
        raise MediaError("Видео удалено или недоступно. Выберите другое.")
    return rows[0]


def add_video(uid, fid, source, filename=None):
    folder(uid, fid)
    metadata = probe(source, "video")
    if metadata["duration"] <= 0 or not metadata["width"] or not metadata["height"]:
        raise MediaError("Это не видео с корректной продолжительностью.")
    digest = file_hash(source)
    existing = video_rows(
        "SELECT id FROM video_assets WHERE user_id=? AND hash=?", (uid, digest)
    )
    if existing:
        try:
            return asset(uid, existing[0]["id"])["id"]
        except MediaError:
            video_execute(
                "DELETE FROM video_assets WHERE user_id=? AND id=?",
                (uid, existing[0]["id"]),
            )
    filename = safe_name(filename or Path(source).name)
    target = (
        storage() / "video_library" / str(uid) / str(fid) / f"{digest[:16]}_{filename}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    try:
        return video_execute(
            """INSERT INTO video_assets(user_id,folder_id,filename,path,hash,duration,width,height,fps,codec)
                            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                uid,
                fid,
                filename,
                str(target),
                digest,
                metadata["duration"],
                metadata["width"],
                metadata["height"],
                metadata["fps"],
                metadata["codec"],
            ),
        )
    except Exception:
        target.unlink(missing_ok=True)
        raise


def move_video(uid, aid, fid):
    item = asset(uid, aid)
    folder(uid, fid)
    target = storage() / "video_library" / str(uid) / str(fid) / Path(item["path"]).name
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(item["path"], target)
    video_execute(
        "UPDATE video_assets SET folder_id=?,path=? WHERE id=? AND user_id=?",
        (fid, str(target), aid, uid),
    )


def delete_video(uid, aid):
    item = asset(uid, aid)
    Path(item["path"]).unlink(missing_ok=True)
    video_execute("DELETE FROM video_assets WHERE id=? AND user_id=?", (aid, uid))


def set_tags(uid, aid, tags):
    asset(uid, aid)
    video_execute(
        "UPDATE video_assets SET tags=? WHERE id=? AND user_id=?",
        (json.dumps(tags), aid, uid),
    )
