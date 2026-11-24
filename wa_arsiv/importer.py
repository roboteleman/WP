"""iPhone (iTunes / Apple Aygıtları) yedeğinden WhatsApp verilerini içe aktarma."""
import calendar
import datetime as dt
import json
import os
import plistlib
import shutil
import sqlite3
import sys
import threading
import time
import traceback
from pathlib import Path

from . import archive as archive_mod
from .paths import long_path

WA_DOMAINS = [
    ("AppDomainGroup-group.net.whatsapp.WhatsApp.shared", "WhatsApp"),
    ("AppDomainGroup-group.net.whatsapp.WhatsAppSMB.shared", "WhatsApp Business"),
]

NO_WHATSAPP_HINT = (
    "Bu yedekte WhatsApp sohbetleri bulunamadı. En sık sebep: iPhone'da WhatsApp'ın "
    "'Uçtan uca şifreli yedekleme' özelliğinin açık olması. WhatsApp → Ayarlar → Sohbetler → "
    "Sohbet Yedeği → Uçtan Uca Şifreli Yedekleme'yi kapatıp bilgisayara yeniden yedek alın."
)


class ImportFailed(Exception):
    pass


class Cancelled(Exception):
    pass


# ---------------------------------------------------------------- yedek bulma

def backup_roots():
    roots = []
    env = os.environ.get("WA_ARSIV_YEDEK_KOK")
    if env:
        roots.append(env)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        profile = os.environ.get("USERPROFILE")
        if appdata:
            roots.append(os.path.join(appdata, "Apple Computer", "MobileSync", "Backup"))
        if profile:
            roots.append(os.path.join(profile, "Apple", "MobileSync", "Backup"))
    elif sys.platform == "darwin":
        roots.append(os.path.expanduser("~/Library/Application Support/MobileSync/Backup"))
    return roots


def _read_plist(path):
    try:
        with open(path, "rb") as f:
            return plistlib.load(f)
    except Exception:
        return {}


def describe_backup(path):
    """Bir yedek klasörü hakkında özet bilgi; yedek değilse None."""
    if not os.path.isdir(path):
        return None
    manifest_db = os.path.join(path, "Manifest.db")
    manifest_plist = os.path.join(path, "Manifest.plist")
    if not (os.path.isfile(manifest_db) or os.path.isfile(manifest_plist)):
        return None
    info = _read_plist(os.path.join(path, "Info.plist"))
    man = _read_plist(manifest_plist)
    date = info.get("Last Backup Date") or man.get("Date")
    if isinstance(date, dt.datetime):
        if date.tzinfo is not None:
            ts = date.timestamp()
        else:  # plistlib saatleri UTC olarak verir
            ts = calendar.timegm(date.timetuple())
    else:
        try:
            ts = os.path.getmtime(manifest_db if os.path.isfile(manifest_db) else manifest_plist)
        except OSError:
            ts = 0
    lockdown = man.get("Lockdown") or {}
    name = (info.get("Device Name") or info.get("Display Name")
            or lockdown.get("DeviceName") or os.path.basename(path.rstrip("\\/")))
    return {
        "path": os.path.abspath(path),
        "name": str(name),
        "product": str(info.get("Product Name") or info.get("Product Type") or lockdown.get("ProductType") or ""),
        "ios": str(info.get("Product Version") or lockdown.get("ProductVersion") or ""),
        "date": int(ts * 1000),
        "encrypted": bool(man.get("IsEncrypted", False)),
    }


def find_backups():
    found = []
    seen = set()
    for root in backup_roots():
        if not os.path.isdir(root):
            continue
        try:
            entries = os.listdir(root)
        except OSError:
            continue
        for entry in entries:
            full = os.path.join(root, entry)
            info = describe_backup(full)
            if info and info["path"] not in seen:
                seen.add(info["path"])
                found.append(info)
    found.sort(key=lambda b: b["date"], reverse=True)
    return found


# ---------------------------------------------------------------- yardımcılar

def wanted(relative_path):
    """Arşiv için gereken dosyalar: sohbet veritabanı, rehber, mesaj medyaları, profil fotoğrafları.

    WhatsApp'ın yedek klasöründe ayrıca önbellek, istatistik vb. dosyalar vardır; bunların bazılarının
    adları Windows'un yol uzunluğu sınırını aşar. Gerekmeyen hiçbir şey kopyalanmaz.
    """
    r = (relative_path or "").replace("\\", "/").lstrip("/")
    top = r.split("/")[0]
    return (top.startswith("ChatStorage.sqlite") or top.startswith("ContactsV2.sqlite")
            or r.startswith("Message/") or r.startswith("Media/Profile/"))


