# Schema

Main table `emails`: `id`, `message_id` (unique → dedupe), `thread_id` (root Message-ID; replies point to it), `subject`, `from_addr`, `from_name`, `to_addrs`, `cc_addrs` (comma-separated, lowercase), `date_utc` (ISO 8601, `...Z`), `direction` (`sent`/`received`), `body` (full text), `body_clean` (quoted replies removed; this is what's searched and embedded), `attachments` (file names only — contents are not stored), `source` (which export file it came from).

`fts` — FTS5 virtual table, `rowid` = `emails.id`; columns `subject, body, people`; Porter stemming. Query with `MATCH`, rank with `bm25(fts,5.0,1.0,2.0)` (subject weighted highest).

`notes(email_id, tag, note, created)` — the user's own annotations (what was fixed, what idea was sent).

`embeddings(email_id, model, vec)` — float32 array bytes from the local embedding model.

`meta(key, value)` — `me` = the user's addresses.

## Handy queries
```sql
-- everything I sent to a domain in 2025
SELECT date_utc, subject FROM emails WHERE direction='sent' AND to_addrs LIKE '%@client.com%' AND date_utc LIKE '2025%';
-- sent mail nobody replied to (rough "waiting on" list)
SELECT e.id, e.subject FROM emails e WHERE direction='sent'
 AND NOT EXISTS (SELECT 1 FROM emails r WHERE r.thread_id=e.thread_id AND r.direction='received' AND r.date_utc>e.date_utc);
```
