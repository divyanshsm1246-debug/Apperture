"""
Apperture combined server — ONE process serves: REST API + WebSocket hub + the website (index.html) + background lifecycle sweep.
Run:   pip install fastapi uvicorn firebase-admin
       APPERTURE_ADMIN_KEY="choose-a-long-secret" APPERTURE_CO_ADMIN_KEYS="key1,key2" uvicorn server:app --host 0.0.0.0 --port 8000
Put index.html, apperture-v2.js, nexus-commands.js next to this file, open http://localhost:8000/
Keys now come from environment variables (never hardcode them in the page). Passwords are stored as PBKDF2 hashes.
"""
import os, json, time, re, math, asyncio, hashlib, hmac, secrets, random, shutil
from datetime import datetime, timezone
from collections import Counter, deque
from contextlib import asynccontextmanager
from typing import Dict
from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

try:
    import firebase_admin
    from firebase_admin import auth as fb_auth
    if not firebase_admin._apps:
        firebase_admin.initialize_app(options={"projectId": "apperture-4889e"})
    FIREBASE = True
except Exception:
    FIREBASE = False

@asynccontextmanager
async def lifespan(_app):
    tasks = [asyncio.create_task(flusher()), asyncio.create_task(sweep())]
    yield
    for t in tasks: t.cancel()
    _write(json.dumps(DB))  # final flush on shutdown

app = FastAPI(title="Apperture Server", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["*"], allow_headers=["*"])

DB_FILE, DAY, HOUR = "database.json", 86_400_000, 3_600_000
ADMIN_KEY = os.getenv("APPERTURE_ADMIN_KEY", "")
CO_KEYS = [k for k in os.getenv("APPERTURE_CO_ADMIN_KEYS", "").split(",") if k]
COUPONS = json.load(open("coupons.json")) if os.path.exists("coupons.json") else {}  # {"CODE": {"s":500,"g":7,"t":3}}
BAD = ["fuck", "shit", "bitch", "bastard", "bhenchod", "madarchod", "chutiya", "randi", "bhosdike", "puta", "mierda", "pendejo"]
FRAUD = ["scam", "whatsapp", "telegram", "paytm", "gpay"]
TAG_RE = re.compile(r"#[a-z0-9_]+")
FREE_USER = {"name": "Guest", "isAdmin": False, "isCoAdmin": False, "stars": 0, "gems": 0, "tickets": 0}

# ---------------- helpers ----------------
def now_ms(): return int(time.time() * 1000)
def utc_date(ms): return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()

def _read():
    keys = ("users", "listings", "cart", "chats", "messages", "notifications", "supremeAds", "rewards", "votes", "tagViews")
    base = {k: ({} if k in ("users", "cart", "rewards", "votes", "tagViews") else []) for k in keys}
    if os.path.exists(DB_FILE):
        try:
            with open(DB_FILE) as f: base.update(json.load(f))
        except Exception: shutil.copy(DB_FILE, DB_FILE + ".corrupt")
    return base

DB = _read()          # single in-memory source of truth: no per-request disk reads, no lost updates
_dirty = False
def load_db(): return DB
def save_db(_=None):
    global _dirty; _dirty = True

def _write(text):
    with open(DB_FILE + ".tmp", "w") as f: f.write(text)
    os.replace(DB_FILE + ".tmp", DB_FILE)

hits: dict = {}
async def flusher():
    global _dirty
    while True:
        await asyncio.sleep(1)
        if _dirty:
            _dirty = False; text = json.dumps(DB); await asyncio.to_thread(_write, text)
        now = time.monotonic()
        for k in [k for k, q in hits.items() if not q or now - q[-1] > 10]: hits.pop(k, None)

def hash_pw(pw, salt=None):
    salt = salt or secrets.token_hex(8)
    return f"pbkdf2${salt}${hashlib.pbkdf2_hmac('sha256', pw.encode(), salt.encode(), 120_000).hex()}"

def check_pw(pw, stored):
    if stored.startswith("pbkdf2$"):
        _, salt, _ = stored.split("$"); return secrets.compare_digest(hash_pw(pw, salt), stored)
    return secrets.compare_digest(pw, stored)  # legacy plaintext; upgraded on next login

