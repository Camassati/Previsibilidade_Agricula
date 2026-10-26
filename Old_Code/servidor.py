# -*- coding: utf-8 -*-
"""Previsão de Colheita — servidor local que lê o banco do TCC.

O navegador não fala com o MySQL (porta 3306). Este servidor fica no meio:
lê o banco em modo SOMENTE LEITURA, aplica o modelo exportado e entrega
JSON para previsao.html, que ele mesmo serve.

    python servidor.py              sobe em http://127.0.0.1:8765
    python servidor.py --conferir   só roda a trava de conferência e sai

De onde vem cada coisa:
    banco tcc2026   BASE_MODELO, PRODUTIVIDADE_OBSERVADA, CONFIGURACAO_SAFRA,
                    CULTURA, REGIAO, TIPO_SOLO, PONTE_ESTACAO_MUNICIPIO,
                    CLIMA_SEMANAL
    arquivos        modelo_export.json  (coeficientes, escalas, resíduos)
                    robustez.xlsx       (desempenho espacial x temporal)
                    df_tcc_modelo.xlsx  (aba janelas_ciclo: janela do treino)

Nenhum número é digitado aqui. A conta do modelo é cópia de treinar_export.py
(filtros e base_hist) e de calculadora.py (prever_rendimento e faixa). A
TRAVA confere, linha a linha, se o que o banco dá é o que o modelo usou; o
resultado aparece no topo da página.

Escrita no banco: nenhuma. Cada consulta abre conexão nova, com a sessão em
READ ONLY, e fecha em seguida (a Locaweb derruba conexão parada).
"""
import json
import math
import os
import sys
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from banco import conectar

PORTA = 8765
ARQ_MODELO = "modelo_export.json"
ARQ_ROBUSTEZ = "robustez.xlsx"
ARQ_JANELAS = "df_tcc_modelo.xlsx"
PAGINA = "previsao.html"

# Os MESMOS filtros de treinar_export.py / robustez.py / modelagem.
MIN_COBERTURA_CICLO = 0.80
FAIXAS_SACAS = {"Soja (em grão)": (15, 100), "Milho (em grão)": (20, 250),
                "Trigo (em grão)": (10, 100)}
TEXTO_BASE = {"municipio_ibge", "UF", "cultura_tcc"}
MOMENTOS = ["M1", "M2", "M3"]
ROTULO_MOMENTO = {"M1": "vegetativo", "M2": "até a floração", "M3": "ciclo completo"}


# ============================================================ banco =========
def ler_sql(sql, args=None):
    """SELECT em conexão própria, sessão READ ONLY, fechada no fim."""
    if not sql.lstrip().upper().startswith("SELECT"):
        raise RuntimeError("servidor só executa SELECT")
    con = conectar()
    try:
        with con.cursor() as cur:
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(sql, args or ())
            cols = [d[0] for d in cur.description]
            linhas = cur.fetchall()
    finally:
        con.close()
    return pd.DataFrame([list(r) for r in linhas], columns=cols)


def numerico(df, exceto=()):
    for c in df.columns:
        if c not in exceto:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ========================================================== estado ==========
EST = {"modelo": None, "base": None, "bruto": None, "trava": None,
       "erro_banco": None, "erro_modelo": None, "carregado_em": None,
       "janelas": None, "culturas_pam": None}


def carregar_modelo():
    with open(ARQ_MODELO, encoding="utf-8") as f:
        return json.load(f)


def montar_base():
    """BASE_MODELO do banco -> mesma base que treinar_export.py montou.
    Trecho de filtros e base_hist copiado de treinar_export.py."""
    df = ler_sql("SELECT * FROM BASE_MODELO")
    df = numerico(df, exceto=TEXTO_BASE)
    bruto = len(df)
    df = df[df["cobertura_ciclo"] >= MIN_COBERTURA_CICLO].copy()
    mascara = pd.Series(False, index=df.index)
    for cultura, (lo, hi) in FAIXAS_SACAS.items():
        mascara |= ((df["cultura_tcc"] == cultura) &
                    df["rendimento_sacas_ha"].between(lo, hi))
    df = df[mascara].copy()
    df = df.sort_values(["cod_municipio", "cultura_tcc", "ano_pam"]).reset_index(drop=True)
    g = df.groupby(["cod_municipio", "cultura_tcc"])["rendimento_sacas_ha"]
    df["base_hist"] = g.apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True)
    ufp = (df.sort_values("ano_pam").groupby(["UF", "cultura_tcc"])["rendimento_sacas_ha"]
             .apply(lambda s: s.shift().expanding().mean()).reset_index(level=[0, 1], drop=True))
    df["base_hist"] = df.base_hist.fillna(ufp).fillna(df.rendimento_sacas_ha.mean())
    return df, bruto


