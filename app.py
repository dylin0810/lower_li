import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4

from flask import Flask, flash, redirect, render_template, request, send_from_directory, url_for
from werkzeug.utils import secure_filename


BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "instance" / "couple.db"
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

CATEGORIES = ["约会", "旅行", "吵架和好", "礼物", "纪念日", "日常", "小惊喜"]
MOODS = ["开心", "感动", "想念", "平静", "甜蜜", "难忘", "有点难过"]


def create_app():
    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-couple-memory")
    app.config["MAX_CONTENT_LENGTH"] = 8 * 1024 * 1024

    ensure_dirs()
    init_db()

    @app.context_processor
    def inject_globals():
        return {
            "categories": CATEGORIES,
            "moods": MOODS,
            "today": date.today(),
        }

    @app.route("/")
    def index():
        together_since = get_setting("together_since")
        days_together = days_between(together_since)
        latest_events = query_all(
            "SELECT * FROM events ORDER BY event_date DESC, created_at DESC LIMIT 5"
        )
        important_events = query_all(
            "SELECT * FROM events WHERE is_important = 1 ORDER BY event_date DESC LIMIT 3"
        )
        return render_template(
            "index.html",
            together_since=together_since,
            days_together=days_together,
            latest_events=latest_events,
            important_events=important_events,
            event_count=query_one("SELECT COUNT(*) AS count FROM events")["count"],
        )

    @app.route("/timeline")
    def timeline():
        category = request.args.get("category", "")
        keyword = request.args.get("q", "").strip()

        sql = "SELECT * FROM events WHERE 1 = 1"
        params = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if keyword:
            sql += " AND (title LIKE ? OR content LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        sql += " ORDER BY event_date DESC, created_at DESC"

        return render_template(
            "timeline.html",
            events=query_all(sql, params),
            selected_category=category,
            keyword=keyword,
        )

    @app.route("/events/new", methods=["GET", "POST"])
    def new_event():
        if request.method == "POST":
            error = save_event()
            if error:
                flash(error, "error")
                return render_template("event_form.html", event=None)
            flash("这条回忆已经记录好啦。", "success")
            return redirect(url_for("timeline"))
        return render_template("event_form.html", event=None)

    @app.route("/events/<int:event_id>/edit", methods=["GET", "POST"])
    def edit_event(event_id):
        event = get_event_or_404(event_id)
        if request.method == "POST":
            error = save_event(event_id, event["image_path"])
            if error:
                flash(error, "error")
                return render_template("event_form.html", event=event)
            flash("回忆已更新。", "success")
            return redirect(url_for("timeline"))
        return render_template("event_form.html", event=event)

    @app.route("/events/<int:event_id>/delete", methods=["POST"])
    def delete_event(event_id):
        event = get_event_or_404(event_id)
        execute("DELETE FROM events WHERE id = ?", [event_id])
        delete_upload(event["image_path"])
        flash("这条记录已删除。", "success")
        return redirect(url_for("timeline"))

    @app.route("/settings", methods=["GET", "POST"])
    def settings():
        if request.method == "POST":
            together_since = request.form.get("together_since") or None
            set_setting("together_since", together_since)
            flash("设置已保存。", "success")
            return redirect(url_for("index"))
        return render_template("settings.html", together_since=get_setting("together_since"))

    @app.route("/uploads/<path:filename>")
    def uploaded_file(filename):
        return send_from_directory(UPLOAD_DIR, filename)

    return app


def ensure_dirs():
    DB_PATH.parent.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                category TEXT NOT NULL,
                content TEXT NOT NULL,
                mood TEXT,
                is_important INTEGER NOT NULL DEFAULT 0,
                image_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )


def query_all(sql, params=None):
    with get_db() as conn:
        return conn.execute(sql, params or []).fetchall()


def query_one(sql, params=None):
    with get_db() as conn:
        return conn.execute(sql, params or []).fetchone()


def execute(sql, params=None):
    with get_db() as conn:
        conn.execute(sql, params or [])
        conn.commit()


def save_event(event_id=None, existing_image=None):
    title = request.form.get("title", "").strip()
    event_date = request.form.get("event_date", "").strip()
    category = request.form.get("category", "日常")
    content = request.form.get("content", "").strip()
    mood = request.form.get("mood", "")
    is_important = 1 if request.form.get("is_important") == "on" else 0

    if not title or not event_date or not content:
        return "标题、日期和内容都要填写。"

    image_path = existing_image
    uploaded = request.files.get("image")
    if uploaded and uploaded.filename:
        image_path = save_upload(uploaded)
        if existing_image and existing_image != image_path:
            delete_upload(existing_image)

    now = datetime.now().isoformat(timespec="seconds")
    if event_id:
        execute(
            """
            UPDATE events
            SET title = ?, event_date = ?, category = ?, content = ?, mood = ?,
                is_important = ?, image_path = ?, updated_at = ?
            WHERE id = ?
            """,
            [title, event_date, category, content, mood, is_important, image_path, now, event_id],
        )
    else:
        execute(
            """
            INSERT INTO events (
                title, event_date, category, content, mood, is_important,
                image_path, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [title, event_date, category, content, mood, is_important, image_path, now, now],
        )
    return None


def save_upload(file_storage):
    filename = secure_filename(file_storage.filename)
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        flash("图片只支持 png、jpg、jpeg、gif、webp。", "error")
        return None
    stored_name = f"{uuid4().hex}.{ext}"
    file_storage.save(UPLOAD_DIR / stored_name)
    return stored_name


def delete_upload(image_path):
    if not image_path:
        return
    target = UPLOAD_DIR / image_path
    if target.exists() and target.is_file():
        target.unlink()


def get_event_or_404(event_id):
    event = query_one("SELECT * FROM events WHERE id = ?", [event_id])
    if not event:
        from flask import abort

        abort(404)
    return event


def get_setting(key):
    row = query_one("SELECT value FROM settings WHERE key = ?", [key])
    return row["value"] if row else None


def set_setting(key, value):
    execute(
        """
        INSERT INTO settings (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        [key, value],
    )


def days_between(date_text):
    if not date_text:
        return None
    try:
        start = datetime.strptime(date_text, "%Y-%m-%d").date()
    except ValueError:
        return None
    return (date.today() - start).days + 1


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
