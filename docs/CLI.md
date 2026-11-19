# Execução por linha de comando

Guia para agentes automatizados rodarem as simulações do Ambiens
sem interface gráfica.

## 1. Pré-requisitos

| Requisito | Detalhe |
|---|---|
| EnergyPlus | Versão 9.4 instalada (ex.: `/usr/local/EnergyPlus-9-4-0`). A pasta precisa conter `Energy+.idd`, `ExpandObjects` e o pacote `pyenergyplus`. |
| Python | 3.10+ |
| Dependências | `eppy`, `numpy`, `pandas`, `pythermalcomfort`, `ladybug-comfort`, `esoreader`, `matplotlib`, `openpyxl` |
| Arquivo IDF | Modelo EnergyPlus com as zonas e schedules esperados (ver seção 6) |
| Arquivo EPW | Arquivo climático |

Ambiente virtual do repositório: `.venv` (já criado nesta máquina, com todas as
dependências). Para recriar:

```bash
python -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Verificação rápida:

```bash
.venv/bin/python -c "import eppy, esoreader, pythermalcomfort; print('deps ok')"
ls /usr/local/EnergyPlus-9-4-0/Energy+.idd /usr/local/EnergyPlus-9-4-0/ExpandObjects
```

## 2. Comando

```bash
.venv/bin/python cli.py [--config CAMINHO] [--set CHAVE=VALOR ...] [--print-config] [--quiet]
```

### Smoke test real, com período curto

Para verificar o pipeline inteiro sem executar o ano completo:

```bash
.venv/bin/python scripts/smoke_energyplus.py
```

O script copia o IDF de exemplo para um diretório temporário próprio, reduz o
`RunPeriod` a 5–11 de janeiro de 2015 (incluindo dias úteis após os dois dias
descartados no pós-processamento), seleciona só `ATELIE1`, valida a configuração
e roda o CLI. Ao terminar, confere `eplusout.end`, ausência de erros fatais no
`eplusout.err`, as planilhas e a configuração salva. O caminho da execução é
impresso na saída; cada chamada usa uma pasta nova. Para conferir apenas a
preparação, sem simular, use `--prepare-only`. O teste curto verifica integração,
não substitui uma rodada anual para comparar resultados agregados.

Executar sempre a partir da raiz do repositório (os imports são `src.*` e os
caminhos padrão da configuração são relativos).

| Flag | Efeito |
|---|---|
| `--config` | JSON de configuração. Padrão: `examples/config.json`. |
| `--set CHAVE=VALOR` | Sobrescreve um campo. Repetível. O valor é lido como JSON quando possível (`1.3`, `true`, `["ATELIE1"]`); caso contrário vira string. |
| `--print-config` | Imprime a configuração final resolvida e sai **sem simular**. Use para validar antes de gastar horas de CPU. |
| `--quiet` | Suprime o resumo de progresso ao final. |

Código de saída: `0` sucesso, `1` falha (mensagem em stderr).

### Exemplos

Rodar a configuração padrão em um diretório de saída próprio:

```bash
.venv/bin/python cli.py --set output_path=./outputs/run_001
```

Trocar modelo, clima e módulo:

```bash
.venv/bin/python cli.py \
  --set idf_path=./examples/idf/FAURB/FAURB_PTHP_ENTORNO.idf \
  --set epw_path=./examples/epw/BRA_RS_Camaqua.869890_INMET.epw \
  --set energy_path=/usr/local/EnergyPlus-9-4-0 \
  --set module_type=CLOSED_WINDOW \
  --set 'rooms=["ATELIE1","ATELIE2"]' \
  --set output_path=./outputs/run_closed_window
