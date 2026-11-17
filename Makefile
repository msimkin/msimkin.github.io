# univariate.org
#   make preview              local preview with drafts at http://localhost:8000 (rebuilds on save)
#                             (make preview host=0.0.0.0 also serves it to your phone on the same Wi-Fi)
#   make new name=my-post     start a draft in drafts/my-post.md
#   make publish name=my-post move it to posts/ and date it today (then commit and push)
#   make build                build the public site into _site/ (what GitHub builds on push)

VENV := .venv
RUN  := $(VENV)/bin/python tools/site.py

.PHONY: preview build new publish clean help
help:
	@sed -n '2,7p' Makefile | sed 's/^# //'

$(VENV)/.ok: requirements.txt
	@for p in /usr/bin/python3 python3.13 python3.12 python3.11 python3.10 python3; do \
	  command -v $$p >/dev/null 2>&1 || continue; \
	  $$p -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null || continue; \
	  rm -rf $(VENV); \
	  if $$p -m venv $(VENV) >/dev/null 2>&1 && $(VENV)/bin/pip install -q --disable-pip-version-check -r requirements.txt; then \
	    touch $@; echo "set up $(VENV) with $$p"; exit 0; fi; \
	done; echo "Could not set up a Python environment (need Python 3.9+ with venv)"; exit 1

preview: $(VENV)/.ok
	@$(RUN) serve $(if $(host),--host $(host),) $(if $(port),--port $(port),)

build: $(VENV)/.ok
	@$(RUN) build

new: $(VENV)/.ok
	@test -n "$(name)" || (echo 'usage: make new name=my-post [title="My post"]' && exit 1)
	@$(RUN) new "$(name)" $(if $(title),--title "$(title)",)

publish: $(VENV)/.ok
	@test -n "$(name)" || (echo 'usage: make publish name=my-post' && exit 1)
	@$(RUN) publish "$(name)"

clean:
	rm -rf _site _preview $(VENV)
