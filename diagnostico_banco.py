# -*- coding: utf-8 -*-
"""Diagnóstico do banco do TCC — SOMENTE LEITURA.

Responde, com dados, três perguntas antes de qualquer decisão:
  1. O que de fato existe no banco hoje (tabelas, contagens, períodos,
     defeitos conhecidos e não conhecidos)?
  2. Os dados que o banco tem bastam para o modelo, ou falta algo?
  3. É preciso reprocessar o histórico (ETL do INMET, PAM do SIDRA), ou as
     planilhas locais já servem de origem para corrigir o banco?

Segurança:
  - A sessão é aberta em modo READ ONLY e a função de consulta recusa
    qualquer comando que não seja SELECT ou SHOW. Nada é gravado no banco.
  - A credencial vem do banco_config.ini via banco.py e NÃO é impressa.

Uso (na pasta do projeto, ao lado de banco.py e banco_config.ini):
    python diagnostico_banco.py                 banco + planilhas locais
    python diagnostico_banco.py --sem-planilhas só o banco (mais rápido)

Saída: diagnostico_banco.txt — é esse arquivo que deve ser enviado de volta.
"""
import argparse
import os
import statistics
import sys
import unicodedata
from datetime import datetime

from banco import conectar

ESPERADAS = ["REGIAO", "TIPO_SOLO", "CULTURA", "CONFIGURACAO_SAFRA",
             "SENSOR_IOT", "LEITURA_IOT", "DADOS_CLIMATICOS_HISTORICOS",
             "PRODUTIVIDADE_OBSERVADA"]
# Tabelas que migrar_banco.py CRIARIA. Se aparecerem, a migração rodou
# (inteira ou em parte) — o diagnóstico precisa saber disso.
DA_MIGRACAO = ["CLIMA_SEMANAL", "PONTE_ESTACAO_MUNICIPIO", "BASE_MODELO"]

linhas = []


def p(s=""):
    print(s)
    linhas.append(str(s))


def secao(titulo):
    p()
    p("=" * 78)
    p(titulo)
    p("=" * 78)


def q(cur, sql, args=None):
    """Executa só leitura. Qualquer outra coisa é bloqueada aqui."""
    s = sql.lstrip().upper()
    if not s.startswith(("SELECT", "SHOW", "SET SESSION TRANSACTION READ ONLY")):
        raise RuntimeError(f"comando bloqueado (diagnóstico é só leitura): {sql[:60]}")
    cur.execute(sql, args or ())
    cols = [d[0] for d in cur.description] if cur.description else []
    return cols, list(cur.fetchall()) if cur.description else []


def fmt(v):
    if v is None:
        return "NULL"
    if isinstance(v, float):
        return f"{v:.3f}"
    return str(v)


def tabela(cols, rows, largura_max=42, limite=None):
    if not cols:
        return
    total = len(rows)
    if limite and total > limite:
        rows = rows[:limite]
    txt = [[fmt(v).replace("\n", " ")[:largura_max] for v in r] for r in rows]
    larg = [max([len(c)] + [len(r[i]) for r in txt]) for i, c in enumerate(cols)]
    p("  " + "  ".join(c.ljust(larg[i]) for i, c in enumerate(cols)))
    p("  " + "  ".join("-" * l for l in larg))
    for r in txt:
        p("  " + "  ".join(r[i].ljust(larg[i]) for i in range(len(cols))))
    if not total:
        p("  (nenhuma linha)")
    elif limite and total > limite:
        p(f"  ... {total - limite} linhas omitidas (total {total})")


def consulta(cur, titulo, sql, args=None, **kw):
    p()
    p(f"-- {titulo}")
    try:
        cols, rows = q(cur, sql, args)
        tabela(cols, rows, **kw)
        return cols, rows
    except Exception as e:
        p(f"  FALHOU: {type(e).__name__}: {e}")
        return [], []


def colunas_de(cur, t):
    _, rows = q(cur, """SELECT COLUMN_NAME FROM information_schema.COLUMNS
                        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                        ORDER BY ORDINAL_POSITION""", (t,))
    return [r[0] for r in rows]


