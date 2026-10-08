PREFIX ?= $(HOME)/.local
DESTDIR ?=
PYTHON ?= python3

.PHONY: help install uninstall check

help:
	@printf '%s\n' 'make install    Install to PREFIX (default: ~/.local)' 'make uninstall  Disable SSH hook and remove installed files' 'make check      Run syntax checks and isolated tests' 'DESTDIR stages packaging; staged operations do not edit login files.' 'Enable SSH separately: bellhop ssh enable'

install:
	$(PYTHON) scripts/install.py install --prefix "$(PREFIX)" --destdir "$(DESTDIR)"

uninstall:
	$(PYTHON) scripts/install.py uninstall --prefix "$(PREFIX)" --destdir "$(DESTDIR)"

check:
	$(PYTHON) -c "from pathlib import Path; compile(Path('bin/bellhop').read_bytes(), 'bin/bellhop', 'exec')"
	$(PYTHON) -m compileall -q bellhop scripts tests bin/bellhop
	bash -n share/ssh-hook.sh
	sh -n bellhop.tmux
	$(PYTHON) -m unittest discover -v
