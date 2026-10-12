"""
Loads the Google service account used to read the Sheet from GCP Secret Manager.

The key is stored as a secret, not in the repo or on disk, and is read at runtime with the
caller's own identity (Application Default Credentials). If Secret Manager is unreachable,
it falls back to the local key file, so the nightly publish never breaks.
"""
from __future__ import annotations

import json
import logging
import os

log = logging.getLogger(__name__)

PROJECT = os.environ.get("JMI_GCP_PROJECT", "thyroid-panel-prasad0411")
SECRET = os.environ.get("JMI_SA_SECRET", "jmi-sheets-service-account")


def secret_name(project: str = PROJECT, secret: str = SECRET) -> str:
    return f"projects/{project}/secrets/{secret}/versions/latest"


def service_account_info(fallback_path: str = ".local/credentials.json") -> dict:
    try:
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        payload = client.access_secret_version(name=secret_name(), timeout=20).payload.data
        info = json.loads(payload.decode("utf-8"))
        log.info("service account loaded from Secret Manager")
        return info
    except Exception as exc:
        log.warning(f"Secret Manager unavailable ({type(exc).__name__}); using {fallback_path}")
        with open(fallback_path) as f:
            return json.load(f)
