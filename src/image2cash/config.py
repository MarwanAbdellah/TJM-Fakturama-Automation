"""Machine-specific settings. Every value can be overridden by an environment variable."""

import os
from pathlib import Path

from dotenv import load_dotenv

# .env in the project root; a variable already set in the shell wins (override=False)
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)

FAKTURAMA_HOME = Path(os.getenv("FAKTURAMA_HOME", r"D:\Fakturama\Fakturama2"))
FAKTURAMA_EXE = FAKTURAMA_HOME / "Fakturama.exe"
HSQL_JAR = Path(os.getenv("HSQL_JAR", str(FAKTURAMA_HOME / "plugins" / "org.hsqldb.hsqldb_2.7.4.jar")))
HSQL_URL = os.getenv("HSQL_URL", "jdbc:hsqldb:hsql://localhost:9001/fakdbneu")
HSQL_USER = os.getenv("HSQL_USER", "SA")
HSQL_PASSWORD = os.getenv("HSQL_PASSWORD", "")

# Eclipse workspace (-data) the automation is meant to run against. Use a copy.
WORKSPACE = Path(os.getenv("FAKTURAMA_WORKSPACE", r"C:\Users\kimi\.fakturama2_test"))
# Fakturama's working directory for that workspace. Its window is titled 'Fakturama - <WORKDIR>'
# and only that window is ever driven (another open Fakturama, e.g. the live one, is left alone).
WORKDIR = Path(os.getenv("FAKTURAMA_WORKDIR", r"D:\Fakturama_Test_Working"))

RUNS_DIR = Path(os.getenv("IMAGE2CASH_RUNS", Path(__file__).resolve().parents[2] / "runs"))

# Opt-in AI fallback (a vision language model): any OpenAI-compatible chat-completions API.
# The default is Gemini; point VLM_BASE_URL at a local server (Ollama, LM Studio) to keep
# images on this machine. Usually set in .env (see .env.example).
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
VLM_MODEL = os.getenv("VLM_MODEL", "gemini-3.6-flash")
VLM_API_KEY = os.getenv("VLM_API_KEY", "")
