# Calculadora Agrícola — como rodar

Sete passos, nesta ordem. Cada um lê o que o anterior escreveu, então pular um
passo quebra o seguinte.

Tudo na mesma pasta. Primeira execução completa: **4 a 6 horas**, quase tudo no
passo 1. As execuções seguintes levam minutos, porque o ETL guarda cache.

---

## Antes de começar

```bash
pip install pandas numpy scikit-learn openpyxl matplotlib ipywidgets requests pyarrow
```

`pyarrow` é opcional — sem ele o cache do ETL cai para pickle, que funciona
igual e só ocupa mais disco. `requests` também é opcional: o notebook do alvo
tem fallback para `urllib`.

Confira a versão do Python:

```bash
python -c "import sys, sklearn, pandas; print(sys.version.split()[0], sklearn.__version__, pandas.__version__)"
```

Precisa ser **Python 3.9 ou superior**. O código usa recursos de pandas 2.x e
scikit-learn 1.5+ que não existem em 3.8 — o requisito RNF02 do TC1.1 diz 3.8+ e
precisa ser corrigido no texto.

---

## Passo 1 — ETL do clima

**Arquivo:** `etl_tcc_semanal.ipynb` · **Tempo:** 3 a 5 h na primeira vez, segundos nas seguintes

Lê os CSVs horários do INMET e devolve uma linha por estação e semana ISO.

| | |
|---|---|
| **Lê** | `inmet_historico/` — os CSVs horários, um por estação e ano |
| **Escreve** | `df_tcc_semana.xlsx` · `cache_diario/` |

Antes de executar, confira o caminho na primeira célula:

```python
CAMINHO_BASE = r"C:\Users\Camassati\Desktop\TCC\arquivos para bd tcc\inmet_historico"
ESTADOS_ALVO = ["SP", "PR", "RS", "MT"]
ANO_INICIAL  = 2015
GRANULARIDADE = "semana"
```

**O cache é versionado.** `VERSAO_CACHE = 2` força a releitura dos CSVs porque a
agregação diária mudou (fuso local, realinhamento da hora anterior, VPD horário).
O cache antigo fica no disco, inofensivo. Se você mexer na agregação diária
outra vez, suba esse número — senão o notebook reutiliza um diário errado.

**Deu certo se:** o resumo final mostra ~180 estações e nenhuma linha de status
`ERRO`. Se aparecer `nome fora do padrão`, confira o padrão dos nomes dos CSVs.

---

## Passo 2 — Alvo e cruzamento

**Arquivo:** `alvo_produtividade_5.ipynb` · **Tempo:** 5 a 15 min

Baixa a produtividade observada do IBGE, cruza estação com município e agrega as
semanas por estádio fenológico.

| | |
|---|---|
| **Lê** | `df_tcc_semana.xlsx` + API SIDRA do IBGE |
| **Escreve** | `df_tcc_modelo.xlsx` · `dados_observados.xlsx` |

Configuração na primeira célula:

```python
ANOS = list(range(2016, 2026))
FONTE_PAM = "api"          # "arquivo" só se a rede bloquear o SIDRA
LIMIAR_FUZZY = 0.88
```

**O passo mais delicado é a agregação por estádio.** A função `agregar_coluna()`
usa `min_count=1` nas somas: sem isso, uma floração inteira sem dado vira
*zero graus-dia*, que é a assinatura de uma seca extrema e não de um sensor
quebrado. Era o defeito que contaminava 27% da base.

**Deu certo se:** a cobertura média do ciclo fica em torno de **69%**, não 95%.
Se der 95%, a cobertura está contando linhas presentes na tabela em vez de
semanas com dado — e o filtro de 80% não vai barrar nada.

Confira a aba `ponte_municipio` antes de seguir: as linhas com
`metodo = aproximado` são casamentos por similaridade de nome e merecem uma
olhada rápida.

---

## Passo 3 — Modelagem

**Arquivo:** `modelagem_exante_expost.ipynb` · **Tempo:** 20 a 40 min

Treina os três algoritmos em dois esquemas de validação e três momentos de corte.

| | |
|---|---|
| **Lê** | `df_tcc_modelo.xlsx` |
| **Escreve** | `resultados_modelagem.xlsx` |

A célula **2b** é uma guarda: ela falha de propósito se a base tiver zeros
impossíveis. Se ela disparar, volte ao passo 2 — não contorne.

Três baselines são calculados, e a diferença entre eles importa:

| baseline | o que sabe |
|---|---|
| `base_media_historica` | só o passado daquele município |
| `base_media_indexada` | o passado **+ como está o ano na região** |
| `base_media_uf` | média da UF nas safras anteriores |

**Use o indexado como comparador principal.** No esquema espacial o modelo
treina com outros municípios da mesma safra, então ele sabe que ano é. A média
histórica pura não sabe, e comparar contra ela infla o ganho de 3,0% para 9,5%.

**Deu certo se:** 920 linhas, 145 municípios, e o RMSE do Elastic Net M3 espacial
em torno de **19,5**.

---

## Passo 4 — Robustez

**Arquivo:** `robustez.py` · **Tempo:** ~4 min

```bash
python robustez.py
```

| | |
|---|---|
| **Lê** | `df_tcc_modelo.xlsx` |
| **Escreve** | `robustez.xlsx` |

Responde as três perguntas que a tabela de desempenho não responde e que a banca
pode fazer:

