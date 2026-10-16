# Getting your mail out (no passwords needed)

**Gmail** — takeout.google.com → "Deselect all" → tick *Mail* (optionally pick labels) → export once → download zip → unzip → you get `.mbox` file(s). Large mailboxes can take hours/days to be prepared.

**Outlook (desktop)** — File → Open & Export → Import/Export → Export to a file → Outlook Data File (.pst). Convert: `readpst -M -o outdir file.pst` (package `pst-utils` / `libpst`) → folder of `.eml`.

**Outlook.com / Microsoft 365** — easiest route: add the account to Thunderbird, then use the ImportExportTools NG add-on to export folders as mbox/eml.

**Apple Mail** — select mailbox → Mailbox menu → Export Mailbox… → folder containing `.mbox`.

**Yahoo / others** — add to Thunderbird (IMAP), let it sync, export as above.

Then: `emailbrain.py ingest <file-or-folder>`. Folders are scanned recursively for `.mbox`, `.eml`, and Maildir `cur/`/`new/`.

Notes: attachment *names* are indexed, contents are not. After a successful ingest and backup, the user can delete the export files if they want a single copy.
