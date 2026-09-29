SHELL := /bin/bash
SCRIPTS := $(wildcard scripts/*.sh) $(wildcard scripts/lib/*.sh) $(wildcard demo/*.sh) $(wildcard demo/skeleton/tools/*.sh)

.PHONY: all validate lint test test-py check eval bench demo-up demo-seed demo-reset demo-warm demo-down

all: check

validate:
	claude plugin validate ./ --strict

lint:
	shellcheck -x $(SCRIPTS) tests/helpers.bash
	python3 -m py_compile scripts/gerrit-rest.py $(wildcard evals/*.py) $(wildcard evals/metrics/*.py) $(wildcard evals/fixtures/*.py)

test:
	bats --print-output-on-failure tests/

test-py:
	python3 -m unittest discover -s tests -p 'test_*.py' -v

check: validate lint test test-py

eval:
	python3 evals/run.py --runs 2 --threshold 0.8

bench:
	python3 evals/run.py --bench --ablation --runs 3

demo-up:
	docker compose -f demo/docker-compose.yml up -d

demo-down:
	docker compose -f demo/docker-compose.yml down

demo-seed:
	bash demo/seed.sh

demo-reset:
	bash demo/reset.sh

demo-warm:
	bash demo/warm-bazel.sh
