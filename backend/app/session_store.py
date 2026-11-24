"""以内存方式保存已完成的会话轮次。"""

from typing import Literal, TypedDict


class Message(TypedDict):
    """会话中保存的一条用户或助手消息。"""

    role: Literal["user", "assistant"]
    content: str


class SessionStore:
    """在当前进程中保存聊天历史。

    这是临时的内存实现：进程重启后会话会丢失，多个后端进程之间也不会共享会话。
    """

    def __init__(self) -> None:
        self._sessions: dict[str, list[Message]] = {}

    def get(self, session_id: str) -> list[Message]:
        """返回会话历史副本；会话不存在时自动创建。"""
        history = self._sessions.setdefault(session_id, [])
        return [message.copy() for message in history]

    def append(
        self, session_id: str, user_msg: Message, assistant_msg: Message
    ) -> None:
        """向会话追加一轮已完成的用户消息和助手回复。"""
        history = self._sessions.setdefault(session_id, [])
        history.extend((user_msg.copy(), assistant_msg.copy()))
