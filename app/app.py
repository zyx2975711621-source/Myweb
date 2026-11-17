import os
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename
from flask import Flask, jsonify, request, session

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "users.db")
UPLOAD_DIR = "/var/www/caddy/uploads"
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "gif"}
MAX_IMAGES = 5
MAX_FILE_SIZE = 5 * 1024 * 1024
CN_TZ = timezone(timedelta(hours=8))

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ["SECRET_KEY"],
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=True,
    MAX_CONTENT_LENGTH=30 * 1024 * 1024,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS foods (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    address_text TEXT NOT NULL DEFAULT '',
    lat REAL,
    lng REAL,
    rating INTEGER NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    visited_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS food_images (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id INTEGER NOT NULL,
    filename TEXT NOT NULL,
    sort INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS likes (
    user_id INTEGER NOT NULL,
    food_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, food_id)
);
CREATE TABLE IF NOT EXISTS favorites (
    user_id INTEGER NOT NULL,
    food_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, food_id)
);
CREATE TABLE IF NOT EXISTS wanttoeat (
    user_id INTEGER NOT NULL,
    food_id INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, food_id)
);
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    food_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def db_connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def query(sql, args=(), one=False):
    with closing(db_connect()) as conn:
        cur = conn.execute(sql, args)
        rows = cur.fetchall()
        conn.commit()
        if one:
            return rows[0] if rows else None
        return rows


def insert(sql, args=()):
    with closing(db_connect()) as conn:
        cur = conn.execute(sql, args)
        conn.commit()
        return cur.lastrowid


with closing(db_connect()) as conn:
    conn.executescript(SCHEMA)
    try:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
    except sqlite3.OperationalError:
        pass
    conn.commit()


# ---------- helpers ----------

def current_user():
    uid = session.get("uid")
    if uid is None:
        return None
    row = query("SELECT id, username, role FROM users WHERE id = ?", (uid,), one=True)
    return dict(row) if row else None


def is_admin(u):
    return bool(u) and u["role"] == "admin"


def is_editor(u):
    return bool(u) and u["role"] in ("editor", "admin")


def require_login():
    u = current_user()
    if u is None:
        return None, (jsonify(ok=False, message="请先登录"), 401)
    return u, None


def require_editor():
    u, err = require_login()
    if err:
        return None, err
    if not is_editor(u):
        return None, (jsonify(ok=False, message="需要编辑者权限"), 403)
    return u, None


def require_admin():
    u, err = require_login()
    if err:
        return None, err
    if not is_admin(u):
        return None, (jsonify(ok=False, message="需要管理员权限"), 403)
    return u, None


def public_user(row):
    return {"id": row["id"], "username": row["username"], "role": row["role"]}


def cn_now_str():
    return datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M")


def parse_visited_at(raw):
    raw = str(raw or "").strip()
    if not raw:
        return cn_now_str()
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d %H:%M")
        except ValueError:
            continue
    return None


def parse_coord(raw):  # kept for compatibility; map picker removed
    return None


def save_image(file):
    name = secure_filename(file.filename or "")
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    if ext not in ALLOWED_EXT:
        ctype = (file.mimetype or "").split("/")[-1].lower()
        ext = ctype if ctype in ALLOWED_EXT else ""
    if not ext:
        return None, "不支持的图片格式"
    filename = uuid.uuid4().hex + "." + ext
    path = os.path.join(UPLOAD_DIR, filename)
    file.save(path)
    if os.path.getsize(path) > MAX_FILE_SIZE:
        os.remove(path)
        return None, "单张图片不能超过 5MB"
    return filename, None


def delete_image_file(filename):
    path = os.path.join(UPLOAD_DIR, filename)
    if os.path.isfile(path):
        try:
            os.remove(path)
        except OSError:
            pass


