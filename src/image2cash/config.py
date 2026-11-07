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


def _installed_folders() -> list[Path]:
    """Folders of every program named Fakturama in Windows' installed-programs list (the
    Uninstall keys an installer writes): InstallLocation, else the folder of its icon or
    uninstaller. Covers any install folder name, e.g. 'C:\\Program Files\\Fakturama 2'."""
    out: list[Path] = []
    try:
        import winreg
    except ImportError:
        return out
    uninstall = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
    keys = [
        (winreg.HKEY_LOCAL_MACHINE, uninstall),
        (winreg.HKEY_LOCAL_MACHINE, uninstall.replace("SOFTWARE", r"SOFTWARE\WOW6432Node", 1)),
        (winreg.HKEY_CURRENT_USER, uninstall),
    ]
    for hive, path in keys:
        try:
            root = winreg.OpenKey(hive, path)
        except OSError:
            continue
        with root:
            for i in range(winreg.QueryInfoKey(root)[0]):
                try:
                    with winreg.OpenKey(root, winreg.EnumKey(root, i)) as k:
                        if "fakturama" not in str(winreg.QueryValueEx(k, "DisplayName")[0]).lower():
                            continue
                        for value in ("InstallLocation", "DisplayIcon", "UninstallString"):
                            try:
                                v = str(winreg.QueryValueEx(k, value)[0]).strip().strip('"').split(",")[0]
                            except OSError:
                                continue
                            if v:
                                p = Path(v.strip('"'))
                                out.append(p.parent if p.suffix.lower() == ".exe" else p)
                except OSError:
                    continue
    return out


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
    # any 'Fakturama*' folder there ('Fakturama2', 'Fakturama 2', 'Fakturama'), newest name last
    usual = sorted(d for r in roots if r for sub in ("", "Programs") for d in (Path(r) / sub).glob("Fakturama*"))
    found = [_seen_running, _last_seen, lambda: shutil.which(EXE), _registered_exe, *_installed_folders()]
    return find_home(os.getenv("FAKTURAMA_HOME"), found + usual[::-1])


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
# They are read on every call, not once: a new workspace gets them only when Fakturama first
# runs on it and asks for a working directory, which can be after this app has started.
PREFS_FILE = WORKSPACE / ".metadata/.plugins/org.eclipse.core.runtime/.settings/com.sebulli.fakturama.rcp.prefs"


def workdir() -> Path | None:
    """Fakturama's working directory for WORKSPACE. Its window is titled 'Fakturama - <workdir>'
    and only that window is ever driven (another open Fakturama, e.g. the live one, is left
    alone). What Fakturama saved wins: it is what its window shows. FAKTURAMA_WORKDIR is only
    a fallback for a workspace Fakturama has not saved one for. None until then."""
    v = read_prefs(PREFS_FILE).get("GENERAL_WORKSPACE") or os.getenv("FAKTURAMA_WORKDIR")
    return Path(v) if v else None


def first_run() -> bool:
    """Fakturama has never opened WORKSPACE: starting it shows the working-directory dialog."""
    return not PREFS_FILE.is_file()


def db_url() -> str:
    return (
        os.getenv("HSQL_URL")
        or read_prefs(PREFS_FILE).get("jakarta.persistence.jdbc.url")
        or "jdbc:hsqldb:hsql://localhost:9001/fakdbneu"
    )


def db_login() -> tuple[str, str]:
    p = read_prefs(PREFS_FILE)
    user = os.getenv("HSQL_USER") or p.get("jakarta.persistence.jdbc.user") or "SA"
    return user, os.getenv("HSQL_PASSWORD", p.get("jakarta.persistence.jdbc.password", ""))


RUNS_DIR = Path(os.getenv("IMAGE2CASH_RUNS", Path(__file__).resolve().parents[2] / "runs"))

# Opt-in AI fallback (a vision language model): any OpenAI-compatible chat-completions API.
# The default is OpenRouter; point LLM_BASE_URL at a local server (Ollama, LM Studio) to keep
# images on this machine. Usually set in .env (see .env.example).
LLM_BASE_URL = os.getenv("LLM_BASE_URL", "https://openrouter.ai/api/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "nvidia/nemotron-3.5-super-vl")
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
