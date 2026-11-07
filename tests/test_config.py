"""Machine settings are found, not hard-coded: Java prefs parsing and the install search order."""

from pathlib import Path

from image2cash import config


def test_workspace_prefs_are_read_like_java_properties(tmp_path):
    prefs = tmp_path / "x.prefs"
    prefs.write_text(
        "#comment\n"
        r"GENERAL_WORKSPACE=D\:\\Fakturama_Test_Working" + "\n"
        r"jakarta.persistence.jdbc.url=jdbc\:hsqldb\:hsql\://localhost\:9001/fakdbneu" + "\n",
        encoding="latin-1",
    )
    p = config.read_prefs(prefs)
    assert p["GENERAL_WORKSPACE"] == r"D:\Fakturama_Test_Working"
    assert p["jakarta.persistence.jdbc.url"] == "jdbc:hsqldb:hsql://localhost:9001/fakdbneu"
    assert config.read_prefs(tmp_path / "missing.prefs") == {}


def test_install_search_order(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
    (b / "Fakturama.exe").write_text("")
    assert config.find_home(str(a), [b]) == a  # the setting always wins
    assert config.find_home(None, [lambda: None, a, b / "Fakturama.exe"]) == b  # first folder with the exe
    assert config.find_home(None, [a, lambda: None]) is None


def test_window_title_ignores_case_and_a_trailing_slash():
    import re
    from pathlib import Path

    from image2cash.ui.core import main_title

    pat = main_title(Path("F:/test"))
    for title in (r"Fakturama - F:\test", r"Fakturama - f:\TEST", "Fakturama - F:\\test\\"):
        assert re.match(pat, title), title
    for title in (r"Fakturama - F:\test2", r"Fakturama - F:\other", r"Fakturama - D:\Fakturama_Working_Directory"):
        assert not re.match(pat, title), title  # never another workspace's window
    assert not re.match(main_title(None), r"Fakturama - F:\test")


def test_a_new_workspace_is_picked_up_without_a_restart(tmp_path, monkeypatch):
    """The hang on a friend's PC: the app started before Fakturama first ran on the workspace."""
    monkeypatch.delenv("FAKTURAMA_WORKDIR", raising=False)
    monkeypatch.setattr(config, "PREFS_FILE", tmp_path / "fakturama.prefs")
    assert config.workdir() is None  # Fakturama has not run on it yet
    config.PREFS_FILE.write_text("GENERAL_WORKSPACE=F" + "\\" + ":" + "\\" + "\\" + "test\n", encoding="latin-1")
    assert str(config.workdir()) == "F:" + "\\" + "test"  # Fakturama asked, the user chose F:\test


def test_what_fakturama_saved_wins_over_env(tmp_path, monkeypatch):
    """The friend's .env named one working directory; Fakturama's first-run dialog saved another."""
    bs = "\\"
    monkeypatch.setenv("FAKTURAMA_WORKDIR", f"F:{bs}Games{bs}Fakturama2{bs}Fakturama_Test_Working")
    monkeypatch.setattr(config, "PREFS_FILE", tmp_path / "fakturama.prefs")
    assert config.first_run() and str(config.workdir()).endswith("Fakturama_Test_Working")  # fallback
    config.PREFS_FILE.write_text(f"GENERAL_WORKSPACE=F{bs}:{bs}{bs}test\n", encoding="latin-1")
    assert not config.first_run() and str(config.workdir()) == f"F:{bs}test"  # what the window shows


def test_launched_on_matches_only_the_test_workspace():
    from image2cash.ui.core import launched_on

    ws = Path(r"C:\Users\x\.fakturama2_test")
    assert launched_on(r"D:\F\Fakturama.exe -data C:\Users\x\.fakturama2_test", ws)
    assert launched_on(r'"D:\F\Fakturama.exe" -data "c:\users\x\.fakturama2_test\"', ws)
    assert not launched_on(r"D:\F\Fakturama.exe -data C:\Users\x\.fakturama2", ws)  # the live workspace
    assert not launched_on(r"D:\F\Fakturama.exe", ws)  # default workspace
    assert not launched_on(None, ws)


def test_an_installed_fakturama_is_found_whatever_its_folder_name(tmp_path, monkeypatch):
    """A laptop: Fakturama installed, never seen running, in a folder like 'Fakturama 2'."""
    install = tmp_path / "Fakturama 2"
    install.mkdir()
    (install / "Fakturama.exe").write_text("")
    monkeypatch.delenv("FAKTURAMA_HOME", raising=False)
    monkeypatch.setenv("ProgramFiles", str(tmp_path))
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    for name in ("_seen_running", "_last_seen", "_registered_exe"):
        monkeypatch.setattr(config, name, lambda: None)
    monkeypatch.setattr(config, "_installed_folders", lambda: [])
    monkeypatch.setattr(config.shutil, "which", lambda exe: None)
    assert config.fakturama_home.__wrapped__() == install
    # the installed-programs list finds it too, wherever it is
    elsewhere = tmp_path / "Apps" / "Fakt"
    elsewhere.mkdir(parents=True)
    (elsewhere / "Fakturama.exe").write_text("")
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "none"))
    monkeypatch.setattr(config, "_installed_folders", lambda: [elsewhere])
    assert config.fakturama_home.__wrapped__() == elsewhere
