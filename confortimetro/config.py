from dataclasses import dataclass
import glob
import json
import os
import platform
import re
import shutil
import subprocess
from functools import lru_cache

from confortimetro.module_type import ModuleType
from confortimetro.paths import new_run_path, runs_root

# Banda do modelo adaptativo por nível de aceitação (ASHRAE 55).
PORCENT2ADAPTATIVE = {
    "90%": 2.5,
    "80%": 3.5
}

ADAPTATIVE2PORCENT = {value: key for key, value in PORCENT2ADAPTATIVE.items()}

# Versão da API do pyenergyplus que o projeto usa.
REQUIRED_EP_VERSION = "9.4"
# `EnergyPlusAPI.api_version()` do EnergyPlus 9.4.
REQUIRED_EP_API_VERSION = "0.2"


def _platform_globs() -> list[str]:
    """Padrões de instalação do EnergyPlus na plataforma atual."""
    system = platform.system()
    if system == "Windows":
        program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
        return [r"C:\EnergyPlusV*", os.path.join(program_files, "EnergyPlusV*")]
    if system == "Darwin":
        return ["/Applications/EnergyPlus-*"]
    return ["/usr/local/EnergyPlus-*", "/opt/EnergyPlus-*"]


def migrated_runs_root(output_path: str) -> str:
    """Raiz das execuções a partir do antigo campo de saída único.

    O usuário escrevia ali as duas coisas: a raiz (`outputs`) ou a pasta de uma
    execução (`outputs/run_001`). Quem decide é o conteúdo: vale a primeira das
    duas que já tenha uma execução dentro; sem nenhuma, a pasta que existir.
    """
    from confortimetro.results.compare import has_runs

    path = (output_path or "").rstrip(os.sep)
    if not path:
        return runs_root()

    parent = os.path.dirname(path)
    for candidate in (path, parent):
        if candidate and has_runs(candidate):
            return candidate
    # Sem execução em nenhuma das duas: a pasta que já existe é a raiz (raiz
    # nova e ainda vazia), e a que não existe era a subpasta da próxima
    # execução — nesse caso a raiz é a de cima.
    if os.path.isdir(path):
        return path
    return parent or runs_root()


def default_energy_path() -> str:
    """Instalação padrão do EnergyPlus 9.4 na plataforma atual."""
    if platform.system() == "Windows":
        return r"C:\EnergyPlusV9-4-0"
    if platform.system() == "Darwin":
        return "/Applications/EnergyPlus-9-4-0"
    return "/usr/local/EnergyPlus-9-4-0"


def is_energy_path(path: str) -> bool:
    """
    Verdadeiro se `path` é a raiz de uma instalação utilizável do EnergyPlus.

    Exige os dois arquivos que a simulação usa: o IDD e a API Python. Isso
    rejeita uma subpasta da instalação, que existe mas quebra depois com
    `IDD file not found`.
    """
    if not path or not os.path.isdir(path):
        return False
    return os.path.isfile(os.path.join(path, "Energy+.idd")) and os.path.isfile(
        os.path.join(path, "pyenergyplus", "api.py")
    )