FOOD_CARD_SQL = """
SELECT f.id, f.title, f.address_text, f.lat, f.lng, f.rating, f.visited_at,
       f.created_at, f.user_id, u.username AS author,
       (SELECT filename FROM food_images WHERE food_id = f.id ORDER BY sort, id LIMIT 1) AS cover,
       (SELECT COUNT(*) FROM food_images WHERE food_id = f.id) AS image_count,
       (SELECT COUNT(*) FROM likes WHERE food_id = f.id) AS like_count,
       (SELECT COUNT(*) FROM comments WHERE food_id = f.id) AS comment_count,
       EXISTS(SELECT 1 FROM likes WHERE food_id = f.id AND user_id = @uid) AS liked,
       (SELECT COUNT(*) FROM wanttoeat WHERE food_id = f.id) AS want_count,
       EXISTS(SELECT 1 FROM wanttoeat WHERE food_id = f.id AND user_id = @uid) AS wanted,
       EXISTS(SELECT 1 FROM favorites WHERE food_id = f.id AND user_id = @uid) AS favorited,
       EXISTS(SELECT 1 FROM favorites WHERE food_id = f.id AND user_id = @uid) AS fav_order_src
FROM foods f JOIN users u ON u.id = f.user_id
"""


def food_cards(sql_tail, args, uid):
    sql = FOOD_CARD_SQL + sql_tail
    rows = query(sql, args)
    out = []
    for r in rows:
        d = dict(r)
        d.pop("fav_order_src", None)
        d["liked"] = bool(d["liked"])
        d["favorited"] = bool(d["favorited"])
        d["can_edit"] = uid is not None and (d["user_id"] == uid)
        out.append(d)
    return out


# ---------- auth ----------

@app.post("/api/register")
def register():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not (2 <= len(username) <= 20) or not re.match(r"^[\w\-.]{2,20}$", username, re.UNICODE):
        return jsonify(ok=False, message="用户名需为 2-20 位字母、数字、下划线、点或短横线"), 400
    if not (6 <= len(password) <= 64):
        return jsonify(ok=False, message="密码长度需为 6-64 位"), 400
    try:
        uid = insert(
            "INSERT INTO users (username, password_hash, role) VALUES (?, ?, 'user')",
            (username, generate_password_hash(password)),
        )
    except sqlite3.IntegrityError:
        return jsonify(ok=False, message="该用户名已被注册"), 409
    session["uid"] = uid
    return jsonify(ok=True, user={"id": uid, "username": username, "role": "user"})


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username") or "").strip()
    password = str(data.get("password") or "")
    if not username or not password:
        return jsonify(ok=False, message="请输入用户名和密码"), 400
    row = query(
        "SELECT id, username, password_hash, role FROM users WHERE username = ?",
        (username,), one=True,
    )
    if row is None or not check_password_hash(row["password_hash"], password):
        return jsonify(ok=False, message="用户名或密码错误"), 401
    session["uid"] = row["id"]
    return jsonify(ok=True, user=public_user(row))


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = current_user()
    return jsonify(ok=True, user=u)


# ---------- foods ----------

@app.get("/api/foods")
def list_foods():
    u = current_user()
    uid = u["id"] if u else -1
    q = str(request.args.get("q") or "").strip()
    stars = request.args.get("stars", type=int)
    sort = request.args.get("sort") or "latest"
    tail = " WHERE 1=1"
    args = {"uid": uid}
    if q:
        tail += " AND f.title LIKE @q"
        args["q"] = "%" + q + "%"
    if stars and 1 <= stars <= 5:
        tail += " AND f.rating = @stars"
        args["stars"] = stars
    tail += " ORDER BY "
    tail += "f.rating DESC, f.visited_at DESC, f.id DESC" if sort == "rating" \
        else "f.visited_at DESC, f.id DESC"
    tail += " LIMIT 200"
    return jsonify(ok=True, foods=food_cards(tail, args, uid))