def vulgar(t): return any(re.search(r"\b" + re.escape(w) + r"\b", (t or "").lower()) for w in BAD)
def tags_of(l): return list(dict.fromkeys(TAG_RE.findall(((l.get("description") or "") + " " + (l.get("title") or "")).lower())))
def public(u): return {k: v for k, v in u.items() if k not in ("password",)}

_sf = "secret.key"
if os.getenv("APPERTURE_SECRET"): SECRET = os.getenv("APPERTURE_SECRET").encode()
else:
    if not os.path.exists(_sf): open(_sf, "w").write(secrets.token_hex(32))
    SECRET = open(_sf).read().strip().encode()
ALLOW_LEGACY = os.getenv("APPERTURE_ALLOW_LEGACY") == "1"   # dev only: trusts the old spoofable user-id header

def _sig(p): return hmac.new(SECRET, p.encode(), hashlib.sha256).hexdigest()
def make_token(uid, days=30):
    p = f"{uid}.{DB['users'].get(uid, {}).get('pwv', 0)}.{int(time.time()) + days * 86400}"; return p + "." + _sig(p)
def verify_token(t):
    try: uid, pwv, exp, sig = (t or "").split(".")
    except ValueError: return None
    if not hmac.compare_digest(sig, _sig(f"{uid}.{pwv}.{exp}")) or int(exp) < time.time(): return None
    u = DB["users"].get(uid)
    return uid if u and u.get("pwv", 0) == int(pwv) else None   # changing password revokes old tokens

def uid_of(request):
    a = request.headers.get("authorization", "")
    if a.lower().startswith("bearer "): return verify_token(a[7:]) or ""
    return request.headers.get("user-id", "") if ALLOW_LEGACY else ""

def me(request, db=None):
    u = DB["users"].get(uid_of(request))
    if not u: raise HTTPException(401, "Unauthorized")
    if u.get("isBanned"): raise HTTPException(403, "Account removed by admin")
    return u

def haversine(a, b, c, d):
    r = math.pi / 180; h = math.sin((c - a) * r / 2) ** 2 + math.cos(a * r) * math.cos(c * r) * math.sin((d - b) * r / 2) ** 2
    return 12742 * math.asin(math.sqrt(h))

@app.middleware("http")
async def limiter(request: Request, call_next):
    q = hits.setdefault(request.client.host if request.client else "?", deque()); now = time.monotonic()
    while q and now - q[0] > 10: q.popleft()
    if len(q) >= 120: return JSONResponse({"detail": "Too many requests, slow down."}, status_code=429)
    q.append(now); return await call_next(request)

# ---------------- websocket hub ----------------
DURABLE = {"chat_message", "notification", "new_chat", "global_alert", "read"}   # replayed to clients that reconnect
class Client:
    __slots__ = ("ws", "uid")
    def __init__(self, ws, uid): self.ws, self.uid = ws, uid

class Hub:
    def __init__(self):
        self.conns: Dict[str, set] = {}; self.seq = 0; self.buf = deque(maxlen=3000)
        self.watching: Dict[str, int] = {}; self.viewers: Dict[int, set] = {}
    def online(self): return list(self.conns)
    async def _push(self, c, msg):
        try: await asyncio.wait_for(c.ws.send_json(msg), 5); return True
        except Exception: self.remove(c); return False          # slow/dead client is dropped, never blocks others
    def _stamp(self, msg, target):
        msg = dict(msg)
        if msg["type"] in DURABLE: self.seq += 1; msg["seq"] = self.seq; self.buf.append((self.seq, target, msg))
        return msg
    async def send(self, msg, uid):
        msg = self._stamp(msg, uid)
        for c in list(self.conns.get(uid, ())): await self._push(c, msg)
    async def broadcast(self, msg):
        msg = self._stamp(msg, None)
        await asyncio.gather(*(self._push(c, msg) for cs in list(self.conns.values()) for c in list(cs)))
    async def connect(self, ws, uid, since):
        await ws.accept(); c = Client(ws, uid); first = uid not in self.conns
        self.conns.setdefault(uid, set()).add(c)
        await self._push(c, {"type": "hello", "seq": self.seq, "online": self.online()[:500]})
        for seq, target, m in list(self.buf):                    # replay what was missed while offline
            if seq > since and target in (None, uid): await self._push(c, m)
        if first: await self.broadcast({"type": "presence", "status": "online", "user_id": uid})
        return c
    def remove(self, c):
        cs = self.conns.get(c.uid)
        if cs is None: return
        cs.discard(c)
        if not cs:
            self.conns.pop(c.uid, None); asyncio.create_task(self._offline(c.uid))
    async def _offline(self, uid):
        await asyncio.sleep(3)                                    # grace period so page refreshes don't flap presence
        if uid in self.conns: return
        await self.unview(uid); await self.broadcast({"type": "presence", "status": "offline", "user_id": uid})
    async def kick(self, uid):
        for c in list(self.conns.get(uid, ())):
            try: await c.ws.close(4000)
            except Exception: pass
    async def _count(self, lid):
        s = self.viewers.get(lid, set())
        for u in list(s): await self.send({"type": "viewers", "listing_id": lid, "count": len(s)}, u)
    async def view(self, uid, lid):
        if self.watching.get(uid) == lid: return
        await self.unview(uid); self.watching[uid] = lid; self.viewers.setdefault(lid, set()).add(uid); await self._count(lid)
    async def unview(self, uid):
        lid = self.watching.pop(uid, None)
        if lid is None: return
        s = self.viewers.get(lid, set()); s.discard(uid)
        if s: await self._count(lid)
        else: self.viewers.pop(lid, None)
