"""TEST AMAÇLI: Sahte bir iPhone (iTunes) yedeği üretir. Gerçek veri içermez.

Kullanım:  python dev/make_fake_backup.py <çıktı_kökü>
Gerekenler: Pillow, imageio-ffmpeg (yalnızca bu test betiği için)
"""
import datetime as dt
import hashlib
import os
import plistlib
import random
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import uuid

from PIL import Image, ImageDraw

DOMAIN = "AppDomainGroup-group.net.whatsapp.WhatsApp.shared"
APPLE_EPOCH = 978307200

TEXTS = [
    "Merhaba, nasılsın?", "İyiyim sen nasılsın 😊", "Akşam yemeğe geliyor musunuz?", "Tamam, haberleşiriz.",
    "Şu an yoldayım, 10 dakikaya oradayım", "Çok güzel olmuş 👍", "Yarın İstanbul'a gidiyorum",
    "Ödemeyi yaptım, dekontu atıyorum", "Bugün hava çok soğuk ❄️", "Doğum günün kutlu olsun! 🎂🎉",
    "Görüşürüz", "Kargo geldi mi?", "Ararım birazdan", "Evet aynen öyle", "Hahaha 😂😂",
    "Toplantı saat 15:00'te", "Ilık bir çay içelim mi?", "Şükrü amcaya selam söyle",
    "Çiçekleri suladın mı?", "Ağabeyin aradı, seni sordu", "Iğdır'dan dönünce görüşelim",
    "Şu videoya bak 😂 https://www.youtube.com/watch?v=abc123XYZ",
    "Tarif burada: https://tr.wikipedia.org/wiki/Mercimek_çorbası",
    "Kampanya linki www.example.com/kampanya?kod=KIS2025 son gün yarın",
]
LONG = ("Bugün pazara gittim, domates çok pahalıydı. *Önemli:* yarın sabah erken kalkmamız lazım, "
        "_unutma_ lütfen. Adres: https://www.google.com/maps ~eski adres~ değişti.\nİkinci satır burada.\n"
        "```kod örneği```")


def apple(ts):
    return ts - APPLE_EPOCH


def stanza():
    return "3EB0" + uuid.uuid4().hex[:16].upper()


def pb_field(num, data):
    return bytes([(num << 3) | 2, len(data)]) + data


