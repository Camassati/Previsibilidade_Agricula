# -*- coding: utf-8 -*-
"""As leituras do sensor estão chegando no banco? Só faz SELECT.

    python checar_sensor.py

Usa o banco.py / banco_config.ini da pasta, como o servidor.py."""
from banco import conectar

con = conectar()
try:
    with con.cursor() as cur:
        cur.execute("SET SESSION TRANSACTION READ ONLY")

        cur.execute("""SELECT COUNT(*), MAX(id_leitura), MAX(data_hora), NOW(),
                              TIMESTAMPDIFF(MINUTE, MAX(data_hora), NOW())
                         FROM LEITURA_IOT""")
        total, ult_id, ult_hora, agora, idade_min = cur.fetchone()
        print(f"Leituras no banco      : {total}")
        print(f"Última leitura gravada : {ult_hora}  (id {ult_id})")
        print(f"Hora do MySQL agora    : {agora}")
        print(f"Há quanto tempo        : {idade_min} min")

        cur.execute("""SELECT COUNT(*) FROM LEITURA_IOT
                        WHERE data_hora >= NOW() - INTERVAL 10 MINUTE""")
        print(f"Leituras nos últimos 10 min: {cur.fetchone()[0]}")

        cur.execute("""SELECT COUNT(*) FROM LEITURA_IOT WHERE data_hora > NOW()""")
        fut = cur.fetchone()[0]
        if fut:
            print(f"ATENÇÃO: {fut} leituras com hora NO FUTURO (fuso diferente?)")

        print("\nÚltimas 5 leituras:")
        cur.execute("""SELECT id_leitura, id_sensor, id_config, temperatura,
                              umidade_relativa, data_hora
                         FROM LEITURA_IOT ORDER BY id_leitura DESC LIMIT 5""")
        for r in cur.fetchall():
            print("  ", r)

        print("\nPor dia (últimos 10 dias com leitura):")
        cur.execute("""SELECT DATE(data_hora), COUNT(*) FROM LEITURA_IOT
                        GROUP BY DATE(data_hora) ORDER BY 1 DESC LIMIT 10""")
        for d, n in cur.fetchall():
            print(f"   {d}  {n}")
finally:
    con.close()
