# Testes de regressão — Homelab Register

Rede de segurança da fase 0: descreve o comportamento atual que tem de se manter
e os bugs conhecidos da auditoria, para que cada correção seja verificável.

## Como correr

Na raiz do projeto, com Python 3.12 (o mesmo da imagem Docker):

```bash
python -m unittest discover -s tests -v        # Windows: py -m unittest discover -s tests -v
```

Os testes arrancam o servidor real numa pasta temporária e usam só dados
sintéticos (`tests/fixtures/dataset.json`). **Nunca tocam em `data/`.**

Os testes de interface são opcionais. Sem Playwright aparecem como *skipped*:

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium
```

Os testes de memória (`test_memoria.py`) medem o pico real do processo e só correm em
Linux. A versão com 60 MB de anexos corre sempre; a de 300 MB é lenta e só corre se pedida:

```bash
HR_TESTES_LENTOS=1 python -m unittest discover -s tests -v
```

## Resultado de referência (fase 5, 2026-09-30)

```
Ran 224 tests — OK (skipped=1, expected failures=1)
```

222 testes a passar, 1 bug ainda aberto (B07, fora do plano com a P09) e o teste
de 300 MB ignorado. Os testes de interface correm no fuso Atlantic/Azores.

## Convenção dos bugs conhecidos

Os ficheiros `test_bugs_conhecidos_*.py` têm um teste por bug ainda aberto (ID no
nome: B07, S04, U05…). Cada teste descreve o comportamento **correto** e está
marcado com `@unittest.expectedFailure`.

- Enquanto o bug existe, aparece como *expected failure* e a suite fica verde.
- Quando uma correção entra, o teste passa a *unexpected success* e a suite fica
  vermelha. No mesmo commit, o teste passa para `test_corrigidos_*.py`, sem o
  marcador e com a proposta no nome (ex.: `test_B03_P04_...`).

Alguns testes fixam o contrato da proposta (ex.: 403 para outra origem em S04).
Se a implementação escolher outro contrato, ajusta-se o teste no mesmo commit.

## Ficheiros

| Ficheiro | Conteúdo |
| --- | --- |
| `harness.py` | Servidor em pasta temporária; cliente HTTP (cookies, revisão, origem, streaming, PDF); extrator de texto de PDF |
| `ui_base.py` | Base dos testes de interface (Playwright + Chromium, fuso dos Açores) |
| `fixtures/` | Dados fictícios e um snapshot no formato anterior à fase 3 |
| `test_api.py` | 39 testes do comportamento base: sessão, validação, anexos, snapshot, estáticos, versão única |
| `test_ui.py` | 7 testes de interface do comportamento base, incluindo mobile a 390 px |
| `test_corrigidos_api.py` / `test_corrigidos_ui.py` | 81 + 25 testes dos bugs já corrigidos |
| `test_memoria.py` | Pico de memória em todos os caminhos de ficheiros (limite 60 MB) |
| `test_p18_pdf.py` … `test_p23_hardware.py` | Testes de cada proposta (P18–P21 e P22–P23) |
| `test_bugs_conhecidos_*.py` | 1 bug aberto: B07 (API). B10 foi corrigido na P26 |
