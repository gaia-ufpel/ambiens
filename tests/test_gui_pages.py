"""Navegação por páginas da janela principal e duplicação de execução."""

import datetime
import os

import pytest

tk = pytest.importorskip("tkinter")

from confortimetro.config import SimulationConfig  # noqa: E402


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("AMBIENS_DATA_DIR", str(tmp_path / "dados"))
    from confortimetro.gui.main_window import MainWindow

    # Configuração já existente: sem ela a janela cai no `SimulationConfig()`
    # sem argumentos e quebra antes de montar as páginas.
    idf_base = tmp_path / "base.idf"
    idf_base.write_text("Zone,\n  SALA1,\n")
    config_path = tmp_path / "config.json"
    SimulationConfig(met_as_watts=125.496, _idf_path=str(idf_base), _met=1.2,
                     epw_path=str(tmp_path / "clima.epw"), rooms=["SALA1"],
                     energy_path=str(tmp_path / "EnergyPlus"),
                     output_path=str(tmp_path / "saidas" / "run_000")).to_json(
                         str(config_path))

    try:
        window = MainWindow(config_path=str(config_path))
    except tk.TclError:
        pytest.skip("sem display para o Tk")
    yield window
    window.destroy()


def _settle(window):
    """Deixa o Tk aplicar a troca antes de olhar o que está no ar."""
    window.update()


def test_paginas_trocam(window):
    assert window._current_page == "runs"
    window.show_page("editor")
    _settle(window)
    assert window._pages["editor"].winfo_ismapped()
    assert not window._pages["runs"].winfo_ismapped()


def test_editor_log_sheet_visivel_ao_abrir(window):
    """Garante que o log de execução não seja colapsado a 1x1 em telas 1366x768."""
    window.geometry("1366x768")
    window.show_page("editor")
    _settle(window)
    assert window.log_sheet.winfo_ismapped()
    assert not window.log_sheet._holder.winfo_ismapped()

    window.log_sheet.set_open(True)
    _settle(window)
    assert window.log_sheet._holder.winfo_ismapped()
    assert window.results_panel.winfo_height() >= 100
    assert window.results_panel.results_text.winfo_height() >= 50


def test_editar_idf_abre_periodo_na_tela_de_execucao(window):
    """O atalho abre Período, sem aba intermediária nem troca de página."""
    window.show_page("editor")
    window.on_edit_idf()
    _settle(window)

    assert window._current_page == "editor"
    assert window.simulation_panel.notebook.tab("current", "text").strip() == "Período"
    assert window.idf_editor_panel.period_tab.winfo_ismapped()


def test_ocupacao_carrega_idf_ao_abrir_aba(window, tmp_path):
    """A aba pode ser aberta diretamente, sem passar pelo atalho Editar IDF."""
    idf = tmp_path / "ocupacao.idf"
    idf.write_text("""People,
  PESSOAS_SALA1,
  SALA1,
  HORARIO_SALA1,
  People,
  12;
""")
    window.show_page("editor")
    window.path_panel.set_idf_path(str(idf))
    window.simulation_panel.notebook.select(window.idf_editor_panel.occupation_tab)
    _settle(window)

    assert window.idf_editor_panel._people[0]["name"] == "PESSOAS_SALA1"
    assert window.idf_editor_panel._people_widgets


def test_idf_configurado_preenche_periodo_ao_abrir_editor(window, tmp_path):
    idf = tmp_path / "verao.idf"
    idf.write_text(
        "Timestep,\n    4;\n\n"
        "RunPeriod,\n    Verão,\n    12,\n    1,\n    2019,\n"
        "    2,\n    28,\n    2020;\n",
        encoding="latin-1")

    window.on_idf_path_changed(str(idf))

    assert window.idf_editor_panel.start_entry.get() == "01/12/2019"
    assert window.idf_editor_panel.end_entry.get() == "28/02/2020"
    assert window.idf_editor_panel.timestep_entry.get() == "4"


