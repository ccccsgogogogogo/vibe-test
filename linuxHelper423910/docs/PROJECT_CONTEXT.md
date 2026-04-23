# Project Context for New AI Session

Use this document to bootstrap a new AI conversation quickly.

## Project Name

OS Intelligent Agent

## Vision

A Windows desktop client that provides Linux server management via natural language.
It should make command-line operations understandable and safer for non-expert operators.

## Core Requirements

1. Multi-model adaptation
- One unified interface for Qwen, Kimi, DeepSeek
- Streaming output support

2. Intent parsing and execution
- Convert user request to Linux command(s)
- Execute command locally (for development) or remotely over SSH
- Return results back to user with concise explanation

3. Security risk control
- Auto-detect high-risk and critical operations
- Block critical operations directly (for example: rm -rf /)
- Require second confirmation for high-risk operations

4. Frontend
- Chat-style flat UI like ChatGPT
- Implemented with PyQt6 for current baseline

5. Environment awareness
- Detect target distro using /etc/os-release
- Select distro profile (debian-family, redhat-family, arch-family, generic)

## Current Scaffold Status

- Implemented: config loading via environment variables
- Implemented: model client abstraction and provider factory
- Implemented: command executor (local + SSH)
- Implemented: rule-first intent planner
- Implemented: risk policy engine
- Implemented: orchestrator end-to-end flow
- Implemented: PyQt6 chat window with confirmation flow

## Key Extension Points

- src/os_agent/models/adapters.py
  Add provider-specific stream parser and payload format.

- src/os_agent/execution/intents.py
  Replace rule-first planner with LLM + tool schema planner.

- src/os_agent/risk/engine.py
  Expand policy list and support role-based policy sets.

- src/os_agent/agent/orchestrator.py
  Add structured memory, execution audit logs, and retries.

## Recommended Next Iteration

1. Add structured command schema (JSON action plan) from model.
2. Add command allowlist and per-role ACL.
3. Add test suite for risk policy and planner behavior.
4. Add SSH profile manager in UI.
5. Add execution streaming (tail-like output) to UI.
