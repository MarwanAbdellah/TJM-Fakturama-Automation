"""Machine-specific settings, found on this machine rather than hard-coded.

Every value can be set in .env or the environment; otherwise it is discovered:
the Fakturama install from the running Fakturama.exe, PATH, the registry or the usual
install folders; the working directory and database from the test workspace's own
Fakturama preferences.
"""

import os
import re
import shutil
from functools import cache
from pathlib import Path

from dotenv import load_dotenv

# .env in the project root; a variable already set in the shell wins (override=False)
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

EXE = "Fakturama.exe"


def read_prefs(path: Path) -> dict[str, str]:
    """A Java .properties file ('D\\:\\\\Work' -> 'D:\\Work'); empty if it does not exist."""
    out: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="latin-1").splitlines()
    except OSError:
        return out
    for line in lines:
        if "=" in line and not line.lstrip().startswith(("#", "!")):
            key, value = line.split("=", 1)
            out[key.strip()] = re.sub(r"\\(.)", r"\1", value.strip())
    return out


def _running_exe() -> Path | None:
    """Fakturama.exe of a running Fakturama (any workspace): the install is the same."""
    try:
        from pywinauto.application import process_get_modules

        return next(Path(p) for _, p, _ in process_get_modules() if p and Path(p).name.lower() == EXE.lower())
    except Exception:  # noqa: BLE001 - not running, or the process list is unreadable
        return None


def _registered_exe() -> Path | None:
    """Fakturama.exe as Windows knows it (Win+R 'Fakturama.exe' uses App Paths)."""
    try:
        import winreg

        for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            try:
                return Path(winreg.QueryValue(hive, rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{EXE}"))
            except OSError:
                continue
    except ImportError:
        pass
    return None


def find_home(env: str | None, candidates) -> Path | None:
    """The first folder that holds Fakturama.exe: the setting, else each candidate exe/folder."""
    if env:
        return Path(env)
    for c in candidates:
        c = c() if callable(c) else c
        if c is None:
            continue
        c = Path(c)
        home = c.parent if c.suffix.lower() == ".exe" else c
        if (home / EXE).is_file():
            return home
    return None


def _remembered() -> Path:
    # per machine, git-ignored: where Fakturama.exe was last seen running
    return Path(os.getenv("IMAGE2CASH_RUNS", Path(__file__).resolve().parents[2] / "runs")) / ".fakturama_home"


def _seen_running() -> Path | None:
    exe = _running_exe()
    if exe is not None:
        try:
            _remembered().parent.mkdir(parents=True, exist_ok=True)
            _remembered().write_text(str(exe.parent), encoding="utf-8")
        except OSError:
            pass
    return exe


def _last_seen() -> Path | None:
    try:
        return Path(_remembered().read_text(encoding="utf-8").strip())
    except OSError:
        return None


@cache
def fakturama_home() -> Path | None:
    """Fakturama's install folder, or None if it cannot be found (then set FAKTURAMA_HOME).
    An unzipped Fakturama is registered nowhere, so where it last ran is remembered."""
    roots = [os.getenv(v) for v in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA")]
    usual = [
        Path(r) / sub / name for r in roots if r for sub in ("", "Programs") for name in ("Fakturama2", "Fakturama")
    ]
    found = [_seen_running, _last_seen, lambda: shutil.which(EXE), _registered_exe, *usual]
    return find_home(os.getenv("FAKTURAMA_HOME"), found)


def hsql_jar() -> Path | None:
    """The HSQLDB driver Fakturama ships (any version), unless HSQL_JAR is set."""
    if os.getenv("HSQL_JAR"):
        return Path(os.environ["HSQL_JAR"])
    home = fakturama_home()
    jars = sorted((home / "plugins").glob("org.hsqldb.hsqldb_*.jar")) if home else []
    return jars[-1] if jars else None


# Eclipse workspace (-data) the automation runs against: a copy, never the live one.
WORKSPACE = Path(os.getenv("FAKTURAMA_WORKSPACE") or Path.home() / ".fakturama2_test")
# Fakturama keeps its working directory and database login in the workspace's preferences.
PREFS = read_prefs(WORKSPACE / ".metadata/.plugins/org.eclipse.core.runtime/.settings/com.sebulli.fakturama.rcp.prefs")
# Its window is titled 'Fakturama - <WORKDIR>' and only that window is ever driven (another
# open Fakturama, e.g. the live one, is left alone). None until the workspace was opened once.
_workdir = os.getenv("FAKTURAMA_WORKDIR") or PREFS.get("GENERAL_WORKSPACE")
WORKDIR = Path(_workdir) if _workdir else None
HSQL_URL = (
    os.getenv("HSQL_URL") or PREFS.get("jakarta.persistence.jdbc.url") or "jdbc:hsqldb:hsql://localhost:9001/fakdbneu"
)
HSQL_USER = os.getenv("HSQL_USER") or PREFS.get("jakarta.persistence.jdbc.user") or "SA"
HSQL_PASSWORD = os.getenv("HSQL_PASSWORD", PREFS.get("jakarta.persistence.jdbc.password", ""))

RUNS_DIR = Path(os.getenv("IMAGE2CASH_RUNS", Path(__file__).resolve().parents[2] / "runs"))

# Opt-in AI fallback (a vision language model): any OpenAI-compatible chat-completions API.
# The default is OpenRouter; point VLM_BASE_URL at a local server (Ollama, LM Studio) to keep
# images on this machine. Usually set in .env (see .env.example).
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "https://openrouter.ai/api/v1")
VLM_MODEL = os.getenv("VLM_MODEL", "nvidia/nemotron-3.5-super-vl")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
