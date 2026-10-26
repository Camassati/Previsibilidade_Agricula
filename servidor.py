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
                    CLIMA_SEMANAL, LEITURA_IOT (só leitura; aba "Sensor ao vivo"
                    e clima da Calculadora)
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
import time
import traceback
from datetime import date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np
import pandas as pd

from banco import conectar
from media_historica import calcular_base_hist, pam_padronizada

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
ERROS_DE_REDE = {2003, 2006, 2013, 2055}   # sem conexão / caiu / perdida no meio


def _erro_de_rede(e):
    cod = e.args[0] if getattr(e, "args", None) else None
    return type(e).__name__ == "OperationalError" and cod in ERROS_DE_REDE


def ler_sql(sql, args=None, tentativas=3):
    """SELECT em conexão própria, sessão READ ONLY, fechada no fim."""
    return ler_varios([(sql, args)], tentativas)[0]


def ler_varios(consultas, tentativas=3):
    """Vários SELECT na MESMA conexão (sessão READ ONLY), fechada no fim.
    Se a conexão cair no meio (erro 2013/2006, comum no DBaaS da Locaweb),
    abre outra e repete — só SELECT, então repetir não muda nada no banco."""
    for sql, _ in consultas:
        if not sql.lstrip().upper().startswith("SELECT"):
            raise RuntimeError("servidor só executa SELECT")
    for n in range(1, tentativas + 1):
        con = None
        try:
            con = conectar()
            saida = []
            with con.cursor() as cur:
                cur.execute("SET SESSION TRANSACTION READ ONLY")
                for sql, args in consultas:
                    cur.execute(sql, args or ())
                    cols = [d[0] for d in cur.description]
                    linhas = cur.fetchall()
                    saida.append(pd.DataFrame([list(r) for r in linhas], columns=cols))
            return saida
        except Exception as e:
            if not _erro_de_rede(e) or n == tentativas:
                raise
            espera = 3 * n
            print(f"   conexão caiu ({e.args[0]}); tentando de novo em {espera}s "
                  f"[{n}/{tentativas - 1}]", flush=True)
            time.sleep(espera)
        finally:
            if con is not None:
                try:
                    con.close()
                except Exception:
                    pass


def numerico(df, exceto=()):
    for c in df.columns:
        if c not in exceto:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ========================================================== estado ==========
EST = {"modelo": None, "base": None, "bruto": None, "trava": None, "pam": None,
       "erro_banco": None, "erro_modelo": None, "carregado_em": None,
       "janelas": None, "culturas_pam": None}


