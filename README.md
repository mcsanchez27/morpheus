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

---

## 🧠 Multi-Brain Swappable Architecture (Gemini, Claude, OpenAI)

Morpheus supports hot-swapping AI models seamlessly via `.env` or live in Discord chat!

### Configuration (`M:\Tools\Google Tools\.env`)
```ini
# Primary Provider: "gemini" (default) | "claude" | "openai"
LLM_PROVIDER=gemini

# Specific Model Overrides (Optional)
GEMINI_MODEL=gemini-2.5-flash
CLAUDE_MODEL=claude-3-5-haiku-20241022
OPENAI_MODEL=gpt-4o-mini

# API Keys
GEMINI_API_KEY=your_gemini_key_here
ANTHROPIC_API_KEY=your_anthropic_key_here
OPENAI_API_KEY=your_openai_key_here
```

### Live Discord In-Chat Brain Swapping
Any authorized user can inspect or swap Morpheus's brain on the fly:
- `!m brain`: Displays active provider and model status.
- `!m brain claude`: Swaps instantly to **Anthropic Claude 3.5 Haiku** (spends your \$100/mo Claude Max credits!).
- `!m brain gemini`: Swaps instantly to **Google Gemini 2.5 Flash** (fast, free tier).
- `!m debate <matchup>`: Commands Morpheus to argue persuasively citing combat feats.
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

### 1. Autonomous Match Playing (`player2_harness.py`)
Morpheus listens for Fight Bot challenge embeds (`on_message` in `morpheus.py`). To automate full match gameplay:
- Listen for cards in `#match-arena` (`1560800071661125745`).
- Parse the fighter assigned to Morpheus.
- Generate arguments via `ask_brain()`.
- Post the rebuttal directly into the match thread.

### 2. Headless CLI Messaging
Any script or AI can send messages via Morpheus without booting the full gateway listener:
```powershell
& "M:\Projects\Matt\Fight Bot\.venv\Scripts\python.exe" "M:\Tools\Google Tools\discord_tool.py" --bot morpheus send <channel_id> "<message>"
```

### 3. Telemetry Log
Morpheus streams all interaction telemetry to:
`M:\Tools\Google Tools\logs\morpheus_telemetry.jsonl`
Format:
```json
{"timestamp": "2026-10-16T23:31:12Z", "event": "morpheus_online", "data": {"user": "Morpheus#0167", "id": 1560794354744819782}}
```
