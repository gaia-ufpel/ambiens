"""Assistente de análise: ferramentas, laço de function calling e histórico.

Sem rede: o cliente Gemini é trocado por um falso que devolve respostas
roteirizadas. O teste com a API de verdade só roda com GEMINI_API_KEY.
"""

import os
import threading

import pytest

pytest.importorskip("google.genai")
from google.genai import types  # noqa: E402

from confortimetro.assistant import simulacao, store  # noqa: E402
from confortimetro.assistant.client import Assistant, AssistantError, Cancelled  # noqa: E402
from confortimetro.assistant.tools import MAX_ROWS, Toolbox  # noqa: E402
from confortimetro.control.base import _pmv  # noqa: E402
from confortimetro.results.compare import recompute_run  # noqa: E402
from tests.test_compare import _make_run  # noqa: E402
from tests.test_stats import ROOM  # noqa: E402

# Lida antes da fixture que limpa o ambiente de cada teste.
REAL_KEY = os.environ.get("GEMINI_API_KEY")


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Configurações, chave e conversas numa pasta descartável."""
    monkeypatch.setenv("AMBIENS_DATA_DIR", str(tmp_path / "dados"))
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    return tmp_path / "dados"


@pytest.fixture
def root(tmp_path):
    outputs = tmp_path / "execucoes"
    outputs.mkdir()
    for name, module, heating in (("COM_JANELA", "COMPLETE", 10.0),
                                  ("FECHADA", "CLOSED_WINDOW", 4.0)):
        recompute_run(str(_make_run(outputs, name, module, heating_kwh=heating)))
    return str(outputs)


# --- Ferramentas ---------------------------------------------------------

def test_listar_e_indicadores(root):
    tools = Toolbox(root)
    names = [row["execucao"] for row in tools.call("listar_execucoes", {})["linhas"]]
    assert sorted(names) == ["COM_JANELA", "FECHADA"]

    rows = tools.call("indicadores", {"execucao": "COM_JANELA", "zona": ROOM})["linhas"]
    assert rows[0]["Energia total (kWh)"] == pytest.approx(10.0)


def test_comparar(root):
    result = Toolbox(root).call("comparar", {"execucoes": ["COM_JANELA", "FECHADA"]})
    assert [row["Energia total (kWh)"] for row in result["linhas"]] == pytest.approx([10.0, 4.0])


def test_nome_de_execucao_nao_sai_da_raiz(root):
    tools = Toolbox(root)
    for name in ("../COM_JANELA", "..", os.path.join("x", "y"), "NAO_EXISTE"):
        assert "erro" in tools.call("configuracao", {"execucao": name})


def test_serie_agregada_soma_energia_em_kwh(root):
    result = Toolbox(root).call("serie_agregada", {
        "execucao": "COM_JANELA", "zona": ROOM, "variaveis": ["pmv", "aquecimento"],
        "agregacao": "dia"})
    (row,) = result["linhas"]
    assert row["data"] == "2015-01-01"
    # PMV do controlador recalculado das séries (ar parado a 25 °C, 0,5 clo).
    assert row["pmv_media"] == pytest.approx(_pmv(25.0, 25.0, 0.0, 50.0, 1.2, 0.5, 0.0), abs=1e-3)
    assert row["aquecimento_kwh"] == pytest.approx(10.0)  # 10 timesteps × 1 kWh


def test_horas_em_condicao_filtro_estruturado(root):
    tools = Toolbox(root)
    result = tools.call("horas_em_condicao", {
        "execucao": "COM_JANELA", "zona": ROOM,
        "condicoes": [{"coluna": "ac", "operador": "==", "valor": 1},
                      {"coluna": "ocupacao", "operador": ">", "valor": 0}]})
    # 10 timesteps de 10 min com AC ligado.
    assert result["horas"] == pytest.approx(10 / 6, abs=0.01)
    assert result["linhas"][0]["mes"] == "2015-01"

    # Nada de expressão livre: operador fora da lista é recusado.
    bad = tools.call("horas_em_condicao", {
        "execucao": "COM_JANELA", "zona": ROOM,
        "condicoes": [{"coluna": "ac", "operador": "__import__('os')", "valor": 1}]})
    assert "erro" in bad


def test_periodo_mm_dd_usa_o_ano_da_serie(root):
    result = Toolbox(root).call("horas_em_condicao", {
        "execucao": "COM_JANELA", "zona": ROOM, "inicio": "01-02", "fim": "01-03",
        "condicoes": [{"coluna": "ac", "operador": "==", "valor": 1}]})
    assert "erro" in result  # a série só tem 01/01


def test_serie_timestep_e_sinais_de_controle(root):
    tools = Toolbox(root)
    result = tools.call("serie_timestep", {
        "execucao": "COM_JANELA", "zona": ROOM, "inicio": "01-01",
        "variaveis": ["ocupacao", "aquecimento"]})
    assert len(result["linhas"]) == 10  # um valor por timestep, sem agregar
    assert result["linhas"][0]["aquecimento_w"] == pytest.approx(6000)  # 1 kWh em 10 min

    result = tools.call("sinais_controle", {"execucao": "COM_JANELA", "zona": ROOM,
                                            "inicio": "01-01"})
    assert result["resumo"]["horas_ac_ligado"] == pytest.approx(10 / 6, abs=0.01)
    assert "janela" in result["linhas"][0]


EPLUSTBL = f"""REPORT:,Input Verification and Results Summary
FOR:,Entire Facility
Zone Summary

