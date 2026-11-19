"""Cliente MCP real via stdio e disparo isolado (sem EnergyPlus demorado)."""

import json
import os
import re
import subprocess
import sys
import time
import types

import anyio
import pandas
import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from confortimetro import mcp_runner, mcp_server

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _payload(result):
    assert not result.isError, result.content
    return result.structuredContent or json.loads(result.content[0].text)


def test_stdio_listar_resumo_validar_e_recusar_traversal(tmp_path):
    root = tmp_path / "dados" / "execucoes"
    root.mkdir(parents=True)
    fixture = root / "fixture"
    fixture.mkdir()
    (fixture / "configs.json").write_text(json.dumps({"rooms": ["SALA"], "module_type": "COMPLETE"}))
    pandas.DataFrame([{"Nome da sala": "SALA", "Energia total (kWh)": 12.5,
                       "Desconforto (%)": 0.1}]).to_excel(fixture / "ESTATISTICAS.xlsx", index=False)
    outside = tmp_path / "fora"
    outside.mkdir()
    (outside / "configs.json").write_text("{}")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
        refused_ids = ("../fora", "link")
    except OSError:  # Windows sem privilégio de criar symlink
        refused_ids = ("../fora",)

    async def check():
        env = {**os.environ, "CONFORTIMETRO_DATA_DIR": str(tmp_path / "dados")}
        env.pop("AMBIENS_DATA_DIR", None)
        env["PYTHONPATH"] = os.path.dirname(os.path.dirname(__file__))
        params = StdioServerParameters(command=sys.executable,
                                       args=["-m", "confortimetro.mcp_server"], env=env)
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert {tool.name for tool in tools.tools} == {
                    "listar_execucoes", "ler_execucao", "validar_configuracao",
                    "iniciar_simulacao", "estado_simulacao"}
                listed = _payload(await session.call_tool("listar_execucoes"))
                assert [run["id"] for run in listed["execucoes"]] == ["fixture"]
                summary = _payload(await session.call_tool("ler_execucao", {"id": "fixture"}))
                assert summary["configuracao"]["module_type"] == "COMPLETE"
                assert summary["indicadores"] == [{"Nome da sala": "SALA",
                    "Energia total (kWh)": 12.5, "Desconforto (%)": 0.1}]
                invalid = _payload(await session.call_tool("validar_configuracao", {
                    "configuracao": {"idf_path": str(outside / "nao-existe.idf")}}))
                assert invalid["valida"] is False
                repo = os.path.dirname(os.path.dirname(__file__))
                if os.path.isfile("/usr/local/EnergyPlus-9-4-0/Energy+.idd"):
                    valid = _payload(await session.call_tool("validar_configuracao", {
                        "configuracao": {
                            "idf_path": os.path.join(repo, "examples", "idf", "SALA", "SALA_PTHP.idf"),
                            "epw_path": os.path.join(repo, "examples", "epw", "BRA_RS_Camaqua.869890_INMET.epw"),
                            "rooms": ["SALA"], "module_type": "CLOSED_WINDOW",
                        }}))
                    assert valid == {"valida": True, "erros": []}
                for name in refused_ids:
                    refused = await session.call_tool("ler_execucao", {"id": name})
                    assert refused.isError

    anyio.run(check)


def test_disparo_fake_runner_persiste_estado(tmp_path, monkeypatch):
    """Fake só na fronteira Popen; não dispara simulação anual de verdade."""
    monkeypatch.setenv("CONFORTIMETRO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AMBIENS_DATA_DIR", raising=False)
    idf = tmp_path / "entrada.idf"
    idf.write_text("Zone, SALA;", encoding="utf-8")
    epw = tmp_path / "clima.epw"
    epw.write_text("epw", encoding="utf-8")
    energy = tmp_path / "EnergyPlus"
    (energy / "pyenergyplus").mkdir(parents=True)
    (energy / "Energy+.idd").touch()
    (energy / "pyenergyplus" / "api.py").touch()
    monkeypatch.setattr(mcp_server, "validar_configuracao", lambda args: {"valida": True, "erros": []})
    called = []
    monkeypatch.setattr(mcp_server, "find_energy_path", lambda: str(energy))
    monkeypatch.setattr(mcp_server.subprocess, "Popen", lambda *args, **kwargs: called.append((args, kwargs))
                        or types.SimpleNamespace(pid=os.getpid()))

    started = mcp_server.iniciar_simulacao({"idf_path": str(idf), "epw_path": str(epw), "rooms": ["SALA"]})
    assert started == {"id": started["id"], "pasta": started["pasta"], "estado": "na_fila"}
    assert re.fullmatch(r"\d{8}_\d{4}_mcp_[0-9a-f]{12}", started["id"])
    assert len(called) == 1
    assert called[0][0][0][:3] == [sys.executable, "-m", "confortimetro.mcp_runner"]
    if os.name != "nt":
        # Sessão própria: o killpg do cliente ao fechar não alcança o runner.
        assert called[0][1]["start_new_session"] is True
    assert mcp_server.estado_simulacao(started["id"])["estado"] == "na_fila"
    from confortimetro.mcp_runner import write_status
    write_status(started["pasta"], "concluida")
    assert mcp_server.estado_simulacao(started["id"])["estado"] == "concluida"
    saved = json.loads((tmp_path / "execucoes" / started["id"] / "entrada_mcp.json").read_text())
    assert saved["output_path"] == started["pasta"]
    with pytest.raises(ValueError, match="zonas"):
        mcp_server._config({"idf_path": str(idf), "epw_path": str(epw),
            "rooms": ["../fora"]}, started["pasta"])


