@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo ========================================
echo カメラ転売リサーチボット 起動中...
echo ========================================
python main.py --schedule
pause
