#!/usr/bin/env python3
"""emailbrain - a private, local-only email memory.

Everything lives in one SQLite file on this machine. Core features use only the
Python standard library. The optional local LLM ("the bot") talks to Ollama on
127.0.0.1 and nothing else. No cloud calls, no telemetry.

Commands: init, ingest, search, show, thread, similar, note, stats,
          embed, ask, chat, serve
"""
import argparse, array, email, email.policy, email.utils, hashlib, html, json
import mailbox, math, os, re, sqlite3, sys, textwrap, urllib.request
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_DB = Path(os.environ.get("EMAILBRAIN_DB", Path.home() / ".emailbrain" / "mail.db"))
OLLAMA = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
CHAT_MODEL = os.environ.get("EMAILBRAIN_MODEL", "llama3.1")
EMBED_MODEL = os.environ.get("EMAILBRAIN_EMBED_MODEL", "nomic-embed-text")

SCHEMA = """
CREATE TABLE IF NOT EXISTS emails(
  id INTEGER PRIMARY KEY,
  message_id TEXT UNIQUE,
  thread_id TEXT, subject TEXT,
  from_addr TEXT, from_name TEXT, to_addrs TEXT, cc_addrs TEXT,
  date_utc TEXT, direction TEXT,          -- 'sent' | 'received'
  body TEXT, body_clean TEXT,             -- body_clean = without quoted replies
  attachments TEXT, source TEXT);
CREATE INDEX IF NOT EXISTS ix_date ON emails(date_utc);
CREATE INDEX IF NOT EXISTS ix_thread ON emails(thread_id);
CREATE INDEX IF NOT EXISTS ix_from ON emails(from_addr);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  subject, body, people, tokenize='porter unicode61 remove_diacritics 2');
CREATE TABLE IF NOT EXISTS notes(
  id INTEGER PRIMARY KEY, email_id INTEGER, tag TEXT, note TEXT, created TEXT);
CREATE TABLE IF NOT EXISTS embeddings(email_id INTEGER PRIMARY KEY, model TEXT, vec BLOB);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
"""

STOP = set("""a an and are as at be but by for from has have i if in into is it its me my of on or our
so that the their them then there these they this to us was we were what when where which who will with
would you your hi hello dear thanks thank regards best please just can could should about also not no
yes re fwd fw""".split())

# ---------------------------------------------------------------- db helpers
def connect(path=None):
    p = Path(path or DEFAULT_DB)
    if not p.exists():
        sys.exit(f"No database at {p}. Run:  emailbrain.py init --me you@example.com")
    c = sqlite3.connect(p)
    c.row_factory = sqlite3.Row
    return c

def meta_get(c, k, d=""):
    r = c.execute("SELECT value FROM meta WHERE key=?", (k,)).fetchone()
    return r[0] if r else d

def cmd_init(a):
    p = Path(a.db or DEFAULT_DB)
    p.parent.mkdir(parents=True, exist_ok=True)
    try: os.chmod(p.parent, 0o700)
    except OSError: pass
    c = sqlite3.connect(p); c.executescript(SCHEMA)
    me = ",".join(x.strip().lower() for x in a.me.split(",") if x.strip())
    c.execute("INSERT OR REPLACE INTO meta VALUES('me',?)", (me,))
    c.commit(); c.close()
    try: os.chmod(p, 0o600)
    except OSError: pass
    print(f"Created {p}\nYour addresses (used to tell sent from received): {me}")

# ---------------------------------------------------------------- parsing
def strip_html(s):
    s = re.sub(r"(?is)<(script|style).*?</\1>", " ", s)
    s = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", s)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", s)))

def body_of(msg):
    plain, htm, atts = [], [], []
    for part in (msg.walk() if msg.is_multipart() else [msg]):
        if part.is_multipart(): continue
        fn = part.get_filename()
        if fn: atts.append(fn); continue
        ct = part.get_content_type()
        try: txt = part.get_content()
        except Exception:
            try: txt = part.get_payload(decode=True).decode("utf-8", "replace")
            except Exception: continue
        if not isinstance(txt, str): continue
        if ct == "text/plain": plain.append(txt)
        elif ct == "text/html": htm.append(txt)
    body = "\n".join(plain) or strip_html("\n".join(htm))
    return body.strip(), atts

