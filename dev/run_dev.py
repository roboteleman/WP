"""GELİŞTİRME: Sunucuyu pencere açmadan başlatır ve adresi yazdırır.

    WA_ARSIV_VERI=<veri klasörü> WA_ARSIV_YEDEK_KOK=<yedek kökü> python dev/run_dev.py [port]
"""
import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from wa_arsiv import importer, paths, server  # noqa: E402

data_root = paths.data_dir()
importer.cleanup_partials(data_root)
app = server.AppState(data_root)
srv, url = server.start(app, port=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
print(url, flush=True)
threading.Event().wait()
