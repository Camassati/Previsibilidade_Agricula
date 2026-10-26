# -*- coding: utf-8 -*-
"""Treina os 3 momentos, exporta coeficientes, escalas e quantis de resíduo."""
import warnings, json, numpy as np, pandas as pd
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import MinMaxScaler
from sklearn.linear_model import ElasticNet
from sklearn.model_selection import GroupKFold, GridSearchCV
warnings.filterwarnings("ignore")
S = 42
ARQUIVO_MODELO = "df_tcc_modelo.xlsx"     # saída do alvo_produtividade_5.ipynb
SAIDA = "modelo_export.json"              # entrada da calculadora

# Os MESMOS dois filtros do notebook de modelagem. Se divergirem, a calculadora
# estaria rodando sobre uma base diferente da que produziu os resultados.
MIN_COBERTURA_CICLO = 0.80
FAIXAS_SACAS = {"Soja (em grão)": (15, 100), "Milho (em grão)": (20, 250),
                "Trigo (em grão)": (10, 100)}

df = pd.read_excel(ARQUIVO_MODELO, sheet_name="modelo")
print(f"Bruto: {len(df)} linhas")
df = df[df["cobertura_ciclo"] >= MIN_COBERTURA_CICLO].copy()
mascara = pd.Series(False, index=df.index)
for cultura, (lo, hi) in FAIXAS_SACAS.items():
    mascara |= ((df["cultura_tcc"] == cultura) &
                df["rendimento_sacas_ha"].between(lo, hi))
df = df[mascara].copy()
print(f"Após filtros: {len(df)} linhas | {df['cod_municipio'].nunique()} municípios")

# Guarda de sanidade: a mesma do notebook de modelagem. Zeros impossíveis aqui
# significam que o notebook do alvo rodou sem agregar_coluna(min_count=1).
impossivel = [c for c in df.columns
              if c.endswith(("graus_dia_acum", "radiacao_total_mjm2"))
              and not c.startswith("ciclo_")]
com_zero = int((df[impossivel] == 0).any(axis=1).sum())
if com_zero:
    raise AssertionError(
        f"{com_zero} linhas com zero impossível em {len(impossivel)} colunas. "
        "Reexecute alvo_produtividade_5.ipynb com agregar_coluna() antes de treinar.")
print("OK: nenhum zero fabricado.")

df = df.sort_values(["cod_municipio", "cultura_tcc", "ano_pam"]).reset_index(drop=True)

# média histórica expansiva (só safras anteriores)
g = df.groupby(["cod_municipio", "cultura_tcc"])["rendimento_sacas_ha"]
df["base_hist"] = g.apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0,1], drop=True)
ufp = (df.sort_values("ano_pam").groupby(["UF","cultura_tcc"])["rendimento_sacas_ha"]
         .apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0,1], drop=True))
df["base_hist"] = df.base_hist.fillna(ufp).fillna(df.rendimento_sacas_ha.mean())

SOLO = ["retencao_agua","drenagem","aptidao_graos","profundidade","risco_erosao"]
MOM = {"M1":["vegetativo_"], "M2":["vegetativo_","floracao_"],
       "M3":["vegetativo_","floracao_","enchimento_"]}
def cols(pref):
    return [c for c in df.columns if any(c.startswith(p) for p in pref)] + \
           [c for c in SOLO if c in df.columns] + ["base_hist"]

y = df.rendimento_sacas_ha.values.astype(float)
gr = df.cod_municipio.values
GRADE = {"m__alpha":[0.001,0.01,0.1,0.5,1.0,5.0],
         "m__l1_ratio":[0.1,0.3,0.5,0.7,0.9,0.95]}
EXPORT = {"momentos": {}}