```

Validar sem simular:

```bash
.venv/bin/python cli.py --print-config --set output_path=./outputs/x
```

## 3. Configuração

`examples/config.json` é a base. Campos (classe `SimulationConfig` em
`confortimetro/config.py`):

### Caminhos

| Campo | Tipo | Descrição |
|---|---|---|
| `_idf_path` | str | Caminho do IDF de entrada. **No JSON o nome tem underscore**; em `--set` use `idf_path` (é uma property). |
| `epw_path` | str | Arquivo climático `.epw`. |
| `output_path` | str | Diretório da execução. Criado se não existir. **Vazio (o padrão) gera uma subpasta com carimbo de data na pasta de dados da aplicação** — `~/.local/share/Ambiens/execucoes` no Linux, `%LOCALAPPDATA%\\Ambiens` no Windows, `~/Library/Application Support` no macOS. A variável `AMBIENS_DATA_DIR` (ou `CONFORTIMETRO_DATA_DIR`) troca essa raiz. |
| `runs_root_path` | str | Raiz que guarda as execuções (a GUI mostra este campo em "Pasta das execuções"). Vazio: a pasta de dados da aplicação. Cada rodada da GUI cria uma subpasta nova dela e grava em `output_path`. |
| `energy_path` | str | Raiz da instalação do EnergyPlus. Detectado automaticamente quando o valor gravado no config não existe (veja abaixo). |
| `input_path` | str | Derivado de `idf_path` (diretório do IDF). Não defina manualmente. |
| `expanded_idf_path` | str | Derivado: `<output_path>/expanded.idf`. Não defina manualmente. |
| `source_idf_path` | str | Preenchido na execução com o IDF escolhido pelo usuário, antes de `idf_path` passar a apontar para a cópia da execução. Não defina manualmente. |
| `idf_filename` | str | Derivado. Não defina manualmente. |

### Conforto e controle

| Campo | Padrão | Descrição |
|---|---|---|
| `_met` / `met` | 1.2 | Taxa metabólica. Setar `met` recalcula `met_as_watts = met * 58.1 * 1.8`. |
| `met_as_watts` | derivado | Não defina manualmente. |
| `wme` | 0.0 | Trabalho mecânico externo. |
| `clo_min` / `clo_max` / `clo_delta` | 0.5 / 1.0 / 0.1 | Faixa e passo de vestimenta. |
| `clo_priority` | true | `true`: varre a grade de clo antes de acionar ventilador/AC. `false`: comportamento antigo — o clo só é ajustado um passo por vez junto com os equipamentos. |
| `pmv_lowerbound` / `pmv_upperbound` | -0.5 / 0.5 | Faixa de PMV aceitável. |
| `pmv_comfort_bound` | 0.2 | Margem de conforto usada nas decisões do controlador. |
| `adaptative_bound` | 2.5 | Banda do modelo adaptativo: `2.5` = 90% de aceitação, `3.5` = 80%. |
| `temp_ac_min` / `temp_ac_max` | 18.0 / 30.0 | Limites de setpoint do ar-condicionado (°C). |
| `temp_open_window_bound` | 5.0 | Margem (°C) para abrir a janela; nas propostas do assistente, maior que 0 e até 15. |
| `max_vel` | 1.2 | Velocidade máxima do ar (m/s) do ventilador. |
| `air_speed_delta` | 0.15 | Passo de incremento da velocidade do ar. |
| `co2_limit` | 900.0 | Limite de CO₂ (ppm) que aciona a ventilação/DOAS. |
| `rooms` | lista | Zonas do IDF processadas e exportadas. |
| `module_type` | `COMPLETE` | Estratégia de condicionamento (seção 4). |
| `ignore_missing_equipment` | `false` | Roda mesmo com zonas sem o equipamento exigido pelo módulo (só avisa no log). |
| `nome` | — | Nome da execução na listagem e na comparação; sem ele, IDF e hora de início. |
| `code_version` | — | Preenchido pela simulação: `versao` do `pyproject.toml` (no executável, a do build), `commit` do git e `alteracoes_locais` em `control/`/`idf/`. Não é para editar. |

## 4. Módulos de condicionamento (`module_type`)

Definidos em `confortimetro/control/`, mapeados em `MODULES_MAPPER`:

| Valor | Classe | Comportamento |
|---|---|---|
| `COMPLETE` | `ConditionerComplete` | Estratégia completa: janela, ventilador, ar-condicionado e DOAS. |
| `CLOSED_WINDOW` | `ConditionerClosedWindow` | Janela sempre fechada. |
| `FIXED_AC_WITHOUT_FAN` | `ConditionerFixedAcWithoutFan` | Setpoint fixo de ar-condicionado, sem ventilador. |
| `WITHOUT_FAN` | `ConditionerWithoutFan` | Sem ventilador, demais estratégias ativas. |
| `ENERGYPLUS_ONLY` | — (sem controlador) | Só roda o EnergyPlus: nenhum callback/atuador, IDF idêntico ao escolhido (nem `Output:Variable` são acrescentadas; só o ExpandObjects, etapa do próprio EnergyPlus, expande `HVACTemplate:*` em `expanded.idf`). Sem planilhas nem estatísticas do Ambiens; ficam os `eplusout.*` na pasta da execução. |

### Equipamento exigido por zona

Cada módulo controla um conjunto de equipamentos, e o controlador escreve nos
schedules `JANELA_<zona>`, `VENT_<zona>` e `AC_<zona>` a cada timestep. Se
nenhum objeto do IDF consome esses schedules, a zona simula decidindo no vazio
— `validate_idf` barra isso antes do EnergyPlus (`unwired_equipment`).

Na GUI, o clique em simular oferece a correção: `plan_equipment_fixes` copia o
equipamento de outra zona do próprio IDF (o `HVACTemplate:Zone:*` com o
thermostat e o DOAS junto, o `ElectricEquipment` do ventilador) e liga a janela
já modelada no `AirflowNetwork` ao schedule. As vazões copiadas viram
`autosize`, e `apply_equipment_fixes` grava tudo num `<nome>_equipamentos.idf`
ao lado do original, que passa a ser o modelo da simulação — o arquivo
escolhido pelo usuário nunca é reescrito. Quando o IDF tem mais de um sistema
de ar-condicionado, um seletor pergunta de qual zona copiar antes de montar o
plano (`plan_equipment_fixes` aceita `template_rooms`), e a confirmação diz o
que entra; o log lista, por zona, cada objeto criado com o nome — do
`HVACTemplate:Zone:*` aos schedules e ao DOAS que ele arrasta junto. O que exige decisão de modelagem
(janela sem abertura no `AirflowNetwork`, ar-condicionado num IDF que não tem
nenhum para copiar) continua como aviso, e aí resta rodar assim mesmo com
`ignore_missing_equipment`.

## 5. O que acontece durante a execução

`Simulation.run()` (`confortimetro/simulation.py`), em ordem:

1. **Módulo condicionador** instanciado a partir de `module_type`.
2. **Diretório da execução** criado, com o modelo copiado para
   `<output_path>/modelo.idf`; é essa cópia que segue no pipeline.
3. **Processamento do IDF** (`confortimetro/idf/processor.py`): valida e insere
   os schedules de controle (`AC_*`, `VENT_*`, `JANELA_*`, `PMV_*`, `ADAP_*`,
   `EM_CONFORTO_*`, `DOAS_STATUS_*`, `MOTIVO_*`, `METABOLISMO`, `WORK_EF`, …). **Grava o IDF
   modificado no lugar.**
4. **ExpandObjects**: copia o IDF para `<output_path>/in.idf`, roda o binário
   `ExpandObjects` (timeout 300 s) e gera `<output_path>/expanded.idf`.
5. **Configuração efetiva** salva em `<output_path>/configs.json`.
6. **EnergyPlus** roda via `pyenergyplus`, com o condicionador registrado no
   callback `begin_zone_timestep_after_init_heat_balance` — ou seja, o controle
   de conforto é decidido a cada timestep dentro da simulação.
7. **Pós-processamento**: `eplusout.eso` → uma planilha por zona → estatísticas →
   recorte por período.

Uma simulação anual leva de dezenas de minutos a horas. O EnergyPlus escreve
seu progresso direto no stdout (`Warming up`, `Starting Simulation at …`).
Ao final de cada etapa, linhas `Tempo ...: N s` mostram duração do IDF,
ExpandObjects, EnergyPlus, leitura do ESO, escrita das planilhas, estatísticas,
recortes por sala e tempo total. Elas também aparecem no log da GUI.

### Lote de execuções independentes

Prepare um JSON por execução, cada um com um `output_path` diferente e vazio,
e execute a partir da raiz do repositório:

```bash
.venv/bin/python scripts/lote.py --workers 2 configs/rodada_1.json configs/rodada_2.json
```

Cada simulação roda em um processo próprio, com log em `<output_path>/lote.log`.
O lote rejeita saídas repetidas ou já preenchidas e retorna erro se alguma
simulação falhar. Ajuste `--workers` à memória, CPU e disco disponíveis;
isso reduz o tempo do conjunto, não a duração de cada simulação.

## 6. Requisitos do IDF

O IDF precisa já conter, por zona listada em `rooms`, os objetos que o
processador altera — em especial `PEOPLE_<ZONA>`, o PTHP `<ZONA> PTHP` e os
schedules citados acima. Um IDF genérico de outra fonte **não** funciona sem
adaptação; use `examples/idf/FAURB/FAURB_PTHP_ENTORNO.idf` como
referência. Erros de validação abortam antes da simulação com
`Erros de validação do IDF: [...]`.

O intervalo de simulação (`RunPeriod`) e `timesteps_per_hour` vêm do IDF, e o
pós-processamento os relê de lá (`read_run_period`, `read_timesteps_per_hour`)
para carimbar as datas — modelos de outro ano ou com outro passo funcionam. O
descarte inicial é de dois dias de aquecimento, proporcional ao timestep.

## 7. Saídas em `output_path`

| Arquivo | Conteúdo |
|---|---|
| `configs.json` | Configuração efetiva daquela execução. |
| `eplusout.eso` / `.csv` / `.err` / `.eio` / `.rdd` | Saídas brutas do EnergyPlus. Comece o diagnóstico por `eplusout.err`. |
| `<ZONA>.xlsx` | Série temporal por zona: temperatura externa, ocupação, PMV, temperatura operativa, CO₂, estados de janela/ventilador/AC/DOAS e energia elétrica por timestep (AC, ventilador, demais equipamentos, iluminação e total). Também `MOTIVO_<ZONA>:Schedule Value` — soma de bits com o porquê das decisões do controlador (`confortimetro.control.motivos.decode` devolve os nomes; tabela no PRD 004) — e as variáveis de diagnóstico: demanda sensível até os setpoints, potência das serpentinas, setpoints do termostato, balanço de calor do ar da zona e ganho/perda pelas janelas. IDFs processados antes do `MOTIVO_*` simulam normalmente, sem a coluna. |
| `ESTATISTICAS.xlsx` | Uma linha por zona com frações do tempo ocupado (`Aquecimento (%)`, `Resfriamento (%)`, `Ventilador ligado (%)`, `Janela aberta (%)`, `DOAS ligado (%)`, `Desconforto (%)`, `CO2 máximo (ppm)`), o consumo anual (`Aquecimento (kWh)`, `Resfriamento (kWh)` — incluindo AC e ventilador —, `Ventilador (kWh)`, `Energia total (kWh)`, incluindo equipamentos e iluminação), o conforto agregado (`PMV médio`, `PMV fora da faixa (%)` — o PMV do controlador, ASHRAE 55 com o efeito do ventilador, recalculado das séries de ar, radiante, UR, `VEL_` e `CLO_`; o Fanger do EnergyPlus fica só na série `pmv_fanger` —, `Fora da banda adaptativa (%)`) e `Timesteps simulados` — é ele que denuncia execuções de períodos diferentes, que não podem ser comparadas pelos totais. |
| `<ZONA>_SPLIT.xlsx` | Recorte por período de cada zona (`VERAO`, `INVERNO`, `DIAS_VERAO`, `DIAS_INVERNO`), só com as horas ocupadas. O ano dos recortes vem da própria planilha. |

## 7.1 Comparar várias execuções

`scripts/comparar.py` junta os `ESTATISTICAS.xlsx` de várias execuções em um CSV
único, com as colunas do `configs.json` (módulo, IDF, EPW, clo, banda adaptativa,
…) ao lado das métricas — é a entrada para os gráficos de comparação.

```bash
.venv/bin/python scripts/comparar.py                         # o que já tem estatísticas completas
.venv/bin/python scripts/comparar.py --recompute             # regera as antigas, sem colunas de energia
.venv/bin/python scripts/comparar.py --runs 'FAURB_ENTORNO_*' --out ./outputs/entorno.csv
```

`--recompute` relê todas as planilhas por zona das execuções desatualizadas (em
paralelo, `--workers`); leva minutos por execução, mas não simula nada de novo.

`--sync` ingere os agregados em `outputs/simulacoes.db` (SQLite, criado
sozinho) e passa a ler de lá: as métricas ficam em formato longo, uma linha por
métrica, e cada ingestão guarda o seu carimbo de tempo — a consulta devolve a
mais recente e o histórico das anteriores continua no banco
(`confortimetro/results/database.py`, função `history`). Uma execução só é
relida quando o `mtime` do seu `ESTATISTICAS.xlsx` muda.

`--graficos DIR` salva em PNG os gráficos que se fazem só com os agregados
(energia × desconforto, consumo por execução, acionamentos e diferença contra a
referência). Os gráficos de série temporal saem pela interface, que consegue
avisar da espera.

```bash
.venv/bin/python scripts/comparar.py --sync --room ATELIE1 --graficos ./outputs/graficos
```

## 7.2 Séries temporais e cache

`confortimetro/results/series.py` lê um `<ZONA>.xlsx` e devolve as colunas com
nomes curtos (`pmv`, `temp_operativa`, `janela`, …). Abrir a planilha leva ~22 s,
então a primeira leitura grava um pickle em `<execução>/.series_cache/` e as
seguintes saem dele em milissegundos, invalidado pelo `mtime` da planilha. O
cache pesa ~12 MB por zona; `clear_cache(run_path)` apaga o de uma execução.

A coluna `em_conforto` (`EM_CONFORTO_<zona>`) entrou depois: um cache gravado
antes dela fica sem a coluna até a planilha mudar ou o cache ser apagado.

Na GUI, **Detalhes da execução → Série temporal** mostra a série de uma zona
timestep a timestep (temperaturas e banda adaptativa, PMV com a faixa de conforto,
estados do controle, CO₂ e energia). `window_series` recorta a janela visível: até
~1 ponto por pixel aparecem os timesteps brutos; acima disso, média e faixa
mín.–máx. por bloco, com estados pelo máximo e `em_conforto` pelo mínimo, para um
acionamento ou um desconforto de um timestep não sumir. Clicar seleciona o timestep
real mais próximo; ← e → andam um timestep. Desenho em
[`docs/tdd/001-serie-temporal-nos-detalhes.md`](tdd/001-serie-temporal-nos-detalhes.md).

## 8. Armadilhas ao automatizar

- **Execuções paralelas não colidem mais.** `modelo.idf`, `in.idf` e
  `expanded.idf` são gravados dentro do `output_path` da execução.
- **O IDF de entrada não é modificado.** A simulação copia o modelo para
  `<output_path>/modelo.idf` e é essa cópia que o `IDFProcessor` altera.
- **`output_path` é reutilizado** se já existir; resultados antigos podem se
  misturar aos novos. Use um diretório novo por execução.
- **Sempre execute a partir da raiz do repositório.**
- **Falha silenciosa de dependência**: sem `openpyxl`/`pandas` a simulação roda
  inteira e só quebra no pós-processamento. Valide as dependências antes.
- **Erros agora abortam.** O pipeline checa: handlers do EnergyPlus ausentes
  (aborta no primeiro timestep), exceções dentro do callback do condicionador
  (guardadas e relançadas depois do run — o ctypes as engolia), código de saída
  do `run_energyplus`, `eplusout.end` (status oficial do EnergyPlus),
  comprimento das séries do `.eso` contra o período esperado e falha ao gravar
  cada `<ZONA>.xlsx`. Qualquer um deles faz o `cli.py` sair com código 1.
- **Sem streaming de progresso**: as mensagens do pipeline vão para uma `Queue`
  e são impressas ao final. O sinal de vida durante a execução é o stdout do
  próprio EnergyPlus.
- Para retomar o pós-processamento de uma simulação já rodada sem simular de
  novo, use as funções de `confortimetro/results/`
  (`summary_rooms_results_from_eso`, `get_stats_from_simulation`,
  `split_target_period_excel`) diretamente sobre o `output_path`.

## 9. Diagnóstico

| Sintoma | Causa provável |
|---|---|
| `No module named 'pyenergyplus'` | `energy_path` errado ou EnergyPlus não instalado. |
| `IDD file not found: <path>/Energy+.idd` | `energy_path` não aponta para a raiz da instalação. |
| `Erros de validação do IDF: [...]` | O IDF não tem os objetos/zonas esperados; confira `rooms`. |
| `ExpandObjects falhou: ...` | IDF inválido ou binário `ExpandObjects` ausente/sem permissão. |
| `Timeout ao expandir objetos` | ExpandObjects passou de 300 s. |
| `Campo desconhecido em --set: X` | Nome de campo inválido; confira com `--print-config`. |
| Simulação termina mas faltam `.xlsx` | Falha no pós-processamento; veja o traceback e `eplusout.err`. |
| `File <ZONA>.xlsx not found! Skipping...` | A zona não existe no `.eso`; nome errado em `rooms`. |

## Etiquetagem da envoltória (INI-C)

Estimativa da classe da envoltória pelo método de simulação da INI-C (Anexo I
da Portaria Inmetro 309/2022; PDFs em `docs/material/Normas PBE Edifica/`).
As `rooms` são as APP avaliadas; as demais zonas são APT.

```bash
.venv/bin/python cli.py --inic condicionado --tipologia educacional \
    --uso "Ensino superior" --set 'rooms=["SALA_AULA","ATELIE1"]'   # --zb 2 opcional
