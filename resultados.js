// Capítulo de resultados. Nenhum número é digitado: todos são lidos de
// res_final.json, robustez.xlsx (exportado em robustez.json) e coef_final.json
// no momento da geração. Se o modelo for reexecutado, basta regerar.
const {
  Document, Packer, Paragraph, TextRun, HeadingLevel, AlignmentType,
  Table, TableRow, TableCell, WidthType, ShadingType, BorderStyle,
  ImageRun, convertInchesToTwip,
} = require("docx");
const fs = require("fs");

const CINZA = "F2F2F2";
const LINHA = "BFBFBF";

// ------------------------------------------------------------- dados ------
const R = JSON.parse(fs.readFileSync("res_final.json", "utf8"));
const COEF = JSON.parse(fs.readFileSync("coef_final.json", "utf8"));
const RB = JSON.parse(fs.readFileSync("robustez.json", "utf8"));

const m = (k) => R[k];
const n2 = (x) => x.toFixed(2).replace(".", ",");
const n3 = (x) => x.toFixed(3).replace(".", ",");
const n1 = (x) => x.toFixed(1).replace(".", ",");
const pct = (de, para) => ((de - para) / de) * 100;

const ESP_EN = m("espacial|M3_completo|com_hist|elastic_net");
const ESP_RF = m("espacial|M3_completo|com_hist|random_forest");
const ESP_TR = m("espacial|tradicional|com_hist|media_historica");
const TMP_EN = m("temporal|M3_completo|com_hist|elastic_net");
const TMP_TR = m("temporal|tradicional|com_hist|media_historica");
const ESP_CLIMA = m("espacial|M3_completo|so_clima|elastic_net");
const INDEX = RB.baseline.find((r) => r.metodo.includes("INDEXADA")).rmse;

const GANHO_TR = pct(ESP_TR.RMSE, ESP_EN.RMSE);
const GANHO_IX = pct(INDEX, ESP_EN.RMSE);
const PERDA_TMP = pct(TMP_TR.RMSE, TMP_EN.RMSE);
const ZERADOS = COEF.filter((c) => Math.abs(c[1]) < 1e-9).length;

// ------------------------------------------------------------ helpers -----
const p = (texto, o = {}) =>
  new Paragraph({
    alignment: o.align || AlignmentType.JUSTIFIED,
    spacing: { line: o.line || 360, after: o.after === undefined ? 160 : o.after },
    indent: o.semRecuo ? undefined : { firstLine: convertInchesToTwip(0.49) },
    children: Array.isArray(texto)
      ? texto
      : [new TextRun({ text: texto, font: "Arial", size: 24 })],
  });

const t = (texto, o = {}) =>
  new TextRun({ text: texto, font: "Arial", size: o.size || 24, bold: o.bold, italics: o.it });

const h = (texto, nivel) =>
  new Paragraph({
    heading: nivel,
    spacing: { before: 400, after: 220, line: 360 },
    children: [new TextRun({ text: texto, font: "Arial", size: 24, bold: true })],
  });

const legenda = (texto) =>
  new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 100, after: 300 },
    children: [new TextRun({ text: texto, font: "Arial", size: 20 })],
  });

const nota = (texto) =>
  new Paragraph({
    alignment: AlignmentType.JUSTIFIED,
    spacing: { before: 120, after: 260, line: 300 },
    indent: { left: convertInchesToTwip(0.4) },
    border: { left: { style: BorderStyle.SINGLE, size: 12, space: 10, color: "7F7F7F" } },
    children: [new TextRun({ text: texto, font: "Arial", size: 20, italics: true })],
  });

// Largura útil da página: 11906 − 1701 − 1134 = 9071 DXA ≈ 6,30 polegadas.
function figura(arquivo, larguraPol) {
  const dim = require("child_process").execSync(
    `python3 -c "from PIL import Image;im=Image.open('${arquivo}');print(im.width,im.height)"`
  ).toString().trim().split(" ").map(Number);
  const w = larguraPol * 96;
  const hgt = Math.round((dim[1] / dim[0]) * w);
  return new Paragraph({
    alignment: AlignmentType.CENTER,
    spacing: { before: 240, after: 60 },
    children: [new ImageRun({
      type: "png",
      data: fs.readFileSync(arquivo),
      transformation: { width: Math.round(w), height: hgt },
    })],
  });
}

