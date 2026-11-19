"""Cliente do servidor do Ambiens: envia simulações e baixa o espelho leve.

O espelho é a pasta da execução sem os brutos do EnergyPlus, gravada na raiz
local de execuções: listagem, comparação, gráficos e assistente leem dele sem
saber que a simulação rodou longe. `remoto.json` liga a pasta local ao id do
servidor, para `sync` completar uma execução se o app fechou no meio.
"""

import json
import os
import time
import zipfile

import httpx

from confortimetro.paths import app_data_path

SETTINGS_FILE = "servidor.json"
TOKEN_FILE = "servidor_token"
KEYRING_SERVICE = "Ambiens-servidor"
KEYRING_USER = "token"
REMOTE_FILE = "remoto.json"
FINAL_STATES = ("concluida", "falhou", "interrompida", "cancelada")
POLL_S = 2
RETRY_S = 10


class RemoteError(RuntimeError):
    pass


# --- Configuração -----------------------------------------------------------

def _write_json(path: str, data):
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2)
    os.replace(temporary, path)


def load_settings() -> dict:
    settings = {"url": "", "ativo": False}
    try:
        with open(os.path.join(app_data_path(), SETTINGS_FILE), encoding="utf-8") as handle:
            saved = json.load(handle)
        settings.update(url=str(saved.get("url") or ""), ativo=saved.get("ativo") is True)
    except (OSError, ValueError, AttributeError):
        pass
    return settings


def save_settings(url: str, active: bool):
    os.makedirs(app_data_path(), exist_ok=True)
    _write_json(os.path.join(app_data_path(), SETTINGS_FILE),
                {"url": url.strip().rstrip("/"), "ativo": bool(active)})


def enabled() -> bool:
    settings = load_settings()
    return settings["ativo"] and bool(settings["url"])


def get_token() -> str:
    from confortimetro.assistant.store import _keyring
    keyring = _keyring()
    if keyring:
        try:
            token = keyring.get_password(KEYRING_SERVICE, KEYRING_USER)
            if token:
                return token
        except Exception:
            pass
    try:
        with open(os.path.join(app_data_path(), TOKEN_FILE), encoding="utf-8") as handle:
            return handle.read().strip()
    except OSError:
        return ""


def set_token(token: str):
    """No keyring; sem ele, num arquivo legível só pelo usuário."""
    from confortimetro.assistant.store import _keyring
    keyring = _keyring()
    if keyring:
        try:
            keyring.set_password(KEYRING_SERVICE, KEYRING_USER, token.strip())
            return
        except Exception:
            pass
    os.makedirs(app_data_path(), exist_ok=True)
    path = os.path.join(app_data_path(), TOKEN_FILE)
    with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w",
              encoding="utf-8") as handle:
        handle.write(token.strip())


# --- HTTP -------------------------------------------------------------------

class Client:
    def __init__(self, url: str = None, token: str = None, transport=None):
        self.url = (url or load_settings()["url"]).rstrip("/")
        if not self.url:
            raise RemoteError("Endereço do servidor não configurado")
        self.http = httpx.Client(
            base_url=self.url, transport=transport,
            headers={"Authorization": f"Bearer {token or get_token()}"},
            timeout=httpx.Timeout(30, read=300))

    def _json(self, method: str, path: str, **kwargs) -> dict:
        response = self.http.request(method, path, **kwargs)
        if response.is_error:
            raise RemoteError(_detail(response))
        return response.json()

    def me(self) -> dict:
        return self._json("GET", "/api/eu")

    def runs(self) -> list:
        return self._json("GET", "/api/execucoes")["execucoes"]

    def submit(self, config) -> str:
        data = {**vars(config), "met": config.met}
        with open(config.idf_path, "rb") as idf, open(config.epw_path, "rb") as epw:
            body = self._json("POST", "/api/execucoes",
                              data={"configuracao": json.dumps(data, default=str)},
                              files={"idf": (os.path.basename(config.idf_path), idf),
                                     "epw": (os.path.basename(config.epw_path), epw)})
        return body["id"]

    def status(self, run_id: str) -> dict:
        return self._json("GET", f"/api/execucoes/{run_id}")

    def log(self, run_id: str, offset: int) -> tuple:
        body = self._json("GET", f"/api/execucoes/{run_id}/log", params={"desde": offset})
        return body["texto"], body["proximo"]

    def cancel(self, run_id: str) -> dict:
        return self._json("POST", f"/api/execucoes/{run_id}/cancelar")

    def download(self, run_id: str, dest: str, full: bool = False):
        """Baixa o espelho (ou, com `full`, tudo, brutos incluídos) para `dest`;
        `configs.json` sai por último, com caminhos locais."""
        os.makedirs(dest, exist_ok=True)
        _write_json(os.path.join(dest, REMOTE_FILE), {"servidor": self.url, "id": run_id})
        partial = os.path.join(dest, ".espelho.zip.parcial")
        params = {"completo": "1"} if full else None
        with self.http.stream("GET", f"/api/execucoes/{run_id}/espelho", params=params) as response:
            if response.is_error:
                response.read()
                raise RemoteError(_detail(response))
            with open(partial, "wb") as handle:
                for chunk in response.iter_bytes():
                    handle.write(chunk)
        with zipfile.ZipFile(partial) as archive:
            names = archive.namelist()
            # extractall descarta `..` e caminhos absolutos dos nomes.
            archive.extractall(dest, members=[name for name in names if name != "configs.json"])
            config = json.loads(archive.read("configs.json")) if "configs.json" in names else None
        os.remove(partial)
        if config is not None:
            _write_json(os.path.join(dest, "configs.json"), localize(config, dest))


