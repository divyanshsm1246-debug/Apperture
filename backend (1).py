from fastapi import FastAPI, Request, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import json
import os
import time
import re
import random
import asyncio
from datetime import datetime, timezone
from typing import Dict

# --- FIREBASE ADMIN (For Real Google Auth on Backend) ---
try:
    import firebase_admin
    from firebase_admin import credentials, auth
    # Initializes with the specific project ID provided
    if not firebase_admin._apps:
        # Note: For full production security, download your Service Account JSON from Firebase Console
        # (Project Settings -> Service Accounts -> Generate new private key)
        # and set the GOOGLE_APPLICATION_CREDENTIALS environment variable on your server.
        firebase_admin.initialize_app(options={'projectId': 'apperture-4889e'})
    FIREBASE_ADMIN_AVAILABLE = True
    print("Firebase Admin SDK initialized successfully for project 'apperture-4889e'.")
except ImportError:
    FIREBASE_ADMIN_AVAILABLE = False
    print("Notice: 'firebase-admin' package not found. Run 'pip install firebase-admin' to enable real Google Auth validation.")

app = FastAPI(title="Apperture Real-Time Backend API")

# Allow frontend to communicate with this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to frontend domain in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

DB_FILE = "database.json"

# --- CONSTANTS & CONFIG ---
ADMIN_SECRET_KEY = "divyanshgupta.Apperture.Elderweb.com@passkey142682920252717"
CO_ADMIN_KEYS = [
    "naman.ronaldo.siue.12344", "sanyam.bro.passkey@!#$%#&^%((}",
    "Passkey.co.admin@apperture.siuuee._12221117", "Passkey.Cricket.madhav.bro.@@paskkey-siuuuuue.",
    "Siiiiiiue.passkey#@@big.dawgs//@!234455.Tehlka-Omelelte."
]

COUPONS = {
    "APERTURE-GOLD-PASS-R8T63": {"s": 1000, "g": 20, "t": 10}, "APERTURE-PREMIUM-GIFT-X1Z86": {"s": 1000, "g": 20, "t": 10},
    "APERTURE-ELITE-SAVINGS-Z9K27": {"s": 1000, "g": 20, "t": 10}, "APERTURE-ULTRA-SALE-M9K32": {"s": 1000, "g": 20, "t": 10},
    "APERTURE-PRIME-LISTING-K9Q71": {"s": 750, "g": 12, "t": 6}, "APERTURE-REWARD-PRIME-X7L92": {"s": 750, "g": 12, "t": 6},
    "APERTURE-VISION-BONUS-Q4M81": {"s": 500, "g": 7, "t": 3}, "APERTURE-FAST-CASHBACK-T7R18": {"s": 500, "g": 7, "t": 3}
}

BAD_WORDS = ["badword1", "badword2", "fuck", "shit", "bitch", "ass", "bastard", "bhenchod", "madarchod", "chutiya", "randi", "bhosdike", "puta", "mierda", "pendejo"]
FRAUD_WORDS = ["scam", "whatsapp", "telegram", "paytm", "gpay"]

# --- REAL-TIME WEBSOCKET MANAGER ---
class ConnectionManager:
    def __init__(self):
        # Maps user_id -> WebSocket connection
        self.active_connections: Dict[str, WebSocket] = {}

    async def connect(self, websocket: WebSocket, user_id: str):
        await websocket.accept()
        self.active_connections[user_id] = websocket
        # Broadcast to everyone that a user came online
        await self.broadcast({"type": "presence", "status": "online", "user_id": user_id})

    def disconnect(self, user_id: str):
        if user_id in self.active_connections:
            del self.active_connections[user_id]
            # Fire and forget disconnect broadcast
            asyncio.create_task(self.broadcast({"type": "presence", "status": "offline", "user_id": user_id}))

    async def send_personal_message(self, message: dict, user_id: str):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_json(message)

    async def broadcast(self, message: dict):
        for connection in self.active_connections.values():
            try:
                await connection.send_json(message)
            except:
                pass # Handle dead connections safely

manager = ConnectionManager()

# --- DATABASE LOGIC ---
def load_db():
    if not os.path.exists(DB_FILE):
        return {
            "users": {}, "listings": [], "cart": {}, "chats": [], 
            "messages": [], "global_chat": [], "notifications": [], 
            "supremeAds": [], "rewards": {}, "votes": {}
        }
    with open(DB_FILE, "r") as f:
        return json.load(f)

def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=4)

def now_ms():
    return int(time.time() * 1000)

def get_utc_date(ts_ms):
    return datetime.fromtimestamp(ts_ms / 1000.0, tz=timezone.utc).date()

