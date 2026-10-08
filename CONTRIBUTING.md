# Contributing

Small fixes and useful polish are welcome. Please keep the core limited to
Python's standard library and tmux, with SSH and plugin setup optional.

## Getting started

Use Python 3.11+, tmux 3.2+, Make, and Bash. Install Zsh too if you're working on
its login integration. No virtual environment or pip installation is needed.
Run `./bin/bellhop` directly from a checkout.

```sh
make check
```

Tests use temporary homes, private tmux sockets, and pseudo-terminals. They
must leave existing sessions and real startup files alone. Some sandboxes
block the socket or terminal access these tests need.

Add a regression test for behavior changes and run the full suite before
submitting a patch. Terminal restoration, session identity, quoting, and
shared-client behavior deserve particular care.

## Where things live

- `bellhop/tmux.py`: session commands and client selection.
- `bellhop/ui.py`: the lobby and prompts.
- `bellhop/integration.py`: SSH hook setup and removal.
- `scripts/install.py`, `Makefile`: installation and checks.
- `bellhop.tmux`: popup binding and TPM entry point.
- `tests/`: unit and terminal integration tests.

Update the README for user-visible changes. Keep credentials and
machine-specific configuration out of commits.
