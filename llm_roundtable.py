#!/usr/bin/env python3
"""
LLM Roundtable: a turn-taking conversation between ChatGPT, Gemini and Claude.

You write the opening post. The models then take turns in the order you choose,
each replying to what the others just said, at roughly the length of your post.
When the rounds are done you can add an entry of your own and let them carry
on. The whole conversation is saved after every turn, as a readable Markdown
transcript and as a JSON file.

SETUP (README.md has the full walk-through)
    1. Install the three SDKs. Either create the conda environment that comes
       with this repo:
           conda env create -f environment.yml
           conda activate roundtable
       or add them to an existing Python (3.11 or newer):
           pip install openai google-genai anthropic
    2. Get an API key for each model:
           ChatGPT   https://platform.openai.com/api-keys
           Gemini    https://aistudio.google.com/apikey
           Claude    https://platform.claude.com/settings/keys
    3. Run the script. It asks for any key it can't find and offers to save it
       in a file named ".env" next to this script. That file looks like this:
           OPENAI_API_KEY=your-openai-key
           GEMINI_API_KEY=your-gemini-key
           ANTHROPIC_API_KEY=your-anthropic-key
       (Environment variables with the same names work too.)

USAGE
    python llm_roundtable.py              asks for the order, rounds and your post
    python llm_roundtable.py --check      tests each API key with one tiny request
    python llm_roundtable.py --order claude,chatgpt,gemini --rounds 4
    python llm_roundtable.py --prompt "Is a hot dog a sandwich?" --words 60
    python llm_roundtable.py --prompt-file my_post.txt --rounds 3 --no-follow-up
    python llm_roundtable.py --name Rafael   what the models call you (remembered)
    python llm_roundtable.py --page conversations/NAME.json   a page to share

    python roundtable_gui.py              the same thing in a window (needs nicegui)
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import re
import shutil
import sys
import textwrap
from datetime import datetime
from pathlib import Path

# =============================================================================
# SETTINGS: the things you are most likely to want to change
# =============================================================================

# The model that plays each participant. These names were checked against each
# provider's documentation in November 2026. Providers rename and retire models
# often, so if one stops working, replace it here (and run --check to test it).
MODELS = {
    "chatgpt": "gpt-6.2-sol",
    "gemini": "gemini-3.8-flash",
    "claude": "claude-sonnet-5-5",
}

# Lighter and stronger models for each participant, cheapest first. The window
# (roundtable_gui.py) offers these in a list. On the command line, copy one into
# MODELS above. Stronger models cost more and take longer to reply.
MODEL_CHOICES = {
    "chatgpt": {
        "gpt-6-luna": "lightest and cheapest",
        "gpt-6.1-terra": "fast and efficient",
        "gpt-6.2-sol": "everyday work",
        "gpt-6.2-astra": "most capable",
    },
    "gemini": {
        "gemini-3.5-flash-lite": "lightest and cheapest",
        "gemini-3.8-flash": "everyday work",
        "gemini-4-argon": "most capable, paid keys only",
    },
    "claude": {
        "claude-haiku-5-5": "lightest and cheapest",
        "claude-sonnet-5-5": "everyday work",
        "claude-opus-5-5": "stronger",
        "claude-fable-5-2": "most capable",
    },
}

DEFAULT_ORDER = ["chatgpt", "gemini", "claude"]  # speaking order if you don't choose one
DEFAULT_ROUNDS = 3  # in one round, each participant speaks once

# Reply length. Each reply aims for about as many words as your opening post,
# kept between the two limits below. The models are told this is a guide, not a
# hard limit, so a reply may run longer when a point needs it. To use one fixed
# length instead, set REPLY_WORDS to a number (or run with --words).
REPLY_WORDS = None
MIN_REPLY_WORDS = 30
MAX_REPLY_WORDS = 400

# How hard each model thinks before replying: "low", "medium" or "high".
# "low" keeps a casual conversation quick and cheap. Use None to leave the
# setting out altogether and get each provider's own default.
REASONING_EFFORT = "low"

# Ceiling on the tokens one reply may use, INCLUDING the model's hidden
# thinking. It is a cost safety net, not the length control (the reply-length
# settings above are), so keep it generous, well above the longest reply you
# expect: if it is too low, a reply can come back empty.
MAX_OUTPUT_TOKENS = 8000

REQUEST_TIMEOUT = 180  # seconds to wait for one reply before giving up

# What the models call you, and how your posts are labelled. Run with --name once
# (or set it in the window) and it is remembered in settings.json.
USER_NAME = "User"

HERE = Path(__file__).resolve().parent
SAVE_DIR = HERE / "conversations"       # transcripts are written here
ENV_FILE = HERE / ".env"                # optional file holding your API keys
SETTINGS_FILE = HERE / "settings.json"  # remembers your name and the window's choices
PROJECT_URL = "https://github.com/shimmeringvoid/LLM_Roundtable"

# The standing instructions every model receives. Edit this to change the tone
# of the conversation (more debate, more humour, a particular audience...).
SYSTEM_PROMPT = """\
You are {name}, the AI model made by {maker}, taking part in a roundtable
conversation with {others}. A human, called "{user}" here, opened the
conversation with a post. {user} reads along and may add a further entry from
time to time. You speak in turns, in this repeating order: {order}.