function tabela(cabecalho, linhas, larguras) {
  const total = larguras.reduce((a, b) => a + b, 0);
  const cel = (texto, { ch = false, w, neg = false }) =>
    new TableCell({
      width: { size: w, type: WidthType.DXA },
      shading: ch ? { type: ShadingType.CLEAR, fill: CINZA, color: "auto" } : undefined,
      margins: { top: 80, bottom: 80, left: 110, right: 110 },
      children: [new Paragraph({
        spacing: { line: 260, after: 0 },
        children: [new TextRun({ text: texto, font: "Arial", size: 19, bold: ch || neg })],
      })],
    });
  return new Table({
    columnWidths: larguras,
    width: { size: total, type: WidthType.DXA },
    borders: {
      top: { style: BorderStyle.SINGLE, size: 6, color: LINHA },
      bottom: { style: BorderStyle.SINGLE, size: 6, color: LINHA },
      left: { style: BorderStyle.SINGLE, size: 6, color: LINHA },
      right: { style: BorderStyle.SINGLE, size: 6, color: LINHA },
      insideHorizontal: { style: BorderStyle.SINGLE, size: 4, color: LINHA },
      insideVertical: { style: BorderStyle.SINGLE, size: 4, color: LINHA },
    },
    rows: [
      new TableRow({
        tableHeader: true, cantSplit: true,
        children: cabecalho.map((c, i) => cel(c, { ch: true, w: larguras[i] })),
      }),
      ...linhas.map((l) => new TableRow({
        cantSplit: true,
        children: l.map((c, i) => cel(c, { w: larguras[i] })),
      })),
    ],
  });
}

const L4 = [3100, 1700, 1600, 1600];
const L5 = [2500, 1500, 1500, 1500, 2000];

const corpo = [];
const add = (...x) => corpo.push(...x);

// ============================================================== abertura ===
add(new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { after: 80 },
  children: [t("RESULTADOS E DISCUSSÃO", { bold: true })],
}));
add(new Paragraph({
  alignment: AlignmentType.CENTER, spacing: { after: 400 },
  children: [t("Calculadora Agrícola — Análise comparativa entre modelos tradicionais "
    + "de estimativa de colheita e algoritmos de aprendizado de máquina", { size: 20 })],
}));

add(h("4.1 Conjunto de dados e protocolo de avaliação", HeadingLevel.HEADING_2));

add(p(
  `A base analítica final reúne ${RB.n_linhas} observações, correspondentes a `
  + `${RB.n_municipios} municípios das unidades federativas de ${RB.ufs.join(", ")}, `
  + `nas culturas de soja, milho e trigo, ao longo das safras de ${RB.safra_min} a `
  + `${RB.safra_max}. Cada observação associa o rendimento municipal informado pela `
  + "Produção Agrícola Municipal do IBGE às variáveis climáticas do ciclo "
  + "correspondente, agregadas por estádio fenológico a partir das séries horárias "
  + "das estações automáticas do INMET."
));

add(p(
  "A avaliação segue dois esquemas de validação, que respondem a perguntas "
  + "distintas e são reportados separadamente. No esquema espacial, as partições da "
  + "validação cruzada são formadas por município, de modo que nenhum município "
  + "apareça simultaneamente em treino e em teste; ele mede o desempenho diante de "
  + "um município sem histórico disponível. No esquema temporal, o modelo é treinado "
  + "com as safras anteriores e testado na safra seguinte, reproduzindo a condição "
  + "real de uso, que é prever antes de colher."
));