# --- AI FRAUD FILTER ---
def check_fraud(text: str):
    if not text:
        return {"isFraud": False, "isVulgar": False}
    text_lower = text.lower()
    is_vulgar = any(re.search(r'\b' + re.escape(w) + r'\b', text_lower) for w in BAD_WORDS)
    is_fraud = any(w in text_lower for w in FRAUD_WORDS)
    return {"isFraud": is_fraud, "isVulgar": is_vulgar}

# --- WEBSOCKET ENDPOINT (The Real-Time Hub) ---
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    await manager.connect(websocket, user_id)
    try:
        while True:
            # Wait for any incoming real-time messages from this client
            data = await websocket.receive_json()
            
            # Example: Handle a live typing indicator
            if data.get("type") == "typing":
                recipient = data.get("recipient_id")
                await manager.send_personal_message({
                    "type": "typing_indicator",
                    "from_user": user_id
                }, recipient)
                
    except WebSocketDisconnect:
        manager.disconnect(user_id)


# --- REST ENDPOINTS ---

@app.post("/auth")
async def auth_route(request: Request):
    body = await request.json()
    db = load_db()
    
    is_google_auth = body.get("isGoogleAuth", False)
    
    # --- 1. REAL GOOGLE AUTHENTICATION FLOW ---
    if is_google_auth:
        id_token = body.get("id_token")
        email = body.get("email")
        username = body.get("username")
        profile_pic = body.get("profile_pic", "")
        
        # Verify cryptographic token if Firebase Admin is available
        if FIREBASE_ADMIN_AVAILABLE and id_token:
            try:
                decoded_token = auth.verify_id_token(id_token)
                email = decoded_token.get("email", email) # Overwrite with verified email
            except Exception as e:
                raise HTTPException(status_code=401, detail=f"Google Token Verification Failed: {str(e)}")
        
        # Find existing user or create a new profile securely
        existing_id = next((k for k, v in db["users"].items() if v.get("email") == email or v.get("name") == username), None)
        
        if existing_id:
            return {"status": "logged_in", "reward": "Google Sign-In Successful!", "user_id": existing_id}
        else:
            new_id = f"user_{now_ms()}"
            db["users"][new_id] = {
                "id": new_id, "name": username, "email": email, "password": "OAUTH_SECURE", 
                "address": "Verified via Google", "mobile": "N/A", "role": "both", "feedback": "",
                "isAdmin": False, "isCoAdmin": False, "stars": 10, "gems": 1, "tickets": 1,
                "profilePic": profile_pic, "usedCoupons": []
            }
            save_db(db)
            return {"status": "created", "reward": "Google Account Linked! Profile Created.", "user_id": new_id}

    # --- 2. STANDARD EMAIL/PASSWORD FLOW ---
    username = body.get("username")
    password = body.get("password")
    referral = body.get("referral")
    is_login = body.get("isLogin", False)
    
    is_admin, is_co_admin = False, False
    reward_msg = None
    start_stars, start_gems, start_tickets = 10, 1, 1
    
    if referral:
        if referral == ADMIN_SECRET_KEY:
            is_admin = True
        elif referral in CO_ADMIN_KEYS:
            is_co_admin = True
        elif referral in COUPONS:
            raise HTTPException(status_code=400, detail="Please redeem coupon codes inside the app via Edit Profile.")
        else:
            referrer_id = next((k for k, v in db["users"].items() if v["name"] == referral), None)
            if referrer_id:
                db["users"][referrer_id]["stars"] += 50
                db["users"][referrer_id]["gems"] += 2
                notif = {
                    "type": "sys", "message": f"Your friend {username} joined! +50 Stars and +2 Gems.",
                    "recipient_id": referrer_id, "expiresAt": now_ms() + (86400000 * 7)
                }
                db["notifications"].append(notif)
                # REAL-TIME: Notify the referrer instantly if they are online!
                await manager.send_personal_message({"type": "notification", "data": notif}, referrer_id)
                start_stars += 50
                start_gems += 2
                reward_msg = "Profile Created! Friend Referral applied: +50 Stars & +2 Gems bonus!"
            else:
                raise HTTPException(status_code=400, detail="Invalid Referral Code or Friend's Username.")

    existing_id = next((k for k, v in db["users"].items() if v["name"] == username), None)

    if is_login:
        if not existing_id: raise HTTPException(status_code=404, detail="User not found.")
        if db["users"][existing_id]["password"] != password: raise HTTPException(status_code=401, detail="Incorrect Password.")
        return {"status": "logged_in", "reward": "Welcome Back!", "user_id": existing_id}
    else:
        if existing_id: raise HTTPException(status_code=400, detail="Username already taken.")
        new_id = f"user_{now_ms()}"
        db["users"][new_id] = {
            "id": new_id, "name": username, "password": password, "address": body.get("address"), 
            "mobile": body.get("mobile"), "role": body.get("role"), "feedback": body.get("feedback"),
            "isAdmin": is_admin, "isCoAdmin": is_co_admin, "stars": start_stars, "gems": start_gems, "tickets": start_tickets,
            "profilePic": body.get("profile_pic", ""), "usedCoupons": []
        }
        save_db(db)
        return {"status": "created", "reward": reward_msg or "Profile Created! Free ticket awarded.", "user_id": new_id}