manager = Hub()

def shares_chat(a, b): return any(a in c["participants"] and b in c["participants"] for c in DB["chats"])

@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket, token: str = "", since: int = 0):
    uid = verify_token(token)
    if not uid or DB["users"][uid].get("isBanned"): await ws.close(4401); return
    c = await manager.connect(ws, uid, since); tokens, last = 30.0, time.monotonic()
    try:
        while True:
            d = await asyncio.wait_for(ws.receive_json(), 70)    # client pings every 20s; silence = dead connection
            now = time.monotonic(); tokens = min(30.0, tokens + (now - last) * 3); last = now
            if tokens < 1: await ws.close(4429); break
            tokens -= 1; t = d.get("type")
            if t == "ping": await ws.send_json({"type": "pong"})
            elif t == "typing" and d.get("recipient_id") and shares_chat(uid, d["recipient_id"]):
                await manager.send({"type": "typing_indicator", "from_user": uid}, d["recipient_id"])
            elif t == "view" and isinstance(d.get("listing_id"), int): await manager.view(uid, d["listing_id"])
            elif t == "unview": await manager.unview(uid)
            elif t == "read":
                ch = next((x for x in DB["chats"] if x["id"] == d.get("chat_id") and uid in x["participants"]), None)
                for p in (ch["participants"] if ch else []):
                    if p != uid: await manager.send({"type": "read", "chat_id": ch["id"], "by": uid}, p)
    except (WebSocketDisconnect, asyncio.TimeoutError, Exception):
        pass
    finally:
        manager.remove(c)

# ---------------- auth & account ----------------
def new_user(db, name, pw, **kw):
    uid = f"user_{now_ms()}"
    db["users"][uid] = {"id": uid, "name": name, "password": hash_pw(pw) if pw else "OAUTH", "address": kw.get("address", ""),
        "mobile": kw.get("mobile", ""), "role": kw.get("role", "both"), "feedback": kw.get("feedback", ""), "email": kw.get("email"),
        "isAdmin": kw.get("isAdmin", False), "isCoAdmin": kw.get("isCoAdmin", False), "stars": kw.get("stars", 10), "gems": kw.get("gems", 1),
        "tickets": 1, "profilePic": kw.get("pic", ""), "usedCoupons": [], "lastActive": now_ms()}
    return uid

