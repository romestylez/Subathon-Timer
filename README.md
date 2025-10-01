# Subathon Timer

A **Subathon Timer** with Twitch, Kick, Tipeee and StreamElements integration.  
It increases the remaining time based on subscriptions, bits, donations, or other events.

---

## 🚀 Features

- Integration with **Twitch Subs**, **Bits**, **Gifted Subs**
- Support for **Kick Subs & Kick KICKS**
- **Tipeee & StreamElements Donations**
- **Timer Web UI** (for stream overlay and control)
- **Persistent Timer State** (restores after restart)
- Control via API endpoints or web interface

---

## 📂 Project Structure

```
.
├── app.py            # Flask + SocketIO backend (event handler & timer)
├── config.json       # Configuration for rewards (Twitch, Kick, Tipeee, etc.)
├── .env              # Tokens and secrets (do not commit to Git)
├── index.html        # Overlay timer (for OBS / stream display)
├── control.html      # Control panel for timer management
├── slideshow.html    # Slideshow for rewards
└── state.json        # Persistent storage of timer state
```

---

## ⚙️ Installation

### Requirements

- Python 3.9+
- Install dependencies manually:

```bash
pip install flask flask-socketio flask-cors websocket-client python-dotenv
```

### Setup

1. Clone this repository
2. Create a `.env` file (see example below)
3. Adjust `config.json` for your reward settings
4. Start the server:
   ```bash
   python app.py
   ```

By default the server runs on **http://localhost:5000**.

---

## 🔑 Example `.env`

```env
SE_TWITCH_TOKEN=your_twitch_token_here
SE_KICK_TOKEN=your_kick_token_here
KICK_APP_KEY=your_kick_app_key_here
KICK_CLUSTER=us2
KICK_CHATROOM_ID=123456
TIPEEE_API_KEY=your_tipeee_api_key_here
SE2_TWITCH_TOKEN=your_second_twitch_token_here
```

---

## ⚙️ Configuration (`config.json`)

Example:

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

---

## 🌐 API Endpoints

All endpoints return JSON.

### Timer State
`GET /state`  
➡️ Returns current state (remaining seconds & paused).

### Pause Timer
`GET /pause`  
➡️ Pauses the timer.

### Resume Timer
`GET /resume`  
➡️ Resumes the timer.

### Toggle Pause/Resume
`GET /toggle`  
➡️ Toggles between pause and resume.

### Adjust Time
`GET /time?delta=5`  
➡️ Adds 5 minutes.

`GET /time?minusdelta=5`  
➡️ Subtracts 5 minutes.

### Rewards
`GET /rewards?streamer=1`  
➡️ Returns reward list for streamer 1.  
(Use `?streamer=2` if a second streamer config exists).

---

## 🖥️ Frontend

- **index.html** → Overlay for OBS, shows timer + pause indicator
- **control.html** → Control panel with buttons for pause/resume and time adjustment
- **slideshow.html** → Slideshow with rewards (e.g. for stream display)

---

## 📝 Logging

- **events.log** → All raw events (subs, bits, donations, …)
- **state.json** → Saves timer state for restarts
