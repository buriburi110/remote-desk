"""호스트 (Windows PC에서 실행): 화면을 H.264로 보내고, 받은 마우스·키보드 입력을 실행한다.

실행: python host.py [시그널링 서버 주소]
  예) python host.py ws://localhost:8765/host
시그널링 서버 주소와 키는 config.json에서 읽는다 (인자와 환경변수 HOST_KEY가 우선).
접속 비밀번호는 첫 실행 때 password.txt에 생성된다.
exe로 빌드(build.bat)하면 더블클릭으로 설치되고, RemoteDeskHost.exe --uninstall로 해제한다.
"""
import asyncio
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import sys
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes
from pathlib import Path

import aiohttp
import av
import mss
import numpy as np
from aiortc import (RTCConfiguration, RTCIceServer, RTCPeerConnection,
                    RTCRtpSender, RTCSessionDescription, VideoStreamTrack)
from aiortc.codecs import h264

FROZEN = getattr(sys, "frozen", False)  # PyInstaller로 만든 exe인지
# 설정·비밀번호 위치: exe는 %LOCALAPPDATA%\RemoteDesk, 스크립트는 같은 폴더
BASE = Path(os.environ["LOCALAPPDATA"]) / "RemoteDesk" if FROZEN else Path(__file__).parent


def install():
    """exe를 더블클릭하면 설치한다: 설치 폴더로 복사 + 로그인 시 자동 실행 등록 + 실행."""
    import shutil
    import subprocess
    import winreg
    run_key = r"Software\Microsoft\Windows\CurrentVersion\Run"
    target = BASE / "RemoteDeskHost.exe"
    box = lambda text: ctypes.windll.user32.MessageBoxW(0, text, "Remote Desk", 0)
    if "--uninstall" in sys.argv:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
            try:
                winreg.DeleteValue(k, "RemoteDesk")
            except FileNotFoundError:
                pass
        box(f"자동 실행을 해제했습니다.\n작업 관리자에서 RemoteDeskHost를 끝낸 뒤 이 폴더를 지우면 됩니다:\n{BASE}")
        sys.exit()
    if Path(sys.executable).resolve() != target.resolve():
        BASE.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sys.executable, target)
        bundled = Path(sys.executable).with_name("config.json")  # exe 옆에 둔 설정을 함께 설치
        if bundled.exists():
            shutil.copy2(bundled, BASE / "config.json")
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, run_key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "RemoteDesk", 0, winreg.REG_SZ, f'"{target}"')
        subprocess.Popen([str(target)])
        box(f"설치했습니다. PC를 켜면 자동으로 실행됩니다.\n접속 비밀번호와 설정 파일은 이 폴더에 있습니다:\n{BASE}")
        sys.exit()


if FROZEN:
    install()

CONFIG_FILE = BASE / "config.json"
if not CONFIG_FILE.exists():
    CONFIG_FILE.write_text(json.dumps({"signal": "ws://localhost:8765/host", "key": "dev"}, indent=2))
config = json.loads(CONFIG_FILE.read_text())
SIGNAL_URL = sys.argv[1] if len(sys.argv) > 1 else config["signal"]
HOST_KEY = os.environ.get("HOST_KEY", config["key"])
MAX_WIDTH = 1920
STUN = "stun:stun.l.google.com:19302"

# aiortc 기본 비트레이트(1~3Mbps)는 데스크톱 글자가 뭉개져서 올린다
h264.DEFAULT_BITRATE = 4_000_000
h264.MAX_BITRATE = 8_000_000

PW_FILE = BASE / "password.txt"
if not PW_FILE.exists():
    PW_FILE.write_text(secrets.token_urlsafe(9))
PASSWORD = PW_FILE.read_text().strip()

# 캡처 좌표와 입력 좌표가 실제 픽셀과 일치하도록
ctypes.windll.shcore.SetProcessDpiAwareness(2)
user32 = ctypes.windll.user32

# ---------------------------------------------------------------- 입력

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class _U(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


def _send(inp):
    user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))


M_MOVE, M_ABS, M_WHEEL, M_HWHEEL = 0x0001, 0x8000, 0x0800, 0x1000
# 브라우저 버튼 번호 -> (누름, 뗌) 플래그
BUTTONS = {0: (0x0002, 0x0004), 1: (0x0020, 0x0040), 2: (0x0008, 0x0010)}


def mouse(flags, x=0.0, y=0.0, data=0):
    inp = INPUT(type=0)
    inp.u.mi = MOUSEINPUT(int(x * 65535), int(y * 65535), data & 0xFFFFFFFF, flags, 0, 0)
    _send(inp)


