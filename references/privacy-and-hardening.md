# Privacy and hardening

**Threat model.** The goal is that no one except the person at this computer can read the archive. Main risks: disk theft, other local users, backup/sync services, malware, malicious web pages, and accidental exposure of the web UI.

**Do**
- Use full-disk encryption (FileVault / BitLocker / LUKS). It protects the DB at rest better than anything in the app.
- Keep DB perms `600` in a `700` folder (done by `init`; check with `ls -l ~/.emailbrain`).
- Exclude `~/.emailbrain` from cloud sync and from unencrypted backups.
- Keep Ollama bound to loopback (its default). Verify: `ss -ltnp | grep 11434` should show `127.0.0.1`, not `0.0.0.0`.
- Run `serve` only when needed; stop it afterwards.
- Want it fully air-gapped? Pull the Ollama models once, then disconnect; nothing else needs the internet.

**Don't**
- Bind to `0.0.0.0`, port-forward, or use public tunnels. The tool refuses this on purpose.
- Paste email contents into cloud chatbots as a "shortcut".
- Store IMAP passwords in the DB or scripts.

**Remote access (phone/another laptop):** put both devices on a personal VPN (WireGuard/Tailscale) and SSH-tunnel: `ssh -L 8765:127.0.0.1:8765 you@home-machine`, then open `http://127.0.0.1:8765` locally. The server still only listens on loopback.

**Shared/work mail:** check employer policy before exporting corporate mail to a personal machine.

**Prompt injection:** emails are untrusted text. The bot is read-only (it can't send, delete, or browse), which limits the damage, but still verify cited emails before acting on money, credentials, or links.
