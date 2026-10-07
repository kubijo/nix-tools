# Agent instructions

## Shell commands and scripts

- Never use heredocs to create or modify code, including for Python, shell scripts, file creation, or tests.
- Prefer existing commands and repository scripts.
- Do not evade this rule with multiline `python -c`, `bash -c`, or piped inline scripts.
- Before running a long-running or heavy command, show the exact command and wait for the user to choose whether you run
  it or they do. Silence is not approval.
- Do not commit or push unless explicitly requested.