QUOTE_HDR = re.compile(r"^(On .{5,200} wrote:|-{2,}\s*Original Message\s*-{2,}|From:\s.+Sent:\s.+)$", re.I)
def remove_quotes(body):
    out = []
    for ln in body.splitlines():
        if QUOTE_HDR.match(ln.strip()): break
        if ln.lstrip().startswith(">"): continue
        out.append(ln)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

def norm_subject(s):
    return re.sub(r"^\s*((re|fwd?|aw|sv)\s*:\s*)+", "", s or "", flags=re.I).strip().lower()

def addrs(msg, h):
    vals = msg.get_all(h) or []
    return [a.lower() for _, a in email.utils.getaddresses([str(v) for v in vals]) if a]

def parse_message(msg, me, source):
    mid = (msg.get("Message-ID") or "").strip().strip("<>")
    sub = str(msg.get("Subject") or "").replace("\n", " ").strip()
    name, frm = email.utils.parseaddr(str(msg.get("From") or ""))
    frm = frm.lower()
    try: d = email.utils.parsedate_to_datetime(str(msg.get("Date")))
    except Exception: d = None
    if d is None: d = datetime(1970, 1, 1, tzinfo=timezone.utc)
    if d.tzinfo is None: d = d.replace(tzinfo=timezone.utc)
    date = d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    body, atts = body_of(msg)
    clean = remove_quotes(body)
    refs = re.findall(r"<([^>]+)>", str(msg.get("References") or "") + " " + str(msg.get("In-Reply-To") or ""))
    if not mid:
        mid = "synthetic:" + hashlib.sha1(f"{frm}{date}{sub}{body[:200]}".encode("utf-8", "ignore")).hexdigest()
    thread = refs[0] if refs else mid   # replies point at the root; a root is its own thread
    return dict(message_id=mid, thread_id=thread, subject=sub, from_addr=frm, from_name=name,
                to_addrs=", ".join(addrs(msg, "To")), cc_addrs=", ".join(addrs(msg, "Cc")),
                date_utc=date, direction="sent" if frm in me else "received",
                body=body, body_clean=clean, attachments=", ".join(atts), source=source)

def insert(c, r):
    cur = c.execute("""INSERT OR IGNORE INTO emails(message_id,thread_id,subject,from_addr,from_name,
      to_addrs,cc_addrs,date_utc,direction,body,body_clean,attachments,source)
      VALUES(:message_id,:thread_id,:subject,:from_addr,:from_name,:to_addrs,:cc_addrs,:date_utc,
      :direction,:body,:body_clean,:attachments,:source)""", r)
    if cur.rowcount == 0: return False
    people = f"{r['from_name']} {r['from_addr']} {r['to_addrs']} {r['cc_addrs']}"
    c.execute("INSERT INTO fts(rowid,subject,body,people) VALUES(?,?,?,?)",
              (cur.lastrowid, r["subject"], r["body_clean"] or r["body"], people))
    return True

def iter_messages(path):
    p = Path(path)
    if p.is_file() and p.suffix.lower() == ".mbox":
        for m in mailbox.mbox(str(p), factory=lambda f: email.message_from_binary_file(f, policy=email.policy.default)):
            yield m, p.name
    elif p.is_file() and p.suffix.lower() == ".eml":
        with open(p, "rb") as f: yield email.message_from_binary_file(f, policy=email.policy.default), p.name
    elif p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file() and f.suffix.lower() in (".mbox", ".eml"):
                yield from iter_messages(f)
            elif f.is_file() and f.parent.name in ("cur", "new"):   # Maildir
                with open(f, "rb") as fh: yield email.message_from_binary_file(fh, policy=email.policy.default), str(f.parent.parent.name)
    else:
        sys.exit(f"Don't know how to read {p}. Give me a .mbox, .eml, or a folder of them.")

def cmd_ingest(a):
    c = connect(a.db); me = set(filter(None, meta_get(c, "me").split(",")))
    new = dup = bad = 0
    for msg, src in iter_messages(a.path):
        try:
            if insert(c, parse_message(msg, me, src)): new += 1
            else: dup += 1
        except Exception as e:
            bad += 1; print(f"  skipped one message: {e}", file=sys.stderr)
        if (new + dup) % 500 == 0: c.commit()
    c.commit()
    print(f"Added {new} new emails, skipped {dup} already stored, {bad} unreadable.")