def test_duplicar_reaproveita_parametros_com_saida_nova(window, tmp_path):
    idf = tmp_path / "modelo.idf"
    idf.write_text("Zone,\n  SALA1,\n")
    run_path = tmp_path / "execucao_antiga"
    run_path.mkdir()
    config = SimulationConfig(met_as_watts=125.496, _idf_path=str(run_path / "modelo.idf"),
                             _met=1.2, epw_path="clima.epw", output_path=str(run_path),
                             source_idf_path=str(idf), pmv_upperbound=0.9,
                             nome="Base")
    config.to_json(str(run_path / "configs.json"))

    window.on_duplicate_run({'run': 'execucao_antiga', 'path': str(run_path),
                          'status': 'pronta', 'rooms_disponiveis': [],
                          'modificado': datetime.datetime.now(), 'config': {}})

    assert window._current_page == "editor"
    assert window.configs.pmv_upperbound == 0.9
    # O modelo escolhido pelo usuário, não a cópia dentro da execução…
    assert window.configs.idf_path == str(idf)
    # …e nenhuma pasta de saída herdada: ela nasce da raiz quando a simulação
    # começa, então nunca escreve por cima da execução duplicada.
    assert window.configs.output_path is None
    # Nome repetido deixaria a listagem e a comparação ambíguas.
    assert window.control_panel.get_name() == "Base (cópia)"

    window.on_new_run()
    assert window.control_panel.get_name() is None


def test_comparacao_mostra_nome_e_nao_a_pasta():
    from confortimetro.gui.components.comparison_panel import run_labels

    runs = [{'run': '20261007_2133', 'idf': 'FAURB.idf', 'config': {}},
            {'run': '20261007_2200', 'config': {'nome': 'Base'}},
            {'run': '20261007_2201', 'config': {'nome': 'Base'}}]
    assert run_labels(runs) == {'20261007_2133': 'FAURB · 07/10 21:33',
                                '20261007_2200': 'Base',
                                '20261007_2201': 'Base (2)'}


def test_tab_no_intervalo_seleciona_o_maximo(window):
    # O FocusOut do mínimo reescrevia o máximo e apagava a seleção do Tab.
    from confortimetro.gui.components.simulation_config_panel import StrictRangeField

    field = StrictRangeField(window, "PMV", -3, 3)
    field.place(x=0, y=0)
    field.set(-0.5, 0.5)
    _settle(window)
    field.min_entry.focus_force()
    _settle(window)
    field.min_entry.event_generate("<Tab>")
    _settle(window)
    assert window.focus_get() is field.max_entry
    assert field.max_entry.selection_present()


def _toast_text(frame):
    """Texto do toast (o rótulo é o segundo filho, depois da barra de cor)."""
    return " ".join(child.cget("text") for child in frame.winfo_children()
                    if "text" in child.keys())


def test_comparar_abre_pagina_propria(window, tmp_path):
    runs = []
    for name in ("run_a", "run_b"):
        run_path = tmp_path / name
        run_path.mkdir()
        (run_path / "configs.json").write_text("{}")
        runs.append({'run': name, 'path': str(run_path), 'status': 'sem estatísticas',
                     'rooms_disponiveis': [], 'config': {},
                     'modificado': datetime.datetime.now()})

    window.on_compare_runs(runs, str(tmp_path))
    _settle(window)

    assert window._current_page == "compare"
    # Sem estatísticas não há o que comparar, mas o usuário precisa saber disso
    # na própria página, não numa tela em branco.
    assert any("estatísticas" in _toast_text(frame)
               for frame in getattr(window, "_toasts", []))


def test_simulation_error_handling(window):
    from queue import Queue
    window.simulation_queue = Queue()

    window._handle_simulation_message("Erro no pós-processamento: falha na extração")
    assert window._simulation_error == "Erro no pós-processamento: falha na extração"
    assert "falha na extração" in window.results_panel.results_text.get("1.0", "end")
    assert window.control_panel.status_pill._text == "Erro no pós-processamento: falha na extração"
    assert window.control_panel.status_pill._state == "error"

    window.simulation_thread = None
    window._check_simulation_thread()
    assert window.control_panel.status_pill._text == "Simulação falhou"
    assert window.control_panel.status_pill._state == "error"
    assert "Simulação finalizada com erros" in window.results_panel.results_text.get("1.0", "end")