def carregar_tudo():
    EST["erro_modelo"] = EST["erro_banco"] = None
    try:
        EST["modelo"] = carregar_modelo()
    except Exception as e:
        EST["modelo"] = None
        EST["erro_modelo"] = f"{type(e).__name__}: {e}"
    try:
        EST["base"], EST["bruto"] = montar_base()
        pam = ler_sql("SELECT DISTINCT cultura_tcc FROM PRODUTIVIDADE_OBSERVADA")
        EST["culturas_pam"] = sorted(pam.cultura_tcc.tolist())
    except Exception as e:
        EST["base"] = None
        EST["erro_banco"] = f"{type(e).__name__}: {e}"
    try:
        EST["janelas"] = pd.read_excel(ARQ_JANELAS, sheet_name="janelas_ciclo")
    except Exception:
        EST["janelas"] = None
    EST["trava"] = conferir() if (EST["modelo"] and EST["base"] is not None) else None
    EST["carregado_em"] = datetime.now().isoformat(timespec="seconds")


# =========================================================== modelo =========
def vazio(v):
    return v is None or (isinstance(v, float) and math.isnan(v))


def prever(linha, momento):
    """Cópia de Calculadora.prever_rendimento (calculadora.py).
    `linha` é qualquer mapeamento variável -> valor."""
    m = EST["modelo"]["momentos"][momento]
    total = m["intercept"]
    contrib, imputadas, ausentes = [], [], []
    for j, var in enumerate(m["variaveis"]):
        coef = m["coef"][j]
        if var not in linha:
            ausentes.append(var)
            bruto = None
        else:
            bruto = linha[var]
            bruto = None if vazio(bruto) else float(bruto)
        if bruto is None:
            bruto = m["mediana"][j]
            if coef != 0:
                imputadas.append(var)
        esc = (bruto - m["min"][j]) / m["range"][j]
        esc = min(1.0, max(0.0, esc))
        parcela = coef * esc
        total += parcela
        if coef != 0:
            contrib.append({"variavel": var, "valor": bruto, "coeficiente": coef,
                            "contribuicao": parcela})
    contrib.sort(key=lambda c: -abs(c["contribuicao"]))
    return total, contrib, imputadas, ausentes


def faixa(rend, momento, cultura):
    """Cópia da faixa de Calculadora.calcular, sem fator do produtor."""
    todos = np.array(EST["modelo"]["momentos"][momento]["residuo"][cultura]["todos"])
    return {"p10": rend - np.percentile(todos, 90), "p25": rend - np.percentile(todos, 75),
            "p50": rend - np.percentile(todos, 50), "p75": rend - np.percentile(todos, 25),
            "p90": rend - np.percentile(todos, 10)}


