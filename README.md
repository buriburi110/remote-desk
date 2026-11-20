# Remote Desk

Windows PC를 브라우저에서 원격 조작하는 작은 WebRTC 원격 데스크톱입니다.
PC에는 호스트 프로그램을 설치하고, 맥북(또는 아무 기기)에서는 브라우저로 접속합니다.

- 화면은 H.264 영상으로, 입력은 데이터 채널로 **P2P 전송**됩니다. 시그널링 서버는 연결을 맺어 줄 뿐 화면을 보지 못합니다.
- 클라이언트는 설치가 필요 없습니다.
- 맥 키보드에 맞춘 키 매핑: `Cmd` → `Ctrl`, 오른쪽 `Option` → 한/영

## 구조

```
브라우저(client.html) ──offer/answer──> 시그널링 서버(signal_server.py) <──WebSocket── 호스트(host.py)
        └────────────── WebRTC P2P: 영상 + 마우스·키보드 ──────────────┘
```

| 파일 | 역할 | 실행 위치 |
|---|---|---|
| `host.py` | 화면 캡처·인코딩, 입력 실행, 비밀번호 확인 | Windows PC |
| `signal_server.py` | 클라이언트 페이지 서빙, offer/answer 중계 | 공인 주소가 있는 서버 |
| `client.html` | 영상 표시, 입력 전송 | 브라우저 |

## 같은 PC에서 먼저 실행해 보기

```
pip install -r requirements.txt
python signal_server.py
python host.py
```

브라우저에서 `http://localhost:8765`를 열고, `host.py`가 처음 실행될 때 만든 `password.txt`의 비밀번호를 입력합니다.

## 외부에서 접속하기

1. 시그널링 서버를 배포합니다. 이 저장소에는 [Render](https://render.com)용 `render.yaml`이 들어 있습니다 (New → Blueprint).
2. 배포 후 Render의 Environment 탭에서 `HOST_KEY` 값을 확인합니다.
3. PC의 `config.json`에 서버 주소와 키를 넣습니다.

```json
{
  "signal": "wss://<배포된 주소>/host",
  "key": "<HOST_KEY 값>"
}
```

4. 호스트를 실행하고, 밖에서 `https://<배포된 주소>`로 접속합니다.

## Windows 설치용 exe

`build.bat`을 실행하면 `dist\RemoteDeskHost.exe`가 만들어집니다.
더블클릭하면 `%LOCALAPPDATA%\RemoteDesk`에 설치되고 로그인 시 자동 실행됩니다.
해제는 `RemoteDeskHost.exe --uninstall`입니다.

## 보안

- 접속 비밀번호는 호스트가 직접 확인하며, 인증 전에는 화면을 보내지 않습니다.
- `HOST_KEY`는 아무나 호스트로 등록하지 못하게 막는 키입니다.
- `password.txt`와 `config.json`은 저장소에 올리지 마세요 (`.gitignore`에 포함).

## 한계

- TURN 중계 서버가 없어서 통신사 공유 IP(CGNAT)나 일부 회사망에서는 연결되지 않을 수 있습니다.
- Windows 로그인 화면과 관리자 권한 확인 창(UAC)은 조작할 수 없습니다.
- 주 모니터만 전송하며, 소리·파일 전송·클립보드 공유는 없습니다.
- 인코딩이 소프트웨어(x264) 방식이라 상용 프로그램보다 지연이 큽니다.