@app.post("/auth")
async def auth_route(request: Request):
    b, db = await request.json(), load_db()
    if b.get("isGoogleAuth"):
        email = b.get("email")
        if FIREBASE and b.get("id_token"):
            try: email = fb_auth.verify_id_token(b["id_token"]).get("email", email)
            except Exception as e: raise HTTPException(401, f"Google token invalid: {e}")
        found = next((k for k, v in db["users"].items() if v.get("email") == email), None)
        if found: return {"status": "logged_in", "reward": "Google Sign-In Successful!", "user_id": found, "token": make_token(found)}
        uid = new_user(db, b.get("username") or email.split("@")[0], None, email=email, pic=b.get("profile_pic", ""), address="Verified via Google")
        save_db(db); return {"status": "created", "reward": "Google account linked!", "user_id": uid, "token": make_token(uid)}

    name, pw, ref = (b.get("username") or "").strip(), b.get("password") or "", b.get("referral")
    if not name or not pw: raise HTTPException(400, "Username and password required.")
    found = next((k for k, v in db["users"].items() if v["name"] == name), None)
    if b.get("isLogin"):
        if not found: raise HTTPException(404, "User not found.")
        if not check_pw(pw, db["users"][found]["password"]): raise HTTPException(401, "Incorrect Password.")
        db["users"][found]["password"] = hash_pw(pw) if not db["users"][found]["password"].startswith("pbkdf2$") else db["users"][found]["password"]
        db["users"][found]["lastActive"] = now_ms(); db["users"][found]["dead"] = False; save_db(db)
        return {"status": "logged_in", "reward": "Welcome Back!", "user_id": found, "token": make_token(found)}
    if found: raise HTTPException(400, "Username already taken.")
    if vulgar(name): raise HTTPException(400, "Inappropriate username.")
    kw, msg = {}, "Profile Created! Free ticket awarded."
    if ref:
        if ADMIN_KEY and secrets.compare_digest(ref, ADMIN_KEY): kw["isAdmin"] = True
        elif ref in CO_KEYS: kw["isCoAdmin"] = True
        elif ref in COUPONS: raise HTTPException(400, "Redeem coupons in Edit Profile.")
        else:
            rid = next((k for k, v in db["users"].items() if v["name"] == ref), None)
            if not rid: raise HTTPException(400, "Invalid referral code or username.")
            db["users"][rid]["stars"] += 50; db["users"][rid]["gems"] += 2
            n = {"type": "sys", "message": f"{name} joined with your referral! +50 Stars +2 Gems", "recipient_id": rid, "expiresAt": now_ms() + 7 * DAY}
            db["notifications"].append(n); await manager.send({"type": "notification", "data": n}, rid)
            kw.update(stars=60, gems=3); msg = "Referral applied: +50 Stars & +2 Gems!"
    uid = new_user(db, name, pw, address=b.get("address", ""), mobile=b.get("mobile", ""), role=b.get("role", "both"),
                   feedback=b.get("feedback", ""), pic=b.get("profile_pic", ""), **kw)
    save_db(db); return {"status": "created", "reward": msg, "user_id": uid, "token": make_token(uid)}

@app.put("/profile")
async def profile_update(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); msg = None
    c = (b.get("coupon") or "").strip()
    if c:
        if c not in COUPONS: raise HTTPException(400, "Invalid Coupon Code")
        if c in u["usedCoupons"]: raise HTTPException(400, "Coupon already redeemed.")
        r = COUPONS[c]; u["stars"] += r["s"]; u["gems"] += r["g"]; u["tickets"] += r["t"]; u["usedCoupons"].append(c)
        msg = f"Coupon Redeemed! +{r['s']} Stars, +{r['g']} Gems, +{r['t']} Tickets"
    n = b.get("name")
    if n and ADMIN_KEY and secrets.compare_digest(n, ADMIN_KEY): u["isAdmin"] = True; n = None
    elif n in CO_KEYS: u["isCoAdmin"] = True; n = None
    if n: u["name"] = n
    if b.get("address"): u["address"] = b["address"]
    if b.get("profile_pic"): u["profilePic"] = b["profile_pic"]
    save_db(db); return {"status": "updated", "reward": msg}

@app.post("/account/username")
async def change_username(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); name = (b.get("username") or "").strip()
    if not 3 <= len(name) <= 24: raise HTTPException(400, "Username must be 3-24 characters.")
    if vulgar(name): raise HTTPException(400, "Inappropriate username.")
    if any(v["name"].lower() == name.lower() and v["id"] != u["id"] for v in db["users"].values()): raise HTTPException(400, "Username already taken.")
    u["name"] = name
    for l in db["listings"]:
        if l["sellerId"] == u["id"]: l["sellerName"] = name
    save_db(db); await manager.broadcast({"type": "profile_update", "user_id": u["id"], "name": name}); return {"status": "ok", "username": name}