# ============================================================ trava =========
def conferir():
    """O que o banco dá é o que o modelo usou? Compara, para cada linha de
    modelo_export.json, o realizado, a base_hist, as 55 variáveis e a
    previsão M3 recalculada a partir do banco."""
    mod, df = EST["modelo"], EST["base"]
    lin, ordem, vals = mod["linhas"], mod["ordem_m3"], mod["valores"]
    pos = {(int(r.cod_municipio), r.cultura_tcc, int(r.ano_pam)): i
           for i, r in enumerate(df.itertuples())}
    faltam, div_real, div_hist, div_vars, exemplos = [], 0, 0, 0, []
    max_var, max_prev = 0.0, 0.0
    for k, l in enumerate(lin):
        chave = (l["m"], l["c"], l["s"])
        if chave not in pos:
            faltam.append(chave)
            continue
        r = df.iloc[pos[chave]]
        if abs(round(float(r.rendimento_sacas_ha), 2) - l["real"]) > 0.0051:
            div_real += 1
        if abs(round(float(r.base_hist), 2) - l["hist"]) > 0.0051:
            div_hist += 1
            if len(exemplos) < 5:
                exemplos.append({"linha": l, "base_hist_banco": float(r.base_hist)})
        for j, c in enumerate(ordem):
            a, b = vals[k][j], r.get(c)
            if a is None and vazio(b):
                continue
            if a is None or vazio(b) or abs(round(float(b), 3) - a) > 0.00051:
                div_vars += 1
                if a is not None and not vazio(b):
                    max_var = max(max_var, abs(float(b) - a))
        p_banco = prever(r, "M3")[0]
        p_json = prever(dict(zip(ordem, vals[k])), "M3")[0]
        max_prev = max(max_prev, abs(p_banco - p_json))
    sobra = len(df) - (len(lin) - len(faltam))
    ok = (not faltam and sobra == 0 and div_real == 0 and div_hist == 0
          and div_vars == 0 and max_prev < 0.05)
    return {"ok": ok, "linhas_modelo": len(lin), "linhas_banco_filtradas": len(df),
            "linhas_banco_brutas": EST["bruto"], "faltam_no_banco": len(faltam),
            "sobram_no_banco": sobra, "divergencias_realizado": div_real,
            "divergencias_base_hist": div_hist, "divergencias_variaveis": div_vars,
            "maior_diferenca_variavel": max_var, "maior_diferenca_previsao_m3": max_prev,
            "exemplos_base_hist": exemplos,
            "faltam_exemplos": [list(f) for f in faltam[:5]]}


# ======================================================== respostas =========
def api_estado(_q):
    return {"carregado_em": EST["carregado_em"], "erro_banco": EST["erro_banco"],
            "erro_modelo": EST["erro_modelo"], "trava": EST["trava"],
            "janelas_disponiveis": EST["janelas"] is not None,
            "modelo": None if not EST["modelo"] else {
                m: {"variaveis": len(EST["modelo"]["momentos"][m]["variaveis"]),
                    "rmse_oos": EST["modelo"]["momentos"][m].get("rmse_oos"),
                    "zeradas": EST["modelo"]["momentos"][m].get("zeradas"),
                    "alpha": EST["modelo"]["momentos"][m].get("alpha"),
                    "l1_ratio": EST["modelo"]["momentos"][m].get("l1_ratio")}
                for m in MOMENTOS}}


def exigir_base():
    if EST["base"] is None or not EST["modelo"]:
        raise RuntimeError(EST["erro_banco"] or EST["erro_modelo"] or "base não carregada")


def api_opcoes(_q):
    exigir_base()
    df = EST["base"]
    saida = {}
    for cult, sub in df.groupby("cultura_tcc"):
        muns = []
        for (cod, nome, uf), s in sub.groupby(["cod_municipio", "municipio_ibge", "UF"]):
            muns.append({"cod": int(cod), "nome": nome, "uf": uf,
                         "safras": sorted(int(a) for a in s.ano_pam)})
        saida[cult] = sorted(muns, key=lambda m: (m["uf"], m["nome"]))
    return {"culturas": saida}


def api_previsao(q):
    exigir_base()
    cod, cult, safra = int(q["m"][0]), q["c"][0], int(q["s"][0])
    df = EST["base"]
    sel = df[(df.cod_municipio == cod) & (df.cultura_tcc == cult) & (df.ano_pam == safra)]
    if sel.empty:
        raise RuntimeError("combinação não está na base filtrada do modelo")
    r = sel.iloc[0]
    momentos = {}
    for m in MOMENTOS:
        total, contrib, imput, aus = prever(r, m)
        momentos[m] = {"previsto": total, "faixa": faixa(total, m, cult),
                       "contribuicoes": contrib[:10], "imputadas": imput, "ausentes": aus}
    pam = ler_sql("""SELECT safra, rendimento_sacas_ha FROM PRODUTIVIDADE_OBSERVADA
                     WHERE cod_municipio_ibge = %s AND cultura_tcc = %s
                     ORDER BY safra""", (cod, cult))
    return {"municipio": r.municipio_ibge, "uf": r.UF, "cultura": cult, "safra": safra,
            "realizado": float(r.rendimento_sacas_ha), "tradicional": float(r.base_hist),
            "cobertura_ciclo": float(r.cobertura_ciclo),
            "semanas_validas": int(r.semanas_validas), "semanas_esperadas": int(r.semanas_esperadas),
            "momentos": momentos,
            "historico_pam": [{"safra": int(a), "sacas_ha": float(b)}
                              for a, b in zip(pam.safra, pam.rendimento_sacas_ha)]}