for nome, pref in MOM.items():
    cs = cols(pref); X = df[cs]
    med = X.median(numeric_only=True)
    Xf = X.fillna(med)
    # previsões fora da amostra, para os quantis de resíduo
    oos = np.full(len(y), np.nan)
    for tr, te in GroupKFold(n_splits=5).split(np.arange(len(y)), groups=gr):
        mtr = X.iloc[tr].median(numeric_only=True)
        b = GridSearchCV(Pipeline([("e",MinMaxScaler()),("m",ElasticNet(max_iter=20000,random_state=S))]),
                         GRADE, cv=GroupKFold(n_splits=4),
                         scoring="neg_root_mean_squared_error", n_jobs=-1)
        b.fit(X.iloc[tr].fillna(mtr), y[tr], groups=gr[tr])
        oos[te] = b.predict(X.iloc[te].fillna(mtr))
    # modelo final na base inteira
    fin = GridSearchCV(Pipeline([("e",MinMaxScaler()),("m",ElasticNet(max_iter=20000,random_state=S))]),
                       GRADE, cv=GroupKFold(n_splits=5),
                       scoring="neg_root_mean_squared_error", n_jobs=-1)
    fin.fit(Xf, y, groups=gr)
    esc = fin.best_estimator_.named_steps["e"]; mod = fin.best_estimator_.named_steps["m"]
    rmse = float(np.sqrt(np.mean((oos-y)**2)))
    EXPORT["momentos"][nome] = {
        "variaveis": cs,
        "min": [float(v) for v in esc.data_min_],
        "range": [float(v) if v else 1.0 for v in (esc.data_max_-esc.data_min_)],
        "coef": [round(float(v),6) for v in mod.coef_],
        "intercept": float(mod.intercept_),
        "mediana": [float(med.get(c,0.0)) for c in cs],
        "alpha": float(fin.best_params_["m__alpha"]),
        "l1_ratio": float(fin.best_params_["m__l1_ratio"]),
        "rmse_oos": round(rmse,3),
        "zeradas": int((mod.coef_==0).sum()),
    }
    # quantis do resíduo relativo, por cultura
    res = {}
    for cult, sub in df.assign(_p=oos).groupby("cultura_tcc"):
        r = (sub._p - sub.rendimento_sacas_ha).values
        r = r[~np.isnan(r)]
        res[cult] = {"q10":round(float(np.percentile(r,10)),2),
                     "q25":round(float(np.percentile(r,25)),2),
                     "q50":round(float(np.percentile(r,50)),2),
                     "q75":round(float(np.percentile(r,75)),2),
                     "q90":round(float(np.percentile(r,90)),2),
                     "dp":round(float(np.std(r)),2),"n":int(len(r)),
                     "todos":[round(float(v),2) for v in np.sort(r)]}
    EXPORT["momentos"][nome]["residuo"] = res
    print(f"{nome}: {len(cs)} vars | RMSE oos {rmse:.2f} | alpha {fin.best_params_['m__alpha']} "
          f"l1 {fin.best_params_['m__l1_ratio']} | {int((mod.coef_==0).sum())} zeradas")

# linhas disponíveis para simulação
linhas = []
for r in df.itertuples():
    linhas.append({"m": int(r.cod_municipio), "nome": r.municipio_ibge, "uf": r.UF,
                   "c": r.cultura_tcc, "s": int(r.ano_pam),
                   "real": round(float(r.rendimento_sacas_ha),2),
                   "hist": round(float(r.base_hist),2)})
EXPORT["linhas"] = linhas
# valores das variáveis por linha, na ordem de M3 (M1 e M2 são prefixos do mesmo conjunto)
ordem = EXPORT["momentos"]["M3"]["variaveis"]
vals = df[ordem].astype(float)
EXPORT["ordem_m3"] = ordem
EXPORT["valores"] = [[None if pd.isna(v) else round(float(v),3) for v in row] for row in vals.values]
json.dump(EXPORT, open(SAIDA, "w"), ensure_ascii=False)
import os
print(f"{SAIDA}: {round(os.path.getsize(SAIDA)/1024)} KB — pronto para a calculadora")
