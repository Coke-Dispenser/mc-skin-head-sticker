import base64
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import warnings
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageOps, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field

DATA = Path(os.environ.get("DATA_DIR", "./data"))
DATA.mkdir(parents=True, exist_ok=True)
Image.MAX_IMAGE_PIXELS = 40_000_000
warnings.simplefilter("error", Image.DecompressionBombWarning)
COOKIE = "bcl_photo_session"
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
login_attempts = {}
attempt_lock = threading.Lock()
export_lock = threading.Lock()


@contextmanager
def database(write=False):
    connection = sqlite3.connect(DATA / "studio.sqlite3", timeout=15)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


with database() as db:
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript("""
    CREATE TABLE IF NOT EXISTS sessions (
      token TEXT PRIMARY KEY, role TEXT NOT NULL, expires REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS photos (
      id TEXT PRIMARY KEY, title TEXT NOT NULL, width INTEGER NOT NULL,
      height INTEGER NOT NULL, default_size REAL NOT NULL DEFAULT 0.08,
      version INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
    );
    CREATE TABLE IF NOT EXISTS avatars (
      id TEXT PRIMARY KEY, photo_id TEXT NOT NULL REFERENCES photos(id) ON DELETE CASCADE,
      username TEXT NOT NULL, x REAL NOT NULL, y REAL NOT NULL,
      size REAL NOT NULL, rotation REAL NOT NULL DEFAULT 0,
      version INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS avatars_photo ON avatars(photo_id);
    CREATE TABLE IF NOT EXISTS photo_thumbnails (
      photo_id TEXT PRIMARY KEY REFERENCES photos(id) ON DELETE CASCADE,
      version INTEGER NOT NULL, content BLOB NOT NULL
    );
    """)


def migrate_avatar_layers(db):
    if "z_index" in {row["name"] for row in db.execute("PRAGMA table_info(avatars)")}:
        return
    db.execute("ALTER TABLE avatars ADD COLUMN z_index INTEGER NOT NULL DEFAULT 0")
    # Preserve the existing composition, including ties in creation timestamps.
    rows = db.execute("SELECT id,photo_id FROM avatars ORDER BY photo_id,created,id").fetchall()
    previous_photo, position = None, 0
    for row in rows:
        if row["photo_id"] != previous_photo:
            previous_photo, position = row["photo_id"], 0
        db.execute("UPDATE avatars SET z_index=? WHERE id=?", (position, row["id"]))
        position += 1


with database(write=True) as db:
    migrate_avatar_layers(db)


@app.middleware("http")
async def request_guards(request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc != request.headers.get("host"):
            return JSONResponse({"detail": "请从本站页面提交操作"}, status_code=403)
        try:
            too_large = int(request.headers.get("content-length", "0")) > 32 * 1024 * 1024
        except ValueError:
            return JSONResponse({"detail": "无效请求"}, status_code=400)
        if too_large:
            return JSONResponse({"detail": "单个文件最大 30 MB"}, status_code=413)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api"):
        response.headers["Cache-Control"] = "private, no-store"
    return response


def identity(request: Request):
    raw = request.cookies.get(COOKIE, "")
    digest = hashlib.sha256(raw.encode()).hexdigest()
    with database() as db:
        row = db.execute("SELECT role FROM sessions WHERE token=? AND expires>?", (digest, time.time())).fetchone()
    if not row:
        raise HTTPException(401, "请先登录")
    return row["role"]


def administrator(role=Depends(identity)):
    if role != "admin":
        raise HTTPException(403, "只有管理员可以管理合照")
    return role


class Login(BaseModel):
    username: str = Field(max_length=80)
    password: str = Field(max_length=256)


@app.post("/api/login")
def login(body: Login, request: Request):
    key = request.client.host
    now = time.time()
    with attempt_lock:
        for expired in [k for k, v in login_attempts.items() if v[0] < now - 600]:
            del login_attempts[expired]
        since, count = login_attempts.get(key, (now, 0))
        if count >= 20:
            raise HTTPException(429, "登录尝试过多，请在十分钟后再试")
        login_attempts[key] = (since, count + 1)
    role = None
    for candidate in ("admin", "member"):
        username = os.environ.get(candidate.upper() + "_USER", candidate)
        stored = os.environ.get(candidate.upper() + "_HASH", "")
        if body.username == username and stored:
            salt, expected = stored.split(":")
            actual = hashlib.pbkdf2_hmac("sha256", body.password.encode(), bytes.fromhex(salt), 260000).hex()
            if hmac.compare_digest(expected, actual):
                role = candidate
    if not role:
        raise HTTPException(401, "账号或密码错误")
    with attempt_lock:
        login_attempts.pop(key, None)
    token = secrets.token_urlsafe(32)
    with database(write=True) as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (now,))
        db.execute("INSERT INTO sessions VALUES (?,?,?)",
                   (hashlib.sha256(token.encode()).hexdigest(), role, now + 7 * 86400))
    result = JSONResponse({"role": role})
    result.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=7 * 86400,
                      secure=os.environ.get("COOKIE_SECURE") == "1")
    return result