# ============================================================ sensor ========
# Regras copiadas de etl_tcc_semanal.ipynb (agregar_para_diario /
# agregar_para_periodo) e de alvo_produtividade_5.ipynb (ESTADIOS,
# AGREGACAO_CICLO). Diferenças em relação ao treino, todas declaradas na tela:
#   - dia com menos de 18 h NÃO é descartado (decisão do autor: usar tudo o que
#     há e avisar que precisa de mais); a contagem de horas aparece;
#   - estádios contados a partir do plantio informado (no treino eram frações
#     da janela fixa da cultura na UF);
#   - graus-dia do estádio projetados: média diária medida x dias do estádio.
ESTADIOS = [("vegetativo", 0.00, 0.45), ("floracao", 0.45, 0.70), ("enchimento", 0.70, 1.00)]
MOMENTO_DO_ESTADIO = {"vegetativo": "M1", "floracao": "M2", "enchimento": "M3"}
TEMP_BASE_GDD = 10.0       # TEMP_BASE_GDD do notebook do ETL
MIN_HORAS_DIA = 18         # MIN_HORAS_DIA do notebook do ETL
VARS_SENSOR = ["temp_media", "temp_max_abs", "temp_min_abs", "amplitude_termica",
               "graus_dia_acum", "umidade_media", "umidade_min_abs", "vpd_medio_kpa",
               "vpd_max_kpa", "cobertura"]
NAO_MEDIDAS = ["precipitacao_total_mm", "radiacao_total_mjm2", "dias_com_chuva",
               "maior_veranico", "dias_secos_acumulados", "pressao_media",
               "vento_medio", "vento_rajada_max"]


def pressao_vapor_saturacao(t):
    """Tetens, em kPa — mesma função do ETL."""
    return 0.6108 * np.exp(17.27 * t / (t + 237.3))


def dias_do_estadio(ciclo):
    """{estadio: [índices de dia do ciclo]} com a mesma partição 45/70/100%."""
    saida = {n: [] for n, _, _ in ESTADIOS}
    for i in range(ciclo):
        f = i / ciclo
        for n, ini, fim in ESTADIOS:
            if ini <= f < fim:
                saida[n].append(i)
                break
    return saida


