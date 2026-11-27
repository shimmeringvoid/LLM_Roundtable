#!/usr/bin/env python3
"""
LLM Roundtable: a turn-taking conversation between ChatGPT, Gemini and Claude.

You write the opening post. The models then take turns in the order you choose,
each replying briefly to what the others just said. The whole conversation is
saved after every turn, as a readable Markdown transcript and as a JSON file.

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
    python llm_roundtable.py --prompt "Is a hot dog a sandwich?"
    python llm_roundtable.py --prompt-file my_post.txt
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
    "chatgpt": "gpt-6.2-sol",       # stronger but pricier: "gpt-6.2-astra"
    "gemini": "gemini-3.8-flash",   # stronger but pricier: "gemini-4-argon" (paid keys only)
    "claude": "claude-sonnet-5-5",  # stronger but pricier: "claude-opus-5-5"
}

DEFAULT_ORDER = ["chatgpt", "gemini", "claude"]  # speaking order if you don't choose one
DEFAULT_ROUNDS = 3  # in one round, each participant speaks once
MAX_WORDS = 120     # target length of each reply

# How hard each model thinks before replying: "low", "medium" or "high".
# "low" keeps a casual conversation quick and cheap. Use None to leave the
# setting out altogether and get each provider's own default.
REASONING_EFFORT = "low"

# Ceiling on the tokens one reply may use, INCLUDING the model's hidden
# thinking. It is a cost safety net, not the length control (MAX_WORDS is), so
# keep it generous: if it is too low, a reply can come back empty.
MAX_OUTPUT_TOKENS = 8000

REQUEST_TIMEOUT = 180  # seconds to wait for one reply before giving up
USER_NAME = "User"     # how your opening post is labelled in the conversation

HERE = Path(__file__).resolve().parent
SAVE_DIR = HERE / "conversations"  # transcripts are written here
ENV_FILE = HERE / ".env"           # optional file holding your API keys

# The standing instructions every model receives. Edit this to change the tone
# of the conversation (more debate, more humour, a particular audience...).
SYSTEM_PROMPT = """\
You are {name}, the AI model made by {maker}, taking part in a roundtable
conversation with {others}. A human, called "{user}" here, opened the
conversation with a post and is now reading along without replying. You speak
in turns, in this repeating order: {order}.

What the others say reaches you labelled with the speaker's name in square
brackets, like "[{user}]: ...". Your own earlier replies appear as your
previous turns.

On each of your turns:
- Respond directly to what {others} said most recently. Agree, disagree,
  question or build on specific points, and mention them by name. If no other
  model has spoken yet, respond to {user}'s opening post.
- Keep it short: {max_words} words at most, in plain conversational prose with
  no headings or bullet lists.
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


def load_env_file() -> None:
    """Read API keys from NAME=value lines in the .env file. Real environment variables win."""
    if not ENV_FILE.exists():
        return
    for line in read_text(ENV_FILE).splitlines():
        name, _, value = line.strip().removeprefix("export ").partition("=")
        name, words = name.strip(), value.split()
        # A key never contains spaces, so anything after the first word (a note, a comment) is ignored.
        if name in KEY_NAMES and words and not os.environ.get(name):
            os.environ[name] = words[0].strip("'\"")


def get_api_key(who: str) -> str | None:
    """Return a participant's API key, asking for it if it isn't set up yet."""
    info = PARTICIPANTS[who]
    for var in info["env"]:
        if os.environ.get(var, "").strip():
            return os.environ[var].strip()

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
        existing = read_text(ENV_FILE) if ENV_FILE.exists() else ""
        if existing and not existing.endswith("\n"):
            existing += "\n"
        try:
            ENV_FILE.write_text(f"{existing}{var}={key}\n", encoding="utf-8")
        except OSError as err:
            print(f"Couldn't save it ({err}), so it will be used for this run only.")
            return key
        try:
            ENV_FILE.chmod(0o600)  # readable only by you (no effect on Windows)
        except OSError:
            pass
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
    raise RoundtableError(
        f"The reply was empty (the provider reported: {stop_reason}). If that mentions a token "
        "limit, the model spent its whole allowance on thinking: raise MAX_OUTPUT_TOKENS."
    )


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
        hint = {
            400: "The provider rejected the request; its message above says why. If it names a "
                 "setting it doesn't accept, try REASONING_EFFORT = None.",
            401: "The API key was rejected. Check that it was copied in full.",
            403: "This API key isn't allowed to do that (often: no access to this model).",
            404: "The model name in MODELS may be out of date.",
            429: "Rate limit or quota reached. Wait a minute, or check your credit and billing with the provider.",
            500: "The provider had a temporary problem. Try again in a little while.",
        }.get(status if isinstance(status, int) else None)
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


def system_prompt(who: str, order: list[str], max_words: int) -> str:
    """Fill in SYSTEM_PROMPT for one participant."""
    names = [PARTICIPANTS[key]["name"] for key in order]
    me = PARTICIPANTS[who]
    return SYSTEM_PROMPT.format(
        name=me["name"],
        maker=me["maker"],
        others=" and ".join(name for name in names if name != me["name"]),
        order=", ".join(names),
        user=USER_NAME,
        max_words=max_words,
    )