def carregar_modelo():
    """UTF-8 é o formato certo. Um JSON gravado pelo treinar_export.py antigo
    no Windows sai em cp1252; nesse caso lê assim mesmo e avisa."""
    bruto = open(ARQ_MODELO, "rb").read()
    try:
        return json.loads(bruto.decode("utf-8"))
    except UnicodeDecodeError:
        print(f"   AVISO: {ARQ_MODELO} não está em UTF-8; lido como cp1252. "
              "Rode o treinar_export.py corrigido para gravá-lo em UTF-8.")
        return json.loads(bruto.decode("cp1252"))


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
    # média histórica: a MESMA função dos scripts de treino (media_historica.py),
    # com o IBGE lido do banco em vez da aba pam_bruto (mesmo conteúdo)
    pam = ler_sql("SELECT uf, cultura_tcc, safra, rendimento_kg_ha FROM PRODUTIVIDADE_OBSERVADA")
    EST["pam"] = pam
    df["base_hist"], df["origem_base_hist"] = calcular_base_hist(df, pam)
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
        # O JSON guarda valores arredondados (2 casas; variáveis com 3). Compara o
        # valor exato do banco com a meia-unidade do arredondamento, para não
        # acusar divergência só porque um x,xx5 desempatou diferente.
        if abs(float(r.rendimento_sacas_ha) - l["real"]) > 0.005 + 1e-6:
            div_real += 1
        if abs(float(r.base_hist) - l["hist"]) > 0.005 + 1e-6:
            div_hist += 1
            if len(exemplos) < 5:
                exemplos.append({"linha": l, "base_hist_banco": float(r.base_hist)})
        for j, c in enumerate(ordem):
            a, b = vals[k][j], r.get(c)
            if a is None and vazio(b):
                continue
            if a is None or vazio(b) or abs(float(b) - a) > 0.0005 + 1e-6:
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
    """LEITURA_IOT -> MÉDIA MENSAL de todos os registros -> estádio.

    Decisão do autor (26/10): o clima de entrada vem SEMPRE do sensor, como a
    média de cada mês sobre todos os registros do mês. Só entram os registros
    dentro do ciclo (do plantio até plantio + ciclo). Para cada estádio, usam-se
    os meses que se sobrepõem ao período do estádio:
      médias (temperatura, umidade, VPD, amplitude diária) -> média dos meses
      extremos (máx/mín)                                   -> máx/mín dos meses
      graus-dia do estádio  = (temp. média - 10 °C) x dias do estádio (projeção)
      cobertura do estádio  = dias com leitura no estádio / dias do estádio
    id_config=None usa todas as leituras do sensor."""
    sql = "SELECT temperatura, umidade_relativa, data_hora, id_config FROM LEITURA_IOT"
    args = None
    if id_config is not None:
        sql += " WHERE id_config = %s"
        args = (id_config,)
    l = ler_sql(sql + " ORDER BY data_hora", args)
    partes = dias_do_estadio(ciclo) if ciclo else {n: [] for n, _, _ in ESTADIOS}
    res = {"leituras": int(len(l)), "primeira": None, "ultima": None,
           "intervalo_mediano_s": None, "fora_do_ciclo": 0, "mensal": [],
           "configs": sorted({int(c) for c in l.id_config}) if len(l) else [],
           "estadios": {}}
    mens, lc = pd.DataFrame(), pd.DataFrame()
    if len(l):
        l = numerico(l, exceto={"data_hora"})
        l["data_hora"] = pd.to_datetime(l["data_hora"])
        res["primeira"], res["ultima"] = l.data_hora.min(), l.data_hora.max()
        gaps = l.data_hora.diff().dt.total_seconds().dropna()
        res["intervalo_mediano_s"] = float(gaps.median()) if len(gaps) else None
        if plantio and ciclo:
            t0 = pd.Timestamp(plantio)
            t1 = t0 + pd.Timedelta(days=ciclo)
            dentro = (l.data_hora >= t0) & (l.data_hora < t1)
            res["fora_do_ciclo"] = int((~dentro).sum())
            lc = l[dentro].copy()
        if len(lc):
            lc["vpd"] = (pressao_vapor_saturacao(lc.temperatura)
                         * (1 - lc.umidade_relativa / 100)).clip(lower=0)
            lc["mes"] = lc.data_hora.dt.to_period("M")
            lc["dia"] = lc.data_hora.dt.normalize()
            lc["hora"] = lc.data_hora.dt.floor("h")
            dia = lc.groupby("dia").agg(mes=("mes", "first"), tmax=("temperatura", "max"),
                                        tmin=("temperatura", "min"), horas=("hora", "nunique"))
            dia["amp"] = dia.tmax - dia.tmin
            mens = lc.groupby("mes").agg(
                leituras=("temperatura", "size"), dias=("dia", "nunique"),
                temp_media=("temperatura", "mean"), temp_max=("temperatura", "max"),
                temp_min=("temperatura", "min"), umidade_media=("umidade_relativa", "mean"),
                umidade_min=("umidade_relativa", "min"), vpd_medio=("vpd", "mean"),
                vpd_max=("vpd", "max"))
            mens["amplitude_media"] = dia.groupby("mes")["amp"].mean()
            res["mensal"] = [dict(mes=str(m), **{k: (float(v) if k not in ("leituras", "dias") else int(v))
                                                for k, v in r.items()})
                             for m, r in mens.iterrows()]
    for nome, _, _ in ESTADIOS:
        idx = partes[nome]
        total = len(idx)
        inicio = (plantio + timedelta(days=idx[0])) if (plantio and idx) else None
        fim = (plantio + timedelta(days=idx[-1])) if (plantio and idx) else None
        decorridos = 0 if not inicio else max(0, min(total, (hoje - inicio).days + 1))
        e = {"inicio": inicio, "fim": fim, "dias_do_estadio": total,
             "dias_decorridos": decorridos, "dias_com_leitura": 0, "dias_com_18h": 0,
             "horas_medidas": 0, "leituras": 0, "meses": [], "valores": None}
        if inicio and len(lc):
            no_est = lc[(lc.dia >= pd.Timestamp(inicio)) & (lc.dia <= pd.Timestamp(fim))]
            e["leituras"] = int(len(no_est))
            e["dias_com_leitura"] = int(no_est.dia.nunique())
            e["horas_medidas"] = int(no_est.hora.nunique())
            if len(no_est):
                hd = no_est.groupby("dia")["hora"].nunique()
                e["dias_com_18h"] = int((hd >= MIN_HORAS_DIA).sum())
            meses_est = set(pd.period_range(pd.Timestamp(inicio), pd.Timestamp(fim), freq="M"))
            ms = mens[mens.index.isin(meses_est)]
            if len(ms) and len(no_est):
                tm = float(ms.temp_media.mean())
                e["meses"] = [str(m) for m in ms.index]
                e["valores"] = {
                    "temp_media": tm,
                    "temp_max_abs": float(ms.temp_max.max()),
                    "temp_min_abs": float(ms.temp_min.min()),
                    "amplitude_termica": float(ms.amplitude_media.mean()),
                    "graus_dia_acum": max(0.0, tm - TEMP_BASE_GDD) * total,
                    "umidade_media": float(ms.umidade_media.mean()),
                    "umidade_min_abs": float(ms.umidade_min.min()),
                    "vpd_medio_kpa": float(ms.vpd_medio.mean()),
                    "vpd_max_kpa": float(ms.vpd_max.max()),
                    "cobertura": e["dias_com_leitura"] / total if total else 0.0,
                }
        res["estadios"][nome] = e
    return res


