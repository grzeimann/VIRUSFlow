from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Dict, Tuple, Optional


@dataclass(frozen=True)
class Serializer:
    # Lightweight description without loading big payloads
    describe: Callable[[str], dict]
    # Full payload loader
    load: Callable[[str], dict]


class SerializerRegistry:
    """Dispatch serializers by (payload_type, storage_format).

    Example keys: ("array", "fits"), ("text", "txt"), ("image", "png").
    """

    def __init__(self) -> None:
        self._by_key: Dict[Tuple[str, str], Serializer] = {}

    def register(self, payload_type: str, storage_format: str, serializer: Serializer) -> None:
        key = (payload_type.strip().lower(), storage_format.strip().lower())
        self._by_key[key] = serializer

    def get(self, payload_type: str, storage_format: str) -> Optional[Serializer]:
        key = (str(payload_type).strip().lower(), str(storage_format).strip().lower())
        return self._by_key.get(key)