def linkable(relative_path):
    """Medya dosyaları içerikleri hiç değişmediği için kopyalamak yerine sabit bağlantıyla alınabilir.
    Veritabanları her zaman kopyalanır (program onlara arama dizini ekler; yedeğe dokunulmamalı)."""
    r = (relative_path or "").replace("\\", "/").lstrip("/")
    return r.startswith("Message/") or r.startswith("Media/Profile/")


def can_hardlink(src, folder):
    """Bu yedek dosyası ile hedef klasör arasında sabit bağlantı (hard link) kurulabiliyor mu?
    Aynı disk ve NTFS/APFS gibi destekleyen bir dosya sistemi gerekir."""
    test = os.path.join(folder, ".baglanti_testi")
    try:
        if os.path.lexists(test):
            os.remove(test)
        os.link(long_path(src), long_path(test))
        os.remove(test)
        return True
    except (OSError, NotImplementedError, AttributeError):
        try:
            os.remove(test)
        except OSError:
            pass
        return False


def _safe_dest(base, relative_path):
    """Yedekteki göreli yolu hedef klasör içinde güvenli bir yola çevirir."""
    parts = [p for p in relative_path.replace("\\", "/").split("/") if p not in ("", ".")]
    if not parts or any(p == ".." for p in parts):
        return None
    if any(":" in p for p in parts):  # Windows'ta sürücü harfi / ADS
        parts = [p.replace(":", "_") for p in parts]
    return os.path.join(base, *parts)


def _prepare_dest(dest):
    """Hedef klasörü oluşturur; dosya bu sistemde oluşturulamıyorsa None döner."""
    try:
        os.makedirs(long_path(os.path.dirname(dest)), exist_ok=True)
        return long_path(dest)
    except (OSError, ValueError):
        return None