What the others say reaches you labelled with the speaker's name in square
brackets, like "[{user}]: ...". Your own earlier replies appear as your
previous turns.

On each of your turns:
- Respond directly to what {others} said most recently. Agree, disagree,
  question or build on specific points, and mention them by name. Whenever
  {user} has just posted, whether the opening post or a later entry, respond
  to that first.
- Aim for about {words} words. That is a guide, not a limit: take more room
  when a point needs it, and never pad to reach it. Write plain conversational
  prose with no headings or bullet lists.
- Move the conversation forward. Add a new point, example or question instead
  of summarising or repeating, skip the compliments, and say so plainly when
  you disagree.
- Speak only for yourself. Never write lines for anyone else, and do not start
  with your own name or a label."""

# =============================================================================
# PARTICIPANTS AND API KEYS
# =============================================================================

# "env" lists the variable names checked for each API key, in order. A key you
# paste in when asked is saved under the first name.
PARTICIPANTS = {
    "chatgpt": {
        "name": "ChatGPT",
        "maker": "OpenAI",
        "package": "openai",
        "env": ["OPENAI_API_KEY"],
        "key_url": "https://platform.openai.com/api-keys",
    },
    "gemini": {
        "name": "Gemini",
        "maker": "Google",
        "package": "google-genai",
        "env": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
        "key_url": "https://aistudio.google.com/apikey",
    },
    "claude": {
        "name": "Claude",
        "maker": "Anthropic",
        "package": "anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "key_url": "https://platform.claude.com/settings/keys",
    },
}
ALIASES = {"gpt": "chatgpt", "openai": "chatgpt", "google": "gemini", "anthropic": "claude"}
KEY_NAMES = {var for info in PARTICIPANTS.values() for var in info["env"]}


class RoundtableError(Exception):
    """A problem with a plain-English message for the person running the script."""


def ask_user(prompt: str, default: str = "") -> str:
    """Like input(), but returns `default` instead of crashing if no input is available."""
    try:
        return input(prompt).strip()
    except EOFError:
        return default


def read_text(path: Path) -> str:
    """Read a text file saved as UTF-8 (or as UTF-16, which some Windows tools produce)."""
    try:
        data = path.read_bytes()
        return data.decode("utf-16" if data[:2] in (b"\xff\xfe", b"\xfe\xff") else "utf-8-sig")
    except OSError as err:
        raise RoundtableError(f"Couldn't read {path}: {err}") from None
    except UnicodeError:
        raise RoundtableError(f"Couldn't read {path}: it isn't UTF-8 text. Please re-save it as UTF-8.") from None


KEYS_FROM_FILE: set[str] = set()  # the key names that load_env_file took from the .env file


def env_name(line: str) -> str:
    """The variable a line of the .env file sets, e.g. "OPENAI_API_KEY" for "export OPENAI_API_KEY=abc"."""
    return line.strip().removeprefix("export ").partition("=")[0].strip()


def load_env_file() -> None:
    """Read API keys from NAME=value lines in the .env file. Real environment variables win."""
    if not ENV_FILE.exists():
        return
    for line in read_text(ENV_FILE).splitlines():
        name, words = env_name(line), line.partition("=")[2].split()
        # A key never contains spaces, so anything after the first word (a note, a comment) is ignored.
        if name in KEY_NAMES and words and not os.environ.get(name):
            os.environ[name] = words[0].strip("'\"")
            KEYS_FROM_FILE.add(name)


def find_api_key(who: str) -> tuple[str | None, str | None]:
    """Return a participant's API key and the variable it came from, or (None, None). Never asks."""
    for var in PARTICIPANTS[who]["env"]:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip(), var
    return None, None


def save_api_key(who: str, key: str) -> None:
    """Use this key from now on and write it to the .env file, replacing any earlier line for it.

    Raises OSError if the file can't be written; the key is then still used until the program ends.
    """
    var = PARTICIPANTS[who]["env"][0]
    os.environ[var] = key
    lines = read_text(ENV_FILE).splitlines() if ENV_FILE.exists() else []
    kept = [line for line in lines if env_name(line) != var]  # within the file, the first line for a name wins
    ENV_FILE.touch(mode=0o600, exist_ok=True)  # a new file is readable only by you from the start
    ENV_FILE.write_text("\n".join([*kept, f"{var}={key}"]) + "\n", encoding="utf-8")
    KEYS_FROM_FILE.add(var)
    try:
        ENV_FILE.chmod(0o600)  # ...and so is one that was already there (no effect on Windows)
    except OSError:
        pass


