import os
import json
import uuid
import threading
import websocket
from flask import Flask, jsonify, request
from flask_cors import CORS
from flask_socketio import SocketIO
from dotenv import load_dotenv
import time
import datetime

# --------------------
# ENV Variablen laden
# --------------------
load_dotenv()

# Streamer 1
SE_TWITCH_TOKEN  = os.getenv("SE_TWITCH_TOKEN")
SE_KICK_TOKEN    = os.getenv("SE_KICK_TOKEN")

# Streamer 2 (optional)
SE2_TWITCH_TOKEN = os.getenv("SE2_TWITCH_TOKEN")

# Kick (optional für Streamer1)
KICK_APP_KEY     = os.getenv("KICK_APP_KEY")
KICK_CLUSTER     = os.getenv("KICK_CLUSTER")
KICK_CHATROOM_ID = os.getenv("KICK_CHATROOM_ID")

# print(f"[DEBUG] SE_TWITCH_TOKEN:  {bool(SE_TWITCH_TOKEN)}")
# print(f"[DEBUG] SE_KICK_TOKEN:    {bool(SE_KICK_TOKEN)}")
# print(f"[DEBUG] SE2_TWITCH_TOKEN: {bool(SE2_TWITCH_TOKEN)}")
# print(f"[DEBUG] KICK_APP_KEY:     {KICK_APP_KEY}")
# print(f"[DEBUG] KICK_CLUSTER:     {KICK_CLUSTER}")
# print(f"[DEBUG] KICK_CHATROOM_ID: {KICK_CHATROOM_ID}")

# --------------------
# Config laden
# --------------------
with open("config.json", "r", encoding="utf-8") as f:
    CONFIG1 = json.load(f)

CONFIG2 = None
if SE2_TWITCH_TOKEN:  # nur laden wenn zweiter Token vorhanden
    try:
        with open("config2.json", "r", encoding="utf-8") as f:
            CONFIG2 = json.load(f)
    except FileNotFoundError:
        print("[WARN] SE2_TWITCH_TOKEN ist gesetzt, aber config2.json fehlt!")

# --------------------
# Flask + SocketIO Setup
# --------------------
app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# --------------------
# Timer Variablen
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
        print("[STATE] Fehler beim Speichern:", e)

def load_state():
    global remaining, paused
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
                remaining = state.get("remaining", remaining)
                paused = state.get("paused", paused)
                print(f"[STATE] Wiederhergestellt: {remaining//60} Minuten, paused={paused}")
        except Exception as e:
            print("[STATE] Fehler beim Laden:", e)

def log_event(platform, data):
    """Schreibt alle RAW Events zusätzlich in ein Logfile"""
    try:
        ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] [{platform}] RAW EVENT: {json.dumps(data, ensure_ascii=False)}\n")
    except Exception as e:
        print("[LOG] Fehler beim Schreiben in events.log:", e)

# beim Start vorhandenen State laden
load_state()