def _detail(response) -> str:
    try:
        return f"Servidor ({response.status_code}): {response.json()['erro']}"
    except (ValueError, KeyError, TypeError):
        return f"Servidor ({response.status_code}): {response.text[:200]}"


def localize(config: dict, dest: str) -> dict:
    """Troca os caminhos da pasta do servidor pelos da cópia local.

    O servidor de produção é Linux (`/`), mas um servidor no Windows grava `\\`:
    a comparação normaliza os dois separadores.
    """
    def norm(path):
        return path.replace("\\", "/")

    server_run = norm(config.get("output_path") or "").rstrip("/")
    for key, value in config.items():
        if server_run and isinstance(value, str) and (
                norm(value) == server_run or norm(value).startswith(server_run + "/")):
            rest = norm(value)[len(server_run):].strip("/")
            config[key] = os.path.join(dest, *rest.split("/")) if rest else dest
    config["runs_root_path"] = os.path.dirname(dest)
    config["energy_path"] = ""  # from_json redetecta a instalação desta máquina
    return config


# --- Simulação e sincronização ----------------------------------------------

class RemoteSimulation:
    """Mesma interface da `Simulation` (`run(q)`, `stop()`, `stop_requested`)."""

    def __init__(self, configs, client: Client = None):
        self.configs = configs
        self.client = client or Client()
        self.stop_requested = False
        self.run_id = None

    def stop(self):
        # Só marca: quem cancela no servidor é o laço de `run`, fora da thread do Tk.
        self.stop_requested = True

    def run(self, q):
        q.put("Enviando IDF, clima e configuração ao servidor...")
        self.run_id = self.client.submit(self.configs)
        dest = self.configs.output_path
        os.makedirs(dest, exist_ok=True)
        _write_json(os.path.join(dest, REMOTE_FILE), {"servidor": self.client.url, "id": self.run_id})
        q.put(f"Execução {self.run_id} criada no servidor.")

        offset, state, cancelled = 0, None, False
        while True:
            try:
                if self.stop_requested and not cancelled:
                    try:
                        self.client.cancel(self.run_id)
                    except RemoteError:
                        pass  # já terminou: o estado final diz como
                    cancelled = True
                current = self.client.status(self.run_id)
                text, offset = self.client.log(self.run_id, offset)
            except httpx.TransportError:
                q.put(f"Sem conexão com o servidor; nova tentativa em {RETRY_S} s...")
                time.sleep(RETRY_S)
                continue
            for line in text.splitlines():
                if line and line != "EXIT":
                    q.put(line)
            if current["estado"] != state:
                state = current["estado"]
                if state in ("aguardando", "na_fila"):
                    q.put(f"Servidor: {state.replace('_', ' ')}")
            if state in FINAL_STATES:
                break
            time.sleep(POLL_S)

        if state == "cancelada" or self.stop_requested:
            q.put("Simulação interrompida")
            return
        if state != "concluida":
            raise RemoteError(f"Simulação {state} no servidor: {current.get('erro', '')}")
        q.put("Baixando resultados do servidor...")
        self.client.download(self.run_id, dest)
        q.put("Resultados baixados do servidor.")


def is_remote(dest: str) -> bool:
    return os.path.isfile(os.path.join(dest, REMOTE_FILE))


def download_full(dest: str):
    """Completa a pasta local com todos os arquivos da execução no servidor."""
    with open(os.path.join(dest, REMOTE_FILE), encoding="utf-8") as handle:
        info = json.load(handle)
    Client(url=info["servidor"]).download(info["id"], dest, full=True)


def sync(root: str, client: Client = None) -> int:
    """Baixa as execuções concluídas que ainda não têm espelho em `root`."""
    client = client or Client()
    known = {}
    for entry in os.scandir(root) if os.path.isdir(root) else ():
        try:
            with open(os.path.join(entry.path, REMOTE_FILE), encoding="utf-8") as handle:
                known[json.load(handle)["id"]] = entry.path
        except (OSError, ValueError, KeyError, TypeError):
            continue
    downloaded = 0
    for run in client.runs():
        dest = known.get(run["id"]) or os.path.join(root, run["id"])
        if run["estado"] == "concluida" and not os.path.isfile(os.path.join(dest, "configs.json")):
            client.download(run["id"], dest)
            downloaded += 1
    return downloaded