def test_simulation_success_handling(window):
    from queue import Queue
    window.simulation_queue = Queue()
    window._simulation_error = None

    window._handle_simulation_message("Executando etapa final")
    # O resumo do eplusout.err sai em toda execução: é aviso, não falha.
    window._handle_simulation_message(
        "WARNING EnergyPlus: 56 avisos e 0 erros graves (veja eplusout.err na pasta da execução).")
    assert window._simulation_error is None

    window.simulation_thread = None
    window._check_simulation_thread()
    assert window.control_panel.status_pill._text == "Simulação concluída"
    assert window.control_panel.status_pill._state == "success"
    assert "Simulação concluída!" in window.results_panel.results_text.get("1.0", "end")


def test_simulation_interrupted_handling(window):
    from queue import Queue
    from unittest.mock import MagicMock
    window.simulation_queue = Queue()
    window._simulation_error = None
    window.simulation = MagicMock()
    window.simulation.stop_requested = True

    window.simulation_thread = None
    window._check_simulation_thread()
    assert window.control_panel.status_pill._text == "Simulação interrompida"
    assert window.control_panel.status_pill._state == "warning"
    assert "Simulação interrompida." in window.results_panel.results_text.get("1.0", "end")


def test_recompute_selected_validacoes_e_mensagens(window, monkeypatch):
    panel = window.simulations_panel

    # Caso 1: Nenhuma execução selecionada
    monkeypatch.setattr(panel, "_selected_runs", lambda: [])
    panel.recompute_selected()
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("Escolha uma execução na lista" in t for t in toasts)

    # Limpa toasts
    for f in list(getattr(window, "_toasts", [])):
        f.destroy()
    window._toasts = []

    # Caso 2: Execução pronta
    monkeypatch.setattr(panel, "_selected_runs", lambda: [{'path': '/dummy', 'status': 'pronta'}])
    panel.recompute_selected()
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("As execuções escolhidas já têm estatísticas atualizadas." in t for t in toasts)

    # Limpa toasts
    for f in list(getattr(window, "_toasts", [])):
        f.destroy()
    window._toasts = []

    # Caso 3: Execução sem planilhas é aceita e dispara recompute_runs
    disparado = []
    monkeypatch.setattr("confortimetro.gui.components.simulations_panel.recompute_runs",
                        lambda paths, **kwargs: disparado.extend(paths) or {paths[0]: None})
    monkeypatch.setattr(panel, "_selected_runs", lambda: [{'path': '/run/sem_plan', 'status': 'sem planilhas'}])

    class MockThread:
        def __init__(self, target, daemon=None):
            self.target = target
        def start(self):
            self.target()

    monkeypatch.setattr("threading.Thread", MockThread)
    panel.recompute_selected()
    window.update()
    assert disparado == ['/run/sem_plan']


def test_recompute_done_toasts(window):
    panel = window.simulations_panel

    # 1 sucesso
    panel._recompute_done({"/p/run1": None})
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("1 execução regerada." in t for t in toasts)
    window._toasts.clear()

    # 2 sucessos
    panel._recompute_done({"/p/run1": None, "/p/run2": None})
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("2 execuções regeradas." in t for t in toasts)
    window._toasts.clear()

    # 1 falha
    panel._recompute_done({"/p/run1": "sem planilhas por zona nem arquivo eplusout.eso"})
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("1 execução falhou ao regerar" in t for t in toasts)
    window._toasts.clear()

    # 2 falhas
    panel._recompute_done({"/p/run1": "erro 1", "/p/run2": "erro 2"})
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("2 execuções falharam ao regerar" in t for t in toasts)
    window._toasts.clear()

    # Misto (1 sucesso, 1 falha)
    panel._recompute_done({"/p/run1": None, "/p/run2": "erro 2"})
    toasts = [_toast_text(f) for f in getattr(window, "_toasts", [])]
    assert any("1 execução regerada, mas 1 falhou" in t for t in toasts)
    window._toasts.clear()


