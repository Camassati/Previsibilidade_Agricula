# -*- coding: utf-8 -*-
"""Exporta as leituras do nó sensor para sensor.json, que a calculadora lê.

A página publicada não alcança o MySQL — o navegador do artefato só faz HTTPS
para domínios liberados, e o banco fala protocolo binário na porta 3306. Então
o caminho é este: você roda isto na sua máquina, que enxerga o banco, e o JSON
gerado acompanha a página.

    python exportar_sensor.py              # grava sensor.json
    python exportar_sensor.py --limite 500 # só as N leituras mais recentes

Para manter atualizado, agende no Agendador de Tarefas do Windows.
"""
import argparse
import json
import sys
from datetime import datetime

import pandas as pd

from banco import conectar

AMOSTRAS_SERIE = 240   # pontos no gráfico; o resto vira resumo


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limite", type=int, default=5000,
                    help="leituras mais recentes a considerar (padrão 5000)")
    ap.add_argument("--saida", default="sensor.json")
    args = ap.parse_args()

    con = conectar()
    # Traz a leitura junto do contexto: qual sensor, qual safra, qual região.
    sql = """
        SELECT l.id_leitura, l.temperatura, l.umidade_relativa, l.data_hora,
               s.modelo, s.fabricante, s.protocolo_comunicacao,
               s.frequencia_amostragem_min, s.status_operacional,
               r.uf, r.cidade, r.cod_estacao,
               c.data_plantio, cu.nome_cultura, t.nome_solo
          FROM LEITURA_IOT l
          JOIN SENSOR_IOT s          ON s.id_sensor  = l.id_sensor
          JOIN CONFIGURACAO_SAFRA c  ON c.id_config  = l.id_config
          LEFT JOIN REGIAO r         ON r.id_regiao  = c.id_regiao
          LEFT JOIN CULTURA cu       ON cu.id_cultura = c.id_cultura
          LEFT JOIN TIPO_SOLO t      ON t.id_solo    = c.id_solo
         ORDER BY l.data_hora DESC
         LIMIT %s"""
    df = pd.read_sql(sql, con, params=(args.limite,))
    con.close()

    if df.empty:
        sys.exit("ABORTADO: LEITURA_IOT não devolveu nenhuma linha.")

    df = df.sort_values("data_hora")
    df["temperatura"] = pd.to_numeric(df.temperatura, errors="coerce")
    df["umidade_relativa"] = pd.to_numeric(df.umidade_relativa, errors="coerce")

    ult = df.iloc[-1]
    inicio, fim = df.data_hora.iloc[0], df.data_hora.iloc[-1]
    duracao_s = (fim - inicio).total_seconds()

    # Intervalo REAL entre leituras, medido — não o que a coluna
    # frequencia_amostragem_min declara. As duas podem divergir, e a página
    # mostra as duas justamente para que a divergência apareça.
    intervalos = df.data_hora.diff().dt.total_seconds().dropna()
    intervalo_medido = float(intervalos.median()) if len(intervalos) else None

    # Reamostra a série para caber na página sem perder a forma.
    passo = max(1, len(df) // AMOSTRAS_SERIE)
    s = df.iloc[::passo]

    out = {
        "gerado_em": datetime.now().isoformat(timespec="seconds"),
        "sensor": {
            "modelo": ult.modelo,
            "fabricante": ult.fabricante,
            "protocolo": ult.protocolo_comunicacao,
            "frequencia_declarada_min": (None if pd.isna(ult.frequencia_amostragem_min)
                                         else int(ult.frequencia_amostragem_min)),
            "intervalo_medido_s": (None if intervalo_medido is None
                                   else round(intervalo_medido, 1)),
            "status": ult.status_operacional,
        },
        "local": {
            "uf": ult.uf, "cidade": ult.cidade, "cod_estacao": ult.cod_estacao,
            "cultura": ult.nome_cultura, "solo": ult.nome_solo,
            "data_plantio": (None if pd.isna(ult.data_plantio)
                             else str(ult.data_plantio)),
        },
        "janela": {
            "inicio": inicio.isoformat(timespec="seconds"),
            "fim": fim.isoformat(timespec="seconds"),
            "leituras": int(len(df)),
            "duracao_horas": round(duracao_s / 3600, 2),
        },
        "ultima": {
            "temperatura": (None if pd.isna(ult.temperatura) else float(ult.temperatura)),
            "umidade": (None if pd.isna(ult.umidade_relativa) else float(ult.umidade_relativa)),
            "quando": ult.data_hora.isoformat(timespec="seconds"),
        },
        "resumo": {
            "temp_media": round(float(df.temperatura.mean()), 2),
            "temp_min": round(float(df.temperatura.min()), 2),
            "temp_max": round(float(df.temperatura.max()), 2),
            "umid_media": round(float(df.umidade_relativa.mean()), 2),
            "umid_min": round(float(df.umidade_relativa.min()), 2),
            "umid_max": round(float(df.umidade_relativa.max()), 2),
        },
        "serie": [
            {"t": r.data_hora.isoformat(timespec="seconds"),
             "temp": (None if pd.isna(r.temperatura) else round(float(r.temperatura), 2)),
             "umid": (None if pd.isna(r.umidade_relativa) else round(float(r.umidade_relativa), 2))}
            for r in s.itertuples()
        ],
    }

    with open(args.saida, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    print(f"{args.saida} gerado.")
    print(f"  leituras            : {out['janela']['leituras']:,}")
    print(f"  janela              : {inicio} a {fim} ({out['janela']['duracao_horas']} h)")
    print(f"  pontos no grafico   : {len(out['serie'])}")
    print(f"  temperatura         : {out['resumo']['temp_min']} a {out['resumo']['temp_max']} C")
    print(f"  umidade             : {out['resumo']['umid_min']} a {out['resumo']['umid_max']} %")
    fd = out["sensor"]["frequencia_declarada_min"]
    im = out["sensor"]["intervalo_medido_s"]
    if fd is not None and im is not None:
        print(f"  intervalo declarado : {fd} (coluna frequencia_amostragem_min)")
        print(f"  intervalo medido    : {im} s")
        if abs(fd * 60 - im) > max(5.0, 0.2 * fd * 60):
            print("  ATENCAO: o intervalo medido nao bate com o declarado na coluna.")


if __name__ == "__main__":
    main()
