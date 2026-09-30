# Fase 3.5 — P22 e P23 (2026-09-30)

Fase focada no modelo de hardware. Dois commits (P22, P23) e um da versão da cache.

- **P22:** componentes podem ser retirados (sem apagar nada) e transferidos entre
  equipamentos mantendo o mesmo ID, dados, anexos e histórico SMART, com o registo das
  máquinas por onde passaram («movido de»). Apagar continua proibido quando há
  histórico; a confirmação passa a avisar que os anexos são apagados definitivamente.
  Migração v2.
- **P23:** RAM e armazenamento do equipamento passam a ser calculados a partir dos
  componentes ativos. Novo campo «Tecnologia» (DDR4, NVMe…) nos componentes. Os campos
  manuais de RAM e armazenamento deixam de existir. Migração v3.

As duas migrações criam um snapshot automático antes (`data/backups/pre-migration-…`).

Aplicar: como nas fases anteriores (snapshot, cópia de `data/`, `git am patches-fase3-5/*.patch`
ou extrair `homelab/`, testes, `docker compose up -d --build`, recarregar a app nos dispositivos).
Numa base de dados nova (build final) as migrações não têm dados a converter.
