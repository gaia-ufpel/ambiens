import subprocess
import sys
import os
import re
import platform
from queue import Queue
from importlib import import_module
import shutil
import logging
from time import perf_counter

from confortimetro.control import MODULES_MAPPER
from confortimetro.versao import code_version
from confortimetro.control.base import request_stop
from confortimetro.results.database import FAILED_MARKER, INTERRUPTED_MARKER
from confortimetro.results import (
    summary_rooms_results_from_eso,
    get_stats_from_simulation,
    split_target_period_excel,
)
from confortimetro.config import SimulationConfig
from confortimetro.module_type import ModuleType
from confortimetro.idf import IDFProcessor, read_run_period, read_timesteps_per_hour

EnergyPlusAPI = None

EXPAND_OBJECTS_APP = "ExpandObjects"

if platform.system() == "Windows":
    EXPAND_OBJECTS_APP = "ExpandObjects.exe"

class Simulation:
    """Classe principal para execução de simulações EnergyPlus."""
    
    def __init__(self, configs: SimulationConfig):
        self.conditioner: object | None = None
        self.stop_requested = False
        # Só vale pedir o stop à API enquanto o EnergyPlus está rodando.
        self._ep_running = False
        # Fila da GUI, para o callback de progresso do EnergyPlus.
        self._queue: Queue | None = None
        self.configs = configs
        self.logger = logging.getLogger("simulation")

        # Configurar EnergyPlus API
        sys.path.append(self.configs.energy_path)
        EnergyPlusAPI = import_module("pyenergyplus.api").EnergyPlusAPI

        self.ep_api = EnergyPlusAPI()
        self.state = self.ep_api.state_manager.new_state()
        
        # Inicializar processador de IDF
        self.idf_processor = IDFProcessor(self.configs)

    @property
    def passive(self) -> bool:
        """Módulo somente EnergyPlus: IDF intacto e nenhum callback registrado."""
        return self.configs.module_type == ModuleType.ENERGYPLUS_ONLY

    def run(self, q: Queue):
        """
        Executar simulação completa.
        
        Args:
            q: Queue para comunicação com interface gráfica
        """
        started = perf_counter()
        try:
            self._queue = q
            self.logger.info("Iniciando simulação")
            q.put("Iniciando simulação...")
            
            # Etapa 1: Definir módulo condicionador
            if self.passive:
                q.put("Módulo somente EnergyPlus: sem controlador nem edição do IDF")
            else:
                q.put("Configurando módulo condicionador...")
                self.conditioner = MODULES_MAPPER[self.configs.module_type](
                    ep_api=self.ep_api,
                    configs=self.configs
                )
            
            # Etapa 2: Preparar o diretório da execução e copiar o IDF para lá
            q.put("Preparando diretórios de saída...")
            self._prepare_output_directories()

            # Etapa 3: Processar o IDF (a cópia da execução, não o original)
            if not self.passive:
                q.put("Processando arquivo IDF...")
                self._timed("Processamento IDF", self._process_idf)

            # Etapa 4: Expandir objetos EnergyPlus
            q.put("Expandindo objetos EnergyPlus...")
            self._timed("ExpandObjects", self._expand_objects)

            self.configs.code_version = code_version()
            self.configs.to_json(os.path.join(self.configs.output_path, "configs.json"))
            
            # Etapa 5: Executar simulação EnergyPlus
            q.put("Executando simulação EnergyPlus...")
            self._timed("EnergyPlus", self._run_energyplus)

            if self.stop_requested:
                q.put("Simulação interrompida")
                return
            
            # Etapa 6: Processar resultados
            q.put("Processando resultados...")
            self._process_results(q)
            
            self.logger.info("Simulação concluída com sucesso")
            
        except Exception as e:
            error_msg = f"Erro durante simulação: {str(e)}"
            self.logger.error(error_msg)
            q.put(error_msg)
            if not self.stop_requested and os.path.isdir(self.configs.output_path or ""):
                with open(os.path.join(self.configs.output_path, FAILED_MARKER), "w", encoding="utf-8") as marker:
                    marker.write(str(e))
            raise
        finally:
            self._record_time("Total", perf_counter() - started)
            # Marca a parada pelo usuário: sem isso a lista só deduz
            # "interrompida" pela falta de planilhas.
            if self.stop_requested and os.path.isdir(self.configs.output_path or ""):
                open(os.path.join(self.configs.output_path, INTERRUPTED_MARKER), "w").close()

    def _record_time(self, stage, seconds):
        message = f"Tempo {stage}: {seconds:.2f} s"
        self.logger.info(message)
        if self._queue is not None:
            self._queue.put(message)
        print(message, flush=True)

    def _timed(self, stage, action, *args, **kwargs):
        started = perf_counter()
        try:
            return action(*args, **kwargs)
        finally:
            self._record_time(stage, perf_counter() - started)
    
    def _process_idf(self):
        """Processar arquivo IDF usando o IDFProcessor."""
        try:
            # Validar IDF antes do processamento
            validation_errors = self.idf_processor.validate_idf()
            if validation_errors:
                raise ValueError(f"Erros de validação do IDF: {validation_errors}")
            
            # Processar arquivo IDF
            success = self.idf_processor.process_idf()
            if not success:
                raise RuntimeError("Falha no processamento do arquivo IDF")
            
            self.logger.info("Arquivo IDF processado com sucesso")
            
        except Exception as e:
            self.logger.error(f"Erro no processamento do IDF: {e}")
            raise
    
    def _expand_objects(self):
        """Expandir objetos EnergyPlus dentro do diretório da execução."""
        try:
            # ExpandObjects lê in.idf e escreve expanded.idf no diretório em que
            # roda; rodar no diretório da execução é o que mantém os artefatos
            # dela juntos e permite execuções paralelas sobre o mesmo modelo.
            shutil.copy(self.configs.idf_path,
                        os.path.join(self.configs.output_path, "in.idf"))

            expand_cmd = [os.path.join(self.configs.energy_path, EXPAND_OBJECTS_APP)]
            result = subprocess.run(expand_cmd, cwd=self.configs.output_path,
                                  capture_output=True, text=True, timeout=300)
            
            if result.returncode != 0:
                raise RuntimeError(f"ExpandObjects falhou: {result.stderr}")
            # Sem nada para expandir (nenhum HVACTemplate nem GroundDomain), o
            # ExpandObjects não escreve o expanded.idf: o modelo já é o final.
            expanded = os.path.join(self.configs.output_path, "expanded.idf")
            if not os.path.isfile(expanded):
                shutil.copy(os.path.join(self.configs.output_path, "in.idf"), expanded)

            self.logger.info("Objetos expandidos com sucesso")
            
        except subprocess.TimeoutExpired:
            raise RuntimeError("Timeout ao expandir objetos")
        except Exception as e:
            self.logger.error(f"Erro ao expandir objetos: {e}")
            raise
    
    def _prepare_output_directories(self):
        """Criar o diretório da execução e trazer o modelo para dentro dele."""
        try:
            os.makedirs(self.configs.output_path, exist_ok=True)
            # output_path pode ter sido trocado depois da configuração ser criada.
            self.configs.expanded_idf_path = os.path.join(
                self.configs.output_path, "expanded.idf")

            # O IDFProcessor grava as alterações no lugar. Processar uma cópia
            # dentro da execução preserva o modelo original e deixa registrado
            # exatamente o IDF que foi simulado.
            if self.configs.source_idf_path is None:
                self.configs.source_idf_path = self.configs.idf_path
            model_path = os.path.join(self.configs.output_path, "modelo.idf")
            shutil.copy(self.configs.source_idf_path, model_path)
            self.configs.idf_path = model_path

            self.logger.info("Diretórios de saída preparados")
            
        except Exception as e:
            self.logger.error(f"Erro ao preparar diretórios: {e}")
            raise
    
    def _run_energyplus(self):
        """Executar simulação EnergyPlus."""
        try:
            if self.stop_requested:
                self.logger.info("Simulação cancelada antes de iniciar o EnergyPlus")
                return
            # Registrar callback do condicionador
            if self.conditioner is not None:
                self.ep_api.runtime.callback_begin_zone_timestep_after_init_heat_balance(
                    self.state, self.conditioner
                )
            self._register_progress_callback()
            
            # Executar simulação
            cmd_args = [
                '--weather', self.configs.epw_path,
                '--output-directory', self.configs.output_path,
                self.configs.expanded_idf_path
            ]
            
            self._ep_running = True
            try:
                exit_code = self.ep_api.runtime.run_energyplus(self.state, cmd_args)
            finally:
                self._ep_running = False
            self.ep_api.state_manager.reset_state(self.state)

            # Exceções do condicionador são engolidas pelo ctypes durante o run;
            # o condicionador guarda a primeira e ela é relançada aqui.
            if self.conditioner is not None and self.conditioner.error is not None:
                raise RuntimeError(
                    f"Condicionador falhou durante a simulação: {self.conditioner.error}"
                ) from self.conditioner.error

            if exit_code != 0 and not self.stop_requested:
                raise RuntimeError(
                    f"EnergyPlus terminou com código {exit_code}. "
                    f"Veja {os.path.join(self.configs.output_path, 'eplusout.err')}"
                )

            self._check_energyplus_errors()

            self.logger.info("Simulação EnergyPlus executada com sucesso")
            
        except Exception as e:
            self.logger.error(f"Erro na simulação EnergyPlus: {e}")
            raise

    def _register_progress_callback(self):
        """Percentual do EnergyPlus na fila, como `PROGRESS <n>`.

        O callback roda na thread do EnergyPlus: só empilha o número, quem
        desenha é a GUI. Falha silenciosa — versões antigas da API não têm
        `callback_progress` e a barra só fica indeterminada."""
        callback = getattr(self.ep_api.runtime, "callback_progress", None)
        if callback is None or self._queue is None:
            return
        queue = self._queue
        last = [-1]

        def on_progress(percent: int):
            # Um evento por ponto percentual: o EnergyPlus chama isso muitas
            # vezes por porcentagem e encheria a fila à toa.
            if percent != last[0]:
                last[0] = percent
                queue.put(f"PROGRESS {percent}")

        callback(self.state, on_progress)

    def _check_energyplus_errors(self):
        """Abortar se o EnergyPlus não tiver terminado com sucesso.

        O eplusout.end é a linha de status canônica do EnergyPlus; sem esta
        checagem um erro fatal passava como "simulação concluída com sucesso".
        """
        if self.stop_requested:
            return

        end_path = os.path.join(self.configs.output_path, "eplusout.end")
        err_path = os.path.join(self.configs.output_path, "eplusout.err")
        if not os.path.exists(end_path):
            raise RuntimeError(
                f"EnergyPlus não gerou {end_path} (execução abortada). Veja {err_path}"
            )

        with open(end_path, "r", errors="replace") as end_file:
            status = end_file.read().strip()

        if "Completed Successfully" not in status:
            raise RuntimeError(f"EnergyPlus não concluiu: {status}. Veja {err_path}")

        self.logger.info(status)
        self._report_warnings(err_path)

    def _report_warnings(self, err_path):
        """Contagem de warnings e severe errors da última linha do eplusout.err."""
        try:
            with open(err_path, "r", errors="replace") as err_file:
                last = [line for line in err_file.read().splitlines() if line.strip()][-1]
            found = re.search(r"(\d+)\s+Warning.*?(\d+)\s+Severe", last)
        except (OSError, IndexError):
            return
        if found and self._queue is not None:
            # `WARNING ` marca aviso: a GUI trata como erro qualquer texto com
            # "erro"/"error", e este resumo sai em toda execução bem-sucedida.
            self._queue.put(f"WARNING EnergyPlus: {found.group(1)} avisos e "
                            f"{found.group(2)} erros graves (veja eplusout.err na pasta da execução).")

    def _say(self, q: Queue, message: str):
        """Mesma mensagem para a GUI (fila) e para quem roda pela CLI (stdout)."""
        q.put(message)
        print(message)

    def _stopped(self, q: Queue) -> bool:
        """Verdadeiro se o cancelamento já foi pedido — checado entre as etapas
        do pós-processamento, que são longas e não interrompíveis por dentro."""
        if not self.stop_requested:
            return False
        self.logger.info("Pós-processamento interrompido")
        q.put("Simulação interrompida")
        return True

    def stop(self):
        """Pedir o cancelamento: encerra o EnergyPlus se ele estiver rodando;
        no pós-processamento a flag basta, checada entre as etapas."""
        self.stop_requested = True
        if self._ep_running:
            request_stop(self.ep_api, self.state)
    
    def _process_results(self, q: Queue):
        """Processar resultados da simulação."""
        try:
            self._say(q, "Simulação finalizada!")
            
            if self._stopped(q):
                return

            if self.passive:
                # O IDF não ganhou as Output:Variable que as planilhas usam; o
                # que o EnergyPlus gerou (eplusout.*, eplustbl.csv) fica na pasta.
                self._say(q, "Sem planilhas: o módulo não altera o IDF. "
                             "Saídas do EnergyPlus na pasta da execução.")
                q.put("EXIT")
                return

            self._say(q, "Extraindo resultados...")
            # O período e o passo vêm do IDF simulado: com valores fixos, um
            # modelo de outro ano ou outro timestep sairia com datas erradas.
            start, end = read_run_period(self.configs.idf_path)
            frames = self._timed("Extração ESO e Excel por sala", summary_rooms_results_from_eso,
                self.configs.output_path, self.configs.rooms,
                timesteps_per_hour=read_timesteps_per_hour(self.configs.idf_path),
                start_date=start, end_date=end, on_timing=self._record_time)
            self._say(q, "Resultados extraidos com sucesso!")
            
            if self._stopped(q):
                return

            self._say(q, "Extraindo estatísticas...")
            self._timed("Estatísticas", get_stats_from_simulation,
                        self.configs.output_path, self.configs.rooms, frames=frames)
            self._say(q, "Estatísticas extraidas com sucesso!")
            
            if self._stopped(q):
                return

            self._say(q, "Dividindo resultados por período...")
            for room in self.configs.rooms:
                if self._stopped(q):
                    return
                self._timed(f"Recortes {room}", split_target_period_excel,
                    os.path.join(self.configs.output_path, f"{room}.xlsx"),
                    room,
                    df=frames.get(room) if frames else None,
                )
            self._say(q, "Resultados divididos com sucesso!")
            
            q.put("EXIT")
            
        except Exception as e:
            error_msg = f"Erro no processamento de resultados: {str(e)}"
            self.logger.error(error_msg)
            q.put(error_msg)
            raise
