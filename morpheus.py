#!/usr/bin/env python3
"""Morpheus - The Autonomous Discord Studio Manager & Sparring Partner.

Mountain Man Studios (Server ID: 1560796428161388595)

Capabilities:
1. Autonomous Player 2 Sparring Agent: Auto-accepts Fight Bot challenges,
   creates argument threads, analyzes opponent arguments, and posts lore-accurate
   rebuttals citing canonical feats using swappable AI brains.
2. Swappable Multi-Brain Engine: Google Gemini (2.5 Flash), Anthropic Claude
   (3.5 Haiku/Sonnet via Claude Max credits), and OpenAI (GPT-4o mini).
3. Live In-Chat Model Hot-Swapping (`!m brain claude`, `!m brain gemini`).
4. Live Telemetry & QA Logging to JSON lines.
"""

import asyncio
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple

# Ensure UTF-8 output on Windows
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

import discord
from discord.ext import commands
import urllib.request
import urllib.error

# Resolve base dir (supporting PyInstaller frozen bundle and standard script execution)
if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    BASE_DIR = Path(__file__).resolve().parent

LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
TELEMETRY_LOG = LOG_DIR / "morpheus_telemetry.jsonl"

FIGHTBOT_DB_PATH = Path("M:/Projects/Matt/Fight Bot/data/court.db")


def _load_env_val(key_name: str, default: str = "") -> str:
    """Load configuration from environment or .env files."""
    val = os.environ.get(key_name, "").strip()
    if val:
        return val
    search_paths = [
        BASE_DIR / ".env",
        Path("M:/Projects/Mountain Man Studios/morpheus/.env"),
        Path("M:/Tools/Google Tools/.env"),
        Path("M:/Projects/Matt/Fight Bot/.env"),
    ]
    for env_path in search_paths:
        if env_path.exists():
            try:
                for line in env_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if line.startswith(f"{key_name}="):
                        return line.split("=", 1)[1].strip().strip('"\'')
            except Exception:
                pass
    return default


# --- Credentials & Brain Configuration ---
MORPHEUS_TOKEN = _load_env_val("MORPHEUS_DISCORD_TOKEN")
GEMINI_API_KEY = _load_env_val("GEMINI_API_KEY")
ANTHROPIC_API_KEY = _load_env_val("ANTHROPIC_API_KEY")
OPENAI_API_KEY = _load_env_val("OPENAI_API_KEY")

DEFAULT_PROVIDER = _load_env_val("LLM_PROVIDER", "gemini").lower()
CURRENT_PROVIDER = DEFAULT_PROVIDER

MODEL_MAP = {
    "gemini": _load_env_val("GEMINI_MODEL", "gemini-2.5-flash"),
    "claude": _load_env_val("CLAUDE_MODEL", "claude-3-5-haiku-20241022"),
    "openai": _load_env_val("OPENAI_MODEL", "gpt-4o-mini"),
}

START_TIME = time.time()

# --- Discord Gateway Setup ---
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix="!m ", intents=intents)


def log_telemetry(event_type: str, data: dict[str, Any]) -> None:
    """Record an interaction event for live QA analysis."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "data": data,
    }
    with open(TELEMETRY_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
    print(f"📡 [Telemetry: {event_type}] {json.dumps(data)[:140]}")


# ==============================================================================
# Database Helpers (Fight Bot Court DB Integration)
# ==============================================================================

def get_court_db() -> Optional[sqlite3.Connection]:
    """Connect to Fight Bot's court database if present."""
    if not FIGHTBOT_DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(str(FIGHTBOT_DB_PATH), timeout=10.0)
        con.row_factory = sqlite3.Row
        return con
    except Exception as e:
        print(f"⚠️ court.db connection error: {e}")
        return None


