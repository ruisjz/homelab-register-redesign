# Homelab Register

Inventário e manutenção do hardware de um homelab: equipamentos, componentes, discos e
SMART, RAM, manutenções e preventivas, anexos, relatórios em PDF, snapshots e restore.
Funciona no browser (desktop e mobile) e instala-se como PWA.

Python 3.12 só com a biblioteca standard (`http.server`), SQLite, HTML/CSS/JS vanilla e Docker.

## Arrancar

```bash
cp .env.example .env      # preencher AUTH_USERNAME e AUTH_PASSWORD
docker compose up -d --build
```

A app fica em `http://localhost:5232`. Os dados ficam em `data/` (base de dados, anexos,
backups automáticos); essa pasta nunca vai para o git nem para a imagem.

## Estrutura

| Pasta / ficheiro | Conteúdo |
| --- | --- |
| `backend/main.py` | Servidor HTTP, rotas, sessão, proteção de origem, streaming de ficheiros |
| `backend/storage.py` | Modelo de dados, validação, migrações, anexos, snapshot e restore |
| `backend/report.py`, `backend/pdf.py` | Relatórios em PDF, sem dependências |
| `static/` | Interface (SPA), service worker e manifest da PWA |
| `tests/` | Testes de regressão e notas de cada fase (`FASE*.md`) |

## Testes

```bash
python -m unittest discover -s tests -v
```

Ver `tests/README.md` (testes de interface com Playwright, testes lentos de memória).

## Nova versão

Mudar só `APP_VERSION` em `backend/main.py`; os dispositivos recebem o aviso
«Nova versão disponível».