@app.put("/profile")
async def update_profile(request: Request):
    # (Same as before)
    body = await request.json()
    user_id = request.headers.get("user-id")
    db = load_db()
    user = db["users"].get(user_id)
    if not user: raise HTTPException(status_code=401, detail="Unauthorized")

    reward_msg = None
    coupon = body.get("coupon", "").strip()
    
    if coupon and coupon in COUPONS:
        if coupon in user.get("usedCoupons", []):
            raise HTTPException(status_code=400, detail="You have already redeemed this coupon code.")
        reward = COUPONS[coupon]
        user["stars"] += reward["s"]
        user["gems"] += reward["g"]
        user["tickets"] += reward["t"]
        user.setdefault("usedCoupons", []).append(coupon)
        reward_msg = f"Coupon Redeemed! +{reward['s']} Stars, +{reward['g']} Gems, +{reward['t']} Tickets"
    elif coupon:
        raise HTTPException(status_code=400, detail="Invalid Coupon Code")
        
    new_name = body.get("name")
    if new_name == ADMIN_SECRET_KEY: new_name = "Divyansh Gupta"; user["isAdmin"] = True
    elif new_name in CO_ADMIN_KEYS: new_name = "Co-Admin"; user["isCoAdmin"] = True
    
    if new_name: user["name"] = new_name
    if body.get("address"): user["address"] = body.get("address")
    if body.get("profile_pic"): user["profilePic"] = body.get("profile_pic")
    
    save_db(db)
    # Broadcast profile update so active chats see new name/pic
    await manager.broadcast({"type": "profile_update", "user_id": user_id, "name": user["name"]})
    return {"status": "updated", "reward": reward_msg}

@app.post("/sync")
async def sync_data(request: Request):
    # (Standard Sync logic for initial load, unchanged)
    body = await request.json()
    user_id = body.get("userId")
    if not user_id: user_id = request.headers.get("user-id", "Guest")
    
    db = load_db()
    now = now_ms()
    
    db["supremeAds"] = [a for a in db["supremeAds"] if a.get("endTime", 0) > now]
    db["notifications"] = [n for n in db["notifications"] if n.get("expiresAt", 0) > now]
    
    for uid, claim in db["rewards"].items():
        if claim.get("streak", 0) > 0:
            d_last = get_utc_date(claim["lastClaimed"])
            d_today = get_utc_date(now)
            if (d_today - d_last).days > 1:
                claim["streak"] = 0

    save_db(db)

    my_notifs = [n for n in db["notifications"] if n.get("recipient_id") == user_id or (not n.get("recipient_id") and n.get("type") in ['admin_alert', 'sys']) or (n.get("type") == 'user_ad' and not n.get("recipient_id") and n.get("scheduledFor", 0) <= now)]
    my_scheduled = [n for n in db["notifications"] if n.get("poster_id") == user_id and n.get("type") == "user_ad"]
    my_supreme = [a for a in db["supremeAds"] if a.get("buyerId") == user_id]
    
    profile = db["users"].get(user_id, {"name": "Guest", "isAdmin": False, "isCoAdmin": False, "stars": 0, "gems": 0, "tickets": 0})
    rewards = db["rewards"].get(user_id, {"streak": 0, "lastClaimed": 0})

    return {
        "listings": db["listings"],
        "notifications": my_notifs,
        "my_scheduled": my_scheduled,
        "my_supreme": my_supreme,
        "cart": db["cart"].get(user_id, []),
        "chats": [c for c in db["chats"] if user_id in c.get("participants", [])],
        "supreme_ads": db["supremeAds"],
        "profile": profile,
        "rewards": rewards
    }