def human_size(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def _new_archive_id(data_root, label):
    base = time.strftime("%Y%m%d-%H%M%S")
    if label != "WhatsApp":
        base += "-business"
    candidate = base
    i = 2
    while os.path.exists(os.path.join(data_root, candidate)) or \
            os.path.exists(os.path.join(data_root, candidate + ".partial")):
        candidate = f"{base}-{i}"
        i += 1
    return candidate


def cleanup_partials(data_root):
    """Yarıda kalmış içe aktarmaları temizler."""
    try:
        for entry in os.listdir(data_root):
            if entry.endswith(".partial"):
                shutil.rmtree(os.path.join(data_root, entry), ignore_errors=True)
    except OSError:
        pass


# ---------------------------------------------------------------- içe aktarma

class ImportJob:
    def __init__(self, data_root, backup_path, password=None):
        self.data_root = data_root
        self.backup_path = backup_path
        self.password = password
        self._cancel = threading.Event()
        self._lock = threading.Lock()
        self.failed = []  # kopyalanamayan dosyalar (göreli yollar)
        self.use_links = False  # medyalar sabit bağlantıyla mı alınacak
        self.linked_files = 0
        self.linked_bytes = 0
        self._status = {
            "state": "running",       # running | done | error | cancelled
            "phase": "Başlatılıyor…",
            "done": 0, "total": 0,
            "bytes_done": 0, "bytes_total": 0,
            "message": "",
            "archives": [],
        }

    # durum
    def snapshot(self):
        with self._lock:
            return dict(self._status)

    def _set(self, **kw):
        with self._lock:
            self._status.update(kw)

    def _add(self, files=0, nbytes=0):
        with self._lock:
            self._status["done"] += files
            self._status["bytes_done"] += nbytes

    def cancel(self):
        self._cancel.set()

    def _check_cancel(self):
        if self._cancel.is_set():
            raise Cancelled()

    def start(self):
        threading.Thread(target=self._run, name="ice-aktarma", daemon=True).start()

    def _run(self):
        created_partials = []
        try:
            info = describe_backup(self.backup_path)
            if not info:
                raise ImportFailed("Seçilen klasör bir iPhone yedeği değil (Manifest.db bulunamadı).")
            if info["encrypted"]:
                plan = self._plan_encrypted(info)
            else:
                plan = self._plan_plain(info)

            total_files = sum(len(p["files"]) for p in plan)
            total_bytes = sum(p["bytes"] for p in plan)
            need = total_bytes
            if not info["encrypted"]:
                probe = next((f for p in plan for f in p["files"] if linkable(f[1])), None)
                if probe and can_hardlink(probe[0], self.data_root):
                    self.use_links = True
                    need = sum(p["db_bytes"] for p in plan)
            free = shutil.disk_usage(self.data_root).free
            if need + 200 * 1024 * 1024 > free:
                raise ImportFailed(
                    f"Diskte yeterli boş alan yok. Gerekli: yaklaşık {human_size(need)}, "
                    f"boş: {human_size(free)}. Programı daha geniş bir diske taşıyıp tekrar deneyin.")
            self._set(total=total_files, bytes_total=total_bytes, done=0, bytes_done=0)

            results = []
            for p in plan:
                self._check_cancel()
                archive_id = _new_archive_id(self.data_root, p["label"])
                partial = os.path.join(self.data_root, archive_id + ".partial")
                files_dir = os.path.join(partial, "files")
                os.makedirs(files_dir, exist_ok=True)
                created_partials.append(partial)

                label_suffix = "" if p["label"] == "WhatsApp" else f" ({p['label']})"
                if self.use_links:
                    self._set(phase=f"Dosyalar alınıyor (medyalar kopyalanmadan bağlanıyor){label_suffix}…")
                else:
                    self._set(phase=f"Dosyalar kopyalanıyor{label_suffix}…")
                self.failed = []
                self.linked_files = self.linked_bytes = 0
                p["extract"](files_dir)
                self._check_cancel()
                if self.failed:
                    print(f"Kopyalanamayan {len(self.failed)} dosya:", *self.failed[:50], sep="\n  ", flush=True)

                self._set(phase="Mesajlar dizinleniyor (arama için)…")
                db_path = os.path.join(files_dir, "ChatStorage.sqlite")
                if not os.path.isfile(db_path):
                    raise ImportFailed(NO_WHATSAPP_HINT)
                stats = archive_mod.prepare_database(db_path, cancel_check=self._check_cancel)

                meta = {
                    "id": archive_id,
                    "name": info["name"] + label_suffix,
                    "device": info["product"],
                    "ios": info["ios"],
                    "backup_date": info["date"],
                    "imported_at": int(time.time() * 1000),
                    "app": p["label"],
                    "domain": p["domain"],
                    "encrypted_backup": info["encrypted"],
                    "missing_files": p.get("missing", 0) + len(self.failed),
                    "linked_files": self.linked_files,
                    "linked_bytes": self.linked_bytes,
                    **stats,
                }
                with open(os.path.join(partial, "archive.json"), "w", encoding="utf-8") as f:
                    json.dump(meta, f, ensure_ascii=False, indent=2)
                final = os.path.join(self.data_root, archive_id)
                os.replace(partial, final)
                created_partials.remove(partial)
                results.append(meta)

            self._set(state="done", phase="Tamamlandı", archives=results)
        except Cancelled:
            self._set(state="cancelled", phase="İptal edildi", message="İçe aktarma iptal edildi.")
        except ImportFailed as e:
            self._set(state="error", phase="Hata", message=str(e))
        except Exception as e:  # beklenmeyen
            traceback.print_exc()
            self._set(state="error", phase="Hata", message=f"Beklenmeyen bir hata oluştu: {e}")
        finally:
            for partial in created_partials:
                shutil.rmtree(partial, ignore_errors=True)
            self.password = None

    # ---- şifresiz yedek
    def _plan_plain(self, info):
        self._set(phase="Yedek okunuyor…")
        manifest = os.path.join(self.backup_path, "Manifest.db")
        try:
            uri = Path(manifest).resolve().as_uri() + "?mode=ro"
            con = sqlite3.connect(uri, uri=True)
            plan = []
            for domain, label in WA_DOMAINS:
                rows = con.execute(
                    "SELECT fileID, relativePath FROM Files WHERE domain = ? AND flags = 1",
                    (domain,)).fetchall()
                if not rows:
                    continue
                if not any(r[1] == "ChatStorage.sqlite" for r in rows):
                    continue
                files, total, db_total, missing = [], 0, 0, 0
                for file_id, rel in rows:
                    if not wanted(rel):
                        continue
                    src = os.path.join(self.backup_path, file_id[:2], file_id)
                    try:
                        size = os.path.getsize(src)
                    except OSError:
                        missing += 1
                        continue
                    files.append((src, rel, size))
                    total += size
                    if not linkable(rel):
                        db_total += size
                plan.append({"domain": domain, "label": label, "files": files, "bytes": total,
                             "db_bytes": db_total, "missing": missing,
                             "extract": (lambda dest, _files=files: self._copy_plain(_files, dest))})
            con.close()
        except sqlite3.DatabaseError:
            raise ImportFailed("Yedeğin dosya listesi (Manifest.db) okunamadı. Yedek bozuk olabilir; "
                               "bilgisayara yeniden yedek almayı deneyin.")
        if not plan:
            raise ImportFailed(NO_WHATSAPP_HINT)
        return plan

    def _copy_plain(self, files, dest_root):
        for src, rel, size in files:
            self._check_cancel()
            dest = _safe_dest(dest_root, rel)
            target = _prepare_dest(dest) if dest else None
            try:
                if target is None:
                    raise OSError("geçersiz hedef yol")
                linked = False
                if self.use_links and linkable(rel):
                    try:
                        os.link(long_path(src), target)
                        linked = True
                        self.linked_files += 1
                        self.linked_bytes += size
                    except OSError:
                        pass  # bağlanamazsa normal kopyaya düş
                if not linked:
                    shutil.copyfile(long_path(src), target)
            except OSError as e:
                if rel.startswith("ChatStorage.sqlite"):
                    raise ImportFailed(f"Sohbet veritabanı kopyalanamadı: {e}")
                self.failed.append(rel)
            self._add(1, size)

    # ---- şifreli yedek
    def _plan_encrypted(self, info):
        try:
            from iphone_backup_decrypt import EncryptedBackup
            from iphone_backup_decrypt.exceptions import IncorrectPassphraseError
        except ImportError:
            raise ImportFailed("Şifreli yedekleri açma bileşeni (iphone_backup_decrypt) eksik.")
        if not self.password:
            raise ImportFailed("Bu yedek şifreli. Lütfen iTunes / Apple Aygıtları'nda belirlediğiniz yedek şifresini girin.")
        self._set(phase="Yedek şifresi doğrulanıyor… (1–2 dakika sürebilir)")
        try:
            backup = EncryptedBackup(backup_directory=self.backup_path, passphrase=self.password)
            backup.test_decryption()
        except IncorrectPassphraseError:
            raise ImportFailed("Yedek şifresi yanlış. Bu, iPhone kilit kodu değil; bilgisayarda "
                               "'Yerel yedeği şifrele' seçeneğini açarken belirlediğiniz şifredir.")
        except ValueError as e:
            raise ImportFailed(f"Şifreli yedek açılamadı: {e}")
        self.password = None
        self._backup = backup

        plan = []
        with backup.manifest_db_cursor() as cur:
            for domain, label in WA_DOMAINS:
                cur.execute("SELECT fileID, relativePath FROM Files WHERE domain = ? AND flags = 1", (domain,))
                rows = [r for r in cur.fetchall() if wanted(r[1])]
                if not rows or not any(r[1] == "ChatStorage.sqlite" for r in rows):
                    continue
                total = 0
                for file_id, _rel in rows:
                    try:
                        total += os.path.getsize(os.path.join(self.backup_path, file_id[:2], file_id))
                    except OSError:
                        pass
                plan.append({"domain": domain, "label": label, "files": rows, "bytes": total,
                             "extract": (lambda dest, _d=domain: self._extract_encrypted(_d, dest))})
        if not plan:
            raise ImportFailed(NO_WHATSAPP_HINT)
        return plan

    def _extract_encrypted(self, domain, dest_root):
        def on_file(*, file_plist=None, relative_path="", **_kw):
            if self._cancel.is_set():
                raise Cancelled()
            if not wanted(relative_path):
                return False
            size = getattr(file_plist, "filesize", 0) or 0
            self._add(1, size)
            dest = _safe_dest(dest_root, relative_path)
            target = _prepare_dest(dest) if dest else None
            if target is not None:
                try:  # dosyanın bu sistemde oluşturulabildiğini önceden dene
                    with open(target, "ab"):
                        pass
                except OSError:
                    target = None
            if target is None:
                if relative_path.startswith("ChatStorage.sqlite"):
                    raise ImportFailed("Sohbet veritabanı kopyalanamadı.")
                self.failed.append(relative_path)
                return False
            return target

        self._backup.extract_files(domain_like=domain, output_folder=dest_root,
                                   preserve_folders=True, filter_callback=on_file)
