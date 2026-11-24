"""Windows için hazır (kurulum gerektirmeyen) paket üretir — Mac/Linux üzerinde çalışır.

Ortaya çıkan zip'in içeriği:
    WhatsApp Arsivi/
        WhatsApp Arsivi.exe   -> başlatıcı (pip'in Windows başlatıcısı + gömülü açılış betiği)
        OKU BENI.txt
        runtime/              -> python.org'un taşınabilir (embeddable) Python 3.12'si + kütüphaneler
        app/                  -> programın kendisi

Kullanım (gerekenleri kendisi indirir; Pillow gerekir):
    python dev/build_windows_package.py --download --out dist

Ya da önceden indirilmiş parçalarla:
    python dev/build_windows_package.py --embed python-3.12.x-embed-amd64.zip --wheels <win paket klasörü> --out dist

İndirilenler: python.org'un taşınabilir Python'u (SHA-256 ile doğrulanır) ve aşağıdaki sabit sürümlü
kütüphanelerin Windows tekerlekleri (pip --platform win_amd64; hiçbiri bu makinede çalıştırılmaz).
"""
import argparse
import hashlib
import io
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_NAME = "WhatsApp Arsivi"

EMBED_URL = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-embed-amd64.zip"
EMBED_SHA256 = "4acbed6dd1c744b0376e3b1cf57ce906f9dc9e95e68824584c8099a63025a3c3"
# Sınanmış sürümler (Windows'ta çalışan pakette bunlar var)
WIN_WHEELS = [
    "pywebview==6.2.1", "bottle==0.13.4", "typing_extensions==4.16.0",
    "pythonnet==3.2.1", "clr_loader==0.3.1", "cffi==2.1.1", "pycparser==3.12",
    "iphone_backup_decrypt==0.11.2", "pycryptodome==4.6.0",
]
PURE_SDISTS = ["proxy_tools==0.1.0"]  # yalnızca kaynak paketi var; saf Python olduğu için her yerde kurulabilir


def download_inputs(cache):
    os.makedirs(cache, exist_ok=True)
    embed = os.path.join(cache, os.path.basename(EMBED_URL))
    if not os.path.isfile(embed):
        print("İndiriliyor:", EMBED_URL)
        urllib.request.urlretrieve(EMBED_URL, embed + ".tmp")
        os.replace(embed + ".tmp", embed)
    with open(embed, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    if digest != EMBED_SHA256:
        os.remove(embed)
        sys.exit(f"Taşınabilir Python'un SHA-256 değeri beklenenden farklı: {digest}")
    wheels = os.path.join(cache, "win-site-packages")
    if os.path.isdir(wheels):
        shutil.rmtree(wheels)
    pip = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
           "--no-compile", "--no-deps", "--target", wheels]
    subprocess.check_call(pip + ["--platform", "win_amd64", "--python-version", "3.12",
                                 "--implementation", "cp", "--only-binary=:all:", *WIN_WHEELS])
    subprocess.check_call(pip + PURE_SDISTS)
    return embed, wheels

PTH = """python312.zip
.
Lib\\site-packages
..\\app
import site
"""

BOOTSTRAP = r'''# WhatsApp Arşivi başlatıcısı: .exe'nin bulunduğu klasörü bulup programı çalıştırır.
import os
import sys

root = os.path.dirname(os.path.abspath(sys.argv[0]))
os.environ["WA_ARSIV_KOK"] = root
app_dir = os.path.join(root, "app")
if app_dir not in sys.path:
    sys.path.insert(0, app_dir)
sys.argv[0] = os.path.join(app_dir, "main.py")

import runpy  # noqa: E402

runpy.run_path(sys.argv[0], run_name="__main__")
'''

README_TXT = """WhatsApp Arşivi
================

ÇALIŞTIRMA
  "WhatsApp Arsivi.exe" dosyasına çift tıklayın. Kurulum gerekmez.

  İlk açılışta Windows "Windows bilgisayarınızı korudu" uyarısı gösterebilir
  (program imzalı olmadığı için). "Ek bilgi" -> "Yine de çalıştır" deyin.

  Bu klasörü zip'ten çıkardıktan sonra kullanın (zip'in içinden çalışmaz).
  Klasörü istediğiniz yere (ör. Belgeler veya harici disk) taşıyabilirsiniz.

İPHONE YEDEĞİ
  1. iPhone'da WhatsApp -> Ayarlar -> Sohbetler -> Sohbet Yedeği:
     "Uçtan Uca Şifreli Yedekleme" açıksa kapatın.
  2. iPhone'u kabloyla bu bilgisayara bağlayın, telefonda "Bu Bilgisayara Güven" deyin.
  3. "Apple Aygıtları" (Microsoft Store) veya iTunes'ta iPhone'u seçip
     "Tüm verileri bu bilgisayara yedekle" -> "Şimdi Yedekle".
  4. Programda sağ üstteki menüden (⋮) "iPhone yedeğinden içe aktar".

VERİLER
  İçe aktarılan sohbetler bu klasördeki "Arsiv_Verisi" klasöründe saklanır.
  Hiçbir veri internete gönderilmez.

  Program iPhone yedeğiyle aynı disktaysa (genelde C:), fotoğraf/video gibi
  medyalar kopyalanmaz, yedekteki dosyalara "sabit bağlantı" ile bağlanır:
  içe aktarma çok hızlı olur ve ek disk alanı kullanılmaz. iPhone yedeği
  sonradan güncellense veya silinse bile arşivdeki medyalar yerinde kalır.
  (Windows Gezgini "Arsiv_Verisi" klasörünün boyutunu yine de tam gösterir;
  bu dosyalar diskte yalnızca bir kez yer kaplar.)

SORUN OLURSA
  - .exe açılmazsa "Alternatif Baslatici.bat" dosyasına çift tıklayın.
  - "Arsiv_Verisi\\program.log" dosyasında hata ayrıntıları bulunur.
"""

