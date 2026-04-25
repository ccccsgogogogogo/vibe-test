"""桌面客户端启动入口

启动顺序固定为：
1) 初始化日志系统（保证后续模块在启动阶段即可输出结构化日志）；
2) 启动 PyQt 客户端主循环。

github:
Riwing123
巴兰尼科夫，我的后效呢?
Jun-1-183
"""

from os_agent.ui.pyqt_chat import run_app
from os_agent.logging_config import setup_logging


if __name__ == "__main__":
    # 先初始化日志，再进入 UI 主循环，避免启动阶段日志丢失。
    setup_logging()
    
    # 启动桌面客户端（阻塞直到窗口关闭）
    run_app()
 