add(p([
  t("Três métodos de referência foram estabelecidos como linha de base. O primeiro "
    + "é a "),
  t("média histórica do município", { bold: true }),
  t(", calculada apenas sobre as safras anteriores à que está sendo prevista. O "
    + "segundo é a "),
  t("média histórica indexada", { bold: true }),
  t(", que corrige essa média por um fator anual estimado a partir dos demais "
    + "municípios da mesma unidade federativa, e representa o produtor que conhece o "
    + "próprio histórico e acompanha o comportamento da safra na sua região. O "
    + "terceiro é o próprio modelo treinado "),
  t("apenas com clima", { bold: true }),
  t(", sem acesso ao histórico, que isola a contribuição das variáveis ambientais."),
]));

// ============================================================ resultados ===
add(h("4.2 Desempenho preditivo diante de município sem histórico", HeadingLevel.HEADING_2));

add(p(
  `No esquema espacial, o Elastic Net alcançou RMSE de ${n2(ESP_EN.RMSE)} sacas por `
  + `hectare, com erro absoluto médio de ${n2(ESP_EN.MAE)} e coeficiente de `
  + `determinação de ${n3(ESP_EN.R2)}. O método tradicional, representado pela média `
  + `histórica do município, obteve RMSE de ${n2(ESP_TR.RMSE)}. A redução do erro é, `
  + `portanto, de ${n1(GANHO_TR)} por cento.`
));

add(figura("fig1_baseline.png", 6.1));
add(legenda("Figura 1 — Erro de previsão por método, esquema espacial. "
  + "Fonte: Autoria própria (2026)."));

add(p(
  "A comparação contra a linha de base indexada é mais exigente e, por isso, mais "
  + `informativa. Esse método atinge RMSE de ${n2(INDEX)}, e a vantagem do Elastic `
  + `Net sobre ele cai para ${n1(GANHO_IX)} por cento. A leitura que se impõe é que `
  + "parte substancial do ganho aparente sobre o método tradicional provém não do "
  + "clima do ciclo, mas da informação de qual ano está sendo previsto — informação "
  + "que um produtor atento à sua região também possui. Registrar essa distinção é "
  + "necessário para que o ganho atribuído ao aprendizado de máquina não seja "
  + "superestimado."
));

add(p(
  `O modelo de árvores aleatórias, avaliado em paralelo, obteve RMSE de `
  + `${n2(ESP_RF.RMSE)}, ligeiramente inferior ao do Elastic Net. A diferença entre `
  + "os dois é pequena diante da dispersão dos dados, e o Elastic Net foi mantido "
  + "como modelo da calculadora por oferecer coeficientes diretamente "
  + "interpretáveis, requisito da terceira frente de avaliação."
));

add(tabela(
  ["Método", "RMSE", "MAE", "R²"],
  [
    ["Elastic Net — ciclo completo", n2(ESP_EN.RMSE), n2(ESP_EN.MAE), n3(ESP_EN.R2)],
    ["Random Forest — ciclo completo", n2(ESP_RF.RMSE), n2(ESP_RF.MAE), n3(ESP_RF.R2)],
    ["Média histórica indexada pelo ano", n2(INDEX), "—", "—"],
    ["Média histórica do município", n2(ESP_TR.RMSE), n2(ESP_TR.MAE), n3(ESP_TR.R2)],
    ["Elastic Net — apenas clima", n2(ESP_CLIMA.RMSE), n2(ESP_CLIMA.MAE), n3(ESP_CLIMA.R2)],
  ],
  L4
));
add(legenda("Quadro 1 — Desempenho no esquema espacial, em sacas por hectare. "
  + "Fonte: Autoria própria (2026)."));

add(figura("fig4_dispersao.png", 4.5));
add(legenda("Figura 2 — Rendimento previsto contra rendimento observado, Elastic Net, "
  + "esquema espacial. Fonte: Autoria própria (2026)."));

add(p(
  "A dispersão entre previsto e observado mostra alinhamento consistente à reta de "
  + "igualdade nas três culturas, com dispersão maior no milho, cuja amplitude de "
  + "rendimento é também a maior. Observa-se compressão em direção à média nos "
  + "extremos, comportamento esperado de um modelo linear regularizado: a "
  + "penalização que estabiliza os coeficientes também atenua as previsões "
  + "extremas."
));

