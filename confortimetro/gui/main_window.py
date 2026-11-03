"""
Main window for the Ambiens application.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import tkinter as tk
from tkinter import messagebox, filedialog
from queue import Queue
import copy
from typing import Optional

from confortimetro.assistant import simulacao
from confortimetro.config import SimulationConfig
from confortimetro.module_type import ModuleType
from confortimetro.drive import sync as drive_sync
from confortimetro.idf import (apply_equipment_fixes, plan_equipment_fixes,
                                read_run_period, read_zone_names, rooms_without_people,
                                unwired_equipment,
                                write_idf_fields)
from confortimetro.paths import app_data_path, new_run_path, runs_root
from .components import (
    COMPARISON_HEADINGS,
    MACHINE_FIELDS,
    SIMULATION_FIELDS,
    PathConfigPanel,
    SimulationConfigPanel,
    IDFEditorPanel,
    ResultsPanel,
    ControlPanel,
    LabelingPanel,
    SimulationsPanel,
    ComparisonPanel,
    TimeSeriesPanel,
    AssistantPanel,
    AssistantSettings,
    DriveSettings,
    RemoteSettings,
    open_share_dialog,
)
from .components.drive_panel import in_background
from . import onboarding
from .theme import (
    COLORS,
    FONTS,
    SPACE,
    BottomSheet,
    Card,
    RoundedButton,
    apply_theme,
    ask_choices,
    icon,
    scrollable,
    toast,
)


class MainWindow(tk.Tk):
    """Main application window."""
    
    def __init__(self, config_path: str = None):
        super().__init__()

        # A configuração do usuário mora na pasta de dados do app; o
        # `examples/config.json` só semeia a primeira abertura.
        self.config_path = config_path or os.path.join(app_data_path(), "config.json")
        self._run_started = 0.0
        self.configs: Optional[SimulationConfig] = None
        self.simulation_thread: Optional[threading.Thread] = None
        # A simulação em andamento, para poder pedir o cancelamento a ela.
        self.simulation = None
        self.simulation_queue: Optional[Queue] = None
        self._simulation_error: Optional[str] = None
        # A listagem só é relida quando alguma execução termina.
        self._runs_dirty = False
        self._detail_run: Optional[dict] = None
        # Barra, log e status de cada página que executa ("editor" ou
        # "labeling"); `control_panel`, `results_panel` e `log_sheet` são os da
        # página que disparou a última simulação.
        self._feedback = {}
        self._run_page = "editor"
        # A thread do Drive marca aqui que algum status mudou.
        self._drive_change = threading.Event()
        # `after` da transição de página em andamento, se houver.
        
        self._setup_window()
        apply_theme(self)
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Escape>", lambda _e: self.show_page("runs"))
        for sequence in ("<F5>", "<Control-Return>"):
            self.bind(sequence, self._on_run_shortcut)
        self._load_configuration()
        # Backfill, pendentes e pull do Drive, depois que a janela aparece.
        self.after(1500, self.drive_sync)
        # Apresentação depois de instalar ou reinstalar (marca do instalador).
        if onboarding.pending():
            self.after(500, self.open_onboarding)

    @property
    def control_panel(self) -> ControlPanel:
        return self._feedback[self._run_page][0]

    @property
    def log_sheet(self) -> BottomSheet:
        return self._feedback[self._run_page][1]

    @property
    def results_panel(self) -> ResultsPanel:
        return self._feedback[self._run_page][2]

    def _build_run_feedback(self, page, key: str, callback, file_actions: bool = True):
        """Topbar de execução e log num bottom sheet, registrados em `key`."""
        topbar = Card(page, pad=SPACE[3])
        topbar.pack(fill="x", pady=(0, SPACE[4]))
        control = ControlPanel(topbar.body, callback=callback, file_actions=file_actions)
        control.pack(fill="x")
        sheet = BottomSheet(page, "Log de execução")
        sheet.pack(side="bottom", fill="x", pady=(SPACE[4], 0))
        results = ResultsPanel(sheet.body, callback=self)
        results.pack(fill="both", expand=True)
        self._feedback[key] = (control, sheet, results)

    def open_onboarding(self):
        return onboarding.open_onboarding(self)
    
    def _setup_window(self):
        """Setup the main window properties."""
        self.title("Ambiens — Simulações EnergyPlus")
        # Relativo à tela: 1200x900 fixo passava do alto de um notebook 768p.
        width = min(1200, int(self.winfo_screenwidth() * 0.9))
        height = min(900, int(self.winfo_screenheight() * 0.9))
        self.geometry(f"{width}x{height}")
        self.minsize(min(800, width), min(600, height))
        self.configure(background=COLORS["bg"])
        self._set_icon()
        self.center_window()

    def _set_icon(self):
        """Ícone da janela. Vários tamanhos porque o Tk reduz imagem sem
        suavizar: entregar um PNG grande só deixa o ícone da barra serrilhado.
        Cada tamanho é rasterizado do `logo.svg`; o gerenciador de janela pega
        o mais próximo. Falha silenciosa: nem todo WM suporta `iconphoto`."""
        assets = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "assets")
        try:
            self._icons = [
                tk.PhotoImage(file=os.path.join(assets, f"logo-{size}.png"))
                for size in (32, 48, 64, 128, 256, 512)
            ]
            self.iconphoto(True, *self._icons)
        except tk.TclError:
            pass

    def _build_ui(self):
        """Uma página por vez no mesmo host: execuções, detalhes, execução e
        configurações. Todas ficam montadas; navegar é `place`.
        """
        from tkinter import ttk

        container = ttk.Frame(self, style="Main.TFrame")
        container.pack(fill="both", expand=True, padx=SPACE[5], pady=SPACE[5])

        header = ttk.Frame(container, style="Main.TFrame")
        header.pack(fill="x", pady=(0, SPACE[3]))
        # `background` explícito: sobre o fundo da janela o estilo não basta
        # (ver o tk_setPalette em theme.apply_theme).
        ttk.Label(header, text="Ambiens", style="H1.TLabel",
                  background=COLORS["bg"]).pack(anchor="w")
        self.subtitle_label = ttk.Label(
            header, text="Simulações personalizadas com EnergyPlus",
            style="Sub.TLabel", background=COLORS["bg"])
        self.subtitle_label.pack(anchor="w", pady=(SPACE[1], 0))

        self.page_host = ttk.Frame(container, style="Main.TFrame")
        self.page_host.pack(fill="both", expand=True)

        footer = ttk.Frame(container, style="Main.TFrame")
        footer.pack(pady=(SPACE[3], 0))
        ttk.Label(footer, text="Feito por ",
                  style="Sub.TLabel", background=COLORS["bg"]).pack(side="left")
        link = ttk.Label(footer, text="glbessa", style="Sub.TLabel",
                         background=COLORS["bg"], cursor="hand2")
        link.configure(foreground=COLORS["accent"], font=FONTS["caption"] + ("underline",))
        link.pack(side="left")
        link.bind("<Button-1>",
                  lambda _e: webbrowser.open("https://github.com/glbessa"))

        self._pages = {
            "runs": self._build_runs_page(),
            "compare": self._build_compare_page(),
            "detail": self._build_detail_page(),
            "editor": self._build_editor_page(),
            "labeling": self._build_labeling_page(),
            "settings": self._build_settings_page(),
            "assistant": self._build_assistant_page(),
        }
        self._current_page = None
        self.show_page("runs")

    # ----------------------------------------------------------- navegação

    _PAGE_SUBTITLES = {
        "runs": "Execuções realizadas e comparação de resultados",
        "compare": "Comparação entre execuções",
        "detail": "Detalhes da execução",
        "editor": "Nova execução com EnergyPlus",
        "labeling": "Classe da envoltória pelo método de simulação da INI-C",
        "settings": "Configurações desta máquina",
        "assistant": "Converse sobre os resultados das execuções",
    }

    def show_page(self, name: str):
        """Mostra uma página trazendo-a para a frente da pilha."""
        if self._current_page == name:
            return

        self._current_page = name
        self.subtitle_label.configure(text=self._PAGE_SUBTITLES[name])
        # Parada, a barra e o log ativos passam a ser os da página que executa
        # e foi aberta; rodando, ficam os da simulação em andamento.
        if name in self._feedback and not self.control_panel.get_is_running():
            self._run_page = name

        # A listagem só relê a pasta quando alguma execução terminou: o sync
        # do banco custa segundos e não vale a cada ida e volta.
        if name == "runs" and self._runs_dirty:
            self._runs_dirty = False
            self.simulations_panel.refresh()

        # Troca seca: a anterior sai, a nova ocupa o host. Animar o deslize
        # repintava a página inteira a cada quadro e o Tk, sem double
        # buffering, pisca.
        for key, page in self._pages.items():
            if key == name:
                page.place(relx=0, rely=0, relwidth=1, relheight=1)
            else:
                page.place_forget()

    def _page_nav(self, page, title: str, back_to: str = None):
        """Cabeçalho da página: título e, quando faz sentido, o botão voltar."""
        from tkinter import ttk

        row = ttk.Frame(page, style="Main.TFrame")
        row.pack(fill="x", pady=(0, SPACE[3]))
        if back_to:
            RoundedButton(row, text="Voltar", variant="bar", icon="back",
                          command=lambda: self.show_page(back_to)).pack(side="left",
                                                                        padx=(0, SPACE[3]))
        ttk.Label(row, text=title, style="H2.TLabel",
                  background=COLORS["bg"]).pack(side="left")
        return row

    # -------------------------------------------------------------- páginas

    def _build_runs_page(self):
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        nav = self._page_nav(page, "Execuções")
        self.btn_nav_new_run = RoundedButton(
            nav, text="Nova execução", variant="primary", icon="new",
            command=lambda: self.show_page(
                self._run_page if self._simulation_running() else "editor"))
        self.btn_nav_new_run.pack(side="right")
        RoundedButton(nav, text="Etiquetagem", variant="bar", icon="zap",
                      command=self.on_new_labeling).pack(side="right", padx=(0, SPACE[2]))
        RoundedButton(nav, text="Atualizar", variant="bar", icon="refresh",
                      command=lambda: self.simulations_panel.refresh()).pack(
                          side="right", padx=(0, SPACE[2]))
        RoundedButton(nav, text="Configurações", variant="bar", icon="settings",
                      command=lambda: self.show_page("settings")).pack(
                          side="right", padx=(0, SPACE[2]))

        self.simulations_panel = SimulationsPanel(page, self._outputs_root(),
                                                  callback=self)
        self.simulations_panel.pack(fill="both", expand=True)
        return page

    def _build_compare_page(self):
        """Tabela e gráficos das execuções escolhidas na listagem."""
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Comparação de resultados", back_to="runs")

        self.comparison_panel = ComparisonPanel(page)
        self.comparison_panel.pack(fill="both", expand=True)
        return page

    def _build_detail_page(self):
        """Configuração completa da execução selecionada, com as ações dela."""
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Detalhes da execução", back_to="runs")

        # --- Ações ancoradas no rodapé ---
        actions = Card(page, pad=SPACE[3])
        actions.pack(side="bottom", fill="x", pady=(SPACE[3], 0))
        row = ttk.Frame(actions.body, style="Surface.TFrame")
        row.pack(fill="x")
        RoundedButton(row, text="Duplicar para nova execução", variant="primary", icon="duplicate",
                      tooltip="Abre a configuração desta execução na tela de execução",
                      command=lambda: self.on_duplicate_run(self._detail_run)).pack(
                          side="left")
        RoundedButton(row, text="Abrir pasta", variant="ghost", icon="open",
                       tooltip="Abre a pasta com as planilhas desta execução",
                       command=self._open_detail_folder).pack(side="left",
                                                              padx=(SPACE[2], 0))
        self.detail_export_button = RoundedButton(
            row, text="Baixar resumo", variant="ghost", icon="export",
            tooltip="Salva uma cópia do ESTATISTICAS.xlsx",
            command=self._export_detail_summary)
        self.detail_export_button.pack(side="left", padx=(SPACE[2], 0))
        self.detail_recompute_button = RoundedButton(
            row, text="Regerar estatísticas", variant="ghost", icon="recompute",
            tooltip="Recalcula o ESTATISTICAS.xlsx a partir das planilhas; leva minutos",
            command=self._recompute_detail_stats)
        self.detail_recompute_button.pack(side="left", padx=(SPACE[2], 0))
        # Só nas execuções do servidor; `on_open_run_details` mostra ou esconde.
        self.detail_full_button = RoundedButton(
            row, text="Baixar arquivos completos", variant="ghost", icon="cloud",
            command=self._download_full_run)

        self.detail_info_var = tk.StringVar()
        ttk.Label(page, textvariable=self.detail_info_var, style="Caption.TLabel",
                  justify="left").pack(anchor="w", pady=(0, SPACE[2]))

        # --- Abas: o resumo de sempre e a série temporal timestep a timestep ---
        self.detail_tabs = ttk.Notebook(page, style="Section.TNotebook")
        self.detail_tabs.pack(fill="both", expand=True)
        summary = ttk.Frame(self.detail_tabs, style="Main.TFrame")
        series_tab = Card(self.detail_tabs, pad=SPACE[3])
        self.detail_tabs.add(summary, text="Resumo")
        self.detail_tabs.add(series_tab, text="Série temporal")
        self.timeseries_panel = TimeSeriesPanel(series_tab.body)
        self.timeseries_panel.pack(fill="both", expand=True)
        assistant_tab = Card(self.detail_tabs, pad=SPACE[3])
        self.detail_tabs.add(assistant_tab, text="Assistente")
        self.detail_assistant = AssistantPanel(
            assistant_tab.body, self._outputs_root,
            config_getter=self._assistant_base_config,
            run_simulation=self.start_assistant_simulation)
        self.detail_assistant.pack(fill="both", expand=True)
        # A série só é lida com a aba visível: abrir os detalhes continua
        # tão rápido quanto antes.
        self.detail_tabs.bind("<<NotebookTabChanged>>", self._on_detail_tab_changed)

        # Rolável: a 125% em 720p não cabia tudo, e o pack zerava a altura do
        # último card (ou da tabela por zona).
        summary = scrollable(summary)

        # --- Resumo de consumo (KPI cards) ---
        kpi_card = Card(summary, pad=SPACE[3])
        kpi_card.pack(fill="x", pady=(0, SPACE[3]))
        kpi_row = ttk.Frame(kpi_card.body, style="Surface.TFrame")
        kpi_row.pack(fill="x")

        self.kpi_total_var = tk.StringVar(value="—")
        self.kpi_aquec_var = tk.StringVar(value="—")
        self.kpi_resfr_var = tk.StringVar(value="—")

        def _make_kpi(title: str, var: tk.StringVar, color: str, icon_name: str):
            f = ttk.Frame(kpi_row, style="Surface.TFrame")
            ic = icon(icon_name, 16, color, master=self)
            lbl_title = ttk.Label(f, text=" " + title, image=ic, compound="left",
                                  style="Caption.TLabel")
            lbl_title.image = ic
            lbl_title.pack(anchor="w")

            val_f = ttk.Frame(f, style="Surface.TFrame")
            val_f.pack(anchor="w", pady=(SPACE[1], 0))
            lbl_val = ttk.Label(val_f, textvariable=var, style="H1.TLabel",
                                background=COLORS["surface"])
            lbl_val.configure(foreground=color)
            lbl_val.pack(side="left")

            lbl_unit = ttk.Label(val_f, text=" kWh", style="Label.TLabel",
                                 background=COLORS["surface"])
            lbl_unit.configure(foreground=COLORS["text_mute"])
            lbl_unit.pack(side="left", anchor="s", pady=(0, 3))
            return f

        kpi1 = _make_kpi("Consumo Total", self.kpi_total_var, COLORS["primary"], "zap")
        kpi1.pack(side="left", padx=(SPACE[2], SPACE[5]))

        ttk.Separator(kpi_row, orient="vertical").pack(
            side="left", fill="y", padx=(0, SPACE[5]))

        kpi2 = _make_kpi("Aquecimento", self.kpi_aquec_var, COLORS["hot_text"], "flame")
        kpi2.pack(side="left", padx=(0, SPACE[5]))

        ttk.Separator(kpi_row, orient="vertical").pack(
            side="left", fill="y", padx=(0, SPACE[5]))

        kpi3 = _make_kpi("Resfriamento", self.kpi_resfr_var, "#2b6cb0", "snowflake")
        kpi3.pack(side="left", padx=(0, SPACE[5]))

        # --- Estatísticas por zona ---
        stats_card = Card(summary, "Estatísticas por zona")
        stats_card.pack(fill="x", pady=(0, SPACE[3]))
        self.detail_stats = ttk.Treeview(stats_card.body, style="Modern.Treeview",
                                         show="headings", height=4)
        stats_scroll = ttk.Scrollbar(stats_card.body, orient="horizontal",
                                     command=self.detail_stats.xview)
        self.detail_stats.configure(xscrollcommand=stats_scroll.set)
        from .components.comparison_panel import METRICS_NOTE
        ttk.Label(stats_card.body, text=METRICS_NOTE, style="Caption.TLabel",
                  wraplength=900, justify="left").pack(
                      side="bottom", fill="x", pady=(SPACE[2], 0))
        stats_scroll.pack(side="bottom", fill="x")
        self.detail_stats.pack(fill="x")
        self.detail_stats.tag_configure("total", font=FONTS["label"])
        self.detail_empty_var = tk.StringVar(
            value="Escolha uma execução na listagem para ver os resultados.")
        self.detail_empty = ttk.Label(summary, textvariable=self.detail_empty_var,
                                      style="Caption.TLabel", justify="left")

        # --- Configuração da execução ---
        card = Card(summary, "Configuração da execução")
        card.pack(fill="x")
        self.detail_text = tk.Text(
            card.body, wrap="none", state="disabled", font=FONTS["mono"], height=14,
            background=COLORS["surface"], foreground=COLORS["text"],
            relief="flat", borderwidth=0, highlightthickness=1,
            highlightbackground=COLORS["line"], padx=SPACE[3], pady=SPACE[3])
        detail_scroll = ttk.Scrollbar(card.body, orient="vertical",
                                      command=self.detail_text.yview)
        self.detail_text.configure(yscrollcommand=detail_scroll.set)
        detail_scroll.pack(side="right", fill="y")
        self.detail_text.pack(side="left", fill="both", expand=True)

        return page

    def _build_editor_page(self):
        """Topbar de execução, parâmetros no meio, log num bottom sheet."""
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Execução", back_to="runs")

        # Topbar (executar, salvar/carregar, estado) e o log no rodapé.
        self._build_run_feedback(page, "editor", self)

        # --- Parâmetros: as abas já são o card, sem moldura em volta ---
        self.simulation_panel = SimulationConfigPanel(page, callback=self)
        self.simulation_panel.pack(side="top", fill="both", expand=True)
        # Os caminhos entram como primeira aba: o IDF e o EPW são a entrada da
        # simulação, não mais um bloco solto acima dos parâmetros.
        self.path_panel = PathConfigPanel(self.simulation_panel.notebook,
                                           callback=self,
                                           fields=SIMULATION_FIELDS,
                                           padding=SPACE[3])
        self.simulation_panel.insert_tab(0, self.path_panel, "Arquivos",
                                          icon_name="files")
        self._build_idf_editor_tabs()
        return page

    def _build_labeling_page(self):
        """Etiquetagem INI-C: arquivos, APP e opções da norma, sem o resto."""
        from types import SimpleNamespace

        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Etiquetagem INI-C (envoltória)", back_to="runs")
        actions = SimpleNamespace(on_run_simulation=self.on_run_labeling,
                                  on_stop_simulation=self.on_stop_simulation)
        self._build_run_feedback(page, "labeling", actions, file_actions=False)
        card = Card(page, pad=0)
        card.pack(side="top", fill="both", expand=True)
        self.labeling_panel = LabelingPanel(card.body)
        self.labeling_panel.pack(fill="both", expand=True)
        return page

    def _build_idf_editor_tabs(self):
        """Inclui Período e Ocupação diretamente nas abas da execução."""
        self.idf_editor_panel = IDFEditorPanel(self.simulation_panel.notebook,
                                                on_save=self.on_save_idf_copy)
        self.simulation_panel.insert_tab(1, self.idf_editor_panel.period_tab,
                                          "Período", select=False,
                                          icon_name="timestep")
        self.simulation_panel.insert_tab(2, self.idf_editor_panel.occupation_tab,
                                          "Ocupação", select=False,
                                          icon_name="details")
        self.simulation_panel.notebook.bind(
            "<<NotebookTabChanged>>", self._load_idf_editor_for_selected_tab,
            add="+")

    def _load_idf_editor_for_selected_tab(self, _event=None):
        """Carrega o IDF ao abrir Período ou Ocupação diretamente.

        As duas abas ficam sempre disponíveis na execução. Sem esse
        carregamento sob demanda, elas só recebiam conteúdo ao usar o atalho
        ``Editar IDF`` do painel Arquivos.
        """
        selected = self.simulation_panel.notebook.select()
        editor_tabs = (str(self.idf_editor_panel.period_tab),
                       str(self.idf_editor_panel.occupation_tab))
        if selected not in editor_tabs:
            return

        idf_path = self.path_panel.get_idf_path().strip()
        if (idf_path and os.path.isfile(idf_path)
                and idf_path != self.idf_editor_panel.idf_path):
            self.idf_editor_panel.load(idf_path)

    def _build_settings_page(self):
        """Os caminhos que são da máquina, não da simulação."""
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Configurações", back_to="runs")

        # Rolável: em 720p os últimos cards ficavam fora da tela.
        body = scrollable(page)
        body.master.configure(bg=COLORS["bg"])
        body.configure(style="Main.TFrame")

        card = Card(body, "Configurações da máquina")
        card.pack(fill="x")
        self.settings_panel = PathConfigPanel(card.body, callback=self,
                                              fields=MACHINE_FIELDS)
        self.settings_panel.pack(fill="x")
        ttk.Label(card.body, text="Valem para todas as simulações desta "
                                  "máquina e são salvos junto da configuração.",
                  style="Caption.TLabel").pack(anchor="w", pady=(SPACE[3], 0))

        assistant_card = Card(body, "Assistente de análise")
        assistant_card.pack(fill="x", pady=(SPACE[3], 0))
        self.assistant_settings = AssistantSettings(assistant_card.body)
        self.assistant_settings.pack(fill="x")

        remote_card = Card(body, "Servidor de simulações")
        remote_card.pack(fill="x", pady=(SPACE[3], 0))
        RemoteSettings(remote_card.body, runs_root=self._outputs_root,
                       on_synced=self.simulations_panel.refresh).pack(fill="x")

        drive_card = Card(body, "Google Drive")
        drive_card.pack(fill="x", pady=(SPACE[3], 0))
        self.drive_settings = DriveSettings(drive_card.body, on_connected=self.drive_sync)
        self.drive_settings.pack(fill="x")

        tour_card = Card(body, "Apresentação")
        tour_card.pack(fill="x", pady=(SPACE[3], 0))
        RoundedButton(tour_card.body, text="Rever apresentação", variant="ghost", icon="info",
                      command=self.open_onboarding).pack(anchor="w")
        return page

    def _build_assistant_page(self):
        """Chat sobre as execuções escolhidas na listagem."""
        from tkinter import ttk

        page = ttk.Frame(self.page_host, style="Main.TFrame")
        self._page_nav(page, "Assistente de análise", back_to="runs")
        card = Card(page, pad=SPACE[3])
        card.pack(fill="both", expand=True)
        self.assistant_panel = AssistantPanel(
            card.body, self._outputs_root,
            config_getter=self._assistant_base_config,
            run_simulation=self.start_assistant_simulation)
        self.assistant_panel.pack(fill="both", expand=True)
        return page

    def _outputs_root(self) -> str:
        """Pasta que a listagem lê: a raiz das execuções, das configurações."""
        outputs_root = (self.settings_panel.get_output_path()
                        if hasattr(self, "settings_panel") else "")
        if not outputs_root and self.configs:
            outputs_root = self.configs.runs_root_path or ""

        if not outputs_root or not os.path.isdir(outputs_root):
            outputs_root = runs_root(create=True)
        return outputs_root

    def center_window(self):
        """Center the window on the screen."""
        self.update_idletasks()
        width = self.winfo_width()
        height = self.winfo_height()
        x = max(0, (self.winfo_screenwidth() - width) // 2)
        y = max(0, (self.winfo_screenheight() - height) // 2)
        self.geometry(f'{width}x{height}+{x}+{y}')
    
    def _load_configuration(self):
        """Load configuration from file."""
        try:
            if not os.path.exists(self.config_path):
                # Primeira abertura: semeia com o exemplo do repositório, se houver.
                seed = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "..", "examples", "config.json")
                try:
                    self.configs = SimulationConfig.from_json(seed)
                except Exception:
                    self.configs = SimulationConfig()
                os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
                self.configs.to_json(self.config_path)
                self.results_panel.append_info("Configuração padrão criada.")
            else:
                self.configs = SimulationConfig.from_json(self.config_path)
                self.results_panel.append_info("Configuração carregada com sucesso.")
            
            # Update UI with loaded configuration
            self._update_ui_from_config()
            self.simulations_panel.set_outputs_path(self._outputs_root())
            
        except Exception as e:
            self.results_panel.append_error(f"Erro ao carregar configuração: {str(e)}")
            self.configs = SimulationConfig()  # Fallback to default
    
    def _update_ui_from_config(self):
        """Update UI components with configuration data."""
        if not self.configs:
            return
        
        # Update path panel
        self.path_panel.set_idf_path(self.configs.idf_path)
        self.settings_panel.set_output_path(
            self.configs.runs_root_path or runs_root())
        self.path_panel.set_epw_path(self.configs.epw_path)
        self.settings_panel.set_energy_path(self.configs.energy_path)
        
        self._refresh_room_options(self.configs.idf_path)
        if os.path.isfile(self.configs.idf_path):
            self.idf_editor_panel.load(self.configs.idf_path)

        # Update simulation panel
        config_dict = {
            'pmv_lowerbound': self.configs.pmv_lowerbound,
            'pmv_upperbound': self.configs.pmv_upperbound,
            'max_vel': self.configs.max_vel,
            'adaptative_bound': self.configs.adaptative_bound,
            'temp_ac_min': self.configs.temp_ac_min,
            'temp_ac_max': self.configs.temp_ac_max,
            'met': self.configs.met,
            'wme': self.configs.wme,
            'pmv_comfort_bound': self.configs.pmv_comfort_bound,
            'co2_limit': self.configs.co2_limit,
            'air_speed_delta': self.configs.air_speed_delta,
            'temp_open_window_bound': self.configs.temp_open_window_bound,
            'clo_min': self.configs.clo_min,
            'clo_max': self.configs.clo_max,
            'clo_delta': self.configs.clo_delta,
            'clo_priority': self.configs.clo_priority,
            'rooms': self.configs.rooms,
            'module_type': self.configs.module_type,
            'run_period_start': self.configs.run_period_start,
            'run_period_end': self.configs.run_period_end,
            'timesteps_per_hour': self.configs.timesteps_per_hour,
        }
        self.simulation_panel.set_configuration(config_dict)
        self.control_panel.set_name(self.configs.nome)
    
    def _update_config_from_ui(self):
        """Update configuration from UI components."""
        if not self.configs:
            self.configs = SimulationConfig()
        
        # Update from path panel
        self.configs.idf_path = self.path_panel.get_idf_path()
        self.configs.runs_root_path = self.settings_panel.get_output_path()
        self.configs.epw_path = self.path_panel.get_epw_path()
        self.configs.energy_path = self.settings_panel.get_energy_path()
        self.configs.nome = self.control_panel.get_name()
        
        # Update from simulation panel
        sim_config = self.simulation_panel.get_configuration()
        if sim_config:  # Only update if we got valid configuration
            for key, value in sim_config.items():
                if hasattr(self.configs, key):
                    setattr(self.configs, key, value)
    
    def _save_configuration(self):
        """Save current configuration to file."""
        try:
            self._update_config_from_ui()
            
            # Ensure directory exists
            os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
            
            self.configs.to_json(self.config_path)
            self.results_panel.append_success("Configuração salva com sucesso.")
            
        except Exception as e:
            self.results_panel.append_error(f"Erro ao salvar configuração: {str(e)}")
            toast(self, f"Erro ao salvar configuração: {e}", "error")
    
    def _resolve_missing_equipment(self) -> list:
        """Faltas de equipamento que sobram depois de oferecer a correção.

        Quando dá para copiar o equipamento de outra zona do próprio IDF, a
        interface pergunta e grava um modelo novo ao lado do original — o
        arquivo escolhido pelo usuário nunca é reescrito.
        """
        problems = unwired_equipment(self.configs.idf_path,
                                     self.configs.rooms or [],
                                     self.configs.module_type)
        if not problems:
            return []
        for problem in problems:
            self.results_panel.append_warning(problem)

        rooms = self.configs.rooms or []
        fixes, _ = plan_equipment_fixes(self.configs.idf_path, rooms,
                                        self.configs.module_type)
        if not fixes:
            return problems

        # Com mais de um sistema para copiar, quem escolhe é o usuário: o
        # ar-condicionado de cada zona pode ser um equipamento diferente.
        choices = {}
        for fix in fixes:
            if len(fix["templates"]) > 1:
                choices.setdefault(
                    fix["prefix"],
                    (f"Copiar o {fix['label']} de:",
                     [f"{source} ({kind})"
                      for kind, source in dict.fromkeys(fix["templates"])]))
        if choices:
            answer = ask_choices(
                self, "Incluir o equipamento que falta?",
                f"{len(fixes)} zona(s) sem o equipamento que o módulo "
                f"{self.configs.module_type} controla. O IDF escolhido não é "
                "alterado: as inclusões vão para um arquivo novo ao lado dele, "
                "que passa a ser o modelo desta simulação.\n\nDe qual zona "
                "copiar o equipamento?",
                {label: options for label, options in choices.values()},
                confirm="Incluir", cancel="Agora não")
            if answer is None:
                return problems
            template_rooms = {}
            for prefix, (label, _) in choices.items():
                template_rooms[prefix] = answer[label].split(" (")[0]
            fixes, _ = plan_equipment_fixes(self.configs.idf_path, rooms,
                                            self.configs.module_type,
                                            template_rooms)

        # O log leva o inventário completo: um IDF costuma ter mais de um
        # sistema de ar-condicionado, e o usuário precisa ver qual foi copiado
        # e que objetos entram no modelo por causa disso.
        for fix in fixes:
            self.results_panel.append_info(fix["description"])
            if fix.get("note"):
                self.results_panel.append_warning(fix["note"])
            for detail in fix.get("details", ()):
                self.results_panel.append_info(f"    {detail}")

        detail = "\n".join(f"• {fix['description']}" for fix in fixes[:6])
        rest = len(fixes) - 6
        if rest > 0:
            detail += f"\n• (e mais {rest} no log)"

        notes = list(dict.fromkeys(fix["note"] for fix in fixes if fix.get("note")))
        if notes:
            detail += "\n\n" + "\n".join(notes)

        created = {}
        for fix in fixes:
            for object_type, _ in fix["objects"]:
                created[object_type] = created.get(object_type, 0) + 1
        if created:
            summary = ", ".join(f"{count}× {object_type}"
                                for object_type, count in sorted(created.items()))
            detail += f"\n\nObjetos criados: {summary} (nomes no log)."

        # Confirmação final mesmo depois do seletor: só aqui a lista reflete
        # o molde escolhido.
        if not messagebox.askyesno(
                "Incluir o equipamento que falta?",
                f"{detail}\n\nO IDF escolhido não é alterado: as inclusões vão "
                "para um arquivo novo ao lado dele, que passa a ser o modelo "
                "desta simulação.\n\nIncluir?",
                icon="question", parent=self):
            return problems

        target = self._new_idf_name(self.configs.idf_path, "equipamentos")
        try:
            apply_equipment_fixes(self.configs.idf_path, target, fixes)
        except (OSError, IndexError) as error:
            toast(self, f"Não foi possível gravar o IDF: {error}", "error")
            return problems

        self.path_panel.set_idf_path(target)
        self.on_idf_path_changed(target)
        toast(self, f"Equipamento incluído em {os.path.basename(target)}.", "ok")
        return unwired_equipment(target, self.configs.rooms or [],
                                 self.configs.module_type)

    def _validate_configuration(self, config: SimulationConfig = None) -> bool:
        """Valida `config` (padrão: a do editor) antes de rodar."""
        config = config or self.configs
        if not config:
            toast(self, "Configuração não carregada.", "error")
            return False
        
        # Check required paths
        if not os.path.exists(config.idf_path or ""):
            toast(self, "Arquivo IDF não encontrado.", "error")
            return False
        
        if not os.path.exists(config.epw_path or ""):
            toast(self, "Arquivo EPW não encontrado.", "error")
            return False
        
        if not os.path.exists(config.energy_path or ""):
            toast(self, "Pasta do EnergyPlus não existe.", "error")
            return False

        if config.etiquetagem:
            # A etiquetagem cria a ocupação da norma e troca o condicionamento:
            # o equipamento do módulo não importa.
            return True

        no_people = rooms_without_people(self.configs.idf_path,
                                         self.configs.rooms or [])
        if no_people:
            for problem in no_people:
                self.results_panel.append_error(problem)
            messagebox.showerror("Zona sem ocupação no IDF", "\n\n".join(no_people))
            return False

        # Zona sem o equipamento do módulo simularia inteira decidindo no vazio;
        # a simulação também barra isso, mas o aviso aqui chega antes da espera.
        self.configs.ignore_missing_equipment = False
        problems = self._resolve_missing_equipment()
        if problems:
            head = "\n".join(problems[:3])
            rest = len(problems) - 3
            if rest > 0:
                head += f"\n(e mais {rest} no log)"
            # Confirmação bloqueante, e não toast: a simulação leva horas e
            # começaria com zonas decidindo no vazio.
            if not messagebox.askyesno(
                    "Equipamento faltando no IDF",
                    f"{head}\n\nEssas zonas vão simular sem o equipamento que o "
                    f"módulo {self.configs.module_type} controla.\n\n"
                    "Rodar mesmo assim?",
                    icon="warning", default="no", parent=self):
                toast(self, "Simulação cancelada: equipamento faltando no IDF.",
                      "error")
                return False
            self.configs.ignore_missing_equipment = True
            self.results_panel.append_warning(
                "Simulação iniciada ignorando o equipamento faltante.")

        return True
    
    def _run_simulation_thread(self, q: Queue, config: SimulationConfig = None):
        """Run simulation in a separate thread."""
        try:
            config = config or self.configs
            if config is None:
                raise ValueError("Configuration not loaded")
            # Importado aqui, e não no topo: pythermalcomfort compila com numba
            # ao ser importado e custa ~12 s — a GUI abre sem ele.
            from confortimetro.remote import client as remote
            if remote.enabled():
                Simulation = remote.RemoteSimulation
            else:
                from confortimetro.simulation import Simulation

            runner = getattr(self, "_labeling_runner", None)
            self.simulation = runner or Simulation(copy.deepcopy(config))
            self.simulation.run(q)
        except Exception as e:
            self._simulation_error = str(e)
            q.put(f"Erro durante a simulação: {str(e)}\n")
    
    def _handle_simulation_message(self, message: str):
        """Mensagem da simulação: percentual vai para a barra, o resto, no log."""
        message = message.strip()
        if message.startswith("PROGRESS "):
            self.control_panel.set_progress(float(message.split()[1]))
            return
        if message.startswith("WARNING "):
            self.results_panel.append_warning(message[len("WARNING "):])
            return
        lower = message.lower()
        if "erro" in lower or "error" in lower or message.startswith("Erro"):
            self.results_panel.append_error(message)
            self._simulation_error = message
            self.control_panel.set_status(message, "error")
            return
        self.results_panel.append_info(message)
        self.control_panel.set_status(message, "running")

    def _check_simulation_thread(self):
        """Check simulation thread status and update UI."""
        if self.simulation_thread and self.simulation_thread.is_alive():
            # Get messages from queue
            while not self.simulation_queue.empty():
                self._handle_simulation_message(self.simulation_queue.get())
            
            # Schedule next check
            self.after(100, self._check_simulation_thread)
        else:
            # Simulation finished: as mensagens que sobraram na fila entram
            # antes do status final, senão elas o sobrescrevem.
            if self.simulation_queue:
                while not self.simulation_queue.empty():
                    self._handle_simulation_message(self.simulation_queue.get())

            self.control_panel.set_running_state(False)
            self.control_panel.run_button.configure(state="normal")
            interrupted = bool(self.simulation and self.simulation.stop_requested)
            has_error = bool(getattr(self, "_simulation_error", None))
            if interrupted:
                self.results_panel.append_warning("Simulação interrompida.")
                self.control_panel.set_status("Simulação interrompida", "warning")
            elif has_error:
                self.results_panel.append_error(
                    "Simulação finalizada com erros. Verifique os logs acima.")
                self.control_panel.set_status("Simulação falhou", "error")
            else:
                self.results_panel.append_success("Simulação concluída!")
                self.control_panel.set_status("Simulação concluída", "success")
            self._runs_dirty = True
            finished_path = getattr(self, "_running_run_path", None)
            if finished_path:
                self.simulations_panel.clear_running(finished_path)
                self._running_run_path = None
            if hasattr(self, "btn_nav_new_run"):
                self.btn_nav_new_run.configure(text="Nova execução", icon="new")
            self.simulation = None
            # A disparada pelo assistente não tira o usuário da conversa.
            from_assistant = getattr(self, "_running_origin", None) == "assistant"
            self._running_origin = None
            if finished_path and not interrupted and not has_error:
                self._drive_push(finished_path)
            runner, self._labeling_runner = getattr(self, "_labeling_runner", None), None
            if runner is not None and not interrupted and not has_error:
                # O grupo da etiquetagem abre na comparação, com a nota.
                for path in list(runner.execucoes.values())[1:]:
                    self._drive_push(path)
                self.simulations_panel.refresh()
                paths = set(runner.execucoes.values())
                runs = [r for r in self.simulations_panel._runs if r['path'] in paths]
                if runs:
                    self.on_compare_runs(runs, self._outputs_root())
            elif finished_path and not interrupted and not has_error and not from_assistant:
                run = next((r for r in self.simulations_panel._runs
                            if r['path'] == finished_path), None)
                if run is not None:
                    self.on_open_run_details(run)
    
    # ------------------------------------------------------------ Google Drive

    def _drive_changed(self):
        """Chamado da thread do Drive: só marca; quem toca no Tk é `_drive_tick`."""
        self._drive_change.set()

    def _drive_tick(self):
        if self._drive_change.is_set():
            self._drive_change.clear()
            self.simulations_panel.update_drive_status()

    def _drive_done(self, result, error):
        self.drive_settings.refresh()
        if error:
            toast(self, f"Google Drive: {error}", "error", timeout=10000)
        elif drive_sync.is_connected() and drive_sync.load_state().get("reconnect"):
            toast(self, "O acesso ao Google Drive expirou. Reconecte em Configurações.", "warn",
                  timeout=10000)
        if result and result.get("pulled"):
            self.simulations_panel.refresh()
            toast(self, f"{len(result['pulled'])} execução(ões) baixada(s) do Google Drive.",
                  "info")
        else:
            self.simulations_panel.update_drive_status()

    def drive_sync(self):
        """Envia o que falta (backfill e pendentes) e baixa as de outras máquinas.

        Na abertura e logo depois de conectar; sem conta, não faz nada.
        """
        self.drive_settings.refresh()
        self.simulations_panel.update_drive_status()
        if not drive_sync.is_connected():
            return
        root = self._outputs_root()

        def work():
            service = drive_sync.connected_service()
            return drive_sync.sync_all(service, root, self._drive_changed) if service else None

        in_background(self, work, self._drive_done, tick=self._drive_tick)

    def _drive_push(self, run_path: str):
        """Envio automático da execução que acabou de terminar."""
        if drive_sync.is_connected():
            in_background(self, lambda: drive_sync.push_after_run(run_path, self._drive_changed),
                          lambda _result, error: self._drive_done(None, error),
                          tick=self._drive_tick)

    def on_share_run(self, run: dict):
        if not drive_sync.is_connected():
            toast(self, "Conecte o Google Drive em Configurações para compartilhar.", "warn")
            return
        open_share_dialog(self, run['path'])

    # Callback implementations for PathConfigPanel
    def on_idf_path_changed(self, path: str):
        """Handle IDF path change."""
        if self.configs:
            self.configs.idf_path = path
        self._refresh_room_options(path)
        if os.path.isfile(path):
            self.idf_editor_panel.load(path)

    # ------------------------------------------------------------ editor IDF

    def on_edit_idf(self):
        """Carrega o IDF e abre a aba Período na tela de execução."""
        idf_path = self.path_panel.get_idf_path().strip()
        if not idf_path or not os.path.isfile(idf_path):
            toast(self, "Escolha um arquivo IDF antes de editá-lo.", "warn")
            return
        self.idf_editor_panel.load(idf_path)
        self.simulation_panel.notebook.select(self.idf_editor_panel.period_tab)

    def on_save_idf_copy(self):
        """Grava um IDF novo (nome sugerido, à escolha do usuário) e passa a usá-lo.

        O arquivo escolhido pelo usuário nunca é reescrito: a sugestão é um
        `<nome>_editado.idf` na pasta de dados do app, nunca em `examples/`.
        """
        source = self.idf_editor_panel.idf_path
        updates = self.idf_editor_panel.get_updates()
        if updates is None:      # já avisou qual campo está inválido
            return
        if not updates:
            toast(self, "Nenhum campo foi alterado.", "info")
            return

        suggested = self._new_idf_name(source)
        target = filedialog.asksaveasfilename(
            parent=self, title="Salvar IDF editado como",
            initialdir=os.path.dirname(suggested),
            initialfile=os.path.basename(suggested),
            defaultextension=".idf", filetypes=[("IDF do EnergyPlus", "*.idf")])
        if not target:
            return
        if os.path.abspath(target) == os.path.abspath(source):
            toast(self, "Escolha um nome diferente: o IDF original não é reescrito.",
                  "warn")
            return
        try:
            write_idf_fields(source, target, updates)
        except (OSError, IndexError) as error:
            toast(self, f"Não foi possível gravar o IDF: {error}", "error")
            return

        self.path_panel.set_idf_path(target)
        self.on_idf_path_changed(target)
        self.simulation_panel.notebook.select(self.path_panel)
        toast(self, f"IDF salvo em {os.path.basename(target)}.", "ok")

    @staticmethod
    def _new_idf_name(source: str, suffix: str = "editado") -> str:
        """`<nome>_<sufixo>.idf` na pasta `modelos` dos dados do app,
        numerado enquanto o nome já existir."""
        base, extension = os.path.splitext(os.path.basename(source))
        folder = os.path.join(app_data_path(), "modelos")
        os.makedirs(folder, exist_ok=True)
        candidate = os.path.join(folder, f"{base}_{suffix}{extension}")
        counter = 2
        while os.path.exists(candidate):
            candidate = os.path.join(folder, f"{base}_{suffix}_{counter}{extension}")
            counter += 1
        return candidate

    def _refresh_room_options(self, idf_path: str):
        """Ofereça as zonas do IDF escolhido no seletor de salas."""
        self.simulation_panel.set_room_options(read_zone_names(idf_path))
    
    def on_output_path_changed(self, path: str):
        """Handle runs root change."""
        if self.configs:
            self.configs.runs_root_path = path
        self.simulations_panel.set_outputs_path(self._outputs_root())
    
    def on_epw_path_changed(self, path: str):
        """Handle EPW path change."""
        if self.configs:
            self.configs.epw_path = path
    
    def on_energy_path_changed(self, path: str):
        """Handle energy path change."""
        if self.configs:
            self.configs.energy_path = path
    
    # Callback implementations for SimulationConfigPanel
    def on_simulation_config_changed(self):
        """Handle simulation configuration change."""
    
    # Callback implementations for ResultsPanel
    def on_results_cleared(self):
        """Handle results cleared."""
        pass
    
    # Callback implementations for ControlPanel
    def _reset_run_feedback(self):
        """Log, contador e selo limpos: cada execução começa sem a anterior."""
        if self.control_panel.get_is_running():
            return
        self.results_panel.clear()
        self.control_panel.set_status("Pronto para executar", "info")
        self._simulation_error = None

    def _busy(self) -> bool:
        """Uma simulação por vez: com outra rodando, avisa onde ela está."""
        if not self.control_panel.get_is_running():
            return False
        if self._current_page != self._run_page:
            toast(self, "Já há uma simulação em andamento; acompanhe em "
                        "\"Ver em andamento\".", "warn")
        return True

    def on_run_simulation(self):
        """Handle run simulation request."""
        if self._busy():
            return
        self._run_page = "editor"
        self._reset_run_feedback()

        # Save current configuration
        try:
            self._update_config_from_ui()
        except ValueError as error:
            self._reject_configuration([str(error)])
            return
        
        # Validate configuration
        if not self._validate_configuration():
            return

        self.start_simulation(self.configs)

    def on_new_labeling(self):
        """Página da etiquetagem; sem IDF escolhido, parte do IDF, do EPW e das
        zonas do editor."""
        panel = self.labeling_panel
        if not panel.get_files()[0]:
            panel.set_files(self.path_panel.get_idf_path().strip(),
                            self.path_panel.get_epw_path().strip())
            panel.set_rooms(self.simulation_panel.rooms_select.get_values())
        if not self._feedback["labeling"][0].get_is_running():
            self._feedback["labeling"][0].set_name(None)
        self.show_page("labeling")

    def _labeling_config(self) -> SimulationConfig:
        """A config da etiquetagem: caminhos da máquina e os campos da página.
        O resto (conforto, módulo) não entra nos modelos da norma."""
        config = copy.deepcopy(self.configs) if self.configs else SimulationConfig()
        config.runs_root_path = self.settings_panel.get_output_path()
        config.energy_path = self.settings_panel.get_energy_path()
        config.idf_path, config.epw_path = self.labeling_panel.get_files()
        config.source_idf_path = None
        config.rooms = self.labeling_panel.get_rooms()
        config.nome = self.control_panel.get_name()
        config.etiquetagem = self.labeling_panel.get_options()
        return config

    def on_run_labeling(self):
        if self._busy():
            return
        self._run_page = "labeling"
        self._reset_run_feedback()
        config = self._labeling_config()
        if not config.rooms:
            self._reject_configuration(["Escolha as zonas de permanência prolongada (APP)."])
            return
        if self._validate_configuration(config):
            self.start_simulation(config, origin="labeling")

    def _on_run_shortcut(self, _event=None):
        """F5 / Ctrl+Enter executam na página de execução (nunca param)."""
        if self._current_page == "editor":
            self.on_run_simulation()
        elif self._current_page == "labeling":
            self.on_run_labeling()

    def _simulation_running(self) -> bool:
        return bool(self.simulation_thread and self.simulation_thread.is_alive())

    def _on_close(self):
        """Fechar a janela com simulação em andamento a encerra antes."""
        if self._simulation_running():
            if not messagebox.askyesno(
                    "Simulação em andamento",
                    "Há uma simulação em andamento. Fechar o Ambiens a encerra e "
                    "a execução fica incompleta.\n\nFechar mesmo assim?",
                    icon="warning", default="no", parent=self):
                return
            self._stop_simulation()
            self.simulation_thread.join(timeout=10)
        self.destroy()

    def _reject_configuration(self, problems):
        """Mostra por que a simulação não começou: log completo e um toast."""
        for problem in problems:
            self.results_panel.append_error(problem)
        rest = f" (e mais {len(problems) - 1} no log)" if len(problems) > 1 else ""
        toast(self, f"Configuração inválida: {problems[0]}{rest}", "error", timeout=8000)

    def start_simulation(self, config: SimulationConfig, origin: str = "editor") -> Optional[str]:
        """Inicia `config` em segundo plano; devolve a pasta da execução.

        Caminho único do botão Executar e da proposta confirmada no
        assistente: pasta nova na raiz, linha "em simulação" na listagem, log
        e a thread acompanhada por `_check_simulation_thread`. Configuração
        recusada por `simulacao.validate` não roda: devolve `None` e o motivo
        vai para o log e um toast.
        """
        if origin not in ("editor", "labeling"):   # esses já limparam ao validar
            self._run_page = "editor"
            self._reset_run_feedback()
        # Só a página da etiquetagem monta o grupo INI-C; o editor e o
        # assistente rodam sempre uma execução comum.
        labeling = config.etiquetagem if origin == "labeling" else None
        if not labeling:
            config.etiquetagem = None
        checked = config
        if labeling:
            # Cada modelo da etiquetagem roda no módulo somente EnergyPlus.
            checked = copy.deepcopy(config)
            checked.module_type = ModuleType.ENERGYPLUS_ONLY
        problems, _ = simulacao.validate(checked)
        if problems:
            self._reject_configuration(problems)
            return None

        self._simulation_error = None
        # Cada rodada escreve numa subpasta nova da raiz configurada; nunca
        # por cima da anterior.
        config.output_path = new_run_path(root=config.runs_root_path)
        self._labeling_runner = None
        if labeling:
            try:
                self._labeling_runner = self._labeling(config, labeling)
            except (OSError, ValueError) as error:
                self._reject_configuration([f"Etiquetagem INI-C: {error}"])
                return None
        # Entra na listagem como "em simulação" já na largada, antes de a
        # pasta existir.
        self._running_run_path = config.output_path
        self._running_origin = origin
        self.simulations_panel.set_running(self._running_run_path, config)
        if hasattr(self, "btn_nav_new_run"):
            self.btn_nav_new_run.configure(text="Ver em andamento", icon="running")
        
        # Start simulation
        self.control_panel.set_running_state(True)
        self.log_sheet.set_open(True)
        self.results_panel.append_info("Iniciando simulação...")
        
        # Create queue for communication
        self.simulation_queue = Queue()
        
        # Start simulation thread
        self.simulation_thread = threading.Thread(
            target=self._run_simulation_thread,
            args=(self.simulation_queue, config),
            daemon=True  # fechar a janela não deixa o processo preso
        )
        self._run_started = time.monotonic()
        self._warn_long_run(config)
        self.simulation_thread.start()
        
        # Start checking thread status
        self.after(100, self._check_simulation_thread)
        return config.output_path

    def _labeling(self, config: SimulationConfig, opcoes: dict):
        """Gera os modelos da etiquetagem; a ZB automática sai do EPW."""
        from confortimetro.etiquetagem.execucao import EtiquetagemRun, zona_sugerida
        from confortimetro.etiquetagem.norma import ZONAS

        opcoes = dict(opcoes)
        if not opcoes.get("zb"):
            zb, cidade = zona_sugerida(config.epw_path, config.idf_path)
            if zb is None:
                raise ValueError("nem o EPW nem o IDF dizem o local; escolha a "
                                 "zona bioclimática.")
            opcoes["zb"] = zb
            self.results_panel.append_info(
                f"Zona bioclimática {zb} ({ZONAS[zb][0].lower()}), pelo município "
                f"mais próximo do EPW: {cidade}.")
        return EtiquetagemRun(copy.deepcopy(config), opcoes, config.output_path)

    def _warn_long_run(self, config: SimulationConfig):
        """Avisa quando o período simulado torna a simulação demorada.

        O período editado na tela vale sobre o do IDF: é ele que vai para o
        RunPeriod da execução."""
        from confortimetro.idf.processor import parse_iso_date
        try:
            start, end = read_run_period(config.idf_path)
        except Exception:
            return
        start = parse_iso_date(getattr(config, "run_period_start", None)) or start
        end = parse_iso_date(getattr(config, "run_period_end", None)) or end
        days = (end - start).days
        if days > 90:
            self.results_panel.append_warning(
                f"Período de {days} dias: a simulação pode levar de dezenas de "
                "minutos a 1–2 h. A barra mostra o progresso e o tempo restante.")

    def _assistant_base_config(self) -> SimulationConfig:
        """Cópia da configuração da tela de execução: a base das propostas."""
        self._update_config_from_ui()
        config = copy.deepcopy(self.configs)
        config.runs_root_path = self._outputs_root()
        return config

    def start_assistant_simulation(self, config: SimulationConfig):
        """Proposta confirmada no assistente: `(pasta, None)` ou `(None, erro)`."""
        if self.control_panel.get_is_running() or (
                self.simulation_thread and self.simulation_thread.is_alive()):
            return None, ("Já há uma simulação em andamento. Espere terminar ou "
                          "interrompa-a na tela de execução.")
        config.runs_root_path = self._outputs_root()
        self.results_panel.append_info("Simulação proposta pelo assistente e "
                                       "confirmada pelo usuário.")
        path = self.start_simulation(config, origin="assistant")
        if path is None:
            return None, "Configuração recusada na validação; veja o log da execução."
        return path, None
    
    def on_stop_simulation(self):
        """Pedido do usuário: confirma antes. Ignora o clique logo após iniciar
        (o segundo clique de um duplo clique em Executar caía aqui)."""
        if not self._simulation_running() or time.monotonic() - self._run_started < 1.0:
            return
        if not messagebox.askyesno(
                "Parar simulação",
                "Parar a simulação em andamento? A execução ficará incompleta.",
                icon="warning", default="no", parent=self):
            return
        self._stop_simulation()

    def _stop_simulation(self):
        """Encerra a simulação (sem perguntar)."""
        if not self._simulation_running():
            return
        # `stop_simulation` da API do EnergyPlus: ele encerra no próximo passo,
        # então a thread continua viva por alguns segundos e o botão fica
        # desabilitado até ela terminar de verdade.
        self.results_panel.append_warning("Parada de simulação solicitada...")
        self.control_panel.set_status("Interrompendo simulação...", "warning")
        self.control_panel.run_button.configure(state="disabled")
        if self.simulation:
            self.simulation.stop()
    
    # --- Callbacks do SimulationsPanel ---

    def on_new_run(self):
        """Página de execução; a pasta de saída sai da raiz ao rodar."""
        self._reset_run_feedback()
        # Os parâmetros da anterior ficam; o nome é dela.
        if not self.control_panel.get_is_running():
            self.control_panel.set_name(None)
        self.show_page("editor")

    def on_compare_runs(self, runs: list, outputs_path: str):
        """Leva as execuções escolhidas para a página de comparação."""
        self.comparison_panel.set_runs(runs, outputs_path)
        self.show_page("compare")

    def on_ask_assistant(self, runs: list, outputs_path: str):
        """Página do assistente com as execuções escolhidas como contexto."""
        self.assistant_panel.set_context([run['run'] for run in runs])
        self.show_page("assistant")

    def on_open_run_details(self, run: dict):
        """Página com a configuração completa da execução escolhida. Se estiver
        em andamento, abre a página de execução com o log ativo para acompanhamento."""
        if (run.get('status') == 'em simulação'
                or run['path'] in self.simulations_panel._running
                or run['path'] == getattr(self, '_running_run_path', None)):
            self.show_page(self._run_page)
            self.log_sheet.set_open(True)
            return

        self._detail_run = run
        self.detail_info_var.set(
            f"Pasta: {os.path.abspath(run['path'])}\n"
            "Planilhas Excel (.xlsx): uma por zona e o resumo ESTATISTICAS.xlsx.")
        # Execução interrompida ou sem planilhas não tem o que regerar.
        self.detail_recompute_button.configure(
            state="disabled" if run.get('status') == 'sem planilhas' else "normal")
        from confortimetro.remote.client import is_remote
        if is_remote(run['path']):
            self.detail_full_button.pack(side="left", padx=(SPACE[2], 0))
        else:
            self.detail_full_button.pack_forget()
        lines = [run['run'], "-" * len(run['run']), "",
                 f"{'pasta':24s} {os.path.abspath(run['path'])}",
                 f"{'status':24s} {run['status']}",
                 f"{'modificado':24s} {run['modificado'].strftime('%d/%m/%Y %H:%M')}",
                 f"{'zonas':24s} {', '.join(run['rooms_disponiveis']) or '—'}", ""]
        def shown(value):
            # Sem "True" nem ponto decimal numa tela em português.
            if isinstance(value, bool):
                return "Sim" if value else "Não"
            if isinstance(value, float):
                return f"{value:g}".replace(".", ",")
            if isinstance(value, (list, tuple)):
                return ", ".join(shown(item) for item in value)
            return value

        lines += [f"{key.lstrip('_'):24s} {shown(value)}"
                  for key, value in run['config'].items()]

        self.detail_text.configure(state="normal")
        self.detail_text.delete("1.0", "end")
        self.detail_text.insert("1.0", "\n".join(lines))
        self.detail_text.configure(state="disabled")
        self._render_detail_stats(run)
        self.detail_tabs.select(0)
        self.timeseries_panel.set_run(run)
        self.detail_assistant.set_context([run['run']], run_filter=run['run'])
        self.show_page("detail")

    def _on_detail_tab_changed(self, _event=None):
        if self.detail_tabs.index("current") == 1:
            self.timeseries_panel.activate()
        else:
            self.timeseries_panel.deactivate()

    def _render_detail_stats(self, run: dict):
        """Uma linha por zona, lida do ESTATISTICAS.xlsx que a execução já gravou."""
        import pandas

        from confortimetro.results.compare import COMPARISON_COLUMNS, PERCENTAGE_COLUMNS

        tree = self.detail_stats
        tree.delete(*tree.get_children())

        stats_path = os.path.join(run['path'], 'ESTATISTICAS.xlsx')
        if not os.path.exists(stats_path):
            if hasattr(self, 'detail_export_button'):
                self.detail_export_button.configure(state="disabled")
            self.kpi_total_var.set("—")
            self.kpi_aquec_var.set("—")
            self.kpi_resfr_var.set("—")
            tree["columns"] = ("aviso",)
            tree.heading("aviso", text="Estatísticas")
            tree.column("aviso", width=600, anchor="w", stretch=True)
            tree.insert("", "end", values=(
                "Sem estatísticas: use Regerar estatísticas na listagem.",))
            self.detail_empty_var.set(
                "Esta execução ainda não tem ESTATISTICAS.xlsx. Use Regerar estatísticas para calcular os indicadores a partir das planilhas existentes.")
            self.detail_empty.pack(anchor="w", pady=(SPACE[2], 0))
            return

        try:
            df = pandas.read_excel(stats_path)
        except Exception as error:
            if hasattr(self, 'detail_export_button'):
                self.detail_export_button.configure(state="disabled")
            self.kpi_total_var.set("—")
            self.kpi_aquec_var.set("—")
            self.kpi_resfr_var.set("—")
            tree["columns"] = ("aviso",)
            tree.heading("aviso", text="Estatísticas")
            tree.column("aviso", width=600, anchor="w", stretch=True)
            tree.insert("", "end", values=(f"Não foi possível ler: {error}",))
            self.detail_empty_var.set("Não foi possível abrir as estatísticas. Tente regerá-las a partir das planilhas da execução.")
            self.detail_empty.pack(anchor="w", pady=(SPACE[2], 0))
            return

        if hasattr(self, 'detail_export_button'):
            self.detail_export_button.configure(state="normal")
        self.detail_empty.pack_forget()

        def _fmt_kwh(val):
            if val is None or pandas.isna(val):
                return "—"
            return f"{float(val):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        total_kwh = df['Energia total (kWh)'].sum() if 'Energia total (kWh)' in df.columns else None
        aquec_kwh = df['Aquecimento (kWh)'].sum() if 'Aquecimento (kWh)' in df.columns else None
        resfr_kwh = df['Resfriamento (kWh)'].sum() if 'Resfriamento (kWh)' in df.columns else None

        self.kpi_total_var.set(_fmt_kwh(total_kwh))
        self.kpi_aquec_var.set(_fmt_kwh(aquec_kwh))
        self.kpi_resfr_var.set(_fmt_kwh(resfr_kwh))

        columns = ["Nome da sala"] + [column for column in COMPARISON_COLUMNS
                                      if column in df.columns]
        tree["columns"] = columns
        for column in columns:
            tree.heading(column, text=COMPARISON_HEADINGS.get(column, column))
            width = 160 if column == "Nome da sala" else 130
            tree.column(column, width=width, minwidth=width,
                        anchor="w" if column == "Nome da sala" else "e",
                        stretch=False)

        def _fmt_cell(col, val):
            if pandas.isna(val):
                return "—"
            if isinstance(val, (int, float)):
                if col in PERCENTAGE_COLUMNS:
                    return f"{val * 100:.1f}%".replace(".", ",")
                return f"{val:.3f}".replace(".", ",")
            return val

        for _, row in df.iterrows():
            tree.insert("", "end", values=[_fmt_cell(col, row[col]) for col in columns])

        # Linha TOTAL no rodapé da tabela
        if len(df) > 0 and total_kwh is not None:
            total_row_values = []
            for col in columns:
                if col == "Nome da sala":
                    total_row_values.append("TOTAL")
                elif col == "Energia total (kWh)":
                    total_row_values.append(f"{total_kwh:.3f}".replace(".", ","))
                elif col == "Aquecimento (kWh)" and aquec_kwh is not None:
                    total_row_values.append(f"{aquec_kwh:.3f}".replace(".", ","))
                elif col == "Resfriamento (kWh)" and resfr_kwh is not None:
                    total_row_values.append(f"{resfr_kwh:.3f}".replace(".", ","))
                else:
                    total_row_values.append("—")
            tree.insert("", "end", values=total_row_values, tags=("total",))

        tree.configure(height=max(4, min(len(df) + 1, 8)))

    def on_duplicate_run(self, run: Optional[dict]):
        """Carrega a configuração da execução na página de execução, com uma
        pasta de saída nova — duplicar é repetir os parâmetros, nunca escrever
        por cima dos resultados que já existem.
        """
        if not run:
            return
        try:
            config = SimulationConfig.from_json(
                os.path.join(run['path'], "configs.json"))
        except Exception as error:
            toast(self, f"Não foi possível ler a configuração: {error}", "error")
            return

        opcoes = config.etiquetagem or {}
        if "papel" in opcoes:
            # Execução de um grupo INI-C: repete a etiquetagem na página dela.
            self.labeling_panel.set_files(opcoes.get("idf_original"), config.epw_path)
            self.labeling_panel.set_rooms(opcoes.get("zonas") or config.rooms)
            self.labeling_panel.set_options(opcoes)
            if not self._busy():
                self._run_page = "labeling"
                self._reset_run_feedback()
                nome = (config.nome or "").split(" · INI-C")[0]
                self.control_panel.set_name(f"{nome} (cópia)" if nome else None)
            self.show_page("labeling")
            return

        # `idf_path` da execução aponta para a cópia dentro dela; o modelo
        # escolhido pelo usuário é o `source_idf_path`.
        if config.source_idf_path:
            config.idf_path = config.source_idf_path
        config.output_path = None
        config.runs_root_path = self._outputs_root()
        # Mesmo nome nas duas deixaria a listagem e a comparação ambíguas.
        if config.nome:
            config.nome = f"{config.nome} (cópia)"

        self.configs = config
        self._update_ui_from_config()
        self._reset_run_feedback()
        self.results_panel.append_info(f"Configuração duplicada de {run['run']}.")
        self.show_page("editor")

    def _recompute_detail_stats(self):
        if not self._detail_run:
            toast(self, "Abra uma execução antes de regerar as estatísticas.", "warn")
            return
        run = self._detail_run
        if run.get('status') == 'sem planilhas':
            toast(self, "Esta execução não tem planilhas (foi interrompida ou falhou): "
                        "não há o que regerar.", "warn")
            return
        from confortimetro.results.compare import recompute_runs
        before = (self.kpi_total_var.get(), self.kpi_aquec_var.get(),
                  self.kpi_resfr_var.get())
        # Enquanto regera, nada de exportar nem regerar de novo.
        self.detail_recompute_button.configure(state="disabled", text="Regerando…")
        self.detail_export_button.configure(state="disabled")
        self.detail_empty_var.set("Regerando estatísticas… isso lê todas as planilhas e leva minutos.")
        toast(self, "Regerando estatísticas… leva minutos.", "info")

        def done(errors, error):
            self.detail_recompute_button.configure(state="normal",
                                                   text="Regerar estatísticas")
            failure = error or (errors or {}).get(run['path'])
            self.simulations_panel.refresh()
            self._render_detail_stats(run)
            if failure:
                self.results_panel.append_error(f"Regerar estatísticas: {failure}")
                toast(self, "Não foi possível regerar as estatísticas: as planilhas "
                            "desta execução estão incompletas ou ilegíveis "
                            "(detalhe no log).", "error", timeout=10000)
                return
            after = (self.kpi_total_var.get(), self.kpi_aquec_var.get(),
                     self.kpi_resfr_var.get())
            if after != before:
                toast(self, f"Estatísticas regeradas e os valores mudaram: total "
                            f"{before[0]} → {after[0]} kWh, resfriamento "
                            f"{before[2]} → {after[2]} kWh.", "warn", timeout=10000)
            else:
                toast(self, "Estatísticas regeradas; os valores não mudaram.", "ok")

        in_background(self, lambda: recompute_runs([run['path']]), done)

    def _export_detail_summary(self):
        """Exporta o resumo das estatísticas da execução aberta em XLSX."""
        if not self._detail_run:
            toast(self, "Abra uma execução antes de exportar o resumo.", "warn")
            return
        stats_path = os.path.join(self._detail_run['path'], 'ESTATISTICAS.xlsx')
        if not os.path.exists(stats_path):
            toast(self, "Esta execução ainda não possui estatísticas para exportar.", "warn")
            return

        run_name = self._detail_run.get('run') or os.path.basename(os.path.normpath(self._detail_run['path']))
        initial_file = f"RESUMO_{run_name}.xlsx"
        dest_path = filedialog.asksaveasfilename(
            parent=self,
            title="Baixar resumo da execução",
            defaultextension=".xlsx",
            initialfile=initial_file,
            filetypes=[("Planilha Excel", "*.xlsx")]
        )
        if not dest_path:
            return

        try:
            shutil.copyfile(stats_path, dest_path)
            toast(self, "Resumo exportado com sucesso.", "ok")
        except OSError as exc:
            toast(self, f"Falha ao exportar resumo: {exc}", "error", timeout=10000)

    def _download_full_run(self):
        """Traz do servidor os brutos do EnergyPlus que o espelho deixou de fora."""
        if not self._detail_run or not messagebox.askyesno(
                "Baixar arquivos completos",
                "Baixa do servidor todos os arquivos da execução, inclusive os brutos "
                "do EnergyPlus (eplusout.eso, .sql…). Uma simulação anual passa de 1 GB.\n\n"
                "Continuar?", parent=self):
            return
        from confortimetro.remote.client import download_full
        path = self._detail_run['path']
        self.detail_full_button.configure(state="disabled", text="Baixando…")

        def done(_result, error):
            self.detail_full_button.configure(state="normal", text="Baixar arquivos completos")
            if error:
                toast(self, f"Falha ao baixar: {error}", "error", timeout=10000)
            else:
                toast(self, "Arquivos completos baixados na pasta da execução.", "ok")

        in_background(self, lambda: download_full(path), done)

    def _open_detail_folder(self):
        if not self._detail_run:
            return
        path = os.path.abspath(self._detail_run['path'])
        if sys.platform == "win32":
            os.startfile(path)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", path])

    def on_save_config(self):
        """Handle save configuration request."""
        self._save_configuration()
    
    def on_load_config(self):
        """Handle load configuration request."""
        file_path = filedialog.askopenfilename(
            title="Carregar Configuração",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
            initialdir=os.path.dirname(self.config_path)
        )
        
        if file_path:
            try:
                self.configs = SimulationConfig.from_json(file_path)
                self._update_ui_from_config()
                self.results_panel.append_success(f"Configuração carregada de: {file_path}")
            except Exception as e:
                self.results_panel.append_error(f"Erro ao carregar configuração: {str(e)}")
                toast(self, f"Erro ao carregar configuração: {e}", "error")


def main():
    """Main entry point."""
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
