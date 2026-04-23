# Linux Helper 开发者指南

## 项目架构概述

Linux Helper 采用模块化架构设计，各模块职责清晰，便于扩展和维护。

### 核心架构图

```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   用户界面层     │    │   业务逻辑层     │    │   基础设施层     │
│                 │    │                 │    │                 │
│  • PyQt6 Chat   │◄──►│  • Orchestrator  │◄──►│  • 模型适配器    │
│  • 对话管理     │    │  • 意图规划器    │    │  • 命令执行器    │
│  • 确认流程     │    │  • 风险评估器    │    │  • 环境探测器    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                        │                        │
         ▼                        ▼                        ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   配置与日志     │    │   数据持久化     │    │   外部服务       │
│                 │    │                 │    │                 │
│  • 环境变量     │    │  • 对话历史     │    │  • 模型API      │
│  • 日志系统     │    │  • 操作记录     │    │  • SSH服务器    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

## 核心模块详解

### 1. 配置管理 (config.py)

#### 配置加载机制

```python
# 配置加载优先级：
# 1. .env 文件（如果存在）
# 2. .env.example 文件（作为默认值）
# 3. 系统环境变量（可覆盖文件配置）
```

#### 主要配置类

```python
@dataclass
class SSHConfig:
    """SSH 连接配置"""
    host: str
    port: int = 22
    username: str = "root"
    password: Optional[str] = None
    private_key_path: Optional[str] = None

@dataclass
class AppConfig:
    """应用级配置"""
    model_provider: str = "qwen"
    model_name: str = ""
    # ... 其他配置字段
```

### 2. 模型层 (models/)

#### 抽象基类

```python
class StreamingModelClient(ABC):
    @abstractmethod
    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """流式聊天接口"""
```

#### 模型工厂模式

```python
class ModelFactory:
    @staticmethod
    def build_model_client(cfg: AppConfig) -> StreamingModelClient:
        """根据配置构建对应的模型客户端"""
        if cfg.model_provider == "qwen":
            return QwenModelAdapter(cfg)
        elif cfg.model_provider == "kimi":
            return KimiModelAdapter(cfg)
        # ... 其他模型
```

#### 适配器实现示例

```python
class QwenModelAdapter(StreamingModelClient):
    def __init__(self, cfg: AppConfig):
        self.base_url = cfg.qwen_base_url
        self.api_key = cfg.qwen_api_key
    
    def stream_chat(self, messages):
        # 实现 Qwen 特定的流式调用逻辑
        pass
```

### 3. 环境探测 (env/)

#### 环境信息结构

```python
@dataclass
class LinuxEnvironment:
    """Linux 环境信息"""
    distro_id: str = "unknown"
    pretty_name: str = "Unknown Linux"
```

#### 发行版识别

```python
def parse_os_release(raw: str) -> LinuxEnvironment:
    """解析 /etc/os-release 文件"""
    # 解析逻辑...

def best_practice_profile(env: LinuxEnvironment) -> str:
    """根据发行版选择最佳实践画像"""
    distro = env.distro_id
    if re.search(r"ubuntu|debian", distro):
        return "debian-family"
    # ... 其他发行版
```

### 4. 意图规划 (execution/intents.py)

#### 意图规划器

```python
class IntentPlanner:
    """规则优先的意图规划器"""
    
    def plan(self, user_text: str, profile: str) -> PlannedCommand:
        """将用户请求映射到可执行命令"""
        
        text = user_text.strip().lower()
        
        # 问候处理
        if self._is_greeting(text):
            return PlannedCommand(
                intent="greeting",
                command="",
                execute=False,
                response_text="问候响应"
            )
        
        # 系统监控意图
        if self._is_system_monitoring(text):
            return self._plan_system_monitoring(text, profile)
        
        # ... 其他意图类型
```

#### 规划结果结构

```python
@dataclass
class PlannedCommand:
    """意图规划结果"""
    intent: str                    # 意图名称
    command: str                   # 生成的命令
    response_text: str = ""        # 响应文本
    execute: bool = True           # 是否执行
    needs_confirmation: bool = False  # 是否需要确认
```

### 5. 风险评估 (risk/engine.py)

#### 风险等级定义

```python
class RiskLevel(str, enum.Enum):
    """风险等级"""
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class RiskAction(str, enum.Enum):
    """处置动作"""
    continue_execution = "continue"
    ask_user = "ask_user"
    block = "block"
```

#### 风险策略引擎

```python
class RiskPolicyEngine:
    """基于正则策略和LLM评分的风险识别引擎"""
    
    CRITICAL_PATTERNS = [
        r"\brm\s+-rf\s+/(?:\s|$)",
        r"\b(?:mkfs|fdisk|parted)\b",
        # ... 其他关键模式
    ]
    
    def assess_risk(self, command: str) -> RiskDecision:
        """评估命令风险"""
        # 1. 正则匹配检查
        # 2. LLM 风险评估（可选）
        # 3. 综合决策
