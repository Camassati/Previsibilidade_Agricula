# -*- coding: utf-8 -*-
"""Sobe os dados corrigidos do pipeline para o MySQL, sem tocar no IoT.

Versão 2 (26/10/2026). Mudanças em relação à versão de 08/10, todas motivadas
pelo diagnóstico do banco e pelo ensaio da versão anterior:

  1. Estações com código repetido em REGIAO (A713 = IPERO e SOROCABA) eram
     resolvidas em silêncio por um dicionário: uma das duas sumia. Agora o
     script escolhe pela cidade que a planilha associa ao código e ABORTA se
     não conseguir decidir.
  2. No ensaio, as estações que seriam inseridas recebem um id provisório,
     para que as contagens do ensaio sejam as mesmas do --aplicar (antes o
     ensaio descartava 316 linhas de clima que o --aplicar gravaria).
  3. Nada de backup sobrescrito: o nome leva data E hora, e o script aborta
     se a migração já tiver sido aplicada (CLIMA_SEMANAL etc. já existem).
  4. Troca atômica. Os dados novos são gravados primeiro em tabelas
     <nome>__novo, conferidos, e só então um único RENAME TABLE troca tudo
     de uma vez. Se qualquer gravação falhar, as tabelas originais ficam
     intactas. (No MySQL, CREATE/RENAME/DROP não voltam com rollback; o
     RENAME TABLE de várias tabelas num comando só é atômico.)
  5. O ensaio abre a sessão em modo READ ONLY: mesmo que um bug escapasse do
     "if APLICAR", o próprio MySQL recusaria a escrita.
  6. O solo em REGIAO (ALTER TABLE + id_solo) saiu do padrão: a decisão do
     grupo sobre o solo está pendente, e o mapeamento da planilha é um
     fallback por UF (3 tipos para 184 estações). Use --com-solo para incluir.
  7. Ponte: uma linha por par (estação, município), como no notebook do alvo.
     Grafias diferentes da mesma estação para o mesmo município viram uma
     linha (fica o casamento de maior confiança); uma estação ligada a dois
     municípios (A713: Sorocaba e Iperó) mantém os dois.
  8. EXCLUIR_ESTACOES: estações fora do escopo (C891 CRIOSFERA) não entram.

PROTEGIDAS — o script não escreve nelas e confere no fim se continuam iguais:
    SENSOR_IOT, LEITURA_IOT, CONFIGURACAO_SAFRA

O QUE FAZ:
    REGIAO                      insere as estações que faltam (preserva os
                                id_regiao existentes, há FK apontando para eles)
    CLIMA_SEMANAL               cria, com df_tcc_semana.xlsx
    DADOS_CLIMATICOS_HISTORICOS backup + recarga com o mensal derivado do semanal
    PRODUTIVIDADE_OBSERVADA     backup + recarga com a PAM com ano de verdade
    PONTE_ESTACAO_MUNICIPIO     cria, com a aba ponte_municipio
    BASE_MODELO                 cria, com a aba modelo

USO:
    python migrar_banco.py                     ensaio (somente leitura)
    python migrar_banco.py --aplicar           executa de verdade
    python migrar_banco.py --aplicar --com-solo   idem, incluindo id_solo em REGIAO
"""
import sys
import unicodedata
from datetime import datetime

import numpy as np
import pandas as pd

from banco import conectar, engine

ARQ_SEMANA = "df_tcc_semana.xlsx"
ARQ_MODELO = "df_tcc_modelo.xlsx"
PROTEGIDAS = ["SENSOR_IOT", "LEITURA_IOT", "CONFIGURACAO_SAFRA"]
APLICAR = "--aplicar" in sys.argv
COM_SOLO = "--com-solo" in sys.argv
LOTE = 1000
CARIMBO = datetime.now().strftime("%Y%m%d_%H%M%S")
SUF = "__novo"
NOVAS = ["CLIMA_SEMANAL", "PONTE_ESTACAO_MUNICIPIO", "BASE_MODELO"]     # não existem hoje
RECARGA = ["PRODUTIVIDADE_OBSERVADA", "DADOS_CLIMATICOS_HISTORICOS"]    # existem; vão p/ backup
# Estações que não devem entrar no banco. C891 "CRIOSFERA" aparece na planilha
# como SP, mas o nome é o do módulo Criosfera do INMET (fora da área agrícola do
# trabalho) [CONFIRMAR no catálogo de estações do INMET]. Para incluir, esvazie.
EXCLUIR_ESTACOES = {"C891"}


def log(s=""):
    print(s, flush=True)


def contar(cur, tabela):
    cur.execute(f"SELECT COUNT(*) FROM `{tabela}`")
    return cur.fetchone()[0]