# KeyboardEvent.code -> Windows 가상 키
VK = {
    "Enter": 0x0D, "Escape": 0x1B, "Backspace": 0x08, "Tab": 0x09, "Space": 0x20,
    "Minus": 0xBD, "Equal": 0xBB, "BracketLeft": 0xDB, "BracketRight": 0xDD, "Backslash": 0xDC,
    "Semicolon": 0xBA, "Quote": 0xDE, "Backquote": 0xC0, "Comma": 0xBC, "Period": 0xBE, "Slash": 0xBF,
    "ArrowLeft": 0x25, "ArrowUp": 0x26, "ArrowRight": 0x27, "ArrowDown": 0x28,
    "Delete": 0x2E, "Home": 0x24, "End": 0x23, "PageUp": 0x21, "PageDown": 0x22, "Insert": 0x2D,
    "ShiftLeft": 0xA0, "ShiftRight": 0xA1, "ControlLeft": 0xA2, "ControlRight": 0xA3, "AltLeft": 0xA4,
    "AltRight": 0x15,                      # 맥 오른쪽 Option -> 한/영
    "MetaLeft": 0xA2, "MetaRight": 0xA2,   # 맥 Cmd -> Ctrl (Cmd+C가 Ctrl+C로)
}
VK.update({f"Key{chr(c)}": c for c in range(0x41, 0x5B)})
VK.update({f"Digit{d}": 0x30 + d for d in range(10)})
VK.update({f"F{n}": 0x6F + n for n in range(1, 13)})
EXTENDED = {0x25, 0x26, 0x27, 0x28, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x2D, 0xA3}


def key(vk, down):
    flags = (0 if down else 0x0002) | (0x0001 if vk in EXTENDED else 0)
    inp = INPUT(type=1)
    inp.u.ki = KEYBDINPUT(vk, user32.MapVirtualKeyW(vk, 0), flags, 0, 0)
    _send(inp)


def handle_input(d, pressed):
    t = d["t"]
    if t == "m":
        mouse(M_MOVE | M_ABS, d["x"], d["y"])
    elif t == "b":
        mouse(M_MOVE | M_ABS | BUTTONS[d["b"]][0 if d["down"] else 1], d["x"], d["y"])
    elif t == "w":
        if d["dy"]:
            mouse(M_WHEEL, data=-int(d["dy"]))
        if d["dx"]:
            mouse(M_HWHEEL, data=int(d["dx"]))
    elif t == "k":
        vk = VK.get(d["c"])
        if vk:
            (pressed.add if d["down"] else pressed.discard)(vk)
            key(vk, d["down"])


def release_all(pressed):
    for vk in pressed:
        key(vk, False)
    pressed.clear()


# ---------------------------------------------------------------- 화면

_pool = ThreadPoolExecutor(max_workers=1)  # mss는 만든 스레드에서만 써야 한다
_sct = None
_BLACK = av.VideoFrame.from_ndarray(np.zeros((360, 640, 3), np.uint8), format="bgr24").reformat(format="yuv420p")


def grab():
    global _sct
    if _sct is None:
        _sct = mss.mss()
    shot = _sct.grab(_sct.monitors[1])  # 주 모니터
    scale = min(1.0, MAX_WIDTH / shot.width)
    w, h = int(shot.width * scale) // 2 * 2, int(shot.height * scale) // 2 * 2
    return av.VideoFrame.from_ndarray(np.asarray(shot), format="bgra").reformat(w, h, "yuv420p")


class ScreenTrack(VideoStreamTrack):
    def __init__(self, state):
        super().__init__()
        self.state = state

    async def recv(self):
        pts, time_base = await self.next_timestamp()  # 30fps로 맞춰준다
        if self.state["authed"]:
            frame = await asyncio.get_running_loop().run_in_executor(_pool, grab)
        else:
            frame = _BLACK.reformat()  # 인증 전에는 화면을 보내지 않는다
        frame.pts, frame.time_base = pts, time_base
        return frame


# ---------------------------------------------------------------- 연결

current = None  # 세션은 하나만. 새 접속이 오면 이전 것을 끊는다
http = None     # main()에서 만드는 aiohttp 세션
ICE_URL = SIGNAL_URL.replace("ws", "http", 1).rsplit("/", 1)[0] + "/ice"
# 클라이언트가 시그널링 서버에서 TURN 자격증명을 받을 때 내는 값 (비밀번호를 아는 쪽만 계산 가능)
ICE_TOKEN = hashlib.sha256(("ice:" + PASSWORD).encode()).hexdigest()


