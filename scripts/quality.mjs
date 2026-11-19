import { existsSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { platform } from "node:os";

// No Windows o venv é o .venv-win: a pasta pode ser a mesma do Linux (dual boot),
// e o .venv de lá não roda aqui. O .venv\Scripts fica como fallback.
const candidates =
  platform() === "win32"
    ? [".venv-win\\Scripts\\python.exe", ".venv\\Scripts\\python.exe"]
    : [".venv/bin/python"];
const python = candidates.find((path) => existsSync(path));

if (!python) {
  const hint = platform() === "win32" ? "bin\\install.bat (ou: uv venv .venv-win --python 3.10)" : "./bin/install.sh";
  console.error(`Ambiente virtual não encontrado em ${candidates.join(" nem ")}. Execute: ${hint}`);
  process.exit(1);
}

const checks = {
  lint: ["-m", "ruff", "check", "."],
  // A base ainda não é integralmente anotada. Verifica os pontos de entrada
  // sem transformar módulos legados sem tipagem em bloqueio de push.
  typecheck: ["-m", "mypy", "--follow-imports=skip", "--ignore-missing-imports", "cli.py", "main.py"],
  test: ["-m", "pytest", "tests", "-q"],
};

const selected = process.argv.slice(2);
const names = selected.length ? selected : Object.keys(checks);

for (const name of names) {
  if (!(name in checks)) {
    console.error(`Verificação desconhecida: ${name}`);
    process.exit(1);
  }
  console.log(`\n> ${name}`);
  execFileSync(python, checks[name], { stdio: "inherit" });
}
