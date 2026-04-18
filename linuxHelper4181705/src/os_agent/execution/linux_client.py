from __future__ import annotations

import subprocess
from dataclasses import dataclass
from typing import Optional

import paramiko

from os_agent.config import SSHConfig


@dataclass
class LinuxCommandResult:
    """命令执行结果对象。"""

    command: str
    return_code: int
    stdout: str
    stderr: str


class LinuxCommandExecutor:
    """Linux 命令执行器，支持本地与 SSH 远程两种模式。"""

    def __init__(self, ssh: Optional[SSHConfig] = None) -> None:
        self.ssh = ssh

    def run(self, command: str, timeout: int = 60) -> LinuxCommandResult:
        """统一执行入口：存在 SSH 配置则远程，否则本地执行。"""

        if self.ssh and self.ssh.host:
            return self._run_remote(command, timeout)
        return self._run_local(command, timeout)

    def read_os_release(self) -> str:
        """读取目标系统发行版信息。"""

        cmd = "cat /etc/os-release"
        result = self.run(cmd, timeout=15)
        return result.stdout if result.return_code == 0 else ""

    def _run_local(self, command: str, timeout: int) -> LinuxCommandResult:
        """本地 shell 执行命令。"""

        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return LinuxCommandResult(
            command=command,
            return_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    def _run_remote(self, command: str, timeout: int) -> LinuxCommandResult:
        """通过 Paramiko 在远端主机执行命令。"""

        assert self.ssh is not None

        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        connect_args = {
            "hostname": self.ssh.host,
            "port": self.ssh.port,
            "username": self.ssh.username,
            "timeout": timeout,
        }
        if self.ssh.private_key_path:
            connect_args["key_filename"] = self.ssh.private_key_path
        else:
            connect_args["password"] = self.ssh.password

        client.connect(**connect_args)
        try:
            stdin, stdout, stderr = client.exec_command(command, timeout=timeout)
            _ = stdin
            out_text = stdout.read().decode("utf-8", errors="replace")
            err_text = stderr.read().decode("utf-8", errors="replace")
            return_code = stdout.channel.recv_exit_status()
            return LinuxCommandResult(
                command=command,
                return_code=return_code,
                stdout=out_text,
                stderr=err_text,
            )
        finally:
            client.close()
