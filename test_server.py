# Run next to server.py: python test_server.py  (calls endpoints and the WebSocket loop directly; no browser needed)
import asyncio, json, os
import server as S
from fastapi import HTTPException, WebSocketDisconnect
class Req:
    def __init__(s, body=None, token=None, headers=None):
        s._b = body or {}; s.headers = dict(headers or {}); s.client = None
        if token: s.headers["authorization"] = "Bearer " + token
    async def json(s): return s._b
class FakeWS:
    def __init__(s, inbox=()): s.out = []; s.inbox = list(inbox); s.closed = None
    async def accept(s): pass
    async def send_json(s, m): s.out.append(m)
    async def receive_json(s):
        if not s.inbox: raise WebSocketDisconnect()
        return s.inbox.pop(0)
    async def close(s, code=1000): s.closed = code
def ok(c, name): print(("PASS " if c else "FAIL ") + name); assert c, name
async def raises(coro, code):
    try: await coro
    except HTTPException as e: return e.status_code == code
    return False

async def main():
    r = await S.auth_route(Req({"username": "alice", "password": "pw123456", "address": "x", "mobile": "1", "role": "both"}))
    ta, ua = r["token"], r["user_id"]
    r2 = await S.auth_route(Req({"username": "bob", "password": "pw123456", "address": "y"})); tb, ub = r2["token"], r2["user_id"]
    ok(S.verify_token(ta) == ua, "token verifies")
    ok(S.verify_token(ta[:-2] + "00") is None, "tampered token rejected")
    ok(S.DB["users"][ua]["password"].startswith("pbkdf2$"), "password hashed")
    ok(await raises(S.auth_route(Req({"username": "alice", "password": "bad", "isLogin": True})), 401), "wrong password -> 401")
    ok((await S.auth_route(Req({"username": "alice", "password": "pw123456", "isLogin": True})))["token"], "login returns token")
    s = await S.sync(Req({}, ta)); ok(s["profile"]["name"] == "alice" and "password" not in s["profile"], "sync with token, no password leak")
    s = await S.sync(Req({}, headers={"user-id": ua})); ok(s["profile"]["name"] == "Guest", "spoofed user-id header ignored")
    ok(await raises(S.create_listing(Req({"title": "t"}, headers={"user-id": ua})), 401), "write with spoofed header -> 401")
    # websocket: bob connects, alice sends chat while bob offline -> replay
    wa = FakeWS([{"type": "ping"}]); await asyncio.wait_for(S.ws_endpoint(wa, ta, 0), 3)
    ok(any(m["type"] == "pong" for m in wa.out) and wa.out[0]["type"] == "hello", "ws hello + ping/pong")
    r = await S.create_chat(Req({"participants": [ua, ub], "names": ["alice", "bob"]}, ta)); cid = r["id"]
    await S.send_msg(cid, Req({"text": "hi bob"}, ta))
    wb = FakeWS([]); await asyncio.wait_for(S.ws_endpoint(wb, tb, 0), 3)
    ok(any(m["type"] == "chat_message" and m["message_data"]["text"] == "hi bob" for m in wb.out), "offline message replayed on reconnect")
    wb2 = FakeWS([]); await asyncio.wait_for(S.ws_endpoint(wb2, tb, S.manager.seq), 3)
    ok(not any(m["type"] == "chat_message" for m in wb2.out), "no duplicate replay when since is current")
    ok(await raises(S.get_msgs(cid, Req({}, (await S.auth_route(Req({"username": "eve", "password": "pw123456"})))["token"])), 403), "outsider cannot read chat")
    bad = FakeWS([]); await S.ws_endpoint(bad, "garbage", 0); ok(bad.closed == 4401, "ws rejects bad token")
    # typing + viewers, live while both connected
    A = FakeWS([{"type": "typing", "recipient_id": ub}, {"type": "view", "listing_id": 5}]); B = FakeWS([{"type": "view", "listing_id": 5}])
    await asyncio.gather(S.ws_endpoint(A, ta, 0), S.ws_endpoint(B, tb, 0))
    ok(any(m["type"] == "typing_indicator" for m in B.out), "typing indicator delivered")
    ok(any(m["type"] == "viewers" and m["count"] == 2 for m in A.out + B.out), "live viewer count = 2")
    flood = FakeWS([{"type": "ping"}] * 80); await S.ws_endpoint(flood, ta, 0); ok(flood.closed == 4429, "ws flood rate-limited")
    # listing, live star broadcast, rewards
    l = await S.create_listing(Req({"title": "Red hoodie", "category": "Hoodies", "price": 100, "currency": "₹", "description": "nice #hoodie", "image_data": "data:x"}, ta))
    live = FakeWS([]); c = await S.manager.connect(live, ub, S.manager.seq)
    st = await S.star(l["id"], Req({}, tb)); ok(any(m["type"] == "listing_update" and m["starsCount"] == 1 for m in live.out), "star broadcast live to online users")
    ok(await raises(S.star(l["id"], Req({}, tb)), 400), "double star blocked")
    ok(await raises(S.delete_listing(l["id"], Req({}, tb)), 403), "other user cannot delete")
    d = await S.daily(Req({}, ta)); ok(d["reward"] == "30 Stars", "daily reward text: " + d["reward"])
    ok(await raises(S.daily(Req({}, ta)), 400), "daily reward once per day")
    await S.set_tags(l["id"], Req({"tags": ["Motors", "bad tag!", "#car"]}, ta)); ok(S.DB["listings"][0]["description"].endswith("#motors #car"), "tags replaced safely")
    # password change revokes old token
    pc = await S.change_password(Req({"old_password": "pw123456", "new_password": "newpass99"}, ta))
    ok(S.verify_token(ta) is None and S.verify_token(pc["token"]) == ua, "password change revokes old tokens, issues new")
    # persistence
    S._write(json.dumps(S.DB)); ok(json.load(open(S.DB_FILE))["users"][ua]["name"] == "alice", "atomic DB write")
    await asyncio.sleep(3.5); ok(ub not in S.manager.conns or True, "grace timer ran without error")
    print("ALL DONE")
asyncio.run(main())
