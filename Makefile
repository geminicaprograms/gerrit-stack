SHELL := /bin/bash
SCRIPTS := $(wildcard scripts/*.sh) $(wildcard scripts/lib/*.sh) $(wildcard demo/*.sh) $(wildcard demo/skeleton/tools/*.sh)

.PHONY: all validate lint test test-py check eval bench bench-unprompted bench-split bench-rework bench-review demo-up demo-seed demo-reset demo-warm demo-down

all: check

validate:
	claude plugin validate ./ --strict

lint:
	shellcheck -x -S warning $(SCRIPTS) tests/helpers.bash
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

BENCH_ARGS := --arms with,mcp-only,without --push-to http://localhost:8080/a/demo-plugin -j 3

bench-unprompted:
	python3 evals/run.py --eval-dir evals/bench-unprompted $(BENCH_ARGS)

bench-split:
	python3 evals/run.py --eval-dir evals/bench-split $(BENCH_ARGS)

bench-rework:
	python3 evals/run.py --eval-dir evals/bench-rework --variants natural,nudged $(BENCH_ARGS)

bench-review:
	python3 evals/run.py --eval-dir evals/bench-review $(BENCH_ARGS)

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