@app.get("/api/foods/<int:fid>")
def food_detail(fid):
    u = current_user()
    uid = u["id"] if u else -1
    rows = food_cards(" WHERE f.id = @fid LIMIT 1", {"uid": uid, "fid": fid}, uid)
    if not rows:
        return jsonify(ok=False, message="记录不存在"), 404
    food = rows[0]
    imgs = query("SELECT id, filename FROM food_images WHERE food_id = ? ORDER BY sort, id", (fid,))
    food["images"] = [dict(i) for i in imgs]
    cmts = query(
        "SELECT c.id, c.user_id, c.content, c.created_at, u.username "
        "FROM comments c JOIN users u ON u.id = c.user_id "
        "WHERE c.food_id = ? ORDER BY c.created_at DESC, c.id DESC", (fid,))
    food["comments"] = [dict(c) for c in cmts]
    food["is_admin"] = is_admin(u)
    if u:
        food["can_edit"] = is_admin(u) or (food["user_id"] == uid and is_editor(u))
        food["can_comment"] = True
    else:
        food["can_edit"] = False
        food["can_comment"] = False
    return jsonify(ok=True, food=food)


@app.post("/api/foods")
def create_food():
    u, err = require_editor()
    if err:
        return err
    form = request.form
    title = str(form.get("title") or "").strip()
    content = str(form.get("content") or "").strip()
    rating = form.get("rating", type=int)
    address_text = str(form.get("address_text") or "").strip()
    visited_at = parse_visited_at(form.get("visited_at"))
    if not (1 <= len(title) <= 50):
        return jsonify(ok=False, message="店名需为 1-50 字"), 400
    if rating is None or not (1 <= rating <= 5):
        return jsonify(ok=False, message="评分需为 1-5 级"), 400
    if len(content) > 2000:
        return jsonify(ok=False, message="评价文字最多 2000 字"), 400
    if visited_at is None:
        return jsonify(ok=False, message="打卡时间格式不正确"), 400
    files = [f for f in request.files.getlist("files") if f and f.filename]
    if len(files) > MAX_IMAGES:
        return jsonify(ok=False, message="最多上传 5 张图片"), 400
    filenames = []
    for f in files:
        filename, ferr = save_image(f)
        if ferr:
            for fn in filenames:
                delete_image_file(fn)
            return jsonify(ok=False, message=ferr), 400
        filenames.append(filename)
    fid = insert(
        "INSERT INTO foods (user_id, title, address_text, lat, lng, rating, content, visited_at, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (u["id"], title, address_text, None, None, rating, content, visited_at, cn_now_str(), cn_now_str()),
    )
    for i, fn in enumerate(filenames):
        insert("INSERT INTO food_images (food_id, filename, sort) VALUES (?, ?, ?)", (fid, fn, i))
    return jsonify(ok=True, id=fid)


@app.put("/api/foods/<int:fid>")
def update_food(fid):
    u, err = require_editor()
    if err:
        return err
    row = query("SELECT id, user_id FROM foods WHERE id = ?", (fid,), one=True)
    if row is None:
        return jsonify(ok=False, message="记录不存在"), 404
    if not (is_admin(u) or row["user_id"] == u["id"]):
        return jsonify(ok=False, message="只能编辑自己发布的记录"), 403
    form = request.form
    title = str(form.get("title") or "").strip()
    content = str(form.get("content") or "").strip()
    rating = form.get("rating", type=int)
    address_text = str(form.get("address_text") or "").strip()
    visited_at = parse_visited_at(form.get("visited_at"))
    lat = parse_coord(form.get("lat"))
    lng = parse_coord(form.get("lng"))
    if not (1 <= len(title) <= 50):
        return jsonify(ok=False, message="店名需为 1-50 字"), 400
    if rating is None or not (1 <= rating <= 5):
        return jsonify(ok=False, message="评分需为 1-5 级"), 400
    if len(content) > 2000:
        return jsonify(ok=False, message="评价文字最多 2000 字"), 400
    if visited_at is None:
        return jsonify(ok=False, message="打卡时间格式不正确"), 400
    keep_ids = []
    try:
        import json as _json
        keep_ids = [int(x) for x in _json.loads(form.get("keep_images") or "[]")]
    except (ValueError, TypeError):
        keep_ids = []
    existing = query("SELECT id, filename FROM food_images WHERE food_id = ? ORDER BY sort, id", (fid,))
    existing_map = {r["id"]: r["filename"] for r in existing}
    new_files = [f for f in request.files.getlist("files") if f and f.filename]
    if len(keep_ids) + len(new_files) > MAX_IMAGES:
        return jsonify(ok=False, message="最多保留 5 张图片"), 400
    if any(kid not in existing_map for kid in keep_ids):
        return jsonify(ok=False, message="要保留的图片不存在"), 400
    filenames = []
    for f in new_files:
        filename, ferr = save_image(f)
        if ferr:
            for fn in filenames:
                delete_image_file(fn)
            return jsonify(ok=False, message=ferr), 400
        filenames.append(filename)
    with closing(db_connect()) as conn:
        conn.execute(
            "UPDATE foods SET title=?, address_text=?, lat=?, lng=?, rating=?, content=?, visited_at=?, updated_at=? WHERE id=?",
            (title, address_text, lat, lng, rating, content, visited_at, cn_now_str(), fid),
        )
        sort_i = 0
        for kid in keep_ids:
            conn.execute("UPDATE food_images SET sort=? WHERE id=?", (sort_i, kid))
            sort_i += 1
        for fn in filenames:
            conn.execute("INSERT INTO food_images (food_id, filename, sort) VALUES (?, ?, ?)", (fid, fn, sort_i))
            sort_i += 1
        for r in existing:
            if r["id"] not in keep_ids:
                conn.execute("DELETE FROM food_images WHERE id=?", (r["id"],))
        conn.commit()
    for r in existing:
        if r["id"] not in keep_ids:
            delete_image_file(r["filename"])
    return jsonify(ok=True, id=fid)


