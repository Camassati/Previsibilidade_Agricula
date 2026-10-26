# -*- coding: utf-8 -*-
"""Ensaio: quanto a base_hist muda com a definição nova (media_historica.py)?

Só lê df_tcc_modelo.xlsx (abas modelo e pam_bruto). Não toca no banco, não
retreina nada. Rode na pasta do TCC:
    python ensaio_media_historica.py
Saída: ensaio_media_historica.txt + ensaio_media_historica.xlsx (linha a linha).
"""
import numpy as np
import pandas as pd

from media_historica import calcular_base_hist, FAIXAS_SACAS

ARQ = "df_tcc_modelo.xlsx"
MIN_COBERTURA_CICLO = 0.80
linhas = []


def p(s=""):
    print(s)
    linhas.append(str(s))


df = pd.read_excel(ARQ, sheet_name="modelo")
pam = pd.read_excel(ARQ, sheet_name="pam_bruto")
p(f"{ARQ}: modelo {len(df)} linhas | pam_bruto {len(pam)} linhas, "
  f"safras {sorted(int(a) for a in pam.ano_pam.dropna().unique())}")

# filtros: cópia de treinar_export.py
df = df[df["cobertura_ciclo"] >= MIN_COBERTURA_CICLO].copy()
m = pd.Series(False, index=df.index)
for cultura, (lo, hi) in FAIXAS_SACAS.items():
    m |= (df["cultura_tcc"] == cultura) & df["rendimento_sacas_ha"].between(lo, hi)
df = df[m].sort_values(["cod_municipio", "cultura_tcc", "ano_pam"]).reset_index(drop=True)
p(f"base filtrada: {len(df)} linhas, {df.cod_municipio.nunique()} municípios")

# ANTIGA: cópia literal de treinar_export.py
g = df.groupby(["cod_municipio", "cultura_tcc"])["rendimento_sacas_ha"]
mun_ant = g.apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True)
ufp = (df.sort_values("ano_pam").groupby(["UF", "cultura_tcc"])["rendimento_sacas_ha"]
         .apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True))
antiga = mun_ant.fillna(ufp).fillna(df.rendimento_sacas_ha.mean())

# NOVA
nova, origem = calcular_base_hist(df, pam)

r = df[["cod_municipio", "municipio_ibge", "UF", "cultura_tcc", "ano_pam",
        "rendimento_sacas_ha"]].copy()
r["base_hist_antiga"], r["base_hist_nova"], r["origem_nova"] = antiga, nova, origem
r["diferenca"] = r.base_hist_nova - r.base_hist_antiga

p("\n1. Origem da média nova")
p("   " + str(origem.value_counts().to_dict()))

p("\n2. Parte do MUNICÍPIO tem que ser idêntica à antiga")
so_mun = origem == "municipio"
dif_mun = (r.loc[so_mun, "diferenca"].abs() > 1e-9).sum()
p(f"   linhas com média municipal: {int(so_mun.sum())} | diferentes da antiga: {int(dif_mun)}"
  f"  {'OK' if dif_mun == 0 else '<<< VERIFICAR'}")

p("\n3. Linhas que mudam (as do reserva)")
mud = r[r.diferenca.abs() > 1e-9]
p(f"   {len(mud)} linhas mudam de valor")
if len(mud):
    p(f"   diferença absoluta: média {mud.diferenca.abs().mean():.2f} | "
      f"mediana {mud.diferenca.abs().median():.2f} | máxima {mud.diferenca.abs().max():.2f} sc/ha")
    p(f"   por safra: {mud.ano_pam.value_counts().sort_index().to_dict()}")
    p(f"   por UF   : {mud.UF.value_counts().to_dict()}")
    p(f"   por cultura: {mud.cultura_tcc.value_counts().to_dict()}")
    p("   as 10 maiores mudanças:")
    for x in mud.reindex(mud.diferenca.abs().sort_values(ascending=False).index).head(10).itertuples():
        p(f"      {x.municipio_ibge} / {x.UF} · {x.cultura_tcc} · {x.ano_pam}: "
          f"{x.base_hist_antiga:.2f} -> {x.base_hist_nova:.2f} (realizado {x.rendimento_sacas_ha:.2f})")

p("\n4. Erro do 'método tradicional' com cada definição (todas as linhas)")
for nome, col in [("antiga", "base_hist_antiga"), ("nova", "base_hist_nova")]:
    ok = r[col].notna()
    rmse = float(np.sqrt(((r.loc[ok, col] - r.loc[ok, "rendimento_sacas_ha"]) ** 2).mean()))
    p(f"   {nome:7s} RMSE {rmse:.3f} sc/ha  (n = {int(ok.sum())})")
p("   (só referência: o número oficial sai da modelagem e da robustez retreinadas)")

p(f"\n5. Linhas sem nenhuma média possível: {int((origem == 'vazio').sum())}")

r.to_excel("ensaio_media_historica.xlsx", index=False)
open("ensaio_media_historica.txt", "w", encoding="utf-8").write("\n".join(linhas))
print("\n>>> ensaio_media_historica.txt e .xlsx gravados")