@app.get("/api/session")
def session(role=Depends(identity)):
    return {"role": role}


@app.post("/api/logout")
def logout(request: Request):
    token = hashlib.sha256(request.cookies.get(COOKIE, "").encode()).hexdigest()
    with database(write=True) as db:
        db.execute("DELETE FROM sessions WHERE token=?", (token,))
    response = JSONResponse({"ok": True})
    response.delete_cookie(COOKIE)
    return response


def photo_row(db, photo_id):
    row = db.execute("SELECT * FROM photos WHERE id=?", (photo_id,)).fetchone()
    if not row:
        raise HTTPException(404, "这张合照已被删除")
    return row


def snapshot(db, photo_id):
    result = dict(photo_row(db, photo_id))
    result["avatars"] = [dict(r) for r in db.execute(
        "SELECT * FROM avatars WHERE photo_id=? ORDER BY z_index,created,id", (photo_id,))]
    return result


@app.get("/api/photos")
def photos(role=Depends(identity)):
    with database() as db:
        return [dict(r) for r in db.execute("""
        SELECT photos.*, (SELECT COUNT(*) FROM avatars WHERE photo_id=photos.id) AS count
        FROM photos ORDER BY created DESC
        """)]


def read_image(upload):
    data = upload.file.read(30 * 1024 * 1024 + 1)
    if len(data) > 30 * 1024 * 1024:
        raise HTTPException(413, "单个文件最大 30 MB")
    try:
        image = Image.open(io.BytesIO(data))
        if image.format not in {"JPEG", "PNG", "WEBP"}:
            raise HTTPException(400, "请上传 JPG、PNG 或 WebP 图片")
        if max(image.size) > 20000:
            raise HTTPException(400, "图片最长边超过 20000 像素，请缩小后上传")
        image.load()
        return ImageOps.exif_transpose(image)
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(400, "图片无法读取，或超过 4000 万像素")


@app.post("/api/photos")
def upload_photo(file: UploadFile = File(...), role=Depends(administrator)):
    image = read_image(file).convert("RGB")
    photo_id = secrets.token_hex(16)
    image.save(DATA / (photo_id + ".jpg"), quality=96)
    preview = image.copy()
    preview.thumbnail((1800, 1800))
    preview.save(DATA / (photo_id + "-preview.jpg"), quality=88)
    title = Path(file.filename or "社团合照").stem[:100]
    try:
        with database(write=True) as db:
            db.execute("INSERT INTO photos (id,title,width,height,created) VALUES (?,?,?,?,?)",
                       (photo_id, title, image.width, image.height, time.time()))
            result = snapshot(db, photo_id)
    except Exception:
        for suffix in (".jpg", "-preview.jpg"):
            (DATA / (photo_id + suffix)).unlink(missing_ok=True)
        raise
    return result


@app.get("/api/photos/{photo_id}")
def get_photo(photo_id: str, role=Depends(identity)):
    with database() as db:
        return snapshot(db, photo_id)


@app.get("/api/photos/{photo_id}/image")
def photo_image(photo_id: str, preview: bool = False, role=Depends(identity)):
    with database() as db:
        photo_row(db, photo_id)
    return FileResponse(DATA / (photo_id + ("-preview.jpg" if preview else ".jpg")), media_type="image/jpeg")


