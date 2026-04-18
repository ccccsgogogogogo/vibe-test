# OS智能代理（Windows 客户端）

这是一个桌面客户端项目，用自然语言驱动 Linux 服务器管理流程。

## 核心目标

- 统一多模型调用层（Qwen、Kimi、DeepSeek），支持流式输出。
- 意图解析与执行：将自然语言转换为 Linux 运维命令。
- 安全风控：识别并拦截危险命令，对高风险操作执行二次确认。
- 前端界面：提供类 ChatGPT 的聊天式桌面 UI（PyQt6）。
- 环境感知：自动识别目标发行版并匹配最佳实践执行路径。

## 快速开始

1. 准备 Python 3.11+ 环境（建议固定同一解释器安装与运行）。
2. 安装依赖：

   `py -3.14 -m pip install -r requirements.txt`

3. 按 `.env.example` 配置环境变量（示例）：

   `set OA_MODEL_PROVIDER=qwen`
   `set OA_QWEN_API_KEY=你的密钥`

4. 启动桌面应用：

   `py -3.14 src/main.py`

## 项目结构

- `src/os_agent/config.py`：配置加载与环境变量解析
- `src/os_agent/models/`：统一模型接口与供应商适配器
- `src/os_agent/env/`：Linux 发行版探测与策略画像
- `src/os_agent/execution/`：意图规划与命令执行（本地/SSH）
- `src/os_agent/risk/`：风险识别与拦截策略
- `src/os_agent/agent/orchestrator.py`：端到端编排流程
- `src/os_agent/ui/pyqt_chat.py`：桌面聊天界面
- `docs/PROJECT_CONTEXT.md`：新会话上下文速览

## 说明

- 当前骨架优先保证架构清晰与安全默认值。
- 各模型供应商的具体 REST 协议细节可在 `models/adapters.py` 中扩展，不影响上层编排逻辑。