@lru_cache(maxsize=32)
def _energyplus_cli_version(path: str) -> str:
    """Versão informada pelo próprio executável (`energyplus --version`)."""
    executable = os.path.join(
        path, "energyplus.exe" if platform.system() == "Windows" else "energyplus"
    )
    if not os.path.isfile(executable):
        return ""
    try:
        output = subprocess.run(
            [executable, "--version"], capture_output=True, text=True, timeout=10
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return ""
    match = re.search(r"Version\s+(\d+)\.(\d+)", output)
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def energy_path_version(path: str) -> str:
    """
    Versão da instalação.

    Pergunta ao próprio EnergyPlus (`energyplus --version`); se o executável
    não responder, cai no nome do diretório (`EnergyPlusV9-4-0`,
    `EnergyPlus-9-4-0`, `EnergyPlus-23-2-0`).

    Retorna "" quando nenhuma das duas fontes diz a versão.
    """
    version = _energyplus_cli_version(path)
    if version:
        return version
    match = re.search(r"(\d+)[-.](\d+)", os.path.basename(os.path.normpath(path)))
    return f"{match.group(1)}.{match.group(2)}" if match else ""


def energy_api_version(path: str) -> str:
    """
    Versão da API Python declarada em `pyenergyplus/api.py` (`api_version`).

    Lê o arquivo em vez de importar: o pyenergyplus da instalação escolhida
    ainda não está no `sys.path` na hora de validar o caminho. Retorna "" se
    o arquivo não declarar a versão.
    """
    api_file = os.path.join(path, "pyenergyplus", "api.py")
    try:
        with open(api_file, encoding="utf-8", errors="replace") as handle:
            source = handle.read()
    except OSError:
        return ""
    # O 9.4 devolve a versão como string (`return "0.2"`); versões antigas
    # devolvem float. Os dois casos caem no mesmo grupo.
    match = re.search(r"def api_version\b.*?return\s+[\"']?([\d.]+)", source, re.S)
    return match.group(1).rstrip(".") if match else ""


def _windows_registry_paths() -> list[str]:
    """Instalações do EnergyPlus registradas no desinstalador do Windows."""
    if platform.system() != "Windows":
        return []

    import winreg

    found = []
    key_path = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            uninstall = winreg.OpenKey(root, key_path)
        except OSError:
            continue
        with uninstall:
            for index in range(winreg.QueryInfoKey(uninstall)[0]):
                try:
                    with winreg.OpenKey(uninstall, winreg.EnumKey(uninstall, index)) as sub:
                        name = winreg.QueryValueEx(sub, "DisplayName")[0]
                        if not str(name).startswith("EnergyPlus"):
                            continue
                        found.append(winreg.QueryValueEx(sub, "InstallLocation")[0])
                except OSError:
                    continue
    return found


def find_energy_path() -> str:
    """
    Localiza a instalação do EnergyPlus, preferindo sempre a
    `REQUIRED_EP_VERSION`.

    Ordem das fontes: variável `ENERGYPLUS_DIR`, `energyplus` no PATH,
    diretórios padrão da plataforma e, no Windows, o registro. Retorna "" se
    nenhuma instalação válida for encontrada.
    """
    candidates = []

    env_path = os.environ.get("ENERGYPLUS_DIR")
    if env_path:
        candidates.append(env_path)

    executable = shutil.which("energyplus")
    if executable:
        candidates.append(os.path.dirname(os.path.realpath(executable)))

    for pattern in _platform_globs():
        candidates.extend(sorted(glob.glob(pattern), reverse=True))

    candidates.extend(_windows_registry_paths())

    valid = [path for path in candidates if is_energy_path(path)]
    for path in valid:
        if energy_path_version(path) == REQUIRED_EP_VERSION:
            return path
    return valid[0] if valid else ""


#: Nome na interface de cada campo numérico, para as mensagens de erro.
FIELD_LABELS = {
    "met": "Met", "wme": "Wme", "pmv_comfort_bound": "Banda de conforto",
    "pmv_lowerbound": "Faixa de PMV (mínimo)",
    "pmv_upperbound": "Faixa de PMV (máximo)",
    "clo_min": "Faixa de Clo (mínimo)", "clo_max": "Faixa de Clo (máximo)",
    "clo_delta": "Variação do Clo",
    "temp_ac_min": "Temperatura do AC (mínimo)",
    "temp_ac_max": "Temperatura do AC (máximo)",
    "max_vel": "Velocidade máxima", "air_speed_delta": "Variação da vel. de ventilação",
    "temp_open_window_bound": "Margem de temp. p/ abrir janela",
    "co2_limit": "Limite de CO2",
}

#: Faixa permitida de cada par mínimo–máximo da tela (escala e limites físicos).
RANGE_PAIRS = (
    ("pmv_lowerbound", "pmv_upperbound", -3.0, 3.0),
    ("clo_min", "clo_max", 0.0, 2.0),
    ("temp_ac_min", "temp_ac_max", 10.0, 35.0),
)


def range_problems(values: dict, pairs=RANGE_PAIRS) -> list[str]:
    """Erros nas faixas PMV, Clo e AC de `values` (nome do campo → número).

    Nada é corrigido: valor fora da escala ou mínimo acima do máximo vira
    mensagem, em vez de ser limitado ou trocado em silêncio.
    """
    problems = []
    for low_key, high_key, lower, upper in pairs:
        for key in (low_key, high_key):
            if not lower <= values[key] <= upper:
                problems.append(f"{FIELD_LABELS[key]}: {values[key]:g} fora da "
                                f"faixa {lower:g} a {upper:g}.")
        if values[low_key] > values[high_key]:
            problems.append(f"{FIELD_LABELS[low_key]} ({values[low_key]:g}) é maior "
                            f"que o máximo ({values[high_key]:g}).")
    return problems


@dataclass
class SimulationConfig:
    met_as_watts: float
    _idf_path: str
    _met: float

    epw_path: str = None
    output_path: str = None
    energy_path: str = None
    rooms: list[str] = None
    pmv_upperbound: float = 0.5
    pmv_lowerbound: float = -0.5
    co2_limit: float = 1000
    max_vel: float = 1.2
    adaptative_bound: float = 2.5
    temp_ac_min: float = 16.0
    temp_ac_max: float = 30.0
    wme: float = 0.0
    clo_max: float = 1.0
    clo_min: float = 0.5
    clo_delta: float = 0.1
    clo_priority: bool = True

    # Pasta que guarda todas as execuções; `output_path` é a subpasta desta
    # execução, criada quando a simulação começa.
    runs_root_path: str = None
    input_path: str = None
    expanded_idf_path: str = None
    # IDF escolhido pelo usuário; `_idf_path` passa a apontar para a cópia
    # gravada dentro da execução assim que a simulação começa.
    source_idf_path: str = None
    idf_filename: str = None
    temp_open_window_bound: float = 5.0
    air_speed_delta: float = 0.15
    pmv_comfort_bound: float = 0.2
    module_type: ModuleType = ModuleType.COMPLETE
    # Rodar mesmo com zonas sem o equipamento que o módulo exige: o usuário
    # confirmou o aviso. Fica no configs.json para a execução registrar isso.
    ignore_missing_equipment: bool = False
    # Versão do código que rodou a execução (`versao.code_version`), gravada
    # quando a simulação começa; o assistente compara com a instalada.
    code_version: dict = None
    # Período (`aaaa-mm-dd`, fim inclusivo) e passo escolhidos na tela; quando
    # presentes, o IDFProcessor os grava no IDF da execução. None = o do IDF.
    run_period_start: str = None
    run_period_end: str = None
    timesteps_per_hour: int = None
    # Nome dado pelo usuário; a listagem e a comparação o mostram no lugar da
    # pasta (`20261007_2133`). None = nome montado com o IDF e a hora.
    nome: str = None
    # Execução de um grupo de etiquetagem INI-C (`confortimetro.etiquetagem`):
    # grupo, papel do modelo (real, real_vn, referencia), tipologia, uso, modo,
    # ZB e zonas avaliadas. None = simulação comum.
    etiquetagem: dict = None

    def __post_init__(self):
        # Sem saída escolhida, cada execução ganha a sua subpasta na pasta de
        # dados da aplicação — o diretório do repositório (ou o do executável,
        # que pode ser somente leitura) não é lugar de resultado.
        if not self.runs_root_path:
            self.runs_root_path = runs_root()
        if not self.output_path:
            self.output_path = new_run_path(root=self.runs_root_path)
        self.input_path = os.path.dirname(self.idf_path)
        # O IDF expandido é artefato da execução e mora com ela; deixá-lo ao
        # lado do IDF de entrada fazia execuções paralelas se atropelarem.
        self.expanded_idf_path = os.path.join(self.output_path, "expanded.idf")
        self.idf_filename = os.path.basename(self.idf_path)
        self.met_as_watts = self.met * 58.1 * 1.8

    @property
    def idf_path(self):
        return self._idf_path

    @idf_path.setter
    def idf_path(self, idf_path: str):
        self._idf_path = idf_path
        self.input_path = os.path.dirname(self.idf_path)
        self.idf_filename = os.path.basename(self.idf_path)

    @property
    def met(self):
        return self._met
    
    @met.setter
    def met(self, met: float):
        self._met = met
        self.met_as_watts = met * 58.1 * 1.8

    def to_json(self, json_path: str=None):
        if json_path is None:
            json_path = os.path.join(self.output_path, "config.json")

        with open(json_path, "w", encoding="utf-8") as writer:
            json.dump(self.__dict__, writer, indent=4)
    
    @staticmethod
    def from_json(json_path: str):
        with open(json_path, "r", encoding="utf-8") as reader:
            data = json.load(reader)
        
        config = SimulationConfig(**data)

        # Config anterior ao `runs_root_path`: o campo de saída guardava a
        # pasta de uma execução ou a raiz que as contém, sem distinção.
        if not data.get("runs_root_path"):
            config.runs_root_path = migrated_runs_root(data.get("output_path"))

        # O config.json versionado traz caminhos de Linux; numa máquina sem esse
        # diretório, redetecta em vez de abrir a GUI com um caminho quebrado.
        if not is_energy_path(config.energy_path):
            config.energy_path = find_energy_path() or default_energy_path()

        return config