@app.delete("/api/photos/{photo_id}")
def delete_photo(photo_id: str, role=Depends(administrator)):
    with database(write=True) as db:
        data = snapshot(db, photo_id)
        db.execute("DELETE FROM photos WHERE id=?", (photo_id,))
    for filename in [photo_id + ".jpg", photo_id + "-preview.jpg"] + [a["id"] + ".png" for a in data["avatars"]]:
        (DATA / filename).unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/photos/{photo_id}/thumbnail")
def photo_thumbnail(photo_id: str, role=Depends(identity)):
    # One cached image per photo. The transaction also prevents duplicate renders
    # and keeps edits/deletions from changing source files during composition.
    with database(write=True) as db:
        photo = photo_row(db, photo_id)
        cached = db.execute(
            "SELECT content FROM photo_thumbnails WHERE photo_id=? AND version=?",
            (photo_id, photo["version"]),
        ).fetchone()
        if cached:
            return Response(cached["content"], media_type="image/jpeg")
        with Image.open(DATA / (photo_id + "-preview.jpg")) as image:
            image.thumbnail((640, 640), Image.Resampling.LANCZOS)
            background = image.convert("RGBA")
        avatars = [(dict(a), (DATA / (a["id"] + ".png")).read_bytes()) for a in db.execute(
            "SELECT * FROM avatars WHERE photo_id=? ORDER BY z_index,created,id", (photo_id,))]
        composite_avatars(background, avatars)
        output = io.BytesIO()
        background.convert("RGB").save(output, format="JPEG", quality=85)
        content = output.getvalue()
        db.execute(
            """INSERT INTO photo_thumbnails (photo_id,version,content) VALUES (?,?,?)
               ON CONFLICT(photo_id) DO UPDATE SET version=excluded.version, content=excluded.content""",
            (photo_id, photo["version"], content),
        )
    return Response(content, media_type="image/jpeg")


def minecraft_face(username):
    if not re.fullmatch(r"[A-Za-z0-9_]{3,16}", username):
        raise HTTPException(400, "请输入 Java 版正版 ID，长度 3–16 位，可含字母、数字和下划线")
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get("https://api.mojang.com/users/profiles/minecraft/" + username)
            if response.status_code in (204, 404):
                raise HTTPException(404, "没有找到这个正版 ID，请检查拼写或上传自己的头像")
            response.raise_for_status()
            account = response.json()
            response = client.get("https://sessionserver.mojang.com/session/minecraft/profile/" + account["id"])
            response.raise_for_status()
            properties = response.json().get("properties", [])
            encoded = next((p["value"] for p in properties if p["name"] == "textures"), None)
            textures = json.loads(base64.b64decode(encoded)) if encoded else {}
            skin = textures.get("textures", {}).get("SKIN", {}).get("url")
            if not skin:
                response = client.get("https://mc-heads.net/avatar/" + account["id"] + "/8")
                response.raise_for_status()
                face = Image.open(io.BytesIO(response.content)).convert("RGBA")
                if face.size != (8, 8):
                    raise ValueError("Invalid default avatar")
                return account["name"], face
            parsed = urlparse(skin)
            if parsed.hostname != "textures.minecraft.net" or not re.fullmatch(r"/texture/[a-fA-F0-9]+", parsed.path):
                raise ValueError("Unexpected skin URL")
            response = client.get("https://textures.minecraft.net" + parsed.path)
            response.raise_for_status()
            if len(response.content) > 2 * 1024 * 1024:
                raise ValueError("Skin too large")
            image = Image.open(io.BytesIO(response.content)).convert("RGBA")
            if image.size not in ((64, 64), (64, 32)):
                raise ValueError("Invalid skin dimensions")
            face = image.crop((8, 8, 16, 16))
            hat = image.crop((40, 8, 48, 16))
            # Older skins often fill the unused hat area with an opaque solid color.
            if len(hat.getcolors(64) or []) > 1 or hat.getextrema()[3][0] < 255:
                face.alpha_composite(hat)
            return account["name"], face
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, StopIteration, OSError):
        raise HTTPException(502, "正版皮肤服务暂时不可用，请稍后重试或上传自己的头像")


