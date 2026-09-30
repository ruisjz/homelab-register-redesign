# Fase 2 — Segurança (2026-09-29)

A fase 2 fecha os riscos de segurança da auditoria. São 6 commits, um por proposta
mais o da versão da cache, cada um com os seus testes. A suite passa de 18 para 9
falhas esperadas; as que ficam são das fases 3 e 4.

## O que mudou

| Proposta | Mudança | Corrige |
| --- | --- | --- |
| P10 | O login aceita acentos. O IP do cliente passa a ser lido da direita no `X-Forwarded-For`, saltando os proxies de confiança | S01, S02 |
| P11 | Gravações só aceites da própria app: `Sec-Fetch-Site: same-origin` ou, sem esse cabeçalho, `Origin` igual ao endereço acedido. Sem nenhum dos dois: 403. `PUT` só em `/api/data` e nos anexos | S04, S09 |
| P12 | `null` passa a texto vazio, números passam a texto, limite de 10 000 caracteres por campo, datas AAAA-MM-DD, snapshot validado antes do restore, datas escapadas no ecrã | S03, S05, B11 |
| P13 | `docker logs` com erros (e traceback), logins e pedidos recusados; timeout de ligação de 60 s; `Content-Length` validado; locks entre threads; mensagens de erro sem texto interno do Python | S06, S08, S10 |
| P14 | `.dockerignore` (a imagem deixa de levar `data/`, `.env`, `.git` e testes), HEALTHCHECK e aviso no login quando o browser recusa o cookie | S07, U07 |
| — | Versão da cache atualizada (`?v=20260929-fase2`, `homelab-register-static-v9`) | — |

**Importante (P14):** até agora, `COPY .` metia o ficheiro `.env`, com a palavra-passe,
dentro da imagem. Se alguma vez enviaste a imagem para um registo (Docker Hub, GHCR…),
convém mudar a palavra-passe. Se a imagem só existiu nos teus servidores, não há risco.

## Aplicar

Nas duas instalações:

1. Na app, **Backup → Descarregar backup completo**, e guardar o ZIP fora do servidor.
2. `docker compose stop && cp -a data data-antes-fase2 && docker compose start`
3. Com git: `git am patches-fase2/*.patch`. Sem git: extrair `homelab/` por cima do projeto
   (não inclui `data/` nem `.env`).
4. `python -m unittest discover -s tests -v` (esperado: OK, 9 falhas esperadas).
5. **`docker compose up -d --build`** — desta vez é preciso reconstruir, porque o Dockerfile mudou.
6. `docker ps` deve mostrar o contentor como `(healthy)` ao fim de ~15 s.
7. Recarregar a app no PC e no telemóvel e voltar a entrar.

## Na VPS: descobrir o IP do HAProxy (TRUSTED_PROXY_IPS)

1. Entra na app pelo domínio (ou falha um login de propósito).
2. `docker logs homelab-register 2>&1 | grep Login`
3. Vais ver uma linha como:
   `Login: ip=10.0.0.5 ligação=10.0.0.5 x-forwarded-for='203.0.113.25'`
   - `ligação=` é o IP do HAProxy da TierHive.
   - `x-forwarded-for=` deve ter o teu IP público. Se vier vazio (`''`), o HAProxy não está
     a enviar o cabeçalho: nesse caso, o rate-limit continua partilhado por todos os
     visitantes, e é preciso pedir à TierHive para ativar `option forwardfor`.
4. No `.env` da VPS: `TRUSTED_PROXY_IPS=10.0.0.5` (o valor de `ligação=`) e
   `docker compose up -d`.
5. Confirmar: no login seguinte, `ip=` passa a mostrar o teu IP público.

Se entradas diferentes mostrarem IPs de ligação diferentes (a TierHive pode ter mais do que
um HAProxy), usa uma lista (`10.0.0.5,10.0.0.6`) ou uma rede (`10.0.0.0/24`).

Em casa, por HTTP: `COOKIE_SECURE=false` e sem `TRUSTED_PROXY_IPS`.

## O que vais notar ao usar

- Palavras-passe com acentos funcionam.
- Mensagens de erro sempre em português.
- Se o browser recusar o cookie (p. ex. `COOKIE_SECURE=true` num acesso por `http://IP`),
  o ecrã de login explica o que mudar, em vez de voltar ao início em silêncio.
- Um snapshot com dados inválidos é recusado com a razão. Limite conhecido: até à P09
  (fase 3), um snapshot com dados SMART/RAM do formato antigo também é recusado. A tua BD
  não tem nenhum.

## Verificação feita

| Verificação | Resultado |
| --- | --- |
| Suite completa | 110 testes: 100 passam, 9 falhas esperadas (fases 3 e 4), 1 lento ignorado |
| Cada falha esperada | falha pelo motivo do bug |
| Cópia da tua BD real | arranca, grava e restaura |
| Snapshot da tua BD criado pela **versão original** | aceite pela validação nova e restaurado com os dados iguais |
| Chromium, com a verificação de origem ativa | login, edição, anexo com acentos, apagar anexo e restore pela interface |
| Comando do HEALTHCHECK | passa com a app ligada, falha com a app desligada |
| Linhas de log | mostram `ip=`, `ligação=` e `x-forwarded-for=`, nunca a palavra-passe |

Não havia Docker neste ambiente: a imagem não foi construída aqui. O primeiro
`docker compose up -d --build` na tua máquina é a verificação final do Dockerfile.

## Decisões mantidas

- O contentor continua a correr como root (a VPS só tem esta app).
- As portas ficam como estavam.
- CSP fora da fase 2.
