"""
Simulation configuration panel component.
"""

import tkinter as tk
from tkinter import ttk
from typing import Protocol, Optional

from ..theme import (
    COLORS, SPACE, ChipSelect, RangeField, Tooltip, fmt_num, icon, parse_num,
    scrollable,
)
from confortimetro.config import (
    ADAPTATIVE2PORCENT, PORCENT2ADAPTATIVE, RANGE_PAIRS, range_problems,
)
from confortimetro.module_type import ModuleType


# Rótulos do combobox: o nome do enum não diz o que cada módulo faz.
MODULE_LABELS = {
    ModuleType.COMPLETE:
        "Completo (janela, ventilador e ar-condicionado)",
    ModuleType.WITHOUT_FAN:
        "Sem ventilador (janela e ar-condicionado)",
    ModuleType.FIXED_AC_WITHOUT_FAN:
        "Ar-condicionado fixo (janela e AC com setpoint fixo, sem ventilador)",
    ModuleType.CLOSED_WINDOW:
        "Janela fechada (ventilador e ar-condicionado)",
    ModuleType.ENERGYPLUS_ONLY:
        "Somente EnergyPlus (sem modificações)",
}
LABEL2MODULE = {label: module for module, label in MODULE_LABELS.items()}

#: O que cada módulo faz, mostrado abaixo do seletor.
MODULE_DESCRIPTIONS = {
    ModuleType.COMPLETE:
        "O controlador abre a janela, liga o ventilador e o ar-condicionado "
        "conforme o PMV e o modelo adaptativo.",
    ModuleType.WITHOUT_FAN:
        "Igual ao completo, mas sem ventilador: só janela e ar-condicionado.",
    ModuleType.FIXED_AC_WITHOUT_FAN:
        "Janela controlada; o ar-condicionado usa sempre o setpoint fixo da "
        "faixa de temperatura, sem ventilador.",
    ModuleType.CLOSED_WINDOW:
        "A janela fica sempre fechada; ventilador e ar-condicionado "
        "fazem o controle.",
    ModuleType.ENERGYPLUS_ONLY:
        "Roda o IDF como está, sem controle e sem planilhas do Ambiens.",
}

#: Ajuda curta dos campos técnicos (balão ao passar o mouse ou focar).
HELP = {
    "pmv": "Faixa de PMV (-3 frio a +3 quente) considerada conforto. Fora dela o "
           "controlador aciona janela, ventilador ou ar-condicionado.",
    "bound": "Folga somada às duas pontas da faixa de PMV nas decisões do "
             "controlador (padrão 0,2). Não é a faixa do índice.",
    "adaptative": "Aceitação do modelo adaptativo (ASHRAE 55): 90% = banda de "
                  "±2,5 °C; 80% = ±3,5 °C em torno da temperatura neutra.",
    "met": "Taxa metabólica do ocupante (1,0 met = sentado, em repouso).",
    "wme": "Wme: trabalho mecânico externo, em W/m². Use 0 para atividade "
           "de escritório.",
    "clo": "Isolamento térmico da roupa. O controlador varia o clo dentro "
           "desta faixa; o valor inicial é o mínimo.",
    "clo_delta": "Quanto o clo muda a cada ajuste do controlador (clo).",
    "ac": "Setpoints do ar-condicionado: o mínimo aquece, o máximo resfria.",
}

#: Banda adaptativa usada quando a configuração não traz uma válida — a mesma
#: do `SimulationConfig`. O 0.8 que estava aqui era resquício de quando o campo
#: guardava a fração de aceitação, e virava uma semibanda de 0,8 °C.
DEFAULT_ADAPTATIVE = 2.5


def _invalid_style():
    """Estilo de campo com valor recusado (herda o `Field.TEntry`)."""
    ttk.Style().configure("Invalid.Field.TEntry", foreground=COLORS["danger"])
    return "Invalid.Field.TEntry"


def _mark(entry, bad: bool):
    """Texto vermelho e, pelo estado `invalid` do sv_ttk, borda vermelha;
    só a cor do texto não aparecia num campo vazio."""
    entry.configure(style=_invalid_style() if bad else "Field.TEntry")
    entry.state(["invalid" if bad else "!invalid"])