def agregar_sensor(id_config, plantio, ciclo, hoje):
    """LEITURA_IOT -> hora -> dia -> estádio, com as regras do ETL."""
    l = ler_sql("""SELECT temperatura, umidade_relativa, data_hora FROM LEITURA_IOT
                    WHERE id_config = %s ORDER BY data_hora""", (id_config,))
    partes = dias_do_estadio(ciclo) if ciclo else {n: [] for n, _, _ in ESTADIOS}
    res = {"leituras": int(len(l)), "primeira": None, "ultima": None,
           "intervalo_mediano_s": None, "fora_do_ciclo": 0, "estadios": {}}
    d = pd.DataFrame()
    if len(l) and plantio and ciclo:
        l = numerico(l, exceto={"data_hora"})
        l["data_hora"] = pd.to_datetime(l["data_hora"])
        res["primeira"], res["ultima"] = l.data_hora.min(), l.data_hora.max()
        gaps = l.data_hora.diff().dt.total_seconds().dropna()
        res["intervalo_mediano_s"] = float(gaps.median()) if len(gaps) else None
        l["vpd"] = (pressao_vapor_saturacao(l.temperatura)
                    * (1 - l.umidade_relativa / 100)).clip(lower=0)
        l["hora"] = l.data_hora.dt.floor("h")
        l["dia"] = l.data_hora.dt.normalize()
        l["dia_ciclo"] = (l.dia - pd.Timestamp(plantio)).dt.days
        mapa = {i: n for n, idx in partes.items() for i in idx}
        l["estadio"] = l.dia_ciclo.map(mapa)
        res["fora_do_ciclo"] = int(l.estadio.isna().sum())
        l = l[l.estadio.notna()]
        if len(l):
            # hora: média das leituras; extremos pela leitura mais alta/baixa
            h = l.groupby("hora").agg(
                dia=("dia", "first"), estadio=("estadio", "first"),
                temp=("temperatura", "mean"), umid=("umidade_relativa", "mean"),
                vpd=("vpd", "mean"), vpd_max=("vpd", "max"),
                tmax=("temperatura", "max"), tmin=("temperatura", "min"),
                umin=("umidade_relativa", "min"), n=("temperatura", "size"))
            # dia: mesma regra do ETL (média das horas, extremos do dia)
            d = h.groupby("dia").agg(
                estadio=("estadio", "first"), temp_media=("temp", "mean"),
                umidade_media=("umid", "mean"), vpd_kpa=("vpd", "mean"),
                vpd_max=("vpd_max", "max"), tmax=("tmax", "max"), tmin=("tmin", "min"),
                umin=("umin", "min"), horas=("temp", "count"), leituras=("n", "sum"))
            d["amplitude"] = d.tmax - d.tmin
            d["graus_dia"] = ((d.tmax + d.tmin) / 2 - TEMP_BASE_GDD).clip(lower=0)
    for nome, _, _ in ESTADIOS:
        idx = partes[nome]
        total = len(idx)
        inicio = (plantio + timedelta(days=idx[0])) if (plantio and idx) else None
        fim = (plantio + timedelta(days=idx[-1])) if (plantio and idx) else None
        decorridos = 0 if not inicio else max(0, min(total, (hoje - inicio).days + 1))
        sub = d[d.estadio == nome] if len(d) else d
        e = {"inicio": inicio, "fim": fim, "dias_do_estadio": total,
             "dias_decorridos": decorridos, "dias_com_leitura": int(len(sub)),
             "dias_com_18h": int((sub.horas >= MIN_HORAS_DIA).sum()) if len(sub) else 0,
             "horas_medidas": int(sub.horas.sum()) if len(sub) else 0,
             "leituras": int(sub.leituras.sum()) if len(sub) else 0, "valores": None}
        if len(sub):
            e["valores"] = {
                "temp_media": float(sub.temp_media.mean()),
                "temp_max_abs": float(sub.tmax.max()),
                "temp_min_abs": float(sub.tmin.min()),
                "amplitude_termica": float(sub.amplitude.mean()),
                "graus_dia_acum": float(sub.graus_dia.mean() * total),
                "umidade_media": float(sub.umidade_media.mean()),
                "umidade_min_abs": float(sub.umin.min()),
                "vpd_medio_kpa": float(sub.vpd_kpa.mean()),
                "vpd_max_kpa": float(sub.vpd_max.max()),
                "cobertura": len(sub) / total if total else 0.0,
            }
            e["graus_dia_medidos"] = float(sub.graus_dia.sum())
        res["estadios"][nome] = e
    return res


def base_hist_ao_vivo(cods, cultura, uf):
    """Média histórica para uma safra nova: como no treino (média expansiva
    das safras anteriores na base filtrada), aqui todas são anteriores."""
    b = EST["base"]
    s = b[b.cod_municipio.isin(cods) & (b.cultura_tcc == cultura)]
    if len(s):
        return float(s.rendimento_sacas_ha.mean()), f"média das {len(s)} safras do município na base do modelo"
    s = b[(b.UF == uf) & (b.cultura_tcc == cultura)]
    if len(s):
        return float(s.rendimento_sacas_ha.mean()), "média da UF na base do modelo (município sem safra na base)"
    return float(b.rendimento_sacas_ha.mean()), "média geral da base do modelo"


def previsao_com_sensor(sensor, cultura, cods, uf):
    if not EST["modelo"] or EST["base"] is None or not cultura:
        return None
    hist, origem = base_hist_ao_vivo(cods, cultura, uf)
    linha = {"base_hist": hist}
    for est, e in sensor["estadios"].items():
        if e["valores"]:
            for v, x in e["valores"].items():
                linha[f"{est}_{v}"] = x
    saida = {"base_hist": hist, "base_hist_origem": origem, "momentos": {}}
    for est, mom in MOMENTO_DO_ESTADIO.items():
        if not sensor["estadios"][est]["valores"]:
            continue
        total, contrib, imput, aus = prever(linha, mom)
        m = EST["modelo"]["momentos"][mom]
        fora = []
        for j, var in enumerate(m["variaveis"]):
            if var in linha and var != "base_hist":
                lo, hi = m["min"][j], m["min"][j] + m["range"][j]
                if linha[var] < lo or linha[var] > hi:
                    fora.append({"variavel": var, "valor": linha[var], "min_treino": lo,
                                 "max_treino": hi, "coeficiente": m["coef"][j]})
        res = m.get("residuo", {}).get(cultura)
        saida["momentos"][mom] = {
            "previsto": total, "faixa": faixa(total, mom, cultura) if res else None,
            "contribuicoes": contrib[:10], "imputadas": imput, "fora_da_faixa": fora,
            "do_sensor": sorted(k for k in linha if k != "base_hist"
                                and k in m["variaveis"])}
    return saida


