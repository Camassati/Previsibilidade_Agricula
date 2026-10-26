# -*- coding: utf-8 -*-
"""Figuras do capítulo de resultados. Todos os valores vêm de res_final.json,
robustez.xlsx e coef_final.json — nenhum número é digitado aqui à mão."""
import json
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D

AZUL, LARANJA, AQUA, VERMELHO = "#2a78d6", "#eb6834", "#1baf7a", "#e34948"
TINTA, SEC, MUDO = "#0b0b0b", "#52514e", "#898781"
GRADE, EIXO, FUNDO = "#e1e0d9", "#c3c2b7", "#fcfcfb"

def br(x, casas=2):
    """Vírgula decimal, como manda a norma — o texto do capítulo usa vírgula e a
    figura não pode usar ponto."""
    return f"{x:.{casas}f}".replace(".", ",")


def br_eixo(x, _pos=None):
    return f"{x:g}".replace(".", ",")


plt.rcParams.update({
    "font.family": "DejaVu Sans", "font.size": 9,
    "figure.facecolor": FUNDO, "axes.facecolor": FUNDO,
    "axes.edgecolor": EIXO, "axes.labelcolor": SEC,
    "xtick.color": MUDO, "ytick.color": MUDO,
    "axes.grid": False, "savefig.facecolor": FUNDO,
})

R = json.load(open("res_final.json"))
COEF = json.load(open("coef_final.json"))
XB = pd.ExcelFile("robustez.xlsx")
BASE = XB.parse("1_baseline_justo")
QUEBRA = XB.parse("2_safra_de_quebra")
ANOM = XB.parse("3_anomalia_climatica")
PREV = XB.parse("previsoes")


def limpar(ax, grade_x=False, grade_y=True):
    for lado in ("top", "right"):
        ax.spines[lado].set_visible(False)
    for lado in ("left", "bottom"):
        ax.spines[lado].set_color(EIXO)
        ax.spines[lado].set_linewidth(.8)
    if grade_y:
        ax.set_axisbelow(True)
        ax.yaxis.grid(True, color=GRADE, linewidth=.8)
    if grade_x:
        ax.set_axisbelow(True)
        ax.xaxis.grid(True, color=GRADE, linewidth=.8)


def eixos_br(ax, x=True, y=True):
    from matplotlib.ticker import FuncFormatter
    if x: ax.xaxis.set_major_formatter(FuncFormatter(br_eixo))
    if y: ax.yaxis.set_major_formatter(FuncFormatter(br_eixo))


def salvar(fig, nome):
    fig.savefig(nome, dpi=300, bbox_inches="tight", pad_inches=.12)
    plt.close(fig)
    print("   ", nome)


# ---------------------------------------------------------------- figura 1 --
# Magnitude, série única: erro de cada método no esquema espacial.
def fig1():
    d = BASE.sort_values("rmse", ascending=False)
    rot = {"média histórica do município": "Média histórica\ndo município",
           "média histórica INDEXADA pelo ano": "Média histórica\nindexada pelo ano",
           "Elastic Net M3 (espacial)": "Elastic Net\n(ciclo completo)",
           "Random Forest M3 (espacial)": "Random Forest\n(ciclo completo)"}
    nomes = [rot[m] for m in d.metodo]
    cores = [MUDO if "média" in m else AZUL for m in d.metodo]
    fig, ax = plt.subplots(figsize=(6.3, 3.2))
    y = np.arange(len(d))
    ax.barh(y, d.rmse, height=.62, color=cores, zorder=3)
    for i, v in enumerate(d.rmse):
        ax.text(v + .18, i, br(v), va="center", ha="left",
                fontsize=9, color=TINTA, fontweight="bold")
    ax.set_yticks(y, nomes, fontsize=8.5, color=SEC)
    ax.set_xlim(0, d.rmse.max() * 1.16)
    ax.set_xlabel("RMSE (sacas por hectare) — menor é melhor")
    limpar(ax, grade_x=True, grade_y=False)
    ax.text(0, len(d) - .35, "métodos tradicionais em cinza", fontsize=8,
            color=MUDO, style="italic")
    eixos_br(ax, y=False)
    salvar(fig, "fig1_baseline.png")


# ---------------------------------------------------------------- figura 2 --
# Duas séries: o mesmo modelo nos dois esquemas de validação.
def fig2():
    pares = [("Elastic Net", R["espacial|M3_completo|com_hist|elastic_net"]["RMSE"],
              R["temporal|M3_completo|com_hist|elastic_net"]["RMSE"]),
             ("Média histórica", R["espacial|tradicional|com_hist|media_historica"]["RMSE"],
              R["temporal|tradicional|com_hist|media_historica"]["RMSE"])]
    x = np.arange(2)
    larg = .34
    fig, ax = plt.subplots(figsize=(6.3, 3.4))
    esp = [p[1] for p in pares]
    tmp = [p[2] for p in pares]
    b1 = ax.bar(x - larg / 2 - .01, esp, larg, label="Município novo (espacial)",
                color=AZUL, zorder=3)
    b2 = ax.bar(x + larg / 2 + .01, tmp, larg, label="Próxima safra (temporal)",
                color=LARANJA, zorder=3)
    for bs in (b1, b2):
        for b in bs:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + .3,
                    br(b.get_height()), ha="center", fontsize=8.8,
                    color=TINTA, fontweight="bold")
    ax.set_xticks(x, [p[0] for p in pares], fontsize=9.5, color=TINTA)
    ax.set_ylabel("RMSE (sacas por hectare)")
    ax.set_ylim(0, max(esp + tmp) * 1.22)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center",
              bbox_to_anchor=(.5, 1.16), ncol=2, labelcolor=SEC)
    limpar(ax)
    eixos_br(ax, x=False)
    salvar(fig, "fig2_esquemas.png")


