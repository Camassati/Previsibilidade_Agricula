# -*- coding: utf-8 -*-
"""Por que a base_hist do banco diverge da do modelo_export.json?

Somente leitura (banco em READ ONLY; planilhas e JSON só lidos).
Rode na pasta do TCC:  python investigar_base_hist.py
Saída: investigar_base_hist.txt — envie de volta.

Hipótese a testar: em treinar_export.py, o "fallback" da UF faz
    df.sort_values("ano_pam").groupby(["UF","cultura_tcc"])...shift().expanding().mean()
O shift é de UMA LINHA, não de um ano. Dentro do mesmo ano, a ordem das
linhas depende do algoritmo de ordenação (quicksort não é estável), e a
média da UF de uma linha pode incluir outros municípios do MESMO ano.
Se isso for verdade, a base_hist muda quando muda a versão do numpy/pandas,
mesmo com os dados idênticos.
"""
import hashlib
import json
import platform
import sys

import numpy as np
import pandas as pd

from servidor import ler_sql, numerico, TEXTO_BASE, MIN_COBERTURA_CICLO, FAIXAS_SACAS

linhas = []


def p(s=""):
    print(s)
    linhas.append(str(s))


def filtrar(df):
    """Trecho de filtros copiado de treinar_export.py."""
    df = df[df["cobertura_ciclo"] >= MIN_COBERTURA_CICLO].copy()
    m = pd.Series(False, index=df.index)
    for cultura, (lo, hi) in FAIXAS_SACAS.items():
        m |= ((df["cultura_tcc"] == cultura) & df["rendimento_sacas_ha"].between(lo, hi))
    return df[m].copy()


def base_hist(df, kind="quicksort", uf_por_ano=False):
    """kind/uf_por_ano=False reproduz treinar_export.py; as outras variantes
    servem para testar a hipótese."""
    df = df.sort_values(["cod_municipio", "cultura_tcc", "ano_pam"]).reset_index(drop=True)
    g = df.groupby(["cod_municipio", "cultura_tcc"])["rendimento_sacas_ha"]
    mun = g.apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True)
    if not uf_por_ano:
        ufp = (df.sort_values("ano_pam", kind=kind).groupby(["UF", "cultura_tcc"])["rendimento_sacas_ha"]
                 .apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True))
    else:
        # variante sem mesmo ano: média da UF só com safras ANTERIORES
        ufp = pd.Series(np.nan, index=df.index)
        for (uf, c), sub in df.groupby(["UF", "cultura_tcc"]):
            for ano in sub.ano_pam.unique():
                prev = sub[sub.ano_pam < ano].rendimento_sacas_ha
                ufp[sub.index[sub.ano_pam == ano]] = prev.mean() if len(prev) else np.nan
    out = df.copy()
    out["sem_historico_municipal"] = mun.isna()
    out["base_hist"] = mun.fillna(ufp).fillna(df.rendimento_sacas_ha.mean())
    return out


def comparar(df, js, rotulo):
    h = {(l["m"], l["c"], l["s"]): l["hist"] for l in js["linhas"]}
    d = df.assign(_js=[h.get((int(a), b, int(c))) for a, b, c in
                       zip(df.cod_municipio, df.cultura_tcc, df.ano_pam)])
    # round() do Python, como em treinar_export.py (o .round do pandas difere em x,xx5)
    arred = d.base_hist.map(lambda v: round(float(v), 2))
    div = (d._js.isna()) | ((arred - d._js).abs() > 0.0051)
    p(f"   {rotulo:58s} divergentes: {int(div.sum()):4d} de {len(d)}")
    return d, div


p(f"Python {sys.version.split()[0]} | numpy {np.__version__} | pandas {pd.__version__} | {platform.platform()}")

js = json.load(open("modelo_export.json", encoding="utf-8"))
p(f"modelo_export.json: {len(js['linhas'])} linhas, "
  f"md5 {hashlib.md5(open('modelo_export.json', 'rb').read()).hexdigest()}")

