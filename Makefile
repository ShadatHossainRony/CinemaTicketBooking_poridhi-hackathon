.PHONY: up down logs migrate test smoke scenario-a clean build-frontend publish help

help:
	@echo "CinemaSeat — common targets"
	@echo "  make up                bring the stack up (db + gateway + migrate + api + api2 + frontend)"
	@echo "  make down              stop the stack (keeps the data volume)"
	@echo "  make logs              tail logs from api and migrate"
	@echo "  make migrate           run alembic upgrade head + seed in the api container"
	@echo "  make build-frontend    (re)build the SPA into ./frontend/dist/"
	@echo "  make publish           rsync ./frontend/dist/ → /var/www/cinemaseat (host Nginx)"
	@echo "  make test              run pytest inside the api container"
	@echo "  make smoke             run the post-deploy smoke test against the local URL"
	@echo "  make scenario-a        fire 100 concurrent holds at one seat (REQ-38)"
	@echo "  make clean-clone       prove REQ-21 by cloning into /tmp and `compose up`"

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs --tail=100 -f api migrate

migrate:
	docker compose run --rm migrate

build-frontend:
	docker compose up -d --build frontend
	@echo "Waiting for dist/index.html..."
	@for _ in $$(seq 1 60); do [ -f frontend/dist/index.html ] && break; sleep 1; done
	@[ -f frontend/dist/index.html ] && echo "frontend OK" || { echo "frontend build failed"; exit 1; }

publish: build-frontend
	sudo mkdir -p /var/www/cinemaseat
	docker compose run --rm publish

test:
	docker compose exec api pytest -q --tb=short

smoke:
	./tests/smoke.sh

scenario-a:
	@./tests/scenario_a.sh

clean-clone:
	@./tests/clean_clone.sh