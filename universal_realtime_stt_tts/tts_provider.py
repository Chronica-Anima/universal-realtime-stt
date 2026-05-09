from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable


@runtime_checkable
class RealtimeTtsProvider(Protocol):
    async def synthesize(
        self, text: str, language: str,
    ) -> AsyncIterator[bytes]: ...
