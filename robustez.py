# -*- coding: utf-8 -*-
"""Análise de robustez — os três testes que qualificam a conclusão.

Roda depois da modelagem e responde três perguntas que a banca pode fazer e
que a tabela de desempenho sozinha não responde:

  1. O ganho sobre o método tradicional sobrevive a um baseline que recebe a
     MESMA informação de ano que o modelo recebe?
  2. O modelo acerta a safra de quebra quando precisa PREVÊ-LA, e não apenas
     quando já viu outros municípios passarem por ela?
  3. O valor do modelo se concentra nos anos de clima atípico?

Entrada : df_tcc_modelo.xlsx  (saída do alvo_produtividade_5.ipynb)
Saída   : robustez.xlsx  +  relatório no console

Tempo   : ~4 minutos.
"""
import warnings
import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler
from sklearn.linear_model import ElasticNet
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupKFold, GridSearchCV

warnings.filterwarnings("ignore")

ARQUIVO = "df_tcc_modelo.xlsx"
SAIDA = "robustez.xlsx"
SEMENTE = 42
MIN_COBERTURA_CICLO = 0.80
FAIXAS_SACAS = {"Soja (em grão)": (15, 100), "Milho (em grão)": (20, 250),
                "Trigo (em grão)": (10, 100)}
COLUNAS_SOLO = ["retencao_agua", "drenagem", "aptidao_graos", "profundidade", "risco_erosao"]
# O caso que a banca vai perguntar: a seca do Rio Grande do Sul.
CASO = {"UF": "RS", "ano_pam": 2022, "cultura": "Soja (em grão)"}


# ------------------------------------------------------------------ carga ---
def carregar():
    df = pd.read_excel(ARQUIVO, sheet_name="modelo")
    df = df[df["cobertura_ciclo"] >= MIN_COBERTURA_CICLO].copy()
    m = pd.Series(False, index=df.index)
    for cultura, (lo, hi) in FAIXAS_SACAS.items():
        m |= (df["cultura_tcc"] == cultura) & df["rendimento_sacas_ha"].between(lo, hi)
    df = df[m].sort_values(["cod_municipio", "cultura_tcc", "ano_pam"]).reset_index(drop=True)

    # média histórica: definição única em media_historica.py (decisão de 26/10/2026)
    from media_historica import calcular_base_hist
    pam = pd.read_excel(ARQUIVO, sheet_name="pam_bruto")
    df["base_hist"], origem_hist = calcular_base_hist(df, pam)
    print(f"base_hist por origem: {origem_hist.value_counts().to_dict()}")
    if (origem_hist == "vazio").any():
        raise AssertionError("há linhas sem nenhuma média histórica possível — investigar")

    # Método tradicional BEM ESPECIFICADO: a média do município escalada pelo
    # fator do ano na região, estimado leave-one-out nos demais municípios.
    k = ["UF", "cultura_tcc", "ano_pam"]
    sr = df.groupby(k)["rendimento_sacas_ha"].transform("sum")
    sh = df.groupby(k)["base_hist"].transform("sum")
    fator = ((sr - df.rendimento_sacas_ha) / (sh - df.base_hist).replace(0, np.nan))
    df["base_indexada"] = (df.base_hist * fator.fillna(1.0)).fillna(df.base_hist)
    return df


def modelos(semente=SEMENTE):
    return {
        "elastic_net": (
            Pipeline([("e", MinMaxScaler()),
                      ("m", ElasticNet(max_iter=20000, random_state=semente))]),
            {"m__alpha": [0.001, 0.01, 0.1, 0.5, 1.0, 5.0],
             "m__l1_ratio": [0.1, 0.3, 0.5, 0.7, 0.9, 0.95]}),
        "random_forest": (
            Pipeline([("m", RandomForestRegressor(n_estimators=200, random_state=semente,
                                                  n_jobs=-1))]),
            {"m__max_depth": [8, 15, None], "m__min_samples_leaf": [1, 5, 10]}),
    }


def prever(df, esquema):
    """Previsões fora da amostra em cada esquema de validação."""
    cols = ([c for c in df.columns
             if c.startswith(("vegetativo_", "floracao_", "enchimento_"))]
            + [c for c in COLUNAS_SOLO if c in df.columns] + ["base_hist"])
    X, y = df[cols], df.rendimento_sacas_ha.values.astype(float)
    gr, idx = df.cod_municipio.values, np.arange(len(df))
    safras = np.sort(df.ano_pam.unique())

    def particoes():
        if esquema == "espacial":
            yield from GroupKFold(n_splits=5).split(idx, groups=gr)
        else:
            for s in safras[1:]:
                tr, te = idx[df.ano_pam.values < s], idx[df.ano_pam.values == s]
                if len(tr) >= 50 and len(te):
                    yield tr, te

    saida = {}
    for nome, (pipe, grade) in modelos().items():
        pv = np.full(len(y), np.nan)
        for tr, te in particoes():
            med = X.iloc[tr].median(numeric_only=True)
            b = GridSearchCV(pipe, grade, cv=GroupKFold(n_splits=4),
                             scoring="neg_root_mean_squared_error", n_jobs=-1)
            b.fit(X.iloc[tr].fillna(med), y[tr], groups=gr[tr])
            pv[te] = b.predict(X.iloc[te].fillna(med))
        saida[nome] = pv
        print(f"   {esquema}/{nome}: pronto")
    return saida


def rmse(p, y):
    p, y = np.asarray(p, float), np.asarray(y, float)
    ok = ~np.isnan(p)
    return float(np.sqrt(((p[ok] - y[ok]) ** 2).mean()))


# --------------------------------------------------------------------- main --
print("Carregando...")
df = carregar()
y = df.rendimento_sacas_ha.values.astype(float)
print(f"{len(df)} linhas | {df.cod_municipio.nunique()} municípios | "
      f"safras {sorted(df.ano_pam.unique())}\n")