def nulos(cur, t):
    """Quantos NULL há em cada coluna. Mostra só colunas com algum nulo."""
    cols = colunas_de(cur, t)
    partes = ", ".join(f"SUM(`{c}` IS NULL)" for c in cols)
    try:
        _, rows = q(cur, f"SELECT COUNT(*), {partes} FROM `{t}`")
    except Exception as e:
        p(f"  FALHOU: {e}")
        return
    total, *ns = rows[0]
    p()
    p(f"-- nulos por coluna em {t} (total {total} linhas; colunas sem nulo omitidas)")
    achou = False
    for c, n in zip(cols, ns):
        n = int(n or 0)
        if n:
            achou = True
            pct = 100 * n / total if total else 0
            p(f"  {c:34s} {n:>8,} ({pct:5.1f}%)")
    if not achou:
        p("  nenhuma coluna com nulo")


def normalizar(nome):
    """Mesma ideia do notebook do alvo: sem acento, caixa alta, sem ' - UF'."""
    t = unicodedata.normalize("NFKD", str(nome))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).upper()
    t = t.split(" - ")[0]
    t = "".join(ch if ch.isalpha() or ch == " " else " " for ch in t)
    return " ".join(t.split())


# =============================================================== banco ======
def diagnosticar_banco():
    with conectar() as con, con.cursor() as cur:
        q(cur, "SET SESSION TRANSACTION READ ONLY")

        # ------------------------------------------------------------ geral
        secao("1. VISÃO GERAL")
        _, r = q(cur, "SELECT VERSION(), DATABASE(), NOW()")
        p(f"  MySQL {r[0][0]} | banco {r[0][1]} | hora do servidor {r[0][2]}")
        _, r = q(cur, "SHOW TABLES")
        tabelas = [x[0] for x in r]
        p(f"  {len(tabelas)} tabelas: {', '.join(tabelas)}")
        faltam = [t for t in ESPERADAS if t not in tabelas]
        extras = [t for t in tabelas if t not in ESPERADAS]
        p(f"  esperadas e ausentes : {faltam or 'nenhuma'}")
        p(f"  presentes e não esperadas: {extras or 'nenhuma'}")
        migr = [t for t in DA_MIGRACAO if t in tabelas]
        p(f"  tabelas que só a migração criaria: {migr or 'NENHUMA -> migração não aplicada'}")

        consulta(cur, "tamanho, motor e colação de cada tabela", """
            SELECT TABLE_NAME, ENGINE, TABLE_ROWS AS linhas_aprox,
                   ROUND((DATA_LENGTH + INDEX_LENGTH) / 1024 / 1024, 2) AS mb,
                   TABLE_COLLATION, CREATE_TIME, UPDATE_TIME
              FROM information_schema.TABLES
             WHERE TABLE_SCHEMA = DATABASE() ORDER BY TABLE_NAME""")
        p("  (TABLE_ROWS é estimativa do InnoDB; as contagens exatas vêm abaixo)")

        p()
        p("-- contagem exata")
        for t in tabelas:
            _, r = q(cur, f"SELECT COUNT(*) FROM `{t}`")
            p(f"  {t:40s} {r[0][0]:>10,}")

        consulta(cur, "chaves estrangeiras declaradas", """
            SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME,
                   REFERENCED_COLUMN_NAME, CONSTRAINT_NAME
              FROM information_schema.KEY_COLUMN_USAGE
             WHERE TABLE_SCHEMA = DATABASE() AND REFERENCED_TABLE_NAME IS NOT NULL
             ORDER BY TABLE_NAME, COLUMN_NAME""")
        consulta(cur, "índices e chaves únicas", """
            SELECT TABLE_NAME, INDEX_NAME, NON_UNIQUE,
                   GROUP_CONCAT(COLUMN_NAME ORDER BY SEQ_IN_INDEX) AS colunas
              FROM information_schema.STATISTICS
             WHERE TABLE_SCHEMA = DATABASE()
             GROUP BY TABLE_NAME, INDEX_NAME, NON_UNIQUE
             ORDER BY TABLE_NAME, INDEX_NAME""")
        consulta(cur, "gatilhos (triggers) — onde uma validação de domínio poderia estar",
                 """SELECT TRIGGER_NAME, EVENT_MANIPULATION, EVENT_OBJECT_TABLE,
                           ACTION_TIMING
                      FROM information_schema.TRIGGERS
                     WHERE TRIGGER_SCHEMA = DATABASE()""")
        consulta(cur, "visões (views)", """
            SELECT TABLE_NAME FROM information_schema.VIEWS
             WHERE TABLE_SCHEMA = DATABASE()""")

        secao("2. DDL COMPLETA (SHOW CREATE TABLE)")
        p("  Observação: o MySQL 5.7 descarta cláusulas CHECK ao criar a tabela,")
        p("  então elas NÃO aparecem aqui mesmo que tenham sido escritas. Se não")
        p("  houver trigger acima, não há validação de domínio no banco.")
        for t in tabelas:
            _, r = q(cur, f"SHOW CREATE TABLE `{t}`")
            p()
            p(f"-- {t}")
            for l in r[0][1].splitlines():
                p("  " + l)

        # ------------------------------------------------ produtividade
        if "PRODUTIVIDADE_OBSERVADA" in tabelas:
            T = "PRODUTIVIDADE_OBSERVADA"
            secao(f"3. {T} — o alvo do modelo")
            consulta(cur, "valores distintos de safra", f"""
                SELECT safra, COUNT(*) AS linhas FROM {T}
                 GROUP BY safra ORDER BY safra""", limite=40)
            consulta(cur, "culturas gravadas", f"""
                SELECT cultura_tcc, COUNT(*) AS linhas FROM {T}
                 GROUP BY cultura_tcc ORDER BY cultura_tcc""")
            consulta(cur, "por UF e cultura", f"""
                SELECT uf, cultura_tcc, COUNT(*) AS linhas,
                       COUNT(DISTINCT cod_municipio_ibge) AS municipios,
                       ROUND(MIN(rendimento_sacas_ha), 1) AS min_sc,
                       ROUND(AVG(rendimento_sacas_ha), 1) AS media_sc,
                       ROUND(MAX(rendimento_sacas_ha), 1) AS max_sc
                  FROM {T} GROUP BY uf, cultura_tcc ORDER BY uf, cultura_tcc""")
            consulta(cur, "quantas linhas cada (município, cultura) tem — "
                          "se o ano se perdeu, isto mostra quantos anos há escondidos", f"""
                SELECT n AS linhas_por_mun_cultura, COUNT(*) AS combinacoes
                  FROM (SELECT cod_municipio_ibge, cultura_tcc, COUNT(*) AS n
                          FROM {T} GROUP BY cod_municipio_ibge, cultura_tcc) x
                 GROUP BY n ORDER BY n""")
            consulta(cur, "linhas EXATAMENTE repetidas (mesmo município, cultura, "
                          "safra e rendimento)", f"""
                SELECT COUNT(*) AS grupos_repetidos,
                       COALESCE(SUM(c - 1), 0) AS linhas_excedentes
                  FROM (SELECT COUNT(*) AS c FROM {T}
                         GROUP BY cod_municipio_ibge, cultura_tcc, safra,
                                  rendimento_kg_ha
                        HAVING c > 1) x""")
            consulta(cur, "faixa de id_produtividade (lacunas indicam apagamentos "
                          "ou cargas múltiplas)", f"""
                SELECT MIN(id_produtividade) AS min_id, MAX(id_produtividade) AS max_id,
                       COUNT(*) AS linhas,
                       MAX(id_produtividade) - MIN(id_produtividade) + 1 - COUNT(*)
                           AS ids_faltando
                  FROM {T}""")
            consulta(cur, "conferência kg/ha -> sacas/ha (60 kg por saca)", f"""
                SELECT SUM(ABS(rendimento_kg_ha / 60 - rendimento_sacas_ha) > 0.01)
                           AS linhas_que_nao_batem,
                       SUM(rendimento_kg_ha <= 0) AS rendimento_zero_ou_negativo
                  FROM {T}""")
            _, top = consulta(cur, "as 5 combinações (município, cultura) com mais linhas", f"""
                SELECT cod_municipio_ibge, municipio_ibge, cultura_tcc, COUNT(*) AS n
                  FROM {T} GROUP BY cod_municipio_ibge, municipio_ibge, cultura_tcc
                 ORDER BY n DESC LIMIT 5""")
            amostras = [(5100102, "Milho (em grão)")]   # a do inventário (Acorizal)
            if top:
                amostras.insert(0, (top[0][0], top[0][2]))
            for cod, cult in amostras:
                consulta(cur, f"linhas de {cod} / {cult}, na ordem de inserção "
                              "(a ordem do id revela a ordem dos anos?)", f"""
                    SELECT id_produtividade, safra, rendimento_kg_ha, rendimento_sacas_ha
                      FROM {T} WHERE cod_municipio_ibge = %s AND cultura_tcc = %s
                     ORDER BY id_produtividade""", (cod, cult), limite=40)
            nulos(cur, T)

        # ------------------------------------------------ clima
        if "DADOS_CLIMATICOS_HISTORICOS" in tabelas:
            T = "DADOS_CLIMATICOS_HISTORICOS"
            secao(f"4. {T} — o clima que está no banco")
            consulta(cur, "período coberto", f"""
                SELECT MIN(ano_mes) AS primeiro_mes, MAX(ano_mes) AS ultimo_mes,
                       COUNT(*) AS linhas, COUNT(DISTINCT id_regiao) AS estacoes
                  FROM {T}""")
            consulta(cur, "por ano", f"""
                SELECT YEAR(ano_mes) AS ano, COUNT(*) AS linhas,
                       COUNT(DISTINCT id_regiao) AS estacoes,
                       ROUND(AVG(cobertura), 3) AS cobertura_media,
                       SUM(cobertura < 0.8) AS meses_cobertura_menor_80
                  FROM {T} GROUP BY YEAR(ano_mes) ORDER BY ano""")
            consulta(cur, "por UF", f"""
                SELECT r.uf, COUNT(*) AS linhas, COUNT(DISTINCT d.id_regiao) AS estacoes,
                       MIN(d.ano_mes) AS de, MAX(d.ano_mes) AS ate
                  FROM {T} d JOIN REGIAO r ON r.id_regiao = d.id_regiao
                 GROUP BY r.uf ORDER BY r.uf""")
            consulta(cur, "(estação, mês) repetidos", f"""
                SELECT COUNT(*) AS grupos_repetidos FROM
                  (SELECT id_regiao, ano_mes FROM {T}
                    GROUP BY id_regiao, ano_mes HAVING COUNT(*) > 1) x""")
            consulta(cur, "estações de REGIAO sem nenhum mês de clima", f"""
                SELECT r.uf, COUNT(*) AS estacoes_sem_clima
                  FROM REGIAO r LEFT JOIN {T} d ON d.id_regiao = r.id_regiao
                 WHERE d.id_clima IS NULL GROUP BY r.uf ORDER BY r.uf""")
            consulta(cur, "id_regiao no clima que não existe em REGIAO (órfãos)", f"""
                SELECT COUNT(*) AS linhas_orfas FROM {T} d
                  LEFT JOIN REGIAO r ON r.id_regiao = d.id_regiao
                 WHERE r.id_regiao IS NULL""")
            consulta(cur, "valores fisicamente suspeitos", f"""
                SELECT SUM(precipitacao_total_mm < 0) AS chuva_negativa,
                       SUM(graus_dia_acum = 0) AS graus_dia_zero,
                       SUM(radiacao_total_mjm2 = 0) AS radiacao_zero,
                       SUM(temp_max_abs > 50) AS tmax_acima_50,
                       SUM(temp_min_abs < -15) AS tmin_abaixo_menos15,
                       SUM(umidade_media > 100 OR umidade_media < 0) AS umidade_fora,
                       SUM(cobertura > 1) AS cobertura_acima_1
                  FROM {T}""")
            nulos(cur, T)
            p()
            p("-- colunas que o modelo usa e que NÃO existem nesta tabela")
            cols = set(colunas_de(cur, T))
            precisa = ["precipitacao_total_mm", "graus_dia_acum", "radiacao_total_mjm2",
                       "dias_com_chuva", "maior_veranico", "dias_secos_acumulados",
                       "temp_media", "temp_max_abs", "temp_min_abs", "amplitude_termica",
                       "umidade_media", "umidade_min_abs", "vpd_medio_kpa", "vpd_max_kpa",
                       "pressao_media", "vento_medio", "vento_rajada_max", "cobertura"]
            ausentes = [c for c in precisa if c not in cols]
            p(f"  {ausentes or 'nenhuma — todas presentes'}")
            p("  (lista = AGREGACAO_CICLO do notebook alvo_produtividade_5)")

        # ------------------------------------------------ regiao
        if "REGIAO" in tabelas:
            secao("5. REGIAO — estações")
            consulta(cur, "por UF", """
                SELECT uf, COUNT(*) AS estacoes, SUM(cod_estacao IS NULL) AS sem_codigo
                  FROM REGIAO GROUP BY uf ORDER BY uf""")
            consulta(cur, "cod_estacao repetido", """
                SELECT cod_estacao, COUNT(*) AS n FROM REGIAO
                 WHERE cod_estacao IS NOT NULL
                 GROUP BY cod_estacao HAVING n > 1""")
            consulta(cur, "lista completa (é a base de uma ponte estação -> município)",
                     "SELECT * FROM REGIAO ORDER BY uf, cidade", limite=400)

            if "PRODUTIVIDADE_OBSERVADA" in tabelas:
                p()
                p("-- quantas estações casam com um município da PAM só pelo nome")
                p("   (normalização simples: sem acento, caixa alta; sem abreviação nem")
                p("    aproximação — o notebook do alvo faz mais que isto)")
                _, reg = q(cur, "SELECT uf, cidade FROM REGIAO")
                _, mun = q(cur, """SELECT DISTINCT uf, municipio_ibge
                                     FROM PRODUTIVIDADE_OBSERVADA""")
                nomes = {(u, normalizar(m)) for u, m in mun}
                casam = [(u, c) for u, c in reg if (u, normalizar(c)) in nomes]
                nao = [(u, c) for u, c in reg if (u, normalizar(c)) not in nomes]
                p(f"  casam direto : {len(casam)} de {len(reg)}")
                p(f"  não casam    : {len(nao)}")
                for u, c in sorted(nao)[:60]:
                    p(f"     {u}  {c}")
                if len(nao) > 60:
                    p(f"     ... e mais {len(nao) - 60}")

        # ------------------------------------------------ catálogos
        secao("6. CATÁLOGOS E CONFIGURAÇÃO")
        if "CULTURA" in tabelas:
            consulta(cur, "CULTURA", "SELECT * FROM CULTURA")
        if "TIPO_SOLO" in tabelas:
            consulta(cur, "TIPO_SOLO (completo — pendência do seletor de solo)",
                     "SELECT * FROM TIPO_SOLO ORDER BY id_solo", largura_max=90)
        if "CONFIGURACAO_SAFRA" in tabelas:
            consulta(cur, "CONFIGURACAO_SAFRA com os nomes resolvidos", """
                SELECT c.id_config, c.data_plantio, r.id_regiao, r.uf, r.cidade,
                       r.cod_estacao, cu.nome_cultura, cu.ciclo_estimado_dias,
                       DATE_ADD(c.data_plantio, INTERVAL cu.ciclo_estimado_dias DAY)
                           AS colheita_por_ciclo, t.nome_solo
                  FROM CONFIGURACAO_SAFRA c
                  LEFT JOIN REGIAO r ON r.id_regiao = c.id_regiao
                  LEFT JOIN CULTURA cu ON cu.id_cultura = c.id_cultura
                  LEFT JOIN TIPO_SOLO t ON t.id_solo = c.id_solo""")
        if "SENSOR_IOT" in tabelas:
            consulta(cur, "SENSOR_IOT", "SELECT * FROM SENSOR_IOT")

        # ------------------------------------------------ IoT
        if "LEITURA_IOT" in tabelas:
            T = "LEITURA_IOT"
            secao(f"7. {T} — leituras do nó sensor")
            consulta(cur, "janela e chaves", f"""
                SELECT COUNT(*) AS leituras, MIN(data_hora) AS primeira,
                       MAX(data_hora) AS ultima,
                       COUNT(DISTINCT id_sensor) AS sensores,
                       COUNT(DISTINCT id_config) AS configs
                  FROM {T}""")
            consulta(cur, "por dia", f"""
                SELECT DATE(data_hora) AS dia, COUNT(*) AS leituras,
                       ROUND(MIN(temperatura), 1) AS tmin, ROUND(MAX(temperatura), 1) AS tmax,
                       ROUND(MIN(umidade_relativa), 1) AS umin,
                       ROUND(MAX(umidade_relativa), 1) AS umax
                  FROM {T} GROUP BY DATE(data_hora) ORDER BY dia""", limite=60)
            consulta(cur, "fora da faixa do DHT22 (temp -40..80 °C, umidade 0..100 %)", f"""
                SELECT SUM(temperatura < -40 OR temperatura > 80) AS temp_fora,
                       SUM(umidade_relativa < 0 OR umidade_relativa > 100) AS umid_fora
                  FROM {T}""")
            _, r = q(cur, f"SELECT data_hora FROM {T} ORDER BY data_hora")
            ts = [x[0] for x in r if x[0] is not None]
            if len(ts) > 1:
                gaps = [(b - a).total_seconds() for a, b in zip(ts, ts[1:])]
                p()
                p("-- intervalo entre leituras consecutivas (medido)")
                p(f"  mediana {statistics.median(gaps):.1f} s | mínimo {min(gaps):.1f} s"
                  f" | máximo {max(gaps):.1f} s")
                p(f"  intervalos > 60 s (falhas de coleta): {sum(g > 60 for g in gaps)}")
                p(f"  intervalos = 0 s (leitura duplicada): {sum(g == 0 for g in gaps)}")
            nulos(cur, T)

        # ------------------------------------------------ extras
        for t in extras:
            secao(f"8. TABELA NÃO ESPERADA: {t}")
            consulta(cur, "amostra", f"SELECT * FROM `{t}` LIMIT 5", largura_max=30)


