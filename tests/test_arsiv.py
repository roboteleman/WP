"""Uçtan uca testler: sahte bir iPhone yedeği üretilir, içe aktarılır ve arşiv sorgulanır.

Çalıştırma:  python -m unittest discover -s tests -v
Gerekenler:  Pillow, iphone_backup_decrypt   (pip install -r requirements-dev.txt)
"""
import hashlib
import importlib.util
import os
import shutil
import sys
import tempfile
import time
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "dev"))

from make_fake_backup import DOMAIN, build_fake_backup  # noqa: E402
from wa_arsiv import archive, importer  # noqa: E402


def _backup_file(backup, rel):
    fid = hashlib.sha1(f"{DOMAIN}-{rel}".encode()).hexdigest()
    return os.path.join(backup, fid[:2], fid)


def _sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


class ArsivTesti(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="wa_test_")
        cls.backup = build_fake_backup(os.path.join(cls.tmp, "yedekler"), big=False)
        cls.backup_db_sha = _sha(_backup_file(cls.backup, "ChatStorage.sqlite"))
        cls.data = os.path.join(cls.tmp, "veri")
        os.makedirs(cls.data)
        job = importer.ImportJob(cls.data, cls.backup)
        job.start()
        deadline = time.time() + 180
        while job.snapshot()["state"] == "running" and time.time() < deadline:
            time.sleep(0.05)
        cls.status = job.snapshot()
        assert cls.status["state"] == "done", cls.status
        cls.meta = cls.status["archives"][0]
        cls.arc = archive.Archive(os.path.join(cls.data, cls.meta["id"]))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def chat(self, name):
        return next(c for c in self.arc.chats() if c["name"] == name)

    def all_messages(self, chat_id):
        out, cur = [], None
        while True:
            page = self.arc.messages(chat_id, before=cur, limit=500) if cur else self.arc.messages(chat_id, limit=500)
            out = page["messages"] + out
            if not page["more_before"]:
                return out
            cur = tuple(page["messages"][0]["cur"])

    # ---- içe aktarma
    def test_ice_aktarma_ozeti(self):
        chats = self.arc.chats()
        self.assertEqual(len(chats), 36)
        self.assertEqual(self.meta["chat_count"], 36)
        self.assertGreater(self.meta["message_count"], 4000)
        self.assertFalse(any(c["jid"] == "status@broadcast" for c in chats))

    def test_yalnizca_gereken_dosyalar_alinir(self):
        files = os.path.join(self.arc.folder, "files")
        self.assertTrue(set(os.listdir(files)) <= {"ChatStorage.sqlite", "ContactsV2.sqlite", "Media", "Message"})

    def test_cok_uzun_dosya_adi_atlanir(self):
        self.assertGreaterEqual(self.meta["missing_files"], 1)

    def test_yedekteki_veritabani_degismez(self):
        self.assertEqual(_sha(_backup_file(self.backup, "ChatStorage.sqlite")), self.backup_db_sha)

    def test_medyalar_sabit_baglantiyla_alinir(self):
        if not hasattr(os, "link"):
            self.skipTest("os.link yok")
        self.assertGreater(self.meta["linked_files"], 0)
        photo = self.arc.media_overview(self.chat("Annem")["id"])["photos"][0]
        self.assertGreaterEqual(os.stat(self.arc.resolve_file(photo["media"])).st_nlink, 2)
        db = os.path.join(self.arc.files, "ChatStorage.sqlite")
        self.assertEqual(os.stat(db).st_nlink, 1)  # veritabanı ayrı kopya

    # ---- isimler
    def test_isimler(self):
        names = {c["name"] for c in self.arc.chats()}
        self.assertIn("Annem", names)
        self.assertIn("Aile 👨‍👩‍👧", names)
        is_ = self.chat("İş Arkadaşları")
        senders = {m["sender"] for m in self.all_messages(is_["id"]) if not m["me"]}
        self.assertIn("~Mehmet Öztürk (+905000000106)", senders)
        self.assertIn("+905000000199", senders)

    # ---- mesajlar
    def test_sayfalama_tum_mesajlar(self):
        msgs = self.all_messages(self.chat("Annem")["id"])
        ids = [m["id"] for m in msgs]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual([m["ts"] for m in msgs], sorted(m["ts"] for m in msgs))
        self.assertEqual(len(msgs), 2600)
        kinds = {m["kind"] for m in msgs}
        self.assertTrue({"text", "image", "voice", "document", "location", "contact", "deleted"} <= kinds)
        self.assertTrue(any(m.get("reply") for m in msgs))

    def test_etrafinda_ve_tarihe_gore(self):
        c = self.chat("Annem")
        msgs = self.all_messages(c["id"])
        target = msgs[1000]
        page = self.arc.messages(c["id"], around=target["id"], limit=40)
        self.assertIn(target["id"], [m["id"] for m in page["messages"]])
        self.assertTrue(page["more_before"] and page["more_after"])
        page = self.arc.messages(c["id"], date_ms=target["ts"])
        self.assertEqual(page["target"], target["id"])

    def test_sistem_ve_eksik_medya(self):
        aile = self.all_messages(self.chat("Aile 👨‍👩‍👧")["id"])
        self.assertTrue(any(m["kind"] == "system" and "grup adını" in m["text"] for m in aile))
        elif_ = self.all_messages(self.chat("Elif Şahin")["id"])
        self.assertTrue(any(m["kind"] == "image" and m["missing"] for m in elif_))

    # ---- arama
    def test_turkce_arama(self):
        self.assertTrue(any("Şükrü" in r["text"] for r in self.arc.search("sukru")))
        self.assertTrue(any("İstanbul" in r["text"] for r in self.arc.search("ISTANBUL")))
        c = self.chat("Annem")
        self.assertTrue(all(r["chat"] == c["id"] for r in self.arc.search("istanbul", chat_id=c["id"])))
        self.assertEqual(self.arc.search("x"), [])

    # ---- medya paneli
    def test_medya_listesi(self):
        m = self.arc.media_overview(self.chat("Annem")["id"])
        self.assertGreater(len(m["photos"]), 0)
        if importlib.util.find_spec("imageio_ffmpeg"):  # ffmpeg yoksa sahte yedekte video olmaz
            self.assertGreater(len(m["videos"]), 0)
        self.assertTrue(any(d["file_name"] == "Fatura_Kasım.pdf" for d in m["docs"]))
        self.assertTrue(any(link["domain"] == "youtube.com" for link in m["links"]))
        self.assertTrue(any(link["url"].startswith("https://www.example.com/") for link in m["links"]))
        self.assertEqual([p["ts"] for p in m["photos"]], sorted(p["ts"] for p in m["photos"]))
        empty = self.arc.media_overview(self.chat("Elif Şahin")["id"])
        self.assertEqual(empty["photos"], [])

    # ---- yardımcılar
    def test_yardimcilar(self):
        self.assertTrue(importer.wanted("Message/Media/a.jpg"))
        self.assertTrue(importer.wanted("ChatStorage.sqlite-wal"))
        self.assertFalse(importer.wanted("fba_extentions_event_store/x"))
        self.assertTrue(importer.linkable("Media/Profile/1.jpg"))
        self.assertFalse(importer.linkable("ChatStorage.sqlite"))
        self.assertIsNone(importer._safe_dest("/tmp/x", "../kacis.txt"))
        self.assertEqual(archive.norm("İĞÜŞÖÇ ığüşöç"), "igusoc igusoc")


if __name__ == "__main__":
    unittest.main()
