@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo テストモードで実行中（2件のみ処理）...
python main.py --test --no-headless
pause