def semana_na_janela(semana, ini, fim):
    return ini <= semana <= fim if fim >= ini else (semana >= ini or semana <= fim)


def api_configuracoes(_q):
    cfg = ler_sql("""
        SELECT c.id_config, c.data_plantio, c.id_regiao, r.uf, r.cidade, r.cod_estacao,
               cu.nome_cultura, cu.ciclo_estimado_dias, t.nome_solo
          FROM CONFIGURACAO_SAFRA c
          JOIN REGIAO r      ON r.id_regiao  = c.id_regiao
          JOIN CULTURA cu    ON cu.id_cultura = c.id_cultura
          LEFT JOIN TIPO_SOLO t ON t.id_solo = c.id_solo
         ORDER BY c.id_config""")
    ponte = ler_sql("SELECT id_regiao, cod_municipio_ibge, municipio_ibge FROM PONTE_ESTACAO_MUNICIPIO")
    clima = ler_sql("""SELECT id_regiao, MAX(periodo) AS ultimo_periodo, MAX(data_fim) AS clima_ate
                         FROM CLIMA_SEMANAL GROUP BY id_regiao""")
    clima_por = {int(a): (int(b), c) for a, b, c in
                 zip(clima.id_regiao, clima.ultimo_periodo, clima.clima_ate)}
    culturas_pam = EST["culturas_pam"] or []
    saida = []
    for r in cfg.itertuples():
        plantio = r.data_plantio
        ciclo = None if vazio(r.ciclo_estimado_dias) else int(r.ciclo_estimado_dias)
        colheita = (plantio + timedelta(days=ciclo)) if (plantio and ciclo) else None
        # CULTURA guarda 'Soja'; a PAM guarda 'Soja (em grão)'. Casamento explícito.
        nome_pam = f"{r.nome_cultura} (em grão)"
        cultura_tcc = nome_pam if nome_pam in culturas_pam else None
        muns = ponte[ponte.id_regiao == r.id_regiao]
        cods = [int(c) for c in muns.cod_municipio_ibge]
        hist = []
        if cods and cultura_tcc:
            marcas = ",".join(["%s"] * len(cods))
            h = ler_sql(f"""SELECT cod_municipio_ibge, municipio_ibge, safra, rendimento_sacas_ha
                              FROM PRODUTIVIDADE_OBSERVADA
                             WHERE cod_municipio_ibge IN ({marcas}) AND cultura_tcc = %s
                             ORDER BY cod_municipio_ibge, safra""", tuple(cods) + (cultura_tcc,))
            hist = [{"cod": int(a), "municipio": b, "safra": int(c), "sacas_ha": float(d)}
                    for a, b, c, d in zip(h.cod_municipio_ibge, h.municipio_ibge,
                                          h.safra, h.rendimento_sacas_ha)]
        semana = per_plantio = None
        if plantio:
            iso = plantio.isocalendar()
            semana, per_plantio = iso[1], iso[0] * 100 + iso[1]
        janela = None
        J = EST["janelas"]
        if J is not None and cultura_tcc:
            s = J[(J.cultura_tcc == cultura_tcc) & (J.UF == r.uf)]
            if len(s):
                j = s.iloc[0]
                janela = {"semana_inicio": int(j.semana_inicio), "semana_fim": int(j.semana_fim),
                          "observacao": str(j.observacao),
                          "plantio_dentro": (None if semana is None else
                                             semana_na_janela(semana, int(j.semana_inicio),
                                                              int(j.semana_fim)))}
        ult = clima_por.get(int(r.id_regiao))
        hoje = date.today()
        sensor = agregar_sensor(int(r.id_config), plantio, ciclo, hoje)
        prev = previsao_com_sensor(sensor, cultura_tcc, cods, r.uf)

        # "Precisa de mais": tudo o que separa esta conta da conta do treino.
        avisos = []
        if sensor["leituras"] == 0:
            avisos.append("nenhuma leitura do sensor ligada a esta configuração "
                          "(LEITURA_IOT.id_config = %d)" % int(r.id_config))
        for est, e in sensor["estadios"].items():
            nome = {"vegetativo": "vegetativo", "floracao": "floração",
                    "enchimento": "enchimento"}[est]
            if e["valores"]:
                avisos.append(
                    f"{nome}: {e['horas_medidas']} h medidas em {e['dias_com_leitura']} dia(s), "
                    f"de {e['dias_do_estadio']} dias do estádio "
                    f"(cobertura {100 * e['valores']['cobertura']:.0f}%); "
                    f"dias com 18 h ou mais: {e['dias_com_18h']}. No treino, um dia só valia "
                    "com 18 h e a safra só entrava com 80% do ciclo medido")
            elif e["dias_decorridos"] > 0:
                avisos.append(f"{nome}: já começou e não tem nenhuma leitura")
        faltam_est = [est for est, e in sensor["estadios"].items() if not e["valores"]]
        if faltam_est and sensor["leituras"]:
            nomes = {"vegetativo": "vegetativo", "floracao": "floração", "enchimento": "enchimento"}
            avisos.append("sem leitura em " + ", ".join(nomes[x] for x in faltam_est) +
                          ": os momentos correspondentes (M2/M3) não são calculados")
        if sensor["leituras"]:
            avisos.append("o sensor mede só temperatura e umidade; chuva, radiação, vento, "
                          "pressão, dias com chuva, veranico e dias secos (8 das 18 variáveis "
                          "de cada estádio) entram com a mediana do treino")
            avisos.append("o clima das estações do INMET no banco vai só até "
                          + (str(ult[1]) if ult else "—")
                          + ", então não há como completar essas variáveis para esta safra")
        if prev:
            for mom, m in prev["momentos"].items():
                if m["fora_da_faixa"]:
                    avisos.append(f"{mom}: {len(m['fora_da_faixa'])} valor(es) do sensor fora da "
                                  "faixa vista no treino; o modelo trava esses valores no limite")
        if sensor["leituras"]:
            avisos.append("graus-dia do estádio projetados: média diária medida × dias do estádio")
            avisos.append("estádios contados a partir do plantio informado; no treino eram "
                          "frações da janela fixa da cultura na UF")
        if janela is None:
            avisos.append("não há janela de ciclo treinada para esta cultura nesta UF")
        elif janela["plantio_dentro"] is False:
            avisos.append("a semana do plantio fica fora da janela de ciclo usada no treino")
        if sensor["leituras"]:
            avisos.append("o sensor não foi comparado com a estação do INMET; o modelo foi "
                          "treinado só com estações")
        avisos.append("prever a safra em curso antes do fim do ciclo é o esquema temporal, "
                      "em que o modelo perde para o método tradicional")
        no_modelo = []
        if EST["base"] is not None and cultura_tcc:
            b = EST["base"]
            no_modelo = sorted(int(a) for a in
                               b[(b.cod_municipio.isin(cods)) & (b.cultura_tcc == cultura_tcc)].ano_pam)
        saida.append({
            "id_config": int(r.id_config), "uf": r.uf, "estacao": r.cidade,
            "cod_estacao": r.cod_estacao, "cultura": r.nome_cultura, "cultura_pam": cultura_tcc,
            "solo": r.nome_solo, "data_plantio": str(plantio) if plantio else None,
            "ciclo_dias": ciclo, "colheita_estimada": str(colheita) if colheita else None,
            "semana_plantio": semana, "janela": janela,
            "municipios": [{"cod": int(a), "nome": b} for a, b in
                           zip(muns.cod_municipio_ibge, muns.municipio_ibge)],
            "historico_pam": hist, "safras_na_base_do_modelo": no_modelo,
            "clima_ate": str(ult[1]) if ult else None, "hoje": hoje,
            "sensor": sensor, "previsao_sensor": prev, "avisos": avisos})
    return {"configuracoes": saida}