# Campos que não aceitam negativo (velocidade −2 passava) e Met, que nem zero.
_NON_NEGATIVE = ("max_vel", "wme", "pmv_comfort_bound", "co2_limit",
                 "air_speed_delta", "clo_delta")


class StrictRangeField(RangeField):
    """`RangeField` que não corrige o que o usuário digitou.

    O campo base limita ao domínio e troca mínimo e máximo trocados; aqui o
    valor inválido fica na tela, em vermelho, e `read_strict` o recusa.
    """

    def __init__(self, parent, label: str, *args, **kwargs):
        super().__init__(parent, label, *args, **kwargs)
        self.label = label
        self.error = ttk.Label(self, text="", style="Caption.TLabel",
                               foreground=COLORS["danger"])
        self.error.grid(row=2, column=0, columnspan=3, sticky="w")
        self.error.grid_remove()

    def read_strict(self) -> tuple:
        """(mínimo, máximo) como digitados; `ValueError` se não forem números."""
        try:
            return (parse_num(self.min_entry.get()),
                    parse_num(self.max_entry.get()))
        except ValueError:
            raise ValueError(f"{self.label}: preencha mínimo e máximo "
                             "com números") from None

    def flag(self, message: str = ""):
        """Marca (ou, sem mensagem, limpa) o erro do campo."""
        for entry in (self.min_entry, self.max_entry):
            _mark(entry, bool(message))
        self._redraw()
        self.error.configure(text=message)
        if message:
            self.error.grid()
        else:
            self.error.grid_remove()

    def _commit(self, _event=None):
        try:
            low, high = self.read_strict()
            if not self._lower <= low <= high <= self._upper:
                raise ValueError(
                    f"{self.label}: use mínimo ≤ máximo, entre "
                    f"{fmt_num(self._lower)} e {fmt_num(self._upper)}")
        except ValueError as error:
            self.flag(str(error))
            if self._on_change:
                self._on_change()
            return
        self.flag()
        super()._commit()

    def _redraw(self):
        """Com valor recusado, só o trilho: desenhar o valor corrigido (−3, ou
        a faixa invertida) dizia que ele seria usado."""
        if self._track.winfo_width() < 8 or not hasattr(self, "label"):
            return super()._redraw()
        try:
            low, high = self.read_strict()
            valid = self._lower <= low <= high <= self._upper
        except ValueError:
            valid = False
        if valid:
            return super()._redraw()
        left, right = self._span()
        middle = self._TRACK_HEIGHT / 2
        self._track.delete("all")
        self._track.create_line(left, middle, right, middle,
                                fill=COLORS["danger"], width=2, dash=(4, 3))


class KeyboardChipSelect(ChipSelect):
    """`ChipSelect` cujos chips recebem foco: Delete ou BackSpace remove."""

    def _render_chips(self):
        super()._render_chips()
        for chip, value in zip(self._chips.winfo_children(), self._values):
            close = chip.winfo_children()[-1]
            close.configure(takefocus=True, highlightthickness=1,
                            highlightcolor=COLORS["primary"])
            for key in ("<Delete>", "<BackSpace>", "<Return>", "<space>"):
                close.bind(key, lambda e, v=value: self._remove(v))
            close.bind("<FocusIn>", lambda e: e.widget.configure(
                fg=COLORS["danger"]))
            close.bind("<FocusOut>", lambda e: e.widget.configure(
                fg=COLORS["text_mute"]))


class SimulationConfigCallback(Protocol):
    """Protocol for simulation configuration callbacks."""
    
    def on_simulation_config_changed(self) -> None:
        """Called when simulation configuration is changed."""
        ...