# ---------------------------------------------------------------- figura 3 --
# Mudança ao longo do ciclo: duas séries, com e sem o histórico do município.
def fig3():
    etapas = ["M1\nvegetativo", "M2\naté floração", "M3\nciclo completo"]
    ch = [R[f"espacial|{m}|com_hist|elastic_net"]["RMSE"]
          for m in ("M1_vegetativo", "M2_floracao", "M3_completo")]
    sc = [R[f"espacial|{m}|so_clima|elastic_net"]["RMSE"]
          for m in ("M1_vegetativo", "M2_floracao", "M3_completo")]
    x = np.arange(3)
    fig, ax = plt.subplots(figsize=(6.3, 3.4))
    ax.plot(x, sc, "-o", color=LARANJA, linewidth=2, markersize=8,
            markeredgecolor=FUNDO, markeredgewidth=2, zorder=3,
            label="Apenas clima")
    ax.plot(x, ch, "-s", color=AZUL, linewidth=2, markersize=8,
            markeredgecolor=FUNDO, markeredgewidth=2, zorder=3,
            label="Clima + histórico do município")
    for i, (a, b) in enumerate(zip(sc, ch)):
        ax.text(i, a + .55, br(a), ha="center", fontsize=8.6, color=TINTA)
        ax.text(i, b - .95, br(b), ha="center", fontsize=8.6, color=TINTA)
    ax.set_xticks(x, etapas, fontsize=9, color=TINTA)
    ax.set_xlim(-.35, 2.35)
    ax.set_ylabel("RMSE (sacas por hectare)")
    ax.set_ylim(min(ch) - 2.6, max(sc) + 2.2)
    ax.legend(frameon=False, fontsize=8.5, loc="upper center",
              bbox_to_anchor=(.5, 1.16), ncol=2, labelcolor=SEC)
    limpar(ax)
    eixos_br(ax, x=False)
    salvar(fig, "fig3_ciclo.png")


# ---------------------------------------------------------------- figura 4 --
# Identidade: previsto contra observado, uma cor por cultura (3 séries).
def fig4():
    cores = {"Soja (em grão)": AZUL, "Milho (em grão)": LARANJA,
             "Trigo (em grão)": AQUA}
    marcas = {"Soja (em grão)": "o", "Milho (em grão)": "^", "Trigo (em grão)": "s"}
    fig, ax = plt.subplots(figsize=(5.0, 5.0))
    lim = [0, max(PREV.rendimento_sacas_ha.max(), PREV.prev_esp_en.max()) * 1.05]
    ax.plot(lim, lim, color=MUDO, linewidth=1, linestyle=(0, (5, 4)), zorder=2)
    ax.text(lim[1] * .60, lim[1] * .655, "previsão perfeita", fontsize=8,
            color=MUDO, rotation=38, style="italic")
    for cult, cor in cores.items():
        s = PREV[PREV.cultura_tcc == cult]
        ax.scatter(s.rendimento_sacas_ha, s.prev_esp_en, s=15, c=cor,
                   marker=marcas[cult], alpha=.62, linewidths=0, zorder=3,
                   label=cult.replace(" (em grão)", ""))
    ax.set_xlim(lim)
    ax.set_ylim(lim)
    ax.set_aspect("equal")
    ax.set_xlabel("Rendimento observado (sacas/ha)")
    ax.set_ylabel("Rendimento previsto (sacas/ha)")
    r2 = R["espacial|M3_completo|com_hist|elastic_net"]["R2"]
    ax.text(.04, .95, f"R² = {br(r2, 3)}   n = {len(PREV)}", transform=ax.transAxes,
            fontsize=9, color=TINTA, fontweight="bold", va="top")
    ax.legend(frameon=False, fontsize=8.5, loc="lower right", labelcolor=SEC,
              handletextpad=.3)
    limpar(ax, grade_x=True)
    eixos_br(ax)
    salvar(fig, "fig4_dispersao.png")


# ---------------------------------------------------------------- figura 5 --
# Polaridade: coeficientes, azul positivo e vermelho negativo.
ESTADIO = {"vegetativo": "vegetativo", "floracao": "floração",
           "enchimento": "enchimento"}