p("\n1. A PLANILHA (df_tcc_modelo.xlsx) reproduz o JSON hoje?")
xl = filtrar(pd.read_excel("df_tcc_modelo.xlsx", sheet_name="modelo"))
bx, div_x = comparar(base_hist(xl), js, "planilha, código atual (quicksort)")
comparar(base_hist(xl, kind="mergesort"), js, "planilha, ordenação estável (mergesort)")
comparar(base_hist(xl, uf_por_ano=True), js, "planilha, UF só com safras anteriores")

p("\n2. O BANCO (BASE_MODELO) é igual à planilha?")
db = filtrar(numerico(ler_sql("SELECT * FROM BASE_MODELO"), exceto=TEXTO_BASE))
bd = base_hist(db)
m = bx.merge(bd, on=["cod_municipio", "cultura_tcc", "ano_pam"], suffixes=("_x", "_b"))
p(f"   linhas casadas: {len(m)} | base_hist planilha x banco divergentes: "
  f"{int(((m.base_hist_x - m.base_hist_b).abs() > 1e-9).sum())}")
cols = [c for c in js["ordem_m3"] if c != "base_hist"]
dif_cols = {}
for c in cols:
    a, b = m[c + "_x"], m[c + "_b"]
    n = int((~((a.isna() & b.isna()) | ((a - b).abs() <= 1e-9))).sum())
    if n:
        dif_cols[c] = n
p(f"   variáveis climáticas planilha x banco com diferença: {dif_cols or 'nenhuma'}")

p("\n3. Variáveis do banco x valores do JSON, coluna por coluna")
pos = {(int(a), b, int(c)): i for i, (a, b, c) in
       enumerate(zip(bd.cod_municipio, bd.cultura_tcc, bd.ano_pam))}
por_col = {}
for k, l in enumerate(js["linhas"]):
    i = pos.get((l["m"], l["c"], l["s"]))
    if i is None:
        continue
    for j, c in enumerate(js["ordem_m3"]):
        a, b = js["valores"][k][j], bd.iloc[i][c]
        if a is None and pd.isna(b):
            continue
        if a is None or pd.isna(b) or abs(round(float(b), 3) - a) > 0.00051:
            por_col[c] = por_col.get(c, 0) + 1
p(f"   colunas com divergência: {por_col or 'nenhuma'}")

p("\n4. Quem são as linhas divergentes (planilha, código atual)?")
d = bx[div_x]
p(f"   {len(d)} linhas | sem histórico municipal (caem no fallback da UF): "
  f"{int(d.sem_historico_municipal.sum())}")
p(f"   por safra: {d.ano_pam.value_counts().sort_index().to_dict()}")
p(f"   por UF   : {d.UF.value_counts().to_dict()}")
tot_fb = int(bx.sem_historico_municipal.sum())
p(f"   na base inteira, linhas que caem no fallback da UF: {tot_fb} de {len(bx)}")

p("\n5. O fallback da UF usa safras do MESMO ano? (código atual)")
s = bx.sort_values("ano_pam")
mesmo = 0
for (uf, c), sub in s.groupby(["UF", "cultura_tcc"], sort=False):
    anos = sub.ano_pam.values
    for i in range(1, len(anos)):
        if anos[i - 1] == anos[i]:
            mesmo += 1
p(f"   linhas em que a média da UF (usada quando falta histórico municipal) já inclui safra do mesmo ano: {mesmo}")

p("\n6. Trecho da base_hist no treinar_export.py local")
try:
    for n, l in enumerate(open("treinar_export.py", encoding="utf-8"), 1):
        if "base_hist" in l or "ufp" in l or "sort_values" in l:
            p(f"   {n:4d}: {l.rstrip()}")
except FileNotFoundError:
    p("   treinar_export.py não encontrado")

open("investigar_base_hist.txt", "w", encoding="utf-8").write("\n".join(linhas))
print("\n>>> gravado em investigar_base_hist.txt")
