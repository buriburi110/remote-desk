"""시그널링 서버: 클라이언트 페이지를 서빙하고, 브라우저의 offer를 호스트에 전달해 answer를 돌려준다.
영상·입력은 이 서버를 거치지 않는다 (WebRTC P2P).

실행: python signal_server.py
환경변수: HOST_KEY (호스트 등록용 키, 기본 dev), PORT (기본 8765)
"""
import asyncio
import hmac
import json
import os
import uuid
from pathlib import Path

from aiohttp import WSMsgType, web

HOST_KEY = os.environ.get("HOST_KEY", "dev")
PORT = int(os.environ.get("PORT", "8765"))

host_ws = None
pending = {}  # 요청 id -> answer를 기다리는 Future


async def index(request):
    return web.FileResponse(Path(__file__).with_name("client.html"))


async def host(request):
    global host_ws
    if not hmac.compare_digest(request.headers.get("X-Host-Key", ""), HOST_KEY):
        raise web.HTTPForbidden()
    ws = web.WebSocketResponse(heartbeat=30)
    await ws.prepare(request)
    host_ws = ws
    print("호스트 연결됨")
    async for msg in ws:
        if msg.type == WSMsgType.TEXT:
            data = json.loads(msg.data)
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
app.add_routes([web.get("/", index), web.get("/host", host), web.post("/offer", offer)])

if __name__ == "__main__":
    web.run_app(app, port=PORT)