```

### 6. 命令执行 (execution/linux_client.py)

#### 执行器设计

```python
class LinuxCommandExecutor:
    """Linux 命令执行器"""
    
    def __init__(self, ssh: Optional[SSHConfig] = None):
        self.ssh = ssh
        self._active_connection = None
    
    def run(self, command: str, timeout: int = 60) -> LinuxCommandResult:
        """统一执行入口"""
        if self.ssh and self.ssh.host:
            return self._run_remote(command, timeout)
        return self._run_local(command, timeout)
```

#### 执行结果结构

```python
@dataclass
class LinuxCommandResult:
    """命令执行结果"""
    command: str
    return_code: int
    stdout: str
    stderr: str
```

### 7. 流程编排 (agent/orchestrator.py)

#### 编排器核心逻辑

```python
class Orchestrator:
    """端到端编排流程"""
    
    def process_turn(self, user_text: str) -> TurnResult:
        """处理单轮对话"""
        
        # 1. 环境探测
        profile = self._detect_environment()
        
        # 2. 意图规划
        planned_cmd = self.planner.plan(user_text, profile)
        
        # 3. 风险评估
        risk_decision = self.risk.assess_risk(planned_cmd.command)
        
        # 4. 命令执行（如果安全）
        if not risk_decision.blocked and planned_cmd.execute:
            execution_result = self.executor.run(planned_cmd.command)
        else:
            execution_result = None
        
        # 5. 结果总结
        assistant_text = self._summarize_results(planned_cmd, execution_result)
        
        return TurnResult(
            user_text=user_text,
            profile=profile,
            planned_intent=planned_cmd.intent,
            command=planned_cmd.command,
            risk=risk_decision,
            execution=execution_result,
            assistant_text=assistant_text
        )
```

### 8. 用户界面 (ui/pyqt_chat.py)

#### 界面架构

```python
class ChatMainWindow(QMainWindow):
    """主聊天窗口"""
    
    def __init__(self, orchestrator: Orchestrator):
        super().__init__()
        self.orchestrator = orchestrator
        self._setup_ui()
    
    def _setup_ui(self):
        """初始化界面组件"""
        # 创建聊天区域、输入框、按钮等
    
    def _on_send_clicked(self):
        """处理发送消息"""
        user_text = self.input_text.toPlainText().strip()
        if user_text:
            self._process_user_message(user_text)
```

## 数据流分析

### 典型请求处理流程

```
用户输入
    ↓
环境探测 → 获取系统信息
    ↓
意图规划 → 生成命令
    ↓
风险评估 → 安全决策
    ↓
命令执行 → 本地/远程执行
    ↓
结果总结 → 模型解释
    ↓
界面显示 → 用户反馈
```

### 错误处理流程

```
执行失败
    ↓
错误分类 → 连接错误/命令错误/权限错误
    ↓
恢复策略 → 重试/降级/用户提示
    ↓
日志记录 → 详细错误信息
    ↓
用户反馈 → 友好错误消息
```

## 扩展开发指南

### 添加新的意图类型

#### 1. 扩展意图规划器

在 `intents.py` 中添加新的意图识别逻辑：

```python
def _is_custom_intent(self, text: str) -> bool:
    """识别自定义意图"""
    keywords = ["自定义关键词1", "自定义关键词2"]
    return any(keyword in text.lower() for keyword in keywords)

def _plan_custom_intent(self, text: str, profile: str) -> PlannedCommand:
    """规划自定义意图"""
    # 根据环境和用户输入生成命令
    command = self._generate_custom_command(text, profile)
    
    return PlannedCommand(
        intent="custom_intent",
        command=command,
        response_text="自定义操作执行中...",
        needs_confirmation=True  # 如果需要确认
    )
```

#### 2. 集成到主流程

在 `plan` 方法中添加对新意图的调用：

```python
def plan(self, user_text: str, profile: str) -> PlannedCommand:
    text = user_text.strip().lower()
    
    # 现有意图检查...
    
    # 新增自定义意图检查
    if self._is_custom_intent(text):
        return self._plan_custom_intent(text, profile)
    
    # 默认处理
    return self._fallback_plan(text, profile)
```

### 添加新的模型提供商

#### 1. 实现模型适配器

在 `models/adapters.py` 中创建新的适配器：

```python
class CustomModelAdapter(StreamingModelClient):
    """自定义模型适配器"""
    
    def __init__(self, cfg: AppConfig):
        self.base_url = cfg.custom_base_url
        self.api_key = cfg.custom_api_key
        self.model_name = cfg.custom_model_name
    
    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """实现流式聊天接口"""
        
        # 构建请求
        payload = {
            "model": self.model_name,
            "messages": messages,
            "stream": True
        }
        
        # 发送请求并处理流式响应
        response = requests.post(
            f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            stream=True
        )
        
        # 解析流式响应
        for line in response.iter_lines():
            if line:
                chunk = self._parse_chunk(line.decode('utf-8'))
                if chunk:
                    yield chunk
