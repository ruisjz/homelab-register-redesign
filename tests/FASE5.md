# Fase 5 — Auditoria independente (2026-09-30)

Segunda auditoria, feita por outro modelo sobre a versão da fase 4. A aplicação estava
sólida; não houve reescritas. Foram encontrados 2 problemas sérios e um conjunto de
correções pequenas. Um commit por proposta, cada um com os seus testes (primeiro
confirmados a falhar no código antigo). A suite passa de 204 para 224 testes; fica
aberto só o B07.

## O que mudou

| Proposta | Mudança | Corrige |
| --- | --- | --- |
| — | `.gitignore` (`data/`, `.env`, caches do Python) | — |
| P24 | O nome do ficheiro de cada anexo tem de ser o próprio ID; nada fora de `data/attachments/` é lido ou apagado, mesmo com uma BD adulterada | S11 |
| P25 | Login: corpo até 4 KB, recusado sem ler; falhas contadas por IP (o nome enviado nunca é guardado); teto de 10 000 IPs | S12 |
| P26 | Registos SMART/RAM antigos associados pelo nome ignoram campos vazios | B10 |
| P27 | Enter nos modais não recarrega a página; snapshot de versão mais recente ou com BD danificada dá 400 com a razão; mensagem clara para SMART/RAM sem componente; título do PDF com acentos | U08, R04, B14 |
| P28 | Apagar equipamento avisa quantos anexos se perdem e que medições de componentes já transferidos também se perdem | B15 |
| P29 | `GET /api/backup/full` recusado quando pedido por outro site; `TRUSTED_PROXY_IPS` inválido impede o arranque; CSP mínima na página; código morto removido | S13 |
| P30 | Nenhum pedido lê ou grava enquanto o restore troca os ficheiros (espera até 5 s, depois 503) | B16 |
| — | `README.md`, `.env.example`, `APP_VERSION = "fase5-2026-09-30"` | — |

## Os dois problemas sérios

- **S11 — snapshot adulterado.** Um snapshot com `stored_name='../homelab.db'` passava a
  validação. Depois do restore, descarregar o anexo servia a base de dados, e apagá-lo
  (ou a limpeza automática de órfãos) apagava-a. Exigia sessão iniciada, mas um restore
  tem de ser seguro com qualquer ficheiro.
- **S12 — login sem autenticação.** Oito pedidos com utilizadores de 15 MB levavam o
  processo a 272 MB, acima do `mem_limit` de 200 MB: o contentor era terminado e
  reiniciado. Depois da P25, o mesmo ataque fica em 23 MB.

## O que vais notar ao usar

- **Login:** 5 falhas seguidas a partir do mesmo IP bloqueiam 15 minutos, **seja qual for
  o utilizador tentado** (antes contava por IP e utilizador).
- **Apagar equipamento:** a confirmação pode ter mais linhas (anexos e medições de
  componentes que já estão noutro equipamento).
- **Durante um restore:** noutro dispositivo, gravar mostra «Está a decorrer um restore.
  Tenta de novo daqui a pouco.»; depois do restore, recarregar como antes.
- **PDF:** o título que aparece no leitor passa a ter acentos e travessão.
- **Nova versão:** aparece o aviso «Nova versão disponível» nos dispositivos.

## Verificação feita

| Verificação | Resultado |
| --- | --- |
| Suite antes das alterações | 204 testes, OK, 1 ignorado, 2 falhas esperadas (igual à fase 4) |
| Suite completa no fim | 224 testes, OK, 1 ignorado (300 MB), 1 falha esperada (B07) |
| Cada teste novo | confirmado a falhar no código anterior à sua correção |
| Memória (60 MB de anexos) | pico ~25 MB em todos os caminhos |
| Remoção dos blocos `if True:` | árvore sintática igual, fora a remoção |
| Interface com a CSP nova | todos os testes de interface (Chromium) passam |

Este ambiente tem Python 3.11 (a imagem usa 3.12) e não tem Docker: o primeiro
`docker compose up -d --build` na tua máquina é a verificação final da imagem.

## Aplicar

1. Na app, **Backup → Descarregar backup completo** e guardar o ZIP fora do servidor.
2. `docker compose stop && cp -a data data-antes-fase5 && docker compose start`
3. `git pull` do branch (ou merge do PR).
4. `python -m unittest discover -s tests -v` (esperado: OK, 1 falha esperada).
5. Confirmar que `TRUSTED_PROXY_IPS` no `.env` só tem IPs ou redes válidos (agora um
   valor inválido impede o arranque; o erro aparece em `docker logs`).
6. `docker compose up -d --build` e recarregar a app nos dispositivos.

Não há migração de esquema nesta fase.

## Ficou de fora

- B07 (dados SMART/RAM do formato antigo numa BD ou snapshot): continua fora do plano,
  como na P09. A base de dados é criada de novo na build final, por isso não há dados destes.
- Consultas N+1 na leitura do inventário: sem problema demonstrável com o volume de um homelab.