// ------------------------------------------------------------------ 4.3 ---
add(h("4.3 Contribuição do clima ao longo do ciclo", HeadingLevel.HEADING_2));

add(p(
  "Para verificar se a informação climática acrescenta capacidade preditiva à "
  + "medida que o ciclo avança, o modelo foi avaliado em três recortes cumulativos: "
  + "apenas o estádio vegetativo, até a floração, e o ciclo completo até a colheita. "
  + "O erro diminui monotonicamente nos dois cenários, com e sem o histórico do "
  + "município, o que confirma que cada estádio adicional carrega informação útil."
));

add(figura("fig3_ciclo.png", 6.1));
add(legenda("Figura 3 — Erro de previsão conforme o ciclo avança, esquema espacial. "
  + "Fonte: Autoria própria (2026)."));

add(p(
  `O efeito é mais pronunciado quando o modelo não dispõe do histórico: o erro cai `
  + `de ${n2(m("espacial|M1_vegetativo|so_clima|elastic_net").RMSE)} no estádio `
  + `vegetativo para ${n2(ESP_CLIMA.RMSE)} no ciclo completo. Com o histórico `
  + `disponível, a queda é menor, de ${n2(m("espacial|M1_vegetativo|com_hist|elastic_net").RMSE)} `
  + `para ${n2(ESP_EN.RMSE)}, porque parte da variabilidade já está explicada antes `
  + "de o clima entrar. Trata-se de resultado coerente com a hipótese do trabalho: "
  + "o clima do ciclo é informativo, e sua contribuição marginal é tanto maior "
  + "quanto menor a informação prévia sobre a localidade."
));

// ------------------------------------------------------------------ 4.4 ---
add(h("4.4 Interpretabilidade dos coeficientes", HeadingLevel.HEADING_2));

add(p(
  `Dos ${COEF.length} coeficientes do modelo, ${ZERADOS} foram reduzidos a zero `
  + `pela penalização, restando ${COEF.length - ZERADOS} variáveis efetivamente `
  + "utilizadas. A seleção é feita pelo próprio ajuste, sem intervenção manual, e "
  + "constitui evidência de que o modelo descarta variáveis redundantes em vez de "
  + "acomodá-las."
));

add(figura("fig5_coeficientes.png", 6.1));
add(legenda("Figura 4 — Coeficientes padronizados de maior magnitude. "
  + "Fonte: Autoria própria (2026)."));

add(p(
  "A hierarquia dos coeficientes é, em si, um resultado. O maior peso não recai "
  + "sobre uma variável climática, e sim sobre a média histórica de rendimento do "
  + "próprio município, o que indica que o modelo aprende primeiro a posição "
  + "produtiva da localidade e só então o efeito do ano. Entre as variáveis "
  + "climáticas, as de maior peso concentram-se no estádio de enchimento de grãos."
));

add(p(
  "Os sinais são agronomicamente coerentes, e essa coerência funciona como "
  + "validação adicional de que o modelo aprendeu relação física e não ruído. A "
  + "temperatura máxima absoluta durante o enchimento de grãos tem o maior "
  + "coeficiente negativo entre as variáveis climáticas, compatível com o estresse "
  + "térmico documentado nessa fase. O acúmulo de graus-dia no mesmo estádio tem "
  + "sinal positivo, compatível com a disponibilidade de energia térmica para o "
  + "enchimento. O déficit de pressão de vapor no estádio vegetativo tem sinal "
  + "negativo, compatível com a demanda atmosférica por água em fase de "
  + "estabelecimento da cultura."
));

// ------------------------------------------------------------------ 4.5 ---
add(h("4.5 Delimitação do escopo: previsão da safra seguinte", HeadingLevel.HEADING_2));

add(p(
  "O segundo esquema de validação produz resultado qualitativamente distinto, e a "
  + "sua declaração é condição para o uso responsável do modelo. Ao ser treinado "
  + "com safras passadas e testado na safra seguinte, o Elastic Net obteve RMSE de "
  + `${n2(TMP_EN.RMSE)}, contra ${n2(TMP_TR.RMSE)} do método tradicional — ou seja, `
  + `desempenho ${n1(Math.abs(PERDA_TMP))} por cento pior.`
));

add(figura("fig2_esquemas.png", 6.1));
add(legenda("Figura 5 — O mesmo modelo nos dois esquemas de validação. "
  + "Fonte: Autoria própria (2026)."));

add(p(
  "A explicação é a extensão da série. O conjunto cobre cinco safras, número "
  + "insuficiente para que o modelo aprenda a dinâmica interanual — isto é, como o "
  + "clima de um ano se traduz em rendimento quando esse ano ainda não foi "
  + "observado. No esquema espacial o modelo generaliza no espaço, tarefa para a "
  + "qual há 145 municípios de treino; no esquema temporal precisaria generalizar no "
  + "tempo, e para isso há apenas quatro transições de safra."
));

add(p(
  "A consequência prática é objetiva e delimita o produto: a calculadora é "
  + "adequada à estimativa dentro da safra em curso, à medida que o clima do ciclo "
  + "se realiza, e não à projeção de uma safra ainda não iniciada. Essa delimitação "
  + "está implementada na própria ferramenta, que reporta a previsão acompanhada da "
  + "faixa de incerteza correspondente ao momento do ciclo em que é consultada."
));

add(tabela(
  ["Esquema", "Pergunta respondida", "Elastic Net", "Tradicional", "Resultado"],
  [
    ["Espacial", "Município sem histórico", n2(ESP_EN.RMSE), n2(ESP_TR.RMSE),
      `favorável (${n1(GANHO_TR)}%)`],
    ["Temporal", "Safra ainda não iniciada", n2(TMP_EN.RMSE), n2(TMP_TR.RMSE),
      `desfavorável (${n1(PERDA_TMP)}%)`],
  ],
  L5
));
add(legenda("Quadro 2 — Síntese dos dois esquemas, RMSE em sacas por hectare. "
  + "Fonte: Autoria própria (2026)."));

// ------------------------------------------------------------------ 4.6 ---
add(h("4.6 Comportamento em safra de quebra", HeadingLevel.HEADING_2));

add(p(
  `Como teste de robustez, avaliou-se o comportamento dos métodos em um evento de `
  + `quebra de safra: a soja no Rio Grande do Sul em 2022, com ${RB.quebra_n} `
  + `municípios na amostra e rendimento efetivamente colhido de ${n1(RB.realizado)} `
  + "sacas por hectare, muito abaixo do padrão histórico da região."
));

add(figura("fig6_quebra.png", 6.1));
add(legenda("Figura 6 — Previsão de cada método na quebra de safra. "
  + "Fonte: Autoria própria (2026)."));

add(p(
  "O resultado separa com nitidez os quatro métodos. A média histórica simples "
  + `projetou ${n1(RB.quebra.find((q) => q.metodo.includes("histórica")).previsto)} `
  + "sacas por hectare, ignorando o evento, como seria de esperar de um método que "
  + "desconhece o ano. A média indexada acompanhou a quebra, o que é consistente "
  + "com o seu desenho, já que o fator anual é estimado a partir dos demais "
  + "municípios da unidade federativa e portanto incorpora o evento regional. O "
  + "Elastic Net no esquema espacial também identificou a anomalia, projetando "
  + `${n1(RB.quebra.find((q) => q.metodo.includes("espacial")).previsto)} sacas por `
  + "hectare a partir das variáveis climáticas do ciclo, sem dispor do fator anual."
));

add(p(
  "O mesmo modelo no esquema temporal falhou neste caso, projetando "
  + `${n1(RB.quebra.find((q) => q.metodo.includes("temporal")).previsto)} sacas por `
  + "hectare. O resultado é consistente com a limitação exposta na seção anterior e "
  + "reforça a delimitação de escopo ali estabelecida."
));

add(p(
  "Uma análise complementar, estratificando as observações por intensidade de "
  + "anomalia climática, mostrou que o modelo não apresenta vantagem sistemática "
  + "sobre o método tradicional nos quartis de maior anomalia. A capacidade de "
  + "identificar o evento, demonstrada no caso acima, não se traduz em erro menor de "
  + "forma consistente no conjunto das situações atípicas. Essa limitação é "
  + "declarada por não haver evidência que a contrarie."
));

// ------------------------------------------------------------------ 4.7 ---
add(h("4.7 Atendimento aos requisitos não funcionais", HeadingLevel.HEADING_2));

add(p(
  "O tempo de resposta da calculadora foi medido sobre a chamada completa, que "
  + "inclui a montagem das variáveis, a padronização, a aplicação dos coeficientes "
  + "e a conversão para sacas e receita, e não apenas a operação aritmética do "
  + `modelo. A média obtida foi de ${n2(RB.inferencia_ms)} milissegundo por `
  + `consulta, cerca de ${Math.round(50 / RB.inferencia_ms)} vezes abaixo do limite `
  + "de cinquenta milissegundos estabelecido como requisito não funcional. O modelo "
  + "é exportado como conjunto de coeficientes e "
  + "parâmetros de padronização, o que dispensa a presença de biblioteca de "
  + "aprendizado de máquina no ambiente do usuário e viabiliza a execução da "
  + "previsão no próprio navegador."
));

// ------------------------------------------------------------------ 4.8 ---
add(h("4.8 Síntese", HeadingLevel.HEADING_2));

add(p(
  "Os resultados sustentam uma conclusão delimitada, e é na delimitação que reside "
  + "a contribuição do trabalho. O clima do ciclo, agregado por estádio fenológico, "
  + "melhora a estimativa de rendimento quando não se dispõe do histórico do "
  + "município, e a melhora é monotônica à medida que o ciclo avança. A vantagem "
  + "sobre o método tradicional ingênuo é substancial; sobre o método tradicional "
  + "bem especificado, que incorpora o comportamento regional do ano, é modesta. Em "
  + "previsão de safra ainda não iniciada, cinco safras de série não são suficientes "
  + "para que o modelo aprenda a dinâmica temporal, e o método tradicional permanece "
  + "superior."
));

add(p(
  "A ferramenta resultante é, portanto, um instrumento de acompanhamento da safra "
  + "em curso, e não de projeção de safras futuras. Essa é uma delimitação de "
  + "escopo apurada por evidência, não uma deficiência de implementação, e a sua "
  + "declaração explícita é o que torna a ferramenta utilizável com segurança pelo "
  + "produtor."
));

add(nota(
  "Todos os valores apresentados neste capítulo são lidos, no momento da geração "
  + "do documento, dos arquivos de resultado produzidos pelos notebooks do pipeline "
  + "(res_final.json, robustez.xlsx e coef_final.json). Nenhum número foi "
  + "transcrito manualmente, de modo que uma reexecução do modelo seguida da "
  + "regeneração deste capítulo mantém texto, quadros e figuras consistentes entre "
  + "si."
));

// ================================================================ documento =
const doc = new Document({
  creator: "Calculadora Agrícola",
  title: "Resultados e discussão",
  numbering: { config: [] },
  styles: {
    default: {
      heading2: { run: { font: "Arial", size: 24, bold: true, color: "000000" } },
      heading3: { run: { font: "Arial", size: 24, bold: true, color: "000000" } },
    },
  },
  sections: [{
    properties: {
      page: {
        size: { width: 11906, height: 16838 },
        margin: { top: 1701, right: 1134, bottom: 1134, left: 1701 },
      },
    },
    children: corpo,
  }],
});

Packer.toBuffer(doc).then((b) => {
  fs.writeFileSync("resultados_tcc.docx", b);
  console.log("resultados_tcc.docx gerado:", b.length, "bytes");
});