@app.post("/account/password")
async def change_password(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db)
    if u["password"] != "OAUTH" and not check_pw(b.get("old_password", ""), u["password"]): raise HTTPException(401, "Current password is wrong.")
    if len(b.get("new_password", "")) < 8: raise HTTPException(400, "New password needs 8+ characters.")
    u["password"] = hash_pw(b["new_password"]); u["pwv"] = u.get("pwv", 0) + 1; save_db(db); await manager.kick(u["id"]); return {"status": "ok", "token": make_token(u["id"])}

@app.put("/shop")
async def save_shop(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db)
    u["shop"] = {"name": str(b.get("name", ""))[:80], "timings": str(b.get("timings", ""))[:40], "lat": float(b["lat"]), "lng": float(b["lng"])}
    save_db(db); return {"status": "ok"}

@app.get("/shops/nearby")
async def shops_nearby(lat: float, lng: float, radius_km: float = 1.5):
    r, db, out = max(0.5, min(radius_km, 5.0)), load_db(), []
    for uid, u in db["users"].items():
        s = u.get("shop")
        if s and not u.get("dead") and not u.get("isBanned") and (d := haversine(lat, lng, s["lat"], s["lng"])) <= r:
            out.append({"seller_id": uid, **s, "km": round(d, 2)})
    return sorted(out, key=lambda x: x["km"])

# ---------------- sync ----------------
@app.post("/sync")
async def sync(request: Request):
    db, now = load_db(), now_ms()
    uid = uid_of(request)
    db["supremeAds"] = [a for a in db["supremeAds"] if a["endTime"] > now]
    db["notifications"] = [n for n in db["notifications"] if n.get("expiresAt", 0) > now]
    for c in db["rewards"].values():
        if c.get("streak") and (utc_date(now) - utc_date(c["lastClaimed"])).days > 1: c["streak"] = 0
    if uid in db["users"]: db["users"][uid]["lastActive"] = now
    save_db(db)
    dead = {k for k, v in db["users"].items() if v.get("dead")}
    notifs = [n for n in db["notifications"] if n.get("recipient_id") == uid or (not n.get("recipient_id") and (n["type"] in ("admin_alert", "sys") or (n["type"] == "user_ad" and n.get("scheduledFor", 0) <= now)))]
    return {"listings": [l for l in db["listings"] if l["sellerId"] not in dead or l["sellerId"] == uid], "notifications": notifs,
            "my_scheduled": [n for n in db["notifications"] if n.get("poster_id") == uid and n["type"] == "user_ad"],
            "my_supreme": [a for a in db["supremeAds"] if a.get("buyerId") == uid], "cart": db["cart"].get(uid, []),
            "chats": [c for c in db["chats"] if uid in c["participants"]], "supreme_ads": db["supremeAds"],
            "profile": public(db["users"].get(uid, FREE_USER)), "rewards": db["rewards"].get(uid, {"streak": 0, "lastClaimed": 0})}

# ---------------- listings ----------------
@app.post("/listings")
async def create_listing(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db)
    if vulgar(" ".join(str(b.get(k, "")) for k in ("title", "description", "address", "category"))): raise HTTPException(400, "Inappropriate language detected.")
    if b.get("category") == "Special Products" and not u.get("isAdmin"): raise HTTPException(403, "Admins only.")
    mine = [l for l in db["listings"] if l["sellerId"] == u["id"]]
    if b.get("isMagnetic") and not u.get("isAdmin") and len([l for l in mine if not l.get("isMagnetic")]) < (len([l for l in mine if l.get("isMagnetic")]) + 1) * 3:
        raise HTTPException(400, "1:3 ratio not met: need 3 normal listings per magnetic one.")
    lid = now_ms()
    l = {"id": lid, "sellerId": u["id"], "sellerName": u["name"], "timestamp": lid, "starsCount": 0, "shuffleIndex": random.random(),
         **{k: b.get(k) for k in ("title", "category", "price", "currency", "description", "condition", "address", "phone", "image_data", "isMagnetic")}, "images": b.get("images", [])}
    db["listings"].append(l); save_db(db)
    await manager.broadcast({"type": "new_listing", "data": l}); return {"status": "created", "id": lid}

