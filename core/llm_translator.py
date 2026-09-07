#!/usr/bin/env python3
import asyncio
import logging
from typing import Dict, Optional

from openai import AsyncOpenAI
from config import TRANSLATOR_CONFIG, get_token_param_name
from config_template import DEFAULT_API_BASE, DEFAULT_MODEL
from core.responses import chat_content, parse_translations, response_usage
from core.retry import retry_async


class TermTranslator:
    def __init__(self, api_key: str, base_url: str = DEFAULT_API_BASE,
                 model: str = DEFAULT_MODEL, profile: Optional[Dict] = None):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model
        self.profile = profile or {}
        self.logger = logging.getLogger("pipeline")
        self.last_errors = []

    @staticmethod
    def _parse(raw):
        return parse_translations(raw)

    def translate_batches(self, prompts, concurrent=None):
        concurrent = concurrent if concurrent is not None else TRANSLATOR_CONFIG["max_concurrent"]
        if concurrent < 1:
            raise ValueError("翻译并发数必须大于 0")
        self.last_errors = []

        async def run():
            client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url,
                                 timeout=TRANSLATOR_CONFIG["timeout"], max_retries=0)
            semaphore = asyncio.Semaphore(concurrent)

            async def one(index, system, user):
                async with semaphore:
                    async def request():
                        return await client.chat.completions.create(
                            model=self.model, temperature=TRANSLATOR_CONFIG["temperature"],
                            **{get_token_param_name(self.model): TRANSLATOR_CONFIG["max_tokens"]},
                            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                        )
                    response = await retry_async(request, max_attempts=3, initial_delay=2, tag=f"translate b{index}")
                    parsed = self._parse(chat_content(response))
                    self.logger.info("[translate b%s] %s tokens, %s valid translations", index, response_usage(response)["total_tokens"], len(parsed))
                    return parsed

            try:
                return await asyncio.gather(*(one(i, sp, up) for i, (sp, up) in enumerate(prompts)),
                                            return_exceptions=True)
            finally:
                await client.close()

        merged = {}
        for index, result in enumerate(asyncio.run(run())):
            if isinstance(result, Exception):
                message = str(result).replace(self.api_key, "***") if self.api_key else str(result)
                self.last_errors.append(f"b{index}: {type(result).__name__}: {message}")
            else:
                merged.update(result)
        for error in self.last_errors:
            self.logger.error("[translate] incomplete: %s", error)
        return merged
