import sqlite3

DB_PATH = "users.db"
db = sqlite3.connect(DB_PATH, check_same_thread=False)
cur = db.cursor()
ALLOWED_FIELDS = {
    "style",
    "vevo_wm_size",
    "explicit_wm_size",
    "explicit_blur",
    "explicit_fg_size",
    "explicit_quality",
    "explicit_format",
    "notifications_enabled",
}

cur.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    style TEXT DEFAULT 'vevo',
    vevo_wm_size INTEGER DEFAULT 450,
    explicit_wm_size INTEGER DEFAULT 300,
    explicit_blur INTEGER DEFAULT 10,
    explicit_fg_size INTEGER DEFAULT 820,
    explicit_quality TEXT DEFAULT 'good',
    explicit_format TEXT DEFAULT 'photo'
)
""")
db.commit()


def migrate_database():
    """Add missing columns to existing tables"""
    cur.execute("PRAGMA table_info(users)")
    columns = {row[1] for row in cur.fetchall()}
    
    # Add missing columns
    if "explicit_wm_size" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN explicit_wm_size INTEGER DEFAULT 300")
        db.commit()
    
    if "explicit_blur" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN explicit_blur INTEGER DEFAULT 10")
        db.commit()
    
    if "explicit_fg_size" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN explicit_fg_size INTEGER DEFAULT 820")
        db.commit()
    
    if "explicit_quality" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN explicit_quality TEXT DEFAULT 'good'")
        db.commit()
    
    if "explicit_format" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN explicit_format TEXT DEFAULT 'photo'")
        db.commit()
    
    if "vevo_wm_size" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN vevo_wm_size INTEGER DEFAULT 450")
        db.commit()
    
    if "notifications_enabled" not in columns:
        cur.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER DEFAULT 0")
        db.commit()


def migrate_channel_publishers():
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_publishers (
            user_id INTEGER PRIMARY KEY
        )
        """
    )
    db.commit()


migrate_database()
migrate_channel_publishers()


def add_channel_publisher(user_id: int):
    cur.execute("INSERT OR IGNORE INTO channel_publishers(user_id) VALUES (?)", (user_id,))
    db.commit()


def remove_channel_publisher(user_id: int):
    cur.execute("DELETE FROM channel_publishers WHERE user_id=?", (user_id,))
    db.commit()


def list_channel_publishers():
    cur.execute("SELECT user_id FROM channel_publishers ORDER BY user_id")
    return [row[0] for row in cur.fetchall()]


def is_channel_publisher(user_id: int) -> bool:
    cur.execute("SELECT 1 FROM channel_publishers WHERE user_id=?", (user_id,))
    return cur.fetchone() is not None


def get_user_settings(uid):
    """Get user settings or create default"""
    cur.execute("SELECT * FROM users WHERE user_id=?", (uid,))
    row = cur.fetchone()
    if not row:
        cur.execute("""
        INSERT INTO users(user_id, style, vevo_wm_size, explicit_wm_size, 
                          explicit_blur, explicit_fg_size, explicit_quality, explicit_format, notifications_enabled)
        VALUES (?,?,?,?,?,?,?,?,?)
        """, (uid, "vevo", 450, 300, 10, 820, "good", "photo", 0))
        db.commit()
        return get_user_settings(uid)
    result = {
        "user_id": row[0],
        "style": row[1],
        "vevo_wm_size": row[2],
        "explicit_wm_size": row[3],
        "explicit_blur": row[4],
        "explicit_fg_size": row[5],
        "explicit_quality": row[6],
        "explicit_format": row[7],
        "notifications_enabled": bool(row[8]) if len(row) > 8 else False
    }
    return result


def update_user_setting(uid, field, value):
    """Update single user setting"""
    if field not in ALLOWED_FIELDS:
        raise ValueError(f"Unsupported field update: {field}")
    cur.execute(f"UPDATE users SET {field}=? WHERE user_id=?", (value, uid))
    db.commit()


def get_all_users():
    """Get list of all user IDs"""
    cur.execute("SELECT user_id FROM users")
    return [row[0] for row in cur.fetchall()]


def migrate_video_database():
    db.executescript('''
    CREATE TABLE IF NOT EXISTS video_folders (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
        UNIQUE(user_id, name));
    CREATE TABLE IF NOT EXISTS video_assets (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, folder_id INTEGER NOT NULL,
        filename TEXT NOT NULL, path TEXT NOT NULL, hash TEXT NOT NULL,
        duration REAL NOT NULL, width INTEGER NOT NULL, height INTEGER NOT NULL,
        fps REAL NOT NULL, codec TEXT NOT NULL, tags TEXT DEFAULT '[]',
        created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(user_id, hash));
    CREATE TABLE IF NOT EXISTS video_projects (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
        payload TEXT NOT NULL, updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS video_presets (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, name TEXT NOT NULL,
        payload TEXT NOT NULL, is_default INTEGER DEFAULT 0, UNIQUE(user_id, name));
    CREATE TABLE IF NOT EXISTS video_analysis_cache (
        cache_key TEXT PRIMARY KEY, payload TEXT NOT NULL, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS video_connections (
        user_id INTEGER PRIMARY KEY, encrypted BLOB NOT NULL,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS video_runtime_settings (
        id INTEGER PRIMARY KEY, payload TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS video_render_jobs (
        id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, project_id INTEGER NOT NULL,
        status TEXT NOT NULL, progress REAL DEFAULT 0, payload TEXT NOT NULL,
        output TEXT, error TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT DEFAULT CURRENT_TIMESTAMP);
    ''')
    db.commit()


def video_execute(sql, parameters=()):
    cursor = db.execute(sql, parameters)
    db.commit()
    return cursor.lastrowid


def video_rows(sql, parameters=()):
    cursor = db.execute(sql, parameters)
    columns = [x[0] for x in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


migrate_video_database()
