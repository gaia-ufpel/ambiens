"""Página da etiquetagem INI-C: só o que a envoltória pela simulação usa.

Módulo, conforto e equipamentos da execução comum não entram: a etiquetagem
troca o condicionamento e a ocupação pelos da norma (ver
`confortimetro.etiquetagem`).
"""

import tkinter as tk
from tkinter import ttk
from typing import Optional

from confortimetro.etiquetagem import modelos, norma
from confortimetro.idf import read_zone_names

from ..theme import SPACE, Tooltip, scrollable
from .path_config_panel import SIMULATION_FIELDS, PathConfigPanel
from .simulation_config_panel import KeyboardChipSelect

MODES = {"condicionado": "Condicionado",
         "hibrido": "Híbrido (ventilação natural)"}
ZB_AUTO = "Automática (pelo EPW)"
TYPOLOGIES = {dados["nome"]: chave for chave, dados in norma.TIPOLOGIAS.items()}


def zb_label(zb: int) -> str:
    """"2 — Frio (ex.: Pelotas/RS)": o número e o que ele quer dizer."""
    clima, exemplos = norma.ZONAS[zb]
    return f"{zb} — {clima} (ex.: {exemplos})"


class LabelingPanel(ttk.Frame):
    """Arquivos, APP e opções da INI-C. `get_options` e `set_options` usam o
    formato de `EtiquetagemRun` (`tipologia`, `uso`, `modo`, `zb`, `fachada`)."""

    def __init__(self, parent, on_idf_changed=None):
        super().__init__(parent, style="Surface.TFrame", padding=SPACE[3])
        self._on_idf_changed = on_idf_changed
        body = scrollable(self)

        files = self._section(body, "Arquivos")
        self.path_panel = PathConfigPanel(files, callback=self, fields=SIMULATION_FIELDS)
        self.path_panel.grid(row=0, column=0, columnspan=4, sticky="ew")

        zones = self._section(body, "Zonas de permanência prolongada (APP)")
        self.rooms_select = KeyboardChipSelect(zones, "Selecione uma zona…")
        self.rooms_select.grid(row=0, column=0, columnspan=4, padx=SPACE[1],
                               pady=(0, SPACE[1]), sticky="ew")
        ttk.Label(zones, style="Caption.TLabel", wraplength=520, justify="left",
                  text="As zonas escolhidas são avaliadas; as demais entram como "
                       "APT (sem condicionamento).").grid(
            row=1, column=0, columnspan=4, padx=SPACE[1], sticky="w")

        section = self._section(body, "Norma")
        self.typology = tk.StringVar(value=norma.TIPOLOGIAS["educacional"]["nome"])
        self._combo(section, 0, 0, "Tipologia", list(TYPOLOGIES), self.typology)
        self.use = tk.StringVar()
        self.cbx_use = self._combo(section, 0, 2, "Uso", (), self.use,
                                   help="Dá a densidade de ocupação das APP (Tabela A.1).")
        self.mode = tk.StringVar(value=MODES["condicionado"])
        self._combo(section, 2, 0, "Modo", list(MODES.values()), self.mode,
                    help="Híbrido: simula também o modelo real com as janelas "
                         "abrindo e desconta as horas em conforto (PHOCT).")
        self.zb = tk.StringVar(value=ZB_AUTO)
        self._combo(section, 2, 2, "Zona bioclimática",
                    [ZB_AUTO] + [zb_label(zb) for zb in range(1, 9)], self.zb,
                    help="Automática: a do município mais próximo do local do EPW.")
        self.facade = tk.StringVar(value="N")
        self.cbx_facade = self._combo(
            section, 4, 0, "Fachada principal", list(modelos.ORIENTACOES), self.facade,
            help="Só no varejo e no mercado: as paredes voltadas a até 45° dela "
                 "recebem o PAF da fachada principal (Tabelas A.5 e A.6).")
        ttk.Label(section, style="Caption.TLabel", wraplength=520, justify="left",
                  text="Gera os modelos real e de referência (e, no híbrido, o real "
                       "ventilado) e simula cada um o ano inteiro no módulo somente "
                       "EnergyPlus. Ao terminar, o grupo abre na comparação com a "
                       "classe. " + norma.AVISO_ESTIMATIVA).grid(
            row=6, column=0, columnspan=4, padx=SPACE[1], sticky="w")
        self._on_typology_changed()

    # ------------------------------------------------------------ layout

    def _section(self, parent, title: str) -> ttk.Labelframe:
        section = ttk.Labelframe(parent, text=title, style="Section.TLabelframe",
                                 padding=SPACE[2])
        section.pack(fill="x", pady=(0, SPACE[2]))
        for column in range(4):
            section.columnconfigure(column, weight=1, uniform="fields")
        return section

    def _combo(self, parent, row, column, label, values, variable, help=""):
        caption = ttk.Label(parent, text=label, style="Label.TLabel")
        caption.grid(row=row, column=column, columnspan=2, padx=SPACE[1],
                     pady=(0, SPACE[1]), sticky="w")
        combo = ttk.Combobox(parent, textvariable=variable, style="Field.TCombobox",
                             state="readonly", values=values)
        combo.grid(row=row + 1, column=column, columnspan=2, padx=SPACE[1],
                   pady=(0, SPACE[2]), sticky="ew")
        if help:
            Tooltip(caption, help)
            Tooltip(combo, help)
        combo.bind("<<ComboboxSelected>>", self._on_typology_changed)
        return combo

    def _on_typology_changed(self, _event=None):
        tipo = norma.TIPOLOGIAS[TYPOLOGIES[self.typology.get()]]
        usos = list(tipo["ocupacao"])
        self.cbx_use.configure(values=usos)
        if self.use.get() not in usos:
            self.use.set(usos[-1])
        self.cbx_facade.configure(
            state="readonly" if isinstance(tipo["paf"], tuple) else "disabled")

    # ------------------------------------------- callback do PathConfigPanel

    def on_idf_path_changed(self, path: str):
        self.rooms_select.set_options(read_zone_names(path))
        if self._on_idf_changed:
            self._on_idf_changed(path)

    def on_epw_path_changed(self, path: str):
        pass

    # ------------------------------------------------------------- dados

    def get_options(self) -> dict:
        tipologia = TYPOLOGIES[self.typology.get()]
        zb = self.zb.get()
        opcoes = {"tipologia": tipologia, "uso": self.use.get(),
                  "modo": next(k for k, v in MODES.items() if v == self.mode.get()),
                  "zb": None if zb == ZB_AUTO else int(zb.split()[0])}
        if isinstance(norma.TIPOLOGIAS[tipologia]["paf"], tuple):
            opcoes["fachada"] = self.facade.get()
        return opcoes

    def set_options(self, opcoes: Optional[dict]):
        if not opcoes:
            return
        self.typology.set(norma.TIPOLOGIAS[opcoes["tipologia"]]["nome"])
        self._on_typology_changed()
        if opcoes.get("uso") in norma.TIPOLOGIAS[opcoes["tipologia"]]["ocupacao"]:
            self.use.set(opcoes["uso"])
        self.mode.set(MODES[opcoes.get("modo", "condicionado")])
        self.zb.set(zb_label(int(opcoes["zb"])) if opcoes.get("zb") else ZB_AUTO)
        self.facade.set(opcoes.get("fachada") or "N")

    def get_files(self):
        """`(idf, epw)` como digitados."""
        return self.path_panel.get_idf_path().strip(), self.path_panel.get_epw_path().strip()

    def set_files(self, idf: str, epw: str):
        self.path_panel.set_idf_path(idf or "")
        self.path_panel.set_epw_path(epw or "")
        self.rooms_select.set_options(read_zone_names(idf) if idf else [])

    def get_rooms(self) -> list:
        return self.rooms_select.get_values()

    def set_rooms(self, rooms):
        self.rooms_select.set_values(list(rooms or []))
