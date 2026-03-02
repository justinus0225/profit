"""LLM 동적 전략 생성기.

LLM에 전략 코드 생성을 요청하고, AST 기반 보안 검증 후
Strategy Registry에 등록한다.

보안 모델:
1. AST 레벨 코드 필터링 (banned imports, builtins, attributes)
2. 제한된 namespace에서 exec (math, statistics만 허용)
3. 기본값 OFF (generation_enabled=False)
"""

from __future__ import annotations

import ast
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from src.agents.quant.strategies.registry import StrategyEntry, StrategyRegistry, StrategyStatus
from src.core.llm.client import LLMResponse, Message, Role

logger = logging.getLogger(__name__)

LLMChatFn = Callable[[list[Message]], Awaitable[LLMResponse]]

# ── 보안 제한 ──

BANNED_IMPORTS = frozenset({
    "os", "sys", "subprocess", "shutil", "socket", "http", "urllib",
    "ctypes", "importlib", "pathlib", "tempfile", "signal", "io",
    "pickle", "shelve", "multiprocessing", "threading", "asyncio",
    "builtins", "code", "codeop", "compileall", "py_compile",
    "webbrowser", "ftplib", "smtplib", "telnetlib", "xmlrpc",
})

BANNED_BUILTINS = frozenset({
    "exec", "eval", "__import__", "compile", "globals", "locals",
    "getattr", "setattr", "delattr", "open", "input", "breakpoint",
    "memoryview", "vars", "dir", "type", "super",
})

BANNED_ATTRIBUTES = frozenset({
    "__subclasses__", "__bases__", "__class__", "__dict__",
    "__globals__", "__code__", "__builtins__", "__import__",
    "__loader__", "__spec__", "__mro__",
})


class CodeSafetyChecker:
    """AST 기반 코드 안전성 검증."""

    def check(self, source: str) -> tuple[bool, list[str]]:
        """코드 안전성 검증.

        Returns:
            (is_safe, list_of_violations)
        """
        violations: list[str] = []

        # 1. 구문 검증
        try:
            tree = ast.parse(source)
        except SyntaxError as e:
            return False, [f"SyntaxError: {e}"]

        # 2. AST 노드 순회
        for node in ast.walk(tree):
            # Import 검사
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module = alias.name.split(".")[0]
                    if module in BANNED_IMPORTS:
                        violations.append(f"Banned import: {alias.name}")

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    module = node.module.split(".")[0]
                    if module in BANNED_IMPORTS:
                        violations.append(f"Banned import from: {node.module}")

            # 함수 호출 검사
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name) and func.id in BANNED_BUILTINS:
                    violations.append(f"Banned builtin call: {func.id}")
                elif isinstance(func, ast.Attribute) and func.attr in BANNED_BUILTINS:
                    violations.append(f"Banned method call: {func.attr}")

            # 속성 접근 검사
            elif isinstance(node, ast.Attribute):
                if node.attr in BANNED_ATTRIBUTES:
                    violations.append(f"Banned attribute access: {node.attr}")

            # 클래스 정의 금지 (전략은 함수로만)
            elif isinstance(node, ast.ClassDef):
                violations.append(f"Class definition not allowed: {node.name}")

        # 3. 문자열 패턴 검사 (AST로 잡히지 않는 것들)
        dangerous_patterns = [
            (r"__import__\s*\(", "Hidden __import__ call"),
            (r"__subclasses__", "Access to __subclasses__"),
            (r"__builtins__", "Access to __builtins__"),
        ]
        for pattern, desc in dangerous_patterns:
            if re.search(pattern, source):
                if desc not in [v for v in violations]:
                    violations.append(f"Dangerous pattern: {desc}")

        is_safe = len(violations) == 0
        if not is_safe:
            logger.warning("Code safety check failed: %s", violations)
        return is_safe, violations


# 생성된 전략에 허용되는 글로벌 네임스페이스
_SAFE_GLOBALS: dict[str, Any] = {
    "__builtins__": {
        "abs": abs, "min": min, "max": max, "sum": sum,
        "len": len, "range": range, "enumerate": enumerate,
        "zip": zip, "map": map, "filter": filter,
        "int": int, "float": float, "bool": bool, "str": str,
        "list": list, "dict": dict, "tuple": tuple, "set": set,
        "round": round, "sorted": sorted, "reversed": reversed,
        "any": any, "all": all, "isinstance": isinstance,
        "True": True, "False": False, "None": None,
        "print": lambda *a, **k: None,  # 무시
    },
}

