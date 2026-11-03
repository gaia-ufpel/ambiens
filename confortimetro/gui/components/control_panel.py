"""
Control panel component.
"""

import time
import tkinter as tk
from tkinter import ttk
from typing import Protocol, Optional

from ..theme import SPACE, RoundedButton, StatusPill, Tooltip


class ControlPanelCallback(Protocol):
    """Protocol for control panel callbacks."""
    
    def on_run_simulation(self) -> None:
        """Called when run simulation is requested."""
        ...
    
    def on_stop_simulation(self) -> None:
        """Called when stop simulation is requested."""
        ...
    
    def on_save_config(self) -> None:
        """Called when save configuration is requested."""
        ...
    
    def on_load_config(self) -> None:
        """Called when load configuration is requested."""
        ...

    def on_edit_idf(self) -> None:
        """Called when editing the IDF fields is requested."""
        ...


class ControlPanel(ttk.Frame):
    """Panel for simulation controls."""
    
    def __init__(self, parent, callback: Optional[ControlPanelCallback] = None,
                 file_actions: bool = True):
        super().__init__(parent, style="Surface.TFrame")
        self.callback = callback
        self.file_actions = file_actions
        self.is_running = False
        self._build_ui()
    
    def _build_ui(self):
        """Build the topbar row: actions on the left, status on the right."""
        row = ttk.Frame(self, style="Surface.TFrame")
        row.pack(fill="x")

        self.run_button = RoundedButton(
            row, text="Validar e executar", variant="primary", icon="play",
            command=self._on_run_clicked)
        self.run_button.pack(side="left")

        self.save_button = RoundedButton(
            row, text="Salvar", variant="ghost", icon="save",
            command=self._on_save_clicked)
        # Salvar, Carregar e Editar IDF são da execução comum; a etiquetagem
        # usa só executar, nome e status.
        if self.file_actions:
            self.save_button.pack(side="left", padx=(SPACE[2], 0))

        self.load_button = RoundedButton(
            row, text="Carregar", variant="ghost", icon="open",
            command=self._on_load_clicked)
        if self.file_actions:
            self.load_button.pack(side="left", padx=(SPACE[2], 0))

        self.edit_idf_button = RoundedButton(
            row, text="Editar IDF", variant="ghost", icon="edit",
            command=self._on_edit_idf_clicked)
        if self.file_actions:
            self.edit_idf_button.pack(side="left", padx=(SPACE[2], 0))

        # Sem nome, a execução aparece como "IDF · dd/mm hh:mm".
        ttk.Label(row, text="Nome", style="Label.TLabel").pack(
            side="left", padx=(SPACE[4], SPACE[1]))
        self.name_entry = ttk.Entry(row, style="Field.TEntry", width=28)
        self.name_entry.pack(side="left")
        Tooltip(self.name_entry, "Opcional. Aparece na listagem e na comparação "
                "no lugar da data e hora.")

        self.status_pill = StatusPill(row, "Pronto para executar", "info")
        self.status_pill.pack(side="right")

        # Só aparece durante a execução — barra parada não informa nada.
        self.progress_bar = ttk.Progressbar(
            self, mode="indeterminate",
            style="Modern.Horizontal.TProgressbar")
        # Porcentagem e tempo restante estimado, junto da barra.
        self.progress_label = ttk.Label(self, text="", style="Caption.TLabel")
        self._progress_start = None

    def _on_run_clicked(self):
        """Handle run button click."""
        if self.is_running:
            if self.callback:
                self.callback.on_stop_simulation()
        else:
            if self.callback:
                self.callback.on_run_simulation()
    
    def _on_save_clicked(self):
        """Handle save button click."""
        if self.callback:
            self.callback.on_save_config()
    
    def _on_load_clicked(self):
        """Handle load button click."""
        if self.callback:
            self.callback.on_load_config()
    
    def _on_edit_idf_clicked(self):
        """Handle edit IDF button click."""
        if self.callback:
            self.callback.on_edit_idf()

    def set_running_state(self, is_running: bool):
        """
        Set the running state of the simulation with visual feedback.

        Args:
            is_running: Whether simulation is currently running
        """
        self.is_running = is_running

        if is_running:
            self.run_button.configure(text="Parar simulação", variant="danger",
                                      icon="stop")
            self.save_button.configure(state="disabled")
            self.load_button.configure(state="disabled")
            self.edit_idf_button.configure(state="disabled")
            self.name_entry.state(["disabled"])
            # Volta ao indeterminado: a etapa de preparo não reporta
            # percentual, só o EnergyPlus reporta.
            self.progress_bar.configure(mode="indeterminate", value=0)
            self.progress_bar.pack(fill="x", pady=(SPACE[3], 0))
            self.progress_label.configure(text="Preparando a simulação…")
            self.progress_label.pack(anchor="w", pady=(SPACE[1], 0))
            self._progress_start = None
            self.progress_bar.start(10)
            self.set_status("Executando simulação...", "running")
        else:
            self.run_button.configure(text="Validar e executar",
                                      variant="primary", icon="play")
            self.save_button.configure(state="normal")
            self.load_button.configure(state="normal")
            self.edit_idf_button.configure(state="normal")
            self.name_entry.state(["!disabled"])
            self.progress_bar.stop()
            self.progress_bar.pack_forget()
            self.progress_label.pack_forget()
            self.set_status("Pronto para executar", "info")

    def set_progress(self, percent: float):
        """Progresso real do EnergyPlus. Troca a barra de indeterminada para
        determinada no primeiro percentual que chega."""
        if str(self.progress_bar.cget("mode")) != "determinate":
            self.progress_bar.stop()
            self.progress_bar.configure(mode="determinate", maximum=100)
        percent = max(0, min(100, percent))
        self.progress_bar.configure(value=percent)
        self.progress_label.configure(text=self._progress_text(percent))

    def _progress_text(self, percent: float) -> str:
        """`37 % concluído · restam ~12 min`, estimado pelo ritmo até agora."""
        now = time.monotonic()
        if self._progress_start is None:
            self._progress_start = (now, percent)
        start_time, start_percent = self._progress_start
        text = f"{percent:.0f} % concluído"
        if percent > start_percent and now - start_time >= 5:
            seconds = (now - start_time) * (100 - percent) / (percent - start_percent)
            if seconds >= 3600:
                text += f" · restam ~{seconds / 3600:.1f} h"
            else:
                text += f" · restam ~{max(1, round(seconds / 60))} min"
        return text

    def set_status(self, status: str, status_type: str = "info"):
        """
        Set the status message.

        Args:
            status: Status message to display
            status_type: 'info', 'success', 'warning', 'error' or 'running'
        """
        self.status_pill.set(status, status_type)

    def get_name(self) -> Optional[str]:
        """Nome digitado, ou None se vazio."""
        return self.name_entry.get().strip() or None

    def set_name(self, name: Optional[str]):
        self.name_entry.delete(0, tk.END)
        self.name_entry.insert(0, name or "")

    def get_is_running(self) -> bool:
        """Check if simulation is running."""
        return self.is_running