def base_hist_ao_vivo(cods, cultura, uf):
    """Média histórica para uma safra nova, com a regra de media_historica.py:
    safras anteriores do município na base; reserva: UF nas safras anteriores
    do IBGE; depois a cultura. Para uma safra em curso, todas são anteriores."""
    b = EST["base"]
    s = b[b.cod_municipio.isin(cods) & (b.cultura_tcc == cultura)]
    if len(s):
        return float(s.rendimento_sacas_ha.mean()), f"média das {len(s)} safras do município na base do modelo"
    p = pam_padronizada(EST["pam"])
    s = p[(p.UF == uf) & (p.cultura_tcc == cultura)]
    if len(s):
        return float(s.sacas.mean()), "média da UF no IBGE (município sem safra na base)"
    s = p[p.cultura_tcc == cultura]
    return (float(s.sacas.mean()) if len(s) else None), "média da cultura no IBGE"


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
            avisos.append("clima do sensor como média mensal de todos os registros; graus-dia do "
                          "estádio projetados: (temperatura média − 10 °C) × dias do estádio")
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


# ======================================================= calculadora ========
KG_SACA = 60   # 1 saca = 60 kg, como em todo o pipeline
ARQ_AJUSTE = "ajuste_solo_cultura.csv"
# Critérios de terreno que o ZARC não indica para soja, milho e trigo
# (portarias SPA/MAPA, ex. nº 370/2023 e nº 385/2022): independem da classe.
ZARC_TERRENO = {
    "raso": "solo de profundidade inferior a 50 cm",
    "pedregoso": "solo muito pedregoso (calhaus e matacões em mais de 15% da massa ou da superfície)",
}


def ajustes_solo():
    """Tabela de ajuste agronômico por solo x cultura. FICA FORA DO MODELO:
    é aplicada depois da previsão. Lida do CSV a cada chamada, para o grupo
    poder editar sem reiniciar o servidor."""
    pasta = os.path.dirname(os.path.abspath(__file__))
    caminho = os.path.join(pasta, ARQ_AJUSTE)
    if not os.path.exists(caminho):
        return {}
    t = pd.read_csv(caminho, sep=";", dtype=str, encoding="utf-8-sig").fillna("")
    out = {}
    for r in t.itertuples():
        out[(int(r.id_solo), r.cultura_tcc)] = {
            "situacao": r.situacao.strip() or "apto",
            "ajuste_pct": float(r.ajuste_pct.replace(",", ".") or 0),
            "motivo": r.motivo.strip(), "fonte": r.fonte.strip()}
    return out


