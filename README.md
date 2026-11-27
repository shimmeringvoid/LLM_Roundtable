# LLM Roundtable

A Python script that runs a turn-taking conversation between ChatGPT, Gemini and Claude.

You write the opening post. The models then take turns in the order you choose, each replying briefly to what the others just said. The whole conversation is saved as it goes, as a readable Markdown transcript and as a JSON file.

## Setup

You need [conda](https://docs.conda.io/) (Miniconda, Anaconda or Miniforge all work) and an API key for each model that takes part.

### 1. Create the conda environment

In a terminal, from this folder:

```bash
conda env create -f environment.yml
conda activate roundtable
```

This creates an environment named `roundtable` with Python 3.13 and the three official SDKs (`openai`, `google-genai`, `anthropic`). Run `conda activate roundtable` in each new terminal before you use the script.

- On Windows, use the Anaconda Prompt, or any terminal where the `conda` command works.
- Newer conda versions prefer `conda create --file environment.yml`. Both commands do the same thing.
- Not using conda? Any Python 3.11 or newer will do: `pip install openai google-genai anthropic`.

### 2. Get your API keys

| Participant | Create a key at | Good to know |
|---|---|---|
| ChatGPT | https://platform.openai.com/api-keys | API use is billed separately from a ChatGPT subscription. |
| Gemini | https://aistudio.google.com/apikey | The default model can be used on Google's free tier, within rate limits. Google may use free-tier content to improve its products. |
| Claude | https://platform.claude.com/settings/keys | The API uses prepaid credits bought in the Console and is billed separately from a Claude app plan. |

These billing notes were correct in November 2026.

You don't have to put the keys anywhere by hand. When the script can't find a key, it asks you to paste it and then offers to save it in a file named `.env` in this folder, so you aren't asked again.

If you would rather create that file yourself, copy `.env.example` to `.env` and fill in the keys. Environment variables with the same names (`OPENAI_API_KEY`, `GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) also work, and they take priority over the file.

> **Keep `.env` private.** Anyone who has a key can spend your credit. The file is listed in `.gitignore`, so git will not commit it. Before your first commit, run `git status` and confirm that `.env` is not in the list.

### 3. Check that it works

```bash
python llm_roundtable.py --check
```

This sends one tiny request to each model. For each one it reports `OK`, `FAILED` (with the reason and a hint), or `SKIPPED` if you didn't give a key.

## Running a conversation

```bash
python llm_roundtable.py
```

The script asks three things, and then the models take over:

1. **Speaking order**, for example `claude, chatgpt, gemini`. Press Enter for the default. List only two names to leave one model out.
2. **Number of rounds.** In one round, each participant speaks once.
3. **Your opening post.** Type or paste it (several paragraphs are fine), then type `END` on a line of its own.

A session looks like this, with the replies left out:

```text
Speaking order [chatgpt, gemini, claude]: claude, chatgpt, gemini
How many rounds? [3]: 2

Your opening post. Several paragraphs are fine; type END on a line of its own to send it.
> Is a hot dog a sandwich?
> END

Speaking order: Claude -> ChatGPT -> Gemini
Saving after every turn to .../conversations/roundtable_2026-11-26_15-04-10.md  (press Ctrl+C to stop early)

----- Round 1 -----

Claude:
  ...

ChatGPT:
  ...

Gemini:
  ...
```

After the last round you can ask for more rounds, or press Enter to finish. Press Ctrl+C at any time to stop early. Everything said up to that point is already saved.

### Command-line options

Anything you give on the command line is not asked for.

| Option | What it does |
|---|---|
| `--order claude,chatgpt,gemini` | Sets the speaking order: any two or all three of `chatgpt`, `gemini` and `claude`. |
| `--rounds 4` | Runs exactly this many rounds, then stops without offering more. |
| `--prompt "Is a hot dog a sandwich?"` | Gives your opening post. |
| `--prompt-file post.txt` | Reads the opening post from a text file. |
| `--max-words 80` | Sets the target length of each reply (default 120). |
| `--check` | Tests each API key and model name, then exits. |

For example, this runs from start to finish without asking anything:

```bash
python llm_roundtable.py --order gemini,claude,chatgpt --rounds 3 --prompt-file post.txt
```

## Saved conversations

Each conversation is written to the `conversations` folder as two files with the same name:

- `roundtable_<date>_<time>.md` is the transcript, for reading.
- `roundtable_<date>_<time>.json` holds the same turns with the speaker, model, round and time of each, plus the settings that were used.

Both files are rewritten after every turn, so a conversation that stops early is still saved up to that point.

The `conversations` folder is listed in `.gitignore`. Remove that line if you want your transcripts in the repository.

## Settings

The settings are at the top of `llm_roundtable.py`.

| Setting | Default | What it controls |
|---|---|---|
| `MODELS` | `gpt-6.2-sol`, `gemini-3.8-flash`, `claude-sonnet-5-5` | The model that plays each participant. A stronger, pricier alternative is noted beside each one. |
| `DEFAULT_ORDER` | ChatGPT, Gemini, Claude | The speaking order when you just press Enter. |
| `DEFAULT_ROUNDS` | 3 | The number of rounds when you just press Enter. |
| `MAX_WORDS` | 120 | The target length of each reply. |
| `REASONING_EFFORT` | `"low"` | How hard each model thinks before replying: `"low"`, `"medium"`, `"high"`, or `None` for each provider's own default. |
| `MAX_OUTPUT_TOKENS` | 8000 | The ceiling on tokens per reply, including hidden thinking. It is a cost safety net, not the length control. |
| `REQUEST_TIMEOUT` | 180 | Seconds to wait for one reply. |
| `USER_NAME` | `"User"` | How your opening post is labelled. |
| `SYSTEM_PROMPT` | | The standing instructions every model receives. Edit it to change the tone of the conversation. |

Providers rename and retire models often. The defaults were current in November 2026. If `--check` reports that a model was not found, put a current one in `MODELS`.

## How it works

The script keeps the transcript. On each turn it sends the speaking model the whole conversation from that model's point of view: its own earlier replies appear as its own turns, and everything the others said arrives labelled with the speaker's name, like `[Gemini]: ...`.

From the second round on, the newest thing each model reads is the other two models' latest replies. Its instructions tell it to respond to them directly and briefly.

## Cost

Every reply is one API call, so a run makes rounds × participants calls, and `--check` makes three small ones.

At November 2026 list prices, a default three-round conversation should cost roughly 5 to 15 US cents in total. That is an estimate: the models' hidden thinking is billed too, and its length varies.

## Troubleshooting

| Message | What to do |
|---|---|
| `The '...' package is not installed in this Python` | The conda environment isn't active. Run `conda activate roundtable`. |
| `The API key was rejected` | The key is wrong or incomplete. Fix or delete its line in `.env`, then run again. |
| `The key was recognised but isn't allowed to make this request` (the provider mentions permissions or scopes) | If you only just created the key or added billing, wait a few minutes and try again. If it still fails, open the provider's API keys page and give the key full permissions. For OpenAI, edit the key and set **Permissions** to **All**. |
| `The model name in MODELS may be out of date` | The provider no longer offers that model. Put a current one in `MODELS`. |
| `Rate limit or quota reached` | Wait a minute and try again, or check your credit and billing with that provider. |
| `The reply was empty`, mentioning a token limit | Raise `MAX_OUTPUT_TOKENS`. |
| A provider rejects one of the settings | Set `REASONING_EFFORT = None`. |

## Maintaining the environment

```bash
conda env update -f environment.yml --prune    # after you edit environment.yml
conda remove --name roundtable --all           # to delete the environment
```

## Files

| File | Purpose |
|---|---|
| `llm_roundtable.py` | The script. |
| `environment.yml` | The `roundtable` conda environment. |
| `.env.example` | A template for the API key file. |
| `.gitignore` | Keeps `.env`, `conversations/` and Python cache files out of git. |
| `LICENSE` | The MIT License. |

The script was written against `openai` 3.60, `google-genai` 3.6 and `anthropic` 2.14 on Python 3.13.

## License

Released under the [MIT License](LICENSE). You are free to use, change and share this project, including commercially, as long as the copyright and license notice stay with it. It comes with no warranty.
