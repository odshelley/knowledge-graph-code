import pytest
from kgc.config import Settings
from kgc.neo4j_io import doctor, DoctorReport, resolve_database


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def single(self):
        return self._rows[0] if self._rows else None


class _FakeSession:
    def __init__(self, ok_databases, counts):
        self._ok = ok_databases
        self._counts = counts
        self.database = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def run(self, query, **params):
        if self.database not in self._ok:
            raise RuntimeError(f"database {self.database!r} does not exist")
        if "count(n)" in query:
            return _FakeResult([{"nodes": self._counts[0]}])
        if "count(r)" in query:
            return _FakeResult([{"rels": self._counts[1]}])
        return _FakeResult([{"version": "5.28.0"}])


class _FakeDriver:
    def __init__(self, ok_databases, counts=(0, 0)):
        self._ok = ok_databases
        self._counts = counts

    def session(self, database=None):
        s = _FakeSession(self._ok, self._counts)
        s.database = database
        return s

    def close(self):
        pass


SETTINGS = Settings(
    neo4j_uri="neo4j+s://abc.databases.neo4j.io",
    neo4j_username="abc",
    neo4j_password="secret",
    neo4j_database="abc",
)


def test_resolve_database_prefers_configured_name():
    driver = _FakeDriver(ok_databases={"abc"})
    assert resolve_database(driver, "abc") == "abc"


def test_resolve_database_falls_back_to_neo4j():
    driver = _FakeDriver(ok_databases={"neo4j"})
    assert resolve_database(driver, "abc") == "neo4j"


def test_resolve_database_raises_when_neither_works():
    driver = _FakeDriver(ok_databases=set())
    with pytest.raises(RuntimeError, match="neither"):
        resolve_database(driver, "abc")


def test_doctor_reports_counts_and_database():
    driver = _FakeDriver(ok_databases={"neo4j"}, counts=(42, 99))
    report = doctor(SETTINGS, driver=driver)
    assert isinstance(report, DoctorReport)
    assert report.reachable is True
    assert report.database == "neo4j"
    assert report.node_count == 42
    assert report.relationship_count == 99
    assert "secret" not in report.message


def test_doctor_reports_unreachable_without_leaking_password():
    class _Broken:
        def session(self, database=None):
            raise OSError("connection refused to neo4j+s://abc with password secret")

        def close(self):
            pass

    report = doctor(SETTINGS, driver=_Broken())
    assert report.reachable is False
    assert "secret" not in report.message


def test_doctor_reports_unreachable_without_leaking_password_on_connect_failure(monkeypatch):
    def _broken_connect(settings):
        raise ValueError("bad uri neo4j+s://x with password secret")

    monkeypatch.setattr("kgc.neo4j_io.connect", _broken_connect)
    report = doctor(SETTINGS)
    assert report.reachable is False
    assert "secret" not in report.message