FALLBACK_BAT = """@echo off
rem WhatsApp Arsivi - alternatif baslatici (exe acilmazsa)
set "WA_ARSIV_KOK=%~dp0"
start "" "%~dp0runtime\\pythonw.exe" "%~dp0app\\main.py"
"""


def make_icon(path):
    from PIL import Image, ImageDraw

    size = 512
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse([8, 8, size - 8, size - 8], fill=(0, 168, 132, 255))
    # konuşma balonu
    d.rounded_rectangle([110, 120, 402, 340], radius=48, fill=(255, 255, 255, 255))
    d.polygon([(170, 330), (150, 410), (240, 336)], fill=(255, 255, 255, 255))
    # mesaj satırları
    for i, w in enumerate((220, 180, 120)):
        y = 170 + i * 52
        d.rounded_rectangle([146, y, 146 + w, y + 22], radius=11, fill=(0, 128, 105, 255))
    img.save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def distlib_launcher():
    from pip._vendor import distlib
    path = os.path.join(os.path.dirname(distlib.__file__), "w64.exe")
    with open(path, "rb") as f:
        return f.read()


def build(embed_zip, wheels_dir, out_dir):
    work = os.path.join(out_dir, PKG_NAME)
    if os.path.exists(work):
        shutil.rmtree(work)
    runtime = os.path.join(work, "runtime")
    os.makedirs(runtime)

    # 1) Taşınabilir Python
    with zipfile.ZipFile(embed_zip) as z:
        z.extractall(runtime)
    pth = [n for n in os.listdir(runtime) if n.endswith("._pth")]
    if len(pth) != 1 or pth[0] != "python312._pth":
        sys.exit(f"Beklenmeyen ._pth dosyası: {pth}")
    with open(os.path.join(runtime, pth[0]), "w", encoding="ascii", newline="\r\n") as f:
        f.write(PTH)

    # 2) Kütüphaneler
    site = os.path.join(runtime, "Lib", "site-packages")
    shutil.copytree(wheels_dir, site, ignore=shutil.ignore_patterns(
        "__pycache__", "*.pdb", "*.jar", "bin"))

    # 3) Program
    app = os.path.join(work, "app")
    os.makedirs(app)
    shutil.copy2(os.path.join(ROOT, "main.py"), app)
    shutil.copytree(os.path.join(ROOT, "wa_arsiv"), os.path.join(app, "wa_arsiv"),
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))

    # 4) Başlatıcı .exe = pip'in Windows başlatıcısı + #!satırı + açılış betiği (zip)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("__main__.py", BOOTSTRAP.encode("utf-8"))
    shebang = b"#!<launcher_dir>\\runtime\\pythonw.exe\n"
    with open(os.path.join(work, PKG_NAME + ".exe"), "wb") as f:
        f.write(distlib_launcher() + shebang + buf.getvalue())

    # 5) Okuma dosyası (Not Defteri Türkçe karakterleri doğru göstersin diye UTF-8 BOM)
    with open(os.path.join(work, "OKU BENI.txt"), "w", encoding="utf-8-sig", newline="\r\n") as f:
        f.write(README_TXT)
    with open(os.path.join(work, "Alternatif Baslatici.bat"), "w", encoding="ascii", newline="\r\n") as f:
        f.write(FALLBACK_BAT)

    # 6) Zip
    zip_path = os.path.join(out_dir, "WhatsApp-Arsivi-Windows.zip")
    if os.path.exists(zip_path):
        os.remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for base, _dirs, files in os.walk(work):
            for name in sorted(files):
                full = os.path.join(base, name)
                arc = os.path.relpath(full, out_dir).replace(os.sep, "/")
                z.write(full, arc)
    shutil.rmtree(work)  # ara klasör; teslim edilen şey zip
    return zip_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true", help="taşınabilir Python'u ve kütüphaneleri indir")
    ap.add_argument("--cache", default=os.path.join(ROOT, "build", "indirilenler"))
    ap.add_argument("--embed")
    ap.add_argument("--wheels")
    ap.add_argument("--out", default=os.path.join(ROOT, "dist"))
    a = ap.parse_args()
    if a.download:
        a.embed, a.wheels = download_inputs(a.cache)
    elif not (a.embed and a.wheels):
        ap.error("--download ya da --embed ile --wheels birlikte verilmeli")
    icon = os.path.join(ROOT, "wa_arsiv", "web", "icon.ico")
    if not os.path.isfile(icon):
        make_icon(icon)
    os.makedirs(a.out, exist_ok=True)
    zip_path = build(a.embed, a.wheels, a.out)
    size = os.path.getsize(zip_path) / 1024 / 1024
    print(f"Zip: {zip_path} ({size:.1f} MB)")


if __name__ == "__main__":
    main()