def api_solos(_q):
    t = ler_sql("""SELECT id_solo, nome_solo, ordem_solo, textura, retencao_agua, drenagem,
                          aptidao_graos, profundidade, risco_erosao, descricao
                     FROM TIPO_SOLO ORDER BY id_solo""")
    aj = ajustes_solo()
    sol = t.to_dict(orient="records")
    for s_ in sol:
        s_["por_cultura"] = {c: aj.get((int(s_["id_solo"]), c), {
                                 "situacao": "sem_regra", "ajuste_pct": 0.0,
                                 "motivo": f"sem linha em {ARQ_AJUSTE}", "fonte": ""})
                             for c in FAIXAS_SACAS}
    return {"solos": sol, "zarc_terreno": ZARC_TERRENO, "arquivo_ajuste": ARQ_AJUSTE}


def api_calcular(q):
    """Formulário do produtor -> produtividade estimada e produção da área.

    Clima: SEMPRE do sensor (LEITURA_IOT), como média mensal de todos os
    registros (decisão do autor, 26/10). Os meses dentro do ciclo, contado a
    partir do plantio informado, alimentam os estádios; o modelo usado é o do
    último estádio que tem leitura (M1, M2 ou M3). Variáveis que o sensor não
    mede entram com a mediana do treino. O solo NÃO entra no modelo."""
    exigir_base()
    cod, cult = int(q["m"][0]), q["c"][0]
    unidade = q.get("unidade", ["ha"])[0]
    area = float(str(q.get("area", ["0"])[0]).replace(",", "."))
    if area <= 0:
        raise RuntimeError("informe uma área maior que zero")
    if not q.get("plantio", [""])[0]:
        raise RuntimeError("informe a data de plantio: é ela que diz em que estádio cada mês do sensor cai")
    # Solo é obrigatório (decisão do autor, 26/10), e cada solo x cultura
    # precisa ter a sua linha na tabela de ajuste — sem regra, não calcula.
    if not q.get("solo", [""])[0]:
        raise RuntimeError("informe o tipo de solo: ele é obrigatório")
    id_solo = int(q["solo"][0])
    if not len(ler_sql("SELECT id_solo FROM TIPO_SOLO WHERE id_solo = %s", (id_solo,))):
        raise RuntimeError(f"tipo de solo {id_solo} não existe na tabela TIPO_SOLO")
    regra = ajustes_solo().get((id_solo, cult))
    if regra is None:
        raise RuntimeError(f"o solo {id_solo} não tem regra para {cult} em {ARQ_AJUSTE}; "
                           "cadastre a linha (situacao, ajuste_pct, motivo, fonte) e calcule de novo")
    ha = area / 10000 if unidade == "m2" else area
    b = EST["base"]
    sub = b[(b.cod_municipio == cod) & (b.cultura_tcc == cult)]
    if len(sub):
        nome, uf = sub.municipio_ibge.iloc[0], sub.UF.iloc[0]
    else:
        raise RuntimeError("município fora da base do modelo")
    avisos, realizado = [], None
    plantio_cfg = date.fromisoformat(q["plantio"][0])
    cu = ler_sql("SELECT nome_cultura, ciclo_estimado_dias FROM CULTURA")
    ciclos = {f"{n} (em grão)": d for n, d in zip(cu.nome_cultura, cu.ciclo_estimado_dias)}
    if cult not in ciclos or vazio(ciclos[cult]):
        raise RuntimeError("cultura sem ciclo_estimado_dias na tabela CULTURA")
    sensor = agregar_sensor(None, plantio_cfg, int(ciclos[cult]), date.today())
    estadios_com = [e for e in ("vegetativo", "floracao", "enchimento")
                    if sensor["estadios"][e]["valores"]]
    if not estadios_com:
        return {"sem_dados": True, "municipio": nome, "uf": uf, "cultura": cult,
                "sensor": sensor,
                "motivo": "nenhum registro do sensor cai dentro do ciclo que começa no plantio "
                          "informado; ajuste a data de plantio ou ligue o sensor"}
    mom = MOMENTO_DO_ESTADIO[estadios_com[-1]]
    linha = {}
    for est in estadios_com:
        for v, x in sensor["estadios"][est]["valores"].items():
            linha[f"{est}_{v}"] = x
    hist, origem_hist = base_hist_ao_vivo([cod], cult, uf)
    linha["base_hist"] = hist
    meses = sorted({m for est in estadios_com for m in sensor["estadios"][est]["meses"]})
    clima = (f"sensor (LEITURA_IOT): média mensal de todos os registros de "
             f"{', '.join(meses)} · modelo {mom} ({ROTULO_MOMENTO[mom]})")
    faltam = [e for e in ("vegetativo", "floracao", "enchimento") if e not in estadios_com]
    if faltam:
        nomes_est = {"vegetativo": "vegetativo", "floracao": "floração", "enchimento": "enchimento"}
        avisos.append("estádios sem registro do sensor: " + ", ".join(nomes_est[x] for x in faltam) +
                      f"; por isso a estimativa usa o modelo {mom}")
    for est in estadios_com:
        e = sensor["estadios"][est]
        cob = f"{100 * e['valores']['cobertura']:.1f}".replace(".", ",")
        nome_e = {"vegetativo": "vegetativo", "floracao": "floração", "enchimento": "enchimento"}[est]
        avisos.append(f"{nome_e}: {e['leituras']} registros em {e['dias_com_leitura']} dia(s) de "
                      f"{e['dias_do_estadio']} do estádio (cobertura {cob}%)")
    if sensor["configs"]:
        avisos.append("os registros do sensor estão ligados à(s) configuração(ões) "
                      + ", ".join(str(c) for c in sensor["configs"])
                      + "; confira se o sensor estava no município escolhido")
    avisos.append("um mês que cruza dois estádios entra nos dois; graus-dia projetados como "
                  "(temperatura média − 10 °C) × dias do estádio")
    # ---- solo e terreno: bloqueio (ZARC) antes de qualquer conta
    terreno = [ZARC_TERRENO[k] for k in ZARC_TERRENO if q.get(k, ["0"])[0] in ("1", "true", "on")]
    if (regra and regra["situacao"] == "nao_indicado") or terreno:
        motivos = ([regra["motivo"]] if regra and regra["situacao"] == "nao_indicado" else []) + \
                  [f"o ZARC não indica áreas com {t}" for t in terreno]
        fontes = []
        if regra and regra["situacao"] == "nao_indicado" and regra["fonte"]:
            fontes.append(regra["fonte"])
        if terreno:
            fontes.append("Portarias ZARC (SPA/MAPA), seção Tipos de Solos Aptos ao Cultivo")
        return {"bloqueado": True, "municipio": nome, "uf": uf, "cultura": cult,
                "motivos": motivos, "fonte": " · ".join(fontes) or "—"}

    total, contrib, imput, aus = prever(linha, mom)
    fx = faixa(total, mom, cult)
    pct = regra["ajuste_pct"] if regra else 0.0
    fator = 1 + pct / 100

    def producao(sc):
        return {"sacas_ha": sc, "sacas": sc * ha, "kg": sc * ha * KG_SACA,
                "toneladas": sc * ha * KG_SACA / 1000}

    solo = None
    if q.get("solo", [""])[0]:
        t = ler_sql("SELECT * FROM TIPO_SOLO WHERE id_solo = %s", (int(q["solo"][0]),))
        if len(t):
            solo = t.iloc[0].to_dict()
    avisos.insert(0, "o modelo foi treinado só com clima e histórico do município, sem solo; "
                     "o ajuste de solo é uma regra aplicada DEPOIS da previsão, definida na "
                     f"tabela {ARQ_AJUSTE}")
    if regra["situacao"] in ("aviso", "apto") and regra["motivo"]:
        avisos.insert(1, f"solo: {regra['motivo']}")
    if pct and not (regra or {}).get("fonte"):
        avisos.insert(1, f"o ajuste de {pct:+.0f}% para este solo NÃO TEM FONTE na tabela; "
                         "é um parâmetro a validar antes de usar no TCC")
    colheita = ciclo = None
    if q.get("plantio", [""])[0]:
        plantio = date.fromisoformat(q["plantio"][0])
        cu = ler_sql("SELECT nome_cultura, ciclo_estimado_dias FROM CULTURA")
        mapa = {f"{n} (em grão)": d for n, d in zip(cu.nome_cultura, cu.ciclo_estimado_dias)}
        if cult in mapa and not vazio(mapa[cult]):
            ciclo = int(mapa[cult])
            colheita = plantio + timedelta(days=ciclo)
    if imput:
        avisos.append(f"{len(imput)} variável(is) com peso no modelo não são medidas pelo sensor "
                      "(chuva, radiação, vento, pressão, veranicos) e entraram com a mediana do treino")
    avisos.append("a faixa vem dos erros reais do modelo fora da amostra (municípios que ele "
                  "não viu); para prever a PRÓXIMA safra o modelo perde para a média histórica")
    return {"municipio": nome, "uf": uf, "cultura": cult, "clima": clima,
            "area_informada": area, "unidade": unidade, "hectares": ha,
            "estimativa_modelo": producao(total),
            "estimativa": producao(total * fator),
            "faixa": {k: producao(v * fator) for k, v in fx.items()},
            "ajuste_solo": {"pct": pct, "fonte": (regra or {}).get("fonte", ""),
                            "situacao": (regra or {}).get("situacao", "apto")},
            "tradicional": producao(hist), "origem_tradicional": origem_hist,
            "realizado": producao(realizado) if realizado is not None else None,
            "contribuicoes": contrib[:8], "solo": solo, "colheita": colheita,
            "ciclo_dias": ciclo, "avisos": avisos, "momento": mom,
            "mensal": sensor["mensal"],
            "estadios": {k: {kk: v[kk] for kk in ("inicio", "fim", "meses", "dias_com_leitura",
                                                  "dias_do_estadio", "leituras", "valores")}
                         for k, v in sensor["estadios"].items()},
            "sensor_resumo": {k: sensor[k] for k in ("leituras", "primeira", "ultima", "fora_do_ciclo", "configs")}}


