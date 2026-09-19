PYTHON ?= python3
SWIFTC ?= swiftc
LUA ?= lua
RUFF ?= ruff

.PHONY: bootstrap build test lint native-test loopback clean

bootstrap:
	$(PYTHON) -m venv .venv
	.venv/bin/python -m pip install -e .

build: build/bridge

build/bridge: native/bridge.swift
	mkdir -p build/swift-module-cache
	$(SWIFTC) -module-cache-path build/swift-module-cache native/bridge.swift -o build/bridge

native-test: build
	build/bridge --self-test

test: native-test
	$(LUA) tests/check-profile.lua
	.venv/bin/python -m unittest discover -s tests -v

lint:
	$(RUFF) check src tests

loopback: build
	build/bridge --loopback-self-test

clean:
	rm -rf build