def messages_for(name: str, transcript: list[dict]) -> list[dict]:
    """Build the conversation as one participant sees it.

    Its own earlier replies become "assistant" messages. Everything the others
    said in between is merged into a single "user" message, each part labelled
    with its speaker, e.g. "[Gemini]: ...". The result alternates user and
    assistant and ends on a user message, the shape all three APIs expect. On
    every turn after its first, that final user message holds exactly the
    other participants' latest replies.
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


def take_turn(who: str, order: list[str], client, round_no: int, transcript: list[dict], max_words: int) -> None:
    """Ask one participant for its next reply and add it to the transcript."""
    name, model = PARTICIPANTS[who]["name"], MODELS[who]
    try:
        reply = ASK[who](client, model, system_prompt(who, order, max_words), messages_for(name, transcript))
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


def choose_rounds(given: int | None) -> int:
    if given:
        return given
    while True:
        answer = ask_user(f"How many rounds? [{DEFAULT_ROUNDS}]: ")
        if not answer:
            return DEFAULT_ROUNDS
        if answer.isdigit() and int(answer) > 0:
            return int(answer)
        print("  Please enter a whole number, 1 or more.")


def read_opening_post(args: argparse.Namespace) -> str:
    if args.prompt_file:
        post = read_text(Path(args.prompt_file))
    elif args.prompt:
        post = args.prompt
    else:
        # An explicit END line (rather than a blank line) lets you paste several paragraphs safely.
        print("\nYour opening post. Several paragraphs are fine; type END on a line of its own to send it.")
        lines = []
        while True:
            try:
                line = input("> ")
            except EOFError:
                break
            if line.strip().upper() == "END":
                break
            lines.append(line)
        post = "\n".join(lines)
    if not post.strip():
        raise RoundtableError("No opening post, so there is nothing to discuss.")
    return post.strip()


def show(text: str) -> None:
    """Print a reply, indented and wrapped to the width of the terminal."""
    width = max(40, min(shutil.get_terminal_size().columns, 100))
    for paragraph in text.splitlines():
        print(textwrap.fill(paragraph, width, initial_indent="  ", subsequent_indent="  "))


def converse(args: argparse.Namespace) -> None:
    order = choose_order(args.order)
    clients = {who: make_client(who) for who in order}  # also asks for any missing API keys
    rounds = choose_rounds(args.rounds)
    post = read_opening_post(args)

    names = [PARTICIPANTS[who]["name"] for who in order]
    started = datetime.now()
    stem = f"roundtable_{started:%Y-%m-%d_%H-%M-%S}"
    base, n = SAVE_DIR / stem, 2
    while base.with_suffix(".md").exists() or base.with_suffix(".json").exists():
        base, n = SAVE_DIR / f"{stem}_{n}", n + 1  # never overwrite an earlier conversation
    details = {
        "started": started.isoformat(timespec="seconds"),
        "order": names,
        "models": {PARTICIPANTS[who]["name"]: MODELS[who] for who in order},
        "max_words": args.max_words,
        "reasoning_effort": REASONING_EFFORT,
    }
    transcript = [make_turn(USER_NAME, post, round_no=0)]
    try:
        save(base, details, transcript)  # saving first means a folder problem shows up before any API call
    except OSError as err:
        raise RoundtableError(f"Can't save conversations in {SAVE_DIR}: {err}") from None
    print(f"\nSpeaking order: {' -> '.join(names)}")
    print(f"Saving after every turn to {base}.md  (press Ctrl+C to stop early)")

    round_no, failed = 0, False
    try:
        while rounds:
            for _ in range(rounds):
                round_no += 1
                print(f"\n----- Round {round_no} -----")
                for who in order:
                    print(f"\n{PARTICIPANTS[who]['name']}:")
                    take_turn(who, order, clients[who], round_no, transcript, args.max_words)
                    save(base, details, transcript)
                    show(transcript[-1]["text"])
            rounds = 0
            if args.rounds is None:  # no fixed number was asked for on the command line, so offer more
                answer = ask_user("\nMore rounds? Enter a number, or press Enter to finish: ")
                rounds = int(answer) if answer.isdigit() else 0
    except KeyboardInterrupt:
        print("\n\nStopped early.")
    except RoundtableError as err:  # a model failed to reply; everything before it is already saved
        print(f"  {err}")
        failed = True

    print(f"\nConversation saved:\n  {base}.md\n  {base}.json")
    if failed:
        sys.exit(1)


def check() -> None:
    """Send one tiny request to each participant to confirm its key and model name work."""
    for who, info in PARTICIPANTS.items():
        label = f"{info['name']} ({MODELS[who]})"
        try:
            client = make_client(who)
        except RoundtableError as err:
            print(f"SKIPPED  {label}: {err}")
            continue
        try:
            reply = ASK[who](client, MODELS[who], "Reply in five words or fewer.", [{"role": "user", "content": "Say hello."}])
            print(f"OK       {label}: {' '.join(reply.split())}")
        except Exception as exc:
            print(f"FAILED   {label}: {explain(exc)}")


def positive_int(text: str) -> int:
    if not text.isdigit() or int(text) < 1:
        raise argparse.ArgumentTypeError("must be a whole number, 1 or more")
    return int(text)


def main() -> None:
    parser = argparse.ArgumentParser(description="A turn-taking conversation between ChatGPT, Gemini and Claude.")
    parser.add_argument("--order", help='speaking order, e.g. "claude,chatgpt,gemini" (any two or all three)')
    parser.add_argument("--rounds", type=positive_int, help="run exactly this many rounds, then stop; everyone speaks once per round (if omitted you are asked, and offered more at the end)")
    parser.add_argument("--prompt", help="your opening post")
    parser.add_argument("--prompt-file", help="read your opening post from a text file")
    parser.add_argument("--max-words", type=positive_int, default=MAX_WORDS, help=f"target length of each reply (default {MAX_WORDS})")
    parser.add_argument("--check", action="store_true", help="test each API key and model name, then exit")
    args = parser.parse_args()

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")  # never crash on a character the terminal can't display
    try:
        load_env_file()
        check() if args.check else converse(args)
    except RoundtableError as err:
        sys.exit(f"\n{err}")
    except KeyboardInterrupt:
        sys.exit("\nStopped.")


if __name__ == "__main__":
    main()
