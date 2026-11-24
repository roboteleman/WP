"""İçe aktarılmış bir WhatsApp (iPhone) arşivini okuma.

Arşiv klasörü yapısı:
    <arşiv>/archive.json          -> özet bilgi
    <arşiv>/files/ChatStorage.sqlite
    <arşiv>/files/ContactsV2.sqlite (varsa)
    <arşiv>/files/Message/Media/... -> mesaj medyaları
    <arşiv>/files/Media/Profile/... -> profil fotoğrafları
"""
import json
import os
import re
import sqlite3
import threading
import unicodedata
from pathlib import Path

from .paths import long_path

APPLE_EPOCH = 978307200  # 2001-01-01 UTC

HIDDEN_JID_SUFFIXES = ("status@broadcast", "@newsletter")

_URL_RE = re.compile(r"(?:https?://|www\.)[^\s<>\"'«»“”]+", re.IGNORECASE)

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".heic", ".webp", ".bmp"}
VIDEO_EXT = {".mp4", ".mov", ".m4v", ".3gp", ".avi", ".mkv", ".webm"}
AUDIO_EXT = {".opus", ".ogg", ".m4a", ".mp3", ".aac", ".amr", ".wav", ".caf"}

# Bilinen iPhone ZMESSAGETYPE değerleri
T_TEXT, T_IMAGE, T_VIDEO, T_AUDIO, T_CONTACT, T_LOCATION, T_SYSTEM, T_LINK, T_DOC = 0, 1, 2, 3, 4, 5, 6, 7, 8
T_GIF, T_DELETED, T_STICKER, T_POLL = 11, 14, 15, 46

# ---------------------------------------------------------------- metin normalizasyonu

_TR_MAP = str.maketrans({
    "İ": "i", "I": "i", "ı": "i", "Ş": "s", "ş": "s", "Ğ": "g", "ğ": "g",
    "Ü": "u", "ü": "u", "Ö": "o", "ö": "o", "Ç": "c", "ç": "c",
    "Â": "a", "â": "a", "Î": "i", "î": "i", "Û": "u", "û": "u",
})


