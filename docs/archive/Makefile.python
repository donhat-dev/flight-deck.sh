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

.PHONY: venv build dev serve service enable logs test pandoc fonts fonts-verify

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

FONT_DIR := backend/flightdeck/treasures/templates/fonts
# Google's css2 API subsets by User-Agent, not by a `subset=` query param (that
# param is ignored) -- an old/plain UA gets served bare .ttf. A modern desktop
# Chrome UA gets woff2, split into multiple @font-face blocks (one per script
# subset, e.g. /* vietnamese */, /* latin-ext */, /* latin */, in no fixed
# order), so each subset's src URL is taken from the block following its own
# comment rather than by position.
FONT_UA  := Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36

pandoc:                     ## fetch the pinned static pandoc into ~/.flightdeck/bin
	bash scripts/fetch-pandoc.sh

# Fetch BOTH the latin and the vietnamese subset of every artifact family.
#
# This target used to grep only the `/* vietnamese */` block, which is where the
# 2026-07-28 font bug came from: Google's vietnamese subset holds ONLY the
# Vietnamese-specific codepoints (113 glyphs, no 'A', no 'a', no '0'). Declared
# without a unicode-range it claimed the whole family, so every Latin character
# in every artifact silently fell back to the system font and Space Grotesk was
# never actually rendered. tokens.css now declares one face per subset WITH its
# unicode-range, so both files per family are required.
#
# latin-ext / cyrillic / greek are deliberately skipped: the artifact `language`
# field only allows en|vi.
fonts:                      ## fetch latin + vietnamese subsets of the artifact fonts
	mkdir -p $(FONT_DIR)
	@set -e; \
	fetch() { \
	  fam="$$1"; slug="$$2"; query="$$3"; \
	  curl -sSL -A "$(FONT_UA)" "https://fonts.googleapis.com/css2?family=$$query&display=swap" \
	    -o /tmp/flightdeck-$$slug.css; \
	  for sub in latin vietnamese; do \
	    url=$$(grep -A 8 "/\* $$sub \*/" /tmp/flightdeck-$$slug.css \
	           | grep -o 'https://[^)]*\.woff2' | head -1); \
	    if [ -z "$$url" ]; then echo "FAIL: no $$sub subset for $$fam" >&2; exit 1; fi; \
	    curl -sSL -o $(FONT_DIR)/$$slug-$$sub.woff2 "$$url"; \
	  done; \
	}; \
	fetch "Space Grotesk"    space-grotesk    'Space+Grotesk:wght@400..700'; \
	fetch "JetBrains Mono"   jetbrains-mono   'JetBrains+Mono:wght@400..700'; \
	fetch "Playfair Display" playfair-display 'Playfair+Display:ital,wght@1,600'
	@$(MAKE) --no-print-directory fonts-verify

fonts-verify:               ## prove each family covers Latin AND Vietnamese
	@.venv/bin/python -c "import sys; sys.path.insert(0,'backend'); \
	from tests.test_treasures_render import _family_cmaps; \
	from fontTools.ttLib import TTFont; \
	import glob, os; \
	rows=[(os.path.basename(p), os.path.getsize(p)/1024, len(TTFont(p).getBestCmap())) for p in sorted(glob.glob('$(FONT_DIR)/*.woff2'))]; \
	[print('  %-38s %6.1f KB  %4d glyphs' % r) for r in rows]; \
	print('  TOTAL %.1f KB' % sum(r[1] for r in rows))"
	cd backend && ../.venv/bin/python -m pytest tests/test_treasures_render.py -q -k font

# Satoshi — FlightDeck's primary typeface, built from the VIETNAMIZED "MJ Satoshi"
# TTFs in fonts/. Not fetched from Fontshare: that cut carries only 26 of 74
# Vietnamese precomposed characters, and session titles here are often Vietnamese.
# No network needed — the sources are in the repo.
satoshi:
	@mkdir -p frontend/src/fonts
	@.venv/bin/python -c "\
from fontTools.ttLib import TTFont; \
import pathlib; \
dest = pathlib.Path('frontend/src/fonts'); \
weights = {'Light': 300, 'Regular': 400, 'Medium': 500, 'Bold': 700, 'Black': 900}; \
[ (lambda f, out: (setattr(f, 'flavor', 'woff2'), f.save(out), print(f'  {out.name}')))\
   (TTFont(f'fonts/MJ Satoshi-{n}.ttf'), dest / f'satoshi-{w}.woff2') \
  for n, w in weights.items() ]"
	@.venv/bin/python -c "\
from fontTools.ttLib import TTFont; \
import glob; \
vn = 'ăâêôơưđáàảãạắằẳẵặấầẩẫậếềểễệốồổỗộớờởỡợứừửữự'; \
[ (lambda f, cm: (\
    __import__('sys').exit(f'{f}: ASCII {sum(1 for c in range(32,127) if c in cm)}/95') \
      if sum(1 for c in range(32,127) if c in cm) != 95 else \
    __import__('sys').exit(f'{f}: Vietnamese {sum(1 for ch in vn if ord(ch) in cm)}/{len(vn)}') \
      if sum(1 for ch in vn if ord(ch) in cm) != len(vn) else None)) \
  (f, TTFont(f).getBestCmap()) for f in sorted(glob.glob('frontend/src/fonts/satoshi-*.woff2')) ]; \
print('satoshi ok: 5 weights, ASCII 95/95, Vietnamese complete')"

.PHONY: satoshi

# Symlink the agent CLI into ~/.local/bin. The shim resolves the repo through the
# symlink, so this works from any cwd afterwards.
cli:
	mkdir -p $(HOME)/.local/bin
	ln -sf $(CURDIR)/bin/flightdeck $(HOME)/.local/bin/flightdeck
	@$(HOME)/.local/bin/flightdeck --version
