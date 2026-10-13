PROJ     := $(shell pwd)
PYTHON   ?= python3
COMPOSE  := docker compose -f docker/odoo19/compose.yml
GO_CACHE := $(HOME)/.cache/fd-go
GO       := docker run --rm -u $(shell id -u):$(shell id -g) \
            -v $(PROJ):/src -v $(GO_CACHE):/cache -w /src/runner \
            -e CGO_ENABLED=0 -e GOFLAGS=-buildvcs=false -e HOME=/cache \
            -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod \
            golang:1.26-alpine go

.PHONY: config build up logs test runner-test

config:
	$(COMPOSE) config --quiet

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d

logs:
	$(COMPOSE) logs -f

test:
	$(PYTHON) -m pytest odoo/tests -q

runner-test:
	mkdir -p $(GO_CACHE)
	$(GO) test ./...
