#!/usr/bin/env python3
"""
LLM Roundtable in a window.

Everything llm_roundtable.py does at the command line, with buttons instead.
Set the speaking order, choose a lighter or a stronger model for each
participant, write your opening post and watch the replies arrive. You can
join in between rounds (or queue an entry while the models are talking), stop
at any point, reopen an earlier conversation and make a page to share.

The conversation itself is run by llm_roundtable.py, which has to sit in the
same folder. It is saved the same way and in the same place, and your API keys
are the same ones.

SETUP
    The window needs one package more than the script does:
        pip install nicegui
    (The conda environment that comes with this repo already has it.)

USAGE
    python roundtable_gui.py              opens the window
    python roundtable_gui.py --tab        opens it as a tab in your usual browser instead
    python roundtable_gui.py --shortcut   adds LLM Roundtable to your applications, so that
                                          from then on you can start it without a terminal

The window is a page that this program serves to your own browser, at an
address only this computer can reach. Close the window and the program stops.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
import webbrowser
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

import llm_roundtable as rt

try:
    from nicegui import Client, app, background_tasks, run, ui
except ImportError:
    sys.exit(
        "The window needs the 'nicegui' package, which is not installed in this Python.\n"
        "If you use the project's conda environment, activate it and bring it up to date:\n"
        "    conda activate roundtable\n"
        "    conda env update -f environment.yml --prune\n"
        "Otherwise install it with:\n"
        "    pip install nicegui"
    )

# =============================================================================
# SETTINGS FOR THE WINDOW ITSELF
# (Models, reply length and the like are set in the window, and remembered in
# settings.json. Their starting values come from llm_roundtable.py.)
# =============================================================================

PORT = 8361  # the window lives at http://127.0.0.1:8361 (another port is used if this one is taken)
GRACE = 30   # seconds the program keeps going after its window is closed, in case that was a slip
ICON = rt.HERE / "assets" / "icon.svg"

NAMES = {who: info["name"] for who, info in rt.PARTICIPANTS.items()}  # "chatgpt" -> "ChatGPT"
WHO = {name: who for who, name in NAMES.items()}                      # and back again

# How hard the models think before replying: what each choice is called, and a word about it.
EFFORTS = {
    "low": ("Low", "quick, and costs least"),
    "medium": ("Medium", "more careful"),
    "high": ("High", "slowest, and costs most"),
    None: ("Auto", "each model's own default"),
}

# Three of the script's hints about what went wrong point at settings in the script. Here those are buttons.
rt.HINTS.update({
    400: "The provider rejected the request; its message above says why. If it names a setting it "
         "doesn't accept, set Thinking to Auto.",
    404: "The model's name may be out of date. Choose another model.",
    "empty": "If that mentions a token limit, the model spent its whole allowance on thinking: set Thinking lower.",
})

# One marker per speaker. Each has its own shape as well as its own colour.
SHAPES = {
    "chatgpt": '<circle cx="7" cy="7" r="6" fill="#0F766E"/>',
    "gemini": '<rect x="1.5" y="1.5" width="11" height="11" rx="2.5" fill="#1D4ED8"/>',
    "claude": '<rect x="2.4" y="2.4" width="9.2" height="9.2" rx="1.5" transform="rotate(45 7 7)" fill="#B45309"/>',
    "you": '<circle cx="7" cy="7" r="5" fill="none" stroke="#14181F" stroke-width="2"/>',
}
LOGO = (
    '<svg width="22" height="22" viewBox="0 0 22 22" aria-hidden="true">'
    '<circle cx="11" cy="11" r="6" fill="none" stroke="#14181F" stroke-width="2"/>'
    '<circle cx="11" cy="2.5" r="2.5" fill="#0F766E"/><circle cx="3.6" cy="15.2" r="2.5" fill="#1D4ED8"/>'
    '<circle cx="18.4" cy="15.2" r="2.5" fill="#B45309"/></svg>'
)

CSS = """
:root { --ink: #14181F; --soft: #4A5361; --softer: #5B6472; --line: #E1E5EA; --edge: #C9CFD8; --tint: #E9EDF2; }
body { background: #F4F6F8; color: var(--ink);
       font-family: system-ui, -apple-system, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
.nicegui-content { padding: 0; gap: 0; }
.rt-app { width: 100%; height: 100vh; display: flex; flex-direction: column; }
.rt-header { flex: none; height: 64px; padding: 0 28px; display: flex; flex-wrap: wrap; align-items: center;
             justify-content: space-between; gap: 12px; background: #fff; border-bottom: 1px solid var(--line); }
.rt-brand { font-size: 18px; font-weight: 600; letter-spacing: -0.01em; }
.rt-body { flex: 1; min-height: 0; width: 100%; max-width: 1280px; margin: 0 auto; padding: 24px 28px;
           display: flex; align-items: flex-start; gap: 28px; }
.rt-side { flex: none; width: 400px; max-height: 100%; overflow-y: auto; padding: 22px; display: flex;
           flex-direction: column; gap: 22px; background: #fff; border: 1px solid var(--line); border-radius: 14px; }
.rt-side.rt-narrow { width: 320px; padding: 20px; gap: 18px; }
.rt-main { flex: 1; min-width: 0; height: 100%; overflow-y: auto; display: flex; flex-direction: column; gap: 14px; }

.rt-h1 { font-size: 30px; font-weight: 600; letter-spacing: -0.01em; line-height: 1.2; }
.rt-h2 { font-size: 20px; font-weight: 600; line-height: 1.3; }
.rt-title { font-size: 14px; font-weight: 600; }
.rt-note { font-size: 13px; color: var(--softer); }
.rt-text { font-size: 15px; line-height: 1.55; color: var(--soft); }
.rt-name { font-size: 15px; font-weight: 600; white-space: nowrap; }
.rt-strong { font-size: 14px; font-weight: 600; }
.rt-warn { font-size: 12px; font-weight: 500; color: #92400E; }
.rt-mono { font-family: ui-monospace, Menlo, Consolas, monospace; font-size: 12px; overflow-wrap: anywhere; }
.rt-mark { flex: none; display: flex; }

.rt-btn { min-height: 44px; padding: 0 14px; border-radius: 10px; font-size: 14px; font-weight: 500; line-height: 1.2; }
.rt-btn .q-icon { font-size: 18px; }
.rt-btn .q-icon.on-left { margin-right: 8px; }
.rt-btn .q-icon.on-right { margin-left: 8px; }
.rt-plain, .rt-square, .rt-pick { background: #fff; border: 1px solid var(--edge); color: var(--ink); }
.rt-go { min-height: 48px; padding: 0 20px; border-radius: 12px; background: var(--ink); color: #fff;
         font-size: 15px; font-weight: 600; }
.rt-quiet { padding: 0 10px; color: var(--ink); text-decoration: underline; }
.rt-square { width: 44px; padding: 0; }
.rt-bare { min-height: 36px; width: 36px; padding: 0; color: var(--softer); }
.rt-btn.disabled { opacity: 0.45 !important; }
.rt-found .q-icon { color: #0F766E; }
.rt-missing .q-icon { color: #B45309; }
.rt-pick { min-height: 40px; max-width: 176px; padding: 0 6px 0 10px; border-radius: 8px; font-size: 13px;
           font-weight: 400; }
.rt-pick .q-btn__content { flex-wrap: nowrap; justify-content: space-between; gap: 2px; }
.rt-seat .rt-pick { flex: 1; min-width: 0; max-width: none; }
.rt-seat .rt-who { flex: none; width: 66px; gap: 0; }
.rt-place .rt-pick { max-width: 100%; }
.rt-choice { min-width: 240px; }
.rt-chosen { font-weight: 600; }

.rt-field .q-field__control { border-radius: 10px; background: #fff; }
.rt-field .q-field__native, .rt-field .q-field__input { font-size: 15px; color: var(--ink); }
.rt-field .q-field__bottom { padding: 6px 0 0; font-size: 13px; }
.rt-field .q-field__messages { color: var(--softer); }
.rt-field.q-field--error .q-field__messages { color: #B91C1C; }
.rt-count { width: 64px; }
.rt-count .q-field__native { text-align: center; font-size: 16px; font-weight: 600; }
.rt-count .q-field__control, .rt-count .q-field__marginal { height: 44px; }
.rt-count input::-webkit-inner-spin-button, .rt-count input::-webkit-outer-spin-button { appearance: none; margin: 0; }
.rt-count input { -moz-appearance: textfield; }
.rt-count.rt-wide { width: 84px; }
.rt-radio .q-radio { padding: 5px 0; }
.rt-area { width: 100%; }
.rt-area .q-field__control { border-radius: 14px; background: #fff; padding: 4px 6px 4px 20px; }
.rt-area textarea { font-size: 16px; line-height: 1.6; color: var(--ink); }
.rt-post { flex: 1; min-height: 180px; }
.rt-post .q-field__inner, .rt-post .q-field__control, .rt-post .q-field__control-container { height: 100%; }
.rt-post textarea { height: 100% !important; resize: none; padding: 14px 0; }
.rt-entry-box .q-field__control { border-radius: 10px; padding: 0 4px 0 12px; }
.rt-entry-box textarea { max-height: 24vh; overflow-y: auto !important; padding-top: 11px; padding-bottom: 11px; }

.rt-seat { width: 100%; min-height: 56px; padding: 6px 8px 6px 2px; flex-wrap: nowrap; align-items: center; gap: 8px;
           background: #FAFBFC; border: 1px solid var(--line); border-radius: 10px; }
.rt-seat.rt-out .rt-name, .rt-seat.rt-out .rt-mark { opacity: 0.45; }
.rt-grip { cursor: grab; }
.rt-place { width: 100%; min-height: 52px; padding: 6px 12px; flex-wrap: nowrap; align-items: center; gap: 10px;
            border: 1px solid var(--line); border-radius: 10px; }
.rt-place.rt-now { border: 2px solid var(--ink); padding: 5px 11px; }
.rt-place.rt-you { border-style: dashed; border-color: var(--edge); }
.rt-place.rt-you.rt-now { border-style: solid; border-color: var(--ink); }
.rt-status { flex: none; font-size: 13px; color: var(--soft); }
.rt-now .rt-status { font-weight: 600; color: var(--ink); }
.rt-facts { width: 100%; padding-top: 16px; border-top: 1px solid var(--line); gap: 12px; font-size: 14px; }
.rt-fact { width: 100%; flex-wrap: nowrap; justify-content: space-between; align-items: center; gap: 12px; }
.rt-fact > :first-child, .rt-fact-name { color: var(--soft); }
.rt-fact > :last-child { font-weight: 500; text-align: right; }

.rt-transcript { flex: 1 1 0; min-height: 140px; width: 100%; overflow-y: auto; padding: 2px 6px 8px 0; }
.rt-turns { width: 100%; gap: 18px; }
.rt-divider { width: 100%; flex-wrap: nowrap; align-items: center; gap: 12px; }
.rt-divider > div:not(.rt-rule) { font-size: 14px; font-weight: 600; color: var(--soft); }
.rt-rule { flex: 1; height: 1px; background: #D5DAE1; }
.rt-yours { width: 100%; gap: 8px; padding: 14px 18px; background: var(--tint); border-radius: 14px; }
.rt-reply { width: 100%; gap: 6px; padding: 0 18px; }
.rt-said { width: 100%; font-size: 16px; line-height: 1.6; overflow-wrap: anywhere; }
.rt-said p { margin: 0 0 0.75em; }
.rt-said > :last-child, .rt-said p:last-child { margin-bottom: 0; }
.rt-said img { display: none; }
.rt-said a { color: #1D4ED8; text-decoration: underline; }
.rt-typed { white-space: pre-wrap; }
.rt-clamp { display: -webkit-box; -webkit-box-orient: vertical; -webkit-line-clamp: 3; overflow: hidden; }
.rt-thinking { min-height: 44px; padding: 0 18px; align-items: center; gap: 8px; }

.rt-card { flex: none; width: 100%; gap: 10px; padding: 16px 18px; background: #fff; border: 1px solid var(--edge);
           border-radius: 14px; }
.rt-card.rt-now { border: 2px solid var(--ink); padding: 15px 17px; }
.rt-trouble { width: 100%; gap: 4px; padding: 12px 14px; background: #FEF2F2; border: 1px solid #FCA5A5;
              border-radius: 10px; font-size: 14px; line-height: 1.5; }
.rt-dialog { width: 560px; max-width: 94vw; gap: 14px; padding: 24px; border-radius: 14px; }
.rt-key { width: 100%; gap: 8px; padding: 12px 14px; border: 1px solid var(--line); border-radius: 10px; }
.rt-past { width: 100%; gap: 2px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px;
           cursor: pointer; }
.rt-past:hover, .rt-past:focus-visible { border-color: var(--ink); }
.rt-list { width: 100%; max-height: 56vh; overflow-y: auto; gap: 8px; }
.rt-ok { color: #0F766E; }
.rt-bad { color: #B91C1C; white-space: pre-wrap; }
.rt-notice { white-space: pre-line; }
a.rt-link { color: #1D4ED8; text-decoration: underline; font-size: 13px; }

@media (max-width: 860px) {            /* a narrow window: one column, and the whole page scrolls */
  .rt-app { height: auto; min-height: 100vh; }
  .rt-header { height: auto; min-height: 64px; padding: 10px 16px; }
  .rt-body { flex-direction: column; padding: 16px; gap: 16px; }
  .rt-side, .rt-side.rt-narrow { width: 100%; max-height: none; }
  .rt-main { height: auto; width: 100%; overflow: visible; }
  .rt-transcript { flex: none; overflow: visible; }
  .rt-post { flex: none; height: 300px; }
}
"""


class NoKey(rt.RoundtableError):
    """A participant has no API key yet. The window answers this one by opening the keys."""


# =============================================================================
# THE TABLE: your settings, and the conversation in progress
# =============================================================================

class Table:
    """Everything the window shows. There is one table, shared by every browser tab that has the window open."""

    def __init__(self) -> None:
        saved = rt.load_settings()
        mine = saved.get("window") if isinstance(saved.get("window"), dict) else {}
        everyone = list(rt.PARTICIPANTS)

        def choice(key: str, default, fits):
            """A setting from settings.json, or `default` if the file has none or holds something unusable."""
            try:
                return mine[key] if key in mine and fits(mine[key]) else default
            except (TypeError, ValueError):
                return default

        # Your settings. They are remembered in settings.json and start from the ones in llm_roundtable.py.
        self.named = isinstance(saved.get("name"), str)  # False until you have been asked what to be called
        rt.use_name(saved.get("name"))
        self.name = rt.USER_NAME
        self.order = choice(  # all the participants, in speaking order...
            "order",
            rt.DEFAULT_ORDER + [who for who in everyone if who not in rt.DEFAULT_ORDER],
            lambda order: sorted(order) == sorted(everyone),
        )
        self.seated = choice(  # ...and those of them who take part
            "seated", list(rt.DEFAULT_ORDER), lambda seated: len(set(seated)) >= 2 and set(seated) <= set(everyone)
        )
        models = choice("models", {}, lambda models: isinstance(models, dict))
        self.models = {who: models[who] if good_model(models.get(who)) else rt.MODELS[who] for who in everyone}
        self.rounds = choice("rounds", rt.DEFAULT_ROUNDS, lambda n: type(n) is int and 1 <= n <= 99)
        self.match = choice("match_length", not rt.REPLY_WORDS, lambda flag: type(flag) is bool)  # reply length follows your post
        self.words = choice("words", rt.REPLY_WORDS or 150, lambda n: type(n) is int and 10 <= n <= 2000)  # or is set here
        self.effort = choice("effort", rt.REASONING_EFFORT, lambda effort: effort in EFFORTS)
        self.post = ""  # your opening post, as typed so far

        # The conversation.
        self.phase = "setup"  # "setup", "running", "waiting" (your move) or "finished"
        self.talk: rt.Conversation | None = None
        self.loaded = False        # True for a conversation reopened from Past conversations
        self.target = 0            # the models keep going until this round is complete
        self.batch = self.rounds   # how many rounds you last asked for in one go
        self.next_rounds = self.rounds  # what "Rounds to run next" shows
        self.speaking: str | None = None  # who is replying at this moment...
        self.since = 0.0                  # ...and when they started
        self.busy = False          # True while a request to a provider is under way
        self.stopping = False      # Stop was pressed: pause as soon as the reply that is on its way has arrived
        self.pause = ""            # why the models paused with a round unfinished: "stopped" or "error"
        self.problem = ""          # what went wrong, if a model could not reply
        self.entry = ""            # the entry of yours being typed
        self.queued = False        # True if that entry is to go in when the current round ends
        self.clients: dict[str, tuple[str, object]] = {}  # an SDK client per participant, with the key it was made for

        self.opened = False        # has a browser tab shown the window yet?
        self.last_seen = time.monotonic()  # when a tab was last open
        self.apply()

    # ----- settings -----

    def remember(self) -> None:
        """Save your settings, so the window opens next time the way you left it."""
        rt.save_settings({
            **({"name": self.name} if self.named else {}),
            "window": {
                "order": self.order, "seated": self.seated, "models": self.models, "rounds": self.rounds,
                "match_length": self.match, "words": self.words, "effort": self.effort,
            },
        })
        if self.phase == "setup":
            self.apply()

    def apply(self) -> None:
        """Hand your settings to llm_roundtable.py, which reads them whenever it asks a model for a reply."""
        rt.MODELS.update(self.models)
        rt.REASONING_EFFORT = self.effort
        rt.USER_NAME = self.name

    def set_name(self, name: str) -> None:
        self.name, self.named = rt.clean_name(name), True
        self.remember()

    def set_model(self, who: str, model: str) -> None:
        """Choose the model that plays a participant: for new conversations, or for the rest of the one in progress."""
        if self.phase == "setup":
            self.models[who] = model
            self.remember()
        else:
            rt.MODELS[who] = model
            self.talk.details["models"][NAMES[who]] = model
            self.talk.save()

    def set_effort(self, effort: str | None) -> None:
        if self.phase == "setup":
            self.effort = effort
            self.remember()
        else:
            rt.REASONING_EFFORT = self.talk.details["reasoning_effort"] = effort
            self.talk.save()

    # ----- the conversation -----

    def client(self, who: str):
        """The SDK client for a participant. It is made again if the API key has changed since."""
        key, _ = rt.find_api_key(who)
        if not key:
            raise NoKey(f"{NAMES[who]} needs an API key before it can take part.")
        if self.clients.get(who, ("", None))[0] != key:
            self.clients[who] = (key, rt.make_client(who))
        return self.clients[who][1]

    def left(self) -> list[str]:
        """Who has still to speak in the current round, in speaking order. Nobody, once the round is complete."""
        talk = self.talk
        if talk.round_no == 0:
            return []  # only the opening post so far
        spoke = {turn["speaker"] for turn in talk.transcript if turn["round"] == talk.round_no}
        return [who for who in talk.order if NAMES[who] not in spoke]

    def start(self) -> None:
        """Begin a new conversation from your settings and your opening post."""
        if self.phase != "setup":
            return  # (asked for twice: a double click, say, or a second tab)
        order = [who for who in self.order if who in self.seated]
        if not self.post.strip():
            raise rt.RoundtableError("Write your opening post first.")
        self.apply()
        for who in order:
            self.client(who)  # a missing key or package is reported here, before anything is saved
        self.talk = rt.Conversation(order, self.post.strip(), None if self.match else self.words)
        self.loaded, self.post, self.entry, self.pause, self.phase = False, "", "", "", "waiting"
        self.go(self.rounds)

    def go(self, rounds: int) -> None:
        """Set the models talking for this many rounds. A round that was interrupted counts as the first of them."""
        if self.phase != "waiting":
            return
        for who in self.talk.order:
            self.client(who)
        if not self.pause:
            self.batch = rounds
        self.target = self.talk.round_no + rounds - (1 if self.left() else 0)
        self.phase, self.pause, self.problem, self.stopping = "running", "", "", False
        background_tasks.create(self.run(), name="roundtable")

    async def run(self) -> None:
        """Let the models speak in turn until the rounds are done, you press Stop, or one of them cannot reply."""
        talk = self.talk
        try:
            while not self.stopping:
                waiting = self.left()
                if not waiting:  # the round is complete
                    if talk.round_no >= self.target:
                        break
                    if self.queued and self.entry.strip():
                        self.add_entry()  # your entry opens the next round
                    else:
                        talk.round_no += 1
                    continue
                self.speaking, self.since = waiting[0], time.monotonic()
                try:
                    await run.io_bound(self.reply, waiting[0], self.client(waiting[0]))
                finally:
                    self.speaking = None
        except rt.RoundtableError as err:
            self.pause, self.problem = "error", str(err)
        except asyncio.CancelledError:
            raise  # the program is closing
        except Exception as err:  # anything unforeseen is shown in the window, which would otherwise wait forever
            self.pause, self.problem = "error", f"Something unexpected went wrong.\n  {type(err).__name__}: {err}"
        finally:
            talk.round_no = talk.transcript[-1]["round"]  # a round nobody has spoken in has not begun
            unfinished = bool(self.left())
            if unfinished and not self.pause:
                self.pause = "stopped"
            # "Rounds to run next" offers the rounds still owed if the models were interrupted, or else the same again.
            owed = self.target - talk.round_no + (1 if unfinished else 0)
            self.next_rounds = max(1, owed) if self.pause else self.batch
            self.phase, self.stopping, self.queued = "waiting", False, False

    def reply(self, who: str, client) -> None:
        """Get one participant's reply and save it. This is the slow part, so it runs in a thread of its own."""
        self.busy = True
        try:
            self.talk.speak(who, client)
        finally:
            self.busy = False

    def add_entry(self) -> None:
        """Put the entry you typed into the conversation. It opens a new round, so every model sees it before replying."""
        talk, last = self.talk, self.talk.transcript[-1]
        if last["speaker"] == talk.human and last["round"]:
            talk.round_no = last["round"] - 1  # nobody has answered your last entry yet, so this one joins it
        talk.add_entry(self.entry.strip())
        talk.round_no += 1
        self.entry, self.queued = "", False

    def send(self, rounds: int) -> None:
        """Add your entry and let the models answer it."""
        if self.phase != "waiting":
            return
        if not self.entry.strip():
            raise rt.RoundtableError("Write your entry first, or carry on without one.")
        for who in self.talk.order:
            self.client(who)  # so that a missing key is noticed before your entry goes in
        self.add_entry()
        self.pause = ""
        self.go(rounds)

    def stop(self) -> None:
        """Pause as soon as the reply that is on its way has arrived."""
        self.stopping = self.phase == "running"

    def finish(self) -> None:
        if self.phase == "waiting":
            self.phase = "finished"

    def carry_on(self) -> None:
        """Take a finished conversation up again: it is your move."""
        if self.phase != "finished":
            return
        unfinished = bool(self.left())
        self.phase, self.pause, self.problem = "waiting", "stopped" if unfinished else "", ""
        self.next_rounds = 1 if unfinished else self.batch

    def new(self) -> None:
        """Clear the table for a new conversation."""
        if self.phase == "finished":
            self.phase, self.talk, self.entry, self.pause, self.problem = "setup", None, "", "", ""
            self.apply()

    def open(self, file: Path) -> None:
        """Reopen a saved conversation, with the name, models and thinking effort it was held with."""
        if self.phase not in ("setup", "finished"):
            raise rt.RoundtableError("Finish the conversation in progress first.")
        talk = rt.Conversation.load(file)
        models = talk.details.get("models")
        talk.details["models"] = models = models if isinstance(models, dict) else {}
        for who in talk.order:
            if good_model(models.get(NAMES[who])):
                rt.MODELS[who] = models[NAMES[who]]
            models[NAMES[who]] = rt.MODELS[who]
        effort = talk.details.get("reasoning_effort")
        rt.REASONING_EFFORT = effort if isinstance(effort, str) else None
        rt.USER_NAME = talk.human
        self.talk, self.loaded, self.phase, self.pause, self.problem, self.entry = talk, True, "finished", "", "", ""


def good_model(name) -> bool:
    """Could this be a model's name? Providers use letters, digits and a little punctuation."""
    return isinstance(name, str) and re.fullmatch(r"[\w.:/@-]{2,100}", name) is not None


def name_problem(name: str) -> str | None:
    """What is wrong with a name for you, if anything."""
    try:
        rt.clean_name(name)
    except rt.RoundtableError as err:
        return str(err)
    return None


def spelled(n: int) -> str:
    small = ["No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve"]
    return small[n] if 0 <= n < len(small) else str(n)


def saved_conversations() -> list[dict]:
    """The conversations in the save folder, newest first, as the Past conversations list shows them."""
    found = []
    for file in rt.SAVE_DIR.glob("*.json"):
        try:
            talk = rt.Conversation.load(file)
            started = datetime.fromisoformat(talk.details["started"])
            rounds = f"{talk.round_no} round{'' if talk.round_no == 1 else 's'}"
            found.append({
                "file": file,
                "started": started,
                "title": rt.default_title({"turns": talk.transcript}),
                "about": f"{started.day} {started:%B %Y, %H:%M} · {rounds} · {', '.join(talk.details['order'])}",
            })
        except (rt.RoundtableError, KeyError, ValueError, TypeError, IndexError):
            continue  # not a conversation: leave it out
    return sorted(found, key=lambda item: item["started"], reverse=True)[:200]


def open_folder(folder: Path) -> None:
    """Show a folder in the computer's file manager."""
    folder.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        os.startfile(folder)  # only exists on Windows
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(folder)],
                         stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# =============================================================================
# THE PIECES THE WINDOW IS BUILT FROM
# =============================================================================

def shape(who: str) -> str:
    """A speaker's marker, as a small drawing."""
    return f'<svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true">{SHAPES[who]}</svg>'


def mark(who: str) -> ui.html:
    return ui.html(shape(who), sanitize=False).classes("rt-mark")


def button(text: str = "", on_click=None, *, kind: str = "plain", icon: str | None = None, label: str = "") -> ui.button:
    """A button. `kind` is "plain", "go" (the main action), "quiet" (looks like a link), "square" or "bare" (an icon)."""
    made = ui.button(text, on_click=on_click, icon=icon, color=None).props("flat no-caps").classes(f"rt-btn rt-{kind}")
    if label:
        made.props["aria-label"] = label
    return made


def divider(text: str = "") -> ui.label:
    """A line across the conversation with a round's name in the middle."""
    with ui.row().classes("rt-divider"):
        ui.element("div").classes("rt-rule")
        label = ui.label(text)
        ui.element("div").classes("rt-rule")
    return label


def stepper(read, write, *, low: int = 1, high: int = 99) -> ui.number:
    """A number of rounds, with a button either side to lower or raise it."""
    def step(by: int) -> None:
        write(max(low, min(high, read() + by)))
        box.value = read()

    def typed(e) -> None:
        if e.value is not None and float(e.value).is_integer() and low <= e.value <= high:
            write(int(e.value))

    with ui.row().classes("items-center gap-1.5 flex-nowrap"):
        button(icon="remove", on_click=lambda: step(-1), kind="square", label="One round fewer")
        box = ui.number(value=read(), min=low, max=high, step=1, format="%d", on_change=typed)
        box.props('outlined dense hide-bottom-space aria-label="Rounds"').classes("rt-field rt-count")
        box.on("blur", lambda: box.set_value(read()))  # anything unusable that was typed gives way to the last good number
        button(icon="add", on_click=lambda: step(+1), kind="square", label="One round more")
    return box


class Window:
    """One browser tab's view of the table. Each tab redraws itself whenever the table changes."""

    def __init__(self) -> None:
        self.tab = ui.context.client  # the browser tab this is
        self.view = None             # what this tab is showing: "setup", or the conversation itself
        self.parts: dict = {}        # the parts of the window that are redrawn on their own: name -> (its box, what draws it)
        self.drawn: dict = {}        # what each of them was last drawn from, to tell when it needs redrawing
        self.mirrors: list = []      # fields that follow a setting: [field, how to read the setting, value last seen]
        self.checks: dict = {}       # what each "Check" in the keys dialog found
        self.showing = ""            # which dialog is open, if any
        self.clock: ui.label | None = None  # the label counting the seconds a model has been thinking
        with ui.dialog() as self.box, ui.card().classes("rt-dialog") as self.card:
            pass
        self.box.on_value_change(lambda e: None if e.value else self.closed())
        with ui.element("div").classes("rt-app"):
            with ui.element("header").classes("rt-header"):
                with ui.row().classes("items-center gap-2.5"):
                    ui.html(LOGO, sanitize=False).classes("rt-mark")
                    ui.label("LLM Roundtable").classes("rt-brand")
                self.part("header", self.header)
            self.part("body", self.body)
        self.drawn = self.state()
        ui.timer(0.2, self.sync)
        if not table.named:
            self.ask_name()

    # ----- keeping the tab up to date -----

    def part(self, name: str, draw) -> None:
        """Draw a part of the window in a box of its own, so that it can be drawn afresh later without the rest."""
        with ui.element("div").classes("contents") as box:
            draw()
        self.parts[name] = (box, draw)

    def redraw(self, name: str) -> None:
        box, draw = self.parts[name]
        box.clear()
        with box:
            draw()

    def state(self) -> dict:
        """What each redrawn part of the window depends on. When a part's entry here changes, the part is redrawn."""
        t = table
        keys = tuple(bool(rt.find_api_key(who)[0]) for who in rt.PARTICIPANTS)
        if t.phase == "setup":
            return {
                "header": (t.phase, keys),
                "seats": (tuple(t.order), tuple(t.seated), tuple(t.models.values()), keys),
                "thinking": (t.effort,),
            }
        talk = t.talk
        place = (talk.round_no, tuple(t.left()), len(talk.transcript))
        return {
            "header": (t.phase, keys, t.stopping, t.target, place),
            "side": (t.phase, t.speaking, place, tuple(rt.MODELS[who] for who in talk.order), rt.REASONING_EFFORT),
            "composer": (t.phase, t.pause, t.problem, t.stopping, t.queued, t.target, place),
        }

    def sync(self) -> None:
        """Bring this tab up to date with the table. Runs a few times a second, and after anything you do."""
        t = table
        now = self.state()
        if ("setup" if t.phase == "setup" else t.talk) is not self.view:
            self.redraw("body")  # a different screen altogether
            self.redraw("header")
        else:
            for part, drawn_from in now.items():
                if drawn_from != self.drawn.get(part):
                    self.composer() if part == "composer" else self.redraw(part)
        self.drawn = now

        if t.phase == "setup":
            self.count_words()
        else:
            self.new_turns()
        for mirror in self.mirrors:
            field, read, last = mirror
            if read() != last:  # the setting has changed, here or in another tab
                mirror[2] = read()
                if field.value != read():
                    field.value = read()
        if self.showing == "name" and t.named:
            self.box.close()  # another tab has answered

    def mirror(self, field, read) -> None:
        """Keep a field showing a setting, including when the setting is changed from another tab."""
        self.mirrors.append([field, read, read()])

    def attempt(self, action, *args) -> bool:
        """Do something the table may refuse, and say why if it does."""
        try:
            action(*args)
        except NoKey as err:
            ui.notify(str(err), type="warning", position="top")
            self.keys()
            return False
        except (rt.RoundtableError, OSError) as err:
            ui.notify(str(err), type="negative", position="top", multi_line=True, close_button="OK", timeout=0,
                      classes="rt-notice")
            return False
        self.sync()
        return True

    # ----- the top of the window -----

    def header(self) -> None:
        t = table
        have = [bool(rt.find_api_key(who)[0]) for who in rt.PARTICIPANTS]
        with ui.row().classes("items-center gap-3"):
            if t.phase == "running":
                now = t.talk.round_no + (0 if t.left() else 1)  # the round in progress, or about to begin
                ui.label(f"Round {min(now, t.target)} of {t.target}").classes("rt-strong")
                if t.stopping:
                    ui.label("Stopping after this reply").classes("rt-note")
                else:
                    button("Stop", icon="stop", on_click=lambda: self.attempt(t.stop))
                return
            if t.phase in ("setup", "waiting"):
                found = f"All {len(have)} keys found" if all(have) else f"{sum(have)} of {len(have)} keys found"
                button(found if any(have) else "No keys yet", icon="check" if all(have) else "key",
                       on_click=self.keys).classes("rt-found" if all(have) else "rt-missing")
            if t.phase in ("waiting", "finished"):
                button("Make a page to share", icon="ios_share", on_click=self.make_page)
            if t.phase in ("setup", "finished"):
                button("Past conversations", icon="folder_open", on_click=self.past)

    def body(self) -> None:
        self.view = "setup" if table.phase == "setup" else table.talk
        self.mirrors, self.clock = [], None
        with ui.element("div").classes("rt-body"):
            if table.phase == "setup":
                self.set_the_table()
            else:
                self.conversation()

    # ----- 1. set the table -----

    def set_the_table(self) -> None:
        t = table

        def changed(setting: str, value) -> None:
            setattr(t, setting, value)
            t.remember()

        def length_chosen(e) -> None:
            changed("match", e.value)
            about.set_visibility(not e.value)

        def words_typed(e) -> None:
            if e.value is not None and float(e.value).is_integer() and 10 <= e.value <= 2000:
                changed("words", int(e.value))

        def name_typed(e) -> None:
            if name_problem(e.value) is None:
                t.set_name(e.value)

        with ui.element("aside").classes("rt-side"):
            with ui.column().classes("w-full gap-2.5"):
                with ui.column().classes("gap-0.5"):
                    ui.label("Speaking order").classes("rt-title")
                    ui.label("Drag to change who speaks first. Untick anyone who should sit this one out.").classes("rt-note")
                self.part("seats", self.seats)

            with ui.row().classes("w-full items-center justify-between flex-nowrap gap-2.5"):
                with ui.column().classes("gap-0.5"):
                    ui.label("Rounds").classes("rt-title")
                    ui.label("Then you can join in.").classes("rt-note")
                self.mirror(stepper(lambda: t.rounds, lambda n: changed("rounds", n)), lambda: t.rounds)

            with ui.column().classes("w-full gap-1"):
                ui.label("Reply length").classes("rt-title")
                length = ui.radio({True: "Match my opening post", False: "A length I choose"}, value=t.match,
                                  on_change=length_chosen).props("dense").classes("rt-radio")
                self.mirror(length, lambda: t.match)
                with ui.row().classes("items-center gap-2 pl-7") as about:
                    ui.label("About")
                    words = ui.number(value=t.words, min=10, max=2000, step=10, format="%d", on_change=words_typed)
                    words.props('outlined dense hide-bottom-space aria-label="Words in a reply"')
                    words.classes("rt-field rt-count rt-wide").on("blur", lambda: words.set_value(t.words))
                    self.mirror(words, lambda: t.words)
                    ui.label("words")
                about.set_visibility(not t.match)

            with ui.row().classes("w-full items-center justify-between flex-nowrap gap-2.5"):
                with ui.column().classes("gap-0.5"):
                    ui.label("Thinking").classes("rt-title")
                    ui.label("How hard the models think before they reply.").classes("rt-note")
                self.part("thinking", self.thinking)

            with ui.column().classes("w-full gap-1.5"):
                ui.label("Your name").classes("rt-title")
                self.name_box = ui.input(value=t.name, validation=name_problem, on_change=name_typed)
                self.name_box.props('outlined dense aria-label="Your name" hint="The models address you by it."')
                self.name_box.classes("rt-field w-full")
                self.mirror(self.name_box, lambda: t.name)

        with ui.element("main").classes("rt-main"):
            ui.label("What should they talk about?").classes("rt-h1")
            ui.label("Write the opening post. The models then take turns, each answering what the others "
                     "have just said.").classes("rt-text")
            ui.label("Your opening post").classes("rt-title")
            post = ui.textarea().bind_value(t, "post").props('outlined aria-label="Your opening post"')
            post.classes("rt-area rt-post").on("keydown.ctrl.enter", self.start)
            with ui.row().classes("w-full items-center justify-between gap-3"):
                self.count = ui.label().classes("rt-text")
                button("Start the conversation", on_click=self.start, kind="go").props("icon-right=arrow_forward")
            with ui.row().classes("w-full items-center justify-between gap-3 flex-nowrap"):
                ui.label("Everything is saved as it goes, as Markdown and JSON, in the conversations folder.").classes("rt-note")
                if shortcut_wanted():
                    where = "my applications" if sys.platform.startswith("linux") else "my Desktop"
                    button(f"Add to {where}", on_click=self.add_shortcut, kind="quiet")
        self.count_words()

    def count_words(self) -> None:
        """Say how long your post is so far, and how long the replies will aim to be."""
        t = table
        count = len(t.post.split())
        words, why = rt.reply_length(t.post, None if t.match else t.words)
        limit = " (the shortest they go)" if "minimum" in why else " (the longest they go)" if "maximum" in why else ""
        self.count.set_text(f"{count} word{'' if count == 1 else 's'}. Replies will aim for about {words}{limit}."
                            if count else f"Replies will aim for about {'the length of your post' if t.match else words}.")

    def seats(self) -> None:
        """The participants in speaking order: a handle to move each one, its model, and whether it takes part."""
        self.grips = {}
        with ui.column().classes("w-full gap-2") as rows:
            for who in table.order:
                self.seat(who)
        rows.make_sortable(handle=".rt-grip", on_end=lambda e: self.moved(e.old_index, e.new_index))

    def seat(self, who: str) -> None:
        t, name = table, NAMES[who]

        def sit(e) -> None:
            if not e.value and len(t.seated) <= 2:
                ui.notify("A conversation needs at least two at the table.", type="warning", position="top")
                e.sender.value = True
                return
            seated = set(t.seated) | {who} if e.value else set(t.seated) - {who}
            t.seated = [w for w in t.order if w in seated]
            t.remember()
            row.classes(remove="rt-out") if e.value else row.classes(add="rt-out")
            model.set_enabled(e.value)
            self.drawn = self.state()  # this tab already shows the change

        with ui.row().classes("rt-seat" + ("" if who in t.seated else " rt-out")) as row:
            grip = button(icon="drag_indicator", kind="bare", label=f"Move {name}").classes("rt-grip")
            grip.on("keydown.up.prevent", lambda: self.nudge(who, -1))
            grip.on("keydown.down.prevent", lambda: self.nudge(who, +1))
            grip.tooltip("Drag, or press ↑ or ↓, to change the speaking order")
            self.grips[who] = grip
            mark(who)
            with ui.column().classes("rt-who"):
                ui.label(name).classes("rt-name")
                if not rt.find_api_key(who)[0]:
                    ui.label("No key yet").classes("rt-warn")
            model = self.model_picker(who, t.models[who])
            model.set_enabled(who in t.seated)
            ui.checkbox(value=who in t.seated, on_change=sit).props(f'dense aria-label="{name} takes part"')

    def moved(self, old: int, new: int) -> None:
        """A seat was dragged to a new place."""
        t = table
        t.order.insert(new, t.order.pop(old))
        t.seated = [who for who in t.order if who in t.seated]
        t.remember()
        self.drawn = self.state()  # the drag has already moved the row in this tab

    def nudge(self, who: str, by: int) -> None:
        """Move a seat one place up or down from the keyboard, and keep the keyboard on its handle."""
        old = table.order.index(who)
        if 0 <= old + by < len(table.order):
            self.moved(old, old + by)
            self.redraw("seats")
            self.tab.run_javascript(f"getHtmlElement({self.grips[who].id})?.focus()")

    def pick(self, current, choices: dict, choose, *, label: str, other=None) -> ui.button:
        """A button that shows the current choice and opens a menu of the others. `choices` maps each to (name, note)."""
        shown = choices[current][0]
        with button(kind="pick", label=f"{label}: {shown}").props("no-wrap") as made:
            ui.label(shown).classes("ellipsis")
            ui.icon("expand_more")
            with ui.menu().props("auto-close"):  # (closed by the browser: choosing redraws the button, menu and all)
                for value, (name, note) in choices.items():
                    with ui.menu_item(on_click=lambda value=value: self.attempt(choose, value), auto_close=False).classes("rt-choice"):
                        with ui.column().classes("gap-0 py-1"):
                            ui.label(name).classes("rt-chosen" if value == current else "")
                            ui.label(note).classes("rt-note")
                if other:
                    ui.separator()
                    ui.menu_item("Another model…", on_click=other, auto_close=False)
        return made

    def model_picker(self, who: str, current: str) -> ui.button:
        """The lighter and stronger models on offer for a participant, and a way to name any other."""
        choices = {model: (model, note) for model, note in rt.MODEL_CHOICES.get(who, {}).items()}
        choices.setdefault(current, (current, "the one in use now"))
        return self.pick(current, choices, lambda model: table.set_model(who, model),
                         label=f"Model for {NAMES[who]}", other=lambda: self.other_model(who))

    def thinking(self) -> None:
        """The button that sets how hard the models think."""
        current = table.effort if table.phase == "setup" else rt.REASONING_EFFORT
        choices = dict(EFFORTS)
        choices.setdefault(current, (str(current), "the setting in use now"))
        self.pick(current, choices, table.set_effort, label="Thinking")

    def start(self) -> None:
        if self.name_box.error:
            ui.notify("Please fix your name on the left first.", type="warning", position="top")
            return
        self.attempt(table.start)

    def add_shortcut(self) -> None:
        try:
            where = install_shortcut()
        except OSError as err:
            ui.notify(f"Couldn't add it: {err}", type="negative", position="top", multi_line=True)
            return
        ui.notify(f"Added. Look for LLM Roundtable among your applications.\n{where}" if sys.platform.startswith("linux")
                  else f"Added:\n{where}", type="positive", position="top", multi_line=True, classes="rt-notice")
        self.redraw("body")

    # ----- 2 and 3. the conversation, and your move -----

    def conversation(self) -> None:
        t = table
        self.shown, self.last_round = 0, None  # how many turns this tab has drawn, and the round of the last one
        with ui.element("aside").classes("rt-side rt-narrow"):
            self.part("side", self.side)
        with ui.element("main").classes("rt-main"):
            with ui.element("div").classes("rt-transcript").props('role="log" aria-live="polite"') as self.transcript:
                with ui.column().classes("rt-turns"):
                    self.turns = ui.column().classes("rt-turns")
                    with ui.column().classes("rt-turns") as self.pending:  # whoever is thinking
                        with ui.column().classes("w-full") as self.pending_rule:
                            self.pending_round = divider()
                        with ui.row().classes("rt-thinking"):
                            self.pending_mark = ui.html("", sanitize=False).classes("rt-mark")
                            self.pending_name = ui.label().classes("rt-name")
                            ui.label("is thinking").classes("rt-status")
                            ui.spinner("dots", size="1.6em", color="grey-7")

            # What you can do next. One box serves both while the models talk and when it is your move.
            with ui.column().classes("rt-card") as self.card_next:
                with ui.column().classes("rt-trouble") as self.trouble:
                    self.trouble_title = ui.label().classes("rt-strong")
                    self.trouble_text = ui.label().classes("rt-typed w-full")
                    ui.label("You can choose another model on the left, or look at the keys (top right), and then "
                             "try again.").classes("rt-note")
                self.what_next = ui.label().classes("rt-h2")
                ui.label("Join in").classes("rt-title")
                with ui.row().classes("w-full items-end gap-3 flex-nowrap"):
                    entry = ui.textarea(placeholder="Write an entry of your own").bind_value(t, "entry")
                    entry.props('outlined autogrow rows=2 aria-label="Your entry"').classes("rt-area rt-entry-box flex-1")
                    entry.on("keydown.ctrl.enter", self.enter)
                    self.queue = button("Add after this round", on_click=self.toggle_queue, kind="go")
                with ui.row().classes("w-full items-center justify-between gap-3") as self.moves:
                    with ui.row().classes("items-center gap-2"):
                        ui.label("Rounds to run next").classes("rt-strong")
                        self.mirror(stepper(lambda: t.next_rounds, lambda n: setattr(t, "next_rounds", n)),
                                    lambda: t.next_rounds)
                    with ui.row().classes("items-center gap-2.5"):
                        button("Finish", on_click=lambda: self.attempt(t.finish), kind="quiet")
                        self.carry = button("Carry on without me", on_click=lambda: self.attempt(t.go, t.next_rounds))
                        button("Send and continue", on_click=lambda: self.attempt(t.send, t.next_rounds),
                               kind="go").props("icon-right=arrow_forward")
                self.hint = ui.label().classes("rt-note")

            with ui.row().classes("rt-card items-center justify-between flex-nowrap") as self.card_done:
                self.done_text = ui.label().classes("rt-text flex-1 min-w-0")
                with ui.row().classes("items-center gap-2.5 flex-nowrap"):
                    button("Carry it on", on_click=self.carry_on)
                    button("Start a new conversation", on_click=lambda: self.attempt(t.new), kind="go")

        # Keep the newest reply in view, unless you have scrolled up to read something earlier.
        self.tab.run_javascript(f"""
            const box = getHtmlElement({self.transcript.id});
            let follow = {"false" if t.loaded else "true"};
            const settle = () => {{ if (follow) box.scrollTop = box.scrollHeight; }};
            box.addEventListener("scroll", () => {{ follow = box.scrollHeight - box.scrollTop - box.clientHeight < 80; }});
            new MutationObserver(settle).observe(box, {{ childList: true, subtree: true, characterData: true }});
            new ResizeObserver(settle).observe(box);
            settle();
        """)
        self.new_turns()
        self.composer()

    def new_turns(self) -> None:
        """Draw the turns that have arrived since this tab last looked, and show who is thinking now."""
        t, talk = table, table.talk
        while self.shown < len(talk.transcript):
            with self.turns:
                self.turn(talk.transcript[self.shown])
            self.shown += 1
        self.pending.set_visibility(bool(t.speaking))
        if t.speaking:
            self.pending_rule.set_visibility(talk.round_no != self.last_round)  # the first reply of a new round
            self.pending_round.set_text(f"Round {talk.round_no}")
            self.pending_mark.set_content(shape(t.speaking))
            self.pending_name.set_text(NAMES[t.speaking])
            if self.clock:
                self.clock.set_text(f"Thinking, {int(time.monotonic() - t.since)} s")

    def turn(self, turn: dict) -> None:
        """Draw one turn of the conversation."""
        if turn["round"] != self.last_round and turn["round"]:
            divider(f"Round {turn['round']}")
        self.last_round = turn["round"]
        who = WHO.get(turn["speaker"])
        if who:  # a model's reply is Markdown
            with ui.column().classes("rt-reply"):
                with ui.row().classes("items-center gap-2"):
                    mark(who)
                    ui.label(turn["speaker"]).classes("rt-name")
                    ui.label(turn.get("model") or "").classes("rt-note")
                ui.markdown(turn["text"], extras=["fenced-code-blocks", "tables", "break-on-newline"]).classes("rt-said")
            return

        def show_all() -> None:
            said.classes(toggle="rt-clamp")
            more.set_text("Show all" if "rt-clamp" in said.classes else "Show less")

        # What you wrote is shown just as you typed it. A long opening post starts folded, since you have only just written it.
        long = not turn["round"] and not table.loaded and len(turn["text"].split()) > 70
        with ui.column().classes("rt-yours"):
            with ui.row().classes("w-full items-center gap-2"):
                mark("you")
                ui.label(turn["speaker"]).classes("rt-name")
                ui.label("" if turn["round"] else "Opening post").classes("rt-note flex-1")
                if long:
                    more = button("Show all", on_click=show_all, kind="quiet")
            said = ui.label(turn["text"]).classes("rt-said rt-typed" + (" rt-clamp" if long else ""))

    def side(self) -> None:
        """Who is at the table and what each is doing, and a few facts about the conversation."""
        t, talk = table, table.talk
        waiting, left = t.phase == "waiting", t.left()
        self.clock = None
        ui.label("At the table").classes("rt-title")
        with ui.column().classes("w-full gap-2"):
            for who in talk.order:
                with ui.row().classes("rt-place" + (" rt-now" if t.speaking == who else "")):
                    mark(who)
                    with ui.column().classes("flex-1 min-w-0 gap-0.5"):
                        ui.label(NAMES[who]).classes("rt-name")
                        if waiting:  # while it is your move, the model can be changed
                            self.model_picker(who, rt.MODELS[who])
                        else:
                            ui.label(rt.MODELS[who]).classes("rt-note ellipsis w-full")
                    if t.speaking == who:
                        self.clock = ui.label("Thinking").classes("rt-status")
                    elif t.phase != "finished":
                        spoke = talk.round_no > 0 and who not in left and (bool(left) or not waiting)
                        ui.label("Spoke" if spoke else "Waiting").classes("rt-status")
            with ui.row().classes("rt-place rt-you" + (" rt-now" if waiting else "")):
                mark("you")
                ui.label(talk.human).classes("rt-name flex-1")
                ui.label({"running": "Any time", "waiting": "Your move"}.get(t.phase, "")).classes("rt-status")
        with ui.column().classes("rt-facts"):
            with ui.row().classes("rt-fact"):
                ui.label("Reply length")
                ui.label(f"About {talk.words} words")
            with ui.row().classes("rt-fact"):
                ui.label("Thinking")
                if waiting:
                    self.thinking()
                else:
                    ui.label(EFFORTS.get(rt.REASONING_EFFORT, (str(rt.REASONING_EFFORT),))[0])
            with ui.row().classes("rt-fact"):
                ui.label("Replies so far")
                ui.label(str(sum(1 for turn in talk.transcript if turn["speaker"] in WHO)))
            with ui.column().classes("w-full gap-1"):
                ui.label("Saved as it goes to" if t.phase == "running" else "Saved to").classes("rt-fact-name")
                ui.label(talk.base.name + ".md").classes("rt-mono")
        button("Open the folder", icon="folder_open", on_click=self.open_folder).classes("w-full")

    def composer(self) -> None:
        """Set the box under the conversation to fit the moment: the models are talking, it is your move, or it is over."""
        t, talk = table, table.talk
        left = t.left()
        running, waiting, wrong = t.phase == "running", t.phase == "waiting", t.pause == "error"
        now = talk.round_no + (0 if left else 1)  # the round in progress, or about to begin...
        last = now >= t.target                      # ...which may be the last one asked for
        up_next = NAMES[left[0]] if left else ""

        self.card_next.set_visibility(running or waiting)
        self.card_done.set_visibility(t.phase == "finished")
        self.card_next.classes(add="rt-now") if waiting else self.card_next.classes(remove="rt-now")
        self.trouble.set_visibility(waiting and wrong)
        what, _, detail = t.problem.partition("\n")
        self.trouble_title.set_text(what)
        self.trouble_text.set_text(detail)
        self.trouble_text.set_visibility(bool(detail))
        self.what_next.set_visibility(waiting)
        self.moves.set_visibility(waiting)
        self.queue.set_visibility(running and not last and not t.stopping)
        self.queue.set_text("Don't add it" if t.queued else "Add after this round")
        self.queue.classes(remove="rt-go rt-plain", add="rt-plain" if t.queued else "rt-go")
        self.carry.set_text("Try again" if wrong else "Carry on without me")

        if waiting and left:
            self.what_next.set_text("What next?" if wrong else f"Round {talk.round_no} stopped before {up_next}'s turn. What next?")
            self.hint.set_text(f"{'Try again' if wrong else 'Carry on without me'} picks up at {up_next}'s turn, and round "
                               f"{talk.round_no} counts as the first of these rounds. An entry from you opens a new round instead.")
        elif waiting:
            done = f"{spelled(talk.round_no)} round{'' if talk.round_no == 1 else 's'} done. "
            self.what_next.set_text(("" if wrong else done) + "What next?")
            self.hint.set_text("Set it to 1 for a final round. The models answer your entry first.")
        elif t.stopping:
            self.hint.set_text("Stopping as soon as this reply is in. What you have typed stays here.")
        elif last:
            self.hint.set_text(f"You can type while they talk. Round {now} is the last for now, so you can send your "
                               "entry when it ends.")
        elif t.queued:
            self.hint.set_text(f"Your entry goes in when round {now} ends, and the models answer you first. "
                               "You can go on editing it until then.")
        else:
            self.hint.set_text(f"You can type while they talk. Your entry goes in when round {now} ends, and the "
                               "models answer you first.")
        if t.phase == "finished":
            started = datetime.fromisoformat(talk.details["started"])
            self.done_text.set_text(f"A conversation from {started.day} {started:%B %Y}." if t.loaded
                                    else "Finished. It is saved in the conversations folder.")

    def toggle_queue(self) -> None:
        """Mark what you have typed to go in when the current round ends, or take that back."""
        if not table.queued and not table.entry.strip():
            ui.notify("Write your entry first.", type="warning", position="top")
            return
        table.queued = not table.queued
        self.sync()

    def enter(self) -> None:
        """Ctrl+Enter in the entry box does what the main button beside or below it does."""
        if table.phase == "waiting":
            self.attempt(table.send, table.next_rounds)
        elif table.phase == "running" and not table.queued and self.queue.visible:
            self.toggle_queue()

    def carry_on(self) -> None:
        if self.attempt(table.carry_on):
            self.tab.run_javascript(f"getHtmlElement({self.transcript.id}).scrollTop = 1e9")  # down to where it left off

    def open_folder(self) -> None:
        try:
            open_folder(rt.SAVE_DIR)
        except OSError as err:
            ui.notify(f"Couldn't open {rt.SAVE_DIR}: {err}", type="negative", position="top", multi_line=True)

    # ----- dialogs -----

    @contextmanager
    def dialog(self, name: str, title: str = "", *, must_answer: bool = False):
        """Open the dialog (or change what the open one shows). What is drawn inside the `with` goes into it."""
        self.showing = name
        self.card.clear()
        self.box.props("persistent") if must_answer else self.box.props(remove="persistent")
        with self.card:
            if title:
                with ui.row().classes("w-full items-center justify-between flex-nowrap"):
                    ui.label(title).classes("rt-h2")
                    button(icon="close", on_click=self.box.close, kind="bare", label="Close")
            yield
        self.box.open()

    def closed(self) -> None:
        self.showing = ""
        with self.card:  # emptied once it has faded out (unless it has been opened again by then)
            ui.timer(0.3, lambda: None if self.box.value else self.card.clear(), once=True)

    def ask_name(self) -> None:
        """Asked once, the first time the window opens. After that the name is changed under "Your name"."""
        def done() -> None:
            if name.validate():
                table.set_name(name.value)
                self.box.close()

        def not_now() -> None:
            table.named = True
            table.remember()
            self.box.close()

        with self.dialog("name", must_answer=True):
            ui.label("Welcome to LLM Roundtable").classes("rt-h2")
            ui.label("What should the models call you? They address you by this name, and your posts are "
                     "labelled with it. You can change it whenever you like.").classes("rt-text")
            name = ui.input(placeholder="Your name", validation=name_problem).without_auto_validation()
            name.props('outlined autofocus aria-label="Your name"').classes("rt-field w-full")
            name.on("keydown.enter", done)
            with ui.row().classes("w-full justify-end items-center gap-2.5"):
                button("Not now", on_click=not_now, kind="quiet")
                button("Continue", on_click=done, kind="go")

    def keys(self) -> None:
        """Which API keys have been found, with a place to paste one and a button to test it."""
        def save(who: str, field: ui.input) -> None:
            key = "".join(field.value.split())
            if len(key) < 8:
                ui.notify("That doesn't look like a whole key.", type="warning", position="top")
                return
            from_environment = rt.find_api_key(who)[1] not in (None, *rt.KEYS_FROM_FILE)
            try:
                rt.save_api_key(who, key)
                note = f"Saved. {NAMES[who]} uses this key from now on."
                if from_environment:
                    note += (" Your computer's environment sets a key of its own for it, though, and that one "
                             "comes first each time the program starts.")
            except OSError as err:
                note = f"Couldn't save it ({err}), so it will be used only until you close the program."
            self.checks.pop(who, None)
            ui.notify(note, type="positive", position="top", multi_line=True)
            self.keys()
            self.sync()

        async def check(who: str, pressed: ui.button) -> None:
            pressed.props("loading")
            try:
                self.checks[who] = await run.io_bound(rt.check_one, who)
            except rt.RoundtableError as err:
                self.checks[who] = (False, str(err))
            if self.showing == "keys":
                self.keys()

        with self.dialog("keys", "API keys"):
            ui.label("Each model needs a key from its maker. Keys stay on this computer, in a file called .env "
                     "beside the program. A key lets its holder spend your credit, so treat it like a "
                     "password.").classes("rt-text")
            for who, info in rt.PARTICIPANTS.items():
                key, variable = rt.find_api_key(who)
                with ui.column().classes("rt-key"):
                    with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
                        mark(who)
                        ui.label(info["name"]).classes("rt-name")
                        where = "the .env file" if variable in rt.KEYS_FROM_FILE else f"your environment ({variable})"
                        ui.label(f"Found in {where}, ending …{key[-4:]}" if key else "No key yet").classes(
                            "rt-note flex-1" if key else "rt-warn flex-1")
                        if key:
                            pressed = button("Check")
                            pressed.on_click(lambda who=who, pressed=pressed: check(who, pressed))
                    if who in self.checks:
                        worked, detail = self.checks[who]
                        ui.label(f"It works. {rt.MODELS[who]} answered: {detail}" if worked else detail).classes(
                            "rt-ok text-sm" if worked else "rt-bad text-sm")
                    with ui.row().classes("w-full items-center gap-2 flex-nowrap"):
                        field = ui.input(placeholder="Paste a new key to replace it" if key else "Paste a key",
                                         password=True)
                        field.props(f'outlined dense hide-bottom-space aria-label="A key for {info["name"]}"')
                        field.classes("rt-field flex-1")
                        button("Save", on_click=lambda who=who, field=field: save(who, field))
                    ui.link(f"Get a key from {info['maker']}", info["key_url"], new_tab=True).classes("rt-link")
            with ui.row().classes("w-full justify-end"):
                button("Done", on_click=self.box.close, kind="go")

    def other_model(self, who: str) -> None:
        """Type the name of a model that isn't in the list."""
        def use() -> None:
            if not good_model(name.value.strip()):
                ui.notify("A model's name has no spaces, just letters, digits, dots and dashes.", type="warning",
                          position="top")
            elif self.attempt(table.set_model, who, name.value.strip()):
                self.box.close()

        info = rt.PARTICIPANTS[who]
        with self.dialog("model", f"Another model for {info['name']}"):
            example = next(iter(rt.MODEL_CHOICES.get(who, {})), rt.MODELS[who])
            ui.label(f"Type the model's name exactly as {info['maker']}'s documentation gives it, "
                     f"for example {example}.").classes("rt-text")
            name = ui.input(placeholder="Model name").props('outlined autofocus aria-label="Model name"')
            name.classes("rt-field w-full").on("keydown.enter", use)
            with ui.row().classes("w-full justify-end items-center gap-2.5"):
                button("Cancel", on_click=self.box.close, kind="quiet")
                button("Use this model", on_click=use, kind="go")

    async def past(self) -> None:
        """The conversations saved so far. Choosing one opens it, to read, carry on or make a page from."""
        def choose(file: Path) -> None:
            if self.attempt(table.open, file):
                self.box.close()

        found = await run.io_bound(saved_conversations)
        with self.dialog("past", "Past conversations"):
            if not found:
                ui.label("Nothing here yet. Conversations are saved in the conversations folder as they "
                         "happen.").classes("rt-text")
            with ui.column().classes("rt-list"):
                for item in found:
                    with ui.column().classes("rt-past").props("tabindex=0 role=button") as row:
                        ui.label(item["title"]).classes("rt-name")
                        ui.label(item["about"]).classes("rt-note")
                    row.on("click", lambda file=item["file"]: choose(file))
                    row.on("keydown.enter", lambda file=item["file"]: choose(file))
            with ui.row().classes("w-full justify-between items-center"):
                button("Open the folder", icon="folder_open", on_click=self.open_folder)
                button("Close", on_click=self.box.close, kind="go")

    def make_page(self) -> None:
        """Lay the conversation out as a page to share: a Markdown file that GitHub and many other sites display."""
        talk = table.talk

        def make() -> None:
            try:
                page = rt.write_page(talk.base.with_suffix(".json"), title.value)
            except rt.RoundtableError as err:
                ui.notify(str(err), type="negative", position="top", multi_line=True)
                return
            with self.dialog("page", "Your page is ready"):
                ui.label("It is a Markdown file, which GitHub and many other sites show as a page, with a title, "
                         "a list of who took part and the whole conversation.").classes("rt-text")
                ui.label(page.name).classes("rt-mono")
                with ui.row().classes("w-full justify-end items-center gap-2.5"):
                    button("Open the folder", icon="folder_open", on_click=self.open_folder)
                    button("Done", on_click=self.box.close, kind="go")

        with self.dialog("page", "Make a page to share"):
            ui.label("Give the page a title. Everything said stays word for word as it was.").classes("rt-text")
            title = ui.input(value=rt.default_title({"turns": talk.transcript})).props('outlined aria-label="Title"')
            title.classes("rt-field w-full").on("keydown.enter", make)
            with ui.row().classes("w-full justify-end items-center gap-2.5"):
                button("Cancel", on_click=self.box.close, kind="quiet")
                button("Make the page", on_click=make, kind="go")


table = Table()


# A reply is the model's own Markdown, so it may hold links and pictures. Links open outside the window, and
# pictures and frames from elsewhere are not fetched: nothing a model writes can make the window call out.
HEAD = """
<base target="_blank">
<meta http-equiv="Content-Security-Policy" content="img-src 'self' data: blob:; media-src 'self'; frame-src 'none'; object-src 'none'">
"""


def page() -> None:
    """Draw the window. This runs once for each browser tab that opens it."""
    ui.add_head_html(HEAD)
    ui.add_css(CSS)
    Window()


# =============================================================================
# STARTING AND STOPPING
# =============================================================================

def shortcut() -> tuple[Path, str]:
    """Where the shortcut that starts the window goes on this computer, and what it says."""
    python, script = sys.executable, Path(__file__).resolve()
    if sys.platform.startswith("linux"):  # an entry in the applications menu
        def plain(text) -> str:  # in a desktop file, a backslash is written twice
            return str(text).replace("\\", "\\\\")

        def quoted(text) -> str:  # ...and a command's arguments go in quotes, with a backslash before " ` $ and \
            return plain('"' + re.sub(r'(["`$\\])', r"\\\1", str(text)).replace("%", "%%") + '"')

        folder = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "applications"
        lines = ["[Desktop Entry]", "Type=Application", "Name=LLM Roundtable",
                 "Comment=A turn-taking conversation between ChatGPT, Gemini and Claude",
                 f"Exec={quoted(python)} {quoted(script)}", f"Path={plain(rt.HERE)}",
                 *([f"Icon={plain(ICON)}"] if ICON.exists() else []), "Terminal=false", "Categories=Utility;"]
        return folder / "llm-roundtable.desktop", "\n".join(lines) + "\n"
    if sys.platform == "darwin":  # a file on the Desktop to double-click
        command = f"#!/bin/sh\nexec {shlex.quote(python)} {shlex.quote(str(script))}\n"
        return Path.home() / "Desktop" / "LLM Roundtable.command", command
    run = f'"{python}" "{script}"'.replace("%", "%%")  # Windows: a batch file, which stays open if something goes wrong
    lines = ["@echo off", "title LLM Roundtable", run, "if errorlevel 1 pause"]
    return Path.home() / "Desktop" / "LLM Roundtable.bat", "\r\n".join(lines) + "\r\n"


def shortcut_wanted() -> bool:
    """True unless an up-to-date shortcut is already in place."""
    file, text = shortcut()
    try:
        return file.read_text(encoding="utf-8") != text
    except (OSError, UnicodeError):
        return True


def install_shortcut() -> Path:
    """Write the shortcut, so the window can be started without a terminal."""
    file, text = shortcut()
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(text, encoding="utf-8", newline="")
    file.chmod(0o755)
    return file


def app_browsers() -> list[str]:
    """The browsers on this computer that can show a page in a window of its own (Chrome and its relatives)."""
    if sys.platform == "darwin":
        names = ["Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium"]
        found = [f"{folder}/{name}.app/Contents/MacOS/{name}" for name in names for folder in ("/Applications", Path.home() / "Applications")]
    elif sys.platform == "win32":
        folders = [os.environ.get(name) for name in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        kinds = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe"]
        found = [os.path.join(folder, kind) for kind in kinds for folder in folders if folder]
    else:
        names = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge", "brave-browser"]
        found = [shutil.which(name) for name in names]
    return [str(path) for path in found if path and os.path.exists(path)]


def open_window(url: str, as_tab: bool = False) -> None:
    """Show the page: in a window of its own if a browser that can do that is installed, otherwise as a browser tab."""
    for browser in [] if as_tab else app_browsers():
        try:
            started = subprocess.Popen([browser, f"--app={url}", "--window-size=1280,900"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
            if started.wait(timeout=2) == 0:
                return  # it passed the page to a browser that was already running
        except subprocess.TimeoutExpired:
            return  # still going, so it is the browser itself
        except OSError:
            continue  # this one would not start: try the next
    webbrowser.open(url)


def fetch(url: str) -> dict | None:
    """Ask this computer (never a proxy) for a page of JSON. None if nothing answers with any."""
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=2) as reply:
            answer = json.load(reply)
    except (OSError, ValueError):
        return None
    return answer if isinstance(answer, dict) else None


def free_port() -> int:
    """PORT if nothing else is using it, otherwise a port that is free."""
    for port in (PORT, 0):
        with socket.socket() as probe:
            if os.name == "posix":  # a port that was only just vacated counts as free, as it will for the server
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind(("127.0.0.1", port))
                return probe.getsockname()[1]
            except OSError:
                continue
    raise rt.RoundtableError("This computer has no free port for the window to use.")


def watch() -> None:
    """Close the program once its window has been closed for a while. Runs every second."""
    t = table
    if any(client.has_socket_connection for client in Client.instances.values()):
        t.opened, t.last_seen = True, time.monotonic()
    elif t.opened and time.monotonic() - t.last_seen > GRACE:
        if t.phase == "running":
            t.stopping = True  # the reply that is on its way is waited for, and saved
        else:
            app.shutdown()


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM Roundtable in a window.")
    parser.add_argument("--tab", action="store_true", help="open the window as a tab in your usual browser")
    parser.add_argument("--shortcut", action="store_true", help="add LLM Roundtable to your applications (on macOS and Windows, to your Desktop), then exit")
    parser.add_argument("--no-window", action="store_true", help="don't open a window; just print the address to open")
    args = parser.parse_args()

    for stream in ("stdout", "stderr"):  # started without a terminal, there may be nowhere to print
        if getattr(sys, stream) is None:
            setattr(sys, stream, open(os.devnull, "w", encoding="utf-8"))
    if args.shortcut:
        try:
            print(f"Added:\n  {install_shortcut()}")
        except OSError as err:
            sys.exit(f"Couldn't add the shortcut: {err}")
        return

    me = {"app": "LLM Roundtable", "folder": str(rt.HERE)}
    if fetch(f"http://127.0.0.1:{PORT}/roundtable") == me:  # already running: show that one instead of starting another
        print("LLM Roundtable is already running.")
        if not args.no_window:
            open_window(f"http://127.0.0.1:{PORT}", args.tab)
        return
    try:
        rt.load_env_file()
        port = free_port()
    except rt.RoundtableError as err:
        sys.exit(f"\n{err}")
    url = f"http://127.0.0.1:{port}"

    from starlette.middleware.trustedhost import TrustedHostMiddleware
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])  # only this computer's own browser
    app.add_api_route("/roundtable", lambda: me)
    app.colors(primary="#14181F")
    app.timer(1.0, watch)

    async def opened() -> None:
        print(f"LLM Roundtable is open at {url}\nClose its window to quit (or press Ctrl+C here).", flush=True)
        if not args.no_window:
            await run.io_bound(open_window, url, args.tab)

    app.on_startup(opened)
    try:
        ui.run(page, host="127.0.0.1", port=port, title="LLM Roundtable", favicon=ICON if ICON.exists() else None,
               show=False, reload=False, show_welcome_message=False)
    except KeyboardInterrupt:
        pass  # Ctrl+C: the server has already shut itself down
    try:
        if table.busy:
            print("Waiting for the reply that is on its way, so that it is saved too. (Press Ctrl+C again to leave now.)")
        while table.busy:
            time.sleep(0.1)
        print("LLM Roundtable has closed.")
    except KeyboardInterrupt:
        print("LLM Roundtable has closed.", flush=True)
        os._exit(0)  # leave without the reply; everything before it is saved


if __name__ == "__main__":
    main()