def find_listing(db, lid):
    l = next((x for x in db["listings"] if x["id"] == lid), None)
    if not l: raise HTTPException(404, "Item not found")
    return l

@app.delete("/listings/{lid}")
async def delete_listing(lid: int, request: Request):
    db = load_db(); u = me(request, db); l = find_listing(db, lid)
    if l["sellerId"] != u["id"] and not u.get("isAdmin"): raise HTTPException(403, "Unauthorized")
    db["listings"].remove(l)
    if l["sellerId"] != u["id"]: db["notifications"].append({"type": "admin_alert", "message": f"Your listing '{l['title']}' was removed by an admin.", "recipient_id": l["sellerId"], "expiresAt": now_ms() + DAY})
    save_db(db); await manager.broadcast({"type": "listing_removed", "id": lid}); return {"status": "deleted"}

@app.put("/listings/{lid}/tags")
async def set_tags(lid: int, request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); l = find_listing(db, lid)
    if l["sellerId"] != u["id"]: raise HTTPException(403, "Not your listing")
    tags = list(dict.fromkeys("#" + t.lstrip("#").lower() for t in b.get("tags", []) if re.fullmatch(r"#?[A-Za-z0-9_]{1,30}", t)))[:30]
    l["description"] = re.sub(r"#[A-Za-z0-9_]+", "", l.get("description") or "").rstrip() + "\n" + " ".join(tags)
    save_db(db); return {"status": "ok", "tags": tags}

@app.post("/listings/{lid}/star")
async def star(lid: int, request: Request):
    db = load_db(); u = me(request, db); l = find_listing(db, lid); v = db["votes"].setdefault(u["id"], [])
    if lid in v: raise HTTPException(400, "Already starred.")
    if len(v) >= 20: raise HTTPException(400, "Max 20 stars per user.")
    v.append(lid); l["starsCount"] = l.get("starsCount", 0) + 1; save_db(db); await manager.broadcast({"type": "listing_update", "id": lid, "starsCount": l["starsCount"]}); return {"stars": l["starsCount"], "message": "Starred +1!"}

@app.post("/listings/{lid}/boost")
async def boost(lid: int, request: Request):
    db = load_db(); u = me(request, db); l = find_listing(db, lid)
    if l["sellerId"] != u["id"]: raise HTTPException(403, "Not your listing")
    if u["gems"] < 25: raise HTTPException(400, "Need 25 Gems.")
    mine = [x for x in db["listings"] if x["sellerId"] == u["id"]]
    if len([x for x in mine if not x.get("isMagnetic")]) - 1 < (len([x for x in mine if x.get("isMagnetic")]) + 1) * 3 and not u.get("isAdmin"):
        raise HTTPException(400, "1:3 ratio not met.")
    u["gems"] -= 25; l["isMagnetic"] = True; l["gemBoosted"] = True; save_db(db); return {"status": "boosted"}

@app.get("/tags/trending")
async def trending(limit: int = 100):
    c = Counter()
    for l in load_db()["listings"]:
        for t in tags_of(l): c[t] += 1 + l.get("starsCount", 0) / 10
    return [t for t, _ in c.most_common(min(limit, 100))]

# ---------------- chats ----------------
@app.post("/chats")
async def create_chat(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db)
    parts = b.get("participants", [])
    if u["id"] not in parts: raise HTTPException(403, "Not a participant")
    chat = {"id": now_ms(), "participants": parts, "names": b.get("names", []), "last_message": b.get("lastMessage", ""), "last_updated": now_ms()}
    db["chats"].append(chat); save_db(db)
    for p in parts:
        if p != u["id"]: await manager.send({"type": "new_chat", "chat_data": chat, "message": f"New chat from {u['name']}"}, p)
    return {"id": chat["id"]}

@app.get("/chats/{cid}/messages")
async def get_msgs(cid: int, request: Request):
    db = load_db(); u = me(request, db); c = next((x for x in db["chats"] if x["id"] == cid), None)
    if not c or u["id"] not in c["participants"]: raise HTTPException(403, "Not your chat")
    return [m for m in db["messages"] if m["chatId"] == cid]

