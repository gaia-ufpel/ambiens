# Execução no Windows

Há dois caminhos: o **instalador** (para o usuário final) e a **instalação a
partir do código** (para quem vai mexer no projeto).

## A. Instalador (recomendado para o usuário final)

1. Instale o **EnergyPlus 9.4.0** —
   <https://github.com/NREL/EnergyPlus/releases/tag/v9.4.0> (instalador
   `Windows-x86_64.exe`), aceitando o diretório padrão `C:\EnergyPlusV9-4-0`.
   A versão **tem que ser a 9.4**: o programa usa o `pyenergyplus` que vem
   dentro da instalação, e as demais versões mudam essa API.
2. Baixe `Ambiens-<versão>-setup.exe` na página de *Releases* do
   repositório e execute. Não pede senha de administrador (instala em
   `%LOCALAPPDATA%\Ambiens`) e cria atalho no menu Iniciar.
3. Abra pelo atalho **Ambiens**. Python não é necessário: já vem
   embutido no executável. O caminho do EnergyPlus é detectado sozinho — se
   você instalou fora do padrão, use o botão **🔍 Detectar** ou **📂 Procurar**
   no campo "Caminho do EnergyPlus".

Onde ficam as coisas depois de instalado:

| O quê | Onde |
|---|---|
| Configuração da interface | `%LOCALAPPDATA%\Ambiens\config.json` |
| IDF e EPW de exemplo | pasta `_internal\examples` dentro da instalação |
| Saída padrão das simulações | `~/.local/share/Ambiens/execucoes` (ou `%LOCALAPPDATA%\Ambiens\execucoes`) |

Para desinstalar: *Configurações → Aplicativos → Ambiens*.

### Como o instalador é gerado

