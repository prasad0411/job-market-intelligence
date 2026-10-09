"""Secret Manager credentials (with file fallback) and BigQuery table preparation."""
import json
import sys
import types

from dashboard_api import bq_load, gcp_secrets


def _fake_google(monkeypatch, secretmanager=None, bigquery=None):
    google = types.ModuleType("google"); cloud = types.ModuleType("google.cloud")
    google.cloud = cloud
    monkeypatch.setitem(sys.modules, "google", google)
    monkeypatch.setitem(sys.modules, "google.cloud", cloud)
    if secretmanager:
        cloud.secretmanager = secretmanager
        monkeypatch.setitem(sys.modules, "google.cloud.secretmanager", secretmanager)
    if bigquery:
        cloud.bigquery = bigquery
        monkeypatch.setitem(sys.modules, "google.cloud.bigquery", bigquery)


def test_secret_manager_is_used_when_available(monkeypatch, tmp_path):
    sm = types.ModuleType("secretmanager")
    calls = {}

    class Client:
        def access_secret_version(self, name, timeout):
            calls["name"] = name
            payload = types.SimpleNamespace(data=json.dumps({"client_email": "sa@x"}).encode())
            return types.SimpleNamespace(payload=payload)
    sm.SecretManagerServiceClient = Client
    _fake_google(monkeypatch, secretmanager=sm)
    assert gcp_secrets.service_account_info(str(tmp_path / "missing.json")) == {"client_email": "sa@x"}
    assert calls["name"].endswith("/secrets/jmi-sheets-service-account/versions/latest")


def test_falls_back_to_the_key_file(monkeypatch, tmp_path):
    sm = types.ModuleType("secretmanager")

    class Client:
        def access_secret_version(self, name, timeout):
            raise PermissionError("denied")
    sm.SecretManagerServiceClient = Client
    _fake_google(monkeypatch, secretmanager=sm)
    key = tmp_path / "credentials.json"
    key.write_text(json.dumps({"client_email": "file@x"}))
    assert gcp_secrets.service_account_info(str(key)) == {"client_email": "file@x"}


def test_prepare_tables_types_dates_for_partitioning():
    snap = {"jobs": [{"id": 1, "entry_date": "2026-10-05", "source": "SWE List"},
                     {"id": 2, "entry_date": None, "source": "Indeed"}],
            "weekly": [{"week": "2026-W41", "postings": 3}], "companies": [], "sources": []}
    t = bq_load.prepare_tables(snap)
    assert set(t) == {"postings", "weekly_ingest", "companies", "sources"}
    assert str(t["postings"].loc[0, "entry_date"]) == "2026-10-05"
    assert t["postings"]["entry_date"].isna().sum() == 1


def test_bigquery_failure_never_raises(monkeypatch):
    bq = types.ModuleType("bigquery")

    def boom(project):
        raise RuntimeError("no credentials")
    bq.Client = boom
    _fake_google(monkeypatch, bigquery=bq)
    assert bq_load.load_snapshot({"jobs": [], "weekly": [], "companies": [], "sources": []}) is False
