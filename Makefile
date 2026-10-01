PROJ        := $(shell pwd)
GO_CACHE    := $(HOME)/.cache/fd-go
GO          := docker run --rm -u $(shell id -u):$(shell id -g) \
               -v $(PROJ):/src -v $(GO_CACHE):/cache -w /src/core \
               -e CGO_ENABLED=0 -e GOFLAGS=-buildvcs=false -e HOME=/cache \
               -e GOCACHE=/cache/build -e GOMODCACHE=/cache/mod \
               golang:1.26-alpine go
GOTEST_ARGS ?= ./...
FD_VERSION  := $(shell git describe --always --dirty 2>/dev/null || echo dev)

.PHONY: go-build go-test build test

go-build:
	mkdir -p $(GO_CACHE)
	$(GO) build -trimpath -ldflags "-s -w -X main.version=$(FD_VERSION)" -o bin/fd ./cmd/fd

go-test:
	mkdir -p $(GO_CACHE)
	$(GO) test $(GOTEST_ARGS)

build: go-build
test: go-test
