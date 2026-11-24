"""Arayüze veri sağlayan yerel HTTP sunucusu (yalnızca 127.0.0.1)."""
import http.cookies
import http.server
import json
import mimetypes
import os
import secrets
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse

from . import archive as A
from . import importer as I
from . import paths

COOKIE = "wa_arsiv_k"

MIME_EXTRA = {
    ".opus": "audio/ogg", ".ogg": "audio/ogg", ".m4a": "audio/mp4", ".aac": "audio/aac",
    ".amr": "audio/amr", ".caf": "audio/x-caf", ".mp3": "audio/mpeg", ".wav": "audio/wav",
    ".mp4": "video/mp4", ".mov": "video/quicktime", ".m4v": "video/mp4", ".3gp": "video/3gpp",
    ".webp": "image/webp", ".thumb": "image/jpeg", ".heic": "image/heic", ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg", ".png": "image/png", ".gif": "image/gif",
    ".vcf": "text/vcard", ".pdf": "application/pdf",
    ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8",
    ".html": "text/html; charset=utf-8", ".svg": "image/svg+xml",
}


def guess_type(path):
    ext = os.path.splitext(path)[1].lower()
    return MIME_EXTRA.get(ext) or mimetypes.guess_type(path)[0] or "application/octet-stream"


class ApiError(Exception):
    def __init__(self, message, code=400):
        super().__init__(message)
        self.code = code


class AppState:
    def __init__(self, data_root):
        self.data_root = data_root
        self.token = secrets.token_urlsafe(24)
        self.lock = threading.Lock()
        self.archives = {}
        self.job = None
        self.job_reloaded = False
        self.last_seen = time.time()  # tarayıcı modunda sayfa kapandı mı diye izlenir
        self.seen_once = False
        self.reload()

    def reload(self):
        new = {}
        for folder in A.list_archives(self.data_root):
            aid = os.path.basename(folder)
            old = self.archives.get(aid)
            if old is not None:
                new[aid] = old
                continue
            try:
                new[aid] = A.Archive(folder)
            except Exception:
                traceback.print_exc()
        with self.lock:
            self.archives = new

    def get(self, aid):
        with self.lock:
            arc = self.archives.get(aid)
        if arc is None:
            raise ApiError("Arşiv bulunamadı.", 404)
        return arc

    def summaries(self):
        with self.lock:
            arcs = list(self.archives.values())
        out = [a.summary() for a in arcs]
        out.sort(key=lambda s: s.get("imported_at", 0), reverse=True)
        return out


def remove_tree(path):
    """Arşiv klasörünü siler. Bağlantılı (hard link) dosyalarda yalnızca arşivdeki ad silinir;
    iPhone yedeğindeki asıl dosyaya dokunulmaz. Salt okunur dosyalarda yazma iznini açıp tekrar dener."""
    def retry(func, p, _exc):
        try:
            os.chmod(p, stat.S_IWRITE)
            func(p)
        except OSError:
            pass
    try:
        shutil.rmtree(path, onexc=retry)  # Python 3.12+
    except TypeError:
        shutil.rmtree(path, onerror=retry)


