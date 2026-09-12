"""
Metobot Universal Master Launcher (Windows & Linux Compatible)

Bu script:
1. Web Dashboard & Quant API'yi (FastAPI / Uvicorn) başlatır (Port 8000).
2. Bot 2 Canlı Algoritmik Sinyal Motorunu (bot2_live.py) başlatır.
3. Otomatik olarak varsayılan web tarayıcınızda http://127.0.0.1:8000 açar.
4. Ctrl+C ile kapatıldığında tüm servisleri güvenle sonlandırır.
"""

import sys
import os
import subprocess
import time
import webbrowser
import signal
import warnings
warnings.filterwarnings("ignore", category=FutureWarning)

def get_python_cmd():
    return sys.executable

def ensure_dependencies():
    """Gerekli kütüphaneleri kontrol eder, eksik varsa requirements.txt üzerinden otomatik kurar."""
    required = [
        ("fastapi", "fastapi"),
        ("uvicorn", "uvicorn[standard]"),
        ("yfinance", "yfinance"),
        ("polygon", "polygon-api-client"),
        ("google.generativeai", "google-generativeai"),
        ("sqlalchemy", "sqlalchemy"),
        ("aiosqlite", "aiosqlite"),
        ("dotenv", "python-dotenv"),
        ("loguru", "loguru"),
        ("pydantic", "pydantic"),
    ]
    missing = []
    for mod, pkg in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        print("📦 Bazı gerekli kütüphaneler eksik tespit edildi!")
        print(f"⏳ Otomatik kurulum başlatılıyor ({len(missing)} kütüphane)...")
        req_file = os.path.join(os.path.dirname(__file__), "requirements.txt")
        if os.path.exists(req_file):
            subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", req_file])
        else:
            subprocess.check_call([sys.executable, "-m", "pip", "install"] + missing)
        print("✅ Tüm kütüphaneler başarıyla kuruldu!\n")
    else:
        print("✓ [KONTROL] Gerekli tüm kütüphaneler eksiksiz ve güncel.")

def main():
    ensure_dependencies()
    py_cmd = get_python_cmd()
    print("=" * 65)
    print("🚀 METOBOT AI QUANTITATIVE TERMINAL BAŞLATILIYOR...")
    print(f"🐍 Python Ortamı: {py_cmd}")
    print("=" * 65)

    processes = []

    try:
        # 1. Web Dashboard & API Sunucusunu Başlat (0.0.0.0 ile tüm dış dünyaya açık)
        host = os.getenv("API_HOST", "0.0.0.0")
        port = os.getenv("API_PORT", "8000")
        public_ip = os.getenv("SERVER_IP", "45.151.83.13")

        print(f"🌐 [1/2] Web Dashboard & API Sunucusu Başlatılıyor ({host}:{port})...")
        web_env = os.environ.copy()
        web_proc = subprocess.Popen(
            [py_cmd, "-m", "uvicorn", "web.api:app", "--host", host, "--port", port],
            env=web_env
        )
        processes.append(web_proc)

        # Sunucunun ayağa kalkması için kısa bekleme
        time.sleep(2)

        # 2. Bot 2 Canlı Algoritma ve Sinyal Motorunu Başlat
        print("📈 [2/2] Bot 2 Canlı Sinyal & Opsiyon Motoru Başlatılıyor...")
        bot_proc = subprocess.Popen(
            [py_cmd, "bot2_live.py"],
            env=web_env
        )
        processes.append(bot_proc)

        # 3. Web Tarayıcısını Aç (Sadece yerel masaüstü ortamında aç)
        if os.name == 'nt' or os.environ.get('DISPLAY'):
            try:
                webbrowser.open(f"http://127.0.0.1:{port}")
            except Exception:
                pass

        print("\n" + "=" * 65)
        print("✅ METOBOT TÜM BİLEŞENLERİYLE CANLI VE ÇALIŞIYOR!")
        print(f"📌 Yerel Erişim: http://127.0.0.1:{port}")
        print(f"🌍 CANLI GENEL ERİŞİM LİNKİ: http://{public_ip}:{port}")
        print("📌 Çıkmak ve sistemi kapatmak için: CTRL + C tuşlarına basın.")
        print("=" * 65 + "\n")

        running = True
        while running:
            time.sleep(1)
            for p in processes:
                if p.poll() is not None:
                    print(f"\n⚠️ Bir servis beklenmedik şekilde sonlandı (PID: {p.pid}).")
                    running = False
                    break

    except KeyboardInterrupt:
        print("\n🛑 Kapatma sinyali alındı. Servisler güvenle durduruluyor...")
    finally:
        for p in processes:
            if p.poll() is None:
                try:
                    if os.name == 'nt':
                        subprocess.run(["taskkill", "/F", "/T", "/PID", str(p.pid)], capture_output=True)
                    else:
                        p.terminate()
                        p.wait(timeout=2)
                except Exception:
                    try:
                        p.kill()
                    except Exception:
                        pass
        print("✓ Tüm Metobot servisleri durduruldu. Görüşmek üzere!")

if __name__ == "__main__":
    main()