# ---------------------------------------------------------------- search
def fts_query(q, mode="and"):
    toks = re.findall(r"[\w']+", q.lower())
    toks = [t for t in toks if t not in STOP and len(t) > 1] or toks
    if not toks: return ""
    return (" OR " if mode == "or" else " ").join(f'"{t}"' + ("*" if len(t) > 3 else "") for t in toks)

def search(c, q, limit=10, frm=None, to=None, since=None, until=None, direction=None, tag=None, mode="and"):
    where, args = [], []
    if frm: where.append("e.from_addr LIKE ?"); args.append(f"%{frm.lower()}%")
    if to: where.append("(e.to_addrs LIKE ? OR e.cc_addrs LIKE ?)"); args += [f"%{to.lower()}%"] * 2
    if since: where.append("e.date_utc >= ?"); args.append(since)
    if until: where.append("e.date_utc <= ?"); args.append(until + "T23:59:59Z" if len(until) == 10 else until)
    if direction: where.append("e.direction = ?"); args.append(direction)
    if tag: where.append("e.id IN (SELECT email_id FROM notes WHERE tag = ?)"); args.append(tag)
    w = (" AND " + " AND ".join(where)) if where else ""
    fq = fts_query(q, mode) if q else ""
    if fq:
        sql = f"""SELECT e.*, snippet(fts,1,'[',']','…',24) AS snip, bm25(fts,5.0,1.0,2.0) AS score
                  FROM fts JOIN emails e ON e.id = fts.rowid WHERE fts MATCH ?{w}
                  ORDER BY score LIMIT ?"""
        try: return c.execute(sql, [fq, *args, limit]).fetchall()
        except sqlite3.OperationalError: pass
    sql = f"SELECT e.*, substr(e.body_clean,1,200) AS snip, 0 AS score FROM emails e WHERE 1=1{w} ORDER BY date_utc DESC LIMIT ?"
    return c.execute(sql, [*args, limit]).fetchall()

def fmt_row(r, full=False):
    arrow = "→ SENT" if r["direction"] == "sent" else "← RECV"
    head = f"#{r['id']:<6} {r['date_utc'][:10]}  {arrow}  {r['from_name'] or r['from_addr']}  ⟶  {r['to_addrs'][:60]}"
    out = [head, f"        Subject: {r['subject']}"]
    if full: out.append("\n" + textwrap.indent(r["body_clean"] or r["body"], "        "))
    else: out.append("        " + re.sub(r"\s+", " ", r["snip"] or "")[:240])
    return "\n".join(out)

def cmd_search(a):
    c = connect(a.db)
    rows = search(c, " ".join(a.query), a.limit, a.frm, a.to, a.since, a.until, a.direction, a.tag, a.mode)
    if a.semantic: rows = hybrid(c, " ".join(a.query), rows, a.limit)
    if a.json: print(json.dumps([dict(r) for r in rows], indent=1, default=str)); return
    if not rows: print("Nothing found. Try fewer words, --mode or, or --semantic."); return
    for r in rows: print(fmt_row(r), "\n")

def cmd_show(a):
    c = connect(a.db); r = c.execute("SELECT * FROM emails WHERE id=?", (a.id,)).fetchone()
    if not r: sys.exit("No such email id.")
    print(fmt_row({**dict(r), "snip": ""}, full=True))
    if r["attachments"]: print("        Attachments:", r["attachments"])
    for n in c.execute("SELECT * FROM notes WHERE email_id=?", (a.id,)): print(f"        NOTE [{n['tag']}] {n['note']}  ({n['created'][:10]})")

def cmd_thread(a):
    c = connect(a.db); r = c.execute("SELECT thread_id FROM emails WHERE id=?", (a.id,)).fetchone()
    if not r: sys.exit("No such email id.")
    for x in c.execute("SELECT *, substr(body_clean,1,200) AS snip FROM emails WHERE thread_id=? ORDER BY date_utc", (r[0],)):
        print(fmt_row(x, full=a.full), "\n")

def cmd_similar(a):
    """Have I already said / sent something like this? Searches SENT mail by default."""
    c = connect(a.db); text = " ".join(a.text)
    words = [w for w in re.findall(r"[a-z][a-z']+", text.lower()) if w not in STOP and len(w) > 2]
    top = [w for w, _ in Counter(words).most_common(12)]
    rows = search(c, " ".join(top), a.limit, direction=None if a.any else "sent", mode="or")
    if not rows: print("No similar emails found — this looks new."); return
    print(f"Closest {'emails' if a.any else 'SENT emails'} to that idea:\n")
    for r in rows: print(fmt_row(r), "\n")

