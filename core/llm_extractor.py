#!/usr/bin/env python3
import asyncio
import json
import logging
import threading
import time
from datetime import datetime
from pathlib import Path

from openai import AsyncOpenAI
from config import EXTRACTOR_CONFIG, get_token_param_name
from config_template import DEFAULT_API_BASE
from core.request_cache import RequestCache, fingerprint
from core.responses import chat_content, json_object, response_usage, validate_terms
from core.retry import retry_async
from core.voting import collect_llm_votes


class LLMExtractor:
    def __init__(self, api_key: str, base_url: str = DEFAULT_API_BASE,
                 base_dir: str = "output", cache_dir: str = ""):
        self.async_client = AsyncOpenAI(
            api_key=api_key, base_url=base_url,
            timeout=EXTRACTOR_CONFIG["timeout"], max_retries=0,
        )
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        session_ts = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        self._exchange_log_file = self.base_dir / f"api_log_{session_ts}.jsonl"
        self._log_fh = self._exchange_log_file.open("a", encoding="utf-8")
        self._log_lock = threading.Lock()
        self._cache = RequestCache(cache_dir, "extraction")
        self._runner = asyncio.Runner()
        self.logger = logging.getLogger("pipeline")

    def run_sync(self, coroutine):
        return self._runner.run(coroutine)

    def close(self):
        try:
            self._runner.run(self.async_client.close())
        finally:
            self._runner.close()
            self._log_fh.close()

    def _log_exchange(self, custom_id, messages, raw_content, usage, model, error=""):
        entry = {
            "timestamp": datetime.now().isoformat(), "custom_id": custom_id,
            "model": model, "messages": messages, "response": raw_content,
            "usage": usage, "error": error,
        }
        with self._log_lock:
            serialized = json.dumps(entry, ensure_ascii=False)
            self._log_fh.write((serialized.replace(self._api_key, "***") if self._api_key else serialized) + "\n")
            self._log_fh.flush()

    async def _process_single_text_async(self, text, custom_id, semaphore, system_prompt,
                                         user_prompt, model, temperature, max_tokens, source_file, records=None):
        params = self._build_api_params(system_prompt, user_prompt, model, temperature, max_tokens)
        key = fingerprint({"schema": 3, "provider": self.base_url, "text": text, "params": params})
        cached = self._cache.load(key)
        if isinstance(cached, dict) and not cached.get("error"):
            try:
                validate_terms(cached.get("extracted_terms"))
                if records is not None:
                    collect_llm_votes([[{**cached, "source_file": source_file}]], {source_file: records})
            except ValueError:
                pass
            else:
                self.logger.info("[%s] cache hit", custom_id)
                return {**cached, "custom_id": custom_id, "source_file": source_file}
        async with semaphore:
            async def request():
                return await self.async_client.chat.completions.create(**params)
            try:
                response = await retry_async(request, max_attempts=5, initial_delay=2, tag=f"[{custom_id}]")
            except Exception as exc:
                result = self._create_error_result(custom_id, model, source_file, f"{type(exc).__name__}: {exc}")
                self._log_exchange(custom_id, params["messages"], "", {}, model, result["error"])
                return result
            result = self._process_api_response(response, custom_id, model, source_file)
            if records is not None and not result.get("error"):
                try:
                    collect_llm_votes([[result]], {source_file: records})
                except ValueError as exc:
                    result = self._create_error_result(custom_id, model, source_file, str(exc))
            raw = str(response)
            if getattr(response, "choices", None):
                raw = getattr(getattr(response.choices[0], "message", None), "content", raw)
            self._log_exchange(custom_id, params["messages"], raw, result["usage"], model, result.get("error", ""))
            if not result.get("error"):
                self._cache.save(key, result)
            return result

    def _build_api_params(self, system_prompt, user_prompt, model, temperature, max_tokens):
        return {
            "model": model,
            "messages": [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            "temperature": temperature, get_token_param_name(model): max_tokens,
        }

    def _process_api_response(self, response, custom_id, model, source_file):
        try:
            terms = self._parse_json_response(chat_content(response))
            return {
                "custom_id": custom_id, "extracted_terms": terms, "usage": response_usage(response),
                "model": getattr(response, "model", model), "source_file": source_file,
                "created": int(time.time()),
            }
        except ValueError as exc:
            self.logger.error("[%s] invalid response: %s", custom_id, exc)
            return self._create_error_result(custom_id, model, source_file, str(exc))

    def _parse_json_response(self, content):
        return validate_terms(json_object(content))

    def _create_error_result(self, custom_id, model, source_file, error_msg):
        if self._api_key:
            error_msg = error_msg.replace(self._api_key, "***")
        return {
            "custom_id": custom_id, "error": error_msg, "extracted_terms": None,
            "usage": {"total_tokens": 0}, "model": model, "source_file": source_file,
            "created": int(time.time()),
        }

    async def run_batch_async(self, texts, system_prompt, user_prompts, model, temperature,
                              max_tokens, max_concurrent, source_files, batch_id="", progress=None,
                              semaphore=None, records=None):
        if max_concurrent < 1:
            raise ValueError("并发数必须大于 0")
        semaphore = semaphore if semaphore is not None else asyncio.Semaphore(max_concurrent)
        tasks = [
            asyncio.create_task(self._process_single_text_async(
                text, f"{batch_id}_t{i}" if batch_id else f"term-extraction-{i + 1}",
                semaphore, system_prompt, user_prompts[i], model, temperature, max_tokens,
                source_files[i] if source_files else "",
                records[i] if records is not None else None,
            ))
            for i, text in enumerate(texts)
        ]
        results = []
        try:
            for done, coro in enumerate(asyncio.as_completed(tasks), 1):
                results.append(await coro)
                if progress:
                    progress(done, len(tasks))
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return results

    def process_batch_concurrent(self, texts, system_prompt=None, user_prompts=None,
                                 model="gpt-4-turbo-preview", temperature=0.1, max_tokens=4096,
                                 max_concurrent=10, source_files=None, batch_id=""):
        if not texts:
            return []
        try:
            results = self.run_sync(self.run_batch_async(
                texts, system_prompt, user_prompts or [""] * len(texts), model, temperature,
                max_tokens, max_concurrent, source_files or [], batch_id,
            ))
            return sorted(results, key=lambda item: item["custom_id"])
        finally:
            self.close()
