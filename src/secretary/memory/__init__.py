"""Memory module — cross-session memory for Secretary."""

from secretary.memory.extractor import ExtractionResult, extract_memory, is_memorable_message
from secretary.memory.store import MemoryEntry, MemoryStatus, MemoryStore, MemoryType, create_memory

__all__ = [
    "MemoryStore",
    "MemoryEntry",
    "MemoryType",
    "MemoryStatus",
    "create_memory",
    "extract_memory",
    "is_memorable_message",
    "ExtractionResult",
    "auto_memorize",
]


def auto_memorize(text: str, source: str = "qqbot") -> bool:
    """Auto-memorize a user message if it's worth remembering.

    Args:
        text: User message text
        source: Source of the message (qqbot, telegram, etc.)

    Returns:
        True if a memory was stored, False otherwise
    """
    if not is_memorable_message(text):
        return False

    extraction = extract_memory(text)
    if not extraction.should_memorize:
        return False

    store = MemoryStore()
    entry = create_memory(
        content=text,
        memory_type=extraction.memory_type,
        entities=extraction.entities,
        tags=extraction.tags,
        source=source,
    )
    store.append(entry)
    return True
