@echo off
rem Windows 호스트 설치용 exe를 dist\RemoteDeskHost.exe로 만든다
python -m pip install --quiet pyinstaller
python -m PyInstaller --onefile --noconsole --noconfirm --name RemoteDeskHost host.py
