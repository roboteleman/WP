"""Windows paketinin kendi Python'uyla çalıştırılan hızlı deneme (GitHub Actions'ta kullanılır).

    "<paket>\\runtime\\python.exe" dev\\smoke_package.py "<paket>" "<sahte yedek klasörü>"

Denenenler: .NET köprüsü (pythonnet) ve WebView2 kütüphaneleri yüklenebiliyor mu; sahte yedek içe
aktarılabiliyor mu; medyalar sabit bağlantıyla alınabiliyor mu.
"""
import os
import sys
import time

pkg, backup = sys.argv[1], sys.argv[2]
sys.path.insert(0, os.path.join(pkg, "app"))

import clr  # noqa: E402  (pythonnet + .NET Framework)
import webview  # noqa: E402

clr.AddReference("System.Windows.Forms")
lib = os.path.join(os.path.dirname(webview.__file__), "lib")
clr.AddReference(os.path.join(lib, "Microsoft.Web.WebView2.Core.dll"))
clr.AddReference(os.path.join(lib, "Microsoft.Web.WebView2.WinForms.dll"))
print("pythonnet ve WebView2 kütüphaneleri yüklendi")

from wa_arsiv import archive, importer  # noqa: E402

data = os.path.join(os.path.dirname(os.path.abspath(backup)), "arsiv_verisi")  # yedekle aynı disk
os.makedirs(data, exist_ok=True)
job = importer.ImportJob(data, backup)
job.start()
while job.snapshot()["state"] == "running":
    time.sleep(0.1)
st = job.snapshot()
assert st["state"] == "done", st
meta = st["archives"][0]
assert meta["linked_files"] > 0, "medyalar sabit bağlantıyla alınamadı"
arc = archive.Archive(os.path.join(data, meta["id"]))
chats = arc.chats()
assert len(chats) == 36, len(chats)
assert arc.search("sukru"), "Türkçe arama çalışmadı"
print(f"Tamam: {meta['message_count']} mesaj, {len(chats)} sohbet, {meta['linked_files']} medya bağlandı")
