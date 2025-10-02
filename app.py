import os
import json
import uuid
import threading
import websocket
import socketio as socketio_client
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO
from dotenv import load_dotenv
import time
import datetime
import re

# --------------------
# Helper function for timestamp
# --------------------
def ts():
    return datetime.datetime.now().strftime("%d.%m.%Y - %H:%M")

# --------------------
# Load ENV variables
# --------------------
load_dotenv()

# Streamer 1
SE_TWITCH_TOKEN  = os.getenv("SE_TWITCH_TOKEN")
SE_KICK_TOKEN    = os.getenv("SE_KICK_TOKEN")
KICK_APP_KEY     = os.getenv("KICK_APP_KEY")
KICK_CLUSTER     = os.getenv("KICK_CLUSTER")
KICK_CHATROOM_ID = os.getenv("KICK_CHATROOM_ID")
TIPEEE_API_KEY   = os.getenv("TIPEEE_API_KEY")

# Streamer 2
SE2_TWITCH_TOKEN  = os.getenv("SE2_TWITCH_TOKEN")
SE2_KICK_TOKEN    = os.getenv("SE2_KICK_TOKEN")
KICK_APP_KEY2     = os.getenv("KICK_APP_KEY2")
KICK_CLUSTER2     = os.getenv("KICK_CLUSTER2")
KICK_CHATROOM_ID2 = os.getenv("KICK_CHATROOM_ID2")
TIPEEE_API_KEY2   = os.getenv("TIPEEE_API_KEY2")

# --------------------
# Load config
# --------------------
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG1 = json.load(f)

CONFIG2 = None
if SE2_TWITCH_TOKEN:  # only load if token for Streamer 2 is present
    try:
        with open("config2.json", "r", encoding="utf-8") as f:
            CONFIG2 = json.load(f)
    except FileNotFoundError:
        print(f"[{ts()}] [WARN] SE2_TWITCH_TOKEN is set, but config2.json is missing!")

# --------------------
# Flask + SocketIO setup
# --------------------
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --------------------
# Timer variables
# --------------------
remaining = CONFIG1["timer"]["start_minutes"] * 60
paused = False
lock = threading.Lock()

STATE_FILE = "state.json"
LOG_FILE = "events.log"

def save_state():
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump({"remaining": remaining, "paused": paused}, f)
    except Exception as e:
        print(f"[{ts()}] [STATE] Error while saving:", e)

def load_state():
    global remaining, paused
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                remaining = state.get("remaining", remaining)
                paused = state.get("paused", paused)
                print(f"[{ts()}] [STATE] Restored: {remaining//60} minutes, paused={paused}")
        except Exception as e:
            print(f"[{ts()}] [STATE] Error while loading:", e)

def log_event(platform, data):
    """Write all RAW events additionally into a logfile"""
    try:
        ts_str = ts()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts_str}] [{platform}] RAW EVENT: {json.dumps(data, ensure_ascii=False)}\n")
    except Exception as e:
        print(f"[{ts()}] [LOG] Error while writing to events.log:", e)

# Load existing state on startup
load_state()

# --------------------
# Timer loop
# --------------------
def timer_loop():
    global remaining
    while True:
        with lock:
            if not paused and remaining > 0:
                remaining -= 1
            socketio.emit("timer_update", {"remaining": remaining, "paused": paused})
        socketio.sleep(1)