class Builder:
    def __init__(self, workdir):
        self.work = workdir
        self.files = {}  # relativePath -> local file
        self.db = sqlite3.connect(os.path.join(workdir, "ChatStorage.sqlite"))
        self.contacts = sqlite3.connect(os.path.join(workdir, "ContactsV2.sqlite"))
        self.files["ChatStorage.sqlite"] = os.path.join(workdir, "ChatStorage.sqlite")
        self.files["ContactsV2.sqlite"] = os.path.join(workdir, "ContactsV2.sqlite")
        self.ffmpeg = None
        try:
            import imageio_ffmpeg
            self.ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            pass
        self._schema()
        self.msg_pk = 0
        self.media_pk = 0
        self.member_pk = 0
        self.session_pk = 0
        self.img_i = 0

    def _schema(self):
        self.db.executescript("""
        CREATE TABLE ZWACHATSESSION (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZARCHIVED INTEGER,
            ZCONTACTJID VARCHAR, ZPARTNERNAME VARCHAR, ZGROUPINFO INTEGER, ZSESSIONTYPE INTEGER,
            ZLASTMESSAGEDATE TIMESTAMP, ZLASTMESSAGETEXT VARCHAR, ZMESSAGECOUNTER INTEGER);
        CREATE TABLE ZWAGROUPINFO (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZCREATIONDATE TIMESTAMP);
        CREATE TABLE ZWAGROUPMEMBER (Z_PK INTEGER PRIMARY KEY, ZCHATSESSION INTEGER, ZMEMBERJID VARCHAR,
            ZCONTACTNAME VARCHAR, ZFIRSTNAME VARCHAR, ZISADMIN INTEGER);
        CREATE TABLE ZWAMESSAGE (Z_PK INTEGER PRIMARY KEY, Z_ENT INTEGER, Z_OPT INTEGER, ZCHATSESSION INTEGER,
            ZGROUPMEMBER INTEGER, ZISFROMME INTEGER, ZMESSAGEDATE TIMESTAMP, ZSENTDATE TIMESTAMP,
            ZMESSAGETYPE INTEGER, ZTEXT VARCHAR, ZFROMJID VARCHAR, ZTOJID VARCHAR, ZSTANZAID VARCHAR,
            ZMETADATA BLOB, ZMEDIAITEM INTEGER, ZMESSAGESTATUS INTEGER);
        CREATE INDEX Z_WAMESSAGE_ZCHATSESSION ON ZWAMESSAGE (ZCHATSESSION);
        CREATE TABLE ZWAMEDIAITEM (Z_PK INTEGER PRIMARY KEY, ZMESSAGE INTEGER, ZMEDIALOCALPATH VARCHAR,
            ZTITLE VARCHAR, ZVCARDNAME VARCHAR, ZVCARDSTRING VARCHAR, ZLATITUDE FLOAT, ZLONGITUDE FLOAT,
            ZFILESIZE INTEGER, ZMOVIEDURATION INTEGER, ZMEDIAURL VARCHAR, ZMEDIAKEY BLOB, ZTHUMBNAILLOCALPATH VARCHAR);
        CREATE TABLE ZWAPROFILEPUSHNAME (Z_PK INTEGER PRIMARY KEY, ZJID VARCHAR, ZPUSHNAME VARCHAR);
        CREATE TABLE ZWAVCARDMENTION (Z_PK INTEGER PRIMARY KEY, ZMEDIAITEM INTEGER, ZSENDERJID VARCHAR);
        """)
        self.contacts.executescript("""
        CREATE TABLE ZWAADDRESSBOOKCONTACT (Z_PK INTEGER PRIMARY KEY, ZFULLNAME VARCHAR, ZGIVENNAME VARCHAR,
            ZPHONENUMBER VARCHAR, ZWHATSAPPID VARCHAR, ZABOUTTEXT VARCHAR, ZLID VARCHAR);
        """)

    # ---------- dosya üretimi
    def _add_file(self, rel, local):
        self.files[rel] = local

    def make_image(self, rel, label, size=(900, 650)):
        self.img_i += 1
        w, h = size
        img = Image.new("RGB", size)
        d = ImageDraw.Draw(img)
        c1 = [random.randint(40, 220) for _ in range(3)]
        c2 = [random.randint(40, 220) for _ in range(3)]
        for y in range(h):
            t = y / h
            d.line([(0, y), (w, y)], fill=tuple(int(c1[i] * (1 - t) + c2[i] * t) for i in range(3)))
        d.ellipse([w * 0.3, h * 0.25, w * 0.7, h * 0.75], outline="white", width=8)
        d.text((30, 30), label, fill="white")
        path = os.path.join(self.work, "media", rel.replace("/", "_"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        img.save(path, "JPEG", quality=70)
        self._add_file(rel, path)
        return os.path.getsize(path)

    def make_sticker(self, rel):
        img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.ellipse([20, 20, 236, 236], fill=(255, 204, 0, 255))
        d.ellipse([80, 80, 110, 120], fill=(60, 40, 0, 255))
        d.ellipse([146, 80, 176, 120], fill=(60, 40, 0, 255))
        d.arc([70, 110, 186, 200], 10, 170, fill=(60, 40, 0, 255), width=10)
        path = os.path.join(self.work, "media", rel.replace("/", "_"))
        img.save(path, "WEBP")
        self._add_file(rel, path)

    def make_pdf(self, rel):
        img = Image.new("RGB", (595, 842), "white")
        d = ImageDraw.Draw(img)
        d.text((60, 60), "Test belgesi - Fatura 2025", fill="black")
        path = os.path.join(self.work, "media", rel.replace("/", "_"))
        img.save(path, "PDF")
        self._add_file(rel, path)
        return os.path.getsize(path)

    def _ff(self, args, rel):
        if not self.ffmpeg:
            return None
        path = os.path.join(self.work, "media", rel.replace("/", "_"))
        r = subprocess.run([self.ffmpeg, "-y", "-loglevel", "error", *args, path], capture_output=True)
        if r.returncode != 0 or not os.path.isfile(path):
            print("ffmpeg hatası:", r.stderr.decode()[:300])
            return None
        self._add_file(rel, path)
        return os.path.getsize(path)

    def make_video(self, rel, seconds=4):
        for codec in (["-c:v", "libx264", "-pix_fmt", "yuv420p"], ["-c:v", "libopenh264"], ["-c:v", "mpeg4"]):
            size = self._ff(["-f", "lavfi", "-i", f"testsrc=duration={seconds}:size=480x360:rate=25",
                             "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                             *codec, "-c:a", "aac", "-shortest", "-movflags", "+faststart"], rel)
            if size:
                return size
        return None

    def make_voice(self, rel, seconds=6):
        return self._ff(["-f", "lavfi", "-i", f"sine=frequency=330:duration={seconds}",
                         "-c:a", "libopus", "-b:a", "24k", "-f", "ogg"], rel)

    # ---------- veritabanı
    def contact(self, number, full=None, push=None, about=None):
        jid = f"{number}@s.whatsapp.net"
        if full:
            self.contacts.execute("INSERT INTO ZWAADDRESSBOOKCONTACT (ZFULLNAME, ZGIVENNAME, ZPHONENUMBER, ZWHATSAPPID, ZABOUTTEXT) VALUES (?,?,?,?,?)",
                                  (full, full.split()[0], "+" + number, number, about))
        if push:
            self.db.execute("INSERT INTO ZWAPROFILEPUSHNAME (ZJID, ZPUSHNAME) VALUES (?,?)", (jid, push))
        return jid

    def session(self, jid, partner, group=False):
        self.session_pk += 1
        gi = None
        if group:
            cur = self.db.execute("INSERT INTO ZWAGROUPINFO (ZCHATSESSION) VALUES (?)", (self.session_pk,))
            gi = cur.lastrowid
        self.db.execute("INSERT INTO ZWACHATSESSION (Z_PK, Z_ENT, Z_OPT, ZARCHIVED, ZCONTACTJID, ZPARTNERNAME, ZGROUPINFO, ZSESSIONTYPE) VALUES (?,?,?,?,?,?,?,?)",
                        (self.session_pk, 1, 1, 0, jid, partner, gi, 1 if group else 0))
        return self.session_pk

    def member(self, session, jid, contact_name=None):
        self.member_pk += 1
        self.db.execute("INSERT INTO ZWAGROUPMEMBER (Z_PK, ZCHATSESSION, ZMEMBERJID, ZCONTACTNAME, ZFIRSTNAME) VALUES (?,?,?,?,?)",
                        (self.member_pk, session, jid, contact_name, contact_name.split()[0] if contact_name else None))
        return self.member_pk

    def msg(self, session, ts, me, mtype=0, text=None, member=None, reply_to=None, media=None, from_jid=None):
        self.msg_pk += 1
        sid = stanza()
        meta = None
        if reply_to:
            meta = pb_field(5, reply_to[0].encode()) + pb_field(6, (reply_to[1] or "").encode())
        self.db.execute("""INSERT INTO ZWAMESSAGE (Z_PK, Z_ENT, Z_OPT, ZCHATSESSION, ZGROUPMEMBER, ZISFROMME, ZMESSAGEDATE,
                ZSENTDATE, ZMESSAGETYPE, ZTEXT, ZFROMJID, ZSTANZAID, ZMETADATA, ZMESSAGESTATUS) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (self.msg_pk, 9, 1, session, member, 1 if me else 0, apple(ts), apple(ts), mtype, text,
                         from_jid, sid, meta, 6))
        if media:
            self.media_pk += 1
            self.db.execute("""INSERT INTO ZWAMEDIAITEM (Z_PK, ZMESSAGE, ZMEDIALOCALPATH, ZTITLE, ZVCARDNAME, ZVCARDSTRING,
                ZLATITUDE, ZLONGITUDE, ZFILESIZE, ZMOVIEDURATION) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                            (self.media_pk, self.msg_pk, media.get("path"), media.get("title"), media.get("vname"),
                             media.get("vstr"), media.get("lat"), media.get("lon"), media.get("size"), media.get("dur")))
            self.db.execute("UPDATE ZWAMESSAGE SET ZMEDIAITEM=? WHERE Z_PK=?", (self.media_pk, self.msg_pk))
        return (sid, from_jid)

    def finish(self):
        self.db.commit()
        self.contacts.commit()
        self.db.close()
        self.contacts.close()


def gen_conversation(b, session, jid, start, end, n, members=None, media_every=25, rich=False):
    """members: [(member_pk, jid)] grup için."""
    ts = start
    step = (end - start) / max(n, 1)
    recent = []
    for i in range(n):
        # her mesaj kendi dilimi içinde rastgele bir anda: sıralı ve [start, end] aralığında
        ts = start + step * (i + random.uniform(0.05, 0.95))
        me = random.random() < 0.45
        member, mjid = (None, None)
        if members and not me:
            member, mjid = random.choice(members)
        kind = "text"
        if rich and i % media_every == 7:
            kind = random.choice(["image", "image", "image", "voice", "video", "doc", "location", "contact", "sticker", "deleted", "gif"])
        sub = f"Media/{jid}/{random.randint(0, 9)}/{random.randint(0, 9)}"
        reply = random.choice(recent) if recent and random.random() < 0.06 else None
        if kind == "image":
            rel = f"{sub}/{uuid.uuid4()}.jpg"
            size = b.make_image("Message/" + rel, f"Foto {b.img_i}")
            caption = random.choice([None, None, "Bak ne güzel manzara 🌄", "Bugünden"])
            recent.append(b.msg(session, ts, me, 1, None, member, reply, {"path": rel, "title": caption, "size": size}, jid))
        elif kind == "voice":
            rel = f"{sub}/{uuid.uuid4()}.opus"
            size = b.make_voice("Message/" + rel, seconds=random.randint(3, 9))
            b.msg(session, ts, me, 3, None, member, None, {"path": rel, "size": size, "dur": 6}, jid)
        elif kind in ("video", "gif"):
            rel = f"{sub}/{uuid.uuid4()}.mp4"
            size = b.make_video("Message/" + rel, seconds=3)
            b.msg(session, ts, me, 2 if kind == "video" else 11, None, member, None,
                  {"path": rel, "size": size, "dur": 3, "title": "Tatilden video" if kind == "video" else None}, jid)
        elif kind == "doc":
            rel = f"{sub}/{uuid.uuid4()}.pdf"
            size = b.make_pdf("Message/" + rel)
            b.msg(session, ts, me, 8, None, member, None, {"path": rel, "title": "Fatura_Kasım.pdf", "size": size}, jid)
        elif kind == "location":
            b.msg(session, ts, me, 5, None, member, None, {"lat": 41.0082, "lon": 28.9784, "title": "Sultanahmet Meydanı"}, jid)
        elif kind == "contact":
            b.msg(session, ts, me, 4, None, member, None, {"vname": "Dr. Zeynep Kaya", "vstr": "BEGIN:VCARD\nFN:Dr. Zeynep Kaya\nEND:VCARD"}, jid)
        elif kind == "sticker":
            rel = f"{sub}/{uuid.uuid4()}.webp"
            b.make_sticker("Message/" + rel)
            b.msg(session, ts, me, 15, None, member, None, {"path": rel}, jid)
        elif kind == "deleted":
            b.msg(session, ts, me, 14, None, member, None, None, jid)
        else:
            text = LONG if (rich and i % 97 == 3) else random.choice(TEXTS)
            recent.append(b.msg(session, ts, me, 0, text, member, reply, None, jid))
        recent = recent[-30:]
    return ts


def build_fake_backup(out_root, big=True, seed=42):
    """Sahte bir iTunes yedeği üretir ve yedek klasörünün yolunu döndürür."""
    random.seed(seed)
    out_root = os.path.abspath(out_root)
    udid = "00008110-001A2B3C4D5E6F70"
    backup_dir = os.path.join(out_root, udid)
    if os.path.exists(backup_dir):
        shutil.rmtree(backup_dir)
    os.makedirs(backup_dir)
    work = tempfile.mkdtemp(prefix="wa_fake_")
    b = Builder(work)

    now = dt.datetime(2026, 11, 20, 21, 30).timestamp()
    year = 365 * 86400

    # Kişiler
    anne = b.contact("905000000101", "Annem", push="Ayşe")
    ahmet = b.contact("905000000102", "Ahmet Yılmaz", push="Ahmet Y.")
    elif_ = b.contact("905000000103", "Elif Şahin", push="Elif")
    baba = b.contact("905000000104", "Babam", push="Mustafa")
    kardes = b.contact("905000000105", "Kardeşim Can", push="Can")
    mehmet = b.contact("905000000106", None, push="Mehmet Öztürk")  # rehberde yok
    yabanci = b.contact("447700900123", None, push="John")

    # Profil fotoğrafları
    for num in ("905000000101", "905000000102", "905000000103"):
        b.make_image(f"Media/Profile/{num}-1700000000.jpg", "Profil", size=(320, 320))
    b.make_image("Media/Profile/120363025555111222-1700000001.jpg", "Grup", size=(320, 320))

    # 1) Annem: uzun sohbet
    s = b.session(anne, "Annem")
    gen_conversation(b, s, anne, now - 3 * year, now - 3600, 2600, rich=True, media_every=40)
    # 2) Ahmet
    s = b.session(ahmet, "Ahmet Yılmaz")
    gen_conversation(b, s, ahmet, now - year, now - 86400 * 2, 300, rich=True, media_every=30)
    # 3) Aile grubu
    g = b.session("120363025555111222@g.us", "Aile 👨‍👩‍👧", group=True)
    members = [(b.member(g, baba, "Babam"), baba), (b.member(g, anne, "Annem"), anne),
               (b.member(g, kardes, "Kardeşim Can"), kardes)]
    t = gen_conversation(b, g, "120363025555111222@g.us", now - 2 * year, now - 86400 * 40, 600,
                         members=members, rich=True, media_every=20)
    b.msg(g, t + 60, False, 6, "Aile 👨‍👩‍👧", members[0][0], None, None, "120363025555111222@g.us")
    gen_conversation(b, g, "120363025555111222@g.us", t + 120, now - 7200, 200, members=members, rich=True, media_every=20)
    # 4) İş grubu (rehberde olmayan üye)
    g2 = b.session("905000000101-1501234567@g.us", "İş Arkadaşları", group=True)
    members2 = [(b.member(g2, elif_, "Elif Şahin"), elif_), (b.member(g2, mehmet, None), mehmet),
                (b.member(g2, "905000000199@s.whatsapp.net", None), "905000000199@s.whatsapp.net")]
    gen_conversation(b, g2, "905000000101-1501234567@g.us", now - 200 * 86400, now - 86400 * 3, 250,
                     members=members2, rich=False)
    # 5) Rehberde olmayan kişi
    s = b.session(yabanci, "+44 7700 900123")
    gen_conversation(b, s, yabanci, now - 400 * 86400, now - 300 * 86400, 15)
    # 6) Eksik medya içeren sohbet
    s = b.session(elif_, "Elif Şahin")
    gen_conversation(b, s, elif_, now - 90 * 86400, now - 86400 * 6, 60)
    b.msg(s, now - 86400 * 5, False, 1, None, None, None, {"path": f"Media/{elif_}/1/2/eksik-foto.jpg", "size": 120000}, elif_)
    b.msg(s, now - 86400 * 5 + 30, True, 0, "Fotoğraf açılmıyor galiba", None, None, None, elif_)
    # 7) Doldurmak için çok sayıda küçük sohbet
    names = ["Berk", "Ceren", "Deniz", "Ebru", "Furkan", "Gizem", "Hakan", "Irmak", "İlker", "Jale", "Kemal", "Leyla",
             "Murat", "Nazlı", "Oğuz", "Özge", "Pınar", "Rıza", "Selin", "Şule", "Tolga", "Ufuk", "Ümran", "Volkan",
             "Yasemin", "Zafer", "Kuaför Salon", "Tesisatçı Ali", "Site Yönetimi", "Dişçi Randevu"]
    for i, nm in enumerate(names):
        num = f"9050000{1000 + i:05d}"
        jid = b.contact(num, nm + (" " + random.choice(["Kaya", "Demir", "Çelik", "Arslan", "Koç"]) if " " not in nm else ""))
        s = b.session(jid, nm)
        start = now - random.uniform(30, 1000) * 86400
        gen_conversation(b, s, jid, start, start + random.uniform(1, 60) * 86400, random.randint(3, 40))
    # 8) Çok büyük bir sohbet (performans denemesi)
    if big:
        big = b.contact("905000000107", "Kalabalık Sohbet")
        s = b.session(big, "Kalabalık Sohbet")
        gen_conversation(b, s, big, now - 5 * year, now - 86400 * 9, 20000)
    # 9) Durum güncellemeleri (gösterilmemeli)
    s = b.session("status@broadcast", "Durum")
    gen_conversation(b, s, "status@broadcast", now - 10 * 86400, now - 86400, 5)
    b.finish()

    # Gerçek yedeklerde bulunan, arşiv için gereksiz ve adı çok uzun dosyalar (atlanmalı)
    junk = os.path.join(work, "junk.bin")
    with open(junk, "wb") as f:
        f.write(b"x" * 100)
    b._add_file("fba_extentions_event_store/net.whatsapp.WhatsApp/net.whatsapp.WhatsApp(963936340)^UFS-01ED35AD-"
                + "^".join(["wa_encr.AReSFGqVR0qz0PVOiV" * 3] * 4), junk)
    b._add_file("Library/Caches/com.facebook.sdk/cache.db", junk)
    # Medya klasöründe Windows'ta oluşturulamayacak kadar uzun adlı bir dosya (atlanıp sayılmalı)
    b._add_file("Message/Media/" + "uzun" * 70 + ".jpg", junk)

    # ---- iTunes yedek yapısı
    man = sqlite3.connect(os.path.join(backup_dir, "Manifest.db"))
    man.execute("CREATE TABLE Files (fileID TEXT PRIMARY KEY, domain TEXT, relativePath TEXT, flags INTEGER, file BLOB)")
    man.execute("CREATE TABLE Properties (key TEXT PRIMARY KEY, value BLOB)")
    dirs = set()
    for rel, local in b.files.items():
        fid = hashlib.sha1(f"{DOMAIN}-{rel}".encode()).hexdigest()
        dest = os.path.join(backup_dir, fid[:2], fid)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(local, dest)
        man.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (fid, DOMAIN, rel, 1, b""))
        parts = rel.split("/")[:-1]
        for k in range(1, len(parts) + 1):
            dirs.add("/".join(parts[:k]))
    for d in dirs:
        fid = hashlib.sha1(f"{DOMAIN}-{d}".encode()).hexdigest()
        man.execute("INSERT OR IGNORE INTO Files VALUES (?,?,?,?,?)", (fid, DOMAIN, d, 2, b""))
    # başka bir uygulamaya ait dosya (atlanmalı)
    fid = hashlib.sha1(b"HomeDomain-Library/Notes/notes.sqlite").hexdigest()
    os.makedirs(os.path.join(backup_dir, fid[:2]), exist_ok=True)
    with open(os.path.join(backup_dir, fid[:2], fid), "wb") as f:
        f.write(b"not whatsapp")
    man.execute("INSERT INTO Files VALUES (?,?,?,?,?)", (fid, "HomeDomain", "Library/Notes/notes.sqlite", 1, b""))
    man.commit()
    man.close()

    backup_date = dt.datetime(2026, 11, 20, 22, 5)
    with open(os.path.join(backup_dir, "Info.plist"), "wb") as f:
        plistlib.dump({"Device Name": "Test iPhone'u", "Display Name": "Test iPhone'u", "Product Name": "iPhone 15",
                       "Product Type": "iPhone15,4", "Product Version": "18.6", "Last Backup Date": backup_date}, f)
    with open(os.path.join(backup_dir, "Manifest.plist"), "wb") as f:
        plistlib.dump({"IsEncrypted": False, "Date": backup_date, "Version": "10.0"}, f)
    shutil.rmtree(work, ignore_errors=True)
    return backup_dir


def main():
    big = os.environ.get("FAKE_BUYUK", "1") == "1"
    path = build_fake_backup(sys.argv[1], big=big)
    print(f"Sahte yedek: {path}")


if __name__ == "__main__":
    main()