@app.post("/chats/{cid}/messages")
async def send_msg(cid: int, request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); c = next((x for x in db["chats"] if x["id"] == cid), None)
    if not c or u["id"] not in c["participants"]: raise HTTPException(403, "Not your chat")
    if vulgar(b.get("text")): raise HTTPException(400, "Inappropriate language detected.")
    m = {"chatId": cid, "text": b.get("text", ""), "sender_id": u["id"], "timestamp": now_ms()}
    db["messages"].append(m); c["last_message"], c["last_updated"] = m["text"], m["timestamp"]; save_db(db)
    for p in c["participants"]:
        if p != u["id"]: await manager.send({"type": "chat_message", "chat_id": cid, "message_data": m}, p)
    return {"status": "sent"}

# ---------------- notifications, tag push, ads ----------------
tag_views = DB["tagViews"]; last_push: dict = {}

@app.post("/tags/view")
async def track_view(request: Request):
    db = load_db(); u = me(request, db); c = tag_views.setdefault(u["id"], {})
    for t in (await request.json()).get("tags", [])[:30]:
        if re.fullmatch(r"#[a-z0-9_]{1,30}", str(t)): c[t] = c.get(t, 0) + 1
    return {"ok": True}

@app.post("/notifications")
async def notifications(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); now = now_ms()
    if vulgar(b.get("message")): raise HTTPException(400, "Inappropriate language detected.")
    if b.get("type") == "admin_alert":
        if not u.get("isAdmin") and not u.get("isCoAdmin"): raise HTTPException(403, "Admins only")
        n = {**b, "scheduledFor": now, "expiresAt": now + DAY}; db["notifications"].append(n); save_db(db)
        await manager.broadcast({"type": "global_alert", "data": n}); return {"status": "sent"}
    if b.get("type") == "user_ad":
        tags = TAG_RE.findall((b.get("message") or "").lower()); reached = 0
        for uid, cnt in tag_views.items():  # tag-targeted: 20+ views of the tag, max 1 push/user/hour
            if tags and any(cnt.get(t, 0) >= 20 for t in tags) and now - last_push.get(uid, 0) >= HOUR:
                last_push[uid] = now; reached += 1
                await manager.send({"type": "notification", "data": {"type": "user_ad", "message": b["message"]}}, uid)
        dur = 30 * 60_000; act = [n for n in db["notifications"] if n["type"] == "user_ad" and not n.get("recipient_id") and n["expiresAt"] > now]
        times = sorted({n["scheduledFor"] for n in act}); t, idx, sub = None, 0, "A"
        for i, x in enumerate(times):
            if sum(1 for n in act if n["scheduledFor"] == x) < 2: t, idx, sub = x, i + 1, "B"; break
        if t is None:
            if len(times) >= 24: raise HTTPException(400, "QUEUE_FULL")
            t, idx = (now + dur if not times else times[-1] + dur), len(times) + 1
        db["notifications"].append({**b, "scheduledFor": t, "expiresAt": t + dur, "poster_id": u["id"], "slotIndex": idx, "subSlot": sub}); save_db(db)
        return {"status": "booked", "scheduledAt": t, "slotIndex": idx, "subSlot": sub, "tag_reach": reached}
    raise HTTPException(400, "Unknown notification type")

@app.post("/ads/supreme")
async def supreme(request: Request):
    b, db = await request.json(), load_db(); u = me(request, db); now = now_ms(); slot = b.get("slot")
    if slot not in ("slot1", "slot2", "slot3"): raise HTTPException(400, "Select Slot 1, 2 or 3.")
    if any(a["slot"] == slot and a["endTime"] > now for a in db["supremeAds"]): raise HTTPException(400, "Space occupied.")
    db["supremeAds"].append({**b, "buyerId": u["id"], "startTime": now, "endTime": now + DAY}); save_db(db); return {"status": "booked"}

