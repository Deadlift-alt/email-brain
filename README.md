# 📬 email-brain

A **private, local-only memory for your email.** Import your mail into one SQLite file on your own computer, then:

- 🔎 search years of email in milliseconds
- 🧠 ask *"Did I already send this idea to anyone?"* before you pitch it twice
- ✅ record *what was fixed and how*, so next time you find the answer, not just the problem
- 🤖 chat with a **local** bot (via [Ollama](https://ollama.com)) that answers only from your emails and cites them

**Nothing leaves your machine.** No cloud, no telemetry, no accounts. The web UI refuses to bind to anything but `127.0.0.1`, and the bot refuses to talk to any non-local model server.

Works two ways: as a plain command-line tool (any Python 3.9+), or as a **Claude skill** so Claude can run it for you in plain English.

---

## Quick start (CLI)

```bash
git clone https://github.com/Deadlift-alt/email-brain.git
cd email-brain

# 1. create the database (list ALL your addresses so sent vs received is right)
python3 scripts/emailbrain.py init --me "me@work.com,me@gmail.com"

# 2. import an export (see "Getting your mail out" below)
python3 scripts/emailbrain.py ingest ~/Downloads/All-mail.mbox

# 3. use it
python3 scripts/emailbrain.py stats
python3 scripts/emailbrain.py search invoice failed --since 2025-01-01
python3 scripts/emailbrain.py similar "offer customers a points-based rewards scheme"
python3 scripts/emailbrain.py thread 123 --full
python3 scripts/emailbrain.py note 123 --tag fixed "updated the card, confirmed 6 Mar"
```

No `pip install` needed — the core uses only the Python standard library (SQLite with FTS5 is included in standard Python builds).

> Tip: `alias eb='python3 /path/to/email-brain/scripts/emailbrain.py'`

### Commands

| Command | What it does |
|---|---|
| `init --me a@x.com,b@y.com` | Create the DB; remember which addresses are yours |
| `ingest <file-or-folder>` | Import `.mbox`, `.eml`, or Maildir (recursive, safe to repeat) |
| `search <words>` | Ranked keyword search. Filters: `--from --to --since --until --direction sent\|received --tag --mode or --limit --json` |
| `similar <text>` | Find the closest **sent** emails to an idea (`--any` includes received) |
| `thread <id>` / `show <id>` | Read a whole conversation / one email with your notes |
| `note <id> --tag fixed "text"` | Attach your own outcome notes (tags: `fixed`, `idea-sent`, `waiting`, …) |
| `stats` | Counts, date range, top correspondents |
| `embed` | *(optional)* build local embeddings for meaning-based search |
| `search … --semantic` | Keyword + meaning search, merged |
| `ask "question"` / `chat` | Local bot answers from your emails, citing `[#id]` |
| `serve [--port 8765]` | Local web UI at `http://127.0.0.1:8765` |

Database lives at `~/.emailbrain/mail.db` by default (override with `--db` or `EMAILBRAIN_DB`).

---

## Getting your mail out (no passwords needed)

| Service | How |
|---|---|
| **Gmail** | [takeout.google.com](https://takeout.google.com) → Mail → get `.mbox` |
| **Outlook** | Export `.pst`, then `readpst -M -o out file.pst` → `.eml` files |
| **Apple Mail** | Mailbox → Export Mailbox… → `.mbox` |
| **Thunderbird / others** | Add account, then export folders as mbox/eml (ImportExportTools NG) |

More detail in [`references/export-guides.md`](references/export-guides.md).

---

## The local bot (optional)

```bash
# install Ollama from https://ollama.com, then:
ollama pull llama3.1
ollama pull nomic-embed-text

python3 scripts/emailbrain.py embed                 # one-time, resumable
python3 scripts/emailbrain.py ask "what did we promise Priya about budget?"
python3 scripts/emailbrain.py chat
python3 scripts/emailbrain.py serve
```

The bot first retrieves the best-matching emails, then answers **only** from them and cites ids. Small local models can still misread things — **verify with `show <id>`** before acting on money, dates, or commitments.

Use different models with `EMAILBRAIN_MODEL` and `EMAILBRAIN_EMBED_MODEL`.

---

## Use it as a Claude skill

Copy or clone this repo into your skills folder so Claude can search your mail for you:

```bash
# Claude Code (personal skills)
git clone https://github.com/Deadlift-alt/email-brain.git ~/.claude/skills/email-brain
```

For claude.ai / Claude desktop, zip the repo folder (or run `git archive -o email-brain.skill HEAD`) and upload it under **Settings → Capabilities → Skills**.

Then just say things like *"Have I already proposed a loyalty program to anyone?"* or *"We fixed this server billing problem before — how?"*.

---

## Privacy model

- One file on your disk (folder `700`, file `600`). Use full-disk encryption for real protection.
- `serve` only binds to loopback and rejects foreign `Host` headers (blocks DNS-rebinding from malicious web pages).
- Ollama calls are restricted to `127.0.0.1` / `localhost`.
- The bot is read-only: it cannot send, delete, or browse.
- Email text is treated as untrusted data, not instructions.
- `.gitignore` blocks `*.db`, `*.mbox`, `*.eml`, `*.pst` so you can't accidentally commit your mail.

Details and remote-access advice (VPN + SSH tunnel, never public tunnels): [`references/privacy-and-hardening.md`](references/privacy-and-hardening.md).

## Development

```bash
python3 -m unittest discover -s tests -v
```
CI runs the same tests on Linux/macOS/Windows with Python 3.9–3.12. See [`SECURITY.md`](SECURITY.md) for reporting issues. PRs welcome — please never include real email in issues or tests.

## License
MIT