@pytest.mark.skipif(not os.path.isfile("/usr/local/EnergyPlus-9-4-0/Energy+.idd"),
                    reason="EnergyPlus 9.4 indisponível")
def test_disparo_valida_idf_real_antes_de_abrir_runner(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFORTIMETRO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AMBIENS_DATA_DIR", raising=False)
    mcp_server._energy_installation.cache_clear()
    monkeypatch.setattr(mcp_server, "find_energy_path", lambda: "/usr/local/EnergyPlus-9-4-0")
    calls = []
    monkeypatch.setattr(mcp_server.subprocess, "Popen", lambda *a, **k: calls.append((a, k))
                        or types.SimpleNamespace(pid=os.getpid()))
    config = {"idf_path": os.path.join(REPO, "examples", "idf", "SALA", "SALA_PTHP.idf"),
              "epw_path": os.path.join(REPO, "examples", "epw", "BRA_RS_Camaqua.869890_INMET.epw"),
              "rooms": ["SALA"], "module_type": "CLOSED_WINDOW"}
    assert mcp_server.iniciar_simulacao(config)["estado"] == "na_fila"
    assert len(calls) == 1
    assert not mcp_server.iniciar_simulacao({**config, "rooms": ["inexistente"]})["valida"]
    assert len(calls) == 1


def _inputs(tmp_path, monkeypatch):
    mcp_server._energy_installation.cache_clear()
    monkeypatch.setenv("CONFORTIMETRO_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("AMBIENS_DATA_DIR", raising=False)
    idf = tmp_path / "entrada.idf"
    idf.write_text("Zone, SALA;", encoding="utf-8")
    epw = tmp_path / "clima.epw"
    epw.write_text("epw", encoding="utf-8")
    energy = tmp_path / "EnergyPlus"
    (energy / "pyenergyplus").mkdir(parents=True)
    (energy / "Energy+.idd").touch()
    (energy / "pyenergyplus" / "api.py").touch()
    monkeypatch.setattr(mcp_server, "find_energy_path", lambda: str(energy))
    return {"idf_path": str(idf), "epw_path": str(epw), "rooms": ["SALA"]}, energy


def _run_dir(tmp_path, name, status=None):
    run = tmp_path / "execucoes" / name
    run.mkdir(parents=True)
    if status is not None:
        (run / mcp_server.STATUS_FILE).write_text(json.dumps(status), encoding="utf-8")
    return os.path.realpath(run)


def test_energy_path_nao_e_argumento_da_ferramenta(tmp_path, monkeypatch):
    """Pasta com cara de EnergyPlus vinda do modelo não pode ser importada pelo runner."""
    args, energy = _inputs(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="energy_path"):
        mcp_server._config({**args, "energy_path": str(energy)}, str(tmp_path / "saida"))
    refused = mcp_server.validar_configuracao({**args, "energy_path": str(energy)})
    assert refused["valida"] is False and "energy_path" in refused["erros"][0]
    config = mcp_server._config(args, str(tmp_path / "saida"))
    assert config.energy_path == os.path.realpath(energy)


def test_caminhos_de_entrada_relativos_sao_recusados(tmp_path, monkeypatch):
    args, _ = _inputs(tmp_path, monkeypatch)
    for field in ("idf_path", "epw_path"):
        assert mcp_server.validar_configuracao({**args, field: os.path.basename(args[field])})["valida"] is False


def test_energyplus_nao_troca_idd_com_servidor_ativo(tmp_path, monkeypatch):
    args, first = _inputs(tmp_path, monkeypatch)
    assert mcp_server._config(args, str(tmp_path / "saida")).energy_path == str(first)
    second = tmp_path / "outra"
    (second / "pyenergyplus").mkdir(parents=True)
    (second / "Energy+.idd").touch()
    (second / "pyenergyplus" / "api.py").touch()
    monkeypatch.setattr(mcp_server, "find_energy_path", lambda: str(second))
    assert mcp_server._config(args, str(tmp_path / "saida")).energy_path == str(first)


def test_idd_diferente_do_fixado_no_processo_vira_erro_tratado(tmp_path, monkeypatch):
    from eppy.modeleditor import IDF
    args, _ = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(IDF, "iddname", str(tmp_path / "outra" / "Energy+.idd"))
    result = mcp_server.validar_configuracao(args)
    assert result == {"valida": False,
                      "erros": ["A instalação do EnergyPlus mudou; reinicie o servidor MCP"]}


def _fake_cli(monkeypatch, behaviour):
    module = types.ModuleType("cli")
    module.main = behaviour
    monkeypatch.setitem(sys.modules, "cli", module)


def _raise(argv):
    raise RuntimeError("IDF quebrado")


@pytest.mark.parametrize("behaviour, code, state, error", [
    (lambda argv: 0, 0, "concluida", ""),
    (lambda argv: 2, 2, "falhou", "Consulte mcp.log na pasta da execução"),
    (_raise, 1, "falhou", "IDF quebrado"),
])
def test_runner_grava_pid_e_resultado(tmp_path, monkeypatch, behaviour, code, state, error):
    run = tmp_path / "exec"
    run.mkdir()
    config = run / "entrada_mcp.json"
    config.write_text("{}", encoding="utf-8")
    seen = []

    def cli(argv):
        seen.append(json.loads((run / "mcp_status.json").read_text()))
        assert argv == ["--config", str(config), "--quiet"]
        return behaviour(argv)

    _fake_cli(monkeypatch, cli)
    monkeypatch.setattr(sys, "argv", ["mcp_runner", str(config)])
    assert mcp_runner.main() == code
    assert seen[0]["estado"] == "executando" and seen[0]["pid"] == os.getpid()
    final = json.loads((run / "mcp_status.json").read_text())
    assert (final["estado"], final["erro"]) == (state, error)


def test_runner_morto_vira_interrompida(tmp_path):
    """Runner de verdade, com um cli falso que só dorme; SIGKILL no meio da execução."""
    fake = tmp_path / "fake_cli"
    fake.mkdir()
    (fake / "cli.py").write_text("import time\n\ndef main(argv):\n    time.sleep(120)\n    return 0\n")
    run = _run_dir(tmp_path, "20260101_0000_mcp_morto")
    config = os.path.join(run, "entrada_mcp.json")
    with open(config, "w", encoding="utf-8") as handle:
        handle.write("{}")
    process = subprocess.Popen([sys.executable, "-m", "confortimetro.mcp_runner", config], cwd=fake,
                               env={**os.environ, "PYTHONPATH": REPO})
    try:
        deadline = time.time() + 30
        while mcp_server._status(run).get("estado") != "executando":
            assert time.time() < deadline, "runner não chegou a executando"
            time.sleep(0.1)
        # No Windows o python.exe do venv é um lançador: o runner roda num
        # processo filho, com outro PID.
        if os.name != "nt":
            assert mcp_server._status(run)["pid"] == process.pid
    finally:
        process.kill()
        process.wait()
    assert mcp_server._status(run) == {"estado": "interrompida", "erro": mcp_server.INTERRUPTED}
    # Persistido: a próxima leitura (outro servidor) já vê o estado final.
    saved = json.loads(open(os.path.join(run, mcp_server.STATUS_FILE), encoding="utf-8").read())
    assert saved["estado"] == "interrompida"


def test_na_fila_sem_runner_expira(tmp_path, monkeypatch):
    monkeypatch.setenv("CONFORTIMETRO_DATA_DIR", str(tmp_path))
    run = _run_dir(tmp_path, "20260101_0000_mcp_fila", {"estado": "na_fila", "erro": ""})
    assert mcp_server._status(run)["estado"] == "na_fila"
    old = time.time() - mcp_server.QUEUE_TIMEOUT - 5
    os.utime(os.path.join(run, mcp_server.STATUS_FILE), (old, old))
    assert mcp_server._status(run)["estado"] == "interrompida"


@pytest.mark.skipif(sys.platform != "linux", reason="checagem de reuso de PID usa /proc")
def test_pid_reaproveitado_nao_conta_como_vivo():
    begun = mcp_server._process_start(os.getpid())
    assert begun is not None
    assert mcp_server._pid_alive(os.getpid(), begun + 1)
    assert not mcp_server._pid_alive(os.getpid(), begun - 100)


def test_teto_de_execucoes_vivas(tmp_path, monkeypatch):
    args, _ = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server, "validar_configuracao", lambda configuracao: {"valida": True, "erros": []})
    monkeypatch.setattr(mcp_server.subprocess, "Popen",
                        lambda *a, **k: types.SimpleNamespace(pid=os.getpid()))
    monkeypatch.delenv(mcp_server.MAX_ACTIVE_ENV, raising=False)
    monkeypatch.delenv(mcp_server.LEGACY_MAX_ACTIVE_ENV, raising=False)
    _run_dir(tmp_path, "20250101_0000", {"estado": "concluida", "erro": ""})
    assert mcp_server.iniciar_simulacao(args)["estado"] == "na_fila"
    assert mcp_server.iniciar_simulacao(args)["estado"] == "na_fila"
    refused = mcp_server.iniciar_simulacao(args)
    assert refused == {"estado": "recusada", "erro": "Limite de simulações simultâneas atingido"}
    monkeypatch.setenv(mcp_server.MAX_ACTIVE_ENV, "3")
    assert mcp_server.iniciar_simulacao(args)["estado"] == "na_fila"
    monkeypatch.setenv(mcp_server.MAX_ACTIVE_ENV, "0")
    with pytest.raises(ValueError, match=mcp_server.MAX_ACTIVE_ENV):
        mcp_server.iniciar_simulacao(args)


def test_listagem_cronologica_junto_com_gui(tmp_path, monkeypatch):
    args, _ = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server, "validar_configuracao", lambda configuracao: {"valida": True, "erros": []})
    monkeypatch.setattr(mcp_server.subprocess, "Popen",
                        lambda *a, **k: types.SimpleNamespace(pid=os.getpid()))
    for name in ("20000101_0000", "29991231_2359"):
        gui = tmp_path / "execucoes" / name
        gui.mkdir(parents=True)
        (gui / "configs.json").write_text(json.dumps({"rooms": ["SALA"]}))
    started = mcp_server.iniciar_simulacao(args)
    ids = [row["id"] for row in mcp_server.listar_execucoes()["execucoes"]]
    assert ids == ["29991231_2359", started["id"], "20000101_0000"]