def test_detalhes_abrem_no_resumo_e_serie_so_carrega_na_aba(window, tmp_path, monkeypatch):
    from confortimetro.gui.components import timeseries_panel

    carregadas = []
    monkeypatch.setattr(timeseries_panel.TimeSeriesPanel, "load",
                        lambda self, room: carregadas.append(room))
    run_path = tmp_path / "run_a"
    run_path.mkdir()
    run = {'run': 'run_a', 'path': str(run_path), 'status': 'pronta',
           'rooms_disponiveis': ['SALA1'], 'config': {},
           'modificado': datetime.datetime.now()}

    window.on_open_run_details(run)
    _settle(window)

    # Abrir os detalhes não lê a planilha: o Resumo continua instantâneo.
    assert window.detail_tabs.index("current") == 0
    assert carregadas == []

    window.detail_tabs.select(1)
    _settle(window)
    assert carregadas == ['SALA1']


def test_detalhes_tabela_formata_porcentagens(window, tmp_path):
    import pandas

    run_path = tmp_path / "run_pct"
    run_path.mkdir()
    df = pandas.DataFrame({
        'Nome da sala': ['SALA1'],
        'Energia total (kWh)': [123.456],
        'Desconforto (%)': [0.152],
        'PMV médio': [-0.25],
        'PMV fora da faixa (%)': [0.084],
        'Fora da banda adaptativa (%)': [0.12],
        'Janela aberta (%)': [0.35],
        'Ventilador ligado (%)': [0.20],
        'DOAS ligado (%)': [0.0],
        'CO2 máximo (ppm)': [800.0],
        'Timesteps simulados': [8760],
    })
    df.to_excel(run_path / "ESTATISTICAS.xlsx", index=False)
    run = {'run': 'run_pct', 'path': str(run_path), 'status': 'pronta',
           'rooms_disponiveis': ['SALA1'], 'config': {},
           'modificado': datetime.datetime.now()}

    window.on_open_run_details(run)
    _settle(window)

    items = window.detail_stats.get_children()
    assert len(items) >= 1
    # Primeira linha são os dados da sala
    values = window.detail_stats.item(items[0])['values']
    cols = window.detail_stats['columns']
    row_dict = dict(zip(cols, values))

    assert row_dict['Desconforto (%)'] == '15,2%'
    assert row_dict['PMV fora da faixa (%)'] == '8,4%'
    assert row_dict['Fora da banda adaptativa (%)'] == '12,0%'
    assert row_dict['Janela aberta (%)'] == '35,0%'
    assert row_dict['Ventilador ligado (%)'] == '20,0%'
    assert row_dict['DOAS ligado (%)'] == '0,0%'
    assert row_dict['PMV médio'] == '-0,250'
    assert row_dict['Energia total (kWh)'] == '123,456'
    assert window.detail_export_button._state == "normal"


def test_exportar_resumo_detalhes(window, tmp_path, monkeypatch):
    import pandas
    from tkinter import filedialog

    run_path = tmp_path / "saidas" / "run_resumo"
    run_path.mkdir(parents=True)
    df = pandas.DataFrame({
        'Nome da sala': ['SALA1'],
        'Energia total (kWh)': [100.0],
    })
    df.to_excel(run_path / "ESTATISTICAS.xlsx", index=False)
    run = {'run': 'run_resumo', 'path': str(run_path), 'status': 'pronta',
           'rooms_disponiveis': ['SALA1'], 'config': {},
           'modificado': datetime.datetime.now()}

    window.on_open_run_details(run)
    _settle(window)

    dest_file = tmp_path / "meu_resumo.xlsx"
    monkeypatch.setattr(filedialog, "asksaveasfilename", lambda **kw: str(dest_file))

    window._export_detail_summary()
    assert dest_file.exists()
    df_read = pandas.read_excel(dest_file)
    assert df_read['Energia total (kWh)'].iloc[0] == 100.0


def _wait_answer(panel, window):
    import time
    deadline = time.time() + 10
    while panel._busy() or panel._pending:
        window.update()
        assert time.time() < deadline, "o assistente não terminou"
        time.sleep(0.02)


