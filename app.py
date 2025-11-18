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

# Debug setting (show/hide RAW events)
DEBUG_EVENTS = os.getenv("DEBUG", "1") == "1"

# Streamer 1
LABEL_STREAMER1 = os.getenv("LABEL_STREAMER1", "Streamer1")
SE_TWITCH_TOKEN  = os.getenv("SE_TWITCH_TOKEN")
SE_KICK_TOKEN    = os.getenv("SE_KICK_TOKEN")
KICK_APP_KEY     = os.getenv("KICK_APP_KEY")
KICK_CLUSTER     = os.getenv("KICK_CLUSTER")
KICK_CHATROOM_ID = os.getenv("KICK_CHATROOM_ID")
TIPEEE_API_KEY   = os.getenv("TIPEEE_API_KEY")

# Twitch IRC (Streamer 1)
TWITCH_IRC_TOKEN   = os.getenv("TWITCH_IRC_TOKEN")
TWITCH_IRC_NICK    = os.getenv("TWITCH_IRC_NICK")
TWITCH_IRC_CHANNEL = os.getenv("TWITCH_IRC_CHANNEL")  # ohne '#'

# Streamer 2
LABEL_STREAMER2 = os.getenv("LABEL_STREAMER2", "Streamer2")
SE2_TWITCH_TOKEN  = os.getenv("SE2_TWITCH_TOKEN")
SE2_KICK_TOKEN    = os.getenv("SE2_KICK_TOKEN")
KICK_APP_KEY2     = os.getenv("KICK_APP_KEY2")
KICK_CLUSTER2     = os.getenv("KICK_CLUSTER2")
KICK_CHATROOM_ID2 = os.getenv("KICK_CHATROOM_ID2")
TIPEEE_API_KEY2   = os.getenv("TIPEEE_API_KEY2")

# Twitch IRC (Streamer 2)
TWITCH_IRC_TOKEN2   = os.getenv("TWITCH_IRC_TOKEN2")
TWITCH_IRC_NICK2    = os.getenv("TWITCH_IRC_NICK2")
TWITCH_IRC_CHANNEL2 = os.getenv("TWITCH_IRC_CHANNEL2")  # ohne '#'

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

# --------------------
# Happy Hour
# --------------------
HAPPY_MULTIPLIER = float(os.getenv("HAPPY_MULTIPLIER", "1"))
happy_active = False
happy_until = 0


STATE_FILE = "state.json"
LOG_FILE = "events.log"
TIME_ADD_LOG = "time_add.log"