class SimulationConfigPanel(ttk.Frame):
    """Panel for simulation configuration."""
    
    def __init__(self, parent, callback: Optional[SimulationConfigCallback] = None):
        super().__init__(parent, style="Surface.TFrame")
        self.callback = callback
        # Fontes de configuração que vivem em outras abas (período do IDF,
        # validação dos arquivos): (ler, aplicar). Ver `register_extra`.
        self._extras = []
        self._build_ui()

    def register_extra(self, read, apply=None):
        """Painel irmão contribui com campos: `read()` devolve um dict (ou
        levanta `ValueError`) e `apply(config)` recebe a configuração lida."""
        self._extras.append((read, apply))
    
    def _build_ui(self):
        """Build the UI components: one notebook tab per logical group."""
        self.notebook = ttk.Notebook(self, style="Section.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self._build_comfort_tab()
        self._build_equipment_tab()
        self._build_rooms_module_tab()

    def insert_tab(self, index: int, widget, title: str,
                   select: bool = True, icon_name: Optional[str] = None) -> None:
        """Insert an externally built frame as a tab of this panel.

        ``select=False`` allows complementary tabs to be mounted without
        taking focus from the initial configuration tab.
        """
        self.notebook.insert(index, widget, **self._tab_label(title, icon_name))
        if select:
            self.notebook.select(index)

    def _tab_label(self, title: str, icon_name: Optional[str]) -> dict:
        """Texto e ícone da aba, no mesmo padrão do IDFEditorPanel."""
        image = icon(icon_name, 16, COLORS["text_mute"], master=self) \
            if icon_name else None
        if image is None:
            return {"text": title}
        return {"text": f" {title}", "image": image, "compound": "left"}

    def _tab(self, title: str, icon_name: Optional[str] = None) -> ttk.Frame:
        """Create a notebook tab whose four columns share the width."""
        frame = ttk.Frame(self.notebook, style="Surface.TFrame",
                          padding=SPACE[3])
        self.notebook.add(frame, **self._tab_label(title, icon_name))
        for column in range(4):
            frame.columnconfigure(column, weight=1, uniform="fields")
        return frame

    def _range(self, parent, row: int, column: int, label: str, lower: float,
               upper: float, step: float = 0.1,
               help: str = "") -> StrictRangeField:
        """Create a min-max range spanning two columns of `parent`."""
        field = StrictRangeField(parent, label, lower, upper, step,
                                 on_change=self._on_config_changed)
        field.grid(row=row, column=column, columnspan=2, rowspan=2,
                   padx=SPACE[1], pady=(0, SPACE[2]), sticky="ew")
        if help:
            Tooltip(field, help)
            for widget in field.winfo_children():
                Tooltip(widget, help)
        return field

    def _field(self, parent, row: int, column: int, label: str,
               help: str = "") -> ttk.Entry:
        """Create a labeled entry in `column` of `parent`."""
        caption = ttk.Label(parent, text=label, style="Label.TLabel")
        caption.grid(row=row, column=column, padx=SPACE[1],
                     pady=(0, SPACE[1]), sticky="ew")
        # Rótulo longo quebra em duas linhas em vez de ser cortado.
        caption.bind("<Configure>",
                     lambda e: e.widget.configure(wraplength=e.width))
        entry = ttk.Entry(parent, style="Field.TEntry")
        entry.grid(row=row + 1, column=column, padx=SPACE[1],
                   pady=(0, SPACE[2]), sticky="ew")
        entry.bind('<FocusOut>', self._on_entry_left)
        entry.label = label  # nomeia o campo no erro de get_configuration
        if help:
            Tooltip(caption, help)
            Tooltip(entry, help)
        return entry

    def _on_entry_left(self, event=None):
        """Marca em vermelho o campo numérico que não é um número."""
        entry = event.widget
        try:
            parse_num(entry.get())
            _mark(entry, False)
        except ValueError:
            _mark(entry, True)
        self._on_config_changed()

    def _combo(self, parent, row: int, column: int, label: str, values,
               variable: tk.StringVar, columnspan: int = 1,
               help: str = "") -> ttk.Combobox:
        """Create a labeled read-only combobox in `column` of `parent`."""
        caption = ttk.Label(parent, text=label, style="Label.TLabel")
        caption.grid(row=row, column=column, columnspan=columnspan,
                     padx=SPACE[1], pady=(0, SPACE[1]), sticky="w")
        if help:
            Tooltip(caption, help)
        combo = ttk.Combobox(parent, textvariable=variable,
                             style="Field.TCombobox", state="readonly",
                             values=values)
        combo.grid(row=row + 1, column=column, columnspan=columnspan,
                   padx=SPACE[1], pady=(0, SPACE[2]), sticky="ew")
        combo.bind('<<ComboboxSelected>>', self._on_config_changed)
        return combo

    def _build_comfort_tab(self):
        """Um bloco por assunto: índice, ocupante e vestimenta."""
        tab = self._scroll_tab("Conforto", "armchair")

        # O módulo decide o que o controlador faz: fica na primeira aba.
        module = self._section(tab, "Módulo de condicionamento")
        self.selected_module = tk.StringVar()
        self.cbx_module = self._combo(module, 0, 0, "Módulo",
                                      list(MODULE_LABELS.values()),
                                      self.selected_module, columnspan=4)
        self.module_help = ttk.Label(module, text="", style="Caption.TLabel",
                                     wraplength=520, justify="left")
        self.module_help.grid(row=2, column=0, columnspan=4, padx=SPACE[1],
                              sticky="w")
        self.cbx_module.bind('<<ComboboxSelected>>', self._on_module_changed)

        index = self._section(tab, "Índice de conforto")
        # Faixa do PMV na escala ASHRAE (-3 frio … +3 quente).
        self.pmv_range = self._range(index, 0, 0, "Faixa de PMV", -3.0, 3.0,
                                help=HELP["pmv"])
        self.comfort_bound_entry = self._field(index, 0, 2,
                                               "Banda de conforto",
                                               help=HELP["bound"])
        self.selected_adaptative = tk.StringVar()
        self.cbx_adaptative = self._combo(index, 0, 3,
                                          "Margem do adaptativo",
                                          ("80%", "90%"),
                                          self.selected_adaptative,
                                          help=HELP["adaptative"])

        occupant = self._section(tab, "Ocupante")
        self.met_entry = self._field(occupant, 0, 0, "Met (met)",
                                    help=HELP["met"])
        self.wme_entry = self._field(occupant, 0, 1, "Wme (W/m²)",
                                     help=HELP["wme"])

        clothing = self._section(tab, "Vestimenta")
        self.clo_range = self._range(clothing, 0, 0, "Faixa de Clo (clo)",
                                     0.0, 2.0, 0.05, help=HELP["clo"])
        self.clo_delta_entry = self._field(clothing, 0, 2,
                                           "Variação do Clo (clo)",
                                           help=HELP["clo_delta"])

        # Prioridade do Clo sobre os equipamentos
        self.clo_priority_var = tk.BooleanVar(value=True)
        self.clo_priority_check = ttk.Checkbutton(
            clothing, text="Ajustar Clo antes dos equipamentos",
            variable=self.clo_priority_var, command=self._on_config_changed,
            style="Card.TCheckbutton")
        # Linha própria: ao lado da variação, a 125% o texto saía cortado.
        self.clo_priority_check.grid(row=2, column=0, columnspan=4, padx=SPACE[1],
                                     pady=(0, SPACE[2]), sticky="w")

    def _scroll_tab(self, title: str, icon_name: str) -> ttk.Frame:
        """Aba rolável: em tela pequena ou com zoom, as seções não ficam com
        altura zero."""
        tab = ttk.Frame(self.notebook, style="Surface.TFrame",
                        padding=SPACE[3])
        self.notebook.add(tab, **self._tab_label(title, icon_name))
        return scrollable(tab)

    def _on_module_changed(self, event=None):
        module = LABEL2MODULE.get(self.selected_module.get())
        self.module_help.configure(text=MODULE_DESCRIPTIONS.get(module, ""))
        self._on_config_changed()

    def _section(self, parent, title: str) -> ttk.Labelframe:
        """Create a titled section that stacks vertically inside a tab."""
        section = ttk.Labelframe(parent, text=title, style="Section.TLabelframe",
                                 padding=SPACE[2])
        section.pack(fill="x", pady=(0, SPACE[2]))
        for column in range(4):
            section.columnconfigure(column, weight=1, uniform="fields")
        return section

    def _build_equipment_tab(self):
        """Um bloco por equipamento: fica claro o que cada ajuste comanda."""
        tab = self._scroll_tab("Equipamentos", "fan")

        ac = self._section(tab, "Ar-condicionado")
        self.temp_ac_range = self._range(
            ac, 0, 0, "Faixa de temperatura do AC (°C)", 10.0, 35.0, 0.5,
            help=HELP["ac"])

        fan = self._section(tab, "Ventilador")
        self.vel_max_entry = self._field(fan, 0, 0, "Velocidade máxima (m/s)",
                                    help="Maior velocidade do ar que o "
                                         "ventilador pode atingir.")
        self.air_speed_delta_entry = self._field(
            fan, 0, 1, "Variação da vel. de ventilação (m/s)",
            help="Quanto a velocidade do ar muda a cada ajuste do ventilador.")

        window = self._section(tab, "Janela")
        self.temp_open_window_bound_entry = self._field(
            window, 0, 0, "Margem de temp. p/ abrir janela (°C)",
            help="A janela só abre se a temperatura externa não estiver mais "
                 "que esta margem abaixo da interna.")
        self.co2_limit_entry = self._field(
            window, 0, 1, "Limite de CO2 (ppm)",
            help="Acima deste CO2 a janela abre para renovar o ar.")

    def _build_rooms_module_tab(self):
        """Zonas simuladas e módulo de condicionamento."""
        tab = self._tab("Zonas", "layout-grid")

        ttk.Label(tab, text="Salas", style="Label.TLabel").grid(
            row=0, column=0, columnspan=4, padx=SPACE[1],
            pady=(0, SPACE[1]), sticky="w")
        self.rooms_select = KeyboardChipSelect(
            tab, "Selecione uma sala…", on_change=self._on_config_changed)
        self.rooms_select.grid(row=1, column=0, columnspan=4, padx=SPACE[1],
                               pady=(0, SPACE[2]), sticky="new")

    def set_room_options(self, rooms):
        """Zonas oferecidas no seletor de salas (lidas do IDF escolhido)."""
        self.rooms_select.set_options(rooms)

    def _on_config_changed(self, event=None):
        """Handle configuration change."""
        if self.callback:
            self.callback.on_simulation_config_changed()
    
    def get_configuration(self) -> dict:
        """Configuração da tela. Campo inválido (texto que não é número,
        valor fora da escala, mínimo acima do máximo) levanta `ValueError` com
        o rótulo do campo e o marca em vermelho; nada é corrigido em silêncio."""
        # Junta todos os erros: um por vez obrigava a uma volta por campo.
        problems = []

        def num(entry):
            try:
                value = parse_num(entry.get())
            except ValueError:
                _mark(entry, True)
                text = entry.get().strip()
                problems.append(f"{entry.label}: “{text}” não é um número."
                                if text else f"{entry.label}: preencha o valor.")
                return None
            _mark(entry, False)
            return value

        # (campo, mínimo, máximo, par em config.RANGE_PAIRS)
        ranges = ((self.pmv_range, "pmv_lowerbound", "pmv_upperbound"),
                  (self.clo_range, "clo_min", "clo_max"),
                  (self.temp_ac_range, "temp_ac_min", "temp_ac_max"))
        values = {}
        for field, low, high in ranges:
            try:
                values[low], values[high] = field.read_strict()
            except ValueError as error:
                field.flag(str(error))
                problems.append(str(error))
                values[low] = values[high] = None

        config = {
            'pmv_lowerbound': values["pmv_lowerbound"],
            'pmv_upperbound': values["pmv_upperbound"],
            'max_vel': num(self.vel_max_entry),
            'adaptative_bound': PORCENT2ADAPTATIVE.get(
                self.selected_adaptative.get(), DEFAULT_ADAPTATIVE),
            'temp_ac_min': values["temp_ac_min"],
            'temp_ac_max': values["temp_ac_max"],
            'met': num(self.met_entry),
            'wme': num(self.wme_entry),
            'pmv_comfort_bound': num(self.comfort_bound_entry),
            'co2_limit': num(self.co2_limit_entry),
            'air_speed_delta': num(self.air_speed_delta_entry),
            'temp_open_window_bound': num(self.temp_open_window_bound_entry),
            'clo_min': values["clo_min"],
            'clo_max': values["clo_max"],
            'clo_delta': num(self.clo_delta_entry),
            'clo_priority': bool(self.clo_priority_var.get()),
            'rooms': self.rooms_select.get_values(),
            'module_type': LABEL2MODULE.get(self.selected_module.get()),
        }

        entries = {'max_vel': self.vel_max_entry, 'met': self.met_entry,
                   'wme': self.wme_entry, 'pmv_comfort_bound': self.comfort_bound_entry,
                   'co2_limit': self.co2_limit_entry,
                   'air_speed_delta': self.air_speed_delta_entry,
                   'clo_delta': self.clo_delta_entry}
        for key, entry in entries.items():
            value = config[key]
            if value is None:
                continue
            if (value <= 0) if key == "met" else (key in _NON_NEGATIVE and value < 0):
                _mark(entry, True)
                problems.append(f"{entry.label}: use um valor "
                                f"{'maior que zero' if key == 'met' else 'a partir de zero'}.")
        for field, low, high in ranges:
            if values[low] is None:
                continue
            found = range_problems(config, [p for p in RANGE_PAIRS
                                            if p[0] == low])
            field.flag(" ".join(found))
            problems += found
        if problems:
            raise ValueError(" ".join(problems))

        for read, _apply in self._extras:
            config.update(read())
        return config

    def set_configuration(self, config: dict):
        """Set configuration from dictionary."""
        self.pmv_range.set(config.get('pmv_lowerbound', -0.5),
                           config.get('pmv_upperbound', 0.5))
        
        self.vel_max_entry.delete(0, tk.END)
        self.vel_max_entry.insert(0, fmt_num(config.get('max_vel')))
        
        self.selected_adaptative.set(
            ADAPTATIVE2PORCENT.get(config.get('adaptative_bound'),
                                   ADAPTATIVE2PORCENT[DEFAULT_ADAPTATIVE]))
        
        self.temp_ac_range.set(config.get('temp_ac_min', 16.0),
                               config.get('temp_ac_max', 30.0))
        
        self.met_entry.delete(0, tk.END)
        self.met_entry.insert(0, fmt_num(config.get('met')))
        
        self.wme_entry.delete(0, tk.END)
        self.wme_entry.insert(0, fmt_num(config.get('wme')))
        
        self.comfort_bound_entry.delete(0, tk.END)
        self.comfort_bound_entry.insert(0, fmt_num(config.get('pmv_comfort_bound')))
        
        self.co2_limit_entry.delete(0, tk.END)
        self.co2_limit_entry.insert(0, fmt_num(config.get('co2_limit')))
        
        self.air_speed_delta_entry.delete(0, tk.END)
        self.air_speed_delta_entry.insert(0, fmt_num(config.get('air_speed_delta')))
        
        self.temp_open_window_bound_entry.delete(0, tk.END)
        self.temp_open_window_bound_entry.insert(0, fmt_num(config.get('temp_open_window_bound')))
        
        self.clo_range.set(config.get('clo_min', 0.5),
                           config.get('clo_max', 1.0))
        
        self.clo_delta_entry.delete(0, tk.END)
        self.clo_delta_entry.insert(0, fmt_num(config.get('clo_delta')))

        self.clo_priority_var.set(bool(config.get('clo_priority', True)))
        
        self.rooms_select.set_values(config.get('rooms', []))
        
        module_type = config.get('module_type')
        if module_type:
            module_type = ModuleType(str(module_type))
            self.selected_module.set(MODULE_LABELS.get(module_type, ''))
        self.module_help.configure(
            text=MODULE_DESCRIPTIONS.get(module_type if module_type else None, ""))

        for _read, apply in self._extras:
            if apply:
                apply(config)
