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


migrate_database()


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
