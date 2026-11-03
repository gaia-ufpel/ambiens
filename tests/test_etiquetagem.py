"""Etiquetagem INI-C: tabelas, modelos gerados a partir do IDF e a nota."""

import os

import pytest

from confortimetro.etiquetagem import avaliacao, execucao, modelos, norma

SALA = os.path.join(os.path.dirname(__file__), "..", "examples", "idf", "SALA", "SALA_PTHP.idf")
PELOTAS = os.path.join(os.path.dirname(__file__), "..", "examples", "epw",
                       "BRA_RS_Pelotas-2003-2017.epw")


def test_limites_e_classes():
    # Tabela 8.13, ZB 2, FF > 0,40: CRCgTT 0,29 → i = 9,67 %.
    limites = norma.limites_envoltoria("educacional", 2, 0.43)
    assert limites["cr"] == 0.29
    assert limites["A"] == pytest.approx(29.0)
    assert norma.classe_envoltoria(29.5, limites) == "A"
    assert norma.classe_envoltoria(20.0, limites) == "B"
    assert norma.classe_envoltoria(10.0, limites) == "C"
    assert norma.classe_envoltoria(0.0, limites) == "D"
    assert norma.classe_envoltoria(-0.1, limites) == "E"
    assert norma.faixa_fator_forma(0.20) == 0 and norma.faixa_fator_forma(0.21) == 1


def test_conforto_adaptativo_fora_da_faixa_nao_conta():
    assert norma.conforto_adaptativo(0.31 * 20 + 17.8, 20)
    assert not norma.conforto_adaptativo(0.31 * 20 + 17.8 + 3.6, 20)
    assert not norma.conforto_adaptativo(21.0, 9.0)


def test_zona_bioclimatica_pelo_epw():
    assert execucao.zona_sugerida(PELOTAS) == (2, "Pelotas/RS")


def _objetos(caminho, tipo):
    with open(caminho, encoding="latin-1") as arquivo:
        return modelos._Idf(arquivo.read()).todos(tipo)


@pytest.mark.parametrize("papel", list(modelos.PAPEIS))
def test_modelos_da_sala(tmp_path, papel):
    destino = str(tmp_path / f"{papel}.idf")
    avisos = modelos.gerar_modelo(SALA, destino, papel, ["SALA"], "educacional",
                                  "Ensino superior", True)

    assert not _objetos(destino, "HVACTemplate:Zone:PTHP")
    ideais = _objetos(destino, "HVACTemplate:Zone:IdealLoadsAirSystem")
    zonas_afn = {z["campos"][0].upper(): z["campos"][1]
                 for z in _objetos(destino, "AirflowNetwork:MultiZone:Zone")}
    if papel == "real_vn":
        assert not ideais and zonas_afn["SALA"] == "Temperature"
    else:
        assert [i["campos"][0] for i in ideais] == ["SALA"]
        assert ideais[0]["campos"][20:23] == ["Sum", "0.005", "0.0006"]
        assert set(zonas_afn.values()) == {"NoVent"}

    pessoas = _objetos(destino, "People")
    assert all(p["campos"][2] == modelos.OCUPACAO and p["campos"][19:] == ["AdaptiveASH55"]
               for p in pessoas)
    periodo = _objetos(destino, "RunPeriod")[0]["campos"]
    assert periodo[1:3] == ["1", "1"] and periodo[4:6] == ["12", "31"]

    if papel != "referencia":
        return
    superficies = modelos._superficies(modelos._Idf(open(destino, encoding="latin-1").read()))
    paredes = [pontos for obj, pontos in superficies.values()
               if obj["campos"][1].lower() == "wall" and obj["campos"][4].lower() == "outdoors"]
    assert {obj["campos"][2] for obj, _ in superficies.values()
            if obj["campos"][1].lower() == "wall"} == {modelos.PAREDE}
    janelas = [j for j in _objetos(destino, "FenestrationSurface:Detailed")
               if j["campos"][1].lower() == "window"]
    assert {j["campos"][2] for j in janelas} == {modelos.JANELA}
    paf = sum(modelos._area(modelos._vertices(j["campos"], 9)) for j in janelas) / \
        sum(modelos._area(p) for p in paredes)
    # A SALA só tem janela numa fachada: a faixa ocupa a parede e o aviso diz o PAF.
    mensagem = next(a for a in avisos if a.startswith("Zona SALA: PAF"))
    assert f"→ {paf * 100:.1f} %" in mensagem
    assert 0.10 < paf <= norma.TIPOLOGIAS["educacional"]["paf"] + 0.005


def test_paf_reduz_quando_passa(tmp_path):
    # Tipologia com PAF menor que o da janela original não amplia nada.
    origem = tmp_path / "sala.idf"
    with open(SALA, encoding="latin-1") as arquivo:
        origem.write_text(arquivo.read(), encoding="latin-1")
    idf = modelos._Idf(origem.read_text(encoding="latin-1"))
    avisos = []
    modelos.ajustar_paf(idf, {"SALA"}, 0.05, avisos)
    assert "reduzida" in avisos[0] and "→ 5.0 %" in avisos[0]


def test_paf_do_varejo_por_fachada():
    # A SALA tem janelas nas paredes norte e sul.
    with open(SALA, encoding="latin-1") as arquivo:
        texto = arquivo.read()
    avisos = []
    modelos.ajustar_paf(modelos._Idf(texto), {"SALA"}, (0.60, 0.05), avisos, "N")
    principal = next(a for a in avisos if a.startswith("Zona SALA (fachada principal)"))
    demais = next(a for a in avisos if a.startswith("Zona SALA (demais fachadas)"))
    assert "→ 60.0 %" in principal and "→ 5.0 %" in demais
    # Voltada a leste, nenhuma parede com janela é a principal.
    avisos = []
    modelos.ajustar_paf(modelos._Idf(texto), {"SALA"}, (0.60, 0.05), avisos, "L")
    assert not any("fachada principal" in a for a in avisos)
    with pytest.raises(ValueError, match="fachada principal"):
        modelos.gerar_modelo(SALA, "/dev/null", "real", ["SALA"], "varejo", "Comércio", True)
    assert set(norma.TIPOLOGIAS) == set(norma.CRCGTT)


