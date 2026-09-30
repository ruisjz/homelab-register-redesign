# Fase 1 — Perda de dados (2026-09-29)

A fase 1 fecha os riscos de perda de dados. São 8 commits, um por proposta,
cada um com os seus testes. A suite passa de 27 para 18 falhas esperadas, e os
bugs que ficam abertos são os das fases 2 a 4.

## O que mudou

| Proposta | Mudança | Corrige |
| --- | --- | --- |
| P07 | O seed.json só é aplicado no primeiro arranque e só se a base estiver vazia | B05 |
| P02 | Import JSON removido (interface e formato antigo em lista no servidor). O Export JSON mantém-se | B01 |
| P06 | Botão “Repor dados de exemplo”, endpoint /api/reset e função clear_attachments removidos | B12 |
| P05 | Antes de cada restore, o estado atual é guardado em `data/backups/`; ficam os 5 mais recentes | B02 |
| P03 | Todas as gravações passam por `commit()`: se falharem, o ecrã volta ao estado anterior e aparece um aviso | B04, B11 (interface) |
| P04 | Revisão otimista entre dispositivos: gravar a partir de dados antigos dá 409 e não altera nada | B03 |
| P08 | Editor de manutenção: adicionar/remover linhas mantém o que foi escrito; preventivas já ligadas não se perdem | B08, B09 |
| — | Versão da cache atualizada (`?v=20260929-fase1`, `homelab-register-static-v8`), igual no index e no SW | — |

## O que vais notar ao usar

- **Página Backup:** já não tem “Importar JSON” nem “Repor dados de exemplo”.
- **Restore:** no fim, um aviso diz onde ficou guardado o estado anterior
  (ex.: `data/backups/pre-restore-20260929-213501-123456.zip`). Esse ZIP é um
  snapshot normal, que podes restaurar pela própria app. Se faltar algum ficheiro
  de anexo no disco, em vez do ZIP fica uma cópia exata da pasta (`...-copia`), e
  o restore não é bloqueado.
- **Erros ao gravar:** aparece sempre um aviso, e o ecrã volta ao que estava.
  Sem ligação ao servidor: “Sem ligação ao servidor.”
- **Sessão expirada** (p. ex. depois de reiniciar o contentor): aviso, ecrã de
  login e, depois de entrares, voltas ao mesmo equipamento. A alteração tem de ser
  repetida; o que estava escrito num formulário aberto perde-se.
- **PC e telemóvel ao mesmo tempo:** se um grava e o outro tenta gravar a partir
  de dados antigos, o segundo recebe o aviso “Os dados foram alterados noutro
  dispositivo…” e pode escolher recarregar. Se não recarregar, as gravações
  seguintes continuam a ser recusadas, e a alteração recusada tem de ser refeita.
  O mesmo vale para dois separadores no mesmo browser.
- **Editor de manutenção:** preventivas que já não estão associadas ao equipamento,
  mas foram feitas nesse registo, aparecem marcadas “(já não associada)”.

## Verificação feita

| Verificação | Resultado |
| --- | --- |
| Suite completa | 89 testes: 70 passam, 18 falhas esperadas (fases 2–4), 1 ignorado (lento) |
| Cada falha esperada | falha pelo motivo do bug, não por erro do teste |
| Cópia da tua BD real (5 equipamentos, 9 manutenções) | arranca, grava, reinicia e restaura sem erros nem perda de dados |
| Atualização com o service worker antigo instalado | o código novo é carregado no primeiro recarregamento |
| Mobile a 390 px | sem overflow horizontal na página de backup nem no editor |

Uma nota sobre a BD real: gravar sem alterações acrescenta `"preventivePlanIds": []`
aos registos que não tinham esse campo. Não é uma perda de dados, e o código
original fazia o mesmo.

## Como aplicar

1. Na app, **Backup → Descarregar backup completo** e guardar o ZIP fora do servidor.
2. `docker compose stop && cp -a data data-antes-fase1 && docker compose start`
3. Se usas git: aplicar os patches de `patches-fase1/` com `git am patches-fase1/*.patch`.
   Sem git: extrair a pasta `homelab/` do ZIP por cima do projeto (não inclui `data/`).
4. `python -m unittest discover -s tests -v` (esperado: OK, 18 falhas esperadas).
5. `docker compose up -d --build`
6. **Em cada dispositivo (PC e telemóvel), recarregar a app** e voltar a iniciar sessão.
   Uma página ainda aberta com o código antigo não consegue gravar por cima de nada
   (o servidor recusa pedidos sem revisão), mas mostra erros até ser recarregada.

## Limites conhecidos (ficam para as fases seguintes)

- `data/backups/` guarda cópias completas, com anexos: 5 restores seguidos ocupam
  5 vezes o espaço dos anexos.
- Um backup `...-copia` (feito com anexos em falta) não se restaura pela app,
  só manualmente; a P16 (fase 3) torna os backups tolerantes a ficheiros em falta.
- Upload de restore ainda carregado todo em memória (R01, fase 3).
- Dados legados SMART/RAM numa BD antiga continuam a bloquear gravações (B07, P09, fase 3);
  a tua BD atual não tem nenhum.
