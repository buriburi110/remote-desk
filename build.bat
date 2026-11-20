@echo off
rem Windows 호스트 설치용 exe를 dist\RemoteDeskHost.exe로 만든다
rem 깨끗한 가상환경에서 빌드해야 exe가 작다 (Anaconda에서 바로 빌드하면 250MB 이상)
python -m venv .build-venv
.build-venv\Scripts\python -m pip install --quiet -r requirements.txt pyinstaller
.build-venv\Scripts\python -m PyInstaller --onefile --noconsole --noconfirm --name RemoteDeskHost host.py
if exist config.json copy /y config.json dist\config.json >nul
