# Fase 0 — Rede de segurança (2026-09-29)

Esta entrega **não muda o comportamento da aplicação**. Acrescenta testes e torna
o código legível, para que as fases seguintes sejam verificáveis.

## O que mudou

1. **Testes de regressão** em `tests/` (ver `tests/README.md`): 45 testes do que
   está correto e 27 testes dos bugs conhecidos, marcados como falha esperada.
2. **Formatação mecânica**, sem alterar lógica:
   - `backend/*.py` com black (linha de 110);
   - `static/app.js`, `app.css`, `index.html` e `manifest.json` com prettier (linha de 110).
3. **Configuração de ferramentas**: `.prettierrc.json`, `pyproject.toml` (só black)
   e `requirements-dev.txt`. `requirements.txt` continua sem dependências.
4. O ZIP tem permissões normais (o original tinha 000 em todas as entradas).

## Prova de que o comportamento não mudou

| Verificação | Resultado |
| --- | --- |
| Árvore sintática do app.js (acorn), antes vs depois | idêntica |
| Árvore sintática dos 5 ficheiros Python (ast), antes vs depois | idêntica |
| Estrutura do index.html | idêntica (só `<meta>` passou a `<meta />`, equivalente em HTML5) |
| manifest.json | idêntico como JSON |
| Estilos calculados no Chromium: 38 estados, 6 650 elementos, ecrã e impressão, 1280 e 390 px | 0 diferenças |
| Suite de testes antes e depois | igual: 73 testes, OK, 27 falhas esperadas, 1 ignorado |

O app.js ainda tem 73 linhas acima de 110 caracteres: são templates HTML dentro
de strings. Parti-las muda o texto gerado, por isso fica para refatoração manual.

Não é preciso mudar a versão da cache (`?v=` e `CACHE_NAME`): o conteúdo servido
é equivalente.

## Como aplicar com segurança

1. Na app, **Backup → descarregar snapshot completo** e guardar o ZIP fora do servidor.
2. Parar o contentor e copiar os dados:
   `docker compose stop && cp -a data data-antes-fase0 && docker compose start`
3. Guardar a versão atual intacta. Com git:
   `git add -A && git commit -m "Versão original auditada" && git tag v-original`
4. Extrair este ZIP por cima do projeto. **Não inclui `data/`**, por isso não toca na base de dados.
5. Correr os testes: `python -m unittest discover -s tests -v`.
6. Opcional (o comportamento é o mesmo): `docker compose up -d --build`.

Nota: enquanto não houver `.dockerignore` (proposta P14), a pasta `tests/` também
entra na imagem. Não tem efeito no funcionamento.

## Próximo passo

Fase 1 — perda de dados, pela ordem do plano: P02, P03, P05, P06, P07, P08 e, no
fim, P04. Um commit por proposta, cada um a remover o `expectedFailure` do seu teste.
