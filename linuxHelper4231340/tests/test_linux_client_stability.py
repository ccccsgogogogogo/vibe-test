from __future__ import annotations

from typing import Any

import paramiko
import pytest

from os_agent.config import SSHConfig
from os_agent.execution.linux_client import LinuxCommandExecutor


class _FakeTransport:
    def __init__(self) -> None:
        self.keepalive_seconds: int | None = None

    def set_keepalive(self, seconds: int) -> None:
        self.keepalive_seconds = seconds


class _FakeStream:
    def __init__(self, text: str, exit_status: int = 0) -> None:
        self._text = text
        self.channel = self
        self._exit_status = exit_status

    def read(self) -> bytes:
        return self._text.encode("utf-8")

    def recv_exit_status(self) -> int:
        return self._exit_status


class _RetryThenSuccessSSHClient:
    connect_calls = 0

    def __init__(self) -> None:
        self._transport = _FakeTransport()

    def set_missing_host_key_policy(self, _policy: Any) -> None:
        return

    def connect(self, **_kwargs: Any) -> None:
        type(self).connect_calls += 1
        if type(self).connect_calls < 3:
            raise paramiko.SSHException("Error reading SSH protocol banner")

    def get_transport(self) -> _FakeTransport:
        return self._transport

    def exec_command(self, _command: str, timeout: int):
        _ = timeout
        return (
            None,
            _FakeStream("ok", exit_status=0),
            _FakeStream("", exit_status=0),
        )

    def close(self) -> None:
        return


class _AuthFailSSHClient:
    connect_calls = 0

    def set_missing_host_key_policy(self, _policy: Any) -> None:
        return

    def connect(self, **_kwargs: Any) -> None:
        type(self).connect_calls += 1
        raise paramiko.AuthenticationException("Authentication failed")

    def close(self) -> None:
        return


def _ssh_cfg() -> SSHConfig:
    return SSHConfig(host="127.0.0.1", port=22, username="ubuntu", password="pw")


def test_remote_connect_retries_for_banner_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _RetryThenSuccessSSHClient.connect_calls = 0

    monkeypatch.setattr("os_agent.execution.linux_client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("os_agent.execution.linux_client.paramiko.SSHClient", _RetryThenSuccessSSHClient)

    executor = LinuxCommandExecutor(_ssh_cfg())
    result = executor.run("echo ok", timeout=5)

    assert _RetryThenSuccessSSHClient.connect_calls == 3
    assert result.return_code == 0
    assert result.stdout == "ok"


def test_auth_failure_should_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    _AuthFailSSHClient.connect_calls = 0

    monkeypatch.setattr("os_agent.execution.linux_client.time.sleep", lambda _seconds: None)
    monkeypatch.setattr("os_agent.execution.linux_client.paramiko.SSHClient", _AuthFailSSHClient)

    executor = LinuxCommandExecutor(_ssh_cfg())

    with pytest.raises(paramiko.AuthenticationException):
        executor.run("echo ok", timeout=5)

    assert _AuthFailSSHClient.connect_calls == 1