def get_api_key(who: str) -> str | None:
    """Return a participant's API key, asking for it if it isn't set up yet."""
    key, _ = find_api_key(who)
    if key:
        return key

    info = PARTICIPANTS[who]
    var = info["env"][0]
    print(f"\nNo API key found for {info['name']} (looked for {var}).")
    print(f"You can create one at {info['key_url']}")
    try:
        key = getpass.getpass("Paste the key and press Enter (it stays hidden), or just press Enter to skip: ")
    except EOFError:
        key = ""
    key = key.strip()
    if not key:
        return None

    os.environ[var] = key
    print(f"Got it: {len(key)} characters, ending in ...{key[-4:]}")
    if ask_user(f"Save it to {ENV_FILE} so you aren't asked again? [Y/n] ", default="n").lower() in ("", "y", "yes"):
        try:
            save_api_key(who, key)
        except OSError as err:
            print(f"Couldn't save it ({err}), so it will be used for this run only.")
            return key
        print("Saved. Keep that file private: anyone who has a key can spend your credit.")
    return key


def require_api_key(who: str) -> str:
    key = get_api_key(who)
    if not key:
        name = PARTICIPANTS[who]["name"]
        raise RoundtableError(
            f"No API key for {name}, so it can't take part. Add a key and run again, "
            f"or leave {name} out by choosing an order without it."
        )
    return key


# =============================================================================
# WHAT IS REMEMBERED BETWEEN RUNS
# settings.json holds your name and, for the window, the choices you last made.
# =============================================================================

def load_settings() -> dict:
    """Read settings.json. A missing or damaged file simply means nothing has been saved yet."""
    try:
        data = json.loads(read_text(SETTINGS_FILE)) if SETTINGS_FILE.exists() else {}
    except (RoundtableError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_settings(changes: dict) -> None:
    """Add `changes` to settings.json. Settings are a convenience, so a failure to save them is ignored."""
    try:
        text = json.dumps({**load_settings(), **changes}, indent=2, ensure_ascii=False)
        SETTINGS_FILE.write_text(text + "\n", encoding="utf-8")
    except OSError:
        pass


def clean_name(name) -> str:
    """Tidy and check a name for you. The models see it in labels like "[Rafael]: ...", so it has to be plain."""
    name = " ".join(name.split()) if isinstance(name, str) else ""
    if not name:
        raise RoundtableError("Please give a name.")
    if len(name) > 40 or re.search(r"[^\w .'’-]|_", name):
        raise RoundtableError("Please use a short name made of letters, numbers and spaces.")
    if name.lower() in {info["name"].lower() for info in PARTICIPANTS.values()}:
        raise RoundtableError(f"{name} is already at the table. Please choose a different name.")
    return name


def use_name(name) -> None:
    """Set what the models call you from here on. Anything unusable leaves the name as it was."""
    global USER_NAME
    try:
        USER_NAME = clean_name(name)
    except RoundtableError:
        pass


# =============================================================================
# TALKING TO THE THREE PROVIDERS
# Each ask_* function takes the same inputs (a system prompt plus a list of
# {"role": "user" | "assistant", "content": "..."} messages) and returns text.
# No temperature is set: several current models reject non-default values.
# =============================================================================

def make_client(who: str):
    """Create the SDK client for one participant. Nothing is sent to the provider yet."""
    try:  # each SDK is imported first, so a missing package is reported before any key is asked for
        if who == "chatgpt":
            from openai import OpenAI
            return OpenAI(api_key=require_api_key(who), timeout=REQUEST_TIMEOUT)
        if who == "gemini":
            from google import genai
            from google.genai import types
            return genai.Client(
                api_key=require_api_key(who),
                http_options=types.HttpOptions(
                    timeout=int(REQUEST_TIMEOUT * 1000),  # this SDK counts in milliseconds
                    retry_options=types.HttpRetryOptions(attempts=3),  # same as the other two SDKs
                ),
            )
        from anthropic import Anthropic
        return Anthropic(api_key=require_api_key(who), timeout=REQUEST_TIMEOUT)
    except ImportError:
        raise RoundtableError(
            f"The '{PARTICIPANTS[who]['package']}' package is not installed in this Python.\n"
            "If you use the project's conda environment, activate it first:\n"
            "    conda activate roundtable\n"
            "Otherwise install the SDKs with:\n"
            "    pip install openai google-genai anthropic"
        ) from None


def require_text(text: str | None, stop_reason) -> str:
    """Return the reply text, or raise a clear error if the model produced none."""
    if text and text.strip():
        return text
    raise RoundtableError(f"The reply was empty (the provider reported: {stop_reason}). {HINTS['empty']}")


def ask_chatgpt(client, model: str, system: str, messages: list[dict]) -> str:
    # OpenAI's Responses API takes the system prompt as `instructions`.
    options = {"reasoning": {"effort": REASONING_EFFORT}} if REASONING_EFFORT else {}
    response = client.responses.create(
        model=model,
        instructions=system,
        input=messages,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        **options,
    )
    return require_text(response.output_text, response.incomplete_details or response.status)


def ask_gemini(client, model: str, system: str, messages: list[dict]) -> str:
    # Gemini calls the assistant role "model" and wraps each message's text in "parts".
    # (generate_content is Google's original API. Google now steers new projects to its
    # "Interactions" API but documents this one as fully supported, and it is the stabler of the two.)
    from google.genai import types
    contents = [
        {"role": "model" if m["role"] == "assistant" else "user", "parts": [{"text": m["content"]}]}
        for m in messages
    ]
    thinking = types.ThinkingConfig(thinking_level=REASONING_EFFORT) if REASONING_EFFORT else None
    response = client.models.generate_content(
        model=model,
        contents=contents,
        config=types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            thinking_config=thinking,
        ),
    )
    stop_reason = response.candidates[0].finish_reason if response.candidates else response.prompt_feedback
    return require_text(response.text, stop_reason)


