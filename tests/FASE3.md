# Fase 3 — Robustez e memória (2026-09-30)

A fase 3 garante que nenhum ficheiro é carregado inteiro para a memória. Tem ainda
backups tolerantes a anexos em falta e migrações do esquema com versão. Foi escrita
para a VPS real, com cerca de 300 MB de RAM. São 3 commits, um por proposta, mais o da
versão da cache. A P09 ficou fora da fase, por decisão.

## Resultado principal: memória

Pico real do processo (`VmHWM`), medido nos testes em cada caminho de ficheiros:

| Caminho | 60 MB de anexos | 300 MB de anexos |
| --- | --- | --- |
| Arranque | 23,1 MB | 23,0 MB |
| Upload de anexos | 24,9 MB | 24,9 MB |
| Download de anexos | 27,2 MB | 26,9 MB |
| Criação do snapshot | 27,2 MB | 26,9 MB |
| Download do backup | 27,2 MB | 26,9 MB |
| Restore (envio, validação, backup automático e extração) | 27,2 MB | 26,9 MB |

O objetivo era ficar abaixo de 60 MB, e o critério original abaixo de 100 MB no restore de
300 MB. Antes da fase 3, um restore de 100 MB chegava a 1,26 GB, e um único anexo de
25 MB rondava os 300 MB (estimativa), o suficiente para esgotar a VPS.

## O que mudou

| Proposta | Mudança | Corrige |
| --- | --- | --- |
| P17 | Versão do esquema na BD (`PRAGMA user_version`); migrações uma vez no arranque, numa transação, com snapshot automático antes; uma BD de versão mais recente impede o arranque | R03 |
| P16 | Anexos em falta não fazem o backup falhar: ficam listados em `missing_attachments` no manifesto. O restore aceita-os, avisa, marca as entradas como “ficheiro em falta” e não cria ficheiros falsos. Uma BD inválida ou um snapshot estruturalmente inválido continuam a ser recusados. Os anexos órfãos só são apagados depois do commit | R02, B13 |
| P15 | Todo o conteúdo de ficheiros passa em blocos de 1 MiB. Uploads sem multipart; restore com um só envio e um código de confirmação; backup em dois passos; anexos escritos diretamente no ZIP, com o hash calculado na mesma passagem; ficheiros estáticos também em blocos. Uma operação pesada de cada vez, verificação de espaço livre e temporários limpos em todos os caminhos | R01 |
| P15 (mobile) | `loading="lazy"` nas miniaturas dos anexos | — |
| P15 (Docker) | `mem_limit: 200m`, `memswap_limit: 200m` e `restart: unless-stopped` | — |
| — | Versão da cache atualizada (`?v=20260930-fase3`, `homelab-register-static-v10`) | — |

## O que vais notar ao usar

- **Descarregar backup:** a app prepara o ZIP e depois o browser descarrega-o diretamente
  para o disco. Se faltarem anexos, vês a lista antes do download.
- **Restaurar snapshot:** o ficheiro é enviado uma só vez. A app valida-o, mostra o resumo e
  pede confirmação. O snapshot validado fica à espera 30 minutos; depois disso é preciso
  escolher o ficheiro de novo.
- **Anexos em falta depois de um restore:** as entradas ficam na lista, marcadas “EM FALTA”
  e sem link. Podes editá-las ou apagá-las.
- **Primeiro arranque depois de atualizar:** a BD é migrada do esquema v0 para o v1 e antes
  disso é criado `data/backups/pre-migration-v0-v1-….zip`. Não se repete nos arranques
  seguintes.
- **“Já há um backup ou restore em curso”:** aparece se tentares duas destas operações ao
  mesmo tempo, por exemplo no PC e no telemóvel.
- **Falta de espaço:** se o disco não tiver espaço para o snapshot, a app recusa com uma
  mensagem clara, em vez de falhar a meio.
- **Restore interrompido:** se o contentor morrer a meio de um restore, o estado anterior é
  reposto no arranque seguinte.

## Aplicar

Nas duas instalações:

