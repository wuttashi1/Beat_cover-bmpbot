"""Transactional per-user autosave and named snapshots in the existing SQLite database."""
from contextlib import contextmanager
import json
import sqlite3
from pathlib import Path
from cover_settings import normalize


class CoverStore:
    def __init__(self, path):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS cover_profiles (
                    user_id INTEGER PRIMARY KEY, settings TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cover_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
                    name TEXT NOT NULL, settings TEXT NOT NULL,
                    UNIQUE(user_id, name));
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, uid, legacy=None):
        with self.connect() as db:
            row = db.execute("SELECT settings FROM cover_profiles WHERE user_id=?", (uid,)).fetchone()
            if row:
                return normalize(json.loads(row[0]))
            settings = normalize(legacy or {})
            if legacy:
                settings["export_format"] = "PNG" if legacy.get("explicit_quality") == "best" else "JPEG"
            db.execute("INSERT INTO cover_profiles VALUES (?,?)", (uid, json.dumps(settings)))
            return settings

    def put(self, uid, settings):
        settings = normalize(settings)
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO cover_profiles VALUES (?,?)", (uid, json.dumps(settings)))
        return settings

    @staticmethod
    def name(value):
        value = value.strip()
        if not 1 <= len(value) <= 40 or any(ord(c) < 32 for c in value):
            raise ValueError("Название: от 1 до 40 символов, без переносов строк")
        return value

    def save(self, uid, name, settings):
        name = self.name(name)
        with self.connect() as db:
            try:
                db.execute("INSERT INTO cover_presets(user_id,name,settings) VALUES (?,?,?)",
                           (uid, name, json.dumps(normalize(settings))))
            except sqlite3.IntegrityError:
                raise ValueError("Такое имя уже есть. Выбери другое название") from None

    def list(self, uid):
        with self.connect() as db:
            return db.execute("SELECT id,name FROM cover_presets WHERE user_id=? ORDER BY name", (uid,)).fetchall()

    def load(self, uid, preset_id):
        with self.connect() as db:
            row = db.execute("SELECT name,settings FROM cover_presets WHERE user_id=? AND id=?", (uid,preset_id)).fetchone()
        if not row:
            raise ValueError("Пресет не найден")
        return row[0], normalize(json.loads(row[1]))

    def rename(self, uid, preset_id, name):
        name = self.name(name)
        with self.connect() as db:
            try:
                changed = db.execute("UPDATE cover_presets SET name=? WHERE user_id=? AND id=?", (name,uid,preset_id)).rowcount
            except sqlite3.IntegrityError:
                raise ValueError("Такое имя уже есть") from None
            if not changed:
                raise ValueError("Пресет не найден")

    def delete(self, uid, preset_id):
        with self.connect() as db:
            db.execute("DELETE FROM cover_presets WHERE user_id=? AND id=?", (uid,preset_id))