def ask_claude(client, model: str, system: str, messages: list[dict]) -> str:
    # Claude replies with a list of blocks (hidden thinking first); keep only the text blocks.
    options = {"output_config": {"effort": REASONING_EFFORT}} if REASONING_EFFORT else {}
    response = client.messages.create(
        model=model,
        system=system,
        messages=messages,
        max_tokens=MAX_OUTPUT_TOKENS,
        **options,
    )
    text = "".join(block.text for block in response.content if block.type == "text")
    return require_text(text, response.stop_reason)


ASK = {"chatgpt": ask_chatgpt, "gemini": ask_gemini, "claude": ask_claude}


# The usual cause of each kind of failure: by HTTP status, plus one for a reply that came back empty.
# (The window, where settings are buttons, words three of these its own way.)
HINTS = {
    400: "The provider rejected the request; its message above says why. If it names a "
         "setting it doesn't accept, try REASONING_EFFORT = None.",
    401: "The API key was rejected. Check that it was copied in full.",
    403: "This API key isn't allowed to do that (often: no access to this model).",
    404: "The model name in MODELS may be out of date.",
    429: "Rate limit or quota reached. Wait a minute, or check your credit and billing with the provider.",
    500: "The provider had a temporary problem. Try again in a little while.",
    "empty": "If that mentions a token limit, the model spent its whole allowance on thinking: "
             "raise MAX_OUTPUT_TOKENS.",
}


def explain(exc: Exception) -> str:
    """Describe an API error briefly, with a hint about the usual cause."""
    if isinstance(exc, RoundtableError):
        return str(exc)
    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)  # the HTTP status, if any
    if isinstance(status, int) and status >= 500:
        status = 500
    message = " ".join(str(exc).split())[:400]
    if status in (401, 403) and re.search(r"permission|scope", message, re.IGNORECASE):
        # The key itself is genuine; what it may do is limited (e.g. an OpenAI "Restricted" or "Read only" key).
        hint = (
            "The key was recognised but isn't allowed to make this request. If the key or its billing was "
            "only just set up, wait a few minutes and try again. Otherwise give the key full permissions on "
            "the provider's API keys page (for OpenAI: edit the key and set Permissions to All)."
        )
    else:
        hint = HINTS.get(status if isinstance(status, int) else None)
    return f"{type(exc).__name__}: {message}" + (f"\n  Hint: {hint}" if hint else "")


# =============================================================================
# THE CONVERSATION
# The transcript is a list of turns: {"speaker", "model", "round", "time", "text"}.
# =============================================================================

def make_turn(speaker: str, text: str, round_no: int, model: str | None = None) -> dict:
    return {
        "speaker": speaker,
        "model": model,
        "round": round_no,
        "time": datetime.now().isoformat(timespec="seconds"),
        "text": text,
    }


def system_prompt(who: str, order: list[str], words: int) -> str:
    """Fill in SYSTEM_PROMPT for one participant."""
    names = [PARTICIPANTS[key]["name"] for key in order]
    me = PARTICIPANTS[who]
    return SYSTEM_PROMPT.format(
        name=me["name"],
        maker=me["maker"],
        others=" and ".join(name for name in names if name != me["name"]),
        order=", ".join(names),
        user=USER_NAME,
        words=words,
    )


def reply_length(post: str, fixed: int | None) -> tuple[int, str]:
    """Decide how many words each reply should aim for, and say where the number came from."""
    if fixed:
        return fixed, "fixed"
    count = len(post.split())
    note = f"your opening post has {count} word{'' if count == 1 else 's'}"
    if count < MIN_REPLY_WORDS:
        note += f", and {MIN_REPLY_WORDS} is the minimum"
    elif count > MAX_REPLY_WORDS:
        note += f", and {MAX_REPLY_WORDS} is the maximum"
    rounded = int(count / 10 + 0.5) * 10  # to the nearest ten
    return max(MIN_REPLY_WORDS, min(rounded, MAX_REPLY_WORDS)), note


