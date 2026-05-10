.PHONY: help up down restart logs ps psql shell test seed backfill build

help:
	@echo "Comandos disponibles:"
	@echo "  make up         — levanta TODO el stack (postgres + backend + frontend + gotenberg)"
	@echo "  make down       — apaga el stack (preserva datos)"
	@echo "  make down-v     — apaga y BORRA el volumen de Postgres (RESET)"
	@echo "  make restart    — reinicia backend + frontend"
	@echo "  make logs       — sigue logs de todos los servicios"
	@echo "  make logs-be    — sigue logs sólo del backend"
	@echo "  make ps         — estado de los contenedores"
	@echo "  make psql       — psql interactivo contra la BD local"
	@echo "  make shell      — bash dentro del contenedor backend"
	@echo "  make test       — pytest dentro del contenedor backend"
	@echo "  make seed       — corre seed_dev.py manualmente"
	@echo "  make backfill   — backfill embeddings de sesiones existentes"
	@echo "  make build      — rebuild de las imágenes (sin tocar datos)"
	@echo ""
	@echo "URLs:"
	@echo "  Backend  → http://localhost:8000  (docs: /docs)"
	@echo "  Frontend → http://localhost:4200"
	@echo "  Login    → admin@notiva.local / notiva"

up:
	@if [ ! -f .env ]; then cp .env.example .env && echo "Creado .env desde .env.example. Ajusta GROQ_API_KEY/OPENAI_API_KEY antes de procesar reuniones."; fi
	docker compose up -d --build
	@echo ""
	@echo "Esperando a que backend responda…"
	@for i in 1 2 3 4 5 6 7 8 9 10; do \
	  if curl -sf http://localhost:8000/ >/dev/null; then \
	    echo "✓ Backend OK"; break; \
	  else sleep 2; fi; \
	done
	@echo ""
	@$(MAKE) ps
	@echo ""
	@echo "→ http://localhost:4200    (frontend)"
	@echo "→ http://localhost:8000/docs (Swagger)"
	@echo "→ Login: admin@notiva.local / notiva"

down:
	docker compose down

down-v:
	docker compose down -v
	@echo "Volúmenes borrados. Próximo `make up` arranca BD limpia."

restart:
	docker compose restart backend frontend

logs:
	docker compose logs -f

logs-be:
	docker compose logs -f backend

ps:
	docker compose ps

psql:
	docker compose exec postgres psql -U notiva -d notiva

shell:
	docker compose exec backend bash || docker compose exec backend sh

test:
	docker compose exec backend pytest -q --maxfail=3

seed:
	docker compose exec backend python scripts/seed_dev.py

backfill:
	docker compose exec backend python scripts/backfill_embeddings.py

build:
	docker compose build
