# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['app.py'],
    pathex=[],
    binaries=[],
    # -----подсказка------------
    # Явно указываем PyInstaller'у забрать шаблоны, статику и бинарники FFmpeg
    datas=[
        ('templates', 'templates'),
        ('static', 'static'),
        ('bin', 'bin'),
    ],
    # -----подсказка------------
    # Скрытые импорты для Flask-SocketIO и APScheduler,
    # которые PyInstaller не видит при статическом анализе.
    hiddenimports=[
        'engineio.async_drivers.threading',
        'apscheduler.triggers.interval',
        'apscheduler.executors.pool',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='IPTV_Overlay',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,      # True - будет черное окно консоли с логами (рекомендую), False - скрыть
    icon='static/favicon.ico' # Если есть иконка
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='IPTV_Overlay',
)