def test_assistente_nos_detalhes_e_na_pagina_geral(window, tmp_path, monkeypatch):
    from confortimetro.assistant import store

    monkeypatch.setattr(store, "_keyring", lambda: None)
    store.set_api_key("chave-de-teste")

    class FakeAssistant:
        def __init__(self, conversation, root, *args, **kwargs):
            self.conversation = conversation

        def ask(self, question, on_event=None, cancel=None):
            on_event("status", "Consultando indicadores…")
            on_event("text", "| a | b |\n|---|---|\n| 1 | 2 |")
            self.conversation["contents"] += [
                {"role": "user", "parts": [{"text": question}]},
                {"role": "model", "parts": [{"text": "resposta"}]}]
            self.conversation["title"] = question
            store.save_conversation(self.conversation)
            return "resposta"

    monkeypatch.setattr("confortimetro.assistant.client.Assistant", FakeAssistant)

    run_path = tmp_path / "saidas" / "run_a"
    run_path.mkdir(parents=True)
    (run_path / "configs.json").write_text("{}")
    run = {'run': 'run_a', 'path': str(run_path), 'status': 'sem estatísticas',
           'rooms_disponiveis': [], 'config': {}, 'modificado': datetime.datetime.now()}

    window.on_open_run_details(run)
    _settle(window)
    panel = window.detail_assistant
    window.detail_tabs.select(2)
    panel.input.insert("1.0", "Quanto consumiu?")
    panel.send()
    _wait_answer(panel, window)
    assert [c["title"] for c in store.list_conversations("run_a")] == ["Quanto consumiu?"]

    # Voltar aos detalhes reabre a conversa da execução.
    window.on_open_run_details(run)
    assert panel.conversation["title"] == "Quanto consumiu?"

    # Página geral: conversa nova com as execuções escolhidas.
    window.on_ask_assistant([run], str(tmp_path / "saidas"))
    _settle(window)
    assert window._current_page == "assistant"
    assert window.assistant_panel.conversation["runs"] == ["run_a"]
    assert window.assistant_panel.conversation["contents"] == []


def test_fluxo_simulacao_em_andamento_botoes_e_selecao(window, tmp_path):
    """Testa se a execução em andamento pode ser selecionada, se os botões são
    renomeados adequadamente e se clicar nela navega de volta ao editor com o log aberto."""
    panel = window.simulations_panel
    run_path = str(tmp_path / "saidas" / "run_em_andamento")

    # Inicia simulação registrada
    panel.set_running(run_path, window.configs)
    _settle(window)

    # 1. Botão do painel de simulações deve estar renomeado
    assert panel.btn_new_run._text == "Ver em andamento"
    assert panel.btn_new_run._icon == "running"

    # 2. Execução em andamento aparece na treeview e pode ser selecionada
    assert run_path in panel._running
    panel.tree.selection_set(run_path)
    panel._on_select()
    _settle(window)

    selected = panel._selected_runs()
    assert len(selected) == 1
    assert selected[0]['path'] == run_path
    assert selected[0]['status'] == 'em simulação'

    # Indicadores e botões contextuais atualizados
    assert panel.kpi_energy_val.cget("text") == "em andamento"
    assert panel.btn_duplicate._state == "disabled"
    assert panel.btn_recompute._state == "disabled"
    assert panel.btn_details._icon == "running"

    # 3. Abrir detalhes da simulação em andamento navega para a página editor com log aberto
    window.show_page("runs")
    _settle(window)
    assert window._current_page == "runs"

    panel._open_details()
    _settle(window)
    assert window._current_page == "editor"
    assert window.log_sheet.is_open

    # 4. Botão do nav da página de execuções reflete simulação ativa
    window.on_run_simulation = lambda: None  # mock para teste isolado
    window._running_run_path = run_path
    window.btn_nav_new_run.configure(text="Ver em andamento", icon="running")
    assert window.btn_nav_new_run._text == "Ver em andamento"

    # 5. Finalizar simulação restaura botões para "Nova execução"
    panel.clear_running(run_path)
    window.btn_nav_new_run.configure(text="Nova execução", icon="new")
    _settle(window)
    assert panel.btn_new_run._text == "Nova execução"
    assert panel.btn_new_run._icon == "new"
    assert window.btn_nav_new_run._text == "Nova execução"
    assert window.btn_nav_new_run._icon == "new"



