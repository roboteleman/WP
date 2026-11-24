"""WhatsApp Arşivi — iPhone yedeğinden WhatsApp sohbetlerini görüntüleyen program.

Çalıştırma:
    python main.py              -> program penceresi
    python main.py --tarayici   -> pencere yerine varsayılan tarayıcıda açar
    python main.py --sunucu     -> yalnızca sunucuyu başlatır ve adresi yazar (hata ayıklama)
"""
import os
import sys
import threading
import time
import traceback
import webbrowser

APP_TITLE = "WhatsApp Arşivi"


def _fix_std_streams(data_root):
    """Konsolsuz çalışmada stdout/stderr yoktur; hataları bir günlük dosyasına yaz."""
    # Çıktı bir dosyaya/boruya yönlendirildiğinde Windows cp1252 kullanır ve "ğ", "ı" ya da dosya
    # adlarındaki emojiler UnicodeEncodeError verir (içe aktarmayı bile durdurabilir). UTF-8'e çevir.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="backslashreplace")
        except (AttributeError, ValueError, OSError):
            pass
    if sys.stdout is not None and sys.stderr is not None:
        return None
    log_path = os.path.join(data_root, "program.log")
    try:
        if os.path.isfile(log_path) and os.path.getsize(log_path) > 5 * 1024 * 1024:
            os.remove(log_path)
        f = open(log_path, "a", encoding="utf-8", buffering=1)
    except OSError:
        f = open(os.devnull, "w", encoding="utf-8")
        log_path = None
    if sys.stdout is None:
        sys.stdout = f
    if sys.stderr is None:
        sys.stderr = f
    print(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} program başladı ---", flush=True)
    return log_path


def _message_box(text, error=True):
    """Windows'ta basit bir uyarı penceresi gösterir (konsol yokken hatayı görünür kılar)."""
    if os.name != "nt":
        print(text, file=sys.stderr)
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, text, APP_TITLE, 0x10 if error else 0x40)
    except Exception:
        pass


def _windows_prepare(root):
    """Windows'a özgü hazırlıklar.

    - İnternetten indirilen zip'ten çıkan dosyalar "internetten geldi" işareti (Zone.Identifier)
      taşır; .NET bu işaretli DLL'leri yüklemeyi reddeder. Program klasöründeki işaretleri kaldırır.
    - Görev çubuğunda Python simgesi yerine programın simgesi görünsün diye uygulama kimliği atar.
    """
    if os.name != "nt" or not root:
        return
    marker = os.path.join(root, "runtime", ".engel_kaldirildi")
    if not os.path.exists(marker):
        for base, _dirs, files in os.walk(root):
            if "Arsiv_Verisi" in base:
                continue
            for name in files:
                try:
                    os.remove(os.path.join(base, name) + ":Zone.Identifier")
                except OSError:
                    pass
        try:
            with open(marker, "w", encoding="utf-8") as f:
                f.write("ok")
        except OSError:
            pass
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("WhatsAppArsivi.Goruntuleyici")
    except Exception:
        pass


class JsApi:
    """Arayüzden (JavaScript) çağrılabilen pencere işlevleri."""

    def __init__(self):
        self._window = None

    def pick_folder(self):
        import webview
        try:
            kind = webview.FileDialog.FOLDER
        except AttributeError:
            kind = webview.FOLDER_DIALOG
        result = self._window.create_file_dialog(kind)
        if not result:
            return None
        return result[0] if isinstance(result, (list, tuple)) else result


def _browser_mode(srv, app, url, open_browser=True):
    """Pencere açılamazsa: tarayıcıda aç; sayfa kapandıktan bir süre sonra kendiliğinden kapan."""
    if open_browser:
        webbrowser.open(url)
    app.watchdog = True
    try:
        while True:
            time.sleep(5)
            idle = time.time() - app.last_seen
            job_running = app.job is not None and app.job.snapshot().get("state") == "running"
            if app.seen_once and idle > 180 and not job_running:
                break
    except KeyboardInterrupt:
        pass
    srv.shutdown()


def main():
    from wa_arsiv import paths, server, importer

    root = os.environ.get("WA_ARSIV_KOK")  # taşınabilir pakette başlatıcı tarafından atanır
    data_root = paths.data_dir()
    log_path = _fix_std_streams(data_root)
    try:
        _windows_prepare(root)
        importer.cleanup_partials(data_root)

        app = server.AppState(data_root)
        srv, url = server.start(app, port=int(os.environ.get("WA_ARSIV_PORT", "0")))

        if "--sunucu" in sys.argv:
            print(url, flush=True)
            _browser_mode(srv, app, url, open_browser=False)
            return
        if "--tarayici" in sys.argv or "--browser" in sys.argv:
            print(f"{APP_TITLE} çalışıyor: {url}", flush=True)
            _browser_mode(srv, app, url)
            return

        try:
            import webview
        except Exception:
            traceback.print_exc()
            _browser_mode(srv, app, url)
            return

        api = JsApi()
        window = webview.create_window(
            APP_TITLE, url, js_api=api,
            width=1280, height=820, min_size=(900, 560), text_select=True,
        )
        api._window = window
        icon = os.path.join(paths.web_dir(), "icon.ico")
        kwargs = {"gui": "edgechromium"} if os.name == "nt" else {}
        if os.path.isfile(icon):
            kwargs["icon"] = icon
        try:
            # Windows'ta Edge WebView2 kullanılır; açılamazsa varsayılan tarayıcıya düşülür.
            webview.start(**kwargs)
        except Exception:
            traceback.print_exc()
            _browser_mode(srv, app, url)
            return
        srv.shutdown()
    except Exception:
        traceback.print_exc()
        where = f"\n\nAyrıntılar: {log_path}" if log_path else ""
        _message_box(f"Program başlatılırken bir hata oluştu:\n\n{sys.exc_info()[1]}{where}")
        raise


if __name__ == "__main__":
    main()