VARIAVEL = {
    "temp_max_abs": "temp. máxima absoluta", "temp_min_abs": "temp. mínima absoluta",
    "temp_media": "temperatura média", "graus_dia_acum": "graus-dia acumulados",
    "amplitude_termica": "amplitude térmica", "vpd_medio_kpa": "VPD médio",
    "vpd_max_kpa": "VPD máximo", "umidade_min_abs": "umidade mínima absoluta",
    "umidade_media": "umidade média", "precipitacao_total_mm": "precipitação total",
    "radiacao_total_mjm2": "radiação total", "cobertura": "cobertura de medição",
    "maior_veranico": "maior veranico", "dias_com_chuva": "dias com chuva",
}


def rotular(nome):
    """Traduz o nome técnico da coluna para leitura humana, sem inventar: o
    prefixo é o estádio fenológico e o resto é a variável climática."""
    if nome == "base_hist":
        return "histórico do município"
    for pref, leg in ESTADIO.items():
        if nome.startswith(pref + "_"):
            resto = nome[len(pref) + 1:]
            return f"{leg} · {VARIAVEL.get(resto, resto.replace('_', ' '))}"
    return VARIAVEL.get(nome, nome.replace("_", " "))


def fig5():
    top = sorted(COEF, key=lambda c: -abs(c[1]))[:12][::-1]
    nomes = [rotular(n) for n, _ in top]
    vals = [v for _, v in top]
    cores = [AZUL if v > 0 else VERMELHO for v in vals]
    fig, ax = plt.subplots(figsize=(6.3, 4.4))
    y = np.arange(len(top))
    ax.barh(y, vals, height=.64, color=cores, zorder=3)
    for i, v in enumerate(vals):
        ax.text(v + (2.5 if v > 0 else -2.5), i, ("+" if v > 0 else "\u2212") + br(abs(v), 1), va="center",
                ha="left" if v > 0 else "right", fontsize=8.4, color=TINTA)
    ax.set_yticks(y, nomes, fontsize=8.2, color=SEC)
    ax.axvline(0, color=EIXO, linewidth=.9)
    ax.set_xlabel("Coeficiente padronizado")
    m = max(abs(min(vals)), abs(max(vals))) * 1.22
    ax.set_xlim(-m, m)
    limpar(ax, grade_x=True, grade_y=False)
    nz = sum(1 for _, v in COEF if abs(v) < 1e-9)
    ax.text(.0, 1.04, f"{len(COEF) - nz} de {len(COEF)} variáveis mantidas; "
            f"{nz} zeradas pela penalização", transform=ax.transAxes,
            fontsize=8, color=MUDO, style="italic")
    legenda = [Line2D([0], [0], marker="s", color="none", markerfacecolor=AZUL,
                      markersize=8, label="aumenta o rendimento"),
               Line2D([0], [0], marker="s", color="none", markerfacecolor=VERMELHO,
                      markersize=8, label="reduz o rendimento")]
    ax.legend(handles=legenda, frameon=False, fontsize=8.3, loc="lower right",
              labelcolor=SEC, handletextpad=.3)
    eixos_br(ax, y=False)
    salvar(fig, "fig5_coeficientes.png")


# ---------------------------------------------------------------- figura 6 --
# Caso de teste: a quebra de safra, cada método contra o realizado.
def fig6():
    d = QUEBRA.copy()
    rot = {"média histórica": "Média\nhistórica",
           "média INDEXADA pelo ano": "Média\nindexada",
           "Elastic Net — espacial": "Elastic Net\nespacial",
           "Elastic Net — temporal": "Elastic Net\ntemporal"}
    real = float(json.load(open("_seca.json"))["realizado"])
    fig, ax = plt.subplots(figsize=(6.3, 3.5))
    x = np.arange(len(d))
    # Uma cor por natureza do método — tradicional em cinza, modelo em azul —,
    # nunca por tamanho do erro: a cor identifica a entidade, não a posição.
    cores = [MUDO if "média" in m else AZUL for m in d.metodo]
    ax.bar(x, d.previsto, .56, color=cores, zorder=3)
    for i, v in enumerate(d.previsto):
        ax.text(i, v + 1.6, br(v, 1), ha="center", fontsize=9,
                color=TINTA, fontweight="bold")
    ax.axhline(real, color=TINTA, linewidth=1.4, linestyle=(0, (5, 3)), zorder=4)
    ax.text(len(d) - .42, real + 1.8, f"realizado: {br(real, 1)}", ha="right",
            fontsize=8.8, color=TINTA, fontweight="bold")
    ax.set_xticks(x, [rot[m] for m in d.metodo], fontsize=8.6, color=TINTA)
    ax.set_ylabel("Rendimento previsto (sacas/ha)")
    ax.set_ylim(0, d.previsto.max() * 1.26)
    ax.text(0, 1.04, "métodos tradicionais em cinza; a linha tracejada é o "
            "rendimento efetivamente colhido", transform=ax.transAxes,
            fontsize=8, color=MUDO, style="italic")
    limpar(ax)
    eixos_br(ax, x=False)
    salvar(fig, "fig6_quebra.png")


if __name__ == "__main__":
    print("gerando figuras:")
    fig1(); fig2(); fig3(); fig4(); fig5(); fig6()