def messages_for(name: str, transcript: list[dict]) -> list[dict]:
    """Build the conversation as one participant sees it.

    Its own earlier replies become "assistant" messages. Everything the others
    said in between is merged into a single "user" message, each part labelled
    with its speaker, e.g. "[Gemini]: ...". The result alternates user and
    assistant and ends on a user message, the shape all three APIs expect. On
    every turn after its first, that final user message holds the other
    participants' latest replies, plus any entry the human has just added.
    """
    messages, heard = [], []
    for turn in transcript:
        if turn["speaker"] == name:
            messages.append({"role": "user", "content": "\n\n".join(heard)})
            messages.append({"role": "assistant", "content": turn["text"]})
            heard = []
        else:
            heard.append(f"[{turn['speaker']}]: {turn['text']}")
    messages.append({"role": "user", "content": "\n\n".join(heard)})
    return messages


def clean_reply(text: str, name: str) -> str:
    """Tidy a reply, dropping a leading "[Name]:" label if the model added one anyway."""
    label = rf"^\s*\**\[?{re.escape(name)}\]?\**\s*:\**\s*"
    return re.sub(label, "", text, count=1, flags=re.IGNORECASE).strip()


def take_turn(who: str, order: list[str], client, round_no: int, transcript: list[dict], words: int) -> None:
    """Ask one participant for its next reply and add it to the transcript."""
    name, model = PARTICIPANTS[who]["name"], MODELS[who]
    try:
        reply = ASK[who](client, model, system_prompt(who, order, words), messages_for(name, transcript))
    except Exception as exc:
        raise RoundtableError(f"{name} ({model}) could not reply.\n  {explain(exc)}") from exc
    transcript.append(make_turn(name, clean_reply(reply, name), round_no, model))


def save(base: Path, details: dict, transcript: list[dict]) -> None:
    """Write the whole conversation so far: a JSON record and a readable Markdown transcript."""
    base.parent.mkdir(parents=True, exist_ok=True)
    record = {**details, "turns": transcript}
    base.with_suffix(".json").write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")

    lines = [
        "# LLM roundtable",
        "",
        f"- Started: {details['started'].replace('T', ' ')}",
        f"- Speaking order: {', '.join(details['order'])}",
        "- Models: " + ", ".join(f"{name} = {model}" for name, model in details["models"].items()),
        "",
    ]
    current_round = None
    for turn in transcript:
        if turn["round"] != current_round:
            current_round = turn["round"]
            lines += [f"## Round {current_round}" if current_round else "## Opening post", ""]
        lines += [f"**{turn['speaker']}:** {turn['text']}", ""]
    base.with_suffix(".md").write_text("\n".join(lines), encoding="utf-8")


class Conversation:
    """One conversation: who is at the table, what has been said so far, and the files it is saved in.

    Both the command line (converse, below) and the window (roundtable_gui.py) run a conversation through this.
    """

    def __init__(self, order: list[str], post: str, fixed_words: int | None = None) -> None:
        self.order = order
        self.words, self.why = reply_length(post, fixed_words)
        self.round_no = 0
        started = datetime.now()
        stem = f"roundtable_{started:%Y-%m-%d_%H-%M-%S}"
        self.base, n = SAVE_DIR / stem, 2
        while self.base.with_suffix(".md").exists() or self.base.with_suffix(".json").exists():
            self.base, n = SAVE_DIR / f"{stem}_{n}", n + 1  # never overwrite an earlier conversation
        self.details = {
            "started": started.isoformat(timespec="seconds"),
            "order": [PARTICIPANTS[who]["name"] for who in order],
            "models": {PARTICIPANTS[who]["name"]: MODELS[who] for who in order},
            "reply_words": self.words,
            "reasoning_effort": REASONING_EFFORT,
        }
        self.transcript = [make_turn(USER_NAME, post, round_no=0)]
        try:
            self.save()  # saving first means a folder problem shows up before any API call
        except OSError as err:
            raise RoundtableError(f"Can't save conversations in {SAVE_DIR}: {err}") from None

    @classmethod
    def load(cls, path: Path) -> Conversation:
        """Reopen a saved conversation from its JSON file, to read it or to carry it on."""
        try:
            record = json.loads(read_text(path))
            names = {info["name"]: who for who, info in PARTICIPANTS.items()}
            talk = cls.__new__(cls)
            talk.transcript = record.pop("turns")
            talk.details = record
            talk.order = [names[name] for name in record["order"]]
            talk.words, talk.why = int(record["reply_words"]), "as before"
            talk.round_no = max(int(turn["round"]) for turn in talk.transcript)
            if not all(isinstance(turn["speaker"], str) and isinstance(turn["text"], str) for turn in talk.transcript):
                raise TypeError
        except (ValueError, KeyError, TypeError, AttributeError):
            raise RoundtableError(f"{path.name} isn't a conversation saved by this program.") from None
        talk.base = path.with_suffix("")
        return talk

    @property
    def human(self) -> str:
        """The name you go by in this conversation."""
        return self.transcript[0]["speaker"]

    def save(self) -> None:
        save(self.base, self.details, self.transcript)

    def speak(self, who: str, client) -> dict:
        """Ask one participant for its reply in the current round, save, and return the new turn."""
        take_turn(who, self.order, client, self.round_no, self.transcript, self.words)
        self.save()
        return self.transcript[-1]

    def add_entry(self, text: str) -> None:
        """Add an entry of yours. It opens the next round, so every model sees it before replying."""
        self.transcript.append(make_turn(self.human, text, self.round_no + 1))
        self.save()