# ============================================================ planilhas ======
def diagnosticar_planilhas():
    secao("9. ARQUIVOS LOCAIS — servem de origem para corrigir o banco?")
    p(f"  pasta: {os.getcwd()}")
    arquivos = ["df_tcc_semana.xlsx", "df_tcc_modelo.xlsx", "dados_observados.xlsx",
                "modelo_export.json", "res_final.json", "coef_final.json",
                "robustez.xlsx", "sensor.json", "calculadora_safra.html",
                "migrar_banco.py", "treinar_export.py"]
    for a in arquivos:
        if os.path.exists(a):
            st = os.stat(a)
            p(f"  OK   {a:28s} {st.st_size / 1024:>10,.0f} KB  "
              f"modificado {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M}")
        else:
            p(f"  --   {a:28s} AUSENTE")
    for d in ["inmet_historico", os.path.join("arquivos para bd tcc", "inmet_historico"),
              "cache_diario"]:
        if os.path.isdir(d):
            n = sum(len(f) for _, _, f in os.walk(d))
            p(f"  OK   pasta {d}: {n} arquivos")
        else:
            p(f"  --   pasta {d}: não encontrada aqui")

    try:
        import pandas as pd
    except ImportError:
        p("  pandas não instalado — conteúdo das planilhas não verificado")
        return

    if os.path.exists("df_tcc_semana.xlsx"):
        p()
        p("-- df_tcc_semana.xlsx (aba 'dados') — origem de CLIMA_SEMANAL")
        try:
            s = pd.read_excel("df_tcc_semana.xlsx", sheet_name="dados")
            p(f"  {len(s):,} linhas | {s['Cod_Estacao'].nunique()} estações | "
              f"periodo {s['periodo'].min()} a {s['periodo'].max()}")
            p(f"  UFs: {s['UF'].value_counts().to_dict()}")
            dup = int(s.duplicated(['Cod_Estacao', 'periodo'], keep=False).sum())
            p(f"  linhas em (estação, periodo) repetidos: {dup}  "
              "(semanas partidas na virada do ano — armadilha A6)")
            tem = [c for c in ["vpd_max_kpa", "dias_secos_acumulados", "vpd_medio_kpa"]
                   if c in s.columns]
            p(f"  colunas que faltam no mensal do banco e estão aqui: {tem}")
        except Exception as e:
            p(f"  FALHOU: {type(e).__name__}: {e}")

    if os.path.exists("df_tcc_modelo.xlsx"):
        p()
        p("-- df_tcc_modelo.xlsx — origem de PRODUTIVIDADE_OBSERVADA e da ponte")
        try:
            x = pd.ExcelFile("df_tcc_modelo.xlsx")
            p(f"  abas: {x.sheet_names}")
            if "pam_bruto" in x.sheet_names:
                pam = x.parse("pam_bruto")
                anos = sorted(pd.to_numeric(pam["ano_pam"], errors="coerce")
                              .dropna().astype(int).unique())
                p(f"  pam_bruto: {len(pam):,} linhas | anos {anos}")
                p("  ANO DE VERDADE (não 112)? "
                  + ("SIM" if anos and min(anos) > 1990 else "NÃO — planilha antiga"))
            if "modelo" in x.sheet_names:
                m = x.parse("modelo")
                p(f"  modelo: {len(m):,} linhas x {m.shape[1]} colunas | "
                  f"{m['cod_municipio'].nunique()} municípios | "
                  f"safras {sorted(m['ano_pam'].unique())}")
            if "ponte_municipio" in x.sheet_names:
                pt = x.parse("ponte_municipio")
                p(f"  ponte_municipio: {len(pt)} linhas | "
                  f"métodos {pt['metodo'].value_counts().to_dict()}")
        except Exception as e:
            p(f"  FALHOU: {type(e).__name__}: {e}")

    if os.path.exists("dados_observados.xlsx"):
        p()
        p("-- dados_observados.xlsx — o realizado do IBGE, separado")
        try:
            o = pd.read_excel("dados_observados.xlsx", sheet_name="observados")
            p(f"  {len(o):,} linhas | safras {sorted(o['safra'].unique())} | "
              f"{o['cod_municipio'].nunique()} municípios")
            p("  (compare o total com as linhas de PRODUTIVIDADE_OBSERVADA no banco)")
        except Exception as e:
            p(f"  FALHOU: {type(e).__name__}: {e}")


# ================================================================ main ======
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sem-planilhas", action="store_true",
                    help="não lê as planilhas locais (mais rápido)")
    ap.add_argument("--saida", default="diagnostico_banco.txt")
    args = ap.parse_args()

    p(f"DIAGNÓSTICO DO BANCO — gerado em {datetime.now():%Y-%m-%d %H:%M:%S}")
    p("Somente leitura. Nenhuma credencial é impressa neste arquivo.")
    try:
        diagnosticar_banco()
    except Exception as e:
        p(f"\nFALHOU ao consultar o banco: {type(e).__name__}: {e}")
        p("Causas comuns: banco_config.ini ausente ou com senha antiga; IP não")
        p("liberado no painel do DBaaS; falta 'pip install pymysql'.")
    if not args.sem_planilhas:
        diagnosticar_planilhas()

    with open(args.saida, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    print(f"\n>>> Diagnóstico gravado em {args.saida} — envie este arquivo.")