1. O ganho sobrevive a um baseline que sabe o ano? → **+3,0%**, não +9,5%
2. O modelo acerta a safra de quebra quando precisa *prevê-la*? → **não**:
   no esquema temporal previu 69,3 sacas/ha onde a lavoura fez 31,6
3. O valor se concentra nos anos de clima atípico? → **não**, o modelo perde nos
   quatro quartis de anomalia

Este passo não alimenta nenhum outro. Ele existe para a seção de análise de
robustez do TCC, e é o que transforma uma fragilidade em uma contribuição.

---

## Passo 5 — Exportar o modelo

**Arquivo:** `treinar_export.py` · **Tempo:** ~3 min

```bash
python treinar_export.py
```

| | |
|---|---|
| **Lê** | `df_tcc_modelo.xlsx` |
| **Escreve** | `modelo_export.json` |

Treina os três momentos e grava coeficientes, escalas Min-Max e a distribuição
empírica dos resíduos por cultura. Aplica os **mesmos filtros** do passo 3 — se
eles divergirem, a calculadora estaria rodando sobre uma base diferente da que
produziu os resultados publicados.

**Reexecute este passo sempre que refizer o passo 2 ou 3.** Senão a calculadora
continua respondendo com o modelo antigo.

---

## Passo 6 — Calculadora no JupyterLab

**Arquivo:** `calculadora_agricola.ipynb` · **Tempo:** segundos

| | |
|---|---|
| **Lê** | `modelo_export.json` + `calculadora.py` |

Execute as células na ordem. A última exibe o formulário: cultura, município,
safra, área, momento, preço, custo e o histórico opcional do produtor.

A célula 2 cronometra a inferência e fecha o RNF01 e o CT02 do TC1.1 — deve dar
em torno de **0,02 ms**, com folga de ~2.000× sobre os 50 ms exigidos.

No campo de histórico, **o ano é obrigatório**:

```
2023: 62
2024: 58
```

Cada safra sua é comparada ao rendimento real do município *naquele mesmo ano*.
Sem o ano, a razão mediria a diferença entre safras, não entre o produtor e a
região dele.

---

## Passo 7 — Calculadora no navegador (opcional)

**Arquivo:** `calculadora.html`

Abra direto no navegador, sem servidor. O modelo está embutido na página e o
cálculo roda em JavaScript — possível porque o Elastic Net é linear, e a previsão
é um produto escalar. Com Random Forest isso não daria.

Para regerar a página depois de retreinar o modelo, os fontes são
`calc_head.html` (estrutura e estilo) e `calc_js.js` (cálculo e desenho).

---

## O módulo compartilhado

`calculadora.py` concentra toda a conta: previsão, fator de calibração, faixa de
incerteza, receita, custo, lucro e ponto de equilíbrio. O notebook e a página
importam daqui, para que as duas interfaces não possam divergir.

Os valores de referência de preço e custo ficam no topo do arquivo e são
editáveis:

```python
PRECO_REF = {"Soja (em grão)": 154.35, ...}   # CEPEA/ESALQ
CUSTO_REF = {"Soja (em grão)": 6115.83, ...}  # Aprosoja/MS
```

---

## Resumo da cadeia

```
inmet_historico/  (CSVs horários)
      │
      ├─[1] etl_tcc_semanal.ipynb ──────────► df_tcc_semana.xlsx
      │                                              │
      │     API SIDRA/IBGE ─────────┐                │
      │                             ▼                ▼
      ├─[2] alvo_produtividade_5.ipynb ──────► df_tcc_modelo.xlsx
      │                                              │
      │        ┌─────────────────────────────────────┤
      │        ▼                                     ▼
      ├─[3] modelagem_exante_expost.ipynb     [4] robustez.py
      │        │                                     │
      │        ▼                                     ▼
      │   resultados_modelagem.xlsx            robustez.xlsx
      │                                              
      │        ┌─────────────────────────────────────┘
      │        ▼
      ├─[5] treinar_export.py ────────────────► modelo_export.json
      │                                              │
      │                        ┌─────────────────────┴──────────┐
      │                        ▼                                ▼
      └─[6] calculadora_agricola.ipynb      [7] calculadora.html
                   (usa calculadora.py)
```

---

## Erros conhecidos

| mensagem | causa | o que fazer |
|---|---|---|
| `381 linhas com zero impossível` | passo 2 rodou sem `agregar_coluna()` | refazer o passo 2 |
| `No module named 'sklearn'` | pacote faltando | `pip install scikit-learn` e **reiniciar o kernel** |
| `ValueError: No objects to concatenate` | células executadas fora de ordem | Kernel → Restart & Run All |
| `ZeroDivisionError` no alvo | o cruzamento devolveu zero linhas | rodar `diagnosticar_merge()`, que testa cada chave separadamente |
| cobertura do ciclo em 95% | cobertura contando linhas, não semanas com dado | conferir se o passo 2 é a versão corrigida |
| `fit_params` não reconhecido | scikit-learn antigo | atualizar; o laço de CV aninhada já é explícito por causa disso |
| página HTML sem fonte serifada | sem internet | a página usa Google Fonts; cai para a fonte do sistema e funciona igual |

---

## O que ainda não existe

Está no TC1.1 e não foi construído: o nó sensor ESP32 com DHT22, a rede LoRaWAN,
o broker MQTT, o banco relacional em SQLite, a previsão de **data** de colheita e
a previsão de **rentabilidade** no sentido de margem do produtor (o que existe é
lucro estimado a partir de preço e custo informados).

A lista completa, com o que atende e o que não atende, está em
`claude/aderencia-tc11.md` no projeto.