# --------------------
# Handle events
# --------------------
def handle_event(platform, data, config):
    global remaining
    minutes_to_add = 0

    # RAW event to logfile + console
    print(f"[{ts()}] [{platform}] RAW EVENT: {json.dumps(data, indent=2)}")
    log_event(platform, data)

    # Twitch subs (incl. Prime & multi-month via amount)
    if data.get("type") == "subscriber":
        tier_raw = str(data.get("data", {}).get("tier", "1000")).lower()
        amount = int(data.get("data", {}).get("amount", 1))  # multi-month / amount

        if tier_raw in ["1000", "prime"]:
            tier_minutes = config["twitch"]["sub_t1"]
        elif tier_raw == "2000":
            tier_minutes = config["twitch"]["sub_t2"]
        elif tier_raw == "3000":
            tier_minutes = config["twitch"]["sub_t3"]
        else:
            tier_minutes = config["twitch"]["sub_t1"]  # fallback to T1

        minutes_to_add = tier_minutes * max(1, amount)

    # Bits
    elif data.get("type") == "cheer":
        bits = int(data.get("data", {}).get("amount", 0))
        minutes_to_add = (bits // 100) * config["twitch"]["bits_per_100"]

    # Gifted subs
    elif data.get("type") == "communityGiftPurchase":
        gift_amount = int(data.get("data", {}).get("amount", 1))
        tier_raw = str(data.get("data", {}).get("tier", "1000")).lower()
        if tier_raw in ["1000", "prime"]:
            minutes_to_add = gift_amount * config["twitch"]["sub_t1"]
        elif tier_raw == "2000":
            minutes_to_add = gift_amount * config["twitch"]["sub_t2"]
        elif tier_raw == "3000":
            minutes_to_add = gift_amount * config["twitch"]["sub_t3"]

    # Donations via Tipeee (only donation type is used)
    elif data.get("type") == "donation" and "tipeee" in config:
        amount = float(data.get("amount", 0))
        minutes_to_add = int(amount * config["tipeee"]["minutes_per_eur"])

    # Donations via StreamElements
    elif data.get("type") == "tip" and "streamelements" in config:
        amount = float(data.get("data", {}).get("amount", 0))
        minutes_to_add = int(amount * config["streamelements"]["minutes_per_eur"])

    # Kick subs (via SE)
    elif data.get("type") == "subscriber" and "kick" in platform.lower():
        if "kick" in config:
            minutes_to_add = config["kick"]["sub"]

    # Kick gifts (via Kick Chat)
    elif data.get("type") == "kick_gift":
        if "kick" in config:
            amount = int(data.get("amount", 0))
            # Only full 100 KICK blocks count (minimum threshold)
            minutes_to_add = (amount // 100) * config["kick"]["kicks_per_100"]

    if minutes_to_add > 0:
        with lock:
            remaining += minutes_to_add * 60
            save_state()
            new_state = {"remaining": remaining, "paused": paused}
        print(f"[{ts()}] [{platform}] +{minutes_to_add} minutes → {remaining//60} min total")
        socketio.start_background_task(socketio.emit, "timer_update", new_state)

# --------------------
# StreamElements WS with auto-reconnect
# --------------------
def start_client(name, token, config):
    url = "wss://astro.streamelements.com"

    def run_ws():
        def on_open(ws):
            print(f"[{ts()}] [{name}] Connected")

        def on_message(ws, message):
            msg = json.loads(message)
            if msg.get("type") == "welcome":
                subscribe(ws, "channel.activities", token, name)
            elif msg.get("type") == "message":
                data = msg.get("data")
                handle_event(name, data, config)

        def on_error(ws, error):
            print(f"[{ts()}] [{name}] Error: {error}")

        def on_close(ws, close_status_code, close_msg):
            print(f"[{ts()}] [{name}] Connection closed, reconnecting in 1s")
            time.sleep(1)
            run_ws()

        def subscribe(ws, topic, token, name):
            sub = {
                "type": "subscribe",
                "nonce": str(uuid.uuid4()),
                "data": {"topic": topic, "token": token, "token_type": "jwt"},
            }
            ws.send(json.dumps(sub))
            print(f"[{ts()}] [{name}] Subscribed to {topic}")

        ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
        )
        ws.run_forever()

    threading.Thread(target=run_ws, daemon=True).start()

# --------------------
# Kick Chat Listener (for Kick Gifts via Chat)
# --------------------
def connect_kick_chat(name, app_key, cluster, chatroom_id, config):
    if not app_key or not cluster or not chatroom_id:
        print(f"[{ts()}] [INFO] KickChat for {name} skipped (missing ENV)")
        return

    url = f"wss://ws-{cluster}.pusher.com/app/{app_key}?protocol=7"

    def on_open(ws):
        print(f"[{ts()}] [{name}] KickChat connected")
        ws.send(json.dumps({
            "event": "pusher:subscribe",
            "data": {"channel": f"chatrooms.{chatroom_id}.v2"}
        }))

    def on_message(ws, message):
        try:
            payload = json.loads(message)
            if payload.get("event") == "App\\Events\\ChatMessageEvent":
                inner = json.loads(payload["data"])  # data is a JSON string
                text = inner.get("content", "")
                # RAW Log for KickChat
                print(f"[{ts()}] [{name}] RAW CHAT EVENT: {json.dumps(inner, indent=2)}")
                log_event(name, inner)
                # Detect Kick Gift amounts
                m = re.search(r"gifted\s+(\d+)\s+KICK", text, re.IGNORECASE)
                if m:
                    amount = int(m.group(1))
                    fake_event = {"type": "kick_gift", "amount": amount}
                    handle_event(name, fake_event, config)
        except Exception as e:
            print(f"[{ts()}] [{name}] KickChat parse error:", e)

    def on_close(ws, *a):
        print(f"[{ts()}] [{name}] KickChat closed, reconnect in 5s")
        time.sleep(5)
        connect_kick_chat(name, app_key, cluster, chatroom_id, config)

    def on_error(ws, error):
        print(f"[{ts()}] [{name}] KickChat error:", error)

    ws = websocket.WebSocketApp(
        url,
        on_open=on_open,
        on_message=on_message,
        on_close=on_close,
        on_error=on_error
    )
    threading.Thread(target=ws.run_forever, daemon=True).start()

# --------------------
# TipeeeStream (donations only)
# --------------------
def start_tipeee(name, api_key, config):
    """
    Connects to TipeeeStream Socket.IO and forwards only 'donation' events
    into handle_event as {'type': 'donation', 'amount': <float>}.
    """
    if not api_key:
        print(f"[{ts()}] [INFO] {name} skipped (no TIPEEE_API_KEY)")
        return

    sio = socketio_client.Client(reconnection=True)

    @sio.event
    def connect():
        print(f"[{ts()}] [{name}] Connected to Tipeee → listening for donations")

    @sio.event
    def disconnect():
        print(f"[{ts()}] [{name}] Disconnected from Tipeee")

    @sio.on("new-event")
    def on_new_event(data):
        try:
            ev = data.get("event", {})
            if ev.get("type") == "donation":
                params = ev.get("parameters", {}) if isinstance(ev.get("parameters", {}), dict) else {}
                amount = float(params.get("amount", 0))
                user = params.get("username", "Unknown")
                # Raw log
                print(f"[{ts()}] [{name}] RAW TIPEEE EVENT: {json.dumps(ev, indent=2)}")
                log_event(name, ev)
                # Forward to timer logic
                fake = {"type": "donation", "amount": amount, "user": user}
                handle_event(name, fake, config)
        except Exception as e:
            print(f"[{ts()}] [{name}] Tipeee parse error:", e)

    def run():
        url = f"https://sso.tipeeestream.com:443?access_token={api_key}"
        try:
            sio.connect(url, transports=["websocket", "polling"])
            sio.wait()
        except Exception as e:
            print(f"[{ts()}] [{name}] Tipeee connection error:", e)
            time.sleep(5)
            run()

    threading.Thread(target=run, daemon=True).start()

# --------------------
# Flask routes
# --------------------
@app.route("/")
def index():
    return "Subathon timer is running!"

@app.route("/rewards")
def rewards():
    streamer = request.args.get("streamer", "1")
    if streamer == "1":
        cfg = CONFIG1
    elif streamer == "2" and CONFIG2:
        cfg = CONFIG2
    else:
        return jsonify({"error": "Streamer not available"}), 400

    rewards_list = [
        {"name": "T 1 Sub", "minutes": cfg["twitch"]["sub_t1"]},
        {"name": "T 2 Sub", "minutes": cfg["twitch"]["sub_t2"]},
        {"name": "T 3 Sub", "minutes": cfg["twitch"]["sub_t3"]},
        {"name": "100 Bits", "minutes": cfg["twitch"]["bits_per_100"]},
    ]

    if "tipeee" in cfg:
        rewards_list.append({"name": "1 € Donation", "minutes": cfg["tipeee"]["minutes_per_eur"]})

    if "streamelements" in cfg:
        rewards_list.append({"name": "1 € Donation", "minutes": cfg["streamelements"]["minutes_per_eur"]})

    if "kick" in cfg:
        rewards_list.append({"name": "Kick Sub", "minutes": cfg["kick"]["sub"]})
        rewards_list.append({"name": "100 Kicks", "minutes": cfg["kick"]["kicks_per_100"]})

    return jsonify(rewards_list)

@app.route("/state")
def get_state():
    return jsonify({"remaining": remaining, "paused": paused})

@app.route("/pause")
def pause_timer():
    global paused
    with lock:
        paused = True
        save_state()
    return jsonify({"remaining": remaining, "paused": paused})

@app.route("/resume")
def resume_timer():
    global paused
    with lock:
        paused = False
        save_state()
    return jsonify({"remaining": remaining, "paused": paused})

@app.route("/toggle")
def toggle_timer():
    global paused
    with lock:
        paused = not paused
        save_state()
    return jsonify({"remaining": remaining, "paused": paused})

@app.route("/time")
def change_time():
    global remaining
    delta_str = request.args.get("delta")
    minusdelta_str = request.args.get("minusdelta")

    if delta_str is None and minusdelta_str is None:
        return jsonify({"error": "delta or minusdelta is missing"}), 400

    try:
        if delta_str is not None:
            delta = int(delta_str)
            if delta < 0:
                return jsonify({"error": "delta cannot be negative, use minusdelta"}), 400
        else:
            delta = -int(minusdelta_str)
            if delta > 0:
                return jsonify({"error": "minusdelta cannot be negative"}), 400
    except ValueError:
        return jsonify({"error": "delta/minusdelta must be a number"}), 400

    with lock:
        remaining = max(0, remaining + delta * 60)
        save_state()
        new_state = {"remaining": remaining, "paused": paused}

    socketio.start_background_task(socketio.emit, "timer_update", new_state)
    print(f"[{ts()}] [MANUAL] {delta:+} minutes → {remaining//60} min total")

    return jsonify(new_state)

# --------------------
# Main start
# --------------------
if __name__ == "__main__":
    socketio.start_background_task(timer_loop)

    # Streamer 1 (SE: Twitch/Kick)
    if SE_TWITCH_TOKEN:
        start_client("SE-Twitch1", SE_TWITCH_TOKEN, CONFIG1)
    if SE_KICK_TOKEN:
        start_client("SE-Kick1", SE_KICK_TOKEN, CONFIG1)
    # Kick Gifts via Kick Chat
    connect_kick_chat("KickChat1", KICK_APP_KEY, KICK_CLUSTER, KICK_CHATROOM_ID, CONFIG1)
    # Tipeee donations
    if TIPEEE_API_KEY:
        start_tipeee("Tipeee1", TIPEEE_API_KEY, CONFIG1)

    # Streamer 2
    if SE2_TWITCH_TOKEN and CONFIG2:
        start_client("SE-Twitch2", SE2_TWITCH_TOKEN, CONFIG2)
    if SE2_KICK_TOKEN and CONFIG2:
        start_client("SE-Kick2", SE2_KICK_TOKEN, CONFIG2)
    if CONFIG2:
        connect_kick_chat("KickChat2", KICK_APP_KEY2, KICK_CLUSTER2, KICK_CHATROOM_ID2, CONFIG2)
    if TIPEEE_API_KEY2 and CONFIG2:
        start_tipeee("Tipeee2", TIPEEE_API_KEY2, CONFIG2)

    print(f"[{ts()}] [APP] Subathon timer running at http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000)