# 전략 코드 생성 프롬프트 템플릿
_GENERATION_PROMPT = """\
You are a quantitative trading strategy developer.
Generate a Python trading strategy function for cryptocurrency spot trading.

Requirements:
1. The function MUST be named `strategy_fn`
2. It receives parameters: (closes: list[float], highs: list[float], lows: list[float], volumes: list[float]) -> dict
3. It must return a dict with: {{"signal": "BUY"|"SELL"|"HOLD", "confidence": float (0-1), "reason": str}}
4. Use only basic Python math operations - NO external imports
5. You can use: abs, min, max, sum, len, range, round
6. Implement the strategy logic inline (calculate indicators from raw data)

Market context:
{market_context}

Existing strategy performance:
{performance_context}

Generate a NEW strategy that addresses the gaps in existing strategy performance.
Focus on {strategy_focus}.

Output ONLY the Python function code, wrapped in ```python``` code fences.
"""


class StrategyGenerator:
    """LLM 기반 전략 코드 생성기."""

    def __init__(
        self,
        registry: StrategyRegistry,
        safety_checker: CodeSafetyChecker | None = None,
    ) -> None:
        self._registry = registry
        self._checker = safety_checker or CodeSafetyChecker()

    async def generate(
        self,
        llm_chat: LLMChatFn,
        market_context: str = "",
        performance_context: str = "",
        strategy_focus: str = "mean-reversion with adaptive thresholds",
    ) -> StrategyEntry | None:
        """LLM에 전략 생성을 요청한다.

        Returns:
            생성된 StrategyEntry (CANDIDATE 상태) 또는 None (실패 시)
        """
        prompt = _GENERATION_PROMPT.format(
            market_context=market_context or "General crypto market conditions",
            performance_context=performance_context or "No existing performance data",
            strategy_focus=strategy_focus,
        )

        try:
            response = await llm_chat([
                Message(role=Role.SYSTEM, content="You are a quantitative strategy code generator."),
                Message(role=Role.USER, content=prompt),
            ])
        except Exception:
            logger.exception("LLM strategy generation failed")
            return None

        # 코드 추출
        source = self._extract_code(response.content)
        if not source:
            logger.warning("No code block found in LLM response")
            return None

        # 안전성 검증
        is_safe, violations = self._checker.check(source)
        if not is_safe:
            logger.warning(
                "Generated code failed safety check: %s", violations
            )
            return None

        # 제한된 namespace에서 실행
        strategy_fn = self._execute_safe(source)
        if strategy_fn is None:
            return None

        # Registry에 등록
        name = f"generated_{uuid.uuid4().hex[:8]}"
        entry = StrategyEntry(
            name=name,
            strategy_fn=strategy_fn,
            status=StrategyStatus.CANDIDATE,
            source="generated",
            parameters={"source_code": source},
        )

        if self._registry.register(entry):
            logger.info("Generated strategy registered: %s", name)
            return entry

        return None

    @staticmethod
    def _extract_code(llm_response: str) -> str | None:
        """LLM 응답에서 Python 코드 블록 추출."""
        # ```python ... ``` 패턴
        pattern = r"```python\s*\n(.*?)```"
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            return match.group(1).strip()

        # ``` ... ``` 패턴 (언어 지정 없음)
        pattern = r"```\s*\n(.*?)```"
        match = re.search(pattern, llm_response, re.DOTALL)
        if match:
            code = match.group(1).strip()
            if "def " in code:
                return code

        return None

    @staticmethod
    def _execute_safe(source: str) -> Any | None:
        """제한된 네임스페이스에서 전략 함수를 실행한다."""
        namespace: dict[str, Any] = dict(_SAFE_GLOBALS)
        try:
            exec(compile(source, "<generated_strategy>", "exec"), namespace)  # noqa: S102
        except Exception:
            logger.exception("Failed to execute generated strategy code")
            return None

        # strategy_fn 찾기
        fn = namespace.get("strategy_fn")
        if fn is None:
            # 다른 이름의 함수 찾기
            for key, val in namespace.items():
                if callable(val) and not key.startswith("_"):
                    fn = val
                    break

        if fn is None:
            logger.warning("No callable strategy function found in generated code")
        return fn