# =============================================================================
# A PAGE TO SHARE
# Lays a saved conversation out for reading, in Markdown that GitHub and most
# other sites display well. Every word stays as it was said; only layout is added.
# =============================================================================

# One marker per speaker. These come from the first emoji set, which every emoji font has.
MARKERS = {"chatgpt": "🔴", "gemini": "🔵", "claude": "🔶"}
HUMAN_MARKER = "👤"
BLOCK_START = re.compile(r"\s*(#{1,6}\s|>|[-+*]\s|\d+[.)]\s|```|~~~|\|)|\s{4}|\t")


def as_typed(line: str) -> str:
    """Markdown that displays a line of plain typed text exactly as it was typed."""
    pieces = re.split(r"(https?://[^\s<>]*[^\s<>.,;:!?)\]'\"])", re.sub(r" {2,}", " ", line.strip()))
    for k, piece in enumerate(pieces):
        if k % 2:
            pieces[k] = f"<{piece}>"  # a web address: keep it whole, as a link
        else:
            pieces[k] = re.sub(r"([\\`*_\[\]<>~|$])", r"\\\1", piece.replace("&", "&amp;"))
    text = re.sub(r"^([#+\-=])", r"\\\1", "".join(pieces))
    return re.sub(r"^(\d+)([.)])", r"\1\\\2", text)


def paragraphs(text: str) -> list[list[str]]:
    """Split a turn into paragraphs, each a list of its lines."""
    found, current = [], []
    for line in text.split("\n"):
        if line.strip():
            current.append(line.rstrip())
        elif current:
            found.append(current)
            current = []
    return found + ([current] if current else [])


def default_title(record: dict) -> str:
    """A title for a conversation's page: the opening words of the opening post."""
    words = record["turns"][0]["text"].split()
    return " ".join(words[:9]).rstrip(".,;:") + ("…" if len(words) > 9 else "")


def page_markdown(record: dict, title: str = "") -> str:
    """Lay a saved conversation out as a page: an introduction, a contents list and a marker for each speaker."""
    turns, names = record["turns"], record["order"]
    human = turns[0]["speaker"]
    keys = {info["name"]: who for who, info in PARTICIPANTS.items()}
    mark = {name: MARKERS.get(keys.get(name, ""), "⚪") for name in names} | {human: HUMAN_MARKER}
    last_round = max(turn["round"] for turn in turns)
    started = datetime.fromisoformat(record["started"])
    words = sum(len(turn["text"].split()) for turn in turns)
    step = 10 if words < 500 else 100 if words < 5000 else 500
    about = max(step, int(words / step + 0.5) * step)

    def span(first: int, last: int) -> str:
        if first == 0:
            if last == 0:
                return "Opening post"
            return "Opening post and " + ("round 1" if last == 1 else f"rounds 1–{last}")
        return f"Round {first}" if last <= first else f"Rounds {first}–{last}"

    # One part for each entry of yours that opens a round: from that round to the last one before your next entry.
    entries = [turn for n, turn in enumerate(turns)
               if turn["speaker"] == human and (n == 0 or turn["round"] != turns[n - 1]["round"])]
    ends = [entry["round"] - 1 for entry in entries[1:]] + [last_round]
    in_parts = len(entries) > 1

    count = {2: "two", 3: "three"}.get(len(names), str(len(names)))
    listed = ", ".join(names[:-1]) + f" and {names[-1]}"
    effort = record.get("reasoning_effort")
    page = [
        f"# {as_typed(title.strip() or default_title(record))}",
        f"A conversation between {'one human' if human == 'User' else as_typed(human)} and {count} AI models, "
        f"{listed}, held on {started.day} {started:%B %Y} with [LLM Roundtable]({PROJECT_URL}). "
        f"About {about:,} words.",
        "\n".join(
            ["| | Speaker | Model |", "|:-:|:--|:--|", f"| {HUMAN_MARKER} | **{as_typed(human)}** | the human at the table |"]
            + [f"| {mark[name]} | **{name}** | `{record['models'].get(name, '')}` |" for name in names]
        ),
        f"The models spoke in the order shown, for {last_round} round{'' if last_round == 1 else 's'}. Each was asked "
        f"to aim for about {record['reply_words']} words a turn"
        + (f", and ran with reasoning effort set to `{effort}`." if effort else "."),
        "Every entry is reproduced word for word from the saved transcript. Only the layout was added for this page.",
    ]
    if in_parts:
        teasers = []
        for n, (entry, end) in enumerate(zip(entries, ends), 1):
            opening = entry["text"].split()
            teaser = as_typed(" ".join(opening[:14])) + ("…" if len(opening) > 14 else "")
            teasers.append(f"- **[Part {n}](#part-{n})** · {span(entry['round'], end)}<br>{teaser}")
        page += ["## Contents", "\n".join(teasers)]

    part, current_round = 0, None
    for turn in turns:
        if turn["round"] != current_round:
            current_round = turn["round"]
            if in_parts and turn["speaker"] == human:
                part += 1
                page.append(f"## Part {part}")
            page.append(f"### Round {current_round}" if current_round else "### Opening post")
        if turn["speaker"] == human:
            quoted = [f"**{HUMAN_MARKER} {as_typed(human)}**"]
            quoted += ["\\\n".join(as_typed(line) for line in lines) for lines in paragraphs(turn["text"])]
            page.append("\n>\n".join("\n".join(f"> {line}" for line in block.split("\n")) for block in quoted))
        else:
            # A model's reply is Markdown already, so it goes in as it is. In plain paragraphs the
            # line breaks are kept; lists, quotations and code are left for Markdown to lay out.
            page.append(f"**{mark.get(turn['speaker'], '⚪')} {turn['speaker']}**")
            if "```" in turn["text"] or "~~~" in turn["text"]:
                page.append(turn["text"].strip("\n"))
                continue
            for lines in paragraphs(turn["text"]):
                plain = not any(BLOCK_START.match(line) for line in lines)
                page.append(("\\\n" if plain else "\n").join(lines))
    page += ["---", f"*Made with [LLM Roundtable]({PROJECT_URL}).*"]
    return "\n\n".join(page) + "\n"