def cmd_note(a):
    c = connect(a.db)
    if not c.execute("SELECT 1 FROM emails WHERE id=?", (a.id,)).fetchone(): sys.exit("No such email id.")
    c.execute("INSERT INTO notes(email_id,tag,note,created) VALUES(?,?,?,?)",
              (a.id, a.tag, " ".join(a.text), datetime.now(timezone.utc).isoformat()))
    c.commit(); print(f"Saved note on #{a.id} [{a.tag}]")

def cmd_stats(a):
    c = connect(a.db)
    n, lo, hi = c.execute("SELECT count(*),min(date_utc),max(date_utc) FROM emails").fetchone()
    print(f"{n} emails  ({(lo or '')[:10]} → {(hi or '')[:10]})   DB: {a.db or DEFAULT_DB}")
    for r in c.execute("SELECT direction,count(*) n FROM emails GROUP BY 1"): print(f"  {r['direction']}: {r['n']}")
    print("  embedded:", c.execute("SELECT count(*) FROM embeddings").fetchone()[0], "| notes:", c.execute("SELECT count(*) FROM notes").fetchone()[0])
    print("  top correspondents:")
    for r in c.execute("SELECT from_addr,count(*) n FROM emails WHERE direction='received' GROUP BY 1 ORDER BY n DESC LIMIT 5"):
        print(f"    {r['n']:>5}  {r['from_addr']}")