def test_windows_sem_breakaway_recusa_disparo(monkeypatch):
    calls = []

    def popen(args, creationflags=0, **kwargs):
        calls.append(creationflags)
        if creationflags & 0x01000000:
            raise PermissionError(5, "Acesso negado")
        return types.SimpleNamespace(pid=1)

    monkeypatch.setattr(mcp_server, "WINDOWS", True)
    monkeypatch.setattr(mcp_server.subprocess, "Popen", popen)
    for name, value in (("CREATE_NEW_PROCESS_GROUP", 0x200), ("DETACHED_PROCESS", 0x8),
                        ("CREATE_BREAKAWAY_FROM_JOB", 0x01000000)):
        monkeypatch.setattr(mcp_server.subprocess, name, value, raising=False)
    with pytest.raises(PermissionError, match="Job Object"):
        mcp_server._spawn(["python"])
    assert calls == [0x01000208]


def test_pywin32_fixado_no_constraints():
    with open(os.path.join(REPO, "constraints.txt"), encoding="utf-8") as handle:
        pins = [line.split("#")[0].strip() for line in handle]
    assert any(re.fullmatch(r'pywin32==\d+ ; sys_platform == "win32"', pin) for pin in pins)


@pytest.mark.skipif(os.name == "nt", reason="symlink exige privilégio no Windows")
def test_status_tmp_symlink_nao_e_seguido(tmp_path):
    alvo = tmp_path / "alvo.txt"
    alvo.write_text("intacto", encoding="utf-8")
    run = tmp_path / "run"
    run.mkdir()
    (run / "mcp_status.tmp").symlink_to(alvo)
    mcp_runner.write_status(str(run), "concluida")
    assert alvo.read_text(encoding="utf-8") == "intacto"
    assert json.loads((run / mcp_server.STATUS_FILE).read_text())["estado"] == "concluida"
    assert not (run / "mcp_status.tmp").exists()


def test_config_invalida_remove_pasta_criada(tmp_path, monkeypatch):
    args, _ = _inputs(tmp_path, monkeypatch)
    monkeypatch.setattr(mcp_server, "validar_configuracao", lambda configuracao: {"valida": True, "erros": []})
    with pytest.raises(ValueError, match="zonas"):
        mcp_server.iniciar_simulacao({**args, "rooms": ["../fora"]})
    assert not [entry for entry in (tmp_path / "execucoes").iterdir() if entry.is_dir()]