.venv/bin/python cli.py --inic hibrido ...    # + modelo real com janelas abrindo (PHOCT)
.venv/bin/python cli.py --inic condicionado --tipologia varejo --fachada NE ...
```

Tipologias (`--tipologia`): `educacional`, `escritorio`, `hospedagem`, `saude`
(EAS exceto hospitais), `varejo`, `mercado`, `alimentacao` e `outra`. No
varejo e no mercado o PAF de referência é 60 % na fachada principal e 5 % ou
10 % nas demais: `--fachada` (N, NE, L, SE, S, SO, O, NO) diz para onde ela
olha; as paredes a até 45° dela formam a fachada principal.

- Gera `modelo_inic.idf` (`confortimetro/etiquetagem/modelos.py`) para cada
  papel — `real`, `referencia` e, no híbrido, `real_vn` — e simula cada um como
  uma execução comum no módulo somente EnergyPlus, o ano inteiro. Pastas:
  `<grupo>`, `<grupo>_inic_real_vn`, `<grupo>_inic_referencia`.
- Nos dois modelos: rotina e densidade de ocupação da tipologia, sistema ideal
  21/24 °C só nas horas ocupadas (aquecimento nas ZB 1–2 ou se o IDF aquece),
  ar externo 5 L/s·pessoa + 0,6 L/s·m², frestas da Tabela C.3. APP sem
  `People` ganha um. Só a referência: paredes, cobertura, piso e vidro da
  Tabela A.9, PAF da tipologia (janelas escaladas no lugar; sem caber, viram
  uma faixa de painéis na parede) e sem brises/beirais (`Shading:Zone`, …;
  `Shading:Building`/`Site` ficam como entorno).
- A nota (`ETIQUETAGEM.json` em cada pasta do grupo) sai de
  `etiquetagem/avaliacao.py`: RedCgTT contra os limites do CRCgTT (Tabelas
  8.12 a 8.19) pelo fator de forma e pela ZB (automática pelo município
  mais próximo do local do EPW). No híbrido, FHdesc desconta só a
  refrigeração e PHOCT ≥ 90 % dá A.
- Recusa geometria simplificada (`Wall:Exterior`, `Window`…) e HVAC fora de
  `HVACTemplate`. Os avisos (PAF, ático, entorno) vão para o log e o cartão da
  comparação.

## 10. Referência de código

| Caminho | Papel |
|---|---|
| `cli.py` | Entrada por linha de comando. |
| `main.py` | Entrada da interface Tkinter. |
| `confortimetro/simulation.py` | Orquestração do pipeline. |
| `confortimetro/idf/processor.py` | Modificações no IDF via eppy. |
| `confortimetro/control/` | Controladores de conforto por timestep. |
| `confortimetro/config.py` | Esquema de configuração. |
| `confortimetro/results/` | Pós-processamento e planilhas. |
| `confortimetro/etiquetagem/` | Etiquetagem INI-C: tabelas, modelos real/referência e nota. |

## Detecção do EnergyPlus

Quando o `energy_path` do config está vazio ou não aponta para uma instalação
válida (a pasta precisa conter **`Energy+.idd`**, **`pyenergyplus/api.py`** e
a biblioteca da API **da plataforma atual** — `energyplusapi.dll` no Windows,
`libenergyplusapi.so` no Linux, `libenergyplusapi.dylib` no macOS),
`find_energy_path()` procura sozinho, nesta ordem, e prefere sempre a versão
9.4:

1. variável de ambiente `ENERGYPLUS_DIR`;
2. `energyplus` no `PATH`;
3. diretórios padrão da plataforma — `C:\EnergyPlusV*` e
   `%ProgramFiles%\EnergyPlusV*` no Windows, `/usr/local/EnergyPlus-*` e
   `/opt/EnergyPlus-*` no Linux, `/Applications/EnergyPlus-*` no macOS;
4. no Windows, as instalações registradas no desinstalador (registro).

A exigência da biblioteca vem do dual boot: no Windows, o
`/usr/local/EnergyPlus-9-4-0` do `examples/config.json` vira
`E:\usr\local\EnergyPlus-9-4-0` quando o drive atual é a partição do Linux, e
essa instalação tem IDD e `api.py`, mas só o `.so`.

Instalação em lugar não convencional: exporte `ENERGYPLUS_DIR` ou passe
`--set energy_path=...`. Na interface gráfica há o botão **🔍 Detectar** ao lado
do campo, e o status embaixo dele diz se a instalação é válida e se a versão é a
esperada.