# ---------------------------------------------------------------- local LLM (Ollama, loopback only)
def ollama(path, payload, timeout=300):
    if not re.match(r"^https?://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$", OLLAMA):
        sys.exit(f"Refusing to talk to non-local host {OLLAMA}. This tool is local-only.")
    req = urllib.request.Request(OLLAMA + path, json.dumps(payload).encode(), {"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: return json.load(r)
    except Exception as e:
        sys.exit(f"Could not reach local Ollama at {OLLAMA} ({e}).\n"
                 f"Install from ollama.com, then:  ollama pull {CHAT_MODEL}  &&  ollama pull {EMBED_MODEL}")

def embed_text(t):
    return ollama("/api/embeddings", {"model": EMBED_MODEL, "prompt": t[:4000]})["embedding"]

def cmd_embed(a):
    c = connect(a.db); done = 0
    rows = c.execute("SELECT id,subject,body_clean FROM emails WHERE id NOT IN (SELECT email_id FROM embeddings)").fetchall()
    for r in rows:
        v = embed_text(f"{r['subject']}\n{r['body_clean']}")
        c.execute("INSERT OR REPLACE INTO embeddings VALUES(?,?,?)", (r["id"], EMBED_MODEL, array.array("f", v).tobytes()))
        done += 1
        if done % 50 == 0: c.commit(); print(f"  {done}/{len(rows)}")
    c.commit(); print(f"Embedded {done} emails (meaning-based search enabled).")

def cos(a, b):
    d = sum(x * y for x, y in zip(a, b)); na = math.sqrt(sum(x * x for x in a)); nb = math.sqrt(sum(y * y for y in b))
    return d / (na * nb) if na and nb else 0.0

def hybrid(c, q, kw_rows, limit):
    """Merge keyword hits with meaning-based hits using reciprocal rank fusion."""
    if not c.execute("SELECT 1 FROM embeddings LIMIT 1").fetchone():
        print("(no embeddings yet — run `embed` for meaning-based search; showing keyword results)", file=sys.stderr); return kw_rows
    qv = embed_text(q)
    sims = []
    for r in c.execute("SELECT email_id, vec FROM embeddings"):
        v = array.array("f"); v.frombytes(r[1]); sims.append((cos(qv, v), r[0]))
    sims.sort(reverse=True)
    score = Counter()
    for rank, r in enumerate(kw_rows): score[r["id"]] += 1 / (60 + rank)
    for rank, (_, i) in enumerate(sims[:limit * 3]): score[i] += 1 / (60 + rank)
    ids = [i for i, _ in score.most_common(limit)]
    byid = {r["id"]: r for r in kw_rows}
    out = []
    for i in ids:
        out.append(byid.get(i) or c.execute("SELECT *, substr(body_clean,1,200) AS snip, 0 AS score FROM emails WHERE id=?", (i,)).fetchone())
    return out

SYSTEM = ("You are a private assistant answering questions about the user's own email archive. "
          "Use ONLY the email excerpts provided. Cite emails as [#id]. Say clearly when emails were SENT by the "
          "user vs RECEIVED. If the excerpts don't contain the answer, say you couldn't find it and suggest a "
          "better search — never guess or invent details. Be concise.")

def build_context(rows, budget=9000):
    parts, used = [], 0
    for r in rows:
        txt = (r["body_clean"] or r["body"] or "")[:1500]
        chunk = f"[#{r['id']}] {r['date_utc'][:10]} {r['direction'].upper()} from {r['from_addr']} to {r['to_addrs'][:80]}\nSubject: {r['subject']}\n{txt}\n"
        if used + len(chunk) > budget: break
        parts.append(chunk); used += len(chunk)
    return "\n---\n".join(parts)

def answer(c, question, k=8, history=None):
    rows = search(c, question, k, mode="or")
    rows = hybrid(c, question, rows, k) if c.execute("SELECT 1 FROM embeddings LIMIT 1").fetchone() else rows
    msgs = [{"role": "system", "content": SYSTEM}, *(history or []),
            {"role": "user", "content": f"EMAIL EXCERPTS:\n{build_context(rows)}\n\nQUESTION: {question}"}]
    out = ollama("/api/chat", {"model": CHAT_MODEL, "messages": msgs, "stream": False})["message"]["content"]
    return out, rows

def cmd_ask(a):
    c = connect(a.db); out, rows = answer(c, " ".join(a.question), a.k)
    print(out, "\n\nSources:", ", ".join(f"#{r['id']}" for r in rows))

def cmd_chat(a):
    c = connect(a.db); hist = []
    print(f"Local email bot ({CHAT_MODEL}). Everything stays on this machine. Ctrl-C or 'exit' to quit.\n")
    while True:
        try: q = input("you> ").strip()
        except (EOFError, KeyboardInterrupt): print(); break
        if q.lower() in ("exit", "quit"): break
        if not q: continue
        out, rows = answer(c, q, a.k, hist[-6:])
        print("\nbot>", out, "\n     sources:", ", ".join(f"#{r['id']}" for r in rows), "\n")
        hist += [{"role": "user", "content": q}, {"role": "assistant", "content": out}]

# ---------------------------------------------------------------- local web UI (loopback only)
PAGE = """<!doctype html><meta charset=utf-8><title>Email Brain (local)</title>
<style>body{font:15px system-ui;max-width:860px;margin:2rem auto;padding:0 1rem;color:#222}
input{width:78%;padding:.6rem;font-size:1rem}button{padding:.6rem 1rem}
.r{border-bottom:1px solid #ddd;padding:.6rem 0}.m{color:#666;font-size:.85rem}
.b{background:#f3f6ff;padding:1rem;border-radius:8px;white-space:pre-wrap;margin:1rem 0}
.tag{font-size:.75rem;padding:1px 6px;border-radius:4px;background:#eee}</style>
<h2>📬 Email Brain <span class=tag>local only · 127.0.0.1</span></h2>
<input id=q placeholder="Search, or ask a question about your mail…" autofocus>
<button onclick=go('search')>Search</button> <button onclick=go('ask')>Ask bot</button>
<div id=out></div>
<script>
const esc=s=>(s||'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
q.addEventListener('keydown',e=>{if(e.key==='Enter')go('search')});
async function go(m){out.innerHTML='…';
 const r=await fetch('/api/'+m+'?q='+encodeURIComponent(q.value));const d=await r.json();
 let h=d.answer?'<div class=b>'+esc(d.answer)+'</div>':'';
 h+=(d.results||[]).map(x=>`<div class=r><b>#${x.id} ${esc(x.subject)}</b><div class=m>${x.date_utc.slice(0,10)} · ${x.direction} · ${esc(x.from_addr)} → ${esc(x.to_addrs).slice(0,80)}</div><div>${esc(x.snip)}</div></div>`).join('');
 out.innerHTML=h||'Nothing found.'}
</script>"""

def cmd_serve(a):
    from http.server import BaseHTTPRequestHandler, HTTPServer
    from urllib.parse import urlparse, parse_qs
    if a.host not in ("127.0.0.1", "localhost", "::1"):
        sys.exit("Refusing to bind to a non-loopback address. Email Brain is local-only by design.")
    ok_hosts = {f"127.0.0.1:{a.port}", f"localhost:{a.port}", f"[::1]:{a.port}"}
    dbp = a.db
    class H(BaseHTTPRequestHandler):
        def log_message(self, *x): pass
        def send(self, code, body, ctype="application/json"):
            self.send_response(code); self.send_header("Content-Type", ctype + "; charset=utf-8")
            self.send_header("Cache-Control", "no-store"); self.send_header("X-Frame-Options", "DENY"); self.end_headers()
            self.wfile.write(body if isinstance(body, bytes) else body.encode())
        def do_GET(self):
            if self.headers.get("Host", "") not in ok_hosts: return self.send(403, "bad host", "text/plain")  # blocks DNS-rebinding
            u = urlparse(self.path); q = parse_qs(u.query).get("q", [""])[0]
            if u.path == "/": return self.send(200, PAGE, "text/html")
            c = connect(dbp)
            if u.path == "/api/search":
                return self.send(200, json.dumps({"results": [dict(r) for r in search(c, q, 15)]}, default=str))
            if u.path == "/api/ask":
                try: out, rows = answer(c, q)
                except SystemExit as e: return self.send(200, json.dumps({"answer": str(e)}))
                return self.send(200, json.dumps({"answer": out, "results": [dict(r) for r in rows]}, default=str))
            self.send(404, "not found", "text/plain")
    print(f"Email Brain running at http://127.0.0.1:{a.port}  (local only — Ctrl-C to stop)")
    HTTPServer((a.host, a.port), H).serve_forever()

# ---------------------------------------------------------------- cli
def fix_output_encoding():
    """Windows pipes/consoles often use cp1252, which can't print arrows or accents.
    Use UTF-8 when output is piped, and never crash on an unprintable character."""
    for stream in (sys.stdout, sys.stderr):
        try:
            if stream.isatty(): stream.reconfigure(errors="replace")
            else: stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError): pass

def main():
    fix_output_encoding()
    p = argparse.ArgumentParser(prog="emailbrain", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--db", help=f"database path (default {DEFAULT_DB})")
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("init"); x.add_argument("--me", required=True, help="your address(es), comma separated"); x.set_defaults(f=cmd_init)
    x = s.add_parser("ingest"); x.add_argument("path"); x.set_defaults(f=cmd_ingest)
    x = s.add_parser("search"); x.add_argument("query", nargs="*")
    x.add_argument("--from", dest="frm"); x.add_argument("--to"); x.add_argument("--since"); x.add_argument("--until")
    x.add_argument("--direction", choices=["sent", "received"]); x.add_argument("--tag"); x.add_argument("--limit", type=int, default=10)
    x.add_argument("--mode", choices=["and", "or"], default="and"); x.add_argument("--semantic", action="store_true"); x.add_argument("--json", action="store_true"); x.set_defaults(f=cmd_search)
    x = s.add_parser("show"); x.add_argument("id", type=int); x.set_defaults(f=cmd_show)
    x = s.add_parser("thread"); x.add_argument("id", type=int); x.add_argument("--full", action="store_true"); x.set_defaults(f=cmd_thread)
    x = s.add_parser("similar"); x.add_argument("text", nargs="+"); x.add_argument("--any", action="store_true", help="include received mail"); x.add_argument("--limit", type=int, default=5); x.set_defaults(f=cmd_similar)
    x = s.add_parser("note"); x.add_argument("id", type=int); x.add_argument("text", nargs="+"); x.add_argument("--tag", default="note"); x.set_defaults(f=cmd_note)
    x = s.add_parser("stats"); x.set_defaults(f=cmd_stats)
    x = s.add_parser("embed"); x.set_defaults(f=cmd_embed)
    x = s.add_parser("ask"); x.add_argument("question", nargs="+"); x.add_argument("-k", type=int, default=8); x.set_defaults(f=cmd_ask)
    x = s.add_parser("chat"); x.add_argument("-k", type=int, default=8); x.set_defaults(f=cmd_chat)
    x = s.add_parser("serve"); x.add_argument("--host", default="127.0.0.1"); x.add_argument("--port", type=int, default=8765); x.set_defaults(f=cmd_serve)
    a = p.parse_args(); a.f(a)

if __name__ == "__main__":
    main()
