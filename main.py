"""
Ambiens - Simulações de Conforto Térmico com EnergyPlus e Python

Ponto de entrada principal da aplicação.
"""

import json
import multiprocessing
import os
import sys

from confortimetro.paths import app_data_path


# Importado em main(), depois do aviso de abertura (os imports pesados levam
# dezenas de segundos); fica aqui para os testes poderem substituí-lo.
MainWindow = None


def _splash():
    """Janelinha 'Abrindo…' enquanto os imports pesados rodam; None sem display."""
    import tkinter as tk
    try:
        root = tk.Tk()
    except tk.TclError:
        return None
    root.title("Ambiens")
    root.overrideredirect(True)
    tk.Label(root, text="Abrindo o Ambiens…\nPode levar alguns instantes.",
             padx=40, pady=24, font=("TkDefaultFont", 12)).pack()
    root.update_idletasks()
    x = (root.winfo_screenwidth() - root.winfo_reqwidth()) // 2
    y = (root.winfo_screenheight() - root.winfo_reqheight()) // 3
    root.geometry(f"+{x}+{y}")
    root.update()
    return root


def resolve_config_path() -> str:
    """
    Caminho do config.json a ser usado pela interface.

    Rodando do repositório: `config.json` na pasta de dados do app (semeado
    de `examples/config.json` pela janela).
    Rodando pelo executável (PyInstaller): uma cópia gravável em
    `%LOCALAPPDATA%\\Ambiens`, semeada na primeira execução com o
    config.json embutido no pacote (o diretório do executável pode ser
    somente leitura para o usuário).
    """
    if not getattr(sys, "frozen", False):
        # Em dados do app, nunca em examples/; a janela semeia do exemplo.
        return os.path.join(app_data_path(), "config.json")

    user_config = os.path.join(app_data_path(), "config.json")
    if not os.path.exists(user_config):
        os.makedirs(os.path.dirname(user_config), exist_ok=True)
        bundle_path = getattr(sys, "_MEIPASS")
        with open(os.path.join(bundle_path, "examples", "config.json"), encoding="utf-8") as reader:
            data = json.load(reader)

        # Os caminhos do config versionado são relativos ao repositório e de
        # Linux; no executável apontam para os exemplos embutidos.
        data["_idf_path"] = os.path.join(bundle_path, *data["_idf_path"].split("/")[1:])
        data["epw_path"] = os.path.join(bundle_path, *data["epw_path"].split("/")[1:])
        data["input_path"] = os.path.dirname(data["_idf_path"])
        # Saída e EnergyPlus vazios: caem nos padrões da plataforma
        # (paths.new_run_path e find_energy_path) ao carregar a configuração.
        data["runs_root_path"] = ""
        data["output_path"] = ""
        data["expanded_idf_path"] = ""
        data["energy_path"] = ""

        with open(user_config, "w", encoding="utf-8") as writer:
            json.dump(data, writer, indent=4)
    return user_config


def main():
    """
    Função principal da aplicação.

    Cria e executa a interface gráfica principal do Ambiens.
    """
    # No executável do Windows, o ProcessPoolExecutor de recompute_runs relança
    # este mesmo .exe para cada worker (spawn). Sem freeze_support o filho volta
    # a abrir a interface em vez de rodar a tarefa — janelas se multiplicando e
    # nenhuma estatística regerada. Tem que ser a primeira coisa do main().
    multiprocessing.freeze_support()

    global MainWindow
    splash = _splash() if MainWindow is None else None
    try:
        if MainWindow is None:
            from confortimetro.gui.main_window import MainWindow
        if splash is not None:
            splash.destroy()
        app = MainWindow(config_path=resolve_config_path())
        app.mainloop()

    except KeyboardInterrupt:
        print("\nAplicação interrompida pelo usuário.")
    except Exception as e:
        print(f"Erro ao executar a aplicação: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()
