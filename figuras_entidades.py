# -*- coding: utf-8 -*-
"""Gera as figuras do dicionário de dados (Figuras 9 a 14) a partir do
inventário real do banco. Nada é digitado à mão: o conteúdo de cada figura é
lido de banco_inventario.txt, que é a saída de banco.py (DESCRIBE por tabela
mais as chaves estrangeiras do information_schema)."""
import pathlib
import re
import subprocess
import sys

INVENTARIO = "/mnt/user-data/uploads/TCC/banco_inventario.txt"
SAIDA = pathlib.Path("/home/claude/tcc/figuras_bd")
SAIDA.mkdir(exist_ok=True)

# Figura -> entidade, na ordem em que aparecem no TC1.1
FIGURAS = [
    (9, "REGIAO", "Municípios e estações meteorológicas monitoradas"),
    (10, "TIPO_SOLO", "Catálogo pedológico e atributos agronômicos"),
    (11, "CULTURA", "Culturas suportadas e ciclo estimado"),
    (12, "CONFIGURACAO_SAFRA", "Parâmetros da safra cadastrada pelo produtor"),
    (13, "SENSOR_IOT", "Dispositivo de monitoramento instalado em campo"),
    (14, "LEITURA_IOT", "Medições ambientais coletadas pelo nó sensor"),
]

CHAVE = {"PRI": ("PK", "#2a78d6"), "UNI": ("UK", "#7b3fb8"),
         "MUL": ("FK", "#1baf7a")}


def parse(texto):
    """Quebra o inventário em {tabela: {colunas, fks, n_linhas}}."""
    tabelas = {}
    blocos = re.split(r"={10,}\n", texto)
    i = 0
    while i < len(blocos):
        cab = blocos[i].strip()
        m = re.match(r"TABELA (\w+)\s+—\s+([\d,]+) linhas", cab)
        if m and i + 1 < len(blocos):
            nome, n = m.group(1), m.group(2)
            corpo = blocos[i + 1]
            cols = []
            for linha in corpo.splitlines():
                c = re.match(r"\s{3}(\w+)\s{2,}([\w()\s,]+?)\s{2,}"
                             r"(NOT NULL|NULL)\s*(?:\[(\w+)\])?\s*$", linha)
                if c:
                    cols.append({"nome": c.group(1), "tipo": c.group(2).strip(),
                                 "nulo": c.group(3), "chave": c.group(4)})
            fks = dict(re.findall(r"\s{3}(\w+) -> ([\w.]+)", corpo))
            tabelas[nome] = {"colunas": cols, "fks": fks, "linhas": n}
        i += 1
    return tabelas


CSS = """
html,body{margin:0;padding:0;background:#fff}
#alvo{width:760px;padding:26px 26px 18px;background:#fcfcfb;color:#0b0b0b;
  font-family:"IBM Plex Mono",ui-monospace,monospace}
.cartao{border:1.5px solid %(cor)s;border-radius:9px;overflow:hidden;background:#fff}
.topo{background:%(cor)s;color:#fff;padding:10px 16px}
.topo .nome{font-size:15px;font-weight:600;letter-spacing:.3px}
.topo .sub{font-size:11px;opacity:.92;margin-top:3px}
table{width:100%%;border-collapse:collapse;font-size:12px}
th{text-align:left;font-weight:600;color:#52514e;background:#f2f2f0;
  padding:7px 16px;border-bottom:1px solid #e1e0d9;font-size:11px;
  text-transform:uppercase;letter-spacing:.4px}
td{padding:6px 16px;border-bottom:1px solid #eeede7;vertical-align:top}
tr:last-child td{border-bottom:none}
.attr{font-weight:600}
.tipo{color:#52514e}
.nulo{color:#898781;font-size:11px}
.tag{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;
  font-weight:600;color:#fff}
.rodape{padding:9px 16px;background:#f7f7f5;border-top:1px solid #e1e0d9;
  font-size:11px;color:#52514e;line-height:1.55}
.rodape b{color:#0b0b0b}
.legenda{text-align:center;font-family:Arial,Helvetica,sans-serif;
  font-size:13px;color:#0b0b0b;margin-top:14px}
.fonte{text-align:center;font-family:Arial,Helvetica,sans-serif;
  font-size:11px;color:#52514e;margin-top:3px;font-style:italic}
"""