def write_page(json_file: Path, title: str = "") -> Path:
    """Make the page for a saved conversation, next to its JSON file. Returns the page's path."""
    try:
        record = json.loads(read_text(json_file))
        text = page_markdown(record, title)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        raise RoundtableError(f"{json_file.name} isn't a conversation saved by this program.") from None
    page = json_file.with_name(json_file.stem + "_page.md")
    try:
        page.write_text(text, encoding="utf-8")
    except OSError as err:
        raise RoundtableError(f"Couldn't write {page}: {err}") from None
    return page


# =============================================================================
# SETTING UP AND RUNNING
# =============================================================================

def parse_order(text: str) -> list[str]:
    """Turn "claude, chatgpt, gemini" into ["claude", "chatgpt", "gemini"], checking it makes sense."""
    keys = [ALIASES.get(word, word) for word in re.split(r"[,;>\s]+", text.lower()) if word]
    unknown = [key for key in keys if key not in PARTICIPANTS]
    if unknown:
        raise RoundtableError(f"I don't know {', '.join(unknown)}. Choose from: {', '.join(PARTICIPANTS)}.")
    if len(set(keys)) < len(keys):
        raise RoundtableError("Each participant can be listed only once.")
    if len(keys) < 2:
        raise RoundtableError("A conversation needs at least two participants.")
    return keys


def choose_order(given: str | None) -> list[str]:
    if given:
        return parse_order(given)
    default = ", ".join(DEFAULT_ORDER)
    while True:
        answer = ask_user(f"Speaking order [{default}]: ")
        try:
            return parse_order(answer or default)
        except RoundtableError as err:
            print(f"  {err}")


def choose_rounds(given: int | None, default: int = DEFAULT_ROUNDS) -> int:
    """Ask how many rounds to run, unless the number was given. Enter accepts the default."""
    if given:
        return given
    while True:
        answer = ask_user(f"How many rounds? [{default}]: ")
        if not answer:
            return default
        if answer.isdecimal() and int(answer) > 0:
            return int(answer)
        print("  Please enter a whole number, 1 or more.")


def read_entry(first_lines: list[str] | None = None) -> str:
    """Read typed or pasted lines until one says END (or input runs out)."""
    # An explicit END line, rather than a blank line, lets you paste several paragraphs safely.
    lines = list(first_lines or [])
    while True:
        try:
            line = input("> ")
        except EOFError:
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)
    return "\n".join(lines).strip()


def read_opening_post(args: argparse.Namespace) -> str:
    if args.prompt_file:
        post = read_text(Path(args.prompt_file))
    elif args.prompt:
        post = args.prompt
    else:
        print("\nYour opening post. Several paragraphs are fine; type END on a line of its own to send it.")
        post = read_entry()
    if not post.strip():
        raise RoundtableError("No opening post, so there is nothing to discuss.")
    return post.strip()


