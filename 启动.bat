@echo off
chcp 65001 >nul
rem 鍥剧墖宸ュ叿绠憋紙libvips锛夊惎鍔ㄥ櫒锛氫娇鐢?pythonw 鍚姩锛屼笉鏄剧ず鎺у埗鍙扮獥鍙?cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" "main.py"
) else (
    echo 鏈壘鍒?.venv锛屽皾璇曚娇鐢ㄧ郴缁?Python...
    start "" pythonw "main.py"
)