,,Area [m2],Volume [m3],People [m2 per person]
,{ROOM},10.00,30.00,5.00

REPORT:,Envelope Summary
FOR:,Entire Facility
Opaque Exterior

,,Gross Area [m2],Tilt [deg],Cardinal Direction
,PAREDE_S,10.00,90.00,S
,OUTRA,99.00,90.00,N

Exterior Fenestration

,,Glass Area [m2],Parent Surface,Cardinal Direction
,JANELA_S,2.50,PAREDE_S,S

REPORT:,Equipment Summary
FOR:,Entire Facility
Heating Coils

,,Type,Nominal Total Capacity [W]
,{ROOM} PTHP HEATING COIL,Coil:Heating:DX:SingleSpeed,5000.00
,{ROOM} PTHP SUPP HEATING COIL,Coil:Heating:Electric,1000.00
,LINSE PTHP HEATING COIL,Coil:Heating:DX:SingleSpeed,9999.00

REPORT:,Sensible Heat Gain Summary
FOR:,Entire Facility
Annual Building Sensible Heat Gain Components

,,People Sensible Heat Addition [kWh],Window Heat Removal [kWh]
,{ROOM},12.5,-3.0
"""

EPLUS_IDF = f"""
BuildingSurface:Detailed, parede_s, Wall, EXTERIOR WALL, {ROOM}, Outdoors;
BuildingSurface:Detailed, outra, Wall, EXTERIOR WALL, OUTRA_ZONA, Outdoors;
FenestrationSurface:Detailed, janela_s, Window, EXTERIOR WINDOW, parede_s;
AirflowNetwork:MultiZone:Surface, janela_s, infiltracao, , 1;
"""


def test_relatorios_do_energyplus(root):
    run = os.path.join(root, "COM_JANELA")
    with open(os.path.join(run, "eplustbl.csv"), "w", encoding="latin-1") as handle:
        handle.write(EPLUSTBL)
    with open(os.path.join(run, "modelo.idf"), "w", encoding="latin-1") as handle:
        handle.write(EPLUS_IDF)
    tools = Toolbox(root)

    zone = tools.call("inspecionar_zona", {"execucao": "COM_JANELA", "zona": ROOM})
    assert zone["zona_resumo"]["pe_direito_m"] == pytest.approx(3.0)
    assert [row["nome"] for row in zone["superficies_externas"]] == ["PAREDE_S"]
    assert zone["wwr_por_direcao"] == [{"direcao": "S", "parede_bruta_m2": 10.0,
                                        "vidro_m2": 2.5, "wwr": 0.25}]
    assert zone["airflownetwork_superficies"][0]["componente"] == "INFILTRACAO"

    balance = tools.call("balanco_termico", {"execucao": "COM_JANELA", "zona": ROOM,
                                             "inicio": "01-01"})
    assert balance["relatorio_anual"]["total_kwh"]["Window Heat Removal [kWh]"] == -3.0
    assert "serie" not in balance and "anterior" in balance["avisos"][0]

    hvac = tools.call("inspecionar_hvac", {"execucao": "COM_JANELA", "zona": ROOM})
    heating = hvac["desempenho"]["aquecimento"]
    assert heating["capacidade_nominal_w"] == 6000  # DX + resistência, sem a do LINSE
    assert heating["horas_acima_90pct_capacidade"] == pytest.approx(10 / 6, abs=0.01)
    assert "anterior" in hvac["demanda_x_entrega"]  # planilha sem as colunas novas

    assert "erro" in tools.call("balanco_termico", {"execucao": "FECHADA", "zona": ROOM})


def _add_new_columns(root):
    """Planilha de COM_JANELA com as colunas de motivo, demanda e balanço."""
    from confortimetro.control.motivos import Motivo
    from confortimetro.results import series
    from tests.test_stats import _room_dataframe

    df = _room_dataframe(rows=10, nan_rows=0)
    def col(alias):
        return series.column(alias, ROOM)
    three = int(Motivo.OCUPADA | Motivo.JANELA_BLOQUEADA_EXTERNA_FRIA | Motivo.AC_MANTIDO)
    df[col("janela")] = [0, 0, 1, 1, 1, 0, 0, 0, 0, 0]
    df[col("motivo")] = [three] * 5 + [int(Motivo.OCUPADA)] * 5
    df[col("setpoint_aquecimento")] = 22.0
    df[col("demanda_aquecimento_w")] = [8000.0] * 3 + [3000.0] * 7
    df[col("demanda_resfriamento_w")] = -100.0
    df[col("serpentina_aquecimento_w")] = [5000.0] * 3 + [2000.0] * 7
    df[col("serpentina_apoio_w")] = [1000.0] * 3 + [0.0] * 7
    df[col("serpentina_resfriamento_w")] = 100.0
    df[col("balanco_superficies_w")] = [-600.0] * 5 + [600.0] * 5
    df[col("balanco_ganhos_internos_w")] = 300.0
    df[col("janelas_perda")] = 3.6e6 / 10
    run = os.path.join(root, "COM_JANELA")
    df.to_excel(os.path.join(run, f"{ROOM}.xlsx"), index=False)
    with open(os.path.join(run, "eplustbl.csv"), "w", encoding="latin-1") as handle:
        handle.write(EPLUSTBL)


def test_balanco_e_hvac_com_colunas_novas(root):
    _add_new_columns(root)
    tools = Toolbox(root)

    balance = tools.call("balanco_termico", {"execucao": "COM_JANELA", "zona": ROOM,
                                             "inicio": "01-01", "somente_ocupado": True})
    air = balance["serie"]["ar_da_zona_kwh"]
    # 5 timesteps de 10 min a -600 W e 5 a +600 W: 0,5 kWh de cada lado.
    assert air["superficies"] == {"ganho": 0.5, "perda": -0.5, "liquido": 0.0}
    assert air["ganhos_internos"]["ganho"] == pytest.approx(0.5)
    assert balance["serie"]["janelas_kwh"] == {"perda": pytest.approx(1.0)}
    assert balance["relatorio_anual"]["total_kwh"]["Window Heat Removal [kWh]"] == -3.0

    hvac = tools.call("inspecionar_hvac", {"execucao": "COM_JANELA", "zona": ROOM})
    heating = hvac["demanda_x_entrega"]["aquecimento"]
    assert heating["pico_demanda_w"] == 8000
    assert heating["horas_demanda_acima_capacidade"] == pytest.approx(0.5)  # 3 × 10 min
    assert heating["horas_entrega_abaixo_90pct_demanda"] == pytest.approx(10 / 6, abs=0.01)
    assert heating["horas_resistencia_apoio"] == pytest.approx(0.5)
    assert hvac["demanda_x_entrega"]["resfriamento"]["horas_entrega_abaixo_90pct_demanda"] == 0

    # Energia das janelas em W no timestep, como aquecimento.
    rows = tools.call("serie_timestep", {"execucao": "COM_JANELA", "zona": ROOM,
                                         "inicio": "01-01",
                                         "variaveis": ["janelas_perda", "motivo"]})["linhas"]
    assert rows[0]["janelas_perda_w"] == pytest.approx(600)
    assert rows[0]["motivos"] == ["OCUPADA", "JANELA_BLOQUEADA_EXTERNA_FRIA", "AC_MANTIDO"]


def test_sinais_controle_apenas_mudancas(root):
    _add_new_columns(root)
    result = Toolbox(root).call("sinais_controle", {
        "execucao": "COM_JANELA", "zona": ROOM, "inicio": "01-01",
        "apenas_mudancas": True, "colunas": ["janela", "motivo"]})
    rows = result["linhas"]
    # Janela abre no 3º, motivo muda no 6º: trechos de 2, 3 e 5 timesteps.
    assert [row["data"][-5:] for row in rows] == ["00:10", "00:30", "01:00"]
    assert [row["duracao_h"] for row in rows] == pytest.approx([2 / 6, 0.5, 5 / 6], abs=0.01)
    assert set(rows[0]) == {"data", "janela", "motivo", "motivos", "duracao_h"}
    assert rows[0]["motivos"] == ["OCUPADA", "JANELA_BLOQUEADA_EXTERNA_FRIA", "AC_MANTIDO"]
    by_motive = result["resumo"]["horas_por_motivo"]
    assert by_motive["OCUPADA"] == pytest.approx(10 / 6, abs=0.01)
    assert by_motive["AC_MANTIDO"] == pytest.approx(5 / 6, abs=0.01)
    assert "AC_LIGADO_POR_PMV" not in by_motive

    # Só a janela pedida: fecha e reabre contam, o motivo não.
    rows = Toolbox(root).call("sinais_controle", {
        "execucao": "COM_JANELA", "zona": ROOM, "inicio": "01-01",
        "apenas_mudancas": True, "colunas": ["janela"]})["linhas"]
    assert [row["janela"] for row in rows] == [0, 1, 0]


def test_sinais_controle_execucao_antiga(root):
    tools = Toolbox(root)
    result = tools.call("sinais_controle", {"execucao": "FECHADA", "zona": ROOM,
                                            "inicio": "01-01", "apenas_mudancas": True,
                                            "colunas": ["ac", "motivo"]})
    assert "erro" not in result
    (row,) = result["linhas"]  # nada muda no período: um trecho só
    assert row["duracao_h"] == pytest.approx(10 / 6, abs=0.01)
    assert "motivos" not in row and "horas_por_motivo" not in result["resumo"]
    assert any("motivo" in aviso for aviso in result["avisos"])
    assert "erro" in tools.call("sinais_controle", {"execucao": "FECHADA", "zona": ROOM,
                                                    "inicio": "01-01", "colunas": ["x"]})


def test_codigo_controle(root):
    tools = Toolbox(root)
    module = tools.call("codigo_controle", {"arquivo": "COMPLETE"})
    assert module["arquivo"].endswith("complete.py")
    assert "versao" in module["versao_instalada"]
    assert "   1| " in module["codigo"] and "room_conditioner" in module["codigo"]

    # A base vem como índice; as funções, com o número de linha real.
    index = tools.call("codigo_controle", {"arquivo": "base"})["indice"]
    start = next(row for row in index if row["funcao"] == "can_open_window")["linhas"]
    code = tools.call("codigo_controle", {"arquivo": "base",
                                          "funcoes": ["can_open_window"]})
    assert code["funcoes"]["can_open_window"].lstrip().startswith(start.split("-")[0] + "|")

    assert "erro" in tools.call("codigo_controle", {"arquivo": "base",
                                                    "funcoes": ["nao_existe"]})
    assert "erro" in tools.call("codigo_controle", {"arquivo": "../assistant/tools"})


def test_norma_guia_busca_e_leitura(root):
    tools = Toolbox(root)
    guia = tools.call("guia_norma", {})
    assert "INI-C" in guia["guia"]
    varejo = next(t for t in guia["tipologias_do_ambiens"] if t["tipologia"] == "varejo")
    assert varejo["paf"] == {"principal": 0.60, "demais": 0.05}

    # O valor da tabela que o Ambiens usa está no texto, na página que o guia aponta.
    achado = tools.call("buscar_norma", {"termos": '"Tabela 8.16"', "documento": "portaria"})
    assert achado["paginas"][0]["pagina"] == 56
    pagina = tools.call("ler_norma", {"documento": "portaria", "pagina": 56})
    assert "ZB 8  0,11  0,12  0,14  0,15" in pagina["paginas"][0]["texto"]

    assert "erro" in tools.call("ler_norma", {"documento": "portaria", "pagina": 9999})
    assert "erro" in tools.call("buscar_norma", {"termos": "xyzwq"})
    assert "erro" in tools.call("buscar_norma", {"termos": "a", "documento": "outra"})


def test_etiquetagem_da_execucao(root):
    from confortimetro.etiquetagem import avaliacao

    tools = Toolbox(root)
    assert "erro" in tools.call("etiquetagem", {"execucao": "COM_JANELA"})
    avaliacao.gravar({"classe": "B", "red_cgtt": 20.0}, [os.path.join(root, "COM_JANELA")])
    assert tools.call("etiquetagem", {"execucao": "COM_JANELA"})["resultado"]["classe"] == "B"


def test_resultado_grande_e_cortado(root, monkeypatch):
    tools = Toolbox(root)
    monkeypatch.setattr(tools, "listar_execucoes",
                        lambda: {"linhas": [{"i": i} for i in range(1000)]})
    result = tools.call("listar_execucoes", {})
    assert len(result["linhas"]) == MAX_ROWS and "aviso" in result


# --- Laço de function calling ---------------------------------------------

def _chunk(*parts, prompt_tokens=100):
    return types.GenerateContentResponse(
        candidates=[types.Candidate(content=types.Content(role="model", parts=list(parts)))],
        usage_metadata=types.GenerateContentResponseUsageMetadata(
            prompt_token_count=prompt_tokens))


class FakeModels:
    """Devolve, a cada chamada, a próxima lista de chunks do roteiro."""

    def __init__(self, script, summary="resumo"):
        self.script = list(script)
        self.requests = []
        self.summary = summary

    def generate_content_stream(self, model, contents, config):
        self.requests.append((contents, config))
        return iter(self.script.pop(0))

    def generate_content(self, model, contents, config):
        return types.GenerateContentResponse(candidates=[types.Candidate(
            content=types.Content(role="model", parts=[types.Part(text=self.summary)]))])


class FakeClient:
    def __init__(self, script, **kwargs):
        self.models = FakeModels(script, **kwargs)


def _call(name, **args):
    return types.Part(function_call=types.FunctionCall(name=name, args=args),
                      thought_signature=b"sig")


def test_chama_ferramenta_e_responde_em_streaming(root):
    client = FakeClient([
        [_chunk(_call("indicadores", execucao="COM_JANELA"))],
        [_chunk(types.Part(text="Consumo de ")),
         _chunk(types.Part(text="10 kWh.", thought_signature=b"fim"))],
    ])
    conversation = store.new_conversation(["COM_JANELA"])
    events = []
    answer = Assistant(conversation, root, client=client).ask(
        "Quanto consumiu?", on_event=lambda kind, data: events.append((kind, data)))

    assert answer == "Consumo de 10 kWh."
    assert ("text", "10 kWh.") in events
    assert any(kind == "status" and "indicadores" in data for kind, data in events)

    # A segunda chamada leva o resultado da ferramenta e a assinatura da chamada.
    contents, _ = client.models.requests[1]
    assert contents[1].parts[0].thought_signature == b"sig"
    result = contents[2].parts[0].function_response.response["resultado"]
    assert result["linhas"][0]["Energia total (kWh)"] == pytest.approx(10.0)

    # Tudo salvo em disco, com título e a versão dos resultados usados.
    saved = store.load_conversation(conversation["id"])
    assert saved["title"] == "Quanto consumiu?"
    assert len(saved["contents"]) == 4
    assert "COM_JANELA" in saved["run_mtimes"]
    assert store.visible_messages(saved) == [("user", "Quanto consumiu?"),
                                             ("model", "Consumo de 10 kWh.")]
    assert "10 kWh" in store.export_markdown(saved)


def test_limite_de_chamadas_forca_resposta(root):
    loop = [[_chunk(_call("listar_execucoes"))] for _ in range(3)]
    client = FakeClient(loop + [[_chunk(types.Part(text="fim"))]])
    settings = dict(store.DEFAULT_SETTINGS, max_tool_calls=2)

    Assistant(store.new_conversation(), root, settings, client=client).ask("oi")

    configs = [config for _, config in client.models.requests]
    assert configs[0].tool_config is None
    # Atingido o limite, a chamada seguinte proíbe ferramentas.
    assert configs[2].tool_config.function_calling_config.mode == "NONE"


def test_cancelar_nao_altera_a_conversa(root):
    cancel = threading.Event()
    cancel.set()
    client = FakeClient([[_chunk(types.Part(text="x"))]])
    conversation = store.new_conversation()
    with pytest.raises(Cancelled):
        Assistant(conversation, root, client=client).ask("oi", cancel=cancel)
    assert conversation["contents"] == []


def test_resposta_vazia_vira_erro(root):
    client = FakeClient([[types.GenerateContentResponse(candidates=[])]])
    with pytest.raises(AssistantError):
        Assistant(store.new_conversation(), root, client=client).ask("oi")


def test_resumo_substitui_historico_so_na_api(root):
    client = FakeClient([
        [_chunk(types.Part(text="primeira"), prompt_tokens=10)],
        [_chunk(types.Part(text="segunda"), prompt_tokens=900_000)],
        [_chunk(types.Part(text="terceira"))],
    ])
    conversation = store.new_conversation()
    assistant = Assistant(conversation, root, client=client)
    assistant.ask("um")
    assistant.ask("dois")  # passa de 700 mil tokens: resume o que veio antes

    assert conversation["summary"] == "resumo"
    assert conversation["summary_upto"] == 2
    assistant.ask("três")
    contents, _ = client.models.requests[2]
    assert "resumo" in contents[0].parts[0].text
    assert [c.parts[0].text for c in contents[2:]] == ["dois", "segunda", "três"]
    assert len(conversation["contents"]) == 6  # o disco guarda tudo


def test_execucao_recalculada_gera_aviso(root):
    conversation = store.new_conversation(["COM_JANELA"])
    conversation["run_mtimes"] = {"COM_JANELA": 1.0, "SUMIU": 1.0}
    stale = store.stale_runs(conversation, root)
    assert stale == {"COM_JANELA": "recalculada", "SUMIU": "apagada"}
    assert "recalculada" in Assistant(conversation, root, client=object())._system_prompt()


# --- Proposta de simulação -----------------------------------------------

def modelo_valido(tmp_path, zone="SALA1"):
    """IDF de 3 dias, EPW e uma "instalação" do EnergyPlus que passa na validação."""
    idf = tmp_path / "modelo.idf"
    idf.write_text(f"Zone,\n  {zone};\n\nPeople,\n  PEOPLE_{zone.upper()};\n\nRunPeriod,\n  Curto,\n  1,\n  5,\n  2015,\n"
                   "  1,\n  7,\n  2015,\n  Monday;\n\nTimestep,\n  6;\n")
    epw = tmp_path / "clima.epw"
    epw.write_text("LOCATION,Teste\n")
    energy = tmp_path / "EnergyPlus-9-4-0"
    (energy / "pyenergyplus").mkdir(parents=True)
    (energy / "Energy+.idd").write_text("")
    (energy / "pyenergyplus" / "api.py").write_text("")
    from confortimetro.config import energy_api_library
    (energy / energy_api_library()).write_text("")
    return str(idf), str(epw), str(energy)


def base_config(tmp_path, zone="SALA1"):
    from confortimetro.config import SimulationConfig
    idf, epw, energy = modelo_valido(tmp_path, zone)
    return SimulationConfig(met_as_watts=125.496, _idf_path=idf, _met=1.2, epw_path=epw,
                            energy_path=energy, rooms=[zone],
                            runs_root_path=str(tmp_path / "execucoes"))


def test_propor_simulacao_valida_e_nao_executa(root, tmp_path):
    tools = Toolbox(root, base_config(tmp_path))
    result = tools.call("propor_simulacao", {
        "alteracoes": [{"campo": "module_type", "valor": "CLOSED_WINDOW"},
                       {"campo": "temp_ac_max", "valor": "28"}],
        "motivo": "testar janela fechada"})

    assert "erro" not in result, result
    assert "aguardando confirmação" in result["status"]
    assert result["alteracoes"] == {"module_type": "CLOSED_WINDOW", "temp_ac_max": 28.0}
    assert result["periodo"]["dias"] == 3
    (proposal,) = tools.proposals
    assert proposal["status"] == "pendente"
    config = proposal["config"]
    assert config["module_type"] == "CLOSED_WINDOW" and config["temp_ac_max"] == 28.0
    # Sem pasta de saída ainda: nasce na raiz da listagem quando o usuário confirmar.
    assert config["output_path"] is None and config["runs_root_path"] == root
    # A zona do modelo não tem equipamento: a confirmação avisa.
    assert any("SALA1" in warning for warning in proposal["avisos"])
    # Propor não cria pasta nenhuma.
    assert {name for name in os.listdir(root)
            if os.path.isdir(os.path.join(root, name))} == {"COM_JANELA", "FECHADA"}


def test_propor_simulacao_recusa_configuracao_invalida(root, tmp_path):
    tools = Toolbox(root, base_config(tmp_path))
    for changes in ([{"campo": "rooms", "valor": '["NAO_EXISTE"]'}],
                    [{"campo": "epw_path", "valor": str(tmp_path / "falta.epw")}],
                    [{"campo": "output_path", "valor": "/tmp/x"}],
                    [{"campo": "clo_min", "valor": "2"}],
                    [{"campo": "module_type", "valor": "TURBO"}]):
        assert "erro" in tools.call("propor_simulacao", {"alteracoes": changes}), changes
    assert tools.proposals == []
    # Sem configuração da tela e sem execução base não há o que propor.
    assert "erro" in Toolbox(root).call("propor_simulacao", {})
    # Execução antiga sem config completo: erro legível, não exceção.
    assert "erro" in Toolbox(root).call("propor_simulacao", {"base_execucao": "COM_JANELA"})


def test_propor_simulacao_nao_troca_o_idf(root, tmp_path):
    outro = tmp_path / "outro"
    outro.mkdir()
    other_idf, _, _ = modelo_valido(outro)
    tools = Toolbox(root, base_config(tmp_path))
    result = tools.call("propor_simulacao", {"alteracoes": [
        {"campo": "idf_path", "valor": other_idf}]})
    assert "erro" in result and tools.proposals == []


def test_propor_simulacao_recusa_numero_fora_da_faixa(root, tmp_path):
    tools = Toolbox(root, base_config(tmp_path))
    for field, value in (("met", "1e999"), ("met", "NaN"), ("co2_limit", "-Infinity"),
                         ("pmv_lowerbound", "-5"), ("pmv_upperbound", "3.5"),
                         ("clo_min", "-0.1"), ("clo_max", "2.5"),
                         ("temp_ac_min", "5"), ("temp_ac_max", "40"),
                         ("adaptative_bound", "1"), ("met", "-5"), ("met", "0.5"),
                         ("met", "4.5"), ("wme", "-0.1"), ("wme", "1.2"),
                         ("pmv_comfort_bound", "0"), ("pmv_comfort_bound", "3.5"),
                         ("max_vel", "0"), ("max_vel", "2.5"),
                         ("co2_limit", "300"), ("co2_limit", "6000")):
        result = tools.call("propor_simulacao", {"alteracoes": [
            {"campo": field, "valor": value}]})
        assert "erro" in result, (field, value)
    assert tools.proposals == []


def test_propor_simulacao_margem_da_janela(root, tmp_path):
    config = base_config(tmp_path)
    tools = Toolbox(root, config)
    for value in ("0", "-100", "15.0001", "50"):
        result = tools.call("propor_simulacao", {"alteracoes": [
            {"campo": "temp_open_window_bound", "valor": value}]})
        assert "erro" in result and "temp_open_window_bound" in result["erro"], value
    assert tools.proposals == []

    for value in ("5", "15"):
        result = Toolbox(root, config).call("propor_simulacao", {"alteracoes": [
            {"campo": "temp_open_window_bound", "valor": value}]})
        assert "erro" not in result, (value, result)
    assert "erro" not in Toolbox(root, config).call(
        "propor_simulacao", {}), "padrão deve ser aceito"


def test_default_margem_da_janela_documentado():
    from pathlib import Path
    from confortimetro.config import SimulationConfig

    cli = (Path(__file__).resolve().parents[1] / "docs" / "CLI.md").read_text(encoding="utf-8")
    assert f"| `temp_open_window_bound` | {SimulationConfig.temp_open_window_bound} |" in cli


def test_propor_simulacao_recusa_idf_sem_zona(root, tmp_path):
    config = base_config(tmp_path)
    with open(config.idf_path, "w") as output:
        output.write("RunPeriod,\n  Curto,\n  1,\n  5,\n  2015,\n  1,\n  7,\n  2015,\n"
                     "  Monday;\n\nTimestep,\n  6;\n")
    tools = Toolbox(root, config)
    result = tools.call("propor_simulacao", {})
    assert "erro" in result and "SALA1" in result["erro"]


def test_propor_simulacao_a_partir_de_execucao(root, tmp_path):
    import json
    config = base_config(tmp_path)
    run = os.path.join(root, "BASE")
    os.makedirs(run)
    data = simulacao.config_to_dict(config)
    data["source_idf_path"], data["_idf_path"] = data["_idf_path"], os.path.join(run, "x.idf")
    with open(os.path.join(run, "configs.json"), "w") as output:
        json.dump(data, output)

    tools = Toolbox(root)
    result = tools.call("propor_simulacao", {"base_execucao": "BASE", "alteracoes": [
        {"campo": "co2_limit", "valor": "800"}]})
    assert "erro" not in result, result
    # Como o Duplicar: o IDF escolhido pelo usuário, não a cópia da execução.
    assert tools.proposals[0]["config"]["_idf_path"] == config.idf_path
    assert tools.proposals[0]["base"] == "execução BASE"


def test_pedido_vira_proposta_pendente_na_conversa(root, tmp_path):
    client = FakeClient([
        [_chunk(_call("propor_simulacao", alteracoes=[
            {"campo": "module_type", "valor": "WITHOUT_FAN"}]))],
        [_chunk(types.Part(text="Confirme na tela."))],
        [_chunk(types.Part(text="ok"))],
    ])
    conversation = store.new_conversation()
    assistant = Assistant(conversation, root, client=client, base_config=base_config(tmp_path))
    assistant.ask("Roda uma simulação sem ventilador")

    proposal = store.pending_proposal(conversation)
    assert proposal is not None and proposal["posicao"] == 2
    assert proposal["config"]["module_type"] == "WITHOUT_FAN"
    saved = store.load_conversation(conversation["id"])
    assert saved["proposals"][0]["status"] == "pendente"

    # O modelo fica sabendo da decisão do usuário na pergunta seguinte.
    store.set_proposal_status(conversation, proposal["id"], "iniciada", run="20260929_1200")
    assistant.ask("E aí?")
    _, config = client.models.requests[2]
    assert "iniciada, execução 20260929_1200" in config.system_instruction
    assert store.pending_proposal(conversation) is None


def test_uma_proposta_pendente_por_resposta(root, tmp_path):
    client = FakeClient([
        [_chunk(_call("propor_simulacao", alteracoes=[
            {"campo": "module_type", "valor": "WITHOUT_FAN"}]),
                _call("propor_simulacao", alteracoes=[
            {"campo": "module_type", "valor": "CLOSED_WINDOW"}]))],
        [_chunk(types.Part(text="Confirme na tela."))],
    ])
    conversation = store.new_conversation()
    assistant = Assistant(conversation, root, client=client, base_config=base_config(tmp_path))
    assistant.ask("Roda duas simulações")

    pending = [p for p in conversation["proposals"] if p["status"] == "pendente"]
    assert len(pending) == 1 and pending[0]["config"]["module_type"] == "WITHOUT_FAN"
    # A segunda chamada volta ao modelo como erro, não some.
    contents, _ = client.models.requests[1]
    results = [part.function_response.response["resultado"]
               for part in contents[-1].parts]
    assert "erro" not in results[0] and "erro" in results[1]


# --- Configurações e chave -----------------------------------------------

def test_configuracoes_e_chave_em_arquivo(monkeypatch, data_dir):
    monkeypatch.setattr(store, "_keyring", lambda: None)
    assert store.get_api_key() == ""
    store.set_api_key("abc")
    assert store.get_api_key() == "abc"
    monkeypatch.setenv("GEMINI_API_KEY", "do-ambiente")
    assert store.get_api_key() == "do-ambiente"
    monkeypatch.delenv("GEMINI_API_KEY")
    store.set_api_key("")
    assert store.get_api_key() == ""

    store.save_settings(dict(store.DEFAULT_SETTINGS, max_tool_calls=5))
    assert store.load_settings()["max_tool_calls"] == 5
    assert not (data_dir / "assistente.json").read_text().count("abc")


def test_listar_conversas_por_execucao():
    first = store.new_conversation(["A"])
    store.save_conversation(first)
    store.save_conversation(store.new_conversation(["B"]))
    assert [c["id"] for c in store.list_conversations("A")] == [first["id"]]
    assert len(store.list_conversations()) == 2
    store.delete_conversation(first["id"])
    assert len(store.list_conversations()) == 1
    with pytest.raises(ValueError):
        store.load_conversation("../x")


@pytest.mark.skipif(not REAL_KEY, reason="teste real só com GEMINI_API_KEY")
def test_api_real(root, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", REAL_KEY)
    answer = Assistant(store.new_conversation(["COM_JANELA"]), root).ask(
        "Qual a energia total da execução COM_JANELA?")
    assert "10" in answer


def test_confirmacao_mostra_toda_alteracao(root, tmp_path):
    tools = Toolbox(root, base_config(tmp_path))
    tools.call("propor_simulacao", {"alteracoes": [{"campo": "clo_delta", "valor": "0.05"}]})
    rows = simulacao.summary(tools.proposals[0])
    assert rows[0][0].startswith("período")
    assert rows[1] == ("clo_delta (alterado)", "0.05")
    assert ("rooms", "SALA1") in rows


def test_confirmacao_mostra_parametros_herdados_e_efetivos(root, tmp_path):
    config = base_config(tmp_path)
    config.wme = 0.3
    config.clo_delta = 0.2
    config.temp_open_window_bound = 7.0
    tools = Toolbox(root, config)
    result = tools.call("propor_simulacao", {"alteracoes": [
        {"campo": "co2_limit", "valor": "850"}]})
    assert "erro" not in result, result

    proposal = tools.proposals[0]
    rows = dict(simulacao.summary(proposal))
    for field in simulacao.EDITABLE_FIELDS:
        assert (field + (" (alterado)" if field == "co2_limit" else "")) in rows
    assert rows["wme"] == "0.3"
    assert rows["clo_delta"] == "0.2"
    assert rows["temp_open_window_bound"] == "7.0"
    assert rows["co2_limit (alterado)"] == "850.0"
    assert rows["ignore_missing_equipment"] == "True"
    assert dict(simulacao.for_model(proposal)["configuracao"]) == rows


def test_indicadores_recusa_execucao_interrompida(tmp_path):
    from confortimetro.assistant.tools import Toolbox
    run = tmp_path / 'PARADA'
    run.mkdir()
    (run / 'configs.json').write_text('{"module_type": "COMPLETE", "rooms": ["Z"]}')
    tools = Toolbox(str(tmp_path))
    assert tools.listar_execucoes()['linhas'][0]['status'] == 'interrompida'
    assert 'interrompida' in tools.call('indicadores', {'execucao': 'PARADA'})['erro']