```

#### 2. 更新模型工厂

在 `models/factory.py` 中注册新的适配器：

```python
class ModelFactory:
    @staticmethod
    def build_model_client(cfg: AppConfig) -> StreamingModelClient:
        if cfg.model_provider == "qwen":
            return QwenModelAdapter(cfg)
        elif cfg.model_provider == "kimi":
            return KimiModelAdapter(cfg)
        elif cfg.model_provider == "custom":  # 新增
            return CustomModelAdapter(cfg)
        else:
            raise ValueError(f"不支持的模型提供商: {cfg.model_provider}")
```

#### 3. 更新配置类

在 `config.py` 中添加新的配置字段：

```python
@dataclass
class AppConfig:
    # 现有字段...
    
    # 新增自定义模型配置
    custom_base_url: str = ""
    custom_api_key: str = ""
    custom_model_name: str = ""
```

### 扩展风险评估策略

#### 1. 添加新的风险模式

在 `risk/engine.py` 中扩展风险模式列表：

```python
class RiskPolicyEngine:
    # 现有模式...
    
    CUSTOM_RISK_PATTERNS = [
        r"\bdangerous_custom_command\b",
        r"\brisky_operation.*--force\b",
    ]
    
    def assess_risk(self, command: str) -> RiskDecision:
        # 现有评估逻辑...
        
        # 新增自定义模式检查
        for pattern in self.CUSTOM_RISK_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return RiskDecision(
                    level=RiskLevel.high,
                    blocked=False,
                    requires_confirmation=True,
                    reason="检测到自定义高风险操作"
                )
```

#### 2. 实现自定义风险评估逻辑

```python
def _assess_custom_risk(self, command: str) -> Optional[RiskDecision]:
    """自定义风险评估逻辑"""
    
    # 基于命令复杂度的评估
    complexity_score = self._calculate_complexity(command)
    
    if complexity_score > 0.8:
        return RiskDecision(
            level=RiskLevel.medium,
            blocked=False,
            requires_confirmation=True,
            reason="命令复杂度较高，建议确认"
        )
    
    return None
```

## 测试指南

### 单元测试结构

```python
# tests/test_intents.py
class TestIntentPlanner:
    def test_greeting_intent(self):
        """测试问候意图识别"""
        planner = IntentPlanner()
        result = planner.plan("你好", "debian-family")
        assert result.intent == "greeting"
        assert not result.execute

class TestRiskEngine:
    def test_critical_command_blocking(self):
        """测试关键命令拦截"""
        engine = RiskPolicyEngine()
        decision = engine.assess_risk("rm -rf /")
        assert decision.blocked == True
        assert decision.level == RiskLevel.critical
```

### 集成测试

```python
# tests/test_orchestrator.py
class TestOrchestratorIntegration:
    def test_end_to_end_flow(self):
        """测试端到端流程"""
        cfg = AppConfig()
        orchestrator = Orchestrator(cfg)
        
        # 模拟用户输入
        result = orchestrator.process_turn("查看磁盘使用情况")
        
        assert result.profile is not None
        assert result.command.startswith("df")
        assert result.risk.level == RiskLevel.low
```

### 性能测试

```python
# tests/test_performance.py
class TestPerformance:
    def test_command_execution_latency(self):
        """测试命令执行延迟"""
        executor = LinuxCommandExecutor()
        
        start_time = time.time()
        result = executor.run("echo 'test'")
        end_time = time.time()
        
        latency = end_time - start_time
        assert latency < 2.0  # 2秒内完成
```

## 部署与运维

### 日志管理

系统使用结构化日志，关键日志事件包括：

- **连接建立/断开**
- **命令执行开始/结束**
- **风险评估结果**
- **错误和异常**

### 监控指标

建议监控的关键指标：

- 命令执行成功率
- 平均响应时间
- 风险拦截率
- 模型API调用成功率

### 安全考虑

- 定期更新依赖包
- 审查风险策略规则
- 监控异常操作模式
- 备份重要配置和数据

## 贡献指南

### 代码规范

- 遵循 PEP 8 编码规范
- 使用类型注解
- 编写清晰的文档字符串
- 添加适当的单元测试

### 提交信息格式

```
类型(模块): 简要描述

详细描述（可选）

BREAKING CHANGE: 重大变更说明（可选）
```

类型包括：feat, fix, docs, style, refactor, test, chore

### 分支策略

- main: 主分支，稳定版本
- develop: 开发分支
- feature/*: 功能分支
- hotfix/*: 热修复分支

---

*最后更新：2026年4月23日*
*版本：1.0*