# -*- coding: utf-8 -*-
"""Média histórica (base_hist) — UMA definição, usada por todos os scripts.

Decisão do grupo em 26/10/2026 (opção 1 + A):
  1. Média do MUNICÍPIO: média das safras ANTERIORES do mesmo município e
     cultura dentro da base do modelo (filtrada). Igual à versão anterior —
     ali cada (município, cultura) tem uma linha por ano, então "linha
     anterior" já era "safra anterior".
  2. Reserva da UF (município sem safra anterior na base): média da UF e
     cultura nas SAFRAS ANTERIORES, calculada sobre o rendimento do IBGE
     (aba pam_bruto, 2016 em diante), com o mesmo filtro de faixa plausível
     da base. Nunca usa a safra prevista nem safra posterior.
  3. Reserva final (UF sem nenhuma safra anterior no IBGE): média da cultura,
     todas as UFs, nas safras anteriores. Se nem isso existir: vazio (NaN).

O que mudou e por quê: a versão anterior fazia
    df.sort_values("ano_pam").groupby(["UF","cultura_tcc"])...shift().expanding().mean()
O shift recua UMA LINHA, não uma safra: a média incluía outros municípios da
MESMA safra e dependia da ordem arbitrária das linhas dentro do ano (não
reproduzível entre máquinas). E o último reserva era a média de TODAS as
safras, inclusive futuras. Ver 20_PENDENCIAS.txt / 30_ARMADILHAS.txt (A8).

Não depende da ordem das linhas: o resultado é o mesmo em qualquer máquina.
"""
import numpy as np
import pandas as pd

FAIXAS_SACAS = {"Soja (em grão)": (15, 100), "Milho (em grão)": (20, 250),
                "Trigo (em grão)": (10, 100)}
KG_POR_SACA = 60


def pam_padronizada(pam):
    """Aceita a aba pam_bruto (planilha) ou PRODUTIVIDADE_OBSERVADA (banco) e
    devolve colunas UF, cultura_tcc, ano, sacas — já com o filtro de faixa.
    sacas = rendimento_kg_ha / 60, como no notebook do alvo."""
    p = pam.copy()
    ren = {"uf": "UF", "safra": "ano", "ano_pam": "ano"}
    p = p.rename(columns={k: v for k, v in ren.items() if k in p.columns})
    p["sacas"] = pd.to_numeric(p["rendimento_kg_ha"], errors="coerce") / KG_POR_SACA
    p["ano"] = pd.to_numeric(p["ano"], errors="coerce")
    p = p.dropna(subset=["UF", "cultura_tcc", "ano", "sacas"])
    ok = pd.Series(False, index=p.index)
    for cultura, (lo, hi) in FAIXAS_SACAS.items():
        ok |= (p["cultura_tcc"] == cultura) & p["sacas"].between(lo, hi)
    p = p[ok]
    p["ano"] = p["ano"].astype(int)
    return p[["UF", "cultura_tcc", "ano", "sacas"]]


def _media_anterior(pam, chaves, alvo):
    """Para cada combinação de `chaves` + ano em `alvo`, média de pam com ano <
    ano alvo. Feito por soma/contagem acumulada por ano: sem depender de ordem."""
    g = pam.groupby(chaves + ["ano"])["sacas"].agg(["sum", "count"]).reset_index()
    saida = []
    for k, sub in alvo.groupby(chaves):
        k = k if isinstance(k, tuple) else (k,)
        m = np.ones(len(g), dtype=bool)
        for c, v in zip(chaves, k):
            m &= (g[c] == v).values
        h = g[m]
        for ano in sorted(sub["ano_pam"].unique()):
            prev = h[h["ano"] < ano]
            n = prev["count"].sum()
            saida.append(dict(zip(chaves, k), ano_pam=ano,
                              valor=(prev["sum"].sum() / n) if n else np.nan))
    return pd.DataFrame(saida, columns=chaves + ["ano_pam", "valor"])


def media_uf_anterior(df, pam):
    """Baseline 'média da UF': média da UF e cultura nas SAFRAS ANTERIORES, no
    IBGE (aba pam_bruto), com reserva na média da cultura. Substitui o
    baseline_media_uf do notebook, que tinha o mesmo defeito de linha."""
    p = pam_padronizada(pam)
    uf = _media_anterior(p, ["UF", "cultura_tcc"], df)
    cu = _media_anterior(p, ["cultura_tcc"], df)
    a = df[["UF", "cultura_tcc", "ano_pam"]].merge(
        uf, on=["UF", "cultura_tcc", "ano_pam"], how="left")["valor"].values
    b = df[["cultura_tcc", "ano_pam"]].merge(
        cu, on=["cultura_tcc", "ano_pam"], how="left")["valor"].values
    return pd.Series(np.where(np.isnan(a), b, a), index=df.index)


def calcular_base_hist(df, pam):
    """df: base do modelo JÁ FILTRADA (cod_municipio, UF, cultura_tcc, ano_pam,
    rendimento_sacas_ha). pam: aba pam_bruto ou PRODUTIVIDADE_OBSERVADA.
    Devolve uma Series alinhada ao índice de df, e uma Series com a origem
    ('municipio' | 'uf_ibge' | 'cultura_ibge' | 'vazio')."""
    p = pam_padronizada(pam)

    # 1. município: média das safras anteriores na base (por ano, não por linha)
    d = df[["cod_municipio", "cultura_tcc", "ano_pam", "rendimento_sacas_ha"]].copy()
    mun = pd.Series(np.nan, index=df.index)
    for _, sub in d.groupby(["cod_municipio", "cultura_tcc"]):
        for i, ano in zip(sub.index, sub["ano_pam"]):
            prev = sub.loc[sub["ano_pam"] < ano, "rendimento_sacas_ha"]
            if len(prev):
                mun[i] = prev.mean()

    # 2. UF no IBGE, safras anteriores
    uf = _media_anterior(p, ["UF", "cultura_tcc"], df)
    uf_val = df[["UF", "cultura_tcc", "ano_pam"]].merge(
        uf, on=["UF", "cultura_tcc", "ano_pam"], how="left")["valor"].values

    # 3. cultura no IBGE (todas as UFs), safras anteriores
    cu = _media_anterior(p, ["cultura_tcc"], df)
    cu_val = df[["cultura_tcc", "ano_pam"]].merge(
        cu, on=["cultura_tcc", "ano_pam"], how="left")["valor"].values

    base = mun.copy()
    origem = pd.Series(np.where(mun.notna(), "municipio", ""), index=df.index, dtype=object)
    usa_uf = base.isna() & ~np.isnan(uf_val)
    base[usa_uf] = uf_val[usa_uf.values]
    origem[usa_uf] = "uf_ibge"
    usa_cu = base.isna() & ~np.isnan(cu_val)
    base[usa_cu] = cu_val[usa_cu.values]
    origem[usa_cu] = "cultura_ibge"
    origem[base.isna()] = "vazio"
    return base, origem
