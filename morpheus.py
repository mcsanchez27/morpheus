#!/usr/bin/env python3
"""Morpheus - The Autonomous Discord Studio Manager & Sparring Partner.

Mountain Man Studios (Server ID: 1560796428161388595)

Capabilities:
1. Autonomous Player 2 Sparring Agent: Auto-accepts Fight Bot challenges,
   creates argument threads, analyzes opponent arguments, and posts lore-accurate
   rebuttals citing canonical feats using swappable AI brains.
2. Swappable Multi-Brain Engine: Google Gemini (2.5 Flash), Anthropic Claude
   (Haiku 5.5 by default), and OpenAI (GPT-4o mini).
3. Live In-Chat Model Hot-Swapping (`!m brain claude`, `!m brain gemini`).
4. Live Telemetry & QA Logging to JSON lines.
"""

import asyncio
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional, Tuple

# Ensure UTF-8 output on Windows (stdout can be None under pythonw / --noconsole builds)
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None and (_stream.encoding or "").lower() != "utf-8":
        try:
            _stream.reconfigure(encoding="utf-8")
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

ENV_SEARCH_PATHS = [
    BASE_DIR / ".env",
    Path("M:/Projects/Mountain Man Studios/morpheus/.env"),
    Path("M:/Tools/Google Tools/.env"),
    Path("M:/Projects/Matt/Fight Bot/.env"),
]


def _read_env_file(env_path: Path, key_name: str) -> Optional[str]:
    """Return the value for key_name from a .env file, or None if absent/empty."""
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return None
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith(f"{key_name}="):
            value = line.split("=", 1)[1].strip().strip('"\'')
            return value or None
    return None


def _load_env_val(key_name: str, default: str = "") -> str:
    """Load configuration from the environment or the first .env file that defines it.

    Empty values (``KEY=``) are treated as unset so the default still applies.
    """
    val = os.environ.get(key_name, "").strip()
    if val:
        return val
    for env_path in ENV_SEARCH_PATHS:
        if env_path.exists():
            found = _read_env_file(env_path, key_name)
            if found:
                return found
    return default


def to_int(value: Any) -> Optional[int]:
    """Coerce a Discord snowflake stored as int/str/float to int (None if impossible)."""
    if value is None:
        return None
    try:
        if isinstance(value, str):
            value = value.strip()
            if not value:
                return None
        return int(value)
    except (TypeError, ValueError):
        return None


FIGHTBOT_DB_PATH = Path(_load_env_val("FIGHTBOT_DB_PATH", "M:/Projects/Matt/Fight Bot/data/court.db"))
# Optional: pin challenge detection to Fight Bot's user ID instead of matching on its name.
FIGHTBOT_USER_ID = to_int(_load_env_val("FIGHTBOT_USER_ID"))

# --- Credentials & Brain Configuration ---
MORPHEUS_TOKEN = _load_env_val("MORPHEUS_DISCORD_TOKEN")
GEMINI_API_KEY = _load_env_val("GEMINI_API_KEY")
ANTHROPIC_API_KEY = _load_env_val("ANTHROPIC_API_KEY")
OPENAI_API_KEY = _load_env_val("OPENAI_API_KEY")

PROVIDER_ALIASES = {
    "gemini": "gemini", "google": "gemini",
    "claude": "claude", "anthropic": "claude",
    "openai": "openai", "gpt": "openai",
}


def normalize_provider(name: Optional[str]) -> Optional[str]:
    """Map a user/env supplied provider name to a canonical key, or None if unknown."""
    if not name:
        return None
    return PROVIDER_ALIASES.get(name.strip().lower())


DEFAULT_PROVIDER = normalize_provider(_load_env_val("LLM_PROVIDER", "gemini")) or "gemini"
CURRENT_PROVIDER = DEFAULT_PROVIDER