def test_assistente_propoe_simulacao_e_so_roda_com_confirmacao(window, tmp_path, monkeypatch):
    """Pedido → proposta validada → confirmação → execução no caminho do botão
    Executar, listada como em simulação. Sem confirmação nada roda."""
    import threading
    import time

    pytest.importorskip("google.genai")
    from google.genai import types

    from confortimetro.assistant import client as client_module, store
    from tests.test_assistant import FakeClient, _call, _chunk, modelo_valido

    monkeypatch.setattr(store, "_keyring", lambda: None)
    store.set_api_key("chave-de-teste")
    idf, epw, energy = modelo_valido(tmp_path)
    runs_root = tmp_path / "execucoes"
    runs_root.mkdir()
    window.path_panel.set_idf_path(idf)
    window.path_panel.set_epw_path(epw)
    window.settings_panel.set_energy_path(energy)
    window.settings_panel.set_output_path(str(runs_root))

    propose = [_chunk(_call("propor_simulacao", motivo="janela fechada", alteracoes=[
        {"campo": "module_type", "valor": "CLOSED_WINDOW"}]))]
    fake = FakeClient([propose, [_chunk(types.Part(text="Confirme na tela."))],
                       propose, [_chunk(types.Part(text="Confirme na tela."))]])
    monkeypatch.setattr(client_module, "make_client", lambda key, timeout: fake)

    # A simulação de verdade levaria horas: a thread só espera ser liberada.
    release, started = threading.Event(), []

    def fake_run(q, config):
        started.append(config)
        release.wait(10)
        q.put("Simulação concluída")

    monkeypatch.setattr(window, "_run_simulation_thread", fake_run)

    window.on_ask_assistant([], str(runs_root))
    panel = window.assistant_panel

    def ask(question):
        panel.input.insert("1.0", question)
        panel.send()
        _wait_answer(panel, window)
        _settle(window)

    # 1. Pedido vira proposta pendente, com a configuração validada na tela.
    ask("Roda o modelo com a janela sempre fechada")
    proposal = store.pending_proposal(panel.conversation)
    assert proposal["config"]["module_type"] == "CLOSED_WINDOW"
    assert panel.proposal_card.winfo_ismapped()
    rows = dict(panel.proposal_table.item(item, "values")
                for item in panel.proposal_table.get_children())
    assert rows["module_type (alterado)"] == "CLOSED_WINDOW"
    assert rows["rooms"] == "SALA1"
    assert window.simulation_thread is None and not window.simulations_panel._running

    # 2. Descartar: nada roda e o cartão some.
    panel.discard_proposal()
    _settle(window)
    assert panel.conversation["proposals"][0]["status"] == "descartada"
    assert not panel.proposal_card.winfo_ismapped()
    assert window.simulation_thread is None and started == []

    # 3. Nova proposta, agora confirmada: inicia pelo caminho do Executar.
    ask("Pode propor de novo")
    assert started == [] and window.simulation_thread is None
    panel.confirm_proposal()
    _settle(window)
    for _ in range(100):
        if started:
            break
        time.sleep(0.01)
    assert started and str(started[0].module_type) == "CLOSED_WINDOW"
    run_path = started[0].output_path
    assert os.path.dirname(run_path) == str(runs_root)
    assert window.simulation_thread.is_alive()
    assert window.simulations_panel._running[run_path]["status"] == "em simulação"
    assert window.simulations_panel.tree.exists(run_path)
    confirmed = panel.conversation["proposals"][1]
    assert confirmed["status"] == "iniciada"
    assert confirmed["execucao"] == os.path.basename(run_path)
    assert not panel.proposal_card.winfo_ismapped()

    # Uma de cada vez: outra confirmação com a simulação rodando é recusada.
    path, error = window.start_assistant_simulation(started[0])
    assert path is None and "em andamento" in error

    # 4. Terminou: sai de "em simulação" e o usuário continua na conversa.
    release.set()
    window.simulation_thread.join(5)
    deadline = time.time() + 5
    while window.simulations_panel._running and time.time() < deadline:
        window.update()
        time.sleep(0.02)
    assert not window.simulations_panel._running
    assert window._current_page == "assistant"


