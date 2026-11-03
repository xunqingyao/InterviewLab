"""Walk one requirement's follow-up chain. Do not use the whole session as a thread."""

from __future__ import annotations

from typing import Any


def parent_chain(turns: list[Any], current: Any) -> list[Any]:
    """Root ASK first, then each FOLLOW_UP, ending at current."""
    if current is None:
        return []
    by_id = {item.id: item for item in turns}
    chain: list[Any] = []
    node: Any | None = current
    seen: set[Any] = set()
    while node is not None and node.id not in seen:
        seen.add(node.id)
        chain.append(node)
        parent_id = getattr(node, "parent_turn_id", None)
        node = by_id.get(parent_id) if parent_id else None
    chain.reverse()
    return chain