@app.post("/listings")
async def create_listing(request: Request):
    body = await request.json()
    user_id = request.headers.get("user-id")
    db = load_db()
    user = db["users"].get(user_id)
    if not user: raise HTTPException(status_code=401, detail="Unauthorized")

    new_id = now_ms()
    listing = {
        "id": new_id, "sellerId": user_id, "sellerName": user["name"],
        "title": body.get("title"), "category": body.get("category"),
        "price": body.get("price"), "currency": body.get("currency"),
        "description": body.get("description"), "condition": body.get("condition"),
        "address": body.get("address"), "image_data": body.get("image_data"), 
        "images": body.get("images", []), "isMagnetic": body.get("isMagnetic", False), 
        "starsCount": 0, "timestamp": new_id
    }
    db["listings"].append(listing)
    save_db(db)

    # REAL-TIME: Instantly push the new listing to everyone online!
    await manager.broadcast({"type": "new_listing", "data": listing})

    return {"status": "created", "id": new_id}

@app.post("/chats")
async def create_chat(request: Request):
    body = await request.json()
    user_id = request.headers.get("user-id")
    db = load_db()
    user = db["users"].get(user_id)

    new_chat = {
        "id": now_ms(),
        "participants": body.get("participants", []),
        "names": body.get("names", []),
        "last_message": body.get("lastMessage", ""),
        "last_updated": now_ms()
    }
    db["chats"].append(new_chat)
    save_db(db)

    recipient = next((p for p in new_chat["participants"] if p != user_id), None)
    if recipient:
        # REAL-TIME: Instantly alert the seller that a chat started
        await manager.send_personal_message({
            "type": "new_chat", "chat_data": new_chat, "message": f"New chat from {user['name']}"
        }, recipient)

    return {"id": new_chat["id"]}

@app.get("/chats/{chat_id}/messages")
async def get_messages(chat_id: int):
    db = load_db()
    return [m for m in db["messages"] if m["chatId"] == chat_id]

@app.post("/chats/{chat_id}/messages")
async def send_message(chat_id: int, request: Request):
    body = await request.json()
    user_id = request.headers.get("user-id")
    db = load_db()
    user = db["users"].get(user_id)

    new_msg = {"chatId": chat_id, "text": body.get("text"), "sender_id": user_id, "timestamp": now_ms()}
    db["messages"].append(new_msg)
    
    chat = next((c for c in db["chats"] if c["id"] == chat_id), None)
    if chat:
        chat["last_message"] = body.get("text")
        chat["last_updated"] = now_ms()
        recipient = next((p for p in chat["participants"] if p != user_id), None)
        
        # REAL-TIME: Instantly send the message to the recipient's screen
        if recipient:
            await manager.send_personal_message({
                "type": "chat_message", "chat_id": chat_id, "message_data": new_msg
            }, recipient)

    save_db(db)
    return {"status": "sent"}

@app.post("/notifications")
async def handle_notifications(request: Request):
    body = await request.json()
    user_id = request.headers.get("user-id")
    db = load_db()
    now = now_ms()
    
    notif_type = body.get("type")
    
    if notif_type == 'admin_alert':
        new_notif = {**body, "scheduledFor": now, "expiresAt": now + 86400000}
        db["notifications"].append(new_notif)
        save_db(db)
        # REAL-TIME: Instantly pop up the admin alert on EVERY connected device!
        await manager.broadcast({"type": "global_alert", "data": new_notif})
        return {"status": "sent"}
        
    elif notif_type == 'user_ad' and not body.get("recipient_id"):
        slot_duration = 30 * 60 * 1000
        active_future = [n for n in db["notifications"] if n.get("type") == 'user_ad' and not n.get("recipient_id") and n.get("expiresAt", 0) > now]
        unique_times = sorted(list(set(n["scheduledFor"] for n in active_future)))
        
        found_time, slot_index, sub_slot = None, 0, 'A'
        
        for i, t in enumerate(unique_times):
            count = sum(1 for n in active_future if n["scheduledFor"] == t)
            if count < 2:
                found_time = t
                slot_index = i + 1
                sub_slot = 'B'
                break
                
        if found_time is None:
            found_time = now + slot_duration if not unique_times else unique_times[-1] + slot_duration
            slot_index = len(unique_times) + 1
            sub_slot = 'A'
            
        new_notif = {**body, "scheduledFor": found_time, "expiresAt": found_time + slot_duration, "poster_id": user_id, "slotIndex": slot_index, "subSlot": sub_slot}
        db["notifications"].append(new_notif)
        save_db(db)
        return {"status": "booked", "scheduledAt": found_time, "slotIndex": slot_index, "subSlot": sub_slot}

# --- Catch-all to prevent 404s during dev ---
@app.get("/{path_name:path}")
@app.post("/{path_name:path}")
@app.put("/{path_name:path}")
@app.delete("/{path_name:path}")
async def catch_all(path_name: str):
    return {"message": f"Reached /{path_name}", "status": "success"}