async def ice_servers():
    """시그널링 서버가 주는 STUN/TURN 목록. 실패하면 STUN만 쓴다."""
    try:
        async with http.get(ICE_URL, headers={"X-Host-Key": HOST_KEY}) as r:
            servers = await r.json()
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError):
        servers = [{"urls": [STUN]}]
    out = []
    for s in servers:
        # aiortc가 안정적으로 쓰는 것만: STUN과 UDP TURN
        urls = [u for u in s["urls"] if u.startswith("stun:") or (u.startswith("turn:") and "transport=tcp" not in u)]
        if urls:
            out.append(RTCIceServer(urls, s.get("username"), s.get("credential")))
    return out


async def handle_offer(msg):
    global current
    if current:
        await current.close()
    pc = current = RTCPeerConnection(RTCConfiguration(await ice_servers()))
    state = {"authed": False}
    pressed = set()

    sender = pc.addTrack(ScreenTrack(state))
    transceiver = next(t for t in pc.getTransceivers() if t.sender == sender)
    transceiver.setCodecPreferences(
        [c for c in RTCRtpSender.getCapabilities("video").codecs if c.mimeType == "video/H264"])

    @pc.on("datachannel")
    def on_channel(channel):
        @channel.on("message")
        def on_message(raw):
            d = json.loads(raw)
            if state["authed"]:
                handle_input(d, pressed)
            elif d.get("t") == "auth" and hmac.compare_digest(str(d.get("p", "")).encode(), PASSWORD.encode()):
                state["authed"] = True
                channel.send("ok")
                print("클라이언트 인증됨")
            else:
                channel.send("no")
                asyncio.get_running_loop().call_later(1, lambda: asyncio.ensure_future(pc.close()))

    @pc.on("connectionstatechange")
    async def on_state():
        print(f"연결 상태: {pc.connectionState} (ICE: {pc.iceConnectionState})")
        if pc.connectionState == "connected":
            try:  # 진단용: 실제로 선택된 경로 (aioice 내부 값이라 없으면 넘어간다)
                pair = pc.sctp.transport.transport._connection._nominated[1]
                print(f"선택된 경로: {pair.local_candidate.type} -> {pair.remote_candidate.type}")
            except Exception:
                pass
        if pc.connectionState in ("failed", "closed"):
            release_all(pressed)
            await pc.close()

    await pc.setRemoteDescription(RTCSessionDescription(msg["sdp"], msg["type"]))
    await pc.setLocalDescription(await pc.createAnswer())
    # 연결 실패 원인 파악용: 양쪽 ICE 후보의 (종류, 주소, 프로토콜)
    candidates = lambda sdp: [(p[7], p[4], p[2]) for p in (l.split() for l in sdp.splitlines() if l.startswith("a=candidate:"))]
    print(f"클라이언트 후보: {candidates(msg['sdp'])}")
    print(f"호스트 후보: {candidates(pc.localDescription.sdp)}")
    return {"id": msg["id"], "sdp": pc.localDescription.sdp, "type": pc.localDescription.type}


async def keepalive(ws):
    # 무료 호스팅은 WebSocket 메시지가 15분간 없으면 서버를 재우므로 주기적으로 빈 메시지를 보낸다
    while True:
        await asyncio.sleep(600)
        await ws.send_json({})


async def main():
    global http
    print(f"접속 비밀번호: {PW_FILE} 파일 참고")
    async with aiohttp.ClientSession() as http:
        while True:
            try:
                async with http.ws_connect(SIGNAL_URL, headers={"X-Host-Key": HOST_KEY}, heartbeat=30) as ws:
                    print(f"시그널링 서버 연결됨: {SIGNAL_URL}")
                    await ws.send_json({"ice_token": ICE_TOKEN})
                    ping = asyncio.create_task(keepalive(ws))
                    try:
                        async for m in ws:
                            if m.type == aiohttp.WSMsgType.TEXT:
                                try:
                                    await ws.send_json(await handle_offer(json.loads(m.data)))
                                except Exception as e:  # 잘못된 offer 하나로 호스트가 죽지 않게
                                    print(f"offer 처리 실패: {e}")
                    finally:
                        ping.cancel()
            except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as e:
                print(f"시그널링 서버 연결 실패: {e}")
            await asyncio.sleep(5)


if __name__ == "__main__":
    asyncio.run(main())
