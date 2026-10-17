#!/usr/bin/env python3
"""Morpheus - The Autonomous Discord Sparring Partner & Testing Agent.

Acts as Player 2 in Fight Bot matches, interacts with Discord UI components,
generates fighter arguments and counter-points using swappable LLM providers
(Google Gemini, Anthropic Claude, OpenAI), and records end-to-end telemetry.
"""

import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Tuple

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


def _load_env_val(key_name: str, default: str = "") -> str:
    """Load configuration from environment or .env files."""
    val = os.environ.get(key_name, "").strip()
    if val:
        return val
    search_paths = [
        BASE_DIR / ".env",
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
    print(f"📡 [Telemetry: {event_type}] {json.dumps(data)[:120]}")


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
    with urllib.request.urlopen(req, timeout=15) as resp:
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
    with urllib.request.urlopen(req, timeout=15) as resp:
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
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))
        return res["choices"][0]["message"]["content"].strip()


def ask_brain(prompt: str, system_prompt: str = "You are Morpheus, an analytical martial debater in Fight Bot.") -> Tuple[str, str]:
    """Execute query using the active LLM provider with fallback."""
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
        print(f"⚠️ Primary brain ({provider}) error: {e}. Attempting Gemini fallback...")
        if provider != "gemini" and GEMINI_API_KEY:
            try:
                fb_model = MODEL_MAP.get("gemini", "gemini-2.5-flash")
                return query_gemini(prompt, system_prompt), f"Gemini Fallback ({fb_model})"
            except Exception as fb_err:
                print(f"❌ Fallback also failed: {fb_err}")
        return f"Argument generation interrupted: {e}", "Offline"


# ==============================================================================
# Bot Gateway Events & Logic
# ==============================================================================

@bot.event
async def on_ready():
    active_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
    print("=" * 60)
    print("🕶️  MORPHEUS - Autonomous Sparring Partner & Testing Agent")
    print(f"👤  Discord Identity: {bot.user} (ID: {bot.user.id})")
    print(f"🧠  Active Brain:     {CURRENT_PROVIDER.upper()} [{active_model}]")
    print(f"📂  Working Directory: {BASE_DIR}")
    print("=" * 60)
    log_telemetry("morpheus_online", {
        "user": str(bot.user),
        "id": bot.user.id,
        "brain": CURRENT_PROVIDER,
        "model": active_model,
    })


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Check if Fight Bot posted a match card or challenge
    is_fight_bot = "Fight Bot" in message.author.name or message.author.bot
    if is_fight_bot and message.embeds:
        for embed in message.embeds:
            title = embed.title or ""
            desc = embed.description or ""
            if "Matchup:" in desc or "Challenge" in title or "Next move:" in desc:
                log_telemetry("challenge_detected", {
                    "message_id": message.id,
                    "channel_id": message.channel.id,
                    "title": title,
                    "description": desc[:150],
                })
                if "Morpheus" in desc or bot.user.mentioned_in(message):
                    print("⚔️ Morpheus identified in matchup! Analyzing combat feats...")

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
            color=0x2ECC71,
        )
        embed.add_field(name="Active Provider", value=f"**{CURRENT_PROVIDER.upper()}**", inline=True)
        embed.add_field(name="Model", value=f"`{cur_model}`", inline=True)
        embed.add_field(
            name="Available Providers",
            value="• `gemini` (Gemini 2.5 Flash - Free Tier)\n• `claude` (Claude 3.5 Haiku / Sonnet - Claude Max Credits)\n• `openai` (GPT-4o mini)",
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
        "Cite 1 or 2 concrete combat feats, physical stats, or tactical advantages in 3 concise, punchy sentences."
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
    embed.add_field(name="Server Count", value=str(len(bot.guilds)), inline=True)
    embed.set_footer(text="Autonomous Sparring Partner • Mountain Man Studios")
    await ctx.send(embed=embed)


# ==============================================================================
# Executable Entrypoint
# ==============================================================================

if __name__ == "__main__":
    if not MORPHEUS_TOKEN:
        print("❌ Error: MORPHEUS_DISCORD_TOKEN is not configured.")
        print("Set MORPHEUS_DISCORD_TOKEN in M:/Tools/Google Tools/.env")
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
