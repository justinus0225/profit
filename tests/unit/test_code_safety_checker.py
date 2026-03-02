"""CodeSafetyChecker 단위 테스트 — 보안 필터링 검증.

LLM 생성 코드의 보안 검증이 가장 중요한 테스트.
모든 알려진 우회 패턴을 커버한다.
"""

from __future__ import annotations

import pytest

from src.agents.quant.strategy_generator import CodeSafetyChecker, StrategyGenerator


@pytest.fixture
def checker() -> CodeSafetyChecker:
    return CodeSafetyChecker()


class TestCodeSafetyChecker:
    # ── 안전한 코드 ──

    def test_safe_simple_function(self, checker: CodeSafetyChecker) -> None:
        code = '''
def strategy_fn(closes, highs, lows, volumes):
    if len(closes) < 14:
        return {"signal": "HOLD", "confidence": 0, "reason": ""}
    avg = sum(closes[-14:]) / 14
    if closes[-1] < avg * 0.95:
        return {"signal": "BUY", "confidence": 0.7, "reason": "Below MA"}
    return {"signal": "HOLD", "confidence": 0.5, "reason": "Neutral"}
'''
        is_safe, violations = checker.check(code)
        assert is_safe
        assert len(violations) == 0

    def test_safe_with_math(self, checker: CodeSafetyChecker) -> None:
        code = '''
import math

def strategy_fn(closes, highs, lows, volumes):
    mean = sum(closes) / len(closes)
    var = sum((x - mean) ** 2 for x in closes) / len(closes)
    std = math.sqrt(var)
    return {"signal": "HOLD", "confidence": 0.5, "reason": str(std)}
'''
        is_safe, violations = checker.check(code)
        assert is_safe

    def test_safe_with_statistics(self, checker: CodeSafetyChecker) -> None:
        code = '''
import statistics

def strategy_fn(closes, highs, lows, volumes):
    median = statistics.median(closes)
    return {"signal": "HOLD", "confidence": 0.5, "reason": str(median)}
'''
        is_safe, violations = checker.check(code)
        assert is_safe

    # ── 위험한 코드: Import ──

    def test_banned_import_os(self, checker: CodeSafetyChecker) -> None:
        code = 'import os\nos.system("rm -rf /")'
        is_safe, violations = checker.check(code)
        assert not is_safe
        assert any("os" in v for v in violations)

    def test_banned_import_subprocess(self, checker: CodeSafetyChecker) -> None:
        code = 'import subprocess\nsubprocess.run(["ls"])'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_from_import(self, checker: CodeSafetyChecker) -> None:
        code = 'from os.path import join'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_import_socket(self, checker: CodeSafetyChecker) -> None:
        code = 'import socket\ns = socket.socket()'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_import_http(self, checker: CodeSafetyChecker) -> None:
        code = 'import http.client'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_import_ctypes(self, checker: CodeSafetyChecker) -> None:
        code = 'import ctypes'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_import_importlib(self, checker: CodeSafetyChecker) -> None:
        code = 'import importlib\nimportlib.import_module("os")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_import_pickle(self, checker: CodeSafetyChecker) -> None:
        code = 'import pickle'
        is_safe, violations = checker.check(code)
        assert not is_safe

    # ── 위험한 코드: Builtins ──

    def test_banned_exec(self, checker: CodeSafetyChecker) -> None:
        code = 'exec("import os")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_eval(self, checker: CodeSafetyChecker) -> None:
        code = 'result = eval("1+1")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_dunder_import(self, checker: CodeSafetyChecker) -> None:
        code = '__import__("os").system("ls")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_compile(self, checker: CodeSafetyChecker) -> None:
        code = 'compile("import os", "", "exec")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_open(self, checker: CodeSafetyChecker) -> None:
        code = 'f = open("/etc/passwd")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_getattr(self, checker: CodeSafetyChecker) -> None:
        code = 'getattr(object, "__subclasses__")'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_globals(self, checker: CodeSafetyChecker) -> None:
        code = 'g = globals()'
        is_safe, violations = checker.check(code)
        assert not is_safe

    # ── 위험한 코드: Attribute Access ──

    def test_banned_subclasses(self, checker: CodeSafetyChecker) -> None:
        code = 'x = "".__class__.__subclasses__()'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_class_access(self, checker: CodeSafetyChecker) -> None:
        code = 'x = "".__class__'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_globals_attr(self, checker: CodeSafetyChecker) -> None:
        code = 'x = strategy_fn.__globals__'
        is_safe, violations = checker.check(code)
        assert not is_safe

    def test_banned_builtins_attr(self, checker: CodeSafetyChecker) -> None:
        code = 'x = strategy_fn.__builtins__'
        is_safe, violations = checker.check(code)
        assert not is_safe

    # ── 위험한 코드: Class Definition ──

    def test_banned_class_def(self, checker: CodeSafetyChecker) -> None:
        code = '''
class Exploit:
    def __init__(self):
        import os
        os.system("ls")
'''
        is_safe, violations = checker.check(code)
        assert not is_safe
        assert any("Class" in v for v in violations)

    # ── 구문 에러 ──

    def test_syntax_error(self, checker: CodeSafetyChecker) -> None:
        code = 'def foo( broken'
        is_safe, violations = checker.check(code)
        assert not is_safe
        assert any("SyntaxError" in v for v in violations)


class TestStrategyGeneratorExtractCode:
    def test_extract_python_block(self) -> None:
        response = '''Here is the strategy:

```python
def strategy_fn(closes, highs, lows, volumes):
    return {"signal": "HOLD", "confidence": 0.5, "reason": "test"}
```

This strategy uses RSI.'''
        code = StrategyGenerator._extract_code(response)
        assert code is not None
        assert "def strategy_fn" in code

    def test_extract_generic_block(self) -> None:
        response = '''```
def my_strategy(closes, highs, lows, volumes):
    return {"signal": "BUY", "confidence": 0.8, "reason": "x"}
```'''
        code = StrategyGenerator._extract_code(response)
        assert code is not None
        assert "def " in code

    def test_no_code_block(self) -> None:
        response = "Just some text without code."
        code = StrategyGenerator._extract_code(response)
        assert code is None


class TestStrategyGeneratorExecuteSafe:
    def test_execute_valid_code(self) -> None:
        source = '''
def strategy_fn(closes, highs, lows, volumes):
    return {"signal": "HOLD", "confidence": 0.5, "reason": "test"}
'''
        fn = StrategyGenerator._execute_safe(source)
        assert fn is not None
        assert callable(fn)

    def test_execute_invalid_code(self) -> None:
        source = 'this is not valid python'
        fn = StrategyGenerator._execute_safe(source)
        assert fn is None

    def test_execute_finds_non_strategy_fn(self) -> None:
        source = '''
def my_custom_strategy(closes, highs, lows, volumes):
    return {"signal": "BUY", "confidence": 0.8, "reason": "x"}
'''
        fn = StrategyGenerator._execute_safe(source)
        assert fn is not None