@app.post("/api/photos/{photo_id}/avatars")
def add_avatar(photo_id: str, username: str = Form(...), mode: str = Form(...),
               file: UploadFile | None = File(None), role=Depends(identity)):
    username = username.strip()
    if not username or len(username) > 40:
        raise HTTPException(400, "ID 长度需要为 1–40 个字符")
    with database() as db:
        photo_row(db, photo_id)
    if mode == "minecraft":
        username, image = minecraft_face(username)
    elif mode == "upload" and file:
        image = read_image(file).convert("RGBA")
    else:
        raise HTTPException(400, "请选择正版 ID 或上传头像")
    image.thumbnail((512, 512), Image.Resampling.NEAREST)
    side = max(image.size)
    square = Image.new("RGBA", (side, side))
    square.paste(image, ((side - image.width) // 2, (side - image.height) // 2))
    square = square.resize((512, 512), Image.Resampling.NEAREST)
    avatar_id = secrets.token_hex(16)
    square.save(DATA / (avatar_id + ".png"))
    try:
        with database(write=True) as db:
            photo = photo_row(db, photo_id)
            count = db.execute("SELECT COUNT(*) FROM avatars WHERE photo_id=?", (photo_id,)).fetchone()[0]
            if count >= 150:
                raise HTTPException(400, "单张照片最多添加 150 个头像")
            if db.execute("SELECT 1 FROM avatars WHERE photo_id=? AND lower(username)=lower(?)", (photo_id, username)).fetchone():
                raise HTTPException(409, "这个 ID 已经贴过头像，请在列表中选中并调整")
            z_index = db.execute("SELECT COALESCE(MAX(z_index),-1)+1 FROM avatars WHERE photo_id=?",
                                 (photo_id,)).fetchone()[0]
            db.execute("INSERT INTO avatars (id,photo_id,username,x,y,size,created,z_index) VALUES (?,?,?,?,?,?,?,?)",
                       (avatar_id, photo_id, username, 0.5, 0.5, photo["default_size"], time.time(), z_index))
            db.execute("UPDATE photos SET version=version+1 WHERE id=?", (photo_id,))
            result = snapshot(db, photo_id)
    except Exception:
        (DATA / (avatar_id + ".png")).unlink(missing_ok=True)
        raise
    return {"photo": result, "selected": avatar_id}


@app.get("/api/avatars/{avatar_id}/image")
def avatar_image(avatar_id: str, role=Depends(identity)):
    with database() as db:
        if not db.execute("SELECT 1 FROM avatars WHERE id=?", (avatar_id,)).fetchone():
            raise HTTPException(404, "头像已被删除")
    return FileResponse(DATA / (avatar_id + ".png"), media_type="image/png")


class Transform(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    version: int = Field(ge=0)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    size: float = Field(ge=0.005, le=1)
    rotation: float = Field(ge=-360, le=360)


@app.patch("/api/photos/{photo_id}/avatars/{avatar_id}")
def update_avatar(photo_id: str, avatar_id: str, body: Transform, role=Depends(identity)):
    with database(write=True) as db:
        photo_row(db, photo_id)
        row = db.execute("SELECT * FROM avatars WHERE id=? AND photo_id=?", (avatar_id, photo_id)).fetchone()
        if not row:
            raise HTTPException(404, "头像已被删除，请刷新合照")
        if row["version"] != body.version:
            raise HTTPException(409, "这个头像刚被他人修改，请刷新合照后再调整")
        if body.size * photo_row(db, photo_id)["width"] > 4096:
            raise HTTPException(400, "单个头像的宽度最大为 4096 像素，请缩小后保存")
        db.execute("UPDATE avatars SET x=?,y=?,size=?,rotation=?,version=version+1 WHERE id=?",
                   (body.x, body.y, body.size, body.rotation, avatar_id))
        first = db.execute("SELECT id FROM avatars WHERE photo_id=? ORDER BY created,id LIMIT 1", (photo_id,)).fetchone()
        # The first avatar establishes the default for later additions.
        if first["id"] == avatar_id:
            db.execute("UPDATE photos SET default_size=? WHERE id=?", (body.size, photo_id))
        db.execute("UPDATE photos SET version=version+1 WHERE id=?", (photo_id,))
        return snapshot(db, photo_id)


@app.delete("/api/photos/{photo_id}/avatars/{avatar_id}")
def delete_avatar(photo_id: str, avatar_id: str, version: int, role=Depends(identity)):
    with database(write=True) as db:
        row = db.execute("SELECT * FROM avatars WHERE id=? AND photo_id=?", (avatar_id, photo_id)).fetchone()
        if not row:
            raise HTTPException(404, "头像已被删除")
        if row["version"] != version:
            raise HTTPException(409, "这个头像刚被他人修改，请刷新合照后再操作")
        db.execute("DELETE FROM avatars WHERE id=?", (avatar_id,))
        db.execute("UPDATE photos SET version=version+1 WHERE id=?", (photo_id,))
        result = snapshot(db, photo_id)
    (DATA / (avatar_id + ".png")).unlink(missing_ok=True)
    return result


class LayerOrder(BaseModel):
    version: int = Field(ge=0)
    avatar_ids: list[str] = Field(max_length=150)


@app.put("/api/photos/{photo_id}/layers")
def reorder_layers(photo_id: str, body: LayerOrder, role=Depends(identity)):
    with database(write=True) as db:
        current = snapshot(db, photo_id)
        if current["version"] != body.version:
            raise HTTPException(409, "合照有新的修改，请刷新合照后再调整图层顺序")
        existing = [avatar["id"] for avatar in current["avatars"]]
        if len(body.avatar_ids) != len(existing) or set(body.avatar_ids) != set(existing):
            raise HTTPException(400, "图层列表与合照不一致，请刷新后重试")
        if body.avatar_ids != existing:
            db.executemany("UPDATE avatars SET z_index=? WHERE id=? AND photo_id=?",
                           [(index, avatar_id, photo_id) for index, avatar_id in enumerate(body.avatar_ids)])
            # Transform versions stay independent: moving a head must not undo a reorder.
            db.execute("UPDATE photos SET version=version+1 WHERE id=?", (photo_id,))
        return snapshot(db, photo_id)


class Uniform(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    version: int = Field(ge=0)
    size: float = Field(ge=0.005, le=1)


@app.post("/api/photos/{photo_id}/uniform")
def uniform(photo_id: str, body: Uniform, role=Depends(identity)):
    with database(write=True) as db:
        if photo_row(db, photo_id)["version"] != body.version:
            raise HTTPException(409, "合照有新的修改，请刷新后再统一尺寸")
        if body.size * photo_row(db, photo_id)["width"] > 4096:
            raise HTTPException(400, "单个头像的宽度最大为 4096 像素")
        db.execute("UPDATE avatars SET size=?,version=version+1 WHERE photo_id=?", (body.size, photo_id))
        db.execute("UPDATE photos SET default_size=?,version=version+1 WHERE id=?", (body.size, photo_id))
        return snapshot(db, photo_id)


@app.get("/api/photos/{photo_id}/export")
def export(photo_id: str, role=Depends(identity)):
    with export_lock:
        return render_export(photo_id)


def render_export(photo_id):
    # Serialize with deletion until all source files have been read.
    with database(write=True) as db:
        photo = snapshot(db, photo_id)
        background = Image.open(DATA / (photo_id + ".jpg")).convert("RGBA")
        avatars = [(a, (DATA / (a["id"] + ".png")).read_bytes()) for a in photo["avatars"]]
    composite_avatars(background, avatars)
    output = io.BytesIO()
    background.convert("RGB").save(output, format="PNG")
    return Response(output.getvalue(), media_type="image/png",
                    headers={"Content-Disposition": 'attachment; filename="bcl-photo-' + photo_id[:8] + '.png"'})


def composite_avatars(background, avatars):
    for avatar, content in avatars:
        image = Image.open(io.BytesIO(content)).convert("RGBA")
        size = max(1, round(avatar["size"] * background.width))
        image = image.resize((size, size), Image.Resampling.NEAREST)
        image = image.rotate(-avatar["rotation"], resample=Image.Resampling.NEAREST, expand=True)
        left = round(avatar["x"] * background.width - image.width / 2)
        top = round(avatar["y"] * background.height - image.height / 2)
        background.alpha_composite(image, (left, top))


@app.get("/health")
def health():
    return {"status": "ok"}


STATIC = Path(os.environ.get("STATIC_DIR", "./frontend/dist"))
if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="frontend")