O build roda no GitHub Actions (`.github/workflows/windows-build.yml`), em
`windows-latest`, disparado por uma tag `v*` ou manualmente. Localmente, em um
Windows com Python e [Inno Setup 6](https://jrsoftware.org/isdl.php):

```bat
pip install -r requirements.txt pyinstaller
pyinstaller packaging\confortimetro.spec --noconfirm
iscc packaging\installer.iss
```

O `.exe` do instalador sai em `dist\`. O EnergyPlus **não** é embutido — o
`pyenergyplus` é carregado em tempo de execução da instalação da máquina.

### Armadilhas do empacotamento

Erros que já quebraram o build e o que os resolve:

| Erro | Causa |
|---|---|
| `script 'packaging/main.py' not found` | Caminhos dentro do `.spec` são resolvidos **em relação ao próprio `.spec`**, não ao diretório de onde o `pyinstaller` foi chamado. Por isso o spec ancora tudo em `SPECPATH`. |
| `Unknown preprocessor directive` (Inno) | Uma linha do `.iss` começando com `#13#10` é lida como diretiva do pré-processador. Quebras de linha só no **fim** da linha. |
| `Unknown identifier 'SLineBreak'` (Inno) | O Pascal Script do Inno não tem `SLineBreak`; use `#13#10`. |
| `collect_data_files - skipping ... not a package` | `esoreader` é um módulo solto, não um pacote — não passe pelo `collect_all`. |

### Versão

A versão é uma só, a do `version` no `pyproject.toml`. O workflow a lê de lá e
passa para o Inno Setup (`iscc /DAppVersion=...`), então o número no
instalador, em *Aplicativos e Recursos* e no nome do arquivo nunca divergem.

- **Release**: crie a tag `v<version>` — `v0.1.0` para `version = "0.1.0"`. Se a
  tag não bater com o `pyproject.toml`, o build falha de propósito, antes de
  publicar nada.
- **Build de teste** (`workflow_dispatch`, sem tag): a versão sai como
  `0.1.0-dev+<sha curto>`, para não se confundir com uma release.

Publicar uma versão nova: altere o `version` no `pyproject.toml`, commite, e
crie a tag `v` correspondente.

## B. Instalação a partir do código

### 1. Instalar os dois pré-requisitos

| Programa | Onde | Observação |
|---|---|---|
| **uv** (recomendado) | `winget install astral-sh.uv` | Baixa o Python 3.10 sozinho; dispensa instalar o Python. |
| ou **Python 3.10 ou 3.11** | <https://www.python.org/downloads/windows/> | Só sem o uv. Marque **"Add python.exe to PATH"**. O 3.12 ou mais novo **não serve**: o `numpy==1.24.3` não tem wheel para ele. |
| **EnergyPlus 9.4.0** | <https://github.com/NREL/EnergyPlus/releases/tag/v9.4.0> (instalador `Windows-x86_64.exe`) | Aceite o diretório padrão `C:\EnergyPlusV9-4-0`. |

A versão do EnergyPlus **tem que ser a 9.4**: o programa usa o `pyenergyplus`
que vem dentro da instalação, e as demais versões mudam essa API.

### 2. Baixar o projeto

Baixe o repositório como ZIP e extraia para uma pasta sem acentos nem espaços,
por exemplo `C:\confortimetro`.

### 3. Instalar as dependências

Duplo-clique em **`bin\install.bat`**. Ele cria o ambiente virtual `.venv-win` dentro da
pasta do projeto (com o uv, se estiver no PATH; senão, com o `py`) e instala as
bibliotecas Python. Para desenvolver, instale também as ferramentas de verificação:

```powershell
uv venv .venv-win --python 3.10          # o install.bat já faz isto
uv pip install --python .venv-win\Scripts\python.exe -r requirements-dev.txt -c constraints.txt
.venv-win\Scripts\python -m pytest tests -q
```

O nome é `.venv-win`, e não `.venv`, porque a pasta pode ser a mesma do Linux
(ver abaixo). Leva alguns minutos na
primeira vez. A janela mostra `[OK] Instalacao concluida` ao terminar.

### 4. Executar

Duplo-clique em **`bin\executar.bat`**. A interface abre.

Na aba de caminhos, confira:

- **Caminho do EnergyPlus**: `C:\EnergyPlusV9-4-0` (preenchido automaticamente
  se a instalação estiver no lugar padrão).
- **Arquivo IDF** e **Arquivo EPW**: os exemplos ficam em `examples\idf` e `examples\epw`.
- **Diretório de saída**: uma pasta nova por execução, ex.: `outputs\run_001`.

## Diferenças em relação ao Linux

O código já trata o que muda entre os sistemas:

- `ExpandObjects.exe` e `ReadVarsESO.exe` no lugar dos binários sem extensão.
- Caminhos montados com `os.path.join`, não com `/` fixo.
- `energy_path` cai no padrão da plataforma (`C:\EnergyPlusV9-4-0`) quando o
  caminho gravado no `resources\config.json` não existe na máquina.

## Mesma pasta no Linux e no Windows (dual boot)

Quem desenvolve nos dois sistemas sobre a mesma árvore (a partição ext4 do Linux
montada no Windows, por exemplo `E:\home\<usuário>\Projetosmbiens`):

- **Um venv por sistema.** O `.venv` é do Linux (`.venv/bin`), o `.venv-win` é do
  Windows (`.venv-win\Scripts`). Criar um venv do Windows dentro do `.venv`
  sobrescreve o `pyvenv.cfg` e quebra o do Linux. O `executar.bat` e o
  `scripts/quality.mjs` procuram o `.venv-win` primeiro.
- **Git recusa o repositório** (`detected dubious ownership`): os arquivos são
  do usuário do Linux. Uma vez por máquina:
  `git config --global --add safe.directory E:/home/<usuário>/Projetos/ambiens`.
- **Fim de linha.** O `.gitattributes` fixa LF para texto (CRLF só nos `.bat`)
  e guarda IDF, EPW e CSV byte a byte; por garantia,
  `git config core.autocrlf false` no repositório — o Git for Windows vem com
  `true`.
- **As execuções ficam separadas**: `%LOCALAPPDATA%\Ambiens` no Windows e
  `~/.local/share/Ambiens` no Linux. Para ver as mesmas nos dois, aponte
  `AMBIENS_DATA_DIR` no Windows para a pasta do Linux
  (`setx AMBIENS_DATA_DIR "E:\home\<usuário>\.local\share\Ambiens\execucoes"`).
- **O hook de `pre-push`** (Husky) precisa do Node no Windows; sem ele, dê push
  pelo Linux.

## Problemas comuns

| Sintoma | Causa provável |
|---|---|
| `[ERRO] Nem uv nem Python encontrados` no `bin\install.bat` | Instale o uv (`winget install astral-sh.uv`) e abra um terminal novo, ou instale o Python 3.10/3.11 marcando "Add python.exe to PATH". |
| `py` diz `No Installed Pythons Found` com Python no PATH | O Python registrado está num disco desmontado ou removido. Use o uv. |
| `No module named 'pyenergyplus'` | Caminho do EnergyPlus errado na interface, ou EnergyPlus não instalado. Clique em **🔍 Detectar**. |
| `IDD file not found` | O **Caminho do EnergyPlus** precisa apontar para a raiz da instalação (a pasta que contém `Energy+.idd`), não para uma subpasta. O status do campo avisa antes de simular. |
| A janela fecha na hora | Rode pelo `bin\executar.bat` (ele mantém a janela aberta e mostra o erro), não por `python main.py` no Explorer. |
| Simulação parece travada | Uma simulação anual leva de dezenas de minutos a horas. O progresso do EnergyPlus sai na janela preta do `bin\executar.bat`. |

## Uma execução por pasta

`in.idf` e `expanded.idf` são gravados na pasta do IDF de entrada, e o IDF de
entrada é modificado no lugar. Não rode duas simulações ao mesmo tempo sobre o
mesmo IDF — copie o modelo para uma pasta por execução.