# === GOALS ===
GOALS_FILE = "goals.json"
def load_goals():
    if not os.path.exists(GOALS_FILE):
        return {"total_minutes_supported": 0.0, "goals": []}
    try:
        with open(GOALS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            # ensure keys
            if "total_minutes_supported" not in data:
                data["total_minutes_supported"] = 0.0
            else:
                # migrate int -> float
                try:
                    data["total_minutes_supported"] = float(data["total_minutes_supported"])
                except:
                    data["total_minutes_supported"] = 0.0
            if "goals" not in data or not isinstance(data["goals"], list):
                data["goals"] = []
            for g in data["goals"]:
                g.setdefault("hours", 0)
                g.setdefault("title", "")
                g.setdefault("reached", False)
            return data
    except Exception as e:
        print(f"[{ts()}] [GOALS] Error while loading {GOALS_FILE}:", e)
        return {"total_minutes_supported": 0.0, "goals": []}

def save_goals():
    try:
        with open(GOALS_FILE, "w", encoding="utf-8") as f:
            json.dump(goals_data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print(f"[{ts()}] [GOALS] Error while saving {GOALS_FILE}:", e)

def log_goal_reached(goal):
    try:
        ts_str = ts()
        line = f"[{ts_str}] [GOAL] 🎯 Ziel erreicht: {goal['hours']} Stunden – {goal['title']}\n"
        with open(TIME_ADD_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[{ts()}] [GOALS] Error while logging goal:", e)

goals_data = load_goals()

def check_goals_reached():
    total_hours = float(goals_data.get("total_minutes_supported", 0.0)) / 60.0
    updated = False
    for goal in goals_data.get("goals", []):
        if not goal.get("reached") and total_hours >= float(goal.get("hours", 0)):
            goal["reached"] = True
            updated = True
            print(f"[{ts()}] [GOAL] 🎯 Ziel erreicht: {goal['hours']} Stunden – {goal['title']}")
            log_goal_reached(goal)
            socketio.emit("goal_reached", goal)
    if updated:
        save_goals()
# === END GOALS ===

def add_support_minutes(mins):
    """Add positive minutes (float) to total support, persist and re-check goals."""
    try:
        mins = float(mins)
    except Exception:
        return
    if mins <= 0:
        return
    with lock:
        goals_data["total_minutes_supported"] = float(goals_data.get("total_minutes_supported", 0.0)) + mins
        # slightly round to avoid floating point artifacts
        goals_data["total_minutes_supported"] = round(goals_data["total_minutes_supported"], 2)
        save_goals()
     # outside of the lock
    check_goals_reached()

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
    if not DEBUG_EVENTS:
        return  # do nothing when debug=0

    try:
        ts_str = ts()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{ts_str}] [{platform}] RAW EVENT: {json.dumps(data, ensure_ascii=False)}\n")
    except Exception as e:
        print(f"[{ts()}] [LOG] Error while writing to events.log:", e)


def log_time_add(platform, minutes_to_add, remaining_seconds, label=None):
    """Write time addition summary (same as console) to a separate logfile"""
    try:
        ts_str = ts()
        if label:
            line = f"[{ts_str}] [{platform}] {label} | +{minutes_to_add} minutes\n"
        else:
            line = f"[{ts_str}] [{platform}] +{minutes_to_add} minutes\n"
        with open(TIME_ADD_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception as e:
        print(f"[{ts()}] [LOG] Error while writing to time_add.log:", e)

# Load existing state on startup
load_state()

# --------------------
# Timer loop
# --------------------
def timer_loop():
    global remaining, happy_active, happy_until
    counter = 0
    while True:
        with lock:
            if not paused and remaining > 0:
                remaining -= 1

            # Happy Hour automatisch deaktivieren, wenn abgelaufen
            if happy_active and time.time() >= happy_until:
                happy_active = False
                happy_until = 0
                print(f"[{ts()}] [HAPPY] Happy Hour expired")

            socketio.emit("timer_update", {"remaining": remaining, "paused": paused})
            # save state every 300 seconds (= 5 minutes)
            counter += 1
            if counter >= 300:
                save_state()
                counter = 0
        socketio.sleep(1)


# --------------------
# Handle events
# --------------------
community_gift_groups = set()   # Gift-Bundle activityGroups
pending_gifted_subs = {}        # ag -> {"platform":..., "tier":..., "ts":..., "config":...}

def minutes_for_tier(cfg, tier_raw):
    if tier_raw in ["1000", "prime"]:
        return cfg["twitch"]["sub_t1"]
    if tier_raw == "2000":
        return cfg["twitch"]["sub_t2"]
    if tier_raw == "3000":
        return cfg["twitch"]["sub_t3"]
    return cfg["twitch"]["sub_t1"]

def fmt_minutes(val: float):
    val = float(val)
    return int(val) if val.is_integer() else round(val, 1)

def check_pending_gift(activity_group):
    """
    Wird verzögert (10s) aufgerufen.
    Wenn bis dahin KEIN communityGiftPurchase mit derselben activityGroup registriert wurde,
    behandeln wir den gespeicherten gifted subscriber als Einzelgift.
    """
    info = pending_gifted_subs.pop(activity_group, None)
    if not info:
        return  # nothing pending (or already recognized as a bundle)
    # If the group has already been marked as a bundle -> ignore
    if activity_group in community_gift_groups:
        return

    platform = info["platform"]
    tier_raw = info["tier"]
    cfg = info["config"]
    add_min = minutes_for_tier(cfg, tier_raw) * get_current_multiplier()

    with lock:
        global remaining
        remaining += add_min * 60
        save_state()
        new_state = {"remaining": remaining, "paused": paused}

    add_support_minutes(add_min)
    label = "Gifted Sub"
    m = fmt_minutes(add_min)
    prefix = ""
    if get_current_multiplier() != 1.0:
        prefix = f"[HAPPY HOUR x{HAPPY_MULTIPLIER}] "


    msg = f"[{ts()}] {prefix}[{platform}] {label} | +{m} minutes"
    print(msg)
    log_time_add(platform, m, remaining, prefix + label)



    socketio.start_background_task(socketio.emit, "timer_update", new_state)



def apply_minutes(platform, minutes_to_add, label):
    """Helper to apply minutes, log and emit."""
    if minutes_to_add <= 0:
        return

    # apply Happy Hour multiplier (NOT for manual additions)
    multiplier = get_current_multiplier()
    if multiplier != 1.0:
        minutes_to_add = minutes_to_add * multiplier

    with lock:
        global remaining
        remaining += int(round(minutes_to_add * 60))  # round up to seconds
        save_state()
        new_state = {"remaining": remaining, "paused": paused}

    add_support_minutes(minutes_to_add)
    m = fmt_minutes(minutes_to_add)


    # Prefix bauen, wenn Happy Hour aktiv ist
    prefix = ""
    if get_current_multiplier() != 1.0:
        prefix = f"[HAPPY HOUR x{HAPPY_MULTIPLIER}] "

    # Ausgabe + Log
    print(f"[{ts()}] {prefix}[{platform}] {label} | +{m} minutes")
    log_time_add(platform, m, remaining, prefix + label)

    socketio.start_background_task(socketio.emit, "timer_update", new_state)
    
    # -------------------------------------
    # NEU: Live Event für time_add.html
    # -------------------------------------
    socketio.emit("time_added", {
        "platform": platform,
        "label": label,     # z.B. "Gift Bundle", "T1 Sub", "Bits (300)"
        "minutes": float(m) # z.B. 12.0
    })

def get_current_multiplier():
    global happy_active, happy_until
    if happy_active and time.time() < happy_until:
        return HAPPY_MULTIPLIER
    return 1.0


def handle_event(platform, data, config):
    global remaining, community_gift_groups, pending_gifted_subs
    minutes_to_add = 0.0

    # RAW event to logfile + optional console
    if DEBUG_EVENTS:
        try:
            print(f"[{ts()}] [{platform}] RAW EVENT: {json.dumps(data, indent=2, ensure_ascii=False)}")
        except Exception:
            print(f"[{ts()}] [{platform}] RAW EVENT (non-json-printable)")
    log_event(platform, data)

    etype = data.get("type")
    text = data.get("data", {}).get("text", "")

    # Completely ignore normal IRC chat messages
    # (only continue if it's the SoundAlerts Bits trigger sentence)

    if etype == "message" and re.search(r"(.+?) löst (.+?) mit (\d+)\s*Bits aus", text, flags=re.IGNORECASE) is None:
        return

    # From here on, only log and process relevant events
    log_event(platform, data)


    # SoundAlerts chat bits parsing (from chat source)
    if etype == "message":
        text = data.get("data", {}).get("text", "")
        match = re.search(r"(.+?) löst (.+?) mit (\d+)\s*Bits aus", text, flags=re.IGNORECASE)
        if match:
            user = match.group(1)
            alert_name = match.group(2)
            bits = int(match.group(3))

            minutes_to_add = (bits / 100.0) * float(config["twitch"]["bits_per_100"])
            minutes_to_add = round(minutes_to_add, 2)
            # Platform label: replace "-IRC" with "-SoundAlerts" to keep logs clean
            nice_platform = platform.replace("-IRC", "-SoundAlerts")

            apply_minutes(nice_platform, minutes_to_add, f"SoundAlerts {bits} Bits")

            return  # handled

    # Twitch/Kick subs via StreamElements
    if etype == "subscriber":
        d = data.get("data", {})
        provider = str(data.get("provider", "")).lower()
        tier_raw = str(d.get("tier", "1000")).lower()
        gifted = d.get("gifted", False)
        ag = data.get("activityGroup")

        # --- Kick subs ---
        if "kick" in provider or "kick" in platform.lower():
            if "kick" in config:
                minutes_to_add = float(config["kick"]["sub"])

        # --- Twitch subs ---
        else:
            if gifted:
                if ag:
                    pending_gifted_subs[ag] = {
                        "platform": platform,
                        "tier": tier_raw,
                        "ts": time.time(),
                        "config": config
                    }
                    threading.Timer(10.0, check_pending_gift, args=(ag,)).start()
                    return
                else:
                    minutes_to_add = float(minutes_for_tier(config, tier_raw))
            else:
                minutes_to_add = float(minutes_for_tier(config, tier_raw))

    # Gifted subs (Bundle)
    elif etype == "communityGiftPurchase":
        d = data.get("data", {})
        gift_amount = int(d.get("amount", 1))
        tier_raw = str(d.get("tier", "1000")).lower()
        ag = data.get("activityGroup")
        if ag:
            community_gift_groups.add(ag)
            pending_gifted_subs.pop(ag, None)
        minutes_to_add = float(gift_amount) * float(minutes_for_tier(config, tier_raw))

    # Bits (normale Twitch-Cheers aus SE-Activities)
    elif etype == "cheer":
        bits = int(data.get("data", {}).get("amount", 0))
        minutes_to_add = round((bits / 100.0) * float(config["twitch"]["bits_per_100"]), 2)

    # Donations via Tipeee
    elif etype == "donation" and "tipeee" in config:
        amount = float(data.get("amount", 0))
        minutes_to_add = float(amount) * float(config["tipeee"]["minutes_per_eur"])

    # Donations via StreamElements
    elif etype == "tip" and "streamelements" in config:
        amount = float(data.get("data", {}).get("amount", 0))
        minutes_to_add = float(amount) * float(config["streamelements"]["minutes_per_eur"])

    # Kick gifts via Chat
    elif etype == "kick_gift":
        if "kick" in config:
            amount = int(data.get("amount", 0))
            minutes_to_add = float((amount // 100) * int(config["kick"]["kicks_per_100"]))

    # --- Apply time addition ---
    if minutes_to_add > 0:
        # passendes Label bestimmen
        label = ""
        if etype == "subscriber":
            if 'gifted' in locals() and gifted:
                label = "Gifted Sub"
            else:
                if tier_raw == "1000" or tier_raw == "prime":
                    label = "T1 Sub"
                elif tier_raw == "2000":
                    label = "T2 Sub"
                elif tier_raw == "3000":
                    label = "T3 Sub"
                else:
                    label = "Sub"
        elif etype == "communityGiftPurchase":
            label = f"Gift Bundle"
        elif etype == "cheer":
            bits = int(data.get("data", {}).get("amount", 0))
            label = f"Bits ({bits})"
        elif etype == "donation":
            label = f"Donation ({amount:.2f} €)"
        elif etype == "tip":
            label = f"Tip ({amount:.2f} €)"
        elif etype == "kick_gift":
            label = f"Kick Gift"
        else:
            label = etype.capitalize()

        apply_minutes(platform, float(minutes_to_add), label)

# --------------------
# StreamElements WS with auto-reconnect (activities only)
# --------------------
def start_client(name, token, config):
    url = "wss://astro.streamelements.com"

    def run_ws():
        def on_open(ws):
            print(f"[{ts()}] [{name}] Connected")

        def on_message(ws, message):
            try:
                msg = json.loads(message)
            except Exception:
                if DEBUG_EVENTS:
                    print(f"[{ts()}] [{name}] Non-JSON message: {message}")
                return

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
# Twitch IRC Chat (for SoundAlerts parsing)
# --------------------
def start_twitch_chat(name, oauth_token, nick, channel, config):
    """
    Connects to Twitch IRC via WebSocket and forwards chat lines to handle_event
    so SoundAlerts messages can be parsed and counted.
    """
    if not oauth_token or not nick or not channel:
        print(f"[{ts()}] [INFO] Twitch IRC for {name} skipped (missing ENV)")
        return

    url = "wss://irc-ws.chat.twitch.tv:443"
    chan = f"#{channel}"

    def run_irc():
        def on_open(ws):
            print(f"[{ts()}] [{name}] IRC connected -> JOIN {chan}")
            # Twitch IRC capabilities (we don't strictly need tags here)
            ws.send("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
            ws.send(f"PASS {oauth_token}\r\n")
            ws.send(f"NICK {nick}\r\n")
            ws.send(f"JOIN {chan}\r\n")

        def on_message(ws, message):
            # Twitch IRC can bunch multiple messages separated by \r\n
            for raw in message.split("\r\n"):
                if not raw:
                    continue
                if DEBUG_EVENTS:
                    print(f"[{ts()}] [{name}] IRC RAW: {raw}")

                # PING -> PONG
                if raw.startswith("PING"):
                    ws.send("PONG :tmi.twitch.tv\r\n")
                    continue

                # Parse PRIVMSG to extract text
                # Example:
                # @tags :username!username@username.tmi.twitch.tv PRIVMSG #channel :message text here
                try:
                    if " PRIVMSG " in raw:
                        parts = raw.split(" PRIVMSG ", 1)
                        trailing = parts[1].split(" :", 1)
                        if len(trailing) == 2:
                            text = trailing[1]
                            # Forward to handle_event as a chat "message"
                            fake = {"type": "message", "data": {"text": text}}
                            handle_event(name, fake, config)
                except Exception as e:
                    print(f"[{ts()}] [{name}] IRC parse error:", e)

        def on_error(ws, error):
            print(f"[{ts()}] [{name}] IRC error:", error)

        def on_close(ws, close_status_code, close_msg):
            print(f"[{ts()}] [{name}] IRC closed, reconnect in 3s")
            time.sleep(3)
            run_irc()

        ws = websocket.WebSocketApp(
            url,
            on_open=on_open,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close
        )
        ws.run_forever()

    threading.Thread(target=run_irc, daemon=True).start()

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
                inner = json.loads(payload["data"])
                text = inner.get("content", "")
                if DEBUG_EVENTS:
                    print(f"[{ts()}] [{name}] RAW CHAT EVENT: {json.dumps(inner, indent=2, ensure_ascii=False)}")
                log_event(name, inner)
                m = re.search(r"gifted\s+(\d+)\s+KICK", text, re.IGNORECASE)
                if m:
                    amount = int(m.group(1))
                    fake_event = {"type": "kick_gift", "amount": amount}
                    handle_event(name, fake_event, config)
        except Exception as e:
            print(f"[{ts()}] [{name}] KickChat parse error:", e)

    def on_close(ws, *a):
        print(f"[{ts()}] [{name}] KickChat closed, reconnect in 5s")
        time.sleep(2)
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
    if not api_key:
        print(f"[{ts()}] [INFO] {name} skipped (no TIPEEE_API_KEY)")
        return

    sio = socketio_client.Client(reconnection=True)

    @sio.event
    def connect():
        print(f"[{ts()}] [{name}] Connected to Tipeee -> listening for donations")

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
                if DEBUG_EVENTS:
                    print(f"[{ts()}] [{name}] RAW TIPEEE EVENT: {json.dumps(ev, indent=2, ensure_ascii=False)}")
                log_event(name, ev)
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
            time.sleep(1)
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

    # Nur positive Werte zählen als Support
    if delta_str is not None and delta > 0:
        add_support_minutes(float(delta))

    socketio.start_background_task(socketio.emit, "timer_update", new_state)
    print(f"[{ts()}] [MANUAL] {delta:+} minutes -> {remaining//60} min total")

    return jsonify(new_state)

@app.route("/log")
def get_log():
    try:
        if not os.path.exists(LOG_FILE):
            return jsonify({"lines": ["(keine Logdatei vorhanden)\n"]})
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()[-100:]
        return jsonify({"lines": lines})
    except Exception as e:
        return jsonify({"lines": [f"Fehler beim Lesen von {LOG_FILE}: {e}\n"]})

@app.route("/time_log")
def get_time_log():
    try:
        if not os.path.exists(TIME_ADD_LOG):
            return jsonify({"lines": ["(no time additions yet)\n"]})
        with open(TIME_ADD_LOG, "r", encoding="utf-8") as f:
            lines = f.readlines()[-10:]
        return jsonify({"lines": lines})
    except Exception as e:
        return jsonify({"lines": [f"Fehler beim Lesen von {TIME_ADD_LOG}: {e}\n"]})

# === GOALS API ===
@app.route("/goals")
def get_goals():
    return jsonify(goals_data)

@app.route("/goals/update", methods=["POST"])
def update_goals():
    global goals_data
    try:
        data = request.get_json(force=True)
        new_goals = data.get("goals", [])
        goals_data["goals"] = []
        for g in new_goals:
            goals_data["goals"].append({
                "hours": g.get("hours", 0),
                "title": g.get("title", ""),
                "reached": False
            })
        save_goals()
        check_goals_reached()
        print(f"[{ts()}] [GOALS] Ziele aktualisiert ({len(new_goals)} Einträge)")
        return jsonify({"status": "ok", "goals": goals_data["goals"]})
    except Exception as e:
        print(f"[{ts()}] [GOALS] Fehler beim Update: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/goals/reset")
def reset_goals():
    goals_data["total_minutes_supported"] = 0.0
    save_goals()
    print(f"[{ts()}] [GOALS] Gesamt-Support auf 0 zurückgesetzt")
    return jsonify({"status": "ok", "total_minutes_supported": 0.0})

@app.route("/happyhour")
def happyhour():
    global happy_active, happy_until

    # Start
    if "start" in request.args:
        t = int(request.args.get("time", "0"))
        if t <= 0:
            return jsonify({"error": "time must be > 0 minutes"}), 400

        happy_active = True
        happy_until = time.time() + (t * 60)

        print(f"[{ts()}] [HAPPY] Happy Hour started for {t} minutes (x{HAPPY_MULTIPLIER})")
        return jsonify({"status": "started", "multiplier": HAPPY_MULTIPLIER, "minutes": t})

    # Stop
    if "stop" in request.args:
        happy_active = False
        happy_until = 0
        print(f"[{ts()}] [HAPPY] Happy Hour stopped")
        return jsonify({"status": "stopped"})

    # Status
    remaining = max(0, int(happy_until - time.time()))
    return jsonify({
        "active": happy_active and remaining > 0,
        "remaining_seconds": remaining,
        "multiplier": HAPPY_MULTIPLIER
    })

# --------------------
# Fake Test Events
# --------------------
@app.route("/fake/soundalerts")
def fake_soundalerts():
    user = request.args.get("user", "Tester")
    alert = request.args.get("alert", "Sound")
    bits = int(request.args.get("bits", "100"))

    platform = "FAKE-SoundAlerts"

    # Minuten wie echte SoundAlerts berechnen – aber NICHT anwenden!
    minutes = round((bits / 100.0) * float(CONFIG1["twitch"]["bits_per_100"]), 2)

    label = f"SoundAlerts {bits} Bits"

    # NICHT: handle_event(...)
    # Nur: direkt an time_add.html senden
    socketio.emit("time_added", {
        "platform": platform,
        "label": label,
        "minutes": minutes
    })

    print(f"[FAKE] {label} | +{minutes} minutes (NO TIMER CHANGE)")
    return jsonify({"ok": True, "label": label, "minutes": minutes})



@app.route("/fake/gift")
def fake_gift():
    try:
        count = int(request.args.get("count", "1"))
        tier = request.args.get("tier", "1")
    except:
        return jsonify({"error": "count must be number"}), 400

    platform = "FAKE-Gift"

    # Minuten pro Sub anhand deiner CONFIG
    if tier == "1":
        per_sub = CONFIG1["twitch"]["sub_t1"]
    elif tier == "2":
        per_sub = CONFIG1["twitch"]["sub_t2"]
    elif tier == "3":
        per_sub = CONFIG1["twitch"]["sub_t3"]
    else:
        per_sub = CONFIG1["twitch"]["sub_t1"]

    minutes = round(per_sub * count, 2)

    # Label exakt so wie echte Events
    label = f"Gift Bundle ({count})"

    # Popup auslösen
    socketio.emit("time_added", {
        "platform": platform,
        "label": label,     # z.B. "Gift Bundle (5)"
        "minutes": minutes  # Minuten die addiert würden
    })

    print(f"[FAKE] {label} | +{minutes} minutes")

    return jsonify({
        "ok": True,
        "label": label,
        "count": count,
        "tier": tier,
        "minutes": minutes
    })

@app.route("/fake/sub")
def fake_sub():
    tier = request.args.get("tier", "1")
    platform = "FAKE-Twitch"

    # Minuten anhand deiner Config bestimmen
    if tier == "1":
        minutes = CONFIG1["twitch"]["sub_t1"]
        label = "T1 Sub"
    elif tier == "2":
        minutes = CONFIG1["twitch"]["sub_t2"]
        label = "T2 Sub"
    elif tier == "3":
        minutes = CONFIG1["twitch"]["sub_t3"]
        label = "T3 Sub"
    else:
        return jsonify({"error": "invalid tier"}), 400

    # Nur anzeigen – nicht den echten Timer verändern
    socketio.emit("time_added", {
        "platform": platform,
        "label": label,
        "minutes": minutes
    })

    print(f"[FAKE] {label} | +{minutes} minutes")
    return jsonify({"ok": True, "label": label, "minutes": minutes})


@app.route("/fake/bits")
def fake_bits():
    amount = int(request.args.get("amount", "100"))
    platform = "FAKE-Bits"

    minutes = (amount / 100.0) * CONFIG1["twitch"]["bits_per_100"]
    minutes = round(minutes, 2)

    label = f"Bits ({amount})"

    socketio.emit("time_added", {
        "platform": platform,
        "label": label,
        "minutes": minutes
    })

    print(f"[FAKE] {label} | +{minutes} minutes")
    return jsonify({"ok": True, "label": label, "minutes": minutes})


@app.route("/fake/euro")
def fake_euro():
    amount = float(request.args.get("amount", "1"))
    platform = "FAKE-Euro"

    # Tipeee oder StreamElements → beide erlaubt
    if "tipeee" in CONFIG1:
        minutes = amount * CONFIG1["tipeee"]["minutes_per_eur"]
    else:
        minutes = amount * CONFIG1["streamelements"]["minutes_per_eur"]

    minutes = round(minutes, 2)

    label = f"Donation ({amount} €)"

    socketio.emit("time_added", {
        "platform": platform,
        "label": label,
        "minutes": minutes
    })

    print(f"[FAKE] {label} | +{minutes} minutes")
    return jsonify({"ok": True, "label": label, "minutes": minutes})


# === END GOALS API ===

# --------------------
# Main start
# --------------------
if __name__ == "__main__":
    socketio.start_background_task(timer_loop)

    # StreamElements Activities (Subs/Bits/Donations etc.)
    if SE_TWITCH_TOKEN:
        start_client(f"{LABEL_STREAMER1}-Twitch", SE_TWITCH_TOKEN, CONFIG1)
    if SE_KICK_TOKEN:
        start_client(f"{LABEL_STREAMER1}-Kick", SE_KICK_TOKEN, CONFIG1)

    # Twitch IRC Chat (SoundAlerts Chatzeilen)
    start_twitch_chat(f"{LABEL_STREAMER1}-IRC", TWITCH_IRC_TOKEN, TWITCH_IRC_NICK, TWITCH_IRC_CHANNEL, CONFIG1)

    # Kick Chat (optional)
    connect_kick_chat(f"{LABEL_STREAMER1}-KickChat", KICK_APP_KEY, KICK_CLUSTER, KICK_CHATROOM_ID, CONFIG1)

    # Tipeee (optional)
    if TIPEEE_API_KEY:
        start_tipeee(f"{LABEL_STREAMER1}-Tipeee", TIPEEE_API_KEY, CONFIG1)

    # Streamer 2
    if SE2_TWITCH_TOKEN and CONFIG2:
        start_client(f"{LABEL_STREAMER2}-Twitch", SE2_TWITCH_TOKEN, CONFIG2)
    if SE2_KICK_TOKEN and CONFIG2:
        start_client(f"{LABEL_STREAMER2}-Kick", SE2_KICK_TOKEN, CONFIG2)
    if CONFIG2:
        start_twitch_chat(f"{LABEL_STREAMER2}-IRC", TWITCH_IRC_TOKEN2, TWITCH_IRC_NICK2, TWITCH_IRC_CHANNEL2, CONFIG2)
        connect_kick_chat(f"{LABEL_STREAMER2}-KickChat", KICK_APP_KEY2, KICK_CLUSTER2, KICK_CHATROOM_ID2, CONFIG2)
    if TIPEEE_API_KEY2 and CONFIG2:
        start_tipeee(f"{LABEL_STREAMER2}-Tipeee", TIPEEE_API_KEY2, CONFIG2)

    print(f"[{ts()}] [APP] Subathon timer running at http://localhost:5000")
    socketio.run(app, host="0.0.0.0", port=5000)