@app.delete("/api/foods/<int:fid>")
def delete_food(fid):
    u, err = require_admin()
    if err:
        return err
    row = query("SELECT id FROM foods WHERE id = ?", (fid,), one=True)
    if row is None:
        return jsonify(ok=False, message="记录不存在"), 404
    imgs = query("SELECT filename FROM food_images WHERE food_id = ?", (fid,))
    with closing(db_connect()) as conn:
        conn.execute("DELETE FROM food_images WHERE food_id=?", (fid,))
        conn.execute("DELETE FROM likes WHERE food_id=?", (fid,))
        conn.execute("DELETE FROM favorites WHERE food_id=?", (fid,))
        conn.execute("DELETE FROM comments WHERE food_id=?", (fid,))
        conn.execute("DELETE FROM foods WHERE id=?", (fid,))
        conn.commit()
    for r in imgs:
        delete_image_file(r["filename"])
    return jsonify(ok=True)


# ---------- like / favorite ----------

@app.post("/api/foods/<int:fid>/like")
def toggle_like(fid):
    u, err = require_login()
    if err:
        return err
    if query("SELECT id FROM foods WHERE id=?", (fid,), one=True) is None:
        return jsonify(ok=False, message="记录不存在"), 404
    exists = query("SELECT 1 AS x FROM likes WHERE user_id=? AND food_id=?", (u["id"], fid), one=True)
    if exists:
        query("DELETE FROM likes WHERE user_id=? AND food_id=?", (u["id"], fid))
        liked = False
    else:
        insert("INSERT INTO likes (user_id, food_id) VALUES (?, ?)", (u["id"], fid))
        liked = True
    n = query("SELECT COUNT(*) AS n FROM likes WHERE food_id=?", (fid,), one=True)["n"]
    return jsonify(ok=True, liked=liked, like_count=n)


@app.post("/api/foods/<int:fid>/favorite")
def toggle_favorite(fid):
    u, err = require_login()
    if err:
        return err
    if query("SELECT id FROM foods WHERE id=?", (fid,), one=True) is None:
        return jsonify(ok=False, message="记录不存在"), 404
    exists = query("SELECT 1 AS x FROM favorites WHERE user_id=? AND food_id=?", (u["id"], fid), one=True)
    if exists:
        query("DELETE FROM favorites WHERE user_id=? AND food_id=?", (u["id"], fid))
        favorited = False
    else:
        insert("INSERT INTO favorites (user_id, food_id) VALUES (?, ?)", (u["id"], fid))
        favorited = True
    return jsonify(ok=True, favorited=favorited)


