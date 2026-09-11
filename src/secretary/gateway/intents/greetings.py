"""Greetings, thanks, praise — the social intents."""

from __future__ import annotations

import os

from secretary.gateway.intents.base import IntentContext

_GREETING_KW = {
    "你好",
    "hi",
    "hello",
    "hey",
    "早",
    "早上好",
    "早安",
    "晚安",
    "在吗",
    "在不在",
    "喂",
    "嗨",
    "哈喽",
    "yo",
    "sup",
    "下午好",
    "晚上好",
}

_THANKS_KW = {"谢谢", "感谢", "thanks", "thank you", "thx", "3q", "3Q", "辛苦了"}

_PRAISE_KW = {"不错", "厉害", "牛", "棒", "好样的", "干得好", "good", "nice", "great", "awesome"}


class GreetingHandler:
    name = "greeting"
    keywords = _GREETING_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str:
        from datetime import datetime

        user_name = os.environ.get("SECRETARY_USER_NAME", "主人")
        hour = datetime.now().hour
        if hour < 6:
            return f"{user_name}，这么晚还没睡？注意休息啊。"
        if hour < 12:
            return f"早安{user_name}！有什么需要帮忙的吗？"
        if hour < 18:
            return f"下午好{user_name}！随时待命。"
        return f"晚上好{user_name}！有什么事吗？"


class ThanksHandler:
    name = "thanks"
    keywords = _THANKS_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str:
        return "不客气，这是秘书该做的。"


class PraiseHandler:
    name = "praise"
    keywords = _PRAISE_KW

    def __init__(self):
        self.patterns = []

    async def handle(self, ctx: IntentContext) -> str:
        return "谢谢夸奖！继续努力。"


HANDLERS = [GreetingHandler(), ThanksHandler(), PraiseHandler()]
