# 🕶️ Morpheus - Autonomous Discord Sparring Partner & Testing Agent

**Morpheus** (`Morpheus#0167`, ID: `1560794354744819782`) is an autonomous Discord bot and testing agent designed for **Mountain Man Studios**. He serves as Player 2 for Fight Bot matches, server architect, live QA tester, and automated sparring partner.

---

## 🚀 1-Click Launch Options (Executable & Scripts)

Morpheus is packaged for instant standalone execution on Windows:

1. **Direct Executable (`Morpheus.exe`)**:
   - Double-click [`M:\Tools\Google Tools\Morpheus.exe`](file:///M:/Tools/Google%20Tools/Morpheus.exe).
   - Compiled with PyInstaller—fully self-contained, requiring no external Python runtime.
2. **1-Click Batch Launcher (`Start_Morpheus.bat`)**:
   - Double-click [`M:\Tools\Google Tools\Start_Morpheus.bat`](file:///M:/Tools/Google%20Tools/Start_Morpheus.bat).
   - Starts Morpheus in a stylized matrix console window.
3. **PowerShell CLI**:
   ```powershell
   & "M:\Projects\Matt\Fight Bot\.venv\Scripts\python.exe" "M:\Tools\Google Tools\morpheus.py"
   ```

### Development setup
```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in the tokens / keys
python -m pytest       # unit tests for the pure helpers
```

---

## 🧠 Multi-Brain Swappable Architecture (Gemini, Claude, OpenAI)

Morpheus supports hot-swapping AI models seamlessly via `.env` or live in Discord chat!

### Configuration (`.env` next to `morpheus.py`, or one of the legacy `M:\...` locations; see `.env.example`)
```ini
# Primary Provider: "gemini" (default) | "claude" | "openai"
LLM_PROVIDER=gemini

# Specific Model Overrides (Optional)
GEMINI_MODEL=gemini-2.5-flash
CLAUDE_MODEL=claude-haiku-5-5
OPENAI_MODEL=gpt-4o-mini

# API Keys
GEMINI_API_KEY=your_gemini_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here

# Fight Bot integration
FIGHTBOT_DB_PATH=M:/Projects/Matt/Fight Bot/data/court.db
FIGHTBOT_USER_ID=            # optional, pins challenge detection to Fight Bot's user ID
```

### Live Discord In-Chat Brain Swapping
Anyone can inspect the brain; switching it requires the bot owner or the **Manage Server** permission (it can spend paid API credits):
- `!m brain`: Displays active provider and model status.
- `!m brain claude`: Swaps instantly to **Anthropic Claude Haiku 5.5** (billed to your Anthropic API account; a Claude Max subscription does not cover API usage).
- `!m brain gemini`: Swaps instantly to **Google Gemini 2.5 Flash** (fast, free tier).
- `!m debate <matchup>`: Commands Morpheus to argue persuasively citing combat feats (15s per-user cooldown).
- `!m ping`: Checks gateway latency and connection health.
- `!m status`: Displays live uptime, latency, and active brain stats.

---

## 🏛️ Discord Server & Channel Blueprint

- **Server Name**: `Mountain Man Studios`
- **Server ID**: `1560796428161388595`

### Channel Directory:
| Category | Channel | Channel ID | Role / Usage |
| :--- | :--- | :--- | :--- |
| **📢 Official & Welcome** (`1560800057069015091`) | `#welcome-and-rules` | `1560800059300511855` | Rules, links, guidelines |
| | `#announcements` | `1560800062064562308` | Studio releases |
| | `#dev-log` | `1560800064375623842` | Build drop notes |
| **🎮 MMS Games** (`1560800066929954848`) | `#fight-bot` | `1560800069354127521` | Discussion, tier lists |
| | `#match-arena` | `1560800071661125745` | Active match cards & rulings |
| **🧪 QA & Feedback** (`1560800074173386884`) | `#bug-reports` | `1560800076711067750` | Error logs, scoring bugs |
| | `#feature-requests` | `1560800079089111162` | Wishlist & suggestions |
| **🛠️ Ops & Telemetry** (`1560800081395974244`) | `#bot-staging` | `1560800083921080340` | Developer feature staging |
| | `#github-activity` | `1560800086383001712` | Webhook commits & PRs |
| | `#morpheus-logs` | `1560800088719364137` | Autonomous test telemetry |

---

## 🤖 Guide for Another AI Agent / Developer Integration

If another AI agent (Antigravity, Jules, Claude, OpenAI, or a local model) is tasked with driving or extending Morpheus:

### 1. Autonomous Match Playing (built into `morpheus.py`)
Morpheus listens for Fight Bot challenge embeds (`on_message` in `morpheus.py`) and plays Player 2 end to end:
- Detects challenge cards from Fight Bot (by `FIGHTBOT_USER_ID`, or by author name) and looks the fight up in `court.db`.
- Accepts open challenges or ones targeted at Morpheus, opens the argument thread, and claims `advocate_b` in `court.db`.
- Generates one rebuttal per fight via `ask_brain_async()` when the opponent posts, then marks itself rested.
- LLM and SQLite calls run off the event loop (`asyncio.to_thread`) so the gateway heartbeat never stalls.

### 2. Headless CLI Messaging
`discord_tool.py` (headless send helper) lives outside this repository in `M:\Tools\Google Tools\`; it is not part of this codebase.

### 3. Telemetry Log
Morpheus streams all interaction telemetry to `logs/morpheus_telemetry.jsonl` next to `morpheus.py` (gitignored).
Format:
```json
{"timestamp": "2026-10-16T23:31:12Z", "event": "morpheus_online", "data": {"user": "Morpheus#0167", "id": 1560794354744819782}}
```
