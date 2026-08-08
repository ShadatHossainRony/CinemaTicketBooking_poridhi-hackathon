.PHONY: up down logs migrate test smoke scenario-a clean help

help:
	@echo "CinemaSeat — common targets"
	@echo "  make up            bring the stack up (db + gateway + migrate + api + api2)"
	@echo "  make down          stop the stack (keeps the data volume)"
	@echo "  make logs          tail logs from api and migrate"
	@echo "  make migrate       run alembic upgrade head + seed in the api container"
	@echo "  make test          run pytest inside the api container"
	@echo "  make smoke         run the post-deploy smoke test against the local URL"
	@echo "  make scenario-a    fire 100 concurrent holds at one seat (REQ-38)"
	@echo "  make clean-clone   prove REQ-21 by cloning into /tmp and `compose up`"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs --tail=100 -f api migrate

migrate:
	docker compose run --rm migrate

test:
	docker compose exec api pytest -q --tb=short

smoke:
	./tests/smoke.sh

scenario-a:
	@./tests/scenario_a.sh

clean-clone:
	@./tests/clean_clone.sh