def test_drive_sem_cliente_fica_desabilitado(window, tmp_path, monkeypatch):
    from confortimetro.drive import auth

    monkeypatch.delenv(auth.CLIENT_VARIABLE, raising=False)
    monkeypatch.setattr(auth, "CLIENT_FILE", str(tmp_path / "nao_existe.json"))
    window.drive_settings.refresh()
    window.drive_sync()  # sem cliente não faz nada

    assert window.drive_settings.connect_button._state == "disabled"
    assert "docs/DRIVE.md" in window.drive_settings.status_var.get()
    window.on_share_run({'run': 'x', 'path': str(tmp_path)})  # só avisa


def test_compartilhar_copia_link(window, tmp_path, monkeypatch):
    import json
    import time

    from test_drive import FakeDrive, _run

    from confortimetro.drive import auth, sync
    from confortimetro.gui.components import open_share_dialog

    client = tmp_path / "client_secret.json"
    client.write_text(json.dumps({"installed": {"client_id": "x"}}))
    monkeypatch.setenv(auth.CLIENT_VARIABLE, str(client))
    fake = FakeDrive()
    monkeypatch.setattr(auth, "service", lambda: fake)
    sync.save_state({"account": "dono@exemplo.com", "runs": {}, "pending": []})
    run = _run(tmp_path / "saidas", "run_a")

    dialog = open_share_dialog(window, str(run))
    assert dialog.unshare_button._state == "disabled"
    dialog.copy_button._command()
    deadline = time.time() + 30
    while "Link copiado" not in dialog.status.get() and time.time() < deadline:
        window.update()
        time.sleep(0.05)

    assert "Link copiado" in dialog.status.get(), dialog.status.get()
    folder = fake.folder_id("run_a")
    assert window.clipboard_get() == f"https://drive.google.com/drive/folders/{folder}"
    assert dialog.unshare_button._state == "normal"
    dialog.destroy()


def test_apresentacao_percorre_os_passos_e_abre_o_editor(window):
    tour = window.open_onboarding()
    for _ in range(4):
        tour.show(tour.step + 1)
        _settle(window)
    assert tour.step == 4
    # Último passo: o botão principal fecha e leva ao editor.
    tour.forward._command()
    _settle(window)
    assert not tour.winfo_exists()
    assert window._current_page == "editor"


def test_esc_volta_e_parar_pede_confirmacao(window, monkeypatch):
    window.show_page("editor")
    window.focus_force()
    window.update()
    window.event_generate("<Escape>")
    window.update()
    assert window._current_page == "runs"

    class Viva:
        def is_alive(self):
            return True

    parou = []
    window.simulation_thread = Viva()
    window.simulation = type("S", (), {"stop": lambda self: parou.append(1)})()
    window._run_started = 0
    from confortimetro.gui import main_window
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: False)
    window.on_stop_simulation()
    assert not parou
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: True)
    window.on_stop_simulation()
    assert parou


