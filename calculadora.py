# -*- coding: utf-8 -*-
"""Calculadora Agrícola — núcleo de cálculo.

Vai do rendimento municipal previsto pelo Elastic Net até o resultado do
produtor: sacas na área dele, receita, custo, lucro e risco de ficar abaixo
do ponto de equilíbrio.

Usado pelo notebook (ipywidgets) e pela página HTML. Toda a conta está aqui,
para que as duas interfaces nunca divirjam.
"""
import json
import numpy as np

# Referências editáveis pelo usuário. Valores de outubro/2026, para ancorar
# o formulário — o produtor informa os dele.
PRECO_REF = {            # R$ por saca de 60 kg
    "Soja (em grão)":  154.35,   # CEPEA/ESALQ, Paraná interior, 01/10/2026
    "Milho (em grão)":  62.00,   # ordem de grandeza do indicador CEPEA
    "Trigo (em grão)":  92.30,   # CEPEA/ESALQ PR, R$ 1.538,31/t convertido
}
CUSTO_REF = {            # R$ por hectare, custo de produção
    "Soja (em grão)":  6115.83,  # Aprosoja/MS, safra 2025/26
    "Milho (em grão)": 5200.00,
    "Trigo (em grão)": 4100.00,
}


class Calculadora:
    def __init__(self, caminho="modelo_export.json"):
        with open(caminho, encoding="utf-8") as f:
            self.d = json.load(f)
        self.ordem = self.d["ordem_m3"]
        self.pos = {v: i for i, v in enumerate(self.ordem)}
        self.linhas = self.d["linhas"]
        self.valores = self.d["valores"]

    # ---------------------------------------------------------------- modelo
    def prever_rendimento(self, idx, momento="M3"):
        """Rendimento municipal previsto, em sacas/ha, com a contribuição de
        cada variável. É um Elastic Net: previsão é soma de termos lineares,
        e é por isso que dá para abrir o resultado variável a variável."""
        m = self.d["momentos"][momento]
        linha = self.valores[idx]
        total = m["intercept"]
        contrib = []
        for j, var in enumerate(m["variaveis"]):
            coef = m["coef"][j]
            bruto = linha[self.pos[var]]
            if bruto is None:
                bruto = m["mediana"][j]
            # mesma escala Min-Max que o Pipeline aplicou no treino
            esc = (bruto - m["min"][j]) / m["range"][j]
            esc = min(1.0, max(0.0, esc))
            parcela = coef * esc
            total += parcela
            if coef != 0:
                contrib.append({"variavel": var, "valor": bruto,
                                "coeficiente": coef, "contribuicao": parcela})
        contrib.sort(key=lambda c: -abs(c["contribuicao"]))
        return total, contrib

    # ----------------------------------------------------------- calibração
    def rendimento_municipal_real(self, cod_municipio, cultura, ano):
        """Rendimento que o município de fato teve naquela safra (PAM/IBGE)."""
        for l in self.linhas:
            if l["m"] == cod_municipio and l["c"] == cultura and l["s"] == ano:
                return l["real"]
        return None

    def fator_produtor(self, historico, cod_municipio, cultura):
        """Quanto o produtor rende acima ou abaixo do município dele.

        `historico` é uma lista de pares (ano, rendimento_dele). Cada safra
        informada é comparada ao rendimento REAL do município NAQUELE MESMO
        ano — comparar com outro ano mediria a diferença entre safras, não a
        diferença entre o produtor e a região dele.

        Com menos de duas safras não há fator: uma só é anedota, não padrão.
        """
        razoes, usadas, ignoradas = [], [], []
        for ano, rend in historico:
            mun = self.rendimento_municipal_real(cod_municipio, cultura, ano)
            if mun and mun > 0:
                razoes.append(rend / mun)
                usadas.append({"ano": ano, "produtor": rend, "municipio": mun,
                               "razao": rend / mun})
            else:
                ignoradas.append(ano)
        if len(razoes) < 2:
            return 1.0, usadas, ignoradas, None
        return float(np.mean(razoes)), usadas, ignoradas, float(np.std(razoes))

    # Faixa plausível para o fator. Fora dela, quase sempre é erro de unidade:
    # o produtor informou sacas da área toda em vez de sacas por hectare, ou
    # quilos em vez de sacas. Vale avisar em vez de aplicar um fator absurdo.
    FATOR_MIN, FATOR_MAX = 0.55, 1.80

    @classmethod
    def fator_suspeito(cls, fator, usadas):
        if fator == 1.0 or not usadas:
            return None
        if fator > cls.FATOR_MAX:
            return (f"O histórico informado dá um fator de {fator:.2f}x — o produtor "
                    f"renderia {fator:.1f} vezes a média do município dele. É possível, "
                    "mas o mais comum é o valor ter sido digitado em sacas da área "
                    "toda, e não em sacas por hectare. Confira antes de usar.")
        if fator < cls.FATOR_MIN:
            return (f"O histórico informado dá um fator de {fator:.2f}x — o produtor "
                    "renderia bem abaixo da média do município. Confira se os valores "
                    "estão em sacas por hectare.")
        return None

    # ------------------------------------------------------------- resultado
    def calcular(self, idx, area_ha, momento="M3", historico=None,
                 preco=None, custo_ha=None):
        linha = self.linhas[idx]
        cult = linha["c"]
        rend_mun, contrib = self.prever_rendimento(idx, momento)
        fator, usadas, ignoradas, disp = self.fator_produtor(
            historico or [], linha["m"], cult)
        rend = rend_mun * fator

        preco = PRECO_REF[cult] if preco is None else preco
        custo_ha = CUSTO_REF[cult] if custo_ha is None else custo_ha

        res = self.d["momentos"][momento]["residuo"][cult]
        todos = np.array(res["todos"]) * fator   # resíduo escala com o fator

        # resíduo = previsto - realizado, então realizado = previsto - resíduo
        faixa = {
            "p10": rend - np.percentile(todos, 90),
            "p25": rend - np.percentile(todos, 75),
            "p50": rend - np.percentile(todos, 50),
            "p75": rend - np.percentile(todos, 25),
            "p90": rend - np.percentile(todos, 10),
        }
        equilibrio = custo_ha / preco if preco else float("inf")
        # distribuição empírica do realizado, para a probabilidade de prejuízo
        realizados = rend - todos
        p_abaixo = float((realizados < equilibrio).mean())

        def financeiro(r_ha):
            sacas = r_ha * area_ha
            receita = sacas * preco
            custo = custo_ha * area_ha
            return {"rendimento_sacas_ha": r_ha, "sacas_totais": sacas,
                    "receita": receita, "custo": custo, "lucro": receita - custo,
                    "margem_pct": 100 * (receita - custo) / receita if receita else 0.0}

        return {
            "municipio": linha["nome"], "uf": linha["uf"], "cultura": cult,
            "safra": linha["s"], "area_ha": area_ha, "momento": momento,
            "rendimento_municipal": rend_mun,
            "fator_produtor": fator, "safras_usadas": usadas,
            "safras_ignoradas": ignoradas, "dispersao_fator": disp,
            "preco_saca": preco, "custo_hectare": custo_ha,
            "ponto_equilibrio_sacas_ha": equilibrio,
            "prob_abaixo_equilibrio": p_abaixo,
            "central": financeiro(rend),
            "pessimista": financeiro(faixa["p10"]),
            "provavel_baixo": financeiro(faixa["p25"]),
            "provavel_alto": financeiro(faixa["p75"]),
            "otimista": financeiro(faixa["p90"]),
            "realizado_sacas_ha": linha["real"],
            "media_historica_sacas_ha": linha["hist"],
            "contribuicoes": contrib[:12],
        }


def brl(v):
    s = f"{abs(v):,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")
    return ("-R$ " if v < 0 else "R$ ") + s
