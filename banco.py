# -*- coding: utf-8 -*-
"""Acesso ao banco MySQL do TCC.

Centraliza a conexão para que os notebooks não precisem repetir credencial.
As credenciais ficam em `banco_config.ini`, que NÃO deve ir para o GitHub nem
ser anexado ao TCC — se o repositório for público, a senha vaza junto.

Uso:
    from banco import conectar, ler_tabela, gravar_tabela

    df = ler_tabela("df_tcc_modelo")
    gravar_tabela(df, "df_tcc_modelo")

Para inspecionar o que existe no banco:
    python banco.py
"""
import configparser
import os
import sys

import pandas as pd

CONFIG = "banco_config.ini"


def _credenciais():
    """Lê de banco_config.ini; variáveis de ambiente têm precedência."""
    cfg = {"host": os.environ.get("TCC_DB_HOST"),
           "user": os.environ.get("TCC_DB_USER"),
           "password": os.environ.get("TCC_DB_PASS"),
           "database": os.environ.get("TCC_DB_NAME"),
           "port": int(os.environ.get("TCC_DB_PORT", 3306))}
    if all(cfg[k] for k in ("host", "user", "password", "database")):
        return cfg

    if not os.path.exists(CONFIG):
        raise FileNotFoundError(
            f"{CONFIG} não encontrado. Crie o arquivo com:\n\n"
            "[mysql]\nhost = ...\nuser = ...\npassword = ...\n"
            "database = ...\nport = 3306\n")
    p = configparser.ConfigParser()
    p.read(CONFIG, encoding="utf-8")
    s = p["mysql"]
    return {"host": s["host"], "user": s["user"], "password": s["password"],
            "database": s["database"], "port": s.getint("port", fallback=3306)}


def conectar():
    """Conexão pymysql. Lembre de fechar, ou use `with conectar() as c:`."""
    import pymysql
    c = _credenciais()
    return pymysql.connect(host=c["host"], user=c["user"], password=c["password"],
                           database=c["database"], port=c["port"],
                           charset="utf8mb4", connect_timeout=30,
                           cursorclass=pymysql.cursors.Cursor)


def engine():
    """Engine SQLAlchemy, que é o que o pandas prefere para to_sql."""
    from sqlalchemy import create_engine
    from urllib.parse import quote_plus
    c = _credenciais()
    return create_engine(
        f"mysql+pymysql://{c['user']}:{quote_plus(c['password'])}"
        f"@{c['host']}:{c['port']}/{c['database']}?charset=utf8mb4",
        pool_pre_ping=True)


def listar_tabelas():
    with conectar() as c, c.cursor() as k:
        k.execute("SHOW TABLES")
        return [r[0] for r in k.fetchall()]


def ler_tabela(nome, limite=None):
    sql = f"SELECT * FROM `{nome}`" + (f" LIMIT {int(limite)}" if limite else "")
    return pd.read_sql(sql, engine())


def gravar_tabela(df, nome, se_existir="replace", lote=1000):
    """Grava o DataFrame numa tabela. `se_existir`: replace | append | fail."""
    df.to_sql(nome, engine(), if_exists=se_existir, index=False,
              chunksize=lote, method="multi")
    print(f"{nome}: {len(df)} linhas gravadas")


# ----------------------------------------------------------- inspeção -------
def inventario(saida="banco_inventario.txt", amostra=3):
    """Varre o banco e escreve um inventário legível: tabelas, colunas, tipos,
    contagem de linhas e uma amostra. É o que eu preciso ver para adaptar o
    pipeline ao que de fato está lá."""
    linhas = []

    def p(s=""):
        print(s)
        linhas.append(str(s))

    with conectar() as c, c.cursor() as k:
        k.execute("SELECT VERSION(), DATABASE()")
        versao, banco = k.fetchone()
        p(f"MySQL {versao} | banco: {banco}")
        k.execute("SHOW TABLES")
        tabelas = [r[0] for r in k.fetchall()]
        p(f"{len(tabelas)} tabelas\n")

        for t in tabelas:
            k.execute(f"SELECT COUNT(*) FROM `{t}`")
            n = k.fetchone()[0]
            p("=" * 72)
            p(f"TABELA {t}  —  {n:,} linhas")
            p("=" * 72)

            k.execute(f"DESCRIBE `{t}`")
            cols = k.fetchall()
            p(f"{len(cols)} colunas:")
            for nome, tipo, nulo, chave, padrao, extra in cols:
                marca = f" [{chave}]" if chave else ""
                p(f"   {nome:34s} {tipo:22s} {'NULL' if nulo == 'YES' else 'NOT NULL':9s}{marca}")

            # chaves estrangeiras, que é o que diz se há integridade referencial
            k.execute("""
                SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s
                  AND REFERENCED_TABLE_NAME IS NOT NULL""", (banco, t))
            fks = k.fetchall()
            if fks:
                p("chaves estrangeiras:")
                for col, rt, rc in fks:
                    p(f"   {col} -> {rt}.{rc}")
            else:
                p("chaves estrangeiras: nenhuma")

            if n and amostra:
                k.execute(f"SELECT * FROM `{t}` LIMIT {int(amostra)}")
                nomes = [d[0] for d in k.description]
                p(f"amostra ({amostra} linhas):")
                for r in k.fetchall():
                    par = ", ".join(f"{a}={b!r}" for a, b in list(zip(nomes, r))[:8])
                    p(f"   {par}" + (" ..." if len(nomes) > 8 else ""))
            p()

    with open(saida, "w", encoding="utf-8") as f:
        f.write("\n".join(linhas))
    print(f"\n>>> Inventário gravado em {saida}")
    return tabelas


if __name__ == "__main__":
    try:
        inventario()
    except Exception as e:
        print(f"\nFALHOU: {type(e).__name__}: {e}\n", file=sys.stderr)
        print("Causas comuns:", file=sys.stderr)
        print("  - banco_config.ini ausente ou com credencial errada", file=sys.stderr)
        print("  - o DBaaS exige liberar o seu IP no painel antes de aceitar conexão",
              file=sys.stderr)
        print("  - falta instalar: pip install pymysql sqlalchemy pandas", file=sys.stderr)
        sys.exit(1)
