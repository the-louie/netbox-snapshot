# nbsnap top-level Makefile.
#
# Targets are organised in two groups:
#   * stack-*    NetBox test stack lifecycle (per INFRA-03c).
#   * lint, test convenience wrappers around ruff, mypy, pytest.

# NetBox image tag, see tests/fixtures/README.md for the pinning
# policy. Change in a dedicated PR that re-runs the integration
# suite.
NETBOX_DOCKER_TAG ?= v4.6-3.4.1
export NETBOX_DOCKER_TAG

# Compose project names keep concurrent dev runs from colliding.
SOURCE_PROJECT := nbsnap-source
DEST_PROJECT   := nbsnap-dest

SOURCE_COMPOSE := docker compose -f tests/fixtures/source/docker-compose.yml --project-name $(SOURCE_PROJECT)
DEST_COMPOSE   := docker compose -f tests/fixtures/dest/docker-compose.yml   --project-name $(DEST_PROJECT)

# Tokens come from the env files, the seeder needs them at runtime.
SOURCE_TOKEN ?= 0123456789abcdef0123456789abcdef01234567
DEST_TOKEN   ?= abcdef0123456789abcdef0123456789abcdef01

.PHONY: help stack-up stack-down stack-wait stack-seed stack-status \
        lint test test-unit test-integration

help:
	@printf 'nbsnap make targets\n'
	@printf '  stack-up      bring both NetBox test stacks up, detached\n'
	@printf '  stack-down    tear both stacks down, including volumes\n'
	@printf '  stack-wait    poll /api/status/ on both, fail after 90s\n'
	@printf '  stack-seed    apply tests/fixtures/seed/*.json to both stacks\n'
	@printf '  stack-status  docker compose ps for both stacks\n'
	@printf '  lint          ruff check, ruff format --check, mypy --strict\n'
	@printf '  test-unit     pytest tests/unit\n'
	@printf '  test-integration  pytest tests/integration (stacks must be up)\n'
	@printf '  test          unit + integration\n'

stack-up:
	$(SOURCE_COMPOSE) up -d
	$(DEST_COMPOSE) up -d

stack-down:
	-$(SOURCE_COMPOSE) down -v
	-$(DEST_COMPOSE) down -v

# Cap the wait at 90s and poll both endpoints every 5s. Exit
# non-zero on timeout so make's error propagation kicks in.
stack-wait:
	@bash -c ' \
	  deadline=$$(( $$(date +%s) + 90 )); \
	  while [ $$(date +%s) -lt $$deadline ]; do \
	    src=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/api/status/ -H "Authorization: Token $(SOURCE_TOKEN)" || echo 000); \
	    dst=$$(curl -s -o /dev/null -w "%{http_code}" http://localhost:8081/api/status/ -H "Authorization: Token $(DEST_TOKEN)" || echo 000); \
	    if [ "$$src" = "200" ] && [ "$$dst" = "200" ]; then \
	      echo "both stacks ready"; exit 0; \
	    fi; \
	    sleep 5; \
	  done; \
	  echo "timeout, source=$$src dest=$$dst"; exit 1 \
	'

stack-seed:
	python3 tests/fixtures/seed.py --url http://localhost:8080 --token $(SOURCE_TOKEN) --dir tests/fixtures/seed
	python3 tests/fixtures/seed.py --url http://localhost:8081 --token $(DEST_TOKEN)   --dir tests/fixtures/seed

stack-status:
	-$(SOURCE_COMPOSE) ps
	-$(DEST_COMPOSE) ps

lint:
	ruff check .
	ruff format --check .
	mypy src/

test-unit:
	pytest tests/unit -q --strict-markers --strict-config

test-integration:
	pytest tests/integration -q --strict-markers

test: test-unit test-integration
