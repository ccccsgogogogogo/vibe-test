from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, Iterable, List


class StreamingModelClient(ABC):
    @abstractmethod
    def stream_chat(self, messages: List[Dict[str, str]]) -> Iterable[str]:
        """按流式方式返回模型输出文本分片。"""
        raise NotImplementedError