def api_desempenho(_q):
    """Lido de robustez.xlsx na hora; RMSE recalculado aqui."""
    p = pd.read_excel(ARQ_ROBUSTEZ, sheet_name="previsoes")
    y = p["rendimento_sacas_ha"].astype(float)

    def rmse(col, mascara=None):
        ok = p[col].notna() if mascara is None else (p[col].notna() & mascara)
        return float(np.sqrt(((p.loc[ok, col] - y[ok]) ** 2).mean())), int(ok.sum())

    esp_en, n_esp = rmse("prev_esp_en")
    esp_hist, _ = rmse("base_hist")
    esp_idx, _ = rmse("base_indexada")
    tem = p["prev_tmp_en"].notna()
    tmp_en, n_tmp = rmse("prev_tmp_en")
    tmp_hist, _ = rmse("base_hist", tem)
    quebra = pd.read_excel(ARQ_ROBUSTEZ, sheet_name="2_safra_de_quebra")
    return {"fonte": ARQ_ROBUSTEZ + " (aba previsoes; RMSE calculado pelo servidor)",
            "espacial": {"n": n_esp, "elastic_net": esp_en, "media_historica": esp_hist,
                         "media_indexada": esp_idx},
            "temporal": {"n": n_tmp, "elastic_net": tmp_en, "media_historica": tmp_hist},
            "safra_de_quebra": quebra.to_dict(orient="records")}


