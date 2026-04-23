from os_agent.agent.orchestrator import Orchestrator


def test_extract_multiline_command_from_code_block() -> None:
    model_output = """```bash
cat > add.py << 'EOF'
print(1 + 2)
EOF
python3 add.py
```"""

    command = Orchestrator._extract_command_from_model_output(model_output)

    assert "cat > add.py" in command
    assert "print(1 + 2)" in command
    assert "EOF" in command
    assert "python3 add.py" in command
    assert "\n" in command


def test_extract_single_line_command_with_prefix() -> None:
    model_output = "Command: ls -la && pwd"
    command = Orchestrator._extract_command_from_model_output(model_output)
    assert command == "ls -la && pwd"


def test_extract_command_list_from_json_output() -> None:
    model_output = '{"steps":[{"command":"mkdir -p demo"},{"command":"echo hello > demo/a.txt"},{"command":"cat demo/a.txt"}]}'
    commands = Orchestrator._extract_command_list_from_json_output(model_output)

    assert len(commands) == 3
    assert commands[0] == "mkdir -p demo"
    assert commands[-1] == "cat demo/a.txt"


def test_detect_composite_request() -> None:
    assert Orchestrator._is_composite_request("创建一个文件并写入内容，然后运行并告诉我结果") is True
    assert Orchestrator._is_composite_request("查看磁盘空间") is False