# ---------------- rewards & wheel ----------------
@app.post("/daily-rewards")
async def daily(request: Request):
    db = load_db(); u = me(request, db); c = db["rewards"].get(u["id"], {"streak": 0, "lastClaimed": 0}); now = now_ms()
    gap = 1 if not c["lastClaimed"] else (utc_date(now) - utc_date(c["lastClaimed"])).days
    if c["lastClaimed"] and gap == 0: raise HTTPException(400, "Already claimed today.")
    if gap > 1 or c["streak"] >= 7: c["streak"] = 0
    c["streak"] += 1; c["lastClaimed"] = now; db["rewards"][u["id"]] = c
    table = {1: (30, 0, 0), 2: (80, 0, 0), 3: (300, 0, 0), 4: (0, 0, 2), 5: (0, 1, 0), 6: (500, 1, 0), 7: (0, 1, 0)}
    s, g, t = table[c["streak"]]; u["stars"] += s; u["gems"] += g; u["tickets"] += t; save_db(db)
    return {"status": "claimed", "reward": " & ".join(x for x in (f"{s} Stars" if s else "", f"{g} Gems" if g else "", f"{t} Tickets" if t else "") if x)}

@app.post("/wheel/spin")
async def spin(request: Request):
    db = load_db(); u = me(request, db)
    if u["tickets"] < 1 and not u.get("isAdmin"): raise HTTPException(400, "Requires 1 Apperture Ticket!")
    if not u.get("isAdmin"): u["tickets"] -= 1
    r = random.random() * 25
    for lim, name, (s, g, t) in [(2, "Legendary Jackpot", (300, 3, 2)), (5, "1 Ticket", (0, 0, 1)), (10, "1 Gem & 30 Stars", (30, 1, 0)), (15, "1 Gem", (0, 1, 0)), (17, "200 Stars", (200, 0, 0)), (22, "40 Stars", (40, 0, 0)), (99, "10 Stars", (10, 0, 0))]:
        if r < lim: u["stars"] += s; u["gems"] += g; u["tickets"] += t; save_db(db); return {"prize": name}

# ---------------- admin ----------------
def admin(request, db, full=True):
    u = me(request, db)
    if not (u.get("isAdmin") or (u.get("isCoAdmin") and not full)): raise HTTPException(403, "Unauthorized Access")
    return u

@app.get("/admin/analytics")
async def analytics(request: Request):
    db = load_db(); admin(request, db, full=False); roles = Counter(u.get("role") for u in db["users"].values()); now = now_ms()
    return {"buyers": roles["buyer"] + roles["both"], "sellers": roles["seller"] + roles["both"], "categories": dict(Counter(l["category"] for l in db["listings"])),
            "activeAds": sum(1 for a in db["supremeAds"] if a["endTime"] > now), "totalListings": len(db["listings"])}

@app.get("/admin/users")
async def admin_users(request: Request):
    db = load_db(); admin(request, db)
    return [{k: u.get(k) for k in ("id", "name", "role", "isAdmin", "isCoAdmin", "isBanned", "dead")} for u in db["users"].values()]

@app.post("/admin/users/{tid}/ban")
async def ban(tid: str, request: Request):
    db = load_db(); a = admin(request, db)
    if tid == a["id"] or tid not in db["users"]: raise HTTPException(400, "Invalid target")
    db["users"][tid]["isBanned"] = True; save_db(db); await manager.kick(tid); return {"status": "banned"}

# ---------------- dead-mode lifecycle ----------------
@app.post("/activity/revive")
async def revive(request: Request):
    db = load_db(); u = me(request, db); u["dead"] = False; u["warned"] = False; u["lastActive"] = now_ms(); save_db(db); return {"status": "revived"}

async def sweep():
    while True:
        db, now, changed = load_db(), now_ms(), False
        for uid, u in db["users"].items():
            idle = now - u.get("lastActive", now); msg = None
            if idle > 30 * DAY and not u.get("dead"): u["dead"] = True; msg = "Nexus AI: your account is DEAD and listings are hidden. Open the account and turn off Dead Mode."
            elif idle > 7 * DAY and not u.get("warned"): u["warned"] = True; msg = "Nexus AI: away for a week. After 30 days your listings go dead."
            if msg:
                n = {"type": "sys", "message": msg, "recipient_id": uid, "expiresAt": now + 7 * DAY}; db["notifications"].append(n); changed = True
                await manager.send({"type": "notification", "data": n}, uid)
        if changed: save_db(db)
        await asyncio.sleep(HOUR)


# ---------------- website (same process) ----------------
@app.get("/")
async def index(): return FileResponse("index.html")

@app.get("/{name}.js")
async def js(name: str):
    p = f"{os.path.basename(name)}.js"
    if not os.path.exists(p): raise HTTPException(404)
    return FileResponse(p, media_type="text/javascript")