# ===================================================== sensor ao vivo =======
AO_VIVO_S = 30          # última leitura há até 30 s  -> "ao vivo" (o nó grava a cada 5 s)
ATRASADO_S = 300        # até 5 min                  -> "atrasado"; acima disso, "parado"
MAX_LINHAS_POLL = 5000  # teto por consulta, para nunca puxar a tabela inteira


def _txt_hora(v):
    return None if v is None else pd.Timestamp(v).strftime("%Y-%m-%d %H:%M:%S")


def api_sensor_ao_vivo(q):
    """Acompanhamento do sensor. Só lê LEITURA_IOT (SELECT, sessão READ ONLY).
    desde=0  -> leituras dos últimos `janela` minutos ATÉ A ÚLTIMA LEITURA
                (mostra algo mesmo com o sensor parado);
    desde=N  -> só as leituras com id_leitura > N (consulta incremental pela PK).
    A idade da última leitura é calculada com o relógio do MySQL (NOW()), o
    mesmo que grava data_hora — não depende do fuso do computador."""
    desde = int(q.get("desde", ["0"])[0] or 0)
    janela = max(1, min(int(q.get("janela", ["60"])[0] or 60), 1440))
    cols = "id_leitura, id_sensor, id_config, temperatura, umidade_relativa, data_hora"
    if desde > 0:
        sql_l = (f"SELECT {cols} FROM LEITURA_IOT WHERE id_leitura > %s "
                 f"ORDER BY id_leitura LIMIT {MAX_LINHAS_POLL}")
        args_l = (desde,)
    else:
        sql_l = (f"SELECT {cols} FROM LEITURA_IOT WHERE data_hora >= "
                 f"(SELECT MAX(data_hora) FROM LEITURA_IOT) - INTERVAL %s MINUTE "
                 f"ORDER BY id_leitura LIMIT {MAX_LINHAS_POLL}")
        args_l = (janela,)
    est, lei, dia, cfg = ler_varios([
        ("""SELECT COUNT(*) AS total, MAX(id_leitura) AS ultimo_id,
                   MAX(data_hora) AS ultima, NOW() AS agora_banco,
                   TIMESTAMPDIFF(SECOND, MAX(data_hora), NOW()) AS idade_s
              FROM LEITURA_IOT""", None),
        (sql_l, args_l),
        ("""SELECT DATE(data_hora) AS dia, COUNT(*) AS leituras,
                   AVG(temperatura) AS temp_media, MIN(temperatura) AS temp_min,
                   MAX(temperatura) AS temp_max, AVG(umidade_relativa) AS umid_media,
                   MIN(umidade_relativa) AS umid_min, MAX(umidade_relativa) AS umid_max
              FROM LEITURA_IOT
             WHERE data_hora >= DATE((SELECT MAX(data_hora) FROM LEITURA_IOT))
             GROUP BY DATE(data_hora)""", None),
        ("""SELECT c.id_config, r.cidade, r.uf, cu.nome_cultura
              FROM CONFIGURACAO_SAFRA c
              JOIN REGIAO r   ON r.id_regiao  = c.id_regiao
              JOIN CULTURA cu ON cu.id_cultura = c.id_cultura
             WHERE c.id_config = (SELECT id_config FROM LEITURA_IOT
                                   ORDER BY id_leitura DESC LIMIT 1)""", None),
    ])
    e = est.iloc[0]
    total = int(e.total or 0)
    idade = None if e.idade_s is None or vazio(e.idade_s) else int(e.idade_s)
    if total == 0:
        situacao = "sem_leituras"
    elif idade is not None and idade <= AO_VIVO_S:
        situacao = "ao_vivo"
    elif idade is not None and idade <= ATRASADO_S:
        situacao = "atrasado"
    else:
        situacao = "parado"
    leituras = []
    if len(lei):
        lei = numerico(lei, exceto={"data_hora"})
        t = lei.temperatura.astype(float)
        vpd = pressao_vapor_saturacao(t) * (1 - lei.umidade_relativa.astype(float) / 100)
        for r, v in zip(lei.itertuples(), vpd):
            leituras.append({"id": int(r.id_leitura), "sensor": int(r.id_sensor),
                             "config": int(r.id_config), "hora": _txt_hora(r.data_hora),
                             "temp": None if vazio(r.temperatura) else float(r.temperatura),
                             "umid": None if vazio(r.umidade_relativa) else float(r.umidade_relativa),
                             "vpd": None if vazio(v) else float(v)})
    resumo_dia = None
    if len(dia):
        d = numerico(dia, exceto={"dia"}).iloc[0]
        resumo_dia = {"dia": str(d.dia), "leituras": int(d.leituras),
                      **{k: float(d[k]) for k in ("temp_media", "temp_min", "temp_max",
                                                  "umid_media", "umid_min", "umid_max")}}
    local = None
    if len(cfg):
        c = cfg.iloc[0]
        local = {"config": int(c.id_config), "cidade": c.cidade, "uf": c.uf,
                 "cultura": c.nome_cultura}
    return {"situacao": situacao, "idade_s": idade, "total": total,
            "ultimo_id": None if vazio(e.ultimo_id) else int(e.ultimo_id),
            "ultima": _txt_hora(e.ultima), "agora_banco": _txt_hora(e.agora_banco),
            "janela_min": janela, "incremental": desde > 0,
            "truncado": len(leituras) >= MAX_LINHAS_POLL,
            "leituras": leituras, "dia": resumo_dia, "local": local,
            "limites": {"ao_vivo_s": AO_VIVO_S, "atrasado_s": ATRASADO_S}}


