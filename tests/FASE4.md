# Fase 4 — P18 a P21 (2026-09-30)

Última fase de desenvolvimento antes da auditoria final. Quatro commits, um por proposta.

- **P18 — PDF:** «Exportar PDF» gera um PDF real no servidor (Python stdlib), para o
  equipamento e para todas as manutenções. Data e fuso vêm do dispositivo.
- **P19 — Rotas, datas e estado:** endereços `#/…` (voltar do Android/PWA, recarregar,
  atalhos); datas no dia local, sem UTC; estado «Offline» com rótulo próprio.
- **P20 — UX:** aviso antes de descartar alterações, progresso nos uploads, nome do anexo
  preenchido, pesquisa só em valores visíveis, acertos mobile, foco, «Tentar de novo»,
  notas no detalhe.
- **P21 — Service worker:** versão única (`APP_VERSION` em `backend/main.py`, a única coisa a
  mudar em cada versão); JS/CSS pedidos à rede; aviso «Nova versão disponível — Atualizar»;
  app abre sem ligação.

## Aplicar

1. Snapshot pela app e `cp -a data data-antes-fase4`.
2. `git am patches-fase4/*.patch` ou extrair `homelab/` por cima do projeto.
3. `python -m unittest discover -s tests -v` (esperado: OK, 2 falhas esperadas).
4. `docker compose up -d --build`.
5. Nos dispositivos, a primeira vez recarregar a app; a partir daqui, as novas versões
   aparecem com o aviso «Atualizar».

## Verificar no Android (não testável aqui)

- [ ] Instalar a app (menu do browser → Instalar) e abrir pelo ícone.
- [ ] Abrir um equipamento, abrir «Editar» e usar o gesto/botão voltar: fecha o modal;
      voltar outra vez regressa à lista.
- [ ] Editar um campo e voltar: pede confirmação para descartar.
- [ ] «Exportar PDF»: o ficheiro aparece em Transferências e abre num leitor de PDF.
- [ ] Modo avião: a app abre com «Sem ligação ao servidor»; ao desligar o modo avião
      volta sozinha.
- [ ] Depois de instalar uma nova versão no servidor: aparece «Nova versão disponível».
- [ ] Atalhos do ícone (toque longo): «Equipamentos» e «Manutenção» abrem a página certa.
