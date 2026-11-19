"""Configuração inválida é recusada antes de simular (GUI e CLI)."""

import pytest

import cli
from confortimetro.config import SimulationConfig, energy_api_library
from confortimetro.gui.theme import parse_num
from tests import test_gui_pages

# Fixture da janela; atribuída (não importada) para o ruff não ver redefinição.
window = test_gui_pages.window


def _config(tmp_path, **over):
    """Configuração que passa em `simulacao.validate`, mais `over`."""
    idf = tmp_path / "modelo.idf"
    idf.write_text("Zone,\n  SALA1;\n\nPeople,\n  PEOPLE_SALA1;\n")
    epw = tmp_path / "clima.epw"
    epw.write_text("LOCATION,Teste\n")
    energy = tmp_path / "EnergyPlus-9-4-0"
    (energy / "pyenergyplus").mkdir(parents=True, exist_ok=True)
    (energy / "Energy+.idd").write_text("")
    (energy / energy_api_library()).write_text("")
    (energy / "pyenergyplus" / "api.py").write_text("")
    config = SimulationConfig(met_as_watts=125.496, _idf_path=str(idf), _met=1.2,
                              epw_path=str(epw), energy_path=str(energy), rooms=["SALA1"],
                              runs_root_path=str(tmp_path / "execucoes"),
                              output_path=str(tmp_path / "execucoes" / "run"))
    for key, value in over.items():
        setattr(config, key, value)
    return config


def test_parse_num_recusa_texto_invalido():
    assert parse_num("0,15") == 0.15
    for text in ("", "1.2 m/s", "abc"):
        with pytest.raises(ValueError):
            parse_num(text)


@pytest.fixture
def no_simulation(monkeypatch):
    class Forbidden:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("a simulação não podia ter começado")
    monkeypatch.setattr(cli, "Simulation", Forbidden)


def test_cli_recusa_air_speed_delta_zero(tmp_path, capsys, no_simulation):
    path = tmp_path / "config.json"
    _config(tmp_path).to_json(str(path))

    assert cli.main(["--config", str(path), "--set", "air_speed_delta=0"]) == 1
    assert "air_speed_delta" in capsys.readouterr().err


def test_cli_recusa_air_speed_delta_vazio(tmp_path, no_simulation):
    path = tmp_path / "config.json"
    _config(tmp_path).to_json(str(path))

    with pytest.raises(SystemExit, match="air_speed_delta"):
        cli.main(["--config", str(path), "--set", "air_speed_delta="])


def test_gui_start_simulation_recusa_air_speed_delta_zero(window, tmp_path, monkeypatch):
    started = []
    monkeypatch.setattr(window, "_run_simulation_thread", lambda q, c: started.append(c))
    errors = []
    monkeypatch.setattr(window.results_panel, "append_error", errors.append)

    assert window.start_simulation(_config(tmp_path, air_speed_delta=0.0)) is None
    assert window.simulation_thread is None and started == []
    assert any("air_speed_delta" in error for error in errors)
    # A mesma configuração com o passo válido passa.
    assert window.start_simulation(_config(tmp_path, air_speed_delta=0.15))
    window.simulation_thread.join(5)
    assert len(started) == 1


def test_gui_campo_vazio_nao_vira_zero(window, monkeypatch):
    started = []
    monkeypatch.setattr(window, "start_simulation", started.append)
    errors = []
    monkeypatch.setattr(window.results_panel, "append_error", errors.append)
    window.simulation_panel.air_speed_delta_entry.delete(0, "end")

    with pytest.raises(ValueError, match="Variação da vel. de ventilação"):
        window.simulation_panel.get_configuration()
    window.on_run_simulation()
    assert started == [] and window.simulation_thread is None
    assert errors and "Variação da vel. de ventilação" in errors[0]


def _set_range(field, low, high):
    for entry, text in ((field.min_entry, low), (field.max_entry, high)):
        entry.delete(0, "end")
        entry.insert(0, text)