ROTAS = {"/api/estado": api_estado, "/api/opcoes": api_opcoes,
         "/api/previsao": api_previsao, "/api/configuracoes": api_configuracoes,
         "/api/desempenho": api_desempenho}


def limpar(o):
    if isinstance(o, dict):
        return {str(k): limpar(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [limpar(v) for v in o]
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating, float)):
        f = float(o)
        return None if math.isnan(f) or math.isinf(f) else f
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if hasattr(o, "item"):
        return o.item()
    if o is not None and not isinstance(o, (str, int, bool)):
        return str(o)
    return o


class Manipulador(BaseHTTPRequestHandler):
    def _enviar(self, codigo, corpo, tipo):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        url = urlparse(self.path)
        if url.path in ("/", "/" + PAGINA):
            try:
                with open(PAGINA, "rb") as f:
                    return self._enviar(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                return self._enviar(404, b"previsao.html nao encontrado", "text/plain")
        if url.path == "/api/recarregar":
            carregar_tudo()
            return self._json(200, api_estado({}))
        fn = ROTAS.get(url.path)
        if not fn:
            return self._json(404, {"erro": "rota inexistente"})
        try:
            return self._json(200, fn(parse_qs(url.query)))
        except Exception as e:
            traceback.print_exc()
            return self._json(500, {"erro": f"{type(e).__name__}: {e}"})

    def _json(self, codigo, obj):
        corpo = json.dumps(limpar(obj), ensure_ascii=False).encode("utf-8")
        self._enviar(codigo, corpo, "application/json; charset=utf-8")

    def log_message(self, fmt, *args):
        sys.stderr.write("  " + fmt % args + "\n")


def imprimir_trava():
    t = EST["trava"]
    print("\nTRAVA — o banco reproduz a base do modelo?")
    if EST["erro_banco"]:
        print(f"   banco: FALHOU — {EST['erro_banco']}")
    if EST["erro_modelo"]:
        print(f"   modelo: FALHOU — {EST['erro_modelo']}")
    if not t:
        return
    for k, v in t.items():
        if k not in ("exemplos_base_hist", "faltam_exemplos"):
            print(f"   {k:32s} {v}")
    for e in t["exemplos_base_hist"]:
        print(f"   exemplo base_hist: {e}")
    for f in t["faltam_exemplos"]:
        print(f"   exemplo ausente no banco: {f}")
    print("   RESULTADO: " + ("OK — a página usa exatamente a base do modelo"
                              if t["ok"] else "DIVERGENTE — ver números acima"))


if __name__ == "__main__":
    print("Carregando modelo e banco (somente leitura)...")
    carregar_tudo()
    imprimir_trava()
    if "--conferir" in sys.argv:
        sys.exit(0 if EST["trava"] and EST["trava"]["ok"] else 1)
    srv = ThreadingHTTPServer(("127.0.0.1", PORTA), Manipulador)
    print(f"\nAbra no navegador: http://127.0.0.1:{PORTA}   (Ctrl+C para parar)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nencerrado.")