print("Treinando (demora alguns minutos)...")
esp = prever(df, "espacial")
tmp = prever(df, "temporal")
df["prev_esp_en"], df["prev_esp_rf"] = esp["elastic_net"], esp["random_forest"]
df["prev_tmp_en"], df["prev_tmp_rf"] = tmp["elastic_net"], tmp["random_forest"]

# ---- 1. o ganho sobrevive a um baseline que sabe o ano? --------------------
print("\n" + "=" * 70)
print("1. O GANHO SOBREVIVE A UM BASELINE QUE SABE O ANO?")
print("=" * 70)
t1 = pd.DataFrame([
    {"metodo": "média histórica do município", "rmse": rmse(df.base_hist, y)},
    {"metodo": "média histórica INDEXADA pelo ano", "rmse": rmse(df.base_indexada, y)},
    {"metodo": "Elastic Net M3 (espacial)", "rmse": rmse(df.prev_esp_en, y)},
    {"metodo": "Random Forest M3 (espacial)", "rmse": rmse(df.prev_esp_rf, y)},
])
print(t1.round(2).to_string(index=False))
r_pura, r_idx = rmse(df.base_hist, y), rmse(df.base_indexada, y)
r_en = rmse(df.prev_esp_en, y)
print(f"\n  Elastic Net contra a média pura     : {100*(r_pura-r_en)/r_pura:+6.1f}%")
print(f"  Elastic Net contra a média indexada : {100*(r_idx-r_en)/r_idx:+6.1f}%  <- o número honesto")
print("\n  A diferença entre os dois é o valor de saber que ano é, que qualquer")
print("  método tradicional consegue e não é mérito do modelo climático.")

# ---- 2. a safra de quebra, nos dois esquemas ------------------------------
print("\n" + "=" * 70)
print(f"2. A SAFRA DE QUEBRA — {CASO['UF']} {CASO['ano_pam']}, {CASO['cultura']}")
print("=" * 70)
s = df[(df.UF == CASO["UF"]) & (df.ano_pam == CASO["ano_pam"]) &
       (df.cultura_tcc == CASO["cultura"])]
ys = s.rendimento_sacas_ha.values
t2 = pd.DataFrame([
    {"metodo": "média histórica", "previsto": s.base_hist.mean(), "rmse": rmse(s.base_hist, ys)},
    {"metodo": "média INDEXADA pelo ano", "previsto": s.base_indexada.mean(), "rmse": rmse(s.base_indexada, ys)},
    {"metodo": "Elastic Net — espacial", "previsto": s.prev_esp_en.mean(), "rmse": rmse(s.prev_esp_en, ys)},
    {"metodo": "Elastic Net — temporal", "previsto": s.prev_tmp_en.mean(), "rmse": rmse(s.prev_tmp_en, ys)},
])
print(f"  {len(s)} municípios | realizado médio {ys.mean():.1f} sacas/ha\n")
print(t2.round(2).to_string(index=False))
print("\n  No esquema ESPACIAL o modelo já tinha visto a seca em outros municípios.")
print("  No TEMPORAL, que é a previsão de verdade, ele só viu as safras anteriores.")

# ---- 3. o valor se concentra nos anos de clima atípico? -------------------
print("\n" + "=" * 70)
print("3. O VALOR SE CONCENTRA NOS ANOS DE CLIMA ATÍPICO?")
print("=" * 70)
d = df[df.prev_tmp_en.notna()].copy()
comp = []
for col in ["ciclo_precipitacao_total_mm", "ciclo_temp_max_abs", "ciclo_graus_dia_acum"]:
    if col not in d.columns:
        continue
    g = d.groupby(["cod_municipio", "cultura_tcc"])[col]
    d["z_" + col] = ((d[col] - g.transform("mean")) /
                     g.transform("std").replace(0, np.nan)).abs()
    comp.append("z_" + col)
d["anom_clima"] = d[comp].max(axis=1)
d = d.dropna(subset=["anom_clima"])
d["err_trad"] = (d.base_hist - d.rendimento_sacas_ha).abs()
d["err_en"] = (d.prev_tmp_en - d.rendimento_sacas_ha).abs()
d["faixa"] = pd.qcut(d.anom_clima, 4, labels=["1 normal", "2", "3", "4 anômalo"])
t3 = d.groupby("faixa", observed=True).agg(
    n=("anom_clima", "size"), z_mediano=("anom_clima", "median"),
    mae_trad=("err_trad", "mean"), mae_en=("err_en", "mean"))
t3["ganho_pct"] = 100 * (t3.mae_trad - t3.mae_en) / t3.mae_trad
print(t3.round(2).to_string())
print("\n  A anomalia é medida pelo CLIMA, não pelo desvio do rendimento. Medir pelo")
print("  rendimento seria circular: a variável de agrupamento seria o próprio erro")
print("  do método tradicional, e o grupo 'normal' seria, por definição, onde a")
print("  média acerta.")

with pd.ExcelWriter(SAIDA, engine="openpyxl") as w:
    t1.round(3).to_excel(w, sheet_name="1_baseline_justo", index=False)
    t2.round(3).to_excel(w, sheet_name="2_safra_de_quebra", index=False)
    t3.round(3).to_excel(w, sheet_name="3_anomalia_climatica")
    df[["cod_municipio", "municipio_ibge", "UF", "cultura_tcc", "ano_pam",
        "rendimento_sacas_ha", "base_hist", "base_indexada",
        "prev_esp_en", "prev_esp_rf", "prev_tmp_en", "prev_tmp_rf"]].to_excel(
        w, sheet_name="previsoes", index=False)
print(f"\n{SAIDA} gravado.")