def impressao_digital(cur, tabela):
    """Contagem + CHECKSUM TABLE do MySQL + CRC32 de cada linha inteira.
    Qualquer valor alterado, linha nova ou linha apagada muda o resultado —
    não só a contagem. Somente leitura."""
    cur.execute("""SELECT COLUMN_NAME FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                   ORDER BY ORDINAL_POSITION""", (tabela,))
    cols = [r[0] for r in cur.fetchall()]
    linha = ", ".join(f"IFNULL(`{c}`, '<NULL>')" for c in cols)
    cur.execute(f"SELECT COUNT(*), COALESCE(BIT_XOR(CRC32(CONCAT_WS('|', {linha}))), 0), "
                f"COALESCE(SUM(CRC32(CONCAT_WS('|', {linha}))), 0) FROM `{tabela}`")
    n, xor, soma = cur.fetchone()
    cur.execute(f"CHECKSUM TABLE `{tabela}`")
    chk = cur.fetchone()[1]
    return (int(n), int(xor), int(soma), chk)


def exportar_csv(cur, tabela, caminho):
    """Cópia local, linha a linha, antes de qualquer escrita. Somente leitura."""
    import csv
    cur.execute(f"SELECT * FROM `{tabela}`")
    nomes = [d[0] for d in cur.description]
    with open(caminho, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(nomes)
        w.writerows(cur.fetchall())


def conferir_protegidas(cur, antes, momento):
    """Compara a impressão digital atual com a de antes. Devolve True se igual."""
    log(f"\nConferindo as tabelas protegidas ({momento}):")
    ok = True
    for t in PROTEGIDAS:
        agora = impressao_digital(cur, t)
        igual = agora == antes[t]
        ok &= igual
        log(f"   {t:22s} {agora[0]:>7,} linhas  checksum {agora[3]}  "
            f"{'IDÊNTICA' if igual else '<<< MUDOU!'}")
        if not igual:
            log(f"      antes : {antes[t]}")
            log(f"      agora : {agora}")
    return ok


def tabelas_do_banco(cur):
    cur.execute("SHOW TABLES")
    return [r[0] for r in cur.fetchall()]


def existe_coluna(cur, tabela, coluna):
    cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS
                   WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s
                     AND COLUMN_NAME = %s""", (tabela, coluna))
    return cur.fetchone()[0] > 0


def executar(cur, sql, args=None, descricao=None):
    if descricao:
        log(f"   {descricao}")
    if APLICAR:
        cur.execute(sql, args or ())
    return APLICAR


def normalizar(nome):
    """Sem acento, caixa alta, só letras e espaço — para comparar cidades."""
    t = unicodedata.normalize("NFKD", str(nome))
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).upper()
    t = "".join(ch if ch.isalpha() or ch == " " else " " for ch in t)
    return " ".join(t.split())


# --------------------------------------------------------------- leitura ----
log("Lendo as planilhas do pipeline...")
sem = pd.read_excel(ARQ_SEMANA, sheet_name="dados")
xm = pd.ExcelFile(ARQ_MODELO)
modelo = xm.parse("modelo")
pam = xm.parse("pam_bruto")
ponte = xm.parse("ponte_municipio")
log(f"   clima semanal : {len(sem):,} linhas, {sem.Cod_Estacao.nunique()} estações")
log(f"   PAM corrigida : {len(pam):,} linhas, safras {sorted(int(a) for a in pam.ano_pam.dropna().unique())}")
log(f"   base do modelo: {len(modelo):,} linhas")

if EXCLUIR_ESTACOES:
    fora_sem = sem[sem.Cod_Estacao.isin(EXCLUIR_ESTACOES)]
    fora_pte = ponte[ponte.Cod_Estacao.isin(EXCLUIR_ESTACOES)]
    log(f"   excluídas por EXCLUIR_ESTACOES {sorted(EXCLUIR_ESTACOES)}: "
        f"{len(fora_sem):,} linhas de clima, {len(fora_pte)} linha(s) da ponte")
    for r in fora_pte.itertuples():
        log(f"      ponte: {r.Cod_Estacao} {r.UF} {r.Cidade} -> {r.municipio_ibge} ({r.metodo})")
        if pd.notna(r.cod_municipio):
            log("      !!! esta estação TEM município na ponte, então pode ter entrado no")
            log("      !!! modelo. Excluí-la deixaria o banco diferente da base do modelo.")
    sem = sem[~sem.Cod_Estacao.isin(EXCLUIR_ESTACOES)].copy()
    ponte = ponte[~ponte.Cod_Estacao.isin(EXCLUIR_ESTACOES)].copy()

# --------------------------------- semanas partidas na virada do ano ---------
# O ETL agrupou por (ano civil, semana ISO). Uma semana ISO que atravessa 31/12
# aparece duas vezes com o MESMO periodo. Em CLIMA_SEMANAL existe UNIQUE KEY
# (id_regiao, periodo), então os dois trechos são reunidos na semana única que
# de fato são, com a mesma semântica de agregação do resto do pipeline.
SEM_SOMA = ["dias_validos", "graus_dia_acum",
            "precipitacao_total_mm", "dias_com_chuva", "radiacao_total_mjm2"]
# dias_no_periodo é propriedade da semana (7), não do trecho: somar daria 14.
SEM_MAX = ["dias_no_periodo", "temp_max_abs", "maior_veranico",
           "dias_secos_acumulados", "vpd_max_kpa", "vento_rajada_max"]
SEM_MIN = ["temp_min_abs", "umidade_min_abs"]

_chave = ["Cod_Estacao", "periodo"]
_dups = sem[sem.duplicated(_chave, keep=False)]
if len(_dups):
    pares = _dups.groupby(_chave, sort=True).size()
    log(f"   {len(_dups)} linhas em {len(pares)} semanas partidas na virada do "
        f"ano — reunindo em uma linha por semana:")
    for (est, per), n in pares.items():
        log(f"      {est} / {per} ({n} trechos)")

    _num = [c for c in sem.columns
            if pd.api.types.is_numeric_dtype(sem[c]) and c not in ("periodo",)]
    _agg = {}
    for c in sem.columns:
        if c in _chave:
            continue
        if c in SEM_SOMA:
            _agg[c] = lambda x: x.sum(min_count=1)
        elif c in SEM_MAX:
            _agg[c] = "max"
        elif c in SEM_MIN:
            _agg[c] = "min"
        elif c == "data_inicio":
            _agg[c] = "min"
        elif c in ("data_fim", "periodo_label"):
            _agg[c] = "max"
        elif c in _num:
            _agg[c] = "mean"
        else:
            _agg[c] = "first"
    antes_sem = len(sem)
    sem = (sem.groupby(_chave, as_index=False, sort=False).agg(_agg)
              .reindex(columns=sem.columns))
    if {"dias_validos", "dias_no_periodo"} <= set(sem.columns):
        sem["cobertura"] = (sem.dias_validos /
                            sem.dias_no_periodo.replace(0, np.nan))
    log(f"   clima semanal : {antes_sem:,} -> {len(sem):,} linhas")
    assert not sem.duplicated(_chave).any(), "ainda há (estação, periodo) repetido"
else:
    log("   nenhuma semana partida na virada do ano")

# Guarda: a PAM tem que ter ano de verdade, não o código 112 do SIDRA.
anos = set(pd.to_numeric(pam["ano_pam"], errors="coerce").dropna().astype(int))
if not anos or min(anos) < 1990 or max(anos) > 2100:
    raise SystemExit(
        f"ABORTADO: ano_pam fora de faixa ({sorted(anos)[:5]}). Se aparecer 112, "
        "a planilha é a versão anterior à correção do SIDRA — reexecute o "
        "notebook do alvo antes de migrar.")
log(f"   OK: anos da PAM plausíveis ({min(anos)}–{max(anos)})")

# ------------------------------------------------------------ conexão -------
con = conectar()
cur = con.cursor()
if not APLICAR:
    # Garantia do próprio MySQL: nesta sessão, qualquer escrita é recusada.
    cur.execute("SET SESSION TRANSACTION READ ONLY")
    log("\n*** ENSAIO — sessão em modo READ ONLY; nada pode ser gravado. ***")
    log("*** Rode com --aplicar para valer.                              ***")

# ------------------------------------------------------- pré-condições ------
log("\nPré-condições:")
existentes = tabelas_do_banco(cur)
ja = [t for t in NOVAS if t in existentes]
if ja:
    raise SystemExit(
        f"ABORTADO: {ja} já existe(m) — a migração parece já ter sido aplicada. "
        "Nada foi alterado. Rode diagnostico_banco.py antes de qualquer coisa.")
falta = [t for t in RECARGA if t not in existentes]
if falta:
    raise SystemExit(f"ABORTADO: tabela(s) esperada(s) ausente(s): {falta}. Nada foi alterado.")
bkps = {t: f"{t}_bkp_{CARIMBO}" for t in RECARGA}
colide = [b for b in bkps.values() if b in existentes]
if colide:
    raise SystemExit(f"ABORTADO: backup {colide} já existe. Nada foi alterado.")
sobras = [t for t in existentes if t.endswith(SUF)]
if sobras:
    log(f"   tabelas temporárias de uma execução anterior que falhou: {sobras}")
    log("   (são só do script, nunca guardam dado original; serão removidas no --aplicar)")
log("   OK: migração ainda não aplicada; backups terão o sufixo _bkp_" + CARIMBO)

# ------------------------------------------------------ estado protegido ----
log("\nTabelas protegidas — impressão digital (antes):")
antes = {}
for t in PROTEGIDAS:
    antes[t] = impressao_digital(cur, t)
    log(f"   {t:22s} {antes[t][0]:>7,} linhas  checksum {antes[t][3]}  crc {antes[t][1]}")
if APLICAR:
    # Cópia local das protegidas ANTES de qualquer escrita no banco.
    for t in PROTEGIDAS:
        arq = f"backup_{t}_{CARIMBO}.csv"
        exportar_csv(cur, t, arq)
        log(f"   cópia local: {arq}")
    con.commit()

# ------------------------------------------------------------- 1. REGIAO ----
log("\n1. REGIAO")
cidade_na_planilha = (sem.drop_duplicates("Cod_Estacao")
                         .set_index("Cod_Estacao")["Cidade"].map(normalizar).to_dict())


def mapa_estacoes():
    """{cod_estacao: id_regiao}. Código repetido em REGIAO é decidido pela
    cidade que a planilha associa ao código; sem decisão possível, aborta."""
    cur.execute("SELECT id_regiao, uf, cidade, cod_estacao FROM REGIAO")
    por_cod = {}
    for i, uf, cid, cod in cur.fetchall():
        if cod:
            por_cod.setdefault(cod, []).append((i, uf, cid))
    mapa, decididos = {}, []
    for cod, cands in por_cod.items():
        if len(cands) == 1:
            mapa[cod] = cands[0][0]
            continue
        alvo = cidade_na_planilha.get(cod)
        bate = [c for c in cands if alvo and normalizar(c[2]) == alvo]
        if len(bate) != 1:
            raise SystemExit(
                f"ABORTADO: o código {cod} aparece em {len(cands)} linhas de REGIAO "
                f"{cands} e a cidade da planilha ({alvo}) não decide qual é a certa. "
                "Nada foi alterado. Corrija REGIAO antes de migrar.")
        mapa[cod] = bate[0][0]
        decididos.append((cod, bate[0], [c for c in cands if c != bate[0]]))
    return mapa, decididos


reg, decididos = mapa_estacoes()
log(f"   {len(reg)} códigos de estação distintos já cadastrados")
for cod, fica, saem in decididos:
    log(f"   código {cod} repetido: usado id {fica[0]} ({fica[2]}), que é a cidade da")
    log(f"      planilha. Linha(s) que NÃO recebem clima: "
        + ", ".join(f"id {s[0]} ({s[2]})" for s in saem))
    log("      -> o código dessa(s) linha(s) em REGIAO está errado; conferir no INMET.")

est = (sem[["Cod_Estacao", "UF", "Cidade", "id_solo"]]
       .drop_duplicates("Cod_Estacao"))
faltam = est[~est.Cod_Estacao.isin(reg)]
log(f"   {len(faltam)} estações da base semanal ainda não estão no banco:")
for r in faltam.itertuples():
    log(f"      {r.UF}  {r.Cidade}  {r.Cod_Estacao}")
if len(faltam):
    if APLICAR:
        cur.executemany(
            "INSERT INTO REGIAO (uf, cidade, cod_estacao) VALUES (%s, %s, %s)",
            [(r.UF, r.Cidade, r.Cod_Estacao) for r in faltam.itertuples()])
        con.commit()
        reg, _ = mapa_estacoes()
        log(f"   inseridas; agora {len(reg)} códigos")
    else:
        # id provisório (negativo), só para o ensaio contar igual ao --aplicar
        for k, cod in enumerate(faltam.Cod_Estacao, 1):
            reg[cod] = -k
        log("   (ensaio: receberiam id ao inserir; contadas abaixo como se já existissem)")

if COM_SOLO:
    if not existe_coluna(cur, "REGIAO", "id_solo"):
        executar(cur, "ALTER TABLE REGIAO ADD COLUMN id_solo INT UNSIGNED NULL",
                 descricao="acrescenta a coluna id_solo")
        executar(cur, "ALTER TABLE REGIAO ADD CONSTRAINT fk_regiao_solo "
                      "FOREIGN KEY (id_solo) REFERENCES TIPO_SOLO(id_solo)",
                 descricao="acrescenta a chave estrangeira para TIPO_SOLO")
    solos = est.dropna(subset=["id_solo"])
    log(f"   preenche id_solo em {len(solos)} estações "
        f"({solos.id_solo.nunique()} tipos distintos)")
    if APLICAR:
        cur.executemany("UPDATE REGIAO SET id_solo = %s WHERE id_regiao = %s",
                        [(int(r.id_solo), reg[r.Cod_Estacao]) for r in solos.itertuples()])
        con.commit()
else:
    log("   solo: PULADO (decisão do grupo pendente). Use --com-solo para incluir.")

# ------------------------------------------------------ 2. CLIMA_SEMANAL ----
log("\n2. CLIMA_SEMANAL")
NUM = [c for c in sem.columns if c not in
       ("periodo", "periodo_label", "data_inicio", "data_fim", "UF", "Cidade",
        "Cod_Estacao", "id_solo", "nome_solo", "ordem_solo", "textura", "fonte",
        "culturas_predominantes", "retencao_agua", "drenagem", "aptidao_graos",
        "profundidade", "risco_erosao")
       and pd.api.types.is_numeric_dtype(sem[c])]
log(f"   {len(NUM)} colunas climáticas")

clima = sem[["Cod_Estacao", "periodo", "periodo_label", "data_inicio", "data_fim"] + NUM].copy()
clima["id_regiao"] = clima.Cod_Estacao.map(reg)
orfas = int(clima.id_regiao.isna().sum())
if orfas:
    raise SystemExit(f"ABORTADO: {orfas} linhas de clima sem estação em REGIAO. "
                     "Nada foi trocado.")
clima["id_regiao"] = clima.id_regiao.astype(int)
clima = clima.drop(columns="Cod_Estacao")
clima = clima.replace([np.inf, -np.inf], np.nan).where(pd.notna(clima), None)
log(f"   {len(clima):,} linhas prontas para gravar (0 sem estação)")


def ddl_clima(nome):
    cols_sql = ",\n        ".join(f"`{c}` DECIMAL(12,3) NULL" for c in NUM)
    return f"""
    CREATE TABLE `{nome}` (
        id_clima_sem BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        id_regiao    INT UNSIGNED NOT NULL,
        periodo      INT UNSIGNED NOT NULL COMMENT 'ano ISO * 100 + semana ISO',
        periodo_label VARCHAR(10) NOT NULL COMMENT 'ex. 2023-W07',
        data_inicio  DATE NULL,
        data_fim     DATE NULL,
        {cols_sql},
        UNIQUE KEY uq_regiao_periodo (id_regiao, periodo),
        KEY ix_periodo (periodo),
        CONSTRAINT fk_clima_sem_regiao FOREIGN KEY (id_regiao)
            REFERENCES REGIAO(id_regiao)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


# ----------------------------------------- 3. PRODUTIVIDADE_OBSERVADA -------
log("\n3. PRODUTIVIDADE_OBSERVADA")
n_atual = contar(cur, "PRODUTIVIDADE_OBSERVADA")
log(f"   {n_atual:,} linhas atuais (com safra='112') -> backup em "
    f"{bkps['PRODUTIVIDADE_OBSERVADA']}")


def ddl_prod(nome):
    return f"""
    CREATE TABLE `{nome}` (
        id_produtividade  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        cod_municipio_ibge INT UNSIGNED NOT NULL,
        municipio_ibge    VARCHAR(150) NULL,
        uf                CHAR(2) NOT NULL,
        cultura_tcc       VARCHAR(100) NOT NULL,
        safra             SMALLINT UNSIGNED NOT NULL COMMENT 'ano da safra PAM',
        rendimento_kg_ha   DECIMAL(12,2) NULL,
        rendimento_sacas_ha DECIMAL(10,2) NULL,
        UNIQUE KEY uq_mun_cultura_safra (cod_municipio_ibge, cultura_tcc, safra),
        KEY ix_uf_safra (uf, safra)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


SACAS = {"Soja (em grão)": 60, "Milho (em grão)": 60, "Trigo (em grão)": 60}
# pam_bruto traz 'cultura' E 'cultura_tcc'; renomear 'cultura' duplicaria a coluna.
p = pam.drop(columns=[c for c in ("cultura",) if "cultura_tcc" in pam.columns
                      and c in pam.columns]).copy()
renomear = {"cod_municipio": "cod_municipio_ibge", "ano_pam": "safra"}
if "cultura_tcc" not in p.columns:
    renomear["cultura"] = "cultura_tcc"
p = p.rename(columns=renomear)
assert not p.columns.duplicated().any(), \
    f"colunas duplicadas após o rename: {list(p.columns[p.columns.duplicated()])}"
p["uf"] = p["UF"] if "UF" in p.columns else None
p["safra"] = pd.to_numeric(p.safra, errors="coerce").astype("Int64")
p["cod_municipio_ibge"] = pd.to_numeric(p.cod_municipio_ibge, errors="coerce").astype("Int64")
p["rendimento_sacas_ha"] = p.rendimento_kg_ha / p.cultura_tcc.map(SACAS).fillna(60)
p = p.dropna(subset=["cod_municipio_ibge", "safra", "cultura_tcc"])
antes_dedup = len(p)
p = p.drop_duplicates(["cod_municipio_ibge", "cultura_tcc", "safra"])
log(f"   {antes_dedup:,} linhas -> {len(p):,} após remover duplicatas "
    f"({antes_dedup - len(p):,} repetidas)")
log(f"   linhas com rendimento zero: {int((p.rendimento_kg_ha <= 0).sum())}")
prod = p[["cod_municipio_ibge", "municipio_ibge", "uf", "cultura_tcc", "safra",
          "rendimento_kg_ha", "rendimento_sacas_ha"]].where(pd.notna(p), None)

# ------------------------------- 3b. DADOS_CLIMATICOS_HISTORICOS (mensal) ----
log("\n3b. DADOS_CLIMATICOS_HISTORICOS")
# Mensal derivado do semanal: soma para acumulados, média para estados, extremos
# para máx/mín. Cada semana vai para o mês da sua data de início (aproximação:
# uma semana ISO pode atravessar a virada do mês).
MENSAL_SOMA = ["precipitacao_total_mm", "graus_dia_acum", "radiacao_total_mjm2",
               "dias_com_chuva", "dias_validos"]
MENSAL_MEDIA = ["temp_media", "temp_max_media", "temp_min_media", "amplitude_termica",
                "umidade_media", "vpd_medio_kpa", "orvalho_medio", "pressao_media",
                "vento_medio", "vento_dir_predom", "vento_dir_consistencia"]
MENSAL_MAX = ["temp_max_abs", "maior_veranico", "vento_rajada_max"]
MENSAL_MIN = ["temp_min_abs", "umidade_min_abs"]

mes = sem.copy()
mes["ano_mes"] = pd.to_datetime(mes["data_inicio"]).dt.to_period("M").dt.to_timestamp()
agg = {c: (lambda x: x.sum(min_count=1)) for c in MENSAL_SOMA if c in mes.columns}
agg.update({c: "mean" for c in MENSAL_MEDIA if c in mes.columns})
agg.update({c: "max" for c in MENSAL_MAX if c in mes.columns})
agg.update({c: "min" for c in MENSAL_MIN if c in mes.columns})
mensal = mes.groupby(["Cod_Estacao", "ano_mes"], as_index=False).agg(agg)
mensal["dias_no_mes"] = mensal.ano_mes.dt.days_in_month
mensal["cobertura"] = (mensal.dias_validos / mensal.dias_no_mes).round(2).clip(upper=1)
mensal["id_regiao"] = mensal.Cod_Estacao.map(reg)
orfas_m = int(mensal.id_regiao.isna().sum())
if orfas_m:
    raise SystemExit(f"ABORTADO: {orfas_m} linhas mensais sem estação. Nada foi trocado.")
mensal["id_regiao"] = mensal.id_regiao.astype(int)
mensal = mensal.drop(columns="Cod_Estacao")
mensal = mensal.replace([np.inf, -np.inf], np.nan).where(pd.notna(mensal), None)
log(f"   {len(mensal):,} linhas mensais, "
    f"{mes.ano_mes.min():%Y-%m} a {mes.ano_mes.max():%Y-%m}")
n_clima = contar(cur, "DADOS_CLIMATICOS_HISTORICOS")
log(f"   {n_clima:,} linhas atuais -> backup em {bkps['DADOS_CLIMATICOS_HISTORICOS']}")
cols_m = [c for c in mensal.columns if c not in ("id_regiao", "ano_mes")]


def ddl_mensal(nome):
    ddl_m = ",\n        ".join(f"`{c}` DECIMAL(12,3) NULL" for c in cols_m)
    return f"""
    CREATE TABLE `{nome}` (
        id_clima  BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        id_regiao INT UNSIGNED NOT NULL,
        ano_mes   DATE NOT NULL,
        {ddl_m},
        UNIQUE KEY uq_regiao_mes (id_regiao, ano_mes),
        KEY ix_ano_mes (ano_mes),
        CONSTRAINT fk_clima_mes_regiao FOREIGN KEY (id_regiao)
            REFERENCES REGIAO(id_regiao)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


# ------------------------------- 3c. PONTE_ESTACAO_MUNICIPIO ----------------
log("\n3c. PONTE_ESTACAO_MUNICIPIO")
log(f"   aba ponte_municipio: {len(ponte)} linhas | métodos {ponte.metodo.value_counts().to_dict()}")
pt = ponte[ponte.cod_municipio.notna()].copy()
log(f"   {len(ponte) - len(pt)} linhas sem município (SEM CASAMENTO) ficam de fora:")
for r in ponte[ponte.cod_municipio.isna()].itertuples():
    log(f"      {r.Cod_Estacao}  {r.UF}  {r.Cidade}")
pt["id_regiao"] = pt.Cod_Estacao.map(reg)
sem_reg = int(pt.id_regiao.isna().sum())
if sem_reg:
    raise SystemExit(f"ABORTADO: {sem_reg} linhas da ponte sem estação em REGIAO. "
                     "Nada foi trocado.")
pt["id_regiao"] = pt.id_regiao.astype(int)
pt["cod_municipio_ibge"] = pd.to_numeric(pt.cod_municipio, errors="coerce").astype("Int64")
# Uma linha por PAR (estação, município) — igual ao notebook do alvo, que junta
# o clima a TODAS as linhas da ponte da estação. Assim a A713, que a planilha
# associa a Sorocaba e a Iperó, continua alimentando os dois municípios, como
# na base do modelo. Nomes diferentes da mesma estação para o MESMO município
# (grafias do INMET) viram uma linha só, ficando o casamento de maior confiança.
CONFIANCA = {"exato": 0, "abreviacao": 1, "aproximado": 2}
pt["_ordem"] = pt.metodo.map(CONFIANCA).fillna(9)
pt = pt.sort_values(["id_regiao", "cod_municipio_ibge", "_ordem", "escore"],
                    ascending=[True, True, True, False])
par = ["id_regiao", "cod_municipio_ibge"]
repetidas = pt[pt.duplicated(par, keep=False)]
if len(repetidas):
    log(f"   {len(repetidas.groupby(par))} pares (estação, município) com mais de um "
        "nome de estação (fica o primeiro de cada grupo):")
    for r in repetidas.itertuples():
        log(f"      {r.Cod_Estacao}  {r.UF}  {r.Cidade} -> {r.municipio_ibge} "
            f"({r.cod_municipio_ibge}, {r.metodo}, escore {r.escore})")
pt = pt.drop_duplicates(par)
multi = pt[pt.duplicated("id_regiao", keep=False)]
for r in multi.itertuples():
    log(f"   estação ligada a mais de um município: {r.Cod_Estacao} -> "
        f"{r.municipio_ibge} ({r.cod_municipio_ibge})")
pt = pt[["id_regiao", "cod_municipio_ibge", "municipio_ibge", "metodo", "escore"]]
pt = pt.where(pd.notna(pt), None)
log(f"   {len(pt)} associações prontas para gravar")


def ddl_ponte(nome):
    return f"""
    CREATE TABLE `{nome}` (
        id_ponte INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
        id_regiao INT UNSIGNED NOT NULL,
        cod_municipio_ibge INT UNSIGNED NOT NULL,
        municipio_ibge VARCHAR(150) NULL,
        metodo VARCHAR(30) NOT NULL COMMENT 'exato | abreviacao | aproximado',
        escore DECIMAL(5,3) NULL COMMENT 'similaridade, 1 = correspondencia exata',
        UNIQUE KEY uq_regiao_municipio (id_regiao, cod_municipio_ibge),
        KEY ix_municipio (cod_municipio_ibge),
        CONSTRAINT fk_ponte_regiao FOREIGN KEY (id_regiao)
            REFERENCES REGIAO(id_regiao)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"""


# ------------------------------------------------------- 4. BASE_MODELO -----
log("\n4. BASE_MODELO")
base = modelo.copy()
base.columns = [c.replace(" ", "_")[:60] for c in base.columns]
log(f"   {len(base):,} linhas x {len(base.columns)} colunas")

# ----------------------------------------------------- 5. gravação + troca --
ESPERADO = {
    "CLIMA_SEMANAL": len(clima),
    "DADOS_CLIMATICOS_HISTORICOS": len(mensal),
    "PRODUTIVIDADE_OBSERVADA": len(prod),
    "PONTE_ESTACAO_MUNICIPIO": len(pt),
    "BASE_MODELO": len(base),
}
TROCA = (", ".join(f"`{t}` TO `{bkps[t]}`, `{t}{SUF}` TO `{t}`" for t in RECARGA)
         + ", " + ", ".join(f"`{t}{SUF}` TO `{t}`" for t in NOVAS))

log("\n5. GRAVAÇÃO EM TABELAS TEMPORÁRIAS, CONFERÊNCIA E TROCA ATÔMICA")
log("   linhas que serão gravadas:")
for t, n in ESPERADO.items():
    log(f"      {t:30s} {n:>8,}")
log("   troca final, num único comando:")
log("      RENAME TABLE " + TROCA.replace(", ", ",\n                   "))

if APLICAR:
    for t in sobras:
        cur.execute(f"DROP TABLE IF EXISTS `{t}`")
        log(f"   removida sobra anterior: {t}")
    cur.execute(ddl_clima(f"CLIMA_SEMANAL{SUF}"))
    cur.execute(ddl_mensal(f"DADOS_CLIMATICOS_HISTORICOS{SUF}"))
    cur.execute(ddl_prod(f"PRODUTIVIDADE_OBSERVADA{SUF}"))
    cur.execute(ddl_ponte(f"PONTE_ESTACAO_MUNICIPIO{SUF}"))
    con.commit()
    log("\n   Gravando nas temporárias (pode levar alguns minutos)...")
    eng = engine()
    clima.to_sql(f"CLIMA_SEMANAL{SUF}", eng, if_exists="append", index=False,
                 chunksize=LOTE, method="multi")
    log("      CLIMA_SEMANAL ok")
    mensal.to_sql(f"DADOS_CLIMATICOS_HISTORICOS{SUF}", eng, if_exists="append",
                  index=False, chunksize=LOTE, method="multi")
    log("      DADOS_CLIMATICOS_HISTORICOS ok")
    prod.to_sql(f"PRODUTIVIDADE_OBSERVADA{SUF}", eng, if_exists="append",
                index=False, chunksize=LOTE, method="multi")
    log("      PRODUTIVIDADE_OBSERVADA ok")
    pt.to_sql(f"PONTE_ESTACAO_MUNICIPIO{SUF}", eng, if_exists="append",
              index=False, chunksize=LOTE, method="multi")
    log("      PONTE_ESTACAO_MUNICIPIO ok")
    base.to_sql(f"BASE_MODELO{SUF}", eng, if_exists="fail", index=False,
                chunksize=LOTE, method="multi")
    log("      BASE_MODELO ok")
    eng.dispose()

    # A conexão principal ficou parada durante toda a gravação (feita por outra
    # conexão) e o servidor da Locaweb a derruba por inatividade — foi o erro
    # 2013 "Lost connection" da primeira tentativa. Reabre antes de conferir e
    # trocar. A conexão nova também enxerga tudo o que a outra gravou.
    try:
        cur.close()
        con.close()
    except Exception:
        pass
    con = conectar()
    cur = con.cursor()
    log("   (conexão reaberta para a conferência e a troca)")
    log("\n   Conferindo as temporárias antes da troca:")
    erros = []
    for t, n in ESPERADO.items():
        real = contar(cur, f"{t}{SUF}")
        log(f"      {t + SUF:36s} {real:>8,}  {'OK' if real == n else f'ESPERADO {n:,}'}")
        if real != n:
            erros.append(t)
    cur.execute(f"SELECT MIN(safra), MAX(safra) FROM `PRODUTIVIDADE_OBSERVADA{SUF}`")
    smin, smax = cur.fetchone()
    log(f"      safras gravadas: {smin} a {smax}")
    if smin is None or smin < min(anos) or smax > max(anos):
        erros.append("safra")
    # Última trava: se uma protegida tiver mudado por QUALQUER motivo, não troca.
    if not conferir_protegidas(cur, antes, "antes da troca"):
        erros.append("protegida mudou")
    if erros:
        raise SystemExit(
            f"ABORTADO ANTES DA TROCA ({erros}). As tabelas originais NÃO foram "
            f"tocadas. As tabelas *{SUF} ficam para inspeção e serão removidas na "
            "próxima execução.")

    cur.execute("RENAME TABLE " + TROCA)
    con.commit()
    log("\n   TROCA FEITA.")

# --------------------------------------------------------- verificação -----
con.commit()
ok = conferir_protegidas(cur, antes, "depois")

if APLICAR:
    log("\nResumo final:")
    for t in ["REGIAO", "TIPO_SOLO", "CULTURA", "PONTE_ESTACAO_MUNICIPIO",
              "CLIMA_SEMANAL", "DADOS_CLIMATICOS_HISTORICOS",
              "PRODUTIVIDADE_OBSERVADA", "BASE_MODELO"] + list(bkps.values()):
        try:
            log(f"   {t:48s} {contar(cur, t):>8,} linhas")
        except Exception:
            log(f"   {t:48s}        (ausente)")
    cur.execute("SELECT MIN(safra), MAX(safra) FROM PRODUTIVIDADE_OBSERVADA")
    log(f"\n   safras na PRODUTIVIDADE_OBSERVADA: {cur.fetchone()}")
    cur.execute("SELECT MIN(ano_mes), MAX(ano_mes) FROM DADOS_CLIMATICOS_HISTORICOS")
    log(f"   período em DADOS_CLIMATICOS_HISTORICOS: {cur.fetchone()}")
    cur.execute("SELECT MIN(periodo), MAX(periodo) FROM CLIMA_SEMANAL")
    log(f"   período em CLIMA_SEMANAL: {cur.fetchone()}")

cur.close()
con.close()
log("\n" + ("Migração concluída." if APLICAR else
            "Ensaio concluído. Nada foi gravado. Rode com --aplicar para gravar."))
if not ok:
    raise SystemExit("ATENÇÃO: uma tabela protegida mudou. Investigue antes de seguir.")
