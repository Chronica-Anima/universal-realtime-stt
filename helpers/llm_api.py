"""
Base Gemini LLM client with shared call logic.

This module provides a reusable GeminiClient class that handles:
- Client initialization with API key
- JSON-based LLM calls with configurable parameters
- Response parsing
"""

from __future__ import annotations

import asyncio
from json import loads, JSONDecodeError
from logging import getLogger
from time import time

from google import genai
from google.genai import types
from google.genai.errors import ServerError

logger = getLogger(__name__)


class LLMBasicClient:
    """
    Base Gemini client with shared LLM call logic.
        max_tokens: Maximum output tokens (default 0 - not applicable)
        temperature: Response randomness (default 0.2)

    Provides a single call_llm() method that handles the common pattern of:
    - Sending a prompt with system instruction
    - Getting JSON response
    - Parsing and returning as dict
    """

    def __init__(self, api_key: str, model_id: str, max_tokens: int = 0, temperature: float = 0.2):
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set")

        self.model_id = model_id
        self.client = genai.Client(api_key=api_key)
        self.max_tokens = max_tokens
        self.temperature = temperature

    async def _call_gemini(self, prompt: str, system_prompt: str):
        """Run a single blocking Gemini API call in a thread pool."""
        return await asyncio.to_thread(
            self.client.models.generate_content,
            model=self.model_id,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=self.temperature,
                max_output_tokens=self.max_tokens,
                response_mime_type="application/json",
            ),
        )

    async def call_llm(self, prompt: str, system_prompt: str, max_retries: int = 3) -> dict:
        """
        Make LLM call and return parsed JSON response.

        Runs the blocking Gemini API call in a thread pool to avoid blocking
        the asyncio event loop. Retries on transient errors (503) with backoff.

        Args:
            prompt: User prompt to send
            system_prompt: System instruction for the LLM
            max_retries: Number of retry attempts on transient errors (default 2)

        Returns:
            Parsed JSON dict from LLM response

        Raises:
            Exception: If LLM call or JSON parsing fails after all retries
        """
        logger.debug("Calling %s with prompt:\n%.1500s", self.model_id, prompt)
        start = time()

        resp = None
        for attempt in range(max_retries + 1):
            try:
                resp = await self._call_gemini(prompt, system_prompt)
                logger.debug("%s raw response:\n%r", self.model_id, resp)
                break  # Success, exit retry loop
            except ServerError as e:
                # ServerError with status 503 indicates transient overload - retry with backoff
                if e.status == 503 and attempt < max_retries:
                    delay = 1.0 * (attempt + 1)  # 1s, 2s backoff
                    logger.warning(
                        "%s attempt %d/%d failed (status=%d), retrying in %.1fs: %s",
                        self.model_id, attempt + 1, max_retries + 1, e.status, delay, e.message
                    )
                    await asyncio.sleep(delay)
                    continue

                # Out of retries on 503, we do not need previous error context, 503 is pretty clear.
                # Other errors are not caught at all and passed up.
                raise RuntimeError(f"{self.model_id}: server unavailable (503) after {max_retries + 1} attempts") from None

        # process to JSON and return; retry once on parse failure
        raw = (resp.text or "").strip()
        logger.debug("%s responded in %.1f s, len: %d", self.model_id, round(time() - start, 1), len(raw))
        try:
            return loads(raw)
        except JSONDecodeError:
            logger.exception(
                "Failed to parse JSON from %s:\nraw_text=%r\nfull_llm_response=%r",
                self.model_id, raw, resp,
            )
            raise
