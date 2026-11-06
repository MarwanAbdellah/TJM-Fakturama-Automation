"""Machine settings are found, not hard-coded: Java prefs parsing and the install search order."""

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