def get_fight_by_message_id(message_id: int) -> Optional[dict[str, Any]]:
    """Look up a fight from its challenge card message ID."""
    con = get_court_db()
    if not con:
        return None
    try:
        row = con.execute("SELECT * FROM fights WHERE card_message_id = ?", (int(message_id),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def get_fight_by_thread_id(thread_id: int) -> Optional[dict[str, Any]]:
    """Look up an active fight from its argument thread ID."""
    con = get_court_db()
    if not con:
        return None
    try:
        row = con.execute("SELECT * FROM fights WHERE thread_id = ?", (int(thread_id),)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def update_fight_row(fight_id: int, **fields: Any) -> bool:
    """Update fields on a fight record in court.db."""
    con = get_court_db()
    if not con:
        return False
    try:
        set_clauses = [f"{k} = ?" for k in fields.keys()]
        sql = f"UPDATE fights SET {', '.join(set_clauses)} WHERE id = ?"
        params = list(fields.values()) + [int(fight_id)]
        con.execute(sql, params)
        con.commit()
        return True
    except Exception as e:
        print(f"⚠️ Error updating fight {fight_id}: {e}")
        return False
    finally:
        con.close()


# ==============================================================================
# LLM Providers (Swappable Brain Engine)
# ==============================================================================

def query_gemini(prompt: str, system_prompt: str) -> str:
    """Query Google Gemini API."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured in .env")
    model = MODEL_MAP.get("gemini", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["candidates"][0]["content"]["parts"][0]["text"].strip()


def query_claude(prompt: str, system_prompt: str) -> str:
    """Query Anthropic Claude API (compatible with Claude Max API credits)."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not configured in .env")
    model = MODEL_MAP.get("claude", "claude-3-5-haiku-20241022")
    url = "https://api.anthropic.com/v1/messages"
    payload = {
        "model": model,
        "max_tokens": 1024,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "x-api-key": ANTHROPIC_API_KEY,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["content"][0]["text"].strip()


def query_openai(prompt: str, system_prompt: str) -> str:
    """Query OpenAI API."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured in .env")
    model = MODEL_MAP.get("openai", "gpt-4o-mini")
    url = "https://api.openai.com/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["choices"][0]["message"]["content"].strip()


def ask_brain(prompt: str, system_prompt: str = "You are Morpheus, an analytical martial debater in Fight Bot.") -> Tuple[str, str]:
    """Execute query using the active LLM provider with graceful fallback."""
    provider = CURRENT_PROVIDER.lower()
    model_name = MODEL_MAP.get(provider, "unknown")
    try:
        if provider in ("claude", "anthropic"):
            return query_claude(prompt, system_prompt), f"Claude ({model_name})"
        elif provider in ("openai", "gpt"):
            return query_openai(prompt, system_prompt), f"OpenAI ({model_name})"
        else:
            return query_gemini(prompt, system_prompt), f"Gemini ({model_name})"
    except Exception as e:
        print(f"⚠️ Primary brain ({provider}) error: {e}. Attempting fallback...")
        if provider != "gemini" and GEMINI_API_KEY:
            try:
                fb_model = MODEL_MAP.get("gemini", "gemini-2.5-flash")
                return query_gemini(prompt, system_prompt), f"Gemini Fallback ({fb_model})"
            except Exception as fb_err:
                print(f"❌ Fallback also failed: {fb_err}")
        return f"Debate synthesis failed: {e}", "Offline"


# ==============================================================================
# Autonomous Sparring Logic (Auto-Accept & In-Thread Rebuttals)
# ==============================================================================

async def handle_incoming_challenge(message: discord.Message) -> None:
    """Auto-accept challenges where Morpheus is the assigned opponent."""
    fight = get_fight_by_message_id(message.id)
    if not fight:
        return
    
    # Check if Morpheus is challenged and challenge is still proposed
    challengee_id = fight.get("challengee_id")
    status = fight.get("status")
    if challengee_id != bot.user.id or status != "proposed":
        return

    side_a = fight.get("side_a") or "Challenger"
    side_b = fight.get("side_b") or "Defender"
    fight_id = int(fight["id"])

    print(f"⚔️ [Auto-Accept] Morpheus challenged in Fight #{fight_id}: {side_a} vs {side_b}!")
    log_telemetry("auto_accept_triggered", {"fight_id": fight_id, "side_a": side_a, "side_b": side_b})

    # Create the argument thread under the challenge card message
    thread_name = f"{side_a} vs {side_b}"[:95]
    try:
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
    except Exception as e:
        print(f"⚠️ Failed to create argument thread: {e}")
        return

    # Update fight status to arguing in court.db
    now_iso = datetime.now(timezone.utc).isoformat()
    success = update_fight_row(
        fight_id,
        status="arguing",
        thread_id=int(thread.id),
        accepted_at=now_iso,
    )
    if not success:
        print("⚠️ Could not update fight status in court.db.")

    # Post Morpheus welcome / challenge acceptance post into the new thread
    welcome_msg = (
        f"⚔️ **Morpheus has accepted the challenge!**\n\n"
        f"**Matchup:** {side_a} vs {side_b}\n"
        f"🔴 **{side_a}**: <@{fight.get('advocate_a_id')}>\n"
        f"🔵 **{side_b}**: <@{bot.user.id}> *(Morpheus)*\n\n"
        f"State your case. Post your argument here and Morpheus will counter."
    )
    await thread.send(welcome_msg)
    log_telemetry("argument_thread_opened", {"fight_id": fight_id, "thread_id": thread.id})


async def handle_in_thread_argument(message: discord.Message) -> None:
    """Generate rebuttal when opponent posts an argument in an active fight thread."""
    if not isinstance(message.channel, discord.Thread):
        return

    thread = message.channel
    fight = get_fight_by_thread_id(thread.id)
    if not fight:
        return

    # Verify the fight is in arguing state
    if fight.get("status") != "arguing":
        return

    # Check if Morpheus is one of the advocates
    adv_a = fight.get("advocate_a_id")
    adv_b = fight.get("advocate_b_id")
    if bot.user.id not in (adv_a, adv_b):
        return

    # Determine Morpheus's side and opponent's side
    is_side_a = (bot.user.id == adv_a)
    my_side = "a" if is_side_a else "b"
    opp_side = "b" if is_side_a else "a"

    my_fighter = fight.get(f"side_{my_side}") or "Fighter A"
    opp_fighter = fight.get(f"side_{opp_side}") or "Fighter B"
    context = fight.get("context") or "Standard martial engagement, no prep time."
    fight_id = int(fight["id"])

    # Check if Morpheus has already rested or already posted in this thread
    rest_field = f"rest_{my_side}_at"
    if fight.get(rest_field):
        return  # Already rested

    # Verify opponent was the author of this message
    opp_user_id = fight.get(f"advocate_{opp_side}_id")
    if message.author.id != opp_user_id:
        return

    print(f"🥊 [Sparring] Opponent argument received in Fight #{fight_id}! Formulating counter...")
    log_telemetry("opponent_argument_received", {
        "fight_id": fight_id,
        "author": str(message.author),
        "content": message.content[:100],
    })

    # Formulate argument prompt
    prompt = (
        f"You are advocating for {my_fighter} against {opp_fighter} in an official Fight Bot debate.\n"
        f"Match Context: {context}\n"
        f"Opponent's Argument: \"{message.content}\"\n\n"
        f"Write a sharp, persuasive, and lore-accurate rebuttal explaining why {my_fighter} defeats {opp_fighter}. "
        f"Cite 2 concrete canonical feats, tactical superiority, or physical attributes. "
        "Keep it punchy (3 to 4 sentences). Do not break character."
    )
    system_prompt = (
        f"You are Morpheus, an elite martial analyst representing {my_fighter} in a battle debate. "
        "Speak with philosophical confidence, supreme precision, and undisputed canon knowledge."
    )

    async with thread.typing():
        argument, source = ask_brain(prompt, system_prompt)

    # Post rebuttal into the thread
    rebuttal_msg = (
        f"**Morpheus** *[{source}]*:\n"
        f"{argument}\n\n"
        f"⚖️ *Morpheus rests his case.*"
    )
    await thread.send(rebuttal_msg)

    # Mark Morpheus as rested in court.db
    now_iso = datetime.now(timezone.utc).isoformat()
    fields_to_update = {rest_field: now_iso}

    # If the opponent already rested, transition status to judge_ready for Fight Bot!
    opp_rest_field = f"rest_{opp_side}_at"
    if fight.get(opp_rest_field):
        fields_to_update["status"] = "judge_ready"
        await thread.send("⚖️ **Both advocates have rested.** The Court will now deliberate on the verdict.")
        print(f"🏁 Fight #{fight_id} is now JUDGE_READY!")

    update_fight_row(fight_id, **fields_to_update)
    log_telemetry("morpheus_rebuttal_posted", {"fight_id": fight_id, "source": source})


# ==============================================================================
# Bot Gateway Events
# ==============================================================================

@bot.event
async def on_ready():
    active_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
    print("=" * 65)
    print("🕶️  MORPHEUS - Autonomous Studio Manager & Sparring Partner")
    print(f"👤  Identity:      {bot.user} (ID: {bot.user.id})")
    print(f"🧠  Active Brain:  {CURRENT_PROVIDER.upper()} [{active_model}]")
    print(f"🏛️  Court DB:      {'Connected' if FIGHTBOT_DB_PATH.exists() else 'Offline'}")
    print(f"📂  Working Dir:   {BASE_DIR}")
    print("=" * 65)
    log_telemetry("morpheus_online", {
        "user": str(bot.user),
        "id": bot.user.id,
        "brain": CURRENT_PROVIDER,
        "model": active_model,
        "db_connected": FIGHTBOT_DB_PATH.exists(),
    })


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # 1. Check for incoming Fight Bot challenge card
    is_fight_bot = "Fight Bot" in message.author.name or message.author.bot
    if is_fight_bot and message.embeds:
        for embed in message.embeds:
            title = embed.title or ""
            desc = embed.description or ""
            if "Matchup:" in desc or "Challenge" in title:
                # Check if Morpheus is mentioned or in court.db
                if bot.user.mentioned_in(message) or "Morpheus" in desc:
                    await handle_incoming_challenge(message)

    # 2. Check for in-thread fight argument
    if isinstance(message.channel, discord.Thread):
        await handle_in_thread_argument(message)

    # 3. Process commands
    await bot.process_commands(message)


# ==============================================================================
# Bot Commands
# ==============================================================================

@bot.command(name="ping")
async def ping(ctx):
    """Health & latency check."""
    latency_ms = round(bot.latency * 1000, 1)
    await ctx.send(f"🕶️ The Matrix is online. Gateway latency: `{latency_ms}ms`")


@bot.command(name="brain")
async def brain(ctx, provider: str = None):
    """View or switch active LLM brain provider (gemini, claude, openai)."""
    global CURRENT_PROVIDER
    if not provider:
        cur_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
        embed = discord.Embed(
            title="🧠 Morpheus Brain Status",
            color=0x00F0FF,
        )
        embed.add_field(name="Active Provider", value=f"**{CURRENT_PROVIDER.upper()}**", inline=True)
        embed.add_field(name="Model", value=f"`{cur_model}`", inline=True)
        embed.add_field(
            name="Available Providers",
            value="• `gemini` (Gemini 2.5 Flash - Free Tier)\n• `claude` (Claude 3.5 Haiku - Claude Max Credits)\n• `openai` (GPT-4o mini)",
            inline=False,
        )
        embed.set_footer(text="Switch live using: !m brain <gemini|claude|openai>")
        await ctx.send(embed=embed)
        return

    prov = provider.lower().strip()
    if prov in ("gemini", "google"):
        CURRENT_PROVIDER = "gemini"
    elif prov in ("claude", "anthropic"):
        if not ANTHROPIC_API_KEY:
            await ctx.send("⚠️ `ANTHROPIC_API_KEY` is not set in `.env`. Add your key to use Claude.")
            return
        CURRENT_PROVIDER = "claude"
    elif prov in ("openai", "gpt"):
        if not OPENAI_API_KEY:
            await ctx.send("⚠️ `OPENAI_API_KEY` is not set in `.env`. Add your key to use OpenAI.")
            return
        CURRENT_PROVIDER = "openai"
    else:
        await ctx.send(f"❌ Unknown provider `{provider}`. Choose from `gemini`, `claude`, or `openai`.")
        return

    active_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
    log_telemetry("brain_switched", {"new_provider": CURRENT_PROVIDER, "model": active_model})
    await ctx.send(f"🕶️ Brain successfully swapped to **{CURRENT_PROVIDER.upper()}** (`{active_model}`). Ready to spar.")


@bot.command(name="debate")
async def debate(ctx, *, matchup: str):
    """Generate a persuasive martial argument for a matchup."""
    prompt = (
        f"Argue persuasively why your fighter wins in this matchup: {matchup}. "
        "Cite 1 or 2 concrete combat feats, physical stats, or tactical advantages in 3 concise sentences."
    )
    async with ctx.typing():
        argument, source = ask_brain(prompt)
    await ctx.send(f"**Morpheus** *[{source}]*:\n{argument}")


@bot.command(name="status")
async def status(ctx):
    """View Morpheus system status and uptime."""
    uptime_sec = int(time.time() - START_TIME)
    mins, secs = divmod(uptime_sec, 60)
    hours, mins = divmod(mins, 60)
    uptime_str = f"{hours}h {mins}m {secs}s"
    cur_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")

    embed = discord.Embed(title="🕶️ Morpheus System Telemetry", color=0x3498DB)
    embed.add_field(name="Uptime", value=uptime_str, inline=True)
    embed.add_field(name="Gateway Latency", value=f"{round(bot.latency * 1000, 1)}ms", inline=True)
    embed.add_field(name="Active Brain", value=f"{CURRENT_PROVIDER.upper()} (`{cur_model}`)", inline=False)
    embed.add_field(name="Court DB", value="Connected ✅" if FIGHTBOT_DB_PATH.exists() else "Offline ❌", inline=True)
    embed.add_field(name="Server Count", value=str(len(bot.guilds)), inline=True)
    embed.set_footer(text="Autonomous Studio Manager • Mountain Man Studios")
    await ctx.send(embed=embed)


# ==============================================================================
# Executable Entrypoint
# ==============================================================================

if __name__ == "__main__":
    if not MORPHEUS_TOKEN:
        print("❌ Error: MORPHEUS_DISCORD_TOKEN is not configured.")
        print("Set MORPHEUS_DISCORD_TOKEN in .env")
        input("\nPress Enter to exit...")
        sys.exit(1)

    print("🚀 Launching Morpheus bot gateway...")
    try:
        bot.run(MORPHEUS_TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        print("⚠️ Message Content Intent not enabled in Developer Portal. Falling back to default intents...")
        intents.message_content = False
        bot = commands.Bot(command_prefix="!m ", intents=intents)
        bot.run(MORPHEUS_TOKEN)
    except Exception as e:
        print(f"❌ Fatal error starting Morpheus: {e}")
        input("\nPress Enter to exit...")