@app.post("/api/foods/<int:fid>/want")
def toggle_want(fid):
    u, err = require_login()
    if err:
        return err
    if query("SELECT id FROM foods WHERE id=?", (fid,), one=True) is None:
        return jsonify(ok=False, message="记录不存在"), 404
    exists = query("SELECT 1 AS x FROM wanttoeat WHERE user_id=? AND food_id=?", (u["id"], fid), one=True)
    if exists:
        query("DELETE FROM wanttoeat WHERE user_id=? AND food_id=?", (u["id"], fid))
        wanted = False
    else:
        insert("INSERT INTO wanttoeat (user_id, food_id) VALUES (?, ?)", (u["id"], fid))
        wanted = True
    n = query("SELECT COUNT(*) AS n FROM wanttoeat WHERE food_id=?", (fid,), one=True)["n"]
    return jsonify(ok=True, wanted=wanted, want_count=n)


@app.get("/api/wanttoeat")
def wanttoeat_list():
    u, err = require_login()
    if err:
        return err
    tail = " JOIN wanttoeat wt ON wt.food_id = f.id AND wt.user_id = @uid ORDER BY wt.created_at DESC LIMIT 200"
    return jsonify(ok=True, foods=food_cards(tail, {"uid": u["id"]}, u["id"]))


@app.get("/api/favorites")
def favorites_list():
    u, err = require_login()
    if err:
        return err
    tail = " JOIN favorites fv ON fv.food_id = f.id AND fv.user_id = @uid ORDER BY fv.created_at DESC LIMIT 200"
    return jsonify(ok=True, foods=food_cards(tail, {"uid": u["id"]}, u["id"]))


# ---------- comments ----------

@app.post("/api/foods/<int:fid>/comments")
def add_comment(fid):
    u, err = require_login()
    if err:
        return err
    if query("SELECT id FROM foods WHERE id=?", (fid,), one=True) is None:
        return jsonify(ok=False, message="记录不存在"), 404
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not (1 <= len(content) <= 500):
        return jsonify(ok=False, message="评论需为 1-500 字"), 400
    cid = insert(
        "INSERT INTO comments (food_id, user_id, content) VALUES (?, ?, ?)",
        (fid, u["id"], content),
    )
    return jsonify(ok=True, comment={
        "id": cid, "food_id": fid, "user_id": u["id"],
        "username": u["username"], "content": content, "created_at": cn_now_str(),
    })


@app.put("/api/comments/<int:cid>")
def edit_comment(cid):
    u, err = require_login()
    if err:
        return err
    row = query("SELECT id, user_id, food_id FROM comments WHERE id=?", (cid,), one=True)
    if row is None:
        return jsonify(ok=False, message="评论不存在"), 404
    if not (is_admin(u) or row["user_id"] == u["id"]):
        return jsonify(ok=False, message="只能管理自己的评论"), 403
    data = request.get_json(silent=True) or {}
    content = str(data.get("content") or "").strip()
    if not (1 <= len(content) <= 500):
        return jsonify(ok=False, message="评论需为 1-500 字"), 400
    query("UPDATE comments SET content=? WHERE id=?", (content, cid))
    return jsonify(ok=True)


@app.delete("/api/comments/<int:cid>")
def delete_comment(cid):
    u, err = require_login()
    if err:
        return err
    row = query("SELECT id, user_id FROM comments WHERE id=?", (cid,), one=True)
    if row is None:
        return jsonify(ok=False, message="评论不存在"), 404
    if not (is_admin(u) or row["user_id"] == u["id"]):
        return jsonify(ok=False, message="只能管理自己的评论"), 403
    query("DELETE FROM comments WHERE id=?", (cid,))
    return jsonify(ok=True)


# ---------- profile ----------

@app.get("/api/profile")
def profile():
    u, err = require_login()
    if err:
        return err
    uid = u["id"]
    comments = query(
        "SELECT c.id, c.content, c.created_at, c.food_id, f.title AS food_title "
        "FROM comments c JOIN foods f ON f.id = c.food_id "
        "WHERE c.user_id = ? ORDER BY c.created_at DESC, c.id DESC LIMIT 100", (uid,))
    foods = []
    if is_editor(u):
        foods = food_cards(
            " WHERE f.user_id = @uid ORDER BY f.visited_at DESC, f.id DESC LIMIT 100",
            {"uid": uid}, uid)
    return jsonify(ok=True, user=public_user(u),
                   comments=[dict(c) for c in comments],
                   foods=foods)


