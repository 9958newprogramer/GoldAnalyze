"""Compile user language into a strictly validated StrategySpec."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date

from openai import AsyncOpenAI, OpenAIError
from pydantic import ValidationError

from app.agent.parsing import parse_timeframe
from app.config import Settings
from app.models import StrategySpec


@dataclass(frozen=True)
class Interpretation:
    spec: StrategySpec
    interpreter: str
    warnings: list[str]


class RuleBasedStrategyInterpreter:
    name = "rule-based"

    @staticmethod
    def _number_near(label: str, question: str) -> float | None:
        patterns = [
            rf"(?:{label})[^0-9]{{0,8}}([0-9]+(?:\.[0-9]+)?)\s*(?:个|個)?\s*(?:bp|bps|基点|基點)",
            rf"(?:{label})[^0-9]{{0,8}}([0-9]+(?:\.[0-9]+)?)\s*%",
        ]
        for index, pattern in enumerate(patterns):
            match = re.search(pattern, question, flags=re.IGNORECASE)
            if match:
                value = float(match.group(1))
                return value if index == 0 else value * 100
        return None

    @staticmethod
    def _initial_cash(question: str) -> float | None:
        match = re.search(r"(?:本金|初始资金)[^0-9]{0,6}([0-9]+(?:\.[0-9]+)?)\s*(万|元)?", question)
        if not match:
            return None
        amount = float(match.group(1))
        return amount * 10_000 if match.group(2) == "万" else amount

    async def interpret(self, question: str) -> Interpretation:
        warnings: list[str] = []
        shared_unit_pair = re.search(
            r"(?<![0-9])([0-9]{1,3})\s*(?:日|小时|小時)?"
            r"\s*(?:与|和|及|[/／、-])\s*([0-9]{1,3})"
            r"\s*(?:日|小时|小時)?\s*(?:均线|均線|MA)",
            question,
            flags=re.IGNORECASE,
        )
        windows = (
            [int(shared_unit_pair.group(1)), int(shared_unit_pair.group(2))]
            if shared_unit_pair
            else [
                int(value)
                for value in re.findall(
                    r"(?<![0-9])([0-9]{1,3})\s*(?:日|小时|小時)?\s*(?:均线|均線|MA)",
                    question,
                    flags=re.IGNORECASE,
                )
            ]
        )
        if len(windows) >= 2:
            fast_window, slow_window = sorted(windows[:2])
        else:
            fast_window, slow_window = 20, 60
            warnings.append("未识别到两条均线参数，已采用 MVP 默认值 20/60。")

        years = [int(value) for value in re.findall(r"(?<!\d)(20\d{2})(?!\d)", question)]
        start_date = date(min(years), 1, 1) if years else None
        end_date = date(max(years), 12, 31) if len(years) >= 2 else None
        timeframe = parse_timeframe(question)
        fee_bps = self._number_near("手续费|手續費|fee", question)
        slippage_bps = self._number_near("滑点|滑點|slippage", question)
        initial_cash = self._initial_cash(question)

        spec = StrategySpec(
            timeframe=timeframe,
            fast_window=fast_window,
            slow_window=slow_window,
            start_date=start_date,
            end_date=end_date,
            fee_bps=2.0 if fee_bps is None else fee_bps,
            slippage_bps=3.0 if slippage_bps is None else slippage_bps,
            initial_cash=100_000.0 if initial_cash is None else initial_cash,
        )
        return Interpretation(spec=spec, interpreter=self.name, warnings=warnings)


class OpenAICompatibleStrategyInterpreter:
    name = "openai-compatible"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.fallback = RuleBasedStrategyInterpreter()

    async def interpret(self, question: str) -> Interpretation:
        if not self.settings.llm_api_key:
            return await self.fallback.interpret(question)

        client = AsyncOpenAI(
            api_key=self.settings.llm_api_key,
            base_url=self.settings.llm_base_url,
        )
        schema = StrategySpec.model_json_schema()
        try:
            response = await client.chat.completions.create(
                model=self.settings.llm_model,
                response_format={"type": "json_object"},
                temperature=0,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是策略规格编译器，不提供投资建议。"
                            "仅将用户描述转换成 JSON。当前只支持 long-only SMA crossover，"
                            "execution 必须是 next_bar_open。不得生成代码、SQL 或额外字段。"
                            f"JSON Schema: {json.dumps(schema, ensure_ascii=False)}"
                        ),
                    },
                    {"role": "user", "content": question},
                ],
            )
            content = response.choices[0].message.content or "{}"
            spec = StrategySpec.model_validate_json(content)
            return Interpretation(spec=spec, interpreter=self.name, warnings=[])
        except (OpenAIError, ValidationError) as exc:
            fallback = await self.fallback.interpret(question)
            return Interpretation(
                spec=fallback.spec,
                interpreter=fallback.interpreter,
                warnings=[
                    f"模型编译失败，已安全回退到规则解释器：{type(exc).__name__}",
                    *fallback.warnings,
                ],
            )


def build_interpreter(settings: Settings) -> OpenAICompatibleStrategyInterpreter:
    return OpenAICompatibleStrategyInterpreter(settings)