ROTAS = {"/api/estado": api_estado, "/api/opcoes": api_opcoes,
         "/api/sensor": api_sensor_ao_vivo,
         "/api/solos": api_solos, "/api/calcular": api_calcular,
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
            # procura ao lado do servidor.py, não na pasta de onde ele foi chamado
            pasta = os.path.dirname(os.path.abspath(__file__))
            caminho = os.path.join(pasta, PAGINA)
            if os.path.exists(caminho):
                with open(caminho, "rb") as f:
                    return self._enviar(200, f.read(), "text/html; charset=utf-8")
            htmls = sorted(n for n in os.listdir(pasta) if n.lower().endswith(".html"))
            msg = (f"{PAGINA} nao encontrado em {pasta}\n\n"
                   f"Arquivos .html nesta pasta: {htmls or 'nenhum'}\n\n"
                   f"Salve o previsao.html nesta pasta, com exatamente esse nome, "
                   f"e recarregue a pagina.")
            return self._enviar(404, msg.encode("utf-8"), "text/plain; charset=utf-8")
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
        linha = fmt % args
        # a aba "Sensor ao vivo" consulta a cada 5 s: só registra se der erro
        if "/api/sensor" in linha and '" 200 ' in linha:
            return
        sys.stderr.write("  " + linha + "\n")


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
    _pag = os.path.join(os.path.dirname(os.path.abspath(__file__)), PAGINA)
    if not os.path.exists(_pag):
        print(f"   AVISO: {PAGINA} não está em {os.path.dirname(_pag)} — a página não vai abrir.")
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