1. Na app, **Backup → Descarregar backup completo**, e guardar o ZIP fora do servidor.
2. `docker compose stop && cp -a data data-antes-fase3 && docker compose start`
3. Com git: `git am patches-fase3/*.patch`. Sem git: extrair `homelab/` por cima do projeto
   (não inclui `data/` nem `.env`).
4. `python -m unittest discover -s tests -v` (esperado: OK, 8 falhas esperadas).
5. **`docker compose up -d --build`**: o `docker-compose.yml` mudou, e é preciso recriar o
   contentor para os limites de memória se aplicarem.
6. `docker logs homelab-register 2>&1 | grep -i migra` deve mostrar o snapshot pré-migração
   e “Base de dados migrada do esquema v0 para v1”.
7. Recarregar a app no PC e no telemóvel e voltar a entrar.

Espaço em disco: cada snapshot automático em `data/backups/` ocupa o tamanho de todos os
anexos. Por omissão ficam 5; na VPS podes reduzir com `AUTO_BACKUPS_KEEP=2` no `.env`
(o compose já passa esta variável ao contentor).

## Docker: confirmar o limite e o reinício (não testado aqui)

Não havia Docker neste ambiente. A configuração tem um teste estático, mas o comportamento
real confirma-se na VPS:

1. **Limites aplicados:**
   `docker inspect homelab-register --format '{{.HostConfig.Memory}} {{.HostConfig.MemorySwap}} {{.HostConfig.RestartPolicy.Name}}'`
   → esperado: `209715200 209715200 unless-stopped`
2. **Consumo real:** `docker stats --no-stream homelab-register`. Em repouso deve rondar
   30–40 MiB de 200 MiB. Durante um backup ou restore o valor pode subir por causa da cache de
   disco do kernel, que é contada no contentor mas libertada quando é precisa. Não é
   memória da app.
3. **Reinício depois de uma falha:** simula a morte do processo, como faria um OOM:
   `sudo kill -9 $(docker inspect -f '{{.State.Pid}}' homelab-register)`
   Ao fim de alguns segundos, `docker ps` mostra o contentor “Up” há poucos segundos e
   `docker inspect -f '{{.RestartCount}}' homelab-register` aumenta 1.
4. **O limite protege a VPS:** `docker exec homelab-register python -c "b = bytearray(250*1024*1024)"`
   é terminado (código 137) por exceder os 200 MB, e a app continua a responder.

Notas:
- `unless-stopped` volta a arrancar o contentor depois de uma falha, de um OOM ou de um
  reinício da VPS, mas não depois de `docker stop` ou `docker kill` feitos por ti. É esse o
  comportamento pretendido.
- `memswap_limit` igual ao `mem_limit` impede o contentor de usar swap, o que torna o
  comportamento previsível numa VPS pequena.
- Fora do Swarm, o Docker não reinicia um contentor só por estar “unhealthy”. O reinício
  cobre o caso de o processo morrer; os timeouts da P13 evitam que a app fique pendurada.

## Verificação feita

| Verificação | Resultado |
| --- | --- |
| Suite completa | 137 testes: 128 passam, 8 falhas esperadas (B07 e fase 4), 1 lento ignorado e corrido à parte |
| Teste de memória com 300 MB de anexos | pico de 26,9 MB em todos os caminhos |
| Temporários | `data/tmp/` vazio depois de cada caminho, incluindo envios e downloads interrompidos e códigos expirados |
| Uma operação pesada de cada vez | um segundo backup durante um restore responde 409 |
| Snapshot no formato anterior | aceite (fixture permanente nos testes) |
| Migração da tua BD real | snapshot pré-migração criado, esquema v0 → v1, dados iguais aos da versão original, não se repete |
| Interface (Chromium) | anexo enviado chega igual e com miniatura `lazy`; backup e restore completos pela app |

## Limites conhecidos

- O inventário completo continua a ser carregado em memória em cada leitura e gravação (decisão da P04). Com o volume atual é desprezável.
- As miniaturas continuam a ser as imagens originais; o `lazy` só evita carregar as que não estão à vista.
- A imagem Docker e os limites não foram testados aqui; seguir a secção acima.
- B07: um snapshot com dados SMART/RAM do formato antigo continua a ser recusado (P09 retirada). A tua BD não tem nenhum.