@app.post("/api/profile/update")
def profile_update():
    u, err = require_login()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    new_username = str(data.get("username") or "").strip()
    old_password = str(data.get("old_password") or "")
    new_password = str(data.get("new_password") or "")
    if not new_username:
        new_username = u["username"]  # 未提供用户名 = 不修改用户名
    row = query("SELECT id, username, password_hash FROM users WHERE id=?", (u["id"],), one=True)
    if row is None:
        return jsonify(ok=False, message="用户不存在"), 404
    if new_username == row["username"] and not new_password:
        return jsonify(ok=False, message="没有需要保存的修改"), 400
    if not old_password or not check_password_hash(row["password_hash"], old_password):
        return jsonify(ok=False, message="旧密码不正确"), 403
    # 全部校验通过后才写入，避免改了一半失败
    if new_username != row["username"]:
        if not (2 <= len(new_username) <= 20) or not re.match(r"^[\w\-.]{2,20}$", new_username, re.UNICODE):
            return jsonify(ok=False, message="用户名需为 2-20 位字母、数字、下划线、点或短横线"), 400
        if query("SELECT 1 AS x FROM users WHERE username=? AND id<>?", (new_username, u["id"]), one=True):
            return jsonify(ok=False, message="该用户名已被注册"), 409
    if new_password and not (6 <= len(new_password) <= 64):
        return jsonify(ok=False, message="新密码长度需为 6-64 位"), 400
    changed = False
    if new_username != row["username"]:
        query("UPDATE users SET username=? WHERE id=?", (new_username, u["id"]))
        changed = True
    if new_password:
        query("UPDATE users SET password_hash=? WHERE id=?",
              (generate_password_hash(new_password), u["id"]))
        changed = True
    if not changed:
        return jsonify(ok=False, message="没有需要保存的修改"), 400
    return jsonify(ok=True)


# ---------- admin ----------

@app.get("/api/admin/users")
def admin_users():
    u, err = require_admin()
    if err:
        return err
    rows = query(
        "SELECT u.id, u.username, u.role, u.created_at, "
        "(SELECT COUNT(*) FROM foods WHERE user_id=u.id) AS food_count, "
        "(SELECT COUNT(*) FROM comments WHERE user_id=u.id) AS comment_count "
        "FROM users u ORDER BY u.id"
    )
    return jsonify(ok=True, users=[dict(r) for r in rows])


@app.post("/api/admin/users/<int:uid>/role")
def admin_set_role(uid):
    u, err = require_admin()
    if err:
        return err
    data = request.get_json(silent=True) or {}
    role = str(data.get("role") or "")
    if role not in ("user", "editor"):
        return jsonify(ok=False, message="角色只能设为 普通用户 或 编辑者"), 400
    row = query("SELECT id, role FROM users WHERE id=?", (uid,), one=True)
    if row is None:
        return jsonify(ok=False, message="用户不存在"), 404
    if row["role"] == "admin":
        return jsonify(ok=False, message="不能修改管理员的角色"), 403
    query("UPDATE users SET role=? WHERE id=?", (role, uid))
    return jsonify(ok=True)


@app.get("/api/admin/comments")
def admin_comments():
    u, err = require_admin()
    if err:
        return err
    rows = query(
        "SELECT c.id, c.content, c.created_at, c.user_id, u.username, c.food_id, f.title AS food_title "
        "FROM comments c JOIN users u ON u.id=c.user_id JOIN foods f ON f.id=c.food_id "
        "ORDER BY c.created_at DESC, c.id DESC LIMIT 100"
    )
    return jsonify(ok=True, comments=[dict(r) for r in rows])


@app.errorhandler(Exception)
def handle_error(e):
    if request.path.startswith("/api/"):
        if isinstance(e, HTTPException):
            return jsonify(ok=False, message=e.description), e.code
        app.logger.exception("api error")
        return jsonify(ok=False, message="服务器内部错误"), 500
    if isinstance(e, HTTPException):
        return e
    raise e
