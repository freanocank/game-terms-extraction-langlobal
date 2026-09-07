"""Optional, explicitly degradable entity hints."""

import asyncio
import logging
from openai import AsyncOpenAI
from core.languages import DEFAULT_SOURCE_LANGUAGE, is_chinese, language_name, language_tag
from core.responses import chat_content, parse_entities
from core.retry import retry_async

logger = logging.getLogger("pipeline")

NER_SYSTEM = """从游戏中文文本中识别可能的命名实体。严格输出 JSON：
{"persons": ["人名"], "places": ["地名"]}
- persons: 有命名语境支持的角色名、人名和指向特定人物的称呼；不确定时不要仅凭姓氏或单字认定人名。
- places: 有上下文支持的地名、场所、门派、组织专名；不包括泛指普通名词。
- 仅分析源文本，不把同行译文、行标识或说明当作实体。保持原文拼写，不翻译。
- 没有相关实体时返回空数组。只输出 JSON。"""


def ner_system_prompt(source_language):
    if is_chinese(source_language):
        return NER_SYSTEM
    return f"""从{language_name(source_language)}游戏文本中识别有命名语境支持的人物和地点专名。
严格输出 JSON：{{"persons": ["人名"], "places": ["地名"]}}。
仅分析 {language_tag(source_language)} 源文本，不包含同行译文、行标识或泛指普通名词。
保持实体原文拼写，不翻译。不确定时不强制归为人名。没有实体时返回空数组。"""


async def ner_flash_batch(batch_c, api_key, base_url, model, source_language=DEFAULT_SOURCE_LANGUAGE,
                          concurrent=5):
    client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=30, max_retries=0)
    semaphore = asyncio.Semaphore(max(1, min(5, concurrent)))

    async def one(index, text):
        if not text.strip():
            return {"persons": [], "places": []}
        async with semaphore:
            async def request():
                response = await client.chat.completions.create(
                    model=model, temperature=0, max_tokens=2048,
                    messages=[{"role": "system", "content": ner_system_prompt(source_language)},
                              {"role": "user", "content": text}],
                )
                return parse_entities(chat_content(response))
            try:
                return await retry_async(request, max_attempts=3, initial_delay=2, delay_cap=10, tag=f"NER c{index}")
            except Exception as exc:
                message = str(exc).replace(api_key, "***") if api_key else str(exc)
                logger.warning("NER chunk %s 失败，已降级为无实体提示：%s: %s", index, type(exc).__name__, message)
                return {"persons": [], "places": []}

    try:
        results = await asyncio.gather(*(one(i, text) for i, text in enumerate(batch_c)))
        return tuple(sorted({entity for result in results for entity in result[key]}) for key in ("persons", "places"))
    finally:
        await client.close()


def ner_scan(batch_c, api_key, base_url, model, source_language=DEFAULT_SOURCE_LANGUAGE, concurrent=5):
    return asyncio.run(ner_flash_batch(batch_c, api_key, base_url, model, source_language, concurrent))