# --------------------
# Timer Loop
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
# Events verarbeiten
# --------------------
def handle_event(platform, data, config):
    global remaining
    minutes_to_add = 0

    # RAW Event ins Logfile + Konsole
    print(f"[{platform}] RAW EVENT: {json.dumps(data, indent=2)}")
    log_event(platform, data)

    # Twitch Subs (inkl. Prime & Multi-Month via amount)
    if data.get("type") == "subscriber":
        tier_raw = str(data.get("data", {}).get("tier", "1000")).lower()
        amount = int(data.get("data", {}).get("amount", 1))  # Multi-Month / Anzahl

        if tier_raw in ["1000", "prime"]:
            tier_minutes = config["twitch"]["sub_t1"]
        elif tier_raw == "2000":
            tier_minutes = config["twitch"]["sub_t2"]
        elif tier_raw == "3000":
            tier_minutes = config["twitch"]["sub_t3"]
        else:
            tier_minutes = config["twitch"]["sub_t1"]  # Fallback auf T1

        minutes_to_add = tier_minutes * max(1, amount)

    # Bits
    elif data.get("type") == "cheer":
        bits = int(data.get("data", {}).get("amount", 0))
        minutes_to_add = (bits // 100) * config["twitch"]["bits_per_100"]

    # Gifted Subs
    elif data.get("type") == "communityGiftPurchase":
        gift_amount = int(data.get("data", {}).get("amount", 1))
        tier_raw = str(data.get("data", {}).get("tier", "1000")).lower()
        if tier_raw in ["1000", "prime"]:
            minutes_to_add = gift_amount * config["twitch"]["sub_t1"]
        elif tier_raw == "2000":
            minutes_to_add = gift_amount * config["twitch"]["sub_t2"]
        elif tier_raw == "3000":
            minutes_to_add = gift_amount * config["twitch"]["sub_t3"]

    # Donations über Tipeee
    elif data.get("type") == "donation" and "tipeee" in config:
        amount = float(data.get("amount", 0))
        minutes_to_add = int(amount * config["tipeee"]["minutes_per_eur"])

    # Donations über StreamElements
    elif data.get("type") == "tip" and "streamelements" in config:
        amount = float(data.get("data", {}).get("amount", 0))
        minutes_to_add = int(amount * config["streamelements"]["minutes_per_eur"])

    # Kick Subs
    elif data.get("type") == "subscriber" and "kick" in platform.lower():
        if "kick" in config:
            minutes_to_add = config["kick"]["sub"]

    # Kick Gifts
    elif data.get("type") == "kick_gift":
        if "kick" in config:
            amount = int(data.get("amount", 0))
            minutes_to_add = (amount // 100) * config["kick"]["kicks_per_100"]

    if minutes_to_add > 0:
        with lock:
            remaining += minutes_to_add * 60
            save_state()
            new_state = {"remaining": remaining, "paused": paused}
        print(f"[{platform}] +{minutes_to_add} Minuten → {remaining//60} min gesamt")
        socketio.start_background_task(socketio.emit, "timer_update", new_state)

# --------------------
# StreamElements WS mit Auto-Reconnect
# --------------------
def start_client(name, token, config):
    url = "wss://astro.streamelements.com"

    def run_ws():
        def on_open(ws):
            print(f"[{name}] Verbunden")

        def on_message(ws, message):
            msg = json.loads(message)
            if msg.get("type") == "welcome":
                subscribe(ws, "channel.activities", token, name)
            elif msg.get("type") == "message":
                data = msg.get("data")
                handle_event(name, data, config)

        def on_error(ws, error):
            print(f"[{name}] Fehler: {error}")

        def on_close(ws, close_status_code, close_msg):
            print(f"[{name}] Verbindung geschlossen, Reconnect in 1s")
            time.sleep(1)
            run_ws()

        def subscribe(ws, topic, token, name):
            sub = {
                "type": "subscribe",
                "nonce": str(uuid.uuid4()),
                "data": {"topic": topic, "token": token, "token_type": "jwt"},
            }
            ws.send(json.dumps(sub))
            print(f"[{name}] subscribed zu {topic}")

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
# Flask Routes
# --------------------
@app.route("/")
def index():
    return "Subathon Timer läuft!"

@app.route("/rewards")
def rewards():
    streamer = request.args.get("streamer", "1")
    if streamer == "1":
        cfg = CONFIG1
    elif streamer == "2" and CONFIG2:
        cfg = CONFIG2
    else:
        return jsonify({"error": "Streamer nicht verfügbar"}), 400

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
        return jsonify({"error": "delta oder minusdelta fehlt"}), 400

    try:
        if delta_str is not None:
            delta = int(delta_str)
            if delta < 0:
                return jsonify({"error": "delta darf nicht negativ sein, nutze minusdelta"}), 400
        else:
            delta = -int(minusdelta_str)
            if delta > 0:
                return jsonify({"error": "minusdelta darf nicht negativ sein"}), 400
    except ValueError:
        return jsonify({"error": "delta/minusdelta muss Zahl sein"}), 400

    with lock:
        remaining = max(0, remaining + delta * 60)
        save_state()
        new_state = {"remaining": remaining, "paused": paused}

    socketio.start_background_task(socketio.emit, "timer_update", new_state)
    print(f"[MANUAL] {delta:+} Minuten → {remaining//60} min gesamt")

    return jsonify(new_state)

# --------------------
# Main Start
# --------------------
if __name__ == "__main__":
    socketio.start_background_task(timer_loop)

    if SE_TWITCH_TOKEN:
        start_client("SE-Twitch1", SE_TWITCH_TOKEN, CONFIG1)
    if SE2_TWITCH_TOKEN and CONFIG2:
        start_client("SE-Twitch2", SE2_TWITCH_TOKEN, CONFIG2)
    if SE_KICK_TOKEN:
        start_client("SE-Kick", SE_KICK_TOKEN, CONFIG1)

    print("[APP] Subathon Timer läuft auf http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000)