def test_fechar_com_simulacao_confirma_e_para(window, monkeypatch):
    class Viva:
        def is_alive(self):
            return True

        def join(self, timeout=None):
            pass

    parou = []
    window.simulation_thread = Viva()
    window.simulation = type("S", (), {"stop": lambda self: parou.append(1)})()
    from confortimetro.gui import main_window
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: False)
    window._on_close()
    assert not parou and window.winfo_exists()
    monkeypatch.setattr(main_window.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(window, "destroy", lambda: parou.append("destroy"))
    window._on_close()
    assert parou == [1, "destroy"]
    window.simulation_thread = None


def test_idf_novo_vai_para_dados_do_app(window):
    alvo = window._new_idf_name("/x/examples/modelo.idf")
    assert alvo.startswith(os.path.join(str(window.config_path and os.environ["AMBIENS_DATA_DIR"])))
    assert alvo.endswith("modelo_editado.idf")


def test_lista_seleciona_varias_pelo_teclado(window):
    tree = window.simulations_panel.tree
    for index in range(3):
        tree.insert("", "end", iid=f"r{index}", values=(f"r{index}",))
    tree.focus("r0")
    tree.selection_set("r0")
    window.simulations_panel._extend_selection(1)
    assert set(tree.selection()) == {"r0", "r1"}
    tree.focus("r2")
    window.simulations_panel._toggle_focused()
    assert set(tree.selection()) == {"r0", "r1", "r2"}
    window.simulations_panel._toggle_focused()
    assert "r2" not in tree.selection()
    window.simulations_panel._select_all()
    assert len(tree.selection()) == 3


def test_botao_desabilitado_sai_do_tab(window):
    from confortimetro.gui.theme import RoundedButton
    button = RoundedButton(window, text="X")
    button.configure(state="disabled")
    assert str(button.cget("takefocus")) == "0"
    button.configure(state="normal")
    assert str(button.cget("takefocus")) == "1"


def test_nome_automatico_usa_hora_de_inicio():
    from confortimetro.gui.components.simulations_panel import _display
    run = {'run': '20261007_2133_2', 'idf': 'FAURB.idf', 'module_type': 'COMPLETE',
           'config': {}, 'modificado': datetime.datetime(2026, 10, 9, 8, 0)}
    assert _display(run)['name'] == "FAURB · 07/10 21:33 (2)"


def test_etiquetagem_tem_pagina_propria(window, tmp_path):
    # A tela de execução comum não oferece mais a etiquetagem.
    notebook = window.simulation_panel.notebook
    tabs = [notebook.tab(tab, "text").strip() for tab in notebook.tabs()]
    assert "Etiquetagem" not in tabs

    # Sem IDF escolhido, a página parte do IDF e das zonas do editor.
    window.on_new_labeling()
    _settle(window)
    assert window._current_page == "labeling"
    idf, _epw = window.labeling_panel.get_files()
    assert idf == window.path_panel.get_idf_path().strip()
    assert window.labeling_panel.get_rooms() == ["SALA1"]
    # Barra e log ativos são os da página da etiquetagem.
    assert window.control_panel is window._feedback["labeling"][0]
    assert not window.control_panel.save_button.winfo_ismapped()

    started = []
    window._validate_configuration = lambda config=None: True
    window.start_simulation = lambda config, origin="editor": started.append((config, origin))
    window.on_run_labeling()
    config, origin = started[0]
    assert origin == "labeling"
    assert config.rooms == ["SALA1"] and config.idf_path == idf
    assert config.etiquetagem["tipologia"] == "educacional"
    # A config do editor não é tocada.
    assert window.configs.etiquetagem is None

    window.show_page("editor")
    assert window.control_panel is window._feedback["editor"][0]


def test_duplicar_execucao_do_grupo_abre_a_etiquetagem(window, tmp_path):
    idf = tmp_path / "modelo.idf"
    idf.write_text("Zone,\n  SALA1,\nZone,\n  SALA2,\n")
    run_path = tmp_path / "grupo"
    run_path.mkdir()
    config = SimulationConfig(met_as_watts=125.496, _idf_path=str(run_path / "modelo_inic.idf"),
                              _met=1.2, epw_path=str(tmp_path / "clima.epw"),
                              output_path=str(run_path), rooms=["SALA2"],
                              nome="Base · INI-C real")
    config.etiquetagem = {"grupo": "grupo", "papel": "real", "zonas": ["SALA2"],
                          "idf_original": str(idf), "tipologia": "escritorio",
                          "uso": "Escritório", "modo": "hibrido", "zb": 3}
    config.to_json(str(run_path / "configs.json"))

    window.on_duplicate_run({'run': 'grupo', 'path': str(run_path), 'status': 'sem planilhas',
                             'rooms_disponiveis': [], 'modificado': datetime.datetime.now(),
                             'config': {}})

    assert window._current_page == "labeling"
    assert window.labeling_panel.get_files()[0] == str(idf)
    assert window.labeling_panel.get_rooms() == ["SALA2"]
    assert window.labeling_panel.get_options() == {
        "tipologia": "escritorio", "uso": "Escritório", "modo": "hibrido", "zb": 3}
    assert window.control_panel.get_name() == "Base (cópia)"
