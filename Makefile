# Makefile
.PHONY: setup start stop logs agent test dashboard clean

## Configuración inicial (ejecutar una sola vez)
setup:
	@echo "==> Preparando entorno..."
	cp -n .env.example .env || true
	mkdir -p .secrets models
	chmod 700 .secrets
	@[ -f .secrets/postgres_password ] || printf '%s' "dev-password-local" > .secrets/postgres_password
	chmod 600 .secrets/postgres_password
	python scripts/init_secrets.py
	bash scripts/download_model.sh
	@echo "Setup completo. Edita .env si necesitas ajustar configuracion."

## Levantar el stack completo (inicializador obligatorio + servicios)
start:
	python scripts/preflight_stack.py
	podman compose up -d
	@echo "Stack listo. API interna en http://agent:8090 (sin publicar al host)."

## Verificar el gate de inicializacion/topologia (AV-02)
verify-stack:
	python scripts/verify_stack_av02.py

## Ejecutar el agente (interactivo)
agent:
	podman compose run --rm -it agent python -m argentgob.agent.main $(ASSET) --profile $(PROFILE)

## Ejecutar tests
test:
	podman compose run --rm agent pytest tests/ -v

## Ver logs en tiempo real
logs:
	podman compose logs -f

## Abrir el dashboard Streamlit
dashboard:
	@echo "Dashboard disponible en http://localhost:8501"
	podman compose up -d dashboard
	podman compose logs -f dashboard

## Detener el stack
stop:
	podman compose down

## Limpiar todo (incluye datos de PostgreSQL)
clean:
	podman compose down -v
	@echo "Todos los datos de PostgreSQL fueron eliminados."
