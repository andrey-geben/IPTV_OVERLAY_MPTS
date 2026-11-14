# reset.ps1 — полный сброс проекта к чистому состоянию

Write-Host "1. Убиваю FFmpeg..." -ForegroundColor Yellow
taskkill /IM ffmpeg.exe /F 2>$null | Out-Null

Write-Host "2. Удаляю data/..." -ForegroundColor Yellow
Remove-Item -Path .\data -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "3. Удаляю __pycache__..." -ForegroundColor Yellow
Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "4. Создаю чистые папки..." -ForegroundColor Yellow
New-Item -Path .\data\logs -ItemType Directory -Force | Out-Null
New-Item -Path .\data\playlists -ItemType Directory -Force | Out-Null
New-Item -Path .\data\hls -ItemType Directory -Force | Out-Null

Write-Host ""
Write-Host "✓ Готово. Теперь: python app.py" -ForegroundColor Green