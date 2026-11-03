# Guia: PBE Edifica, INI-C e a etiquetagem do Ambiens

## Como responder sobre a norma

- Toda afirmação sobre a norma vem do texto: use `buscar_norma` (termos, siglas,
  "Tabela 8.16", "C.I.4") e `ler_norma` (página inteira, para tabelas e
  equações). Cite documento, item/tabela e página. Não cite de memória.
- As páginas são as do PDF, que coincidem com a numeração impressa da Portaria.
- O texto vem do `pdftotext`: tabelas saem em colunas separadas por espaços e
  índices de equação perdem a formatação (CgTT,real vira "CgTT,real"). Se uma
  célula parecer ambígua, leia a página inteira antes de citar o valor.
- Distinga o que a norma pede do que o Ambiens faz (seção "O que o Ambiens
  implementa"). O resultado do Ambiens é estimativa: a ENCE oficial exige
  inspeção por OIA-EEE ou profissional certificado (RAC, Anexo III).

## Documentos (`documento` nas ferramentas)

- `portaria` — Portaria Inmetro 309/2022 consolidada e retificada pela NT 02.
  - Anexo I, INI-C (comerciais, de serviços e públicas), p. 11–154:
    siglas p. 11; definições (item 4) p. 15; visão geral p. 28; método
    simplificado da envoltória, condições (6) p. 30; elegibilidade para A (7)
    p. 32; classificação geral (8.1) p. 47; envoltória (8.2.1) p. 54 com as
    Tabelas 8.11 (classes) p. 55 e 8.12–8.19 (CRCgTT) p. 55–57;
    condicionamento (8.2.2) p. 58; iluminação (8.2.3) p. 61; água quente
    (8.2.4) p. 62.
  - Anexo A (condição de referência: Tabelas A.1–A.8 por tipologia, A.9
    componentes) p. 63–80. Anexo B (método simplificado) p. 81–124.
  - Anexo C (método de simulação) p. 125: C.I simulação termoenergética
    p. 126–140 (C.I.3 p. 127, C.I.4 modelo de referência p. 129, C.I.5 p. 132,
    C.I.6 conforto/PHOCT p. 133, C.I.7 híbridas p. 135; Tabelas C.1 e C.3
    p. 136, C.2 rotinas p. 133); C.II iluminação natural p. 141.
  - Anexos D (geração renovável) p. 146, E (CO₂) p. 147, F (água) p. 149,
    G (classificação climática) p. 153.
  - Anexo II, INI-R (residenciais), p. 155–222. Anexo III, RAC (avaliação da
    conformidade, ENCE, inspeção) p. 223–314. Anexo IV, selo, p. 315.
- `definicoes_inic` — Manual de definições da INI-C (ago/2023): cada termo com
  explicação e figuras (APP, APT, PAF, FF, aberturas, sombreamento…).
- `definicoes_inir` — Manual de definições da INI-R (mai/2025).

## Siglas que mais aparecem

APP/APT: ambiente de permanência prolongada/transitória. CgTT: carga térmica
total anual (refrigeração CgTR + aquecimento CgTA). RedCgTT: redução da CgTT
do real em relação à referência. CRCgTT(D-A): coeficiente de redução da CgTT
de D para A. FF: fator de forma (área da envoltória / volume). PAF: percentual
de abertura da fachada. PHOCT: percentual de horas ocupadas em conforto
térmico. FHdesc: fração de horas em desconforto. ZB: zona bioclimática
(classificação climática, Anexo G). CEP/RedCEP: consumo de energia primária e
sua redução (classificação geral, 8.1). ENCE: etiqueta. OIA-EEE: organismo de
inspeção acreditado.

## Classificação da envoltória (8.2.1)

RedCgTT = (CgTTrefD − CgTTreal) / CgTTrefD · 100 (Equação B.I.1, p. 81). Intervalo i = CRCgTT·100/3
(Equação 8.10); CRCgTT pela tipologia (Tabelas 8.12–8.19), pela ZB e pela
faixa de FF. Classes (Tabela 8.11): A acima de 3i, B acima de 2i, C acima de
i, D de 0 a i, E com carga do real maior que a da referência. Tipologia
mista: a dominante (maior área). Blocos: um FF e uma classe por bloco.

## O que o Ambiens implementa (página Etiquetagem / `cli.py --inic`)

- Só a envoltória pelo método de simulação (Anexo C.I). Não calcula a
  classificação geral (CEP), nem condicionamento, iluminação, água quente,
  método simplificado, INI-R ou ENCE.
- Modelos, cada um uma execução no módulo ENERGYPLUS_ONLY, ano inteiro:
  real; referência (paredes, cobertura, piso e vidro da Tabela A.9, PAF da
  tipologia, sem brises/beirais da própria edificação; o entorno fica); no
  modo híbrido, também o real ventilado (`real_vn`), janelas abrindo por
  temperatura, para o PHOCT.
- Nos dois: rotina de ocupação da Tabela C.2 pela tipologia (dias úteis),
  densidade do uso escolhido, sistema ideal 21/24 °C nas horas ocupadas
  (aquecimento só nas ZB 1–2 ou se o IDF aquece, C.I.3), ar externo
  5 L/s·pessoa + 0,6 L/s·m², frestas da Tabela C.3. As APP são as zonas
  escolhidas na página Etiquetagem (ou `rooms` na CLI); as demais são APT.
- PAF de referência: janelas escaladas no lugar; se não cabem, viram faixa de
  painéis na parede e o aviso diz o PAF obtido. No varejo e no mercado, PAF
  da fachada principal (paredes a até 45° da orientação escolhida) e das
  demais.
- Nota: CgTT somada nas APP; FF da geometria do IDF; ZB pelo município mais
  próximo do local do EPW (CSV oficial do PBE Edifica) ou escolhida. Híbrido:
  FHdesc = (100 − PHOCT)/100 desconta só a refrigeração do real; PHOCT ≥ 90 %
  dá A. Conforto pelo modelo adaptativo (80 % de aceitabilidade): fora da
  faixa de média externa 10–33,5 °C, a hora conta como desconforto.
- Simplificações conhecidas: o FF não aplica as exclusões da definição de
  área da envoltória; aberturas zenitais ficam como no real (Tabela C.1 não
  aplicada); só geometria detalhada e HVAC por `HVACTemplate`.
- Resultado gravado em `ETIQUETAGEM.json` de cada execução do grupo; leia com
  a ferramenta `etiquetagem`.