def open_with_system(path):
    if os.name == "nt":
        os.startfile(path)  # noqa: (yalnızca Windows)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "WhatsAppArsivi/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # konsolsuz .exe'de stderr olmayabilir
        pass

    @property
    def app(self) -> AppState:
        return self.server.app

    # ---- yanıt yardımcıları
    def _send(self, code, body=b"", ctype="text/plain; charset=utf-8", headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self._send(code, body, "application/json; charset=utf-8")

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        if n > 1_000_000:
            raise ApiError("İstek çok büyük.")
        try:
            return json.loads(self.rfile.read(n).decode("utf-8"))
        except ValueError:
            raise ApiError("Geçersiz istek.")

    def _authorized(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return False
        try:
            c = http.cookies.SimpleCookie(raw)
        except http.cookies.CookieError:
            return False
        morsel = c.get(COOKIE)
        return bool(morsel) and secrets.compare_digest(morsel.value, self.app.token)

    # ---- yönlendirme
    def do_GET(self):
        self._dispatch("GET")

    def do_HEAD(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def _dispatch(self, method):
        try:
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            qs = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            if path == "/" and method == "GET":
                return self._index(qs)
            if not self._authorized():
                return self._send(403, "Bu sayfa yalnızca WhatsApp Arşivi programından açılabilir.".encode("utf-8"))
            self.app.last_seen = time.time()
            self.app.seen_once = True
            if path.startswith("/static/") and method == "GET":
                return self._static(urllib.parse.unquote(path[len("/static/"):]))
            if path.startswith("/m/") and method == "GET":
                return self._media(urllib.parse.unquote(path[len("/m/"):]))
            if path.startswith("/api/"):
                return self._json(self._api(method, path[len("/api/"):], qs))
            self._send(404, b"Bulunamadi")
        except ApiError as e:
            self._json({"error": str(e)}, e.code)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass
        except Exception as e:
            traceback.print_exc()
            try:
                self._json({"error": f"Beklenmeyen hata: {e}"}, 500)
            except Exception:
                pass

    def _index(self, qs):
        key = qs.get("k")
        if key and secrets.compare_digest(key, self.app.token):
            cookie = f"{COOKIE}={self.app.token}; Path=/; HttpOnly; SameSite=Strict"
            return self._send(302, b"", headers={"Location": "/", "Set-Cookie": cookie})
        if not self._authorized():
            return self._send(403, "Bu sayfa yalnızca WhatsApp Arşivi programından açılabilir. "
                                   "Programı yeniden başlatın.".encode("utf-8"))
        self._static("index.html")

    def _static(self, name):
        base = os.path.realpath(paths.web_dir())
        full = os.path.realpath(os.path.join(base, name))
        if os.path.commonpath([base, full]) != base or not os.path.isfile(full):
            return self._send(404, b"Bulunamadi")
        with open(full, "rb") as f:
            body = f.read()
        self._send(200, body, guess_type(full))

    def _media(self, rest):
        aid, _, rel = rest.partition("/")
        arc = self.app.get(aid)
        full = arc.resolve_file(rel)
        if not full:
            return self._send(404, b"Dosya yok")
        self._serve_file(full, guess_type(full))

    def _serve_file(self, full, ctype):
        full = paths.long_path(full)
        size = os.path.getsize(full)
        start, end, status = 0, size - 1, 200
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes=") and size > 0:
            first = rng[6:].split(",")[0].strip()
            s, _, e = first.partition("-")
            try:
                if s == "":
                    start = max(0, size - int(e))
                else:
                    start = int(s)
                    if e:
                        end = min(int(e), size - 1)
                if start > end or start >= size:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200
        length = max(0, end - start + 1)
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "private, max-age=86400")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        if self.command == "HEAD" or length == 0:
            return
        with open(full, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(256 * 1024, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

    # ---- API
    def _api(self, method, name, qs):
        app = self.app
        if name == "ping":
            return {"ok": True}
        if name == "state" and method == "GET":
            from . import __version__
            return {"archives": app.summaries(), "data_dir": app.data_root, "platform": sys.platform,
                    "version": __version__}

        if name == "backups" and method == "GET":
            return {"backups": I.find_backups(), "roots": I.backup_roots()}

        if name == "inspect-folder" and method == "POST":
            path = (self._body().get("path") or "").strip().strip('"')
            if not path or not os.path.isdir(path):
                raise ApiError("Klasör bulunamadı.")
            info = I.describe_backup(path)
            if info:
                return {"backups": [info]}
            found = []
            try:
                for entry in os.listdir(path):
                    sub = I.describe_backup(os.path.join(path, entry))
                    if sub:
                        found.append(sub)
            except OSError:
                pass
            if not found:
                raise ApiError("Bu klasörde iPhone yedeği bulunamadı. Yedek klasörü içinde "
                               "'Manifest.db' dosyası olmalıdır.")
            found.sort(key=lambda b: b["date"], reverse=True)
            return {"backups": found}

        if name == "import":
            if method == "POST":
                body = self._body()
                if app.job and app.job.snapshot()["state"] == "running":
                    raise ApiError("Zaten devam eden bir içe aktarma var.")
                path = (body.get("path") or "").strip()
                if not path:
                    raise ApiError("Yedek klasörü seçilmedi.")
                app.job = I.ImportJob(app.data_root, path, body.get("password") or None)
                app.job_reloaded = False
                app.job.start()
                return app.job.snapshot()
            if not app.job:
                return {"state": "idle"}
            snap = app.job.snapshot()
            if snap["state"] == "done" and not app.job_reloaded:
                app.reload()
                app.job_reloaded = True
            return snap

        if name == "import-cancel" and method == "POST":
            if app.job:
                app.job.cancel()
            return {"ok": True}

        if name == "chats" and method == "GET":
            arc = app.get(qs.get("a", ""))
            return {"archive": arc.summary(), "chats": arc.chats()}

        if name == "messages" and method == "GET":
            arc = app.get(qs.get("a", ""))
            chat = _int(qs.get("c"))
            kw = {}
            for key in ("before", "after"):
                if qs.get(key):
                    parts = qs[key].split(",")
                    if len(parts) != 2:
                        raise ApiError("Geçersiz imleç.")
                    kw[key] = (float(parts[0]), int(parts[1]))
            if qs.get("around"):
                kw["around"] = _int(qs["around"])
            if qs.get("date"):
                kw["date_ms"] = float(qs["date"])
            if qs.get("n"):
                kw["limit"] = max(1, min(2000, _int(qs["n"])))
            return arc.messages(chat, **kw)

        if name == "media" and method == "GET":
            arc = app.get(qs.get("a", ""))
            return arc.media_overview(_int(qs.get("c")))

        if name == "search" and method == "GET":
            arc = app.get(qs.get("a", ""))
            chat = _int(qs["c"]) if qs.get("c") else None
            return {"results": arc.search(qs.get("q", ""), chat)}

        if name == "open" and method == "POST":
            body = self._body()
            arc = app.get(body.get("a", ""))
            full = arc.resolve_file(body.get("path") or "")
            if not full:
                raise ApiError("Dosya bulunamadı.", 404)
            open_with_system(full)
            return {"ok": True}

        if name == "rename-archive" and method == "POST":
            body = self._body()
            arc = app.get(body.get("a", ""))
            new_name = (body.get("name") or "").strip()[:80]
            if not new_name:
                raise ApiError("İsim boş olamaz.")
            arc.meta["name"] = new_name
            with open(os.path.join(arc.folder, "archive.json"), "w", encoding="utf-8") as f:
                json.dump(arc.meta, f, ensure_ascii=False, indent=2)
            return {"ok": True}

        if name == "delete-archive" and method == "POST":
            body = self._body()
            arc = app.get(body.get("a", ""))
            with app.lock:
                app.archives.pop(arc.id, None)
            remove_tree(arc.folder)
            return {"ok": True}

        raise ApiError("Bilinmeyen istek.", 404)


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        raise ApiError("Geçersiz sayı.")


class Server(http.server.ThreadingHTTPServer):
    daemon_threads = True


def start(app, port=0):
    srv = Server(("127.0.0.1", port), Handler)
    srv.app = app
    threading.Thread(target=srv.serve_forever, name="http", daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/?k={app.token}"
    return srv, url
