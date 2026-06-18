# nbsnap top-level Makefile.
#
# Targets are organised in two groups:
#   * stack-*    NetBox test stack lifecycle (per INFRA-03c).
#   * lint, test convenience wrappers around ruff, mypy, pytest.

# NetBox image tag, see tests/fixtures/README.md for the pinning
# policy. Change in a dedicated PR that re-runs the integration
# suite.
NETBOX_DOCKER_TAG ?= v4.6.3
export NETBOX_DOCKER_TAG

# Compose project names keep concurrent dev runs from colliding.
SOURCE_PROJECT := nbsnap-source
DEST_PROJECT   := nbsnap-dest

SOURCE_COMPOSE := docker compose -f tests/fixtures/source/docker-compose.yml --project-name $(SOURCE_PROJECT)
DEST_COMPOSE   := docker compose -f tests/fixtures/dest/docker-compose.yml   --project-name $(DEST_PROJECT)

# Tokens come from the env files, the seeder needs them at runtime.
SOURCE_TOKEN ?= 0123456789abcdef0123456789abcdef01234567
DEST_TOKEN   ?= abcdef0123456789abcdef0123456789abcdef01

.PHONY: help setup stack-up stack-down stack-wait stack-bootstrap stack-seed stack-status \
        lint test test-unit test-integration

help:
	@printf 'nbsnap make targets\n'
	@printf '  setup            idempotent venv + dev install via scripts/setup-dev.sh\n'
	@printf '  stack-up         bring both NetBox test stacks up, detached\n'
	@printf '  stack-down       tear both stacks down, including volumes\n'
	@printf '  stack-wait       poll /login/ on both, fail after 300s\n'
	@printf '  stack-bootstrap  create a v1 admin API token on each stack\n'
	@printf '  stack-seed       apply tests/fixtures/seed/*.json to both stacks\n'
	@printf '  stack-status     docker compose ps for both stacks\n'
	@printf '  lint          ruff check, ruff format --check, mypy --strict\n'
	@printf '  test-unit     pytest tests/unit\n'
	@printf '  test-integration  pytest tests/integration (stacks must be up)\n'
	@printf '  test          unit + integration\n'

setup:
	./scripts/setup-dev.sh

stack-up:
	$(SOURCE_COMPOSE) up -d
	$(DEST_COMPOSE) up -d

stack-down:
	-$(SOURCE_COMPOSE) down -v
	-$(DEST_COMPOSE) down -v

# Cap the wait at 300s and poll both endpoints every 5s. A cold
# netbox-docker stack takes 2 to 4 minutes to finish migrations
# and bind nginx, so the 90s budget the prior version used was too
# tight for a fresh runner. Exit non-zero on timeout so make's
# error propagation kicks in. We probe `/login/` because it is the
# one endpoint NetBox serves without authentication. The
# subsequent `stack-bootstrap` step seeds the API token; until
# then any /api/* path would answer 403 even on a healthy server.
# `curl -w` writes "000" on a failed connect, which is the natural
# sentinel for "not ready yet".
stack-wait:
	@bash -c ' \
	  deadline=$$(( $$(date +%s) + 300 )); \
	  src=000; dst=000; \
	  while [ $$(date +%s) -lt $$deadline ]; do \
	    src=$$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
	      http://localhost:8080/login/); \
	    dst=$$(curl -s -o /dev/null -w "%{http_code}" --max-time 5 \
	      http://localhost:8081/login/); \
	    if [ "$$src" = "200" ] && [ "$$dst" = "200" ]; then \
	      echo "both stacks ready"; exit 0; \
	    fi; \
	    echo "waiting, source=$$src dest=$$dst"; \
	    sleep 5; \
	  done; \
	  echo "timeout, source=$$src dest=$$dst"; exit 1 \
	'

# Create a v1 admin API token on each stack so the seeder and the
# integration tests can authenticate with the simple legacy
# `Authorization: Token <40-char>` header. v1 tokens skip the
# pepper/key plumbing that the netbox-docker v2 path requires and
# match the header format the rest of the test code already uses.
#
# Why the `Token.objects.create(..., token=...)` shape instead of
# `update_or_create(..., plaintext=...)`: NetBox's Token.save() runs
#
#     if self._state.adding and self.token is None:
#         self.token = self.generate()
#
# which silently overwrites a manually populated `plaintext` field
# with a fresh random value, because `plaintext` is a DB field and
# the `token` *property* tracks the kwarg passed via `__init__`.
# Passing `token=...` routes through the property setter, which
# writes `self.plaintext` AND leaves `self._token` non-None so
# `save()` keeps the value we asked for.
#
# Idempotency: delete any existing v1 token for the user first, so
# re-running this target replaces stale rows. Test stacks only, so
# wiping a token row each invocation is fine.
#
# The Python command is single-line on purpose; multi-line via
# shell backslash continuation preserves Make's recipe indentation
# and Python then raises `IndentationError: unexpected indent`.
stack-bootstrap:
	@printf 'creating v1 admin token on source stack\n'
	@$(SOURCE_COMPOSE) exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "from users.models import User, Token; u = User.objects.get(username='admin'); Token.objects.filter(user=u, version=1).delete(); t = Token.objects.create(user=u, version=1, token='$(SOURCE_TOKEN)'); print('source token id', t.pk, 'plaintext', t.plaintext)"
	@printf 'creating v1 admin token on destination stack\n'
	@$(DEST_COMPOSE) exec -T netbox /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py shell -c "from users.models import User, Token; u = User.objects.get(username='admin'); Token.objects.filter(user=u, version=1).delete(); t = Token.objects.create(user=u, version=1, token='$(DEST_TOKEN)'); print('dest token id', t.pk, 'plaintext', t.plaintext)"

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