def norm(text):
    """Aramada Türkçe karakter/büyük-küçük harf farkını yok sayar."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", str(text)).translate(_TR_MAP).lower()


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------- içe aktarma sonrası hazırlık

def _columns(con, table):
    try:
        return {r[1] for r in con.execute(f"PRAGMA table_info({table})")}
    except sqlite3.DatabaseError:
        return set()


def prepare_database(db_path, cancel_check=None):
    """Arşivin kendi kopyasına dizinler ve arama tablosu ekler. Özet istatistik döndürür."""
    con = sqlite3.connect(db_path)
    try:
        con.execute("PRAGMA journal_mode=DELETE")  # WAL varsa birleştir, salt okunur açılabilsin
        msg_cols = _columns(con, "ZWAMESSAGE")
        if not msg_cols:
            raise RuntimeError("ChatStorage.sqlite içinde ZWAMESSAGE tablosu yok")
        has_media = bool(_columns(con, "ZWAMEDIAITEM"))

        con.execute("CREATE INDEX IF NOT EXISTS ARSIV_MSG_CHAT_DATE ON ZWAMESSAGE(ZCHATSESSION, ZMESSAGEDATE, Z_PK)")
        if "ZSTANZAID" in msg_cols:
            con.execute("CREATE INDEX IF NOT EXISTS ARSIV_MSG_STANZA ON ZWAMESSAGE(ZSTANZAID)")
        if has_media:
            con.execute("CREATE INDEX IF NOT EXISTS ARSIV_MEDIA_MSG ON ZWAMEDIAITEM(ZMESSAGE)")

        con.execute("DROP TABLE IF EXISTS ARSIV_SEARCH")
        con.execute("CREATE TABLE ARSIV_SEARCH (id INTEGER PRIMARY KEY, chat INTEGER, d REAL, t TEXT)")
        title = "mi.ZTITLE" if has_media and "ZTITLE" in _columns(con, "ZWAMEDIAITEM") else "NULL"
        join = "LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK" if has_media else ""
        cur = con.execute(f"""
            SELECT m.Z_PK, m.ZCHATSESSION, m.ZMESSAGEDATE, m.ZTEXT, {title}
            FROM ZWAMESSAGE m {join}
            WHERE (m.ZTEXT IS NOT NULL OR {title} IS NOT NULL)
              AND COALESCE(m.ZMESSAGETYPE, 0) NOT IN ({T_SYSTEM}, {T_DELETED})
        """)
        insert = con.cursor()
        while True:
            if cancel_check:
                cancel_check()
            rows = cur.fetchmany(5000)
            if not rows:
                break
            insert.executemany(
                "INSERT OR IGNORE INTO ARSIV_SEARCH (id, chat, d, t) VALUES (?, ?, ?, ?)",
                [(pk, chat, d, norm(" ".join(x for x in (text, ttl) if x)))
                 for pk, chat, d, text, ttl in rows])
        con.execute("CREATE INDEX ARSIV_SEARCH_CHAT ON ARSIV_SEARCH(chat, d)")
        con.commit()

        stats = con.execute("SELECT COUNT(*), MIN(ZMESSAGEDATE), MAX(ZMESSAGEDATE) FROM ZWAMESSAGE").fetchone()
        try:
            chats = con.execute("""
                SELECT COUNT(DISTINCT m.ZCHATSESSION) FROM ZWAMESSAGE m
                JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION
                WHERE s.ZCONTACTJID IS NOT NULL AND s.ZCONTACTJID <> 'status@broadcast'
                  AND s.ZCONTACTJID NOT LIKE '%@newsletter'
            """).fetchone()[0]
        except sqlite3.DatabaseError:
            chats = con.execute("SELECT COUNT(DISTINCT ZCHATSESSION) FROM ZWAMESSAGE").fetchone()[0]
        return {
            "message_count": stats[0] or 0,
            "chat_count": chats or 0,
            "first_date": int((stats[1] + APPLE_EPOCH) * 1000) if stats[1] else None,
            "last_date": int((stats[2] + APPLE_EPOCH) * 1000) if stats[2] else None,
        }
    finally:
        con.close()


# ---------------------------------------------------------------- yardımcılar

def _ts(apple_seconds):
    if apple_seconds is None:
        return 0
    return int((float(apple_seconds) + APPLE_EPOCH) * 1000)


def _jid_user(jid):
    return (jid or "").split("@")[0]


def _looks_like_phone(name):
    s = (name or "").replace("+", "").replace(" ", "").replace("-", "").replace("(", "").replace(")", "")
    return s.isdigit()


def _format_phone(jid):
    user = _jid_user(jid)
    if jid and jid.endswith("@s.whatsapp.net") and user.isdigit():
        return "+" + user
    return None


def _quoted_stanza_id(blob):
    """ZMETADATA içindeki (protobuf) alıntılanan mesaj kimliğini (alan 5) bulur."""
    if not blob or not isinstance(blob, (bytes, bytearray)):
        return None
    data = bytes(blob)
    i, n = 0, len(data)
    try:
        while i < n:
            key, i = _varint(data, i)
            field, wire = key >> 3, key & 7
            if wire == 0:
                _, i = _varint(data, i)
            elif wire == 1:
                i += 8
            elif wire == 5:
                i += 4
            elif wire == 2:
                length, i = _varint(data, i)
                chunk = data[i:i + length]
                i += length
                if field == 5:
                    s = chunk.decode("utf-8", "ignore").strip()
                    return s or None
            else:
                return None
    except (IndexError, ValueError):
        return None
    return None


def _varint(data, i):
    shift = result = 0
    while True:
        b = data[i]
        i += 1
        result |= (b & 0x7F) << shift
        if not b & 0x80:
            return result, i
        shift += 7
        if shift > 63:
            raise ValueError("varint")


def _ext(path):
    return os.path.splitext(path or "")[1].lower()


def _media_kind(mtype, path):
    if mtype == T_IMAGE:
        return "image"
    if mtype == T_VIDEO:
        return "video"
    if mtype == T_AUDIO:
        return "audio"
    if mtype == T_GIF:
        return "gif"
    if mtype == T_STICKER:
        return "sticker"
    if mtype == T_DOC:
        return "document"
    ext = _ext(path)
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    if ext in AUDIO_EXT:
        return "audio"
    return "document"


KIND_LABELS = {
    "image": "📷 Fotoğraf",
    "video": "🎥 Video",
    "gif": "GIF",
    "audio": "🎵 Ses",
    "voice": "🎤 Sesli mesaj",
    "sticker": "Çıkartma",
    "document": "📄 Belge",
    "location": "📍 Konum",
    "contact": "👤 Kişi",
    "deleted": "🚫 Bu mesaj silindi",
    "poll": "📊 Anket",
}


def label_for(msg):
    """Sohbet listesi / alıntı için kısa açıklama."""
    kind = msg["kind"]
    if kind == "text":
        return msg.get("text") or ""
    if kind == "system":
        return msg.get("text") or ""
    base = KIND_LABELS.get(kind, "Mesaj")
    if kind == "document" and msg.get("file_name"):
        base = "📄 " + msg["file_name"]
    if kind == "contact" and msg.get("contact_name"):
        base = "👤 " + msg["contact_name"]
    if kind == "poll" and msg.get("text"):
        base = "📊 " + msg["text"]
    extra = msg.get("caption") or (msg.get("text") if kind not in ("poll",) else None)
    return f"{base} {extra}".strip() if extra and kind != "document" else base


# ---------------------------------------------------------------- arşiv

class Archive:
    def __init__(self, folder):
        self.folder = folder
        self.id = os.path.basename(folder.rstrip("\\/"))
        self.files = os.path.join(folder, "files")
        self.db_path = os.path.join(self.files, "ChatStorage.sqlite")
        with open(os.path.join(folder, "archive.json"), encoding="utf-8") as f:
            self.meta = json.load(f)
        self._lock = threading.Lock()
        self._names = None
        self._avatars = None
        self._cols = None
        self._chat_cache = None
        self._ensure_search_table()

    # ---- bağlantı
    def connect(self):
        uri = Path(self.db_path).resolve().as_uri() + "?mode=ro"
        con = sqlite3.connect(uri, uri=True, check_same_thread=False)
        con.row_factory = sqlite3.Row
        return con

    def _ensure_search_table(self):
        con = sqlite3.connect(self.db_path)
        try:
            exists = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ARSIV_SEARCH'").fetchone()
        finally:
            con.close()
        if not exists:
            prepare_database(self.db_path)

    def cols(self, table):
        with self._lock:
            if self._cols is None:
                self._cols = {}
            if table not in self._cols:
                con = self.connect()
                try:
                    self._cols[table] = _columns(con, table)
                finally:
                    con.close()
            return self._cols[table]

    def _c(self, alias, table, col, as_name=None):
        """Sütun yoksa NULL döndüren güvenli SELECT ifadesi."""
        as_name = as_name or col
        if col in self.cols(table):
            return f"{alias}.{col} AS {as_name}"
        return f"NULL AS {as_name}"

    # ---- isimler
    def names(self):
        with self._lock:
            if self._names is not None:
                return self._names
        names = {}
        push = {}
        con = self.connect()
        try:
            # 1) WhatsApp'ın rehber kopyası (ContactsV2.sqlite)
            contacts_db = os.path.join(self.files, "ContactsV2.sqlite")
            if os.path.isfile(contacts_db):
                try:
                    c2 = sqlite3.connect(Path(contacts_db).resolve().as_uri() + "?mode=ro", uri=True)
                    cols = _columns(c2, "ZWAADDRESSBOOKCONTACT")
                    if "ZWHATSAPPID" in cols and ("ZFULLNAME" in cols or "ZGIVENNAME" in cols):
                        full = "ZFULLNAME" if "ZFULLNAME" in cols else "NULL"
                        given = "ZGIVENNAME" if "ZGIVENNAME" in cols else "NULL"
                        lid = "ZLID" if "ZLID" in cols else "NULL"
                        for wid, fn, gn, lidv in c2.execute(
                                f"SELECT ZWHATSAPPID, {full}, {given}, {lid} FROM ZWAADDRESSBOOKCONTACT"):
                            name = (fn or gn or "").strip()
                            if not wid or not name:
                                continue
                            jid = wid if "@" in wid else wid + "@s.whatsapp.net"
                            names.setdefault(jid, name)
                            if lidv:
                                ljid = lidv if "@" in str(lidv) else f"{lidv}@lid"
                                names.setdefault(ljid, name)
                    c2.close()
                except sqlite3.DatabaseError:
                    pass
            # 2) Bire bir sohbetlerdeki kayıtlı isim
            s_cols = self.cols("ZWACHATSESSION")
            if "ZCONTACTJID" in s_cols and "ZPARTNERNAME" in s_cols:
                for r in con.execute("SELECT ZCONTACTJID, ZPARTNERNAME FROM ZWACHATSESSION"):
                    jid, pn = r[0], (r[1] or "").strip()
                    if jid and pn and not jid.endswith("@g.us") and not _looks_like_phone(pn):
                        names.setdefault(jid, pn)
            # 3) Grup üyelerinin rehber adı
            g_cols = self.cols("ZWAGROUPMEMBER")
            if "ZMEMBERJID" in g_cols:
                cn = "ZCONTACTNAME" if "ZCONTACTNAME" in g_cols else "NULL"
                fn = "ZFIRSTNAME" if "ZFIRSTNAME" in g_cols else "NULL"
                for jid, cname, fname in con.execute(f"SELECT ZMEMBERJID, {cn}, {fn} FROM ZWAGROUPMEMBER"):
                    name = (cname or fname or "").strip()
                    if jid and name and not _looks_like_phone(name):
                        names.setdefault(jid, name)
            # 4) WhatsApp profil adları (~ ile gösterilir)
            if {"ZJID", "ZPUSHNAME"} <= self.cols("ZWAPROFILEPUSHNAME"):
                for jid, pn in con.execute("SELECT ZJID, ZPUSHNAME FROM ZWAPROFILEPUSHNAME"):
                    if jid and pn and pn.strip():
                        push[jid] = pn.strip()
        finally:
            con.close()
        for jid, pn in push.items():
            if jid not in names:
                phone = _format_phone(jid)
                names[jid] = f"~{pn}" + (f" ({phone})" if phone else "")
        with self._lock:
            self._names = names
        return names

    def name_for(self, jid):
        if not jid:
            return "Bilinmeyen"
        n = self.names().get(jid)
        if n:
            return n
        return _format_phone(jid) or _jid_user(jid) or "Bilinmeyen"

    # ---- profil fotoğrafları
    def avatar_for(self, jid):
        with self._lock:
            if self._avatars is None:
                prof = os.path.join(self.files, "Media", "Profile")
                try:
                    self._avatars = sorted(os.listdir(prof))
                except OSError:
                    self._avatars = []
            files = self._avatars
        user = _jid_user(jid)
        if not user:
            return None
        # Dosya adı "<kullanıcı>-<zaman>.jpg" biçimindedir. Eski grup kimlikleri de
        # "<numara>-<zaman>" olduğundan, kişiye grup fotoğrafı eşleşmesin diye tam eşleşme aranır.
        pat = re.compile(re.escape(user) + r"(-\d+)?\.(jpg|jpeg|png|thumb)", re.IGNORECASE)
        cands = [f for f in files if pat.fullmatch(f)]
        if not cands:
            return None
        jpgs = [f for f in cands if f.lower().endswith(".jpg")]
        pick = (jpgs or cands)[-1]
        return "Media/Profile/" + pick

    # ---- sohbet listesi
    def chats(self):
        with self._lock:
            if self._chat_cache is not None:
                return self._chat_cache
        s_cols = self.cols("ZWACHATSESSION")
        gi = "s.ZGROUPINFO" if "ZGROUPINFO" in s_cols else "NULL"
        con = self.connect()
        try:
            rows = con.execute(f"""
                SELECT s.Z_PK AS id, s.ZCONTACTJID AS jid, {self._c('s', 'ZWACHATSESSION', 'ZPARTNERNAME', 'pname')},
                       {gi} AS gi,
                       (SELECT COUNT(*) FROM ZWAMESSAGE m WHERE m.ZCHATSESSION = s.Z_PK) AS cnt,
                       (SELECT m.Z_PK FROM ZWAMESSAGE m WHERE m.ZCHATSESSION = s.Z_PK
                            AND COALESCE(m.ZMESSAGETYPE, 0) <> {T_SYSTEM}
                            ORDER BY m.ZMESSAGEDATE DESC, m.Z_PK DESC LIMIT 1) AS last_pk,
                       (SELECT MAX(m.ZMESSAGEDATE) FROM ZWAMESSAGE m WHERE m.ZCHATSESSION = s.Z_PK) AS last_d
                FROM ZWACHATSESSION s
            """).fetchall()
            rows = [r for r in rows if r["cnt"] and r["jid"]
                    and not any(r["jid"].endswith(sfx) for sfx in HIDDEN_JID_SUFFIXES)]
            last_pks = [r["last_pk"] for r in rows if r["last_pk"]]
            last_msgs = {m["id"]: m for m in self._fetch_by_ids(con, last_pks)}
        finally:
            con.close()

        out = []
        for r in rows:
            jid = r["jid"]
            is_group = bool(r["gi"]) or jid.endswith("@g.us")
            if is_group:
                name = (r["pname"] or "").strip() or "Grup"
            else:
                name = self.names().get(jid) or (r["pname"] or "").strip() or self.name_for(jid)
            last = last_msgs.get(r["last_pk"])
            preview = ""
            if last:
                preview = label_for(last)
                if last["me"]:
                    preview = "Sen: " + preview
                elif is_group and last.get("sender"):
                    preview = f"{last['sender']}: {preview}"
            out.append({
                "id": r["id"],
                "jid": jid,
                "name": name,
                "group": is_group,
                "count": r["cnt"],
                "last": _ts(r["last_d"]),
                "preview": preview[:140],
                "avatar": self.avatar_for(jid),
                "phone": None if is_group else _format_phone(jid),
            })
        out.sort(key=lambda c: c["last"], reverse=True)
        with self._lock:
            self._chat_cache = out
        return out

    def chat_by_id(self, chat_id):
        for c in self.chats():
            if c["id"] == chat_id:
                return c
        return None

    # ---- mesajlar
    def _select_sql(self):
        has_media = bool(self.cols("ZWAMEDIAITEM"))
        gm_ok = bool(self.cols("ZWAGROUPMEMBER")) and "ZGROUPMEMBER" in self.cols("ZWAMESSAGE")
        cols = [
            "m.Z_PK AS id", "m.ZCHATSESSION AS chat", "m.ZISFROMME AS me", "m.ZMESSAGEDATE AS d",
            "m.ZTEXT AS text", "m.ZMESSAGETYPE AS t",
            self._c("m", "ZWAMESSAGE", "ZSTANZAID", "sid"),
            self._c("m", "ZWAMESSAGE", "ZMETADATA", "meta"),
            self._c("m", "ZWAMESSAGE", "ZFROMJID", "fjid"),
            self._c("s", "ZWACHATSESSION", "ZCONTACTJID", "cjid"),
        ]
        cols.append(self._c("gm", "ZWAGROUPMEMBER", "ZMEMBERJID", "mjid") if gm_ok else "NULL AS mjid")
        for col, alias in (("ZMEDIALOCALPATH", "path"), ("ZTITLE", "title"), ("ZVCARDNAME", "vname"),
                           ("ZLATITUDE", "lat"), ("ZLONGITUDE", "lon"), ("ZFILESIZE", "size"),
                           ("ZMOVIEDURATION", "dur")):
            cols.append(self._c("mi", "ZWAMEDIAITEM", col, alias) if has_media else f"NULL AS {alias}")
        joins = "LEFT JOIN ZWACHATSESSION s ON s.Z_PK = m.ZCHATSESSION"
        if gm_ok:
            joins += " LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER"
        if has_media:
            joins += " LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK"
        return f"SELECT {', '.join(cols)} FROM ZWAMESSAGE m {joins}"

    def _fetch_by_ids(self, con, ids):
        if not ids:
            return []
        out = []
        sql = self._select_sql()
        for i in range(0, len(ids), 500):
            chunk = ids[i:i + 500]
            q = f"{sql} WHERE m.Z_PK IN ({','.join('?' * len(chunk))})"
            for r in con.execute(q, chunk):
                m = self._convert(r)
                if m:
                    out.append(m)
        return out

    def _convert(self, r):
        mtype = r["t"] if r["t"] is not None else 0
        me = bool(r["me"])
        cjid = r["cjid"] or ""
        is_group = cjid.endswith("@g.us")
        sender_jid = None
        if is_group and not me:
            sender_jid = r["mjid"] or (r["fjid"] if r["fjid"] and r["fjid"] != cjid else None)
        msg = {
            "id": r["id"],
            "chat": r["chat"],
            "ts": _ts(r["d"]),
            "cur": [r["d"] or 0, r["id"]],
            "me": me,
            "sender": self.name_for(sender_jid) if sender_jid else None,
            "sender_jid": sender_jid,
            "kind": "text",
            "text": r["text"],
        }
        text = r["text"]
        path = r["path"]

        if mtype == T_DELETED:
            msg["kind"] = "deleted"
            msg["text"] = None
            return msg
        if mtype == T_SYSTEM:
            if is_group and text and not text.strip().isdigit():
                who = "Sen" if me else (msg["sender"] or "Biri")
                verb = "grup adını değiştirdin" if me else "grup adını değiştirdi"
                msg["kind"] = "system"
                msg["text"] = f"{who} {verb}: “{text}”"
                return msg
            return None
        if mtype == T_LOCATION:
            msg["kind"] = "location"
            msg["lat"], msg["lon"] = r["lat"], r["lon"]
            msg["text"] = text or r["title"]
            return msg
        if mtype == T_CONTACT:
            msg["kind"] = "contact"
            msg["contact_name"] = (r["vname"] or "").split("_$!<Name-Separator>!$_")[0] or text
            msg["text"] = None
            return msg
        if mtype == T_POLL:
            msg["kind"] = "poll"
            return msg

        media_types = (T_IMAGE, T_VIDEO, T_AUDIO, T_GIF, T_STICKER, T_DOC)
        if mtype in media_types or (path and mtype not in (T_TEXT, T_LINK)):
            kind = _media_kind(mtype, path)
            rel = ("Message/" + path.lstrip("/")) if path else None
            exists = bool(rel) and os.path.isfile(long_path(os.path.join(self.files, *rel.split("/"))))
            if kind == "audio" and _ext(path) in (".opus", ".ogg"):
                kind = "voice"
            msg["kind"] = kind
            msg["media"] = rel if exists else None
            msg["missing"] = not exists
            msg["size"] = r["size"]
            msg["duration"] = r["dur"]
            title = r["title"]
            if kind == "document":
                msg["file_name"] = title or (os.path.basename(path) if path else None)
                msg["caption"] = None
            else:
                msg["caption"] = title
            if kind in ("image", "video", "gif") and text and not msg.get("caption"):
                msg["caption"] = text
            msg["text"] = None if kind != "document" else text
            return msg

        if mtype == T_LINK or text:
            msg["kind"] = "text"
            return msg
        if r["title"]:
            msg["text"] = r["title"]
            return msg
        return None  # gösterilecek bir şey yok (çağrı kaydı, şifreleme bildirimi vb.)

    def _attach_replies(self, con, msgs):
        if "ZMETADATA" not in self.cols("ZWAMESSAGE") or "ZSTANZAID" not in self.cols("ZWAMESSAGE"):
            return
        rows = {}
        ids = [m["id"] for m in msgs]
        if not ids:
            return
        q = f"SELECT Z_PK, ZMETADATA FROM ZWAMESSAGE WHERE Z_PK IN ({','.join('?' * len(ids))}) AND ZMETADATA IS NOT NULL"
        for pk, blob in con.execute(q, ids):
            qid = _quoted_stanza_id(blob)
            if qid:
                rows[pk] = qid
        if not rows:
            return
        found = {}
        for qid in set(rows.values()):
            r = con.execute("SELECT Z_PK FROM ZWAMESSAGE WHERE ZSTANZAID = ? LIMIT 1", (qid,)).fetchone()
            if not r and len(qid) >= 17:
                prefix = qid[:17]
                r = con.execute("SELECT Z_PK FROM ZWAMESSAGE WHERE ZSTANZAID >= ? AND ZSTANZAID < ? LIMIT 1",
                                (prefix, prefix + "￿")).fetchone()
            if r:
                found[qid] = r[0]
        if not found:
            return
        quoted = {m["id"]: m for m in self._fetch_by_ids(con, list(set(found.values())))}
        for m in msgs:
            qid = rows.get(m["id"])
            if not qid or qid not in found:
                continue
            q = quoted.get(found[qid])
            if not q:
                continue
            if q["me"]:
                who = "Sen"
            elif q.get("sender"):
                who = q["sender"]
            else:
                chat = self.chat_by_id(q["chat"])
                who = chat["name"] if chat else ""
            m["reply"] = {"id": q["id"], "sender": who, "sender_jid": q.get("sender_jid"),
                          "me": q["me"], "text": label_for(q)[:200],
                          "thumb": q.get("media") if q["kind"] in ("image", "sticker") else None}

    def messages(self, chat_id, before=None, after=None, around=None, date_ms=None, limit=80):
        """Bir sohbetin mesajlarını sayfa sayfa döndürür (eski → yeni sırada)."""
        sql = self._select_sql()
        con = self.connect()
        try:
            def page(cond, params, order, n):
                q = f"{sql} WHERE m.ZCHATSESSION = ? {cond} ORDER BY m.ZMESSAGEDATE {order}, m.Z_PK {order} LIMIT ?"
                return con.execute(q, [chat_id, *params, n + 1]).fetchall()

            def finish(raw_rows):
                msgs = [m for m in (self._convert(r) for r in raw_rows) if m]
                self._attach_replies(con, msgs)
                return msgs

            target = None
            if date_ms is not None:
                apple = date_ms / 1000.0 - APPLE_EPOCH
                r = con.execute("SELECT Z_PK FROM ZWAMESSAGE WHERE ZCHATSESSION = ? AND ZMESSAGEDATE >= ? "
                                "ORDER BY ZMESSAGEDATE, Z_PK LIMIT 1", (chat_id, apple)).fetchone()
                if r:
                    around = r[0]
                else:
                    before = after = around = None
            if around is not None:
                r = con.execute("SELECT ZMESSAGEDATE, Z_PK FROM ZWAMESSAGE WHERE Z_PK = ? AND ZCHATSESSION = ?",
                                (around, chat_id)).fetchone()
                if r:
                    d, pk = r[0] or 0, r[1]
                    half = limit // 2
                    older = page("AND (COALESCE(m.ZMESSAGEDATE,0), m.Z_PK) < (?, ?)", [d, pk], "DESC", half)
                    newer = page("AND (COALESCE(m.ZMESSAGEDATE,0), m.Z_PK) >= (?, ?)", [d, pk], "ASC", half)
                    more_before = len(older) > half
                    more_after = len(newer) > half
                    rows = list(reversed(older[:half])) + newer[:half]
                    return {"messages": finish(rows), "more_before": more_before,
                            "more_after": more_after, "target": pk}
            if before is not None:
                rows = page("AND (COALESCE(m.ZMESSAGEDATE,0), m.Z_PK) < (?, ?)", list(before), "DESC", limit)
                more = len(rows) > limit
                return {"messages": finish(list(reversed(rows[:limit]))), "more_before": more, "more_after": None}
            if after is not None:
                rows = page("AND (COALESCE(m.ZMESSAGEDATE,0), m.Z_PK) > (?, ?)", list(after), "ASC", limit)
                more = len(rows) > limit
                return {"messages": finish(rows[:limit]), "more_before": None, "more_after": more}
            rows = page("", [], "DESC", limit)
            more = len(rows) > limit
            return {"messages": finish(list(reversed(rows[:limit]))), "more_before": more,
                    "more_after": False, "target": target}
        finally:
            con.close()

    # ---- arama
    def search(self, query, chat_id=None, limit=150):
        q = norm(query).strip()
        if len(q) < 2:
            return []
        pattern = f"%{like_escape(q)}%"
        con = self.connect()
        try:
            if chat_id is not None:
                ids = [r[0] for r in con.execute(
                    "SELECT id FROM ARSIV_SEARCH WHERE chat = ? AND t LIKE ? ESCAPE '\\' ORDER BY d DESC LIMIT ?",
                    (chat_id, pattern, limit))]
            else:
                ids = [r[0] for r in con.execute(
                    "SELECT id FROM ARSIV_SEARCH WHERE t LIKE ? ESCAPE '\\' ORDER BY d DESC LIMIT ?",
                    (pattern, limit))]
            msgs = {m["id"]: m for m in self._fetch_by_ids(con, ids)}
        finally:
            con.close()
        out = []
        for pk in ids:
            m = msgs.get(pk)
            if not m:
                continue
            chat = self.chat_by_id(m["chat"])
            if not chat:
                continue
            out.append({
                "id": m["id"], "chat": m["chat"], "chat_name": chat["name"], "ts": m["ts"],
                "me": m["me"], "sender": m.get("sender"), "text": label_for(m)[:300],
                "avatar": chat["avatar"], "group": chat["group"],
            })
        return out

    # ---- medya, dosya ve bağlantı listesi (sohbet bilgisi paneli)
    def media_overview(self, chat_id):
        """Bir sohbetteki fotoğraf, video, dosya ve bağlantıları eskiden yeniye döndürür."""
        sql = self._select_sql()
        has_media = bool(self.cols("ZWAMEDIAITEM"))
        media_types = f"{T_IMAGE},{T_VIDEO},{T_AUDIO},{T_DOC},{T_GIF}"
        media_cond = f"(mi.ZMEDIALOCALPATH IS NOT NULL OR m.ZMESSAGETYPE IN ({media_types}))" if has_media \
            else f"m.ZMESSAGETYPE IN ({media_types})"
        out = {"photos": [], "videos": [], "docs": [], "links": []}
        con = self.connect()
        try:
            for r in con.execute(f"{sql} WHERE m.ZCHATSESSION = ? AND {media_cond} "
                                 "ORDER BY m.ZMESSAGEDATE, m.Z_PK", (chat_id,)):
                m = self._convert(r)
                if not m:
                    continue
                item = {k: m.get(k) for k in ("id", "ts", "kind", "media", "caption", "sender", "sender_jid",
                                              "me", "file_name", "size", "duration", "missing")}
                if m["kind"] == "image" and m.get("media"):
                    out["photos"].append(item)
                elif m["kind"] in ("video", "gif") and m.get("media"):
                    out["videos"].append(item)
                elif m["kind"] in ("document", "audio"):
                    if not item.get("file_name"):
                        item["file_name"] = (m.get("media") or "").split("/")[-1] or "Dosya"
                    out["docs"].append(item)
            # bağlantılar: metinlerdeki ve açıklamalardaki adresler
            title = "mi.ZTITLE" if has_media and "ZTITLE" in self.cols("ZWAMEDIAITEM") else "NULL"
            join = "LEFT JOIN ZWAMEDIAITEM mi ON mi.ZMESSAGE = m.Z_PK" if has_media else ""
            gm_ok = bool(self.cols("ZWAGROUPMEMBER")) and "ZGROUPMEMBER" in self.cols("ZWAMESSAGE")
            gm_col = "gm.ZMEMBERJID" if gm_ok else "NULL"
            gm_join = "LEFT JOIN ZWAGROUPMEMBER gm ON gm.Z_PK = m.ZGROUPMEMBER" if gm_ok else ""
            for pk, d, me, text, ttl, mjid in con.execute(f"""
                    SELECT m.Z_PK, m.ZMESSAGEDATE, m.ZISFROMME, m.ZTEXT, {title}, {gm_col}
                    FROM ZWAMESSAGE m {join} {gm_join}
                    WHERE m.ZCHATSESSION = ? AND COALESCE(m.ZMESSAGETYPE, 0) NOT IN ({T_SYSTEM}, {T_DELETED})
                      AND (m.ZTEXT LIKE '%http%' OR m.ZTEXT LIKE '%www.%'
                           OR {title} LIKE '%http%' OR {title} LIKE '%www.%')
                    ORDER BY m.ZMESSAGEDATE, m.Z_PK""", (chat_id,)):
                body = " ".join(x for x in (text, ttl) if x)
                seen = set()
                for url in _URL_RE.findall(body):
                    url = url.rstrip(".,;:!?)]}'\"”’")
                    if len(url) < 8 or url in seen:
                        continue
                    seen.add(url)
                    href = url if url.lower().startswith("http") else "https://" + url
                    domain = re.sub(r"^https?://", "", href, flags=re.I).split("/")[0].split("?")[0]
                    out["links"].append({
                        "id": pk, "ts": _ts(d), "me": bool(me), "url": href, "display": url,
                        "domain": domain.lower().removeprefix("www."),
                        "text": body[:240],
                        "sender": None if me or not mjid else self.name_for(mjid),
                    })
        finally:
            con.close()
        return out

    def summary(self):
        meta = dict(self.meta)
        meta["id"] = self.id
        return meta

    def resolve_file(self, rel):
        """Arşiv içindeki göreli bir dosya yolunu güvenli şekilde tam yola çevirir."""
        parts = [p for p in rel.replace("\\", "/").split("/") if p not in ("", ".")]
        if not parts or any(p == ".." for p in parts):
            return None
        full = os.path.abspath(os.path.join(self.files, *parts))
        base = os.path.abspath(self.files)
        if os.path.commonpath([base, full]) != base or not os.path.isfile(long_path(full)):
            return None
        return full


def list_archives(data_root):
    out = []
    try:
        entries = sorted(os.listdir(data_root))
    except OSError:
        return out
    for entry in entries:
        folder = os.path.join(data_root, entry)
        if entry.endswith(".partial") or not os.path.isfile(os.path.join(folder, "archive.json")):
            continue
        out.append(folder)
    return out
