"""Machine-specific settings. Every value can be overridden by an environment variable."""

import os
from pathlib import Path

FAKTURAMA_HOME = Path(os.getenv("FAKTURAMA_HOME", r"D:\Fakturama\Fakturama2"))
FAKTURAMA_EXE = FAKTURAMA_HOME / "Fakturama.exe"
HSQL_JAR = Path(os.getenv("HSQL_JAR", str(FAKTURAMA_HOME / "plugins" / "org.hsqldb.hsqldb_2.7.4.jar")))
HSQL_URL = os.getenv("HSQL_URL", "jdbc:hsqldb:hsql://localhost:9001/fakdbneu")
HSQL_USER = os.getenv("HSQL_USER", "SA")
HSQL_PASSWORD = os.getenv("HSQL_PASSWORD", "")

# Eclipse workspace (-data) the automation is meant to run against. Use a copy.
WORKSPACE = Path(os.getenv("FAKTURAMA_WORKSPACE", r"C:\Users\kimi\.fakturama2_test"))

RUNS_DIR = Path(os.getenv("IMAGE2CASH_RUNS", Path(__file__).resolve().parents[2] / "runs"))