def what_next(rounds: int) -> tuple[str | None, int]:
    """Ask what to do once the rounds are done.

    Returns your new entry (or None) and the number of rounds to run next; 0 rounds means finish.
    After an entry you are asked how many rounds should follow it, and Enter keeps the number used last time.
    """
    print(
        "\nWhat next?\n"
        "  Join in:   type your entry, then END on a line of its own, and say how many rounds should follow.\n"
        "  Carry on:  type a number, and the models run that many more rounds without you.\n"
        "  Finish:    press Enter."
    )
    first = ask_user("> ")
    if not first or first.upper() == "END":
        return None, 0
    if first.isdecimal():
        return None, int(first)
    entry = read_entry([first])
    return entry, choose_rounds(None, default=rounds)


def show(text: str) -> None:
    """Print a reply, indented and wrapped to the width of the terminal."""
    width = max(40, min(shutil.get_terminal_size().columns, 100))
    for paragraph in text.splitlines():
        print(textwrap.fill(paragraph, width, initial_indent="  ", subsequent_indent="  "))


def converse(args: argparse.Namespace) -> None:
    order = choose_order(args.order)
    clients = {who: make_client(who) for who in order}  # also asks for any missing API keys
    rounds = choose_rounds(args.rounds)
    talk = Conversation(order, read_opening_post(args), args.words)
    print(f"\nSpeaking order: {' -> '.join(talk.details['order'])}")
    print(f"Reply length: about {talk.words} words each ({talk.why})")
    print(f"Saving after every turn to {talk.base}.md  (press Ctrl+C to stop early)")

    failed = False
    try:
        while rounds:
            for _ in range(rounds):
                talk.round_no += 1
                print(f"\n----- Round {talk.round_no} -----")
                for who in order:
                    print(f"\n{PARTICIPANTS[who]['name']}:")
                    show(talk.speak(who, clients[who])["text"])
            if args.no_follow_up:
                break
            entry, rounds = what_next(rounds)
            if entry and rounds:
                talk.add_entry(entry)
    except KeyboardInterrupt:
        print("\n\nStopped early.")
    except RoundtableError as err:  # a model failed to reply; everything before it is already saved
        print(f"  {err}")
        failed = True

    print(f"\nConversation saved:\n  {talk.base}.md\n  {talk.base}.json")
    if failed:
        sys.exit(1)


def check_one(who: str) -> tuple[bool, str]:
    """Send one tiny request to a participant. Returns whether it worked, and its reply or what went wrong."""
    client = make_client(who)
    try:
        reply = ASK[who](client, MODELS[who], "Reply in five words or fewer.", [{"role": "user", "content": "Say hello."}])
    except Exception as exc:
        return False, explain(exc)
    return True, " ".join(reply.split())


def check() -> None:
    """Test every participant, to confirm its key and model name work."""
    for who, info in PARTICIPANTS.items():
        label = f"{info['name']} ({MODELS[who]})"
        try:
            worked, detail = check_one(who)
        except RoundtableError as err:
            print(f"SKIPPED  {label}: {err}")
            continue
        print(f"{'OK      ' if worked else 'FAILED  '} {label}: {detail}")


def positive_int(text: str) -> int:
    if not text.isdecimal() or int(text) < 1:
        raise argparse.ArgumentTypeError("must be a whole number, 1 or more")
    return int(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="A turn-taking conversation between ChatGPT, Gemini and Claude.")
    parser.add_argument("--order", help='speaking order, e.g. "claude,chatgpt,gemini" (any two or all three)')
    parser.add_argument("--rounds", type=positive_int, help="how many rounds the models run before you are asked what next; everyone speaks once per round (asked for if omitted)")
    parser.add_argument("--prompt", help="your opening post")
    parser.add_argument("--prompt-file", help="read your opening post from a text file")
    parser.add_argument("--words", type=positive_int, default=REPLY_WORDS, help="aim for about this many words per reply, instead of matching the length of your opening post")
    parser.add_argument("--no-follow-up", action="store_true", help="finish when the rounds are done, without asking what next")
    parser.add_argument("--name", help="what the models call you; it is remembered for next time (default: User)")
    parser.add_argument("--check", action="store_true", help="test each API key and model name, then exit")
    parser.add_argument("--page", metavar="FILE", help="make a page to share from a saved conversation's .json file, then exit")
    parser.add_argument("--title", help="the title for that page (default: the opening words of the conversation)")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # never crash on a character the terminal can't display
    try:
        if args.page:
            print(f"Page written:\n  {write_page(Path(args.page), args.title or '')}")
            return
        if args.name:
            save_settings({"name": clean_name(args.name)})
        use_name(load_settings().get("name"))
        load_env_file()
        check() if args.check else converse(args)
    except RoundtableError as err:
        sys.exit(f"\n{err}")
    except KeyboardInterrupt:
        sys.exit("\nStopped.")


if __name__ == "__main__":
    main()
