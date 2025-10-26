# Subathon Timer
A **Subathon Timer** with Twitch, Kick, Tipeee and StreamElements integration.
It increases the remaining time based on subscriptions, bits, donations, or other events.

🚀 Features
- Integration with Twitch Subs, Bits, Gifted Subs
- Support for Kick Subs & Kick KICKS (via Kick Chat listener)
- Tipeee & StreamElements Donations
- Timer Web UI (for stream overlay and control)
- Persistent Timer State (restores after restart)
- Control via API endpoints or web interface
- Multi-Streamer Support → 2 streamers with different configs, all events add to the same timer ✅
- **Goal Tracking & Milestones** → Tracks total supported time, auto-marks reached goals, persistent across restarts

📂 Project Structure
```
.
├── app.py            # Flask + SocketIO backend (event handler & timer)
├── config.json       # Config for Streamer 1 (rewards)
├── config2.json      # Config for Streamer 2 (optional)
├── .env              # Tokens and secrets (do not commit to Git)
├── index.html        # Overlay timer (for OBS / stream display)
├── control.html      # Control panel for timer management + goal editing
├── goal.html         # Overlay for displaying current goals
├── slideshow.html    # Slideshow for rewards
├── state.json        # Persistent storage of timer state
└── goals.json        # Persistent goal data (progress & reached goals)
```

⚙️ Installation
### Requirements
Python 3.9+

Install dependencies manually:
```
pip install flask flask-socketio flask-cors websocket-client python-dotenv
```

### Setup
1. Clone this repository  
2. Create a `.env` file (see example below)  
3. Adjust `config.json` for Streamer 1  
4. (optional) Create `config2.json` for Streamer 2  
5. Start the server:
   ```bash
   python app.py
   ```
   By default the server runs on `http://localhost:5000`.

🔑 Example .env
```
# Streamer 1
SE_TWITCH_TOKEN=your_twitch_token_1
SE_KICK_TOKEN=your_kick_token_1
KICK_APP_KEY=your_kick_app_key_1
KICK_CLUSTER=us2
KICK_CHATROOM_ID=123456
TIPEEE_API_KEY=your_tipeee_api_key_1

# Streamer 2 (optional)
SE2_TWITCH_TOKEN=your_twitch_token_2
SE2_KICK_TOKEN=your_kick_token_2
KICK_APP_KEY2=your_kick_app_key_2
KICK_CLUSTER2=us2
KICK_CHATROOM_ID2=654321
TIPEEE_API_KEY2=your_tipeee_api_key_2
```

👉 If `SE2_TWITCH_TOKEN` is empty, Streamer 2 is skipped automatically.  
👉 Kick gifts require valid Kick Chat ENV vars (`KICK_APP_KEY`, `KICK_CLUSTER`, `KICK_CHATROOM_ID`).

⚙️ Configuration (config.json / config2.json)
Each streamer has their own config file (config.json for Streamer 1, config2.json for Streamer 2).
They follow the same structure, but you can set different values per streamer:

```json
{
  "twitch": {
    "sub_t1": 7,
    "sub_t2": 15,
    "sub_t3": 40,
    "bits_per_100": 2
  },
  "kick": {
    "sub": 15,
    "kicks_per_100": 4
  },
  "tipeee": {
    "minutes_per_eur": 2
  },
  "streamelements": {
    "minutes_per_eur": 3
  },
  "timer": {
    "start_minutes": 60,
    "max_minutes": 0
  }
}
```
Twitch subs, gifted subs, bits, donations → tracked via StreamElements  
Kick subs → tracked via StreamElements Kick token  
Kick gifts (KICKs) → tracked via Kick Chat listener  
Tipeee donations → tracked via Tipeee API  
All events (from Streamer 1 & 2) will add time to the same shared timer.

🌐 API Endpoints
All endpoints return JSON.

### Timer
- `GET /state` → Returns current state (remaining seconds & paused)
- `GET /pause` → Pauses the timer
- `GET /resume` → Resumes the timer
- `GET /toggle` → Toggles between pause and resume
- `GET /time?delta=5` → Adds 5 minutes
- `GET /time?minusdelta=5` → Subtracts 5 minutes

### Rewards
- `GET /rewards?streamer=1` → Returns reward list for Streamer 1
- `GET /rewards?streamer=2` → Returns reward list for Streamer 2 (if config2.json exists)

### Goals
- `GET /goals` → Returns all current goals and total supported minutes  
- `POST /goals/update` → Update goal list (from control panel)  
- `GET /goals/reset` → Resets total support and all goal progress to zero  

🖥️ Frontend
- **index.html** → Overlay for OBS, shows timer + pause indicator  
- **control.html** → Control panel with buttons for pause/resume, time adjustment, and goal editing (add/edit/save/reset)  
- **goal.html** → Stream overlay displaying total support and goal progress  
- **slideshow.html** → Slideshow with rewards (for display on stream)

🧭 Goal System
- Goals are stored persistently in `goals.json`
- Each goal has hours, title, and a “reached” status
- Total support increases automatically based on added time (via subs, bits, tips, etc.)
- Once total supported time exceeds a goal threshold, it’s marked ✅ as *reached*
- Progress and reached status are restored after restart
- The control panel allows manual editing and a full reset of all goals

📝 Logging
- `events.log` → All raw events (subs, bits, donations, Kick gifts, …)
- `time_add.log` → Summary log of all added time (+X minutes)
- `state.json` → Saves timer state for restarts
- `goals.json` → Saves total support and goals persistently
