"""시그널링 서버: 클라이언트 페이지를 서빙하고, 브라우저의 offer를 호스트에 전달해 answer를 돌려준다.
영상·입력은 이 서버를 거치지 않는다 (WebRTC P2P).

실행: python signal_server.py
환경변수: HOST_KEY (호스트 등록용 키, 기본 dev), PORT (기본 8765)
          TURN_KEY_ID, TURN_API_TOKEN (선택: Cloudflare TURN 중계. 없으면 STUN만 쓴다)
"""
import asyncio
import hmac
import json
import os
import re
import uuid
from pathlib import Path

from aiohttp import ClientSession, WSMsgType, web

HOST_KEY = os.environ.get("HOST_KEY", "dev")
PORT = int(os.environ.get("PORT", "8765"))

# TURN 중계 (Cloudflare Realtime TURN). 둘 다 설정돼 있을 때만 쓴다
TURN_KEY_ID = os.environ.get("TURN_KEY_ID")
TURN_API_TOKEN = os.environ.get("TURN_API_TOKEN")
STUN_ONLY = [{"urls": ["stun:stun.l.google.com:19302"]}]

host_ws = None
pending = {}  # 요청 id -> answer를 기다리는 Future
ice_token = None  # 호스트가 등록한 값. 이 값을 아는 클라이언트에게만 TURN 자격증명을 준다


async def ice(request):
    """ICE 서버 목록. 호스트 키나 ice_token을 낸 쪽에만 TURN을 포함해 준다 (중계 트래픽 도용 방지)."""
    is_host = hmac.compare_digest(request.headers.get("X-Host-Key", ""), HOST_KEY)
    is_client = bool(ice_token) and hmac.compare_digest(request.headers.get("X-Ice-Token", ""), ice_token)
    if not (TURN_KEY_ID and TURN_API_TOKEN and (is_host or is_client)):
        return web.json_response(STUN_ONLY)
    url = f"https://rtc.live.cloudflare.com/v1/turn/keys/{TURN_KEY_ID}/credentials/generate-ice-servers"
    async with ClientSession() as http:
        async with http.post(url, headers={"Authorization": f"Bearer {TURN_API_TOKEN}"}, json={"ttl": 86400}) as r:
            if r.status >= 300:
                print(f"TURN 자격증명 발급 실패: {r.status}")
                return web.json_response(STUN_ONLY)
            servers = (await r.json())["iceServers"]
    if isinstance(servers, dict):
        servers = [servers]
    for s in servers:
        urls = [s["urls"]] if isinstance(s["urls"], str) else s["urls"]
        s["urls"] = [u for u in urls if not re.search(r":53(\?|$)", u)]  # 브라우저가 막는 53번 포트 제외
    return web.json_response(servers)


async def index(request):
    # 브라우저가 예전 페이지를 캐시해서 쓰지 않게 한다
    return web.FileResponse(Path(__file__).with_name("client.html"), headers={"Cache-Control": "no-store"})


async def host(request):
    global host_ws, ice_token
    if not hmac.compare_digest(request.headers.get("X-Host-Key", ""), HOST_KEY):
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    host_ws = ws
    print("호스트 연결됨")
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
            if "ice_token" in data:
                ice_token = data["ice_token"]
            fut = pending.pop(data.get("id"), None)  # id 없는 메시지는 호스트의 keepalive
            if fut and not fut.done():
                fut.set_result(data)
    if host_ws is ws:
        host_ws = None
    print("호스트 연결 끊김")
    return ws


async def offer(request):
    if host_ws is None:
        raise web.HTTPServiceUnavailable(text="host offline")
    body = await request.json()
    rid = uuid.uuid4().hex
    fut = pending[rid] = asyncio.get_running_loop().create_future()
    await host_ws.send_json({"id": rid, "sdp": body["sdp"], "type": body["type"]})
    try:
        answer = await asyncio.wait_for(fut, 20)
    except asyncio.TimeoutError:
        pending.pop(rid, None)
        raise web.HTTPGatewayTimeout(text="host timeout")
    return web.json_response({"sdp": answer["sdp"], "type": answer["type"]})


app = web.Application()
app.add_routes([web.get("/", index), web.get("/host", host), web.post("/offer", offer), web.get("/ice", ice)])

if __name__ == "__main__":
    web.run_app(app, port=PORT)
