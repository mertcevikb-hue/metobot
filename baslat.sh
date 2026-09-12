#!/usr/bin/env bash
# Metobot AI Terminal - Linux & macOS Launcher with Auto-Dependency Installer

set -e

echo "==================================================================="
echo "              METOBOT AI QUANTITATIVE TERMINAL"
echo "==================================================================="
echo ""

# 1. Python kontrolü
echo "[1/3] Python ortamı kontrol ediliyor..."
PY_CMD=""
if command -v python3 &>/dev/null; then
    PY_CMD="python3"
    echo "[OK] python3 bulundu."
elif command -v python &>/dev/null; then
    PY_CMD="python"
    echo "[OK] python bulundu."
else
    echo "[HATA] Sisteminizde Python bulunamadı!"
    echo "Lütfen Python 3 kurun (Örn: sudo apt update && sudo apt install python3 python3-pip)"
    exit 1
fi

# 2. Kütüphanelerin kontrolü
echo ""
echo "[2/3] Gerekli kütüphaneler kontrol ediliyor..."
if ! $PY_CMD -c "import fastapi, uvicorn, yfinance, polygon, google.generativeai, sqlalchemy, aiosqlite, dotenv, loguru, pydantic" &>/dev/null; then
    echo "-------------------------------------------------------------------"
    echo "[DİKKAT] Bazı kütüphaneler eksik! Otomatik kurulum başlatılıyor..."
    echo "-------------------------------------------------------------------"
    $PY_CMD -m pip install -r requirements.txt
    echo "[OK] Tüm kütüphaneler başarıyla kuruldu!"
else
    echo "[OK] Tüm kütüphaneler eksiksiz ve güncel."
fi

# 3. Projenin Başlatılması
echo ""
echo "[3/3] Metobot AI ve Canlı Motor başlatılıyor..."
echo "🌐 CANLI PANEL LİNKİNİZ: http://45.151.83.13:8000"
echo ""
$PY_CMD run.py