def test_janelas_da_mesma_parede_nao_se_sobrepoem():
    parede = [(0, 0, 3), (0, 0, 0), (10, 0, 0), (10, 0, 3)]
    a = [(1, 0, 2), (1, 0, 1), (2, 0, 1), (2, 0, 2)]
    b = [(3, 0, 2), (3, 0, 1), (4, 0, 1), (4, 0, 2)]
    dados = [[{"campos": ["A", "Window", "", "P"]}, a, 1.0, 10.0],
             [{"campos": ["B", "Window", "", "P"]}, b, 1.0, 10.0]]
    modelos._limitar_vizinhas(dados, {"P": ("Z", parede)})
    # Centros a 2 m, meias larguras somando 1 m: escala até ~2.
    assert dados[0][3] == pytest.approx(2 - modelos.FOLGA_BORDA)


def test_hibrido_desconta_so_a_refrigeracao(monkeypatch, tmp_path):
    reais = {"SALA": {"resfriamento": 1000.0, "aquecimento": 100.0, "total": 1100.0}}
    referencia = {"SALA": {"resfriamento": 2000.0, "aquecimento": 200.0, "total": 2200.0}}
    monkeypatch.setattr(avaliacao, "cargas",
                        lambda eso, zonas: reais if "real" in eso.split(os.sep)[-2] else referencia)
    monkeypatch.setattr(avaliacao, "phoct", lambda eso, zonas: {"SALA": 75.0})
    pastas = {papel: str(tmp_path / papel) for papel in ("real", "real_vn", "referencia")}
    resultado = avaliacao.avaliar(pastas, ["SALA"], "educacional", 2, SALA)
    assert resultado["cgtt_real"] == pytest.approx(1000 * 0.25 + 100)
    assert resultado["cgtt_ref"] == 2200
    assert resultado["phoct"] == pytest.approx(75.0) and resultado["modo"] == "hibrido"

    monkeypatch.setattr(avaliacao, "phoct", lambda eso, zonas: {"SALA": 92.0})
    assert avaliacao.avaliar(pastas, ["SALA"], "educacional", 2, SALA)["classe"] == "A"

    avaliacao.gravar(resultado, [str(tmp_path)])
    assert avaliacao.ler(str(tmp_path))["classe"] == resultado["classe"]
    assert "classe" in execucao.resumo(resultado)


def test_geometria_nao_suportada(tmp_path):
    idf = tmp_path / "simples.idf"
    idf.write_text("Zone, Z;\nWall:Exterior, W, C, Z, 0, 90, 0, 0, 0, 3, 3;\n"
                   "RunPeriod, R, 1, 1, , 12, 31;\n", encoding="latin-1")
    with pytest.raises(ValueError, match="geometria simplificada"):
        modelos.gerar_modelo(str(idf), str(tmp_path / "x.idf"), "real", ["Z"],
                             "educacional", "Ensino superior", True)


def test_painel_e_cartao_da_nota(tmp_path):
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("sem display para o Tk")
    try:
        from confortimetro.gui.components.comparison_panel import ComparisonPanel
        from confortimetro.gui.components.labeling_panel import LabelingPanel
        from confortimetro.gui.theme import apply_theme

        apply_theme(root)
        panel = LabelingPanel(root)
        assert panel.get_options()["zb"] is None   # automática por padrão
        opcoes = {"tipologia": "escritorio", "uso": "Escritório", "modo": "hibrido", "zb": 3}
        # A config de uma execução do grupo traz mais chaves; só as da norma voltam.
        panel.set_options(dict(opcoes, papel="real", grupo="g"))
        assert panel.get_options() == opcoes
        varejo = {"tipologia": "varejo", "uso": "Comércio", "modo": "condicionado",
                  "zb": None, "fachada": "NE"}
        panel.set_options(varejo)
        assert panel.get_options() == varejo
        # Uso de outra tipologia não fica preso no combobox.
        panel.set_options(dict(varejo, uso="Escritório"))
        assert panel.get_options()["uso"] == "Comércio"

        resultado = {"classe": "B", "modo": "condicionado", "red_cgtt": 20.0,
                     "limites": norma.limites_envoltoria("educacional", 2, 0.43),
                     "zb": 2, "ff": 0.43, "cgtt_real": 80.0, "cgtt_ref": 100.0, "phoct": None,
                     "zonas": [{"zona": "SALA", "cgtt_real": 80.0, "phoct": None,
                                "referencia": {"total": 100.0}}],
                     "avisos": ["Zona SALA: PAF 10.0 % → 40.0 %."],
                     "aviso_estimativa": norma.AVISO_ESTIMATIVA}
        runs = []
        for papel in ("real", "referencia"):
            pasta = tmp_path / papel
            pasta.mkdir()
            avaliacao.gravar(resultado, [str(pasta)])
            runs.append({"run": papel, "path": str(pasta), "status": "sem planilhas",
                         "rooms_disponiveis": [], "nome": papel})
        comparison = ComparisonPanel(root)
        comparison.pack()
        comparison.set_runs(runs, str(tmp_path))
        root.update()
        assert comparison.labeling_card.winfo_ismapped()
        assert "classe B" in comparison.labeling_title.cget("text")
    finally:
        root.destroy()
