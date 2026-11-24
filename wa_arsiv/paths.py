"""Programın dosya konumları."""
import os
import sys

APP_NAME = "WhatsApp Arşivi"


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def web_dir() -> str:
    """Arayüz dosyalarının (html/css/js) klasörü."""
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


def long_path(path: str) -> str:
    """Windows'ta 260 karakteri aşan yollar için \\\\?\\ önekini ekler (diğer sistemlerde aynen döner)."""
    if os.name != "nt" or not path:
        return path
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\") or len(path) < 240:
        return path
    if path.startswith("\\\\"):  # ağ paylaşımı
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _writable(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".yazma_testi")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
        os.remove(probe)
        return True
    except OSError:
        return False


def data_dir() -> str:
    """Arşivlerin saklandığı klasör.

    Varsayılan olarak programın (.exe) yanındaki "Arsiv_Verisi" klasörüdür; böylece
    program ve arşiv birlikte başka bir diske kopyalanabilir. Oraya yazılamıyorsa
    (ör. Program Files) kullanıcının AppData klasörüne düşer.
    """
    env = os.environ.get("WA_ARSIV_VERI")
    if env:
        os.makedirs(env, exist_ok=True)
        return env
    portable_root = os.environ.get("WA_ARSIV_KOK")  # taşınabilir paket: .exe'nin klasörü
    if portable_root and os.path.isdir(portable_root):
        base = portable_root
    elif is_frozen():
        base = os.path.dirname(sys.executable)
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    candidate = os.path.join(base, "Arsiv_Verisi")
    if _writable(candidate):
        return candidate
    fallback_base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    fallback = os.path.join(fallback_base, "WhatsAppArsivi")
    os.makedirs(fallback, exist_ok=True)
    return fallback
