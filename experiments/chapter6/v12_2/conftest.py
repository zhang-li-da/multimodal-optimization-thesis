"""Every readiness test runs with network and real provider access forbidden."""
import socket
import urllib.request

import pytest

from chapter6_demo.providers import ModelClient
from chapter6_demo.v12_2 import calls


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Readiness tests must not call a model or network.")
    monkeypatch.setattr(ModelClient, "from_opencode", forbidden)
    monkeypatch.setattr(ModelClient, "complete", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    monkeypatch.setattr(calls, "urlopen", forbidden)