def html_entidade(num, nome, descricao, dados, cor):
    linhas = []
    for c in dados["colunas"]:
        tag = ""
        if c["chave"] in CHAVE:
            rot, tc = CHAVE[c["chave"]]
            # MUL só é chave estrangeira de fato se houver FK declarada
            if c["chave"] == "MUL" and c["nome"] not in dados["fks"]:
                rot, tc = "IDX", "#898781"
            tag = f'<span class="tag" style="background:{tc}">{rot}</span>'
        linhas.append(
            f'<tr><td class="attr">{c["nome"]}</td>'
            f'<td class="tipo">{c["tipo"]}</td>'
            f'<td class="nulo">{"obrigatório" if c["nulo"] == "NOT NULL" else "opcional"}</td>'
            f'<td>{tag}</td></tr>')
    if dados["fks"]:
        fk = " · ".join(f"<b>{k}</b> → {v}" for k, v in sorted(dados["fks"].items()))
        rodape = f'<div class="rodape">Chaves estrangeiras: {fk}</div>'
    else:
        rodape = '<div class="rodape">Chaves estrangeiras: nenhuma</div>'
    return f"""<!doctype html><html><head><meta charset="utf-8">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&display=swap">
<style>{CSS % {"cor": cor}}</style></head><body><div id="alvo">
<div class="cartao">
  <div class="topo"><div class="nome">{nome}</div>
    <div class="sub">{descricao}</div></div>
  <table>
    <tr><th>Atributo</th><th>Tipo de dado</th><th>Obrigatoriedade</th><th>Chave</th></tr>
    {"".join(linhas)}
  </table>
  {rodape}
</div>
<div class="legenda">Figura {num} – Entidade {nome}</div>
<div class="fonte">Fonte: Autoria própria, (2026)</div>
</div></body></html>"""


CORES = {"REGIAO": "#2a78d6", "TIPO_SOLO": "#2a78d6", "CULTURA": "#2a78d6",
         "CONFIGURACAO_SAFRA": "#1baf7a", "SENSOR_IOT": "#1baf7a",
         "LEITURA_IOT": "#1baf7a"}

if __name__ == "__main__":
    texto = open(INVENTARIO, encoding="utf-8", errors="replace").read()
    tab = parse(texto)
    faltando = [n for _, n, _ in FIGURAS if n not in tab]
    if faltando:
        sys.exit(f"ABORTADO: não achei no inventário: {faltando}")

    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        b = pw.chromium.launch(executable_path="/opt/pw-browsers/chromium")
        pg = b.new_page(viewport={"width": 860, "height": 900},
                        device_scale_factor=3)
        for num, nome, desc in FIGURAS:
            arq = SAIDA / f"fig{num}_{nome.lower()}.html"
            arq.write_text(html_entidade(num, nome, desc, tab[nome],
                                         CORES[nome]), encoding="utf-8")
            pg.goto("file://" + str(arq.resolve()))
            pg.wait_for_timeout(1400)
            png = SAIDA / f"fig{num}_{nome}.png"
            pg.query_selector("#alvo").screenshot(path=str(png))
            subprocess.run([
                "python3", "-c",
                f"from PIL import Image;im=Image.open('{png}');"
                f"im.convert('RGB').save('{SAIDA}/fig{num}_{nome}.jpg','JPEG',"
                f"quality=94,dpi=(300,300),subsampling=0);print('{nome}',im.size)"
            ], check=True)
        b.close()
