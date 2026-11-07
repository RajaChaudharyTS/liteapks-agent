@echo off
title File Sharing & App Wishlist Telegram Bot (@FileShareAppWishBot)
cd /d "%~dp0"

:loop
echo ==============================================================
echo [%date% %time%] Launching File Sharing & App Wishlist Bot (@FileShareAppWishBot)...
echo ==============================================================
python on_demand_bot.py
echo [WARNING] Bot process exited with code %ERRORLEVEL%. Auto-restarting in 5 seconds...
timeout /t 5 /nobreak >nul
goto loop