MODEL_MAP = {
    "gemini": _load_env_val("GEMINI_MODEL", "gemini-2.5-flash"),
    # claude-3-5-haiku-20241022 was retired by Anthropic on 2026-02-19.
    "claude": _load_env_val("CLAUDE_MODEL", "claude-haiku-5-5"),
    "openai": _load_env_val("OPENAI_MODEL", "gpt-4o-mini"),
}
PROVIDER_LABELS = {"gemini": "Gemini", "claude": "Claude", "openai": "OpenAI"}

# Discord hard limit for a single message body.
DISCORD_MAX_MESSAGE = 2000
# Cap model output so replies fit in one Discord message (roughly 4 chars/token).
MAX_OUTPUT_TOKENS = 450
HTTP_TIMEOUT_SEC = 20
# Seconds between LLM-backed mention replies per user.
MENTION_COOLDOWN_SEC = 15
# Fight Bot stores card_message_id after sending the card, so give it a moment.
CARD_LOOKUP_ATTEMPTS = 4
CARD_LOOKUP_DELAY_SEC = 1.5

START_TIME = time.time()

# --- Discord Gateway Setup ---
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!m ", intents=intents)


def log_telemetry(event_type: str, data: dict[str, Any]) -> None:
    """Record an interaction event for live QA analysis."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event_type,
        "data": data,
    }
    try:
        with open(TELEMETRY_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except OSError as e:
        print(f"⚠️ Telemetry write failed: {e}")
    print(f"📡 [Telemetry: {event_type}] {json.dumps(data)[:140]}")


# ==============================================================================
# Small utilities
# ==============================================================================

def truncate_for_discord(text: str, limit: int = DISCORD_MAX_MESSAGE) -> str:
    """Trim text so it fits in a single Discord message."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def strip_bot_mention(content: str, bot_id: int) -> str:
    """Remove both ``<@id>`` and ``<@!id>`` mention forms for the bot."""
    return re.sub(rf"<@!?{bot_id}>", "", content).strip()


