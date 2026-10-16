# Security policy

email-brain handles highly sensitive data, so the design rule is: **local only, no exceptions.**

Please report privately (GitHub → Security → "Report a vulnerability") for anything that could:
- expose the database or web UI beyond loopback,
- let a web page or email trigger actions or read data (DNS rebinding, CSRF, prompt injection),
- make the tool contact a non-local host.

Do **not** attach real emails, databases, or exports to issues. Use the synthetic data helpers in `tests/`.

PRs that add a network listener on a non-loopback interface, cloud LLM calls, or telemetry will not be merged.
