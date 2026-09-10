"""Perception engine — extract emotion, urgency, and tasks from text."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class Emotion(Enum):
    """Detected emotional state."""

    HAPPY = "happy"
    TIRED = "tired"
    FRUSTRATED = "frustrated"
    ANXIOUS = "anxious"
    NEUTRAL = "neutral"


class Urgency(Enum):
    """Detected urgency level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class PerceptionResult:
    """Result of perceiving a natural language input."""

    emotion: Emotion
    urgency: Urgency
    tasks: list[str] = field(default_factory=list)
    raw_text: str = ""
    should_auto_create: bool = False


# ── Keyword maps ────────────────────────────────────────────────────────────

_EMOTION_KEYWORDS: dict[Emotion, list[str]] = {
    Emotion.HAPPY: ["开心", "高兴", "太好了", "棒", "不错", "开心", "哈哈", "nice", "great"],
    Emotion.TIRED: ["累了", "疲惫", "好困", "不想动", "没力气", "乏了", "tired", "exhausted"],
    Emotion.FRUSTRATED: ["烦", "崩溃", "受不了", "太难了", "卡住了", "frustrated", "stuck", "annoying"],
    Emotion.ANXIOUS: ["焦虑", "担心", "害怕", "压力大", "来不及", "panic", "anxious", "worried"],
}

_URGENCY_KEYWORDS: dict[Urgency, list[str]] = {
    Urgency.CRITICAL: ["紧急", "马上", "立刻", "立刻!", "ASAP", "火烧", "出事了", "critical"],
    Urgency.HIGH: ["尽快", "今天要", "很重要", "不能拖", "urgent", "important", "高优先"],
    Urgency.MEDIUM: ["最好", "有空做", "不急但", "medium", "留意"],
    Urgency.LOW: ["以后", "有空", "不急", "low", "闲了再"],
}

# Task extraction patterns — look for imperative/action phrases
_TASK_PATTERNS = [
    r"(?:需要|要|得|必须|应该|可以)\s*(.{2,20})",
    r"(?:做|完成|处理|搞定|解决|写|改|调|修)\s*(.{2,20})",
    r"(?:TODO|todo|Task|task)[：:]\s*(.{2,40})",
]


class PerceptionEngine:
    """Extract emotion, urgency, and implied tasks from text."""

    def perceive(self, text: str) -> PerceptionResult:
        """Analyze text and return structured perception."""
        emotion = self._detect_emotion(text)
        urgency = self._detect_urgency(text)
        tasks = self._extract_tasks(text)
        should_auto = self._should_auto_create(urgency, emotion)

        return PerceptionResult(
            emotion=emotion,
            urgency=urgency,
            tasks=tasks,
            raw_text=text,
            should_auto_create=should_auto,
        )

    @staticmethod
    def _detect_emotion(text: str) -> Emotion:
        text_lower = text.lower()
        best: Emotion | None = None
        best_count = 0
        for emotion, keywords in _EMOTION_KEYWORDS.items():
            count = sum(1 for kw in keywords if kw in text_lower)
            if count > best_count:
                best_count = count
                best = emotion
        return best or Emotion.NEUTRAL

    @staticmethod
    def _detect_urgency(text: str) -> Urgency:
        text_lower = text.lower()
        for urgency in [Urgency.CRITICAL, Urgency.HIGH, Urgency.MEDIUM, Urgency.LOW]:
            for kw in _URGENCY_KEYWORDS[urgency]:
                if kw in text_lower:
                    return urgency
        return Urgency.MEDIUM  # default

    @staticmethod
    def _extract_tasks(text: str) -> list[str]:
        tasks: list[str] = []
        for pattern in _TASK_PATTERNS:
            for match in re.finditer(pattern, text):
                task = match.group(1).strip()
                if task and len(task) >= 2:
                    tasks.append(task)
        return list(dict.fromkeys(tasks))  # dedupe preserving order

    @staticmethod
    def _should_auto_create(urgency: Urgency, emotion: Emotion) -> bool:
        """Auto-create tasks when urgency is high and user is not tired."""
        return urgency in (Urgency.CRITICAL, Urgency.HIGH) and emotion != Emotion.TIRED