class Cooldown:
    """Per-key cooldown gate (used to rate-limit paid LLM calls)."""

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._last: dict[Any, float] = {}

    def allow(self, key: Any, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        last = self._last.get(key)
        if last is not None and now - last < self.seconds:
            return False
        self._last[key] = now
        return True


class TTLSet:
    """Set whose members expire after ``ttl`` seconds (negative cache for DB misses)."""

    def __init__(self, ttl: float):
        self.ttl = ttl
        self._expiry: dict[Any, float] = {}

    def add(self, key: Any, now: Optional[float] = None) -> None:
        now = time.monotonic() if now is None else now
        self._expiry[key] = now + self.ttl
        if len(self._expiry) > 2048:
            self._expiry = {k: v for k, v in self._expiry.items() if v > now}

    def contains(self, key: Any, now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else now
        exp = self._expiry.get(key)
        if exp is None:
            return False
        if exp <= now:
            del self._expiry[key]
            return False
        return True


def describe_http_error(err: Exception) -> str:
    """Human-readable error including the API's JSON body when available."""
    if isinstance(err, urllib.error.HTTPError):
        body = ""
        try:
            body = err.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        return f"HTTP {err.code} {err.reason}: {body}".strip()
    return f"{type(err).__name__}: {err}"


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


def _fetch_fight(where_sql: str, param: int) -> Optional[dict[str, Any]]:
    con = get_court_db()
    if not con:
        return None
    try:
        row = con.execute(f"SELECT * FROM fights WHERE {where_sql} = ?", (int(param),)).fetchone()
        return dict(row) if row else None
    except Exception as e:
        print(f"⚠️ court.db query error ({where_sql}): {e}")
        return None
    finally:
        con.close()


def get_fight_by_message_id(message_id: int) -> Optional[dict[str, Any]]:
    """Look up a fight from its challenge card message ID."""
    return _fetch_fight("card_message_id", message_id)


def get_fight_by_thread_id(thread_id: int) -> Optional[dict[str, Any]]:
    """Look up an active fight from its argument thread ID."""
    return _fetch_fight("thread_id", thread_id)


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

class BrainError(RuntimeError):
    """Raised when every configured LLM provider failed to answer."""


def _post_json(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SEC) as resp:
        return json.loads(resp.read().decode("utf-8"))


def query_gemini(prompt: str, system_prompt: str) -> str:
    """Query Google Gemini API."""
    if not GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured in .env")
    model = MODEL_MAP.get("gemini", "gemini-2.5-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "systemInstruction": {"parts": [{"text": system_prompt}]},
        "generationConfig": {"maxOutputTokens": MAX_OUTPUT_TOKENS},
    }
    # Key goes in a header rather than the query string so it never lands in URL logs.
    res = _post_json(url, payload, {"x-goog-api-key": GEMINI_API_KEY})
    try:
        candidate = res["candidates"][0]
        return "".join(p.get("text", "") for p in candidate["content"]["parts"]).strip()
    except (KeyError, IndexError, TypeError):
        reason = (res.get("promptFeedback") or {}).get("blockReason") or (
            (res.get("candidates") or [{}])[0].get("finishReason")
        )
        raise ValueError(f"Gemini returned no text (reason: {reason or 'unknown'})")


def query_claude(prompt: str, system_prompt: str) -> str:
    """Query Anthropic Claude API."""
    if not ANTHROPIC_API_KEY:
        raise ValueError("ANTHROPIC_API_KEY not configured in .env")
    model = MODEL_MAP.get("claude", "claude-haiku-5-5")
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
    }
    res = _post_json(
        "https://api.anthropic.com/v1/messages",
        payload,
        {"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
    )
    text = "".join(block.get("text", "") for block in res.get("content", []) if block.get("type") == "text")
    if not text.strip():
        raise ValueError(f"Claude returned no text (stop_reason: {res.get('stop_reason')})")
    return text.strip()


def query_openai(prompt: str, system_prompt: str) -> str:
    """Query OpenAI API."""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured in .env")
    model = MODEL_MAP.get("openai", "gpt-4o-mini")
    payload = {
        "model": model,
        "max_tokens": MAX_OUTPUT_TOKENS,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
    }
    res = _post_json(
        "https://api.openai.com/v1/chat/completions",
        payload,
        {"Authorization": f"Bearer {OPENAI_API_KEY}"},
    )
    return res["choices"][0]["message"]["content"].strip()



def ask_brain(prompt: str, system_prompt: str = "You are Morpheus, an analytical martial debater in Fight Bot.") -> Tuple[str, str]:
    """Execute a query using the active LLM provider, falling back to Gemini.

    Blocking: call via ``ask_brain_async`` from the event loop. Raises BrainError
    when no provider could answer, so callers never post an error as an argument.
    """
    provider = normalize_provider(CURRENT_PROVIDER) or "gemini"
    model_name = MODEL_MAP.get(provider, "unknown")
    # Look the function up at call time so tests (and hot-swaps) can monkeypatch it.
    query_fn = globals()[f"query_{provider}"]
    try:
        return query_fn(prompt, system_prompt), f"{PROVIDER_LABELS[provider]} ({model_name})"
    except Exception as e:
        primary_err = describe_http_error(e)
        print(f"⚠️ Primary brain ({provider}) error: {primary_err}. Attempting fallback...")
        if provider != "gemini" and GEMINI_API_KEY:
            try:
                fb_model = MODEL_MAP.get("gemini", "gemini-2.5-flash")
                return globals()["query_gemini"](prompt, system_prompt), f"Gemini Fallback ({fb_model})"
            except Exception as fb_err:
                print(f"❌ Fallback also failed: {describe_http_error(fb_err)}")
        raise BrainError(primary_err) from e


async def ask_brain_async(prompt: str, system_prompt: Optional[str] = None) -> Tuple[str, str]:
    """Run ask_brain in a worker thread so the gateway heartbeat keeps flowing."""
    if system_prompt is None:
        return await asyncio.to_thread(ask_brain, prompt)
    return await asyncio.to_thread(ask_brain, prompt, system_prompt)


# ==============================================================================
# Message classification helpers
# ==============================================================================

def looks_like_challenge_card(embeds: Iterable[Any]) -> bool:
    """True if any embed looks like a Fight Bot challenge card."""
    for embed in embeds:
        title = getattr(embed, "title", None) or ""
        desc = getattr(embed, "description", None) or ""
        if "Matchup:" in desc or "Challenge" in title or "Card" in title:
            return True
    return False


def is_fight_bot_author(author: Any, self_id: int) -> bool:
    """True if the message author is Fight Bot (by configured ID, else by name)."""
    if not getattr(author, "bot", False) or author.id == self_id:
        return False
    if FIGHTBOT_USER_ID is not None:
        return author.id == FIGHTBOT_USER_ID
    return "fight bot" in (getattr(author, "name", "") or "").lower()


def should_answer_mention(message: Any, self_id: int) -> bool:
    """True for a direct @mention from a human (never for bots or @everyone)."""
    if getattr(message.author, "bot", False):
        return False
    if message.mention_everyone:
        return False
    return any(u.id == self_id for u in message.mentions)


# ==============================================================================
# Autonomous Sparring Logic (Auto-Accept & In-Thread Rebuttals)
# ==============================================================================

# Fights with a rebuttal currently being generated (prevents double rebuttals).
REBUTTALS_IN_PROGRESS: set[int] = set()
# Thread IDs recently confirmed not to be fight threads (skip the DB for a while).
NON_FIGHT_THREADS = TTLSet(ttl=60)
MENTION_COOLDOWN = Cooldown(MENTION_COOLDOWN_SEC)


async def _lookup_card_fight(message_id: int) -> Optional[dict[str, Any]]:
    """Fight Bot writes card_message_id after sending the card, so retry briefly."""
    for attempt in range(CARD_LOOKUP_ATTEMPTS):
        fight = await asyncio.to_thread(get_fight_by_message_id, message_id)
        if fight:
            return fight
        if attempt < CARD_LOOKUP_ATTEMPTS - 1:
            await asyncio.sleep(CARD_LOOKUP_DELAY_SEC)
    return None


async def handle_incoming_challenge(message: discord.Message) -> None:
    """Auto-accept challenges where Morpheus is the assigned opponent (or the challenge is open)."""
    fight = await _lookup_card_fight(message.id)
    if not fight:
        return

    if fight.get("status") != "proposed":
        return

    challengee_id = to_int(fight.get("challengee_id"))
    # Accept if open challenge (None) or specifically targeted to Morpheus
    if challengee_id is not None and challengee_id != bot.user.id:
        return

    side_a = fight.get("side_a") or "Challenger"
    side_b = fight.get("side_b") or "Defender"
    fight_id = int(fight["id"])

    print(f"⚔️ [Auto-Accept] Morpheus challenged in Fight #{fight_id}: {side_a} vs {side_b}!")
    log_telemetry("auto_accept_triggered", {"fight_id": fight_id, "side_a": side_a, "side_b": side_b})

    thread_name = f"{side_a} vs {side_b}"[:95]
    try:
        thread = await message.create_thread(name=thread_name, auto_archive_duration=1440)
    except Exception as e:
        print(f"⚠️ Failed to create argument thread: {e}")
        return

    # Claim the fight in court.db before announcing anything; roll the thread back on failure.
    now_iso = datetime.now(timezone.utc).isoformat()
    success = await asyncio.to_thread(
        update_fight_row,
        fight_id,
        status="arguing",
        thread_id=int(thread.id),
        advocate_b_id=int(bot.user.id),
        advocate_b_name="Morpheus",
        accepted_at=now_iso,
    )
    if not success:
        print("⚠️ Could not update fight status in court.db; removing orphan thread.")
        try:
            await thread.delete()
        except Exception as e:
            print(f"⚠️ Could not delete orphan thread {thread.id}: {e}")
        return

    welcome_msg = (
        f"🕶️ **Morpheus has accepted the challenge!**\n\n"
        f"**Matchup:** {side_a} vs {side_b}\n"
        f"🔴 **{side_a}**: <@{fight.get('advocate_a_id')}>\n"
        f"🔵 **{side_b}**: <@{bot.user.id}> *(Morpheus)*\n\n"
        f"You believe {side_a} holds supremacy. Present your arguments here, advocate — and Morpheus will counter."
    )
    await thread.send(truncate_for_discord(welcome_msg))
    log_telemetry("argument_thread_opened", {"fight_id": fight_id, "thread_id": thread.id})


async def handle_in_thread_argument(message: discord.Message) -> None:
    """Generate a rebuttal when the opponent posts an argument in an active fight thread."""
    if not isinstance(message.channel, discord.Thread):
        return
    # Cheap filters first so most thread traffic never touches court.db.
    if message.author.bot or not message.content.strip():
        return

    thread = message.channel
    if NON_FIGHT_THREADS.contains(thread.id):
        return

    fight = await asyncio.to_thread(get_fight_by_thread_id, thread.id)
    if not fight:
        NON_FIGHT_THREADS.add(thread.id)
        return

    if fight.get("status") != "arguing":
        return

    adv_a = to_int(fight.get("advocate_a_id"))
    adv_b = to_int(fight.get("advocate_b_id"))
    if bot.user.id not in (adv_a, adv_b):
        return

    is_side_a = (bot.user.id == adv_a)
    my_side = "a" if is_side_a else "b"
    opp_side = "b" if is_side_a else "a"

    my_fighter = fight.get(f"side_{my_side}") or "Fighter A"
    opp_fighter = fight.get(f"side_{opp_side}") or "Fighter B"
    context = fight.get("context") or "Standard martial engagement, no prep time."
    fight_id = int(fight["id"])

    rest_field = f"rest_{my_side}_at"
    if fight.get(rest_field):
        return  # Already rested

    opp_user_id = adv_b if is_side_a else adv_a
    if message.author.id != opp_user_id:
        return

    if fight_id in REBUTTALS_IN_PROGRESS:
        return
    REBUTTALS_IN_PROGRESS.add(fight_id)
    try:
        print(f"🥊 [Sparring] Opponent argument received in Fight #{fight_id}! Formulating counter...")
        log_telemetry("opponent_argument_received", {
            "fight_id": fight_id,
            "author": str(message.author),
            "content": message.content[:100],
        })

        prompt = (
            f"You are advocating for {my_fighter} against {opp_fighter} in an official Fight Bot debate.\n"
            f"Match Context: {context}\n"
            f"Opponent's Argument: \"{message.content[:1500]}\"\n\n"
            f"Write a sharp, persuasive, and lore-accurate rebuttal explaining why {my_fighter} defeats {opp_fighter}. "
            f"Cite 2 concrete canonical feats, tactical superiority, or physical attributes. "
            "Keep it punchy (3 to 4 sentences). Do not break character."
        )
        system_prompt = (
            f"You are Morpheus, an elite martial analyst representing {my_fighter} in a battle debate. "
            "Speak with philosophical confidence, supreme precision, and undisputed canon knowledge."
        )

        try:
            async with thread.typing():
                argument, source = await ask_brain_async(prompt, system_prompt)
        except BrainError as e:
            # Do not rest on a failed synthesis; the next opponent message will retry.
            print(f"❌ Rebuttal synthesis failed for Fight #{fight_id}: {e}")
            log_telemetry("morpheus_rebuttal_failed", {"fight_id": fight_id, "error": str(e)[:200]})
            await thread.send("🕶️ *Morpheus pauses to gather his thoughts. Restate your argument and he will answer.*")
            return

        rebuttal_msg = truncate_for_discord(
            f"**Morpheus** *[{source}]*:\n"
            f"{argument}\n\n"
            f"⚖️ *Morpheus rests his case.*"
        )
        await thread.send(rebuttal_msg)

        now_iso = datetime.now(timezone.utc).isoformat()
        fields_to_update = {rest_field: now_iso}

        opp_rest_field = f"rest_{opp_side}_at"
        if fight.get(opp_rest_field):
            fields_to_update["status"] = "judge_ready"
            await thread.send("⚖️ **Both advocates have rested.** The Court will now deliberate on the verdict.")
            print(f"🏁 Fight #{fight_id} is now JUDGE_READY!")

        await asyncio.to_thread(update_fight_row, fight_id, **fields_to_update)
        log_telemetry("morpheus_rebuttal_posted", {"fight_id": fight_id, "source": source})
    finally:
        REBUTTALS_IN_PROGRESS.discard(fight_id)


async def handle_mention(message: discord.Message) -> None:
    """Answer a direct @mention in character (outside fight threads)."""
    clean_text = strip_bot_mention(message.content, bot.user.id)
    if not clean_text:
        return
    if not MENTION_COOLDOWN.allow(message.author.id):
        return
    prompt = f"A user asks: \"{clean_text[:1500]}\""
    sys_prompt = (
        "You are Morpheus from The Matrix. Speak with calm, profound wisdom, philosophical gravity, "
        "and concise clarity. Do not break character."
    )
    try:
        async with message.channel.typing():
            reply, _ = await ask_brain_async(prompt, sys_prompt)
    except BrainError as e:
        print(f"❌ Mention reply failed: {e}")
        await message.reply("🕶️ The Matrix is quiet right now. Ask again in a moment.")
        return
    await message.reply(truncate_for_discord(f"🕶️ {reply}"))


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
    print(f"🏛️  Court DB:      {'Connected' if FIGHTBOT_DB_PATH.exists() else 'Offline'} ({FIGHTBOT_DB_PATH})")
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
    if bot.user is None or message.author.id == bot.user.id:
        return

    in_thread = isinstance(message.channel, discord.Thread)

    try:
        # 1. Incoming Fight Bot challenge card
        if message.embeds and is_fight_bot_author(message.author, bot.user.id) and looks_like_challenge_card(message.embeds):
            await handle_incoming_challenge(message)

        # 2. In-thread fight argument
        elif in_thread:
            await handle_in_thread_argument(message)

        # 3. Direct @mention outside fight threads
        elif should_answer_mention(message, bot.user.id):
            await handle_mention(message)
    except discord.HTTPException as e:
        print(f"⚠️ Discord API error while handling message {message.id}: {e}")
    except Exception as e:
        print(f"⚠️ Unexpected error while handling message {message.id}: {type(e).__name__}: {e}")

    # 4. Prefix commands always get a chance to run
    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"⚠️ Missing argument `{error.param.name}`. Usage: `!m {ctx.command.qualified_name} {ctx.command.signature}`")
        return
    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(f"⏳ Slow down. Try again in {error.retry_after:.0f}s.")
        return
    if isinstance(error, commands.CheckFailure):
        await ctx.send("🚫 You are not permitted to run that command.")
        return
    print(f"⚠️ Command error in !m {ctx.command}: {type(error).__name__}: {error}")
    await ctx.send("⚠️ Something went wrong running that command.")


# ==============================================================================
# Bot Commands
# ==============================================================================

@bot.command(name="ping")
async def ping(ctx):
    """Health & latency check."""
    latency_ms = round(bot.latency * 1000, 1)
    await ctx.send(f"🕶️ The Matrix is online. Gateway latency: `{latency_ms}ms`")


async def can_switch_brain(ctx: commands.Context) -> bool:
    """Only the bot owner or server managers may switch to (possibly paid) providers."""
    if await bot.is_owner(ctx.author):
        return True
    perms = getattr(ctx.author, "guild_permissions", None)
    return bool(perms and perms.manage_guild)


@bot.command(name="brain")
async def brain(ctx, provider: Optional[str] = None):
    """View or switch active LLM brain provider (gemini, claude, openai)."""
    global CURRENT_PROVIDER
    if not provider:
        cur_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
        embed = discord.Embed(title="🧠 Morpheus Brain Status", color=0x00F0FF)
        embed.add_field(name="Active Provider", value=f"**{CURRENT_PROVIDER.upper()}**", inline=True)
        embed.add_field(name="Model", value=f"`{cur_model}`", inline=True)
        embed.add_field(
            name="Available Providers",
            value=(
                f"• `gemini` (`{MODEL_MAP['gemini']}`){' ✅' if GEMINI_API_KEY else ' ⛔ no key'}\n"
                f"• `claude` (`{MODEL_MAP['claude']}`){' ✅' if ANTHROPIC_API_KEY else ' ⛔ no key'}\n"
                f"• `openai` (`{MODEL_MAP['openai']}`){' ✅' if OPENAI_API_KEY else ' ⛔ no key'}"
            ),
            inline=False,
        )
        embed.set_footer(text="Switch live using: !m brain <gemini|claude|openai>")
        await ctx.send(embed=embed)
        return

    if not await can_switch_brain(ctx):
        await ctx.send("🚫 Only the bot owner or server managers can switch brains.")
        return

    prov = normalize_provider(provider)
    if prov is None:
        await ctx.send(f"❌ Unknown provider `{provider}`. Choose from `gemini`, `claude`, or `openai`.")
        return

    key_for = {"gemini": GEMINI_API_KEY, "claude": ANTHROPIC_API_KEY, "openai": OPENAI_API_KEY}
    key_name = {"gemini": "GEMINI_API_KEY", "claude": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}
    if not key_for[prov]:
        await ctx.send(f"⚠️ `{key_name[prov]}` is not set in `.env`. Add your key to use {PROVIDER_LABELS[prov]}.")
        return

    CURRENT_PROVIDER = prov
    active_model = MODEL_MAP.get(CURRENT_PROVIDER, "default")
    log_telemetry("brain_switched", {"new_provider": CURRENT_PROVIDER, "model": active_model, "by": str(ctx.author)})
    await ctx.send(f"🕶️ Brain successfully swapped to **{CURRENT_PROVIDER.upper()}** (`{active_model}`). Ready to spar.")


@bot.command(name="debate")
@commands.cooldown(rate=1, per=15, type=commands.BucketType.user)
async def debate(ctx, *, matchup: str):
    """Generate a persuasive martial argument for a matchup."""
    prompt = (
        f"Argue persuasively why your fighter wins in this matchup: {matchup[:500]}. "
        "Cite 1 or 2 concrete combat feats, physical stats, or tactical advantages in 3 concise sentences."
    )
    try:
        async with ctx.typing():
            argument, source = await ask_brain_async(prompt)
    except BrainError as e:
        print(f"❌ !m debate failed: {e}")
        await ctx.send("🕶️ The Matrix is quiet right now. Try again in a moment.")
        return
    await ctx.send(truncate_for_discord(f"**Morpheus** *[{source}]*:\n{argument}"))


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

def _pause_before_exit() -> None:
    """Keep a double-clicked console window open; no-op when there is no TTY."""
    try:
        if sys.stdin is not None and sys.stdin.isatty():
            input("\nPress Enter to exit...")
    except (EOFError, OSError):
        pass


if __name__ == "__main__":
    if not MORPHEUS_TOKEN:
        print("❌ Error: MORPHEUS_DISCORD_TOKEN is not configured.")
        print("Set MORPHEUS_DISCORD_TOKEN in .env")
        _pause_before_exit()
        sys.exit(1)

    print("🚀 Launching Morpheus bot gateway...")
    try:
        bot.run(MORPHEUS_TOKEN)
    except discord.errors.PrivilegedIntentsRequired:
        # A fresh Bot() here would have none of the handlers above registered, so
        # fail loudly instead of connecting a bot that silently ignores everything.
        print("❌ The Message Content privileged intent is not enabled for this bot.")
        print("   Enable it in the Discord Developer Portal → Bot → Privileged Gateway Intents, then restart.")
        _pause_before_exit()
        sys.exit(1)
    except Exception as e:
        print(f"❌ Fatal error starting Morpheus: {e}")
        _pause_before_exit()
        sys.exit(1)
