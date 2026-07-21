# FlightDeck host runner. See docs/host-stack-migration.md.
# Runs directly on the host (no app container). Dev = hot reload; prod = systemd.
# Monorepo layout: Python package lives in backend/flightdeck, frontend at repo root.
PROJ     := $(shell pwd)
NODE_BIN := /home/nathando/.nvm/versions/node/v22.23.0/bin
PORT     ?= 8010

# Workspace root for the Diff tool + NAKIVO dependency graph. The package now
# sits one level deeper (backend/flightdeck), so the code's relative-path
# fallbacks would resolve to the wrong dir -- pin them explicitly here.
WORKSPACE   := /home/nathando/Documents/Projects
GRAPH_FILE  := /home/nathando/Documents/Projects/nakivo-graph/nakivo-graph.json
RUN_ENV     := FLIGHTDECK_WORKSPACE=$(WORKSPACE) TOKEN_AUDIT_GRAPH_FILE=$(GRAPH_FILE)

.PHONY: venv build dev serve service enable logs test

venv:                       ## create .venv (repo root) + install python deps
	python3 -m venv .venv
	.venv/bin/pip install -q --upgrade pip
	.venv/bin/pip install -q -r backend/requirements.txt

build:                      ## build the frontend into frontend/dist
	npm --prefix frontend ci
	npm --prefix frontend run build

dev:                        ## fast local dev: backend --reload + vite HMR (Ctrl-C stops both)
	./demo.sh

serve:                      ## prod-style single process: serve built dist, no reload
	cd backend && set -a && [ -f ../.env ] && . ../.env; set +a; \
	  PATH="$(NODE_BIN):$$PATH" $(RUN_ENV) ../.venv/bin/uvicorn flightdeck.server:app \
	  --host 127.0.0.1 --port $(PORT)

service:                    ## install the systemd --user unit (inert until `make enable`)
	mkdir -p $(HOME)/.config/systemd/user
	sed "s#__PROJ__#$(PROJ)#g; s#__NODE_BIN__#$(NODE_BIN)#g" deploy/flightdeck.service \
	  > $(HOME)/.config/systemd/user/flightdeck.service
	systemctl --user daemon-reload
	@echo "installed. NOTE :8010 clashes with the Docker container — stop it first."
	@echo "then: make enable"

enable:                     ## enable + start the durable service (survives reboot)
	loginctl enable-linger $(USER)
	systemctl --user enable --now flightdeck

logs:                       ## follow the durable service logs
	journalctl --user -u flightdeck -f

test:                       ## run the test suite in the venv
	cd backend && ../.venv/bin/python -m pytest tests -q
