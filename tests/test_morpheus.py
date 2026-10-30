"""Unit tests for Morpheus pure helpers (no Discord gateway needed)."""
import io
import urllib.error
from types import SimpleNamespace

import pytest

import morpheus as m


# --- .env loading -----------------------------------------------------------

def test_env_file_empty_value_falls_through_to_default(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=\nGEMINI_MODEL=gemini-x\n", encoding="utf-8")
    monkeypatch.setattr(m, "ENV_SEARCH_PATHS", [env])
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    assert m._load_env_val("LLM_PROVIDER", "gemini") == "gemini"
    assert m._load_env_val("GEMINI_MODEL", "default") == "gemini-x"


def test_env_file_strips_quotes_and_ignores_comments(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("# TOKEN=commented\nTOKEN=\"abc 123\"\n", encoding="utf-8")
    monkeypatch.setattr(m, "ENV_SEARCH_PATHS", [env])
    monkeypatch.delenv("TOKEN", raising=False)
    assert m._load_env_val("TOKEN") == "abc 123"


# --- provider normalization ---------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    ("gemini", "gemini"), ("Google", "gemini"),
    ("claude", "claude"), ("ANTHROPIC", "claude"),
    ("openai", "openai"), ("gpt", "openai"),
    ("", None), ("llama", None),
])
def test_normalize_provider(raw, expected):
    assert m.normalize_provider(raw) == expected


def test_default_claude_model_is_not_retired():
    assert m.MODEL_MAP["claude"] != "claude-3-5-haiku-20241022"
    assert "3-5" not in m.MODEL_MAP["claude"]


# --- mention stripping --------------------------------------------------------

def test_strip_bot_mention_handles_both_mention_forms():
    assert m.strip_bot_mention("<@42> hello", 42) == "hello"
    assert m.strip_bot_mention("<@!42> hello", 42) == "hello"
    assert m.strip_bot_mention("hi <@!42> there <@42>", 42) == "hi  there"


def test_strip_bot_mention_bare_nickname_mention_is_empty():
    assert m.strip_bot_mention("<@!42>", 42) == ""


# --- Discord length limit -----------------------------------------------------

def test_truncate_for_discord_leaves_short_text_alone():
    assert m.truncate_for_discord("hello") == "hello"


def test_truncate_for_discord_caps_at_2000_chars():
    out = m.truncate_for_discord("x" * 5000)
    assert len(out) <= 2000
    assert out.endswith("…")


# --- ID coercion --------------------------------------------------------------

@pytest.mark.parametrize("raw,expected", [
    (123, 123), ("123", 123), (" 123 ", 123), (None, None), ("", None), ("abc", None), (12.0, 12),
])
def test_to_int(raw, expected):
    assert m.to_int(raw) == expected


# --- mention cooldown ---------------------------------------------------------

def test_cooldown_blocks_second_call_within_window():
    cd = m.Cooldown(seconds=10)
    assert cd.allow(1, now=100.0) is True
    assert cd.allow(1, now=105.0) is False
    assert cd.allow(2, now=105.0) is True
    assert cd.allow(1, now=110.0) is True


# --- negative cache for non-fight threads ------------------------------------

def test_ttl_set_expires_entries():
    cache = m.TTLSet(ttl=5)
    cache.add(7, now=0.0)
    assert cache.contains(7, now=4.0) is True
    assert cache.contains(7, now=6.0) is False
    assert cache.contains(8, now=0.0) is False


# --- HTTP error detail ---------------------------------------------------------

def test_http_error_detail_includes_response_body():
    body = io.BytesIO(b'{"error": {"message": "quota exceeded"}}')
    err = urllib.error.HTTPError("https://example.test", 429, "Too Many Requests", {}, body)
    detail = m.describe_http_error(err)
    assert "429" in detail
    assert "quota exceeded" in detail


# --- brain errors -------------------------------------------------------------

def test_ask_brain_raises_when_no_provider_is_configured(monkeypatch):
    monkeypatch.setattr(m, "CURRENT_PROVIDER", "gemini")
    monkeypatch.setattr(m, "GEMINI_API_KEY", "")
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(m, "OPENAI_API_KEY", "")
    with pytest.raises(m.BrainError):
        m.ask_brain("hi", "sys")


def test_ask_brain_falls_back_to_gemini_when_primary_fails(monkeypatch):
    monkeypatch.setattr(m, "CURRENT_PROVIDER", "claude")
    monkeypatch.setattr(m, "ANTHROPIC_API_KEY", "k")
    monkeypatch.setattr(m, "GEMINI_API_KEY", "k")

    def boom(prompt, system_prompt):
        raise RuntimeError("claude down")

    monkeypatch.setattr(m, "query_claude", boom)
    monkeypatch.setattr(m, "query_gemini", lambda p, s: "gemini says hi")
    text, source = m.ask_brain("hi", "sys")
    assert text == "gemini says hi"
    assert "Fallback" in source


# --- challenge card detection ---------------------------------------------------

def _embed(title="", description=""):
    return SimpleNamespace(title=title, description=description)


def test_looks_like_challenge_card_matches_title_or_matchup():
    assert m.looks_like_challenge_card([_embed(title="Challenge Card")]) is True
    assert m.looks_like_challenge_card([_embed(description="Matchup: A vs B")]) is True
    assert m.looks_like_challenge_card([_embed(title="Verdict")]) is False
    assert m.looks_like_challenge_card([]) is False


def test_is_fight_bot_author_requires_bot_flag():
    human = SimpleNamespace(bot=False, id=1, name="Fight Bot")
    fightbot = SimpleNamespace(bot=True, id=2, name="Fight Bot")
    other_bot = SimpleNamespace(bot=True, id=3, name="GitHub")
    assert m.is_fight_bot_author(human, self_id=99) is False
    assert m.is_fight_bot_author(fightbot, self_id=99) is True
    assert m.is_fight_bot_author(other_bot, self_id=99) is False


def test_is_fight_bot_author_prefers_configured_id(monkeypatch):
    monkeypatch.setattr(m, "FIGHTBOT_USER_ID", 555)
    named = SimpleNamespace(bot=True, id=2, name="Fight Bot")
    configured = SimpleNamespace(bot=True, id=555, name="Whatever")
    assert m.is_fight_bot_author(named, self_id=99) is False
    assert m.is_fight_bot_author(configured, self_id=99) is True


# --- mention trigger predicate ---------------------------------------------------

def _msg(author_bot=False, author_id=1, mentions=(), everyone=False):
    return SimpleNamespace(
        author=SimpleNamespace(bot=author_bot, id=author_id),
        mentions=[SimpleNamespace(id=i) for i in mentions],
        mention_everyone=everyone,
    )


def test_should_answer_mention_ignores_bots_and_everyone():
    assert m.should_answer_mention(_msg(mentions=[42]), self_id=42) is True
    assert m.should_answer_mention(_msg(author_bot=True, mentions=[42]), self_id=42) is False
    assert m.should_answer_mention(_msg(mentions=[42], everyone=True), self_id=42) is False
    assert m.should_answer_mention(_msg(mentions=[7]), self_id=42) is False