@pytest.mark.parametrize("low, high, texto", [
    ("", "0,5", "Faixa de PMV"),            # vazio
    ("abc", "0,5", "Faixa de PMV"),         # texto
    ("-0,5", "5", "fora da faixa"),         # fora da escala -3..3
    ("1", "0", "maior"),                    # mínimo acima do máximo
])
def test_gui_pmv_invalido_nao_e_corrigido(window, low, high, texto):
    panel = window.simulation_panel
    _set_range(panel.pmv_range, low, high)
    panel.pmv_range._commit()               # o foco saindo não pode "consertar"
    assert (panel.pmv_range.min_entry.get(), panel.pmv_range.max_entry.get()) == (low, high)
    with pytest.raises(ValueError, match=texto):
        panel.get_configuration()


def test_gui_clo_e_ac_invalidos_bloqueiam(window):
    panel = window.simulation_panel
    _set_range(panel.clo_range, "1,5", "0,5")
    with pytest.raises(ValueError, match="Faixa de Clo"):
        panel.get_configuration()
    _set_range(panel.clo_range, "0,5", "1")
    _set_range(panel.temp_ac_range, "5", "30")
    with pytest.raises(ValueError, match="Temperatura do AC"):
        panel.get_configuration()


def test_gui_idf_que_nao_e_idf_bloqueia(window, tmp_path):
    texto = tmp_path / "notas.txt"
    texto.write_text("nada de IDF aqui\n")
    window.path_panel.set_idf_path(str(texto))
    with pytest.raises(ValueError, match="Arquivo IDF"):
        window.simulation_panel.get_configuration()
    epw = tmp_path / "clima.epw"
    epw.write_text("LOCATION,x\n")
    window.path_panel.set_idf_path(str(epw))
    with pytest.raises(ValueError, match=r"\.idf"):
        window.simulation_panel.get_configuration()


def test_gui_periodo_editado_vai_para_a_configuracao(window, tmp_path):
    idf = tmp_path / "anual.idf"
    idf.write_text("Zone,\n  SALA1;\n"
                   "RunPeriod,\n  ANO, 1, 1, 2015, 12, 31, 2015;\n")
    window.idf_editor_panel.load(str(idf))
    window.idf_editor_panel.end_entry.delete(0, "end")
    window.idf_editor_panel.end_entry.insert(0, "31/01/2015")
    config = window.simulation_panel.get_configuration()
    assert config["run_period_start"] == "2015-01-01"
    assert config["run_period_end"] == "2015-01-31"
    window.idf_editor_panel.end_entry.delete(0, "end")
    window.idf_editor_panel.end_entry.insert(0, "32/01/2015")
    with pytest.raises(ValueError, match="Período"):
        window.simulation_panel.get_configuration()


def test_gui_junta_erros_e_recusa_negativo(window):
    panel = window.simulation_panel
    panel.vel_max_entry.delete(0, "end")
    panel.vel_max_entry.insert(0, "-2")
    panel.met_entry.delete(0, "end")
    with pytest.raises(ValueError) as error:
        panel.get_configuration()
    message = str(error.value)
    assert "a partir de zero" in message and "preencha o valor" in message
    # Vazio também ganha a borda vermelha (estado invalid do sv_ttk).
    assert panel.met_entry.instate(["invalid"])
    assert panel.vel_max_entry.instate(["invalid"])


def test_gui_data_inexistente_diz_por_que(window, tmp_path):
    idf = tmp_path / "anual.idf"
    idf.write_text("Zone,\n  SALA1;\n"
                   "RunPeriod,\n  ANO, 1, 1, 2015, 12, 31, 2015;\n")
    window.idf_editor_panel.load(str(idf))
    window.idf_editor_panel.end_entry.delete(0, "end")
    window.idf_editor_panel.end_entry.insert(0, "31/02/2015")
    with pytest.raises(ValueError, match="não existe no calendário"):
        window.simulation_panel.get_configuration()


def test_gui_passos_por_hora_precisa_dividir_60(window, tmp_path):
    idf = tmp_path / "anual.idf"
    idf.write_text("Zone,\n  SALA1;\n"
                   "RunPeriod,\n  ANO, 1, 1, 2015, 12, 31, 2015;\n")
    window.idf_editor_panel.load(str(idf))
    entry = window.idf_editor_panel.timestep_entry
    entry.delete(0, "end")
    entry.insert(0, "7")
    with pytest.raises(ValueError, match="dividir 60"):
        window.simulation_panel.get_configuration()
