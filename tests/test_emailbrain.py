import email.message, mailbox, os, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "emailbrain.py"

def run(db, *args, check=True):
    r = subprocess.run([sys.executable, str(SCRIPT), "--db", db, *args], capture_output=True, text=True)
    if check: assert r.returncode == 0, r.stderr
    return r

def add(mb, mid, frm, to, sub, body, date, ref=None):
    m = email.message.EmailMessage()
    m["Message-ID"] = f"<{mid}>"; m["From"] = frm; m["To"] = to; m["Subject"] = sub; m["Date"] = date
    if ref: m["In-Reply-To"] = m["References"] = f"<{ref}>"
    m.set_content(body); mb.add(m)

class T(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory(); d = self.d.name
        self.db = os.path.join(d, "t.db"); self.mbox = os.path.join(d, "t.mbox")
        mb = mailbox.mbox(self.mbox)
        add(mb, "a1", "me@corp.com", "priya@client.com", "Proposal: loyalty program", "Idea: a points-based loyalty program to cut churn.", "Mon, 03 Mar 2025 10:00:00 +0000")
        add(mb, "a2", "priya@client.com", "me@corp.com", "Re: Proposal: loyalty program", "Budget is tight.\n\nOn Mon, Mar 3 me wrote:\n> points-based loyalty", "Tue, 04 Mar 2025 10:00:00 +0000", "a1")
        add(mb, "a3", "billing@vendor.com", "me@corp.com", "Invoice 4471 failed", "Payment failed for hosting.", "Wed, 05 Mar 2025 10:00:00 +0000")
        add(mb, "a1", "me@corp.com", "x@y.com", "dup", "dup", "Mon, 03 Mar 2025 10:00:00 +0000")
        mb.flush(); mb.close()
        run(self.db, "init", "--me", "me@corp.com")

    def tearDown(self): self.d.cleanup()

    def test_ingest_dedupes(self):
        self.assertIn("Added 3 new", run(self.db, "ingest", self.mbox).stdout)
        self.assertIn("Added 0 new", run(self.db, "ingest", self.mbox).stdout)

    def test_search_and_direction(self):
        run(self.db, "ingest", self.mbox)
        self.assertIn("loyalty", run(self.db, "search", "loyalty").stdout)
        out = run(self.db, "search", "invoice", "--direction", "sent").stdout
        self.assertIn("Nothing found", out)

    def test_quoted_text_not_indexed(self):
        run(self.db, "ingest", self.mbox)
        out = run(self.db, "search", "points-based", "--direction", "received").stdout
        self.assertIn("Nothing found", out)   # only appears in a quoted line of #2

    def test_similar_checks_sent_only(self):
        run(self.db, "ingest", self.mbox)
        out = run(self.db, "similar", "rewards scheme with loyalty points").stdout
        self.assertIn("SENT", out); self.assertNotIn("RECV", out)

    def test_thread_groups_reply(self):
        run(self.db, "ingest", self.mbox)
        out = run(self.db, "thread", "1").stdout
        self.assertIn("SENT", out); self.assertIn("RECV", out)

    def test_notes_and_tag_filter(self):
        run(self.db, "ingest", self.mbox)
        run(self.db, "note", "3", "--tag", "fixed", "card updated")
        self.assertIn("Invoice 4471", run(self.db, "search", "--tag", "fixed").stdout)

    def test_serve_refuses_public_bind(self):
        r = run(self.db, "serve", "--host", "0.0.0.0", check=False)
        self.assertNotEqual(r.returncode, 0); self.assertIn("non-loopback", r.stderr)

if __name__ == "__main__":
    unittest.main()
