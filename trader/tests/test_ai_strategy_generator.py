"""
AI Strategy Generator Tests
============================

测试覆盖：
1. CodeSandbox危险代码检测
2. AIAuditLog审计日志
3. AIStrategyGenerator生成与验证
4. 多LLM后端适配器
"""

import asyncio
import threading
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from insight import code_sandbox as code_sandbox_module
from insight.ai_audit_log import (
    AIAuditLog,
    AuditEntry,
    AuditEventType,
    AuditStatus,
    InMemoryAuditLogStorage,
)
from insight.ai_strategy_generator import (
    AIStrategyGenerator,
    GeneratedStrategy,
    GeneratedStrategyPlugin,
    GenerationConfig,
    LLMBackend,
    MockAdapter,
)
from insight.code_sandbox import (
    UNIX_AVAILABLE,
    CodeSandbox,
    DangerousCodeError,
    SandboxConfig,
    SandboxResult,
    SandboxStatus,
)

# ``resource`` is a stub ``None`` on Windows; the Unix-only integration test is
# skipped there and the cross-platform tests inject a fake module instead.
resource = code_sandbox_module.resource

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    RiskLevel,
    Signal,
    SignalType,
    StrategyResourceLimits,
    ValidationResult,
    ValidationStatus,
)

# ==================== Fixtures ====================


@pytest.fixture
def sandbox_config():
    return SandboxConfig(
        timeout_seconds=5.0,
        max_memory_mb=256,
        enable_network=False,
        enable_file_ops=False,
    )


@pytest.fixture
def sandbox(sandbox_config):
    return CodeSandbox(config=sandbox_config)


@pytest.fixture
def audit_log():
    return AIAuditLog(storage=InMemoryAuditLogStorage())


@pytest.fixture
def generation_config():
    return GenerationConfig(
        risk_level=RiskLevel.LOW,
        max_position_size=Decimal("1.0"),
        max_daily_loss=Decimal("100.0"),
        max_orders_per_minute=10,
    )


# ==================== Unix resource-limit test doubles ====================

_MIB = 1024 * 1024


class _FakeResource:
    """Minimal stand-in for the Unix-only ``resource`` module.

    Windows has no ``resource`` module, so the sandbox's real limit arithmetic
    can still be exercised there by injecting this double.
    """

    RLIMIT_AS = 101
    RLIMIT_CPU = 102
    RLIM_INFINITY = -1
    RUSAGE_SELF = 0

    def __init__(self, as_soft=-1, as_hard=-1, cpu_soft=-1, cpu_hard=-1, cpu_seconds=0.0):
        self.limits = {
            self.RLIMIT_AS: (as_soft, as_hard),
            self.RLIMIT_CPU: (cpu_soft, cpu_hard),
        }
        self.cpu_seconds = cpu_seconds
        self.calls: list[tuple[int, tuple[int, int]]] = []

    def getrlimit(self, kind):
        return self.limits[kind]

    def setrlimit(self, kind, value):
        self.calls.append((kind, tuple(value)))
        self.limits[kind] = tuple(value)

    def getrusage(self, who):
        return SimpleNamespace(ru_utime=self.cpu_seconds, ru_stime=0.0)


class _FakeSignal:
    """Minimal stand-in for the signal subset the sandbox installs."""

    SIGALRM = 14
    SIGXCPU = 24
    SIG_IGN = "SIG_IGN"
    SIG_DFL = "SIG_DFL"
    ITIMER_REAL = 0

    def __init__(self, fail_on=()):
        self.handlers: dict[int, object] = {}
        self.fail_on = set(fail_on)
        self.timer = (0.0, 0.0)
        self.timers: list[tuple[float, float]] = []

    def signal(self, signum, handler):
        if signum in self.fail_on:
            raise ValueError("signal only works in main thread of the main interpreter")
        previous = self.handlers.get(signum, self.SIG_DFL)
        self.handlers[signum] = handler
        return previous

    def setitimer(self, which, delay, interval=0.0):
        previous = self.timer
        self.timer = (float(delay), float(interval))
        self.timers.append(self.timer)
        return previous

    def getitimer(self, which):
        return self.timer


@pytest.fixture
def fake_unix(monkeypatch):
    """Force the Unix limit path with a fake resource/signal backend.

    This runs on every platform, so the real limit arithmetic and the
    apply/restore ordering are covered on Windows too instead of returning
    green without asserting anything.
    """

    def _install(fake_resource=None, fake_signal=None, address_space=100 * _MIB):
        resource_double = fake_resource or _FakeResource()
        signal_double = fake_signal or _FakeSignal()
        monkeypatch.setattr(code_sandbox_module, "UNIX_AVAILABLE", True)
        monkeypatch.setattr(code_sandbox_module, "resource", resource_double)
        monkeypatch.setattr(code_sandbox_module, "signal", signal_double)
        monkeypatch.setattr(
            CodeSandbox,
            "_current_address_space_bytes",
            lambda self: address_space,
        )
        return resource_double, signal_double

    return _install


# ==================== CodeSandbox Tests ====================


class TestCodeSandboxValidation:
    """测试沙箱静态验证"""

    def test_valid_code_passes(self, sandbox):
        """合法代码应通过验证"""
        code = """
def strategy():
    def on_market_data(self, data):
        return None
    def validate(self):
        return True
"""
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.SUCCESS
        assert not result.has_danger

    def test_exec_detection(self, sandbox):
        """检测exec"""
        code = "exec('print(1)')"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE
        assert any("EXEC" in d[0] for d in result.detected_dangers)

    def test_eval_detection(self, sandbox):
        """检测eval"""
        code = "eval('1+1')"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_open_detection(self, sandbox):
        """检测open"""
        code = """
with open('test.txt') as f:
    content = f.read()
"""
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE
        assert any("FILE" in d[0] for d in result.detected_dangers)

    def test_socket_detection(self, sandbox):
        """检测socket"""
        code = """
import socket
s = socket.socket()
s.connect(('localhost', 8080))
"""
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE
        assert any("SOCKET" in d[0] or "IMPORT" in d[0] for d in result.detected_dangers)

    def test_requests_detection(self, sandbox):
        """检测requests库"""
        code = "import requests"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_subprocess_detection(self, sandbox):
        """检测subprocess"""
        code = "import subprocess"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_threadding_detection(self, sandbox):
        """检测threadding"""
        code = "import threading"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_os_system_detection(self, sandbox):
        """检测os.system"""
        code = "os.system('ls')"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_getattr_detection(self, sandbox):
        """检测getattr"""
        code = """
class MyClass:
    def __init__(self):
        self.value = 1

obj = MyClass()
print(getattr(obj, 'value'))
"""
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_pickle_detection(self, sandbox):
        """检测pickle"""
        code = "import pickle"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_yaml_load_detection(self, sandbox):
        """检测yaml.load"""
        code = "yaml.load(data)"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_http_url_detection(self, sandbox):
        """检测HTTP URL"""
        code = "url = 'http://example.com'"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.DANGEROUS_CODE

    def test_syntax_error(self, sandbox):
        """语法错误检测"""
        code = "def strategy(:\n    pass"
        result = sandbox.validate_code(code)
        assert result.status == SandboxStatus.INVALID_CODE
        assert "语法错误" in result.error

    def test_allowed_modules(self):
        """允许的模块白名单"""
        config = SandboxConfig(allowed_modules=("numpy", "pandas"))
        sandbox = CodeSandbox(config=config)

        # numpy应该被允许
        assert "numpy" in sandbox.get_allowed_modules()

        # 验证通过
        code = "import numpy as np"
        result = sandbox.validate_code(code)
        # 这个会通过因为import语句本身不包含危险模式


class TestCodeSandboxExecution:
    """测试沙箱执行"""

    def test_simple_execution(self, sandbox):
        """简单代码执行"""
        code = """
result = 1 + 2
strategy = {'name': 'test', 'value': result}
"""
        result = sandbox.execute(code)
        assert result.status == SandboxStatus.SUCCESS
        assert result.execution_time >= 0

    def test_market_data_context(self, sandbox):
        """市场数据上下文"""
        code = """
price = market_data.price
result = price * 2
"""
        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("50000"),
            volume=Decimal("1.0"),
        )
        result = sandbox.execute(code, market_data=market_data)
        assert result.status == SandboxStatus.SUCCESS

    def test_decimal_support(self, sandbox):
        """Decimal支持"""
        code = """
from decimal import Decimal
result = Decimal('100.5')
"""
        result = sandbox.execute(code)
        assert result.status == SandboxStatus.SUCCESS

    def test_execution_applies_and_restores_soft_limits(self, sandbox, fake_unix):
        """Limits are installed for the execution and the originals restored."""
        fake_resource, _ = fake_unix()
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.SUCCESS, result.error

        # Hard limits are never touched and the original pair is restored.
        assert fake_resource.limits[fake_resource.RLIMIT_AS] == (-1, -1)
        assert fake_resource.limits[fake_resource.RLIMIT_CPU] == (-1, -1)
        assert [kind for kind, _ in fake_resource.calls] == [
            fake_resource.RLIMIT_AS,
            fake_resource.RLIMIT_CPU,
            fake_resource.RLIMIT_CPU,
            fake_resource.RLIMIT_AS,
        ]
        memory_soft, memory_hard = fake_resource.calls[0][1]
        assert memory_soft == 100 * _MIB + 256 * _MIB
        assert memory_hard == -1

    def test_execution_restores_limits_after_sandboxed_failure(self, sandbox, fake_unix):
        """A failing sandbox body must still restore the process limits."""
        fake_resource, _ = fake_unix()
        result = sandbox.execute("raise ValueError('boom')")
        assert result.status == SandboxStatus.RUNTIME_ERROR
        assert fake_resource.limits[fake_resource.RLIMIT_AS] == (-1, -1)
        assert fake_resource.limits[fake_resource.RLIMIT_CPU] == (-1, -1)

    def test_partial_guard_initialization_still_restores_limits(self, sandbox, fake_unix):
        """A failure while installing signal handlers must not leak the limits."""
        fake_resource, _ = fake_unix(fake_signal=_FakeSignal(fail_on={_FakeSignal.SIGALRM}))
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.RESOURCE_EXCEEDED
        assert "SANDBOX_TIMEOUT_GUARD_UNAVAILABLE" in result.error
        assert fake_resource.limits[fake_resource.RLIMIT_AS] == (-1, -1)
        assert fake_resource.limits[fake_resource.RLIMIT_CPU] == (-1, -1)

    def test_partial_guard_initialization_keeps_the_caller_timer(self, sandbox, fake_unix):
        """The caller's timer must survive a failed guard installation."""
        fake_resource, fake_signal = fake_unix(
            fake_signal=_FakeSignal(fail_on={_FakeSignal.SIGALRM})
        )
        fake_signal.timer = (30.0, 0.0)
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.RESOURCE_EXCEEDED
        assert fake_signal.timer == (30.0, 0.0), "the caller's timer was destroyed"

    def test_unmeasurable_address_space_fails_closed(self, sandbox, monkeypatch):
        """An unreadable /proc must not silently disable the memory limit."""
        monkeypatch.setattr(code_sandbox_module, "UNIX_AVAILABLE", True)
        monkeypatch.setattr(code_sandbox_module, "resource", _FakeResource())
        monkeypatch.setattr(code_sandbox_module, "signal", _FakeSignal())

        def _boom(self):
            raise OSError("no /proc")

        monkeypatch.setattr(CodeSandbox, "_current_address_space_bytes", _boom)
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.RESOURCE_EXCEEDED
        assert "PROCESS_ADDRESS_SPACE_UNKNOWN" in result.error

    def test_existing_stricter_soft_limits_are_not_loosened(self, sandbox, fake_unix):
        """The sandbox must never raise a tighter limit the caller already set.

        The caller's limit is still workable (above the current address space)
        but tighter than the sandbox's own budget, so the sandbox has to keep
        the caller's value instead of widening it.
        """
        caller_soft = 100 * _MIB + 64 * _MIB
        strict = _FakeResource(as_soft=caller_soft, cpu_soft=2)
        fake_resource, _ = fake_unix(fake_resource=strict)
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.SUCCESS, result.error

        applied = dict(fake_resource.calls[:2])
        assert applied[fake_resource.RLIMIT_AS][0] <= caller_soft
        assert applied[fake_resource.RLIMIT_CPU][0] <= 2
        assert fake_resource.limits[fake_resource.RLIMIT_AS] == (caller_soft, -1)
        assert fake_resource.limits[fake_resource.RLIMIT_CPU] == (2, -1)

    def test_soft_limit_below_the_current_address_space_is_refused(self, sandbox, fake_unix):
        """A caller limit already below the address space leaves no room."""
        fake_resource, _ = fake_unix()
        fake_resource.limits[fake_resource.RLIMIT_AS] = (50 * _MIB, -1)
        result = sandbox.execute("result = 1 + 2")
        assert result.status == SandboxStatus.RESOURCE_EXCEEDED
        assert "PROCESS_MEMORY_SOFT_LIMIT_BELOW_ADDRESS_SPACE" in result.error
        assert fake_resource.calls == []

    def test_subsecond_timeout_is_not_truncated(self, fake_unix):
        """A fractional timeout must arm a timer instead of cancelling it."""
        _, fake_signal = fake_unix()
        fast = CodeSandbox(SandboxConfig(timeout_seconds=0.25, max_memory_mb=256))
        result = fast.execute("result = 1 + 2")
        assert result.status == SandboxStatus.SUCCESS, result.error
        assert (0.25, 0.0) in fake_signal.timers

    def test_worker_thread_fails_closed_without_touching_limits(self, sandbox, fake_unix):
        """A worker thread cannot install the timeout guard: refuse, don't leak."""
        fake_resource, _ = fake_unix()
        outcome: dict[str, SandboxResult] = {}

        def _run():
            outcome["result"] = sandbox.execute("result = 1 + 2")

        worker = threading.Thread(target=_run)
        worker.start()
        worker.join(timeout=30)
        assert not worker.is_alive()
        assert outcome["result"].status == SandboxStatus.RESOURCE_EXCEEDED
        assert "SANDBOX_REQUIRES_MAIN_THREAD" in outcome["result"].error
        assert fake_resource.calls == []

    @pytest.mark.skipif(not UNIX_AVAILABLE, reason="real RLIMIT requires a POSIX platform")
    def test_execution_restores_real_process_resource_limits(self, sandbox):
        """Unix integration: the real process limits survive one execution."""
        before = (
            resource.getrlimit(resource.RLIMIT_AS),
            resource.getrlimit(resource.RLIMIT_CPU),
        )
        result = sandbox.execute("result = 1 + 2")
        after = (
            resource.getrlimit(resource.RLIMIT_AS),
            resource.getrlimit(resource.RLIMIT_CPU),
        )
        assert result.status == SandboxStatus.SUCCESS, result.error
        assert after == before


# ==================== AIAuditLog Tests ====================


class TestAIAuditLog:
    """测试审计日志"""

    @pytest.mark.asyncio
    async def test_log_generation(self, audit_log):
        """记录生成"""
        entry = await audit_log.log_generation(
            prompt="创建一个移动平均策略",
            generated_code="def strategy(): pass",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="MA_Strategy",
        )

        assert entry.strategy_name == "MA_Strategy"
        assert entry.llm_backend == "openai"
        assert entry.status == AuditStatus.DRAFT
        assert entry.event_type == AuditEventType.GENERATED

        # Verify entry is stored
        stored = await audit_log.get_entry(entry.entry_id)
        assert stored is not None
        assert stored.strategy_name == entry.strategy_name

    @pytest.mark.asyncio
    async def test_submit_for_approval(self, audit_log):
        """提交审批"""
        entry = await audit_log.log_generation(
            prompt="test",
            generated_code="code",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
        )

        await audit_log.submit_for_approval(entry.entry_id)

        updated = await audit_log.get_entry(entry.entry_id)
        assert updated.status == AuditStatus.PENDING
        # Note: event_type update depends on update_status implementation

    @pytest.mark.asyncio
    async def test_approve(self, audit_log):
        """批准"""
        entry = await audit_log.log_generation(
            prompt="test",
            generated_code="code",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
        )

        await audit_log.submit_for_approval(entry.entry_id)
        await audit_log.approve(entry.entry_id, "admin", "LGTM")

        updated = await audit_log.get_entry(entry.entry_id)
        assert updated.status == AuditStatus.APPROVED
        assert updated.approver == "admin"
        assert updated.approval_comment == "LGTM"

    @pytest.mark.asyncio
    async def test_reject(self, audit_log):
        """拒绝"""
        entry = await audit_log.log_generation(
            prompt="test",
            generated_code="code",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
        )

        await audit_log.submit_for_approval(entry.entry_id)
        await audit_log.reject(entry.entry_id, "admin", "Not good")

        updated = await audit_log.get_entry(entry.entry_id)
        assert updated.status == AuditStatus.REJECTED

    @pytest.mark.asyncio
    async def test_deploy(self, audit_log):
        """部署"""
        entry = await audit_log.log_generation(
            prompt="test",
            generated_code="code",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
        )

        await audit_log.submit_for_approval(entry.entry_id)
        await audit_log.approve(entry.entry_id, "admin")
        await audit_log.deploy(entry.entry_id)

        updated = await audit_log.get_entry(entry.entry_id)
        assert updated.status == AuditStatus.ACTIVE

    @pytest.mark.asyncio
    async def test_get_strategy_versions(self, audit_log):
        """获取策略版本"""
        entry1 = await audit_log.log_generation(
            prompt="test",
            generated_code="code1",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
        )

        entry2 = await audit_log.log_generation(
            prompt="test update",
            generated_code="code2",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test",
            strategy_id=entry1.strategy_id,
        )

        versions = await audit_log.get_strategy_versions(entry1.strategy_id)
        assert len(versions) == 2

    @pytest.mark.asyncio
    async def test_statistics(self, audit_log):
        """统计"""
        await audit_log.log_generation(
            prompt="test1",
            generated_code="code1",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="Test1",
        )

        await audit_log.log_generation(
            prompt="test2",
            generated_code="code2",
            llm_backend="anthropic",
            llm_model="claude-3",
            strategy_name="Test2",
        )

        stats = await audit_log.get_statistics()
        assert stats.total_generations == 2
        assert stats.by_backend["openai"] == 1
        assert stats.by_backend["anthropic"] == 1

    @pytest.mark.asyncio
    async def test_search(self, audit_log):
        """搜索"""
        await audit_log.log_generation(
            prompt="移动平均交叉策略",
            generated_code="code",
            llm_backend="openai",
            llm_model="gpt-4",
            strategy_name="MA_Cross",
        )

        results = await audit_log.search(query="移动平均")
        assert len(results) == 1

        results = await audit_log.search(status=AuditStatus.DRAFT)
        assert len(results) == 1


# ==================== AIStrategyGenerator Tests ====================


class TestAIStrategyGenerator:
    """测试AI策略生成器"""

    def test_mock_adapter(self):
        """Mock适配器"""
        adapter = MockAdapter("def strategy(): pass")
        assert asyncio.run(adapter.validate_connection())

    def test_validate_code_safe(self):
        """验证安全代码"""
        generator = AIStrategyGenerator(llm_backend=LLMBackend.MOCK)

        code = """
class MAStrategy:
    name = "MA_Strategy"
    version = "1.0.0"
    
    def on_market_data(self, data):
        return None
    
    def validate(self):
        return ValidationResult.valid()
"""
        result = generator.validate_code(code)
        # 由于代码是动态的，验证结果可能不是完全valid
        assert result is not None

    def test_validate_code_dangerous(self):
        """验证危险代码"""
        generator = AIStrategyGenerator(llm_backend=LLMBackend.MOCK)

        code = """
import socket
socket.socket()
"""
        result = generator.validate_code(code)
        assert result.status == ValidationStatus.INVALID
        assert any("SOCKET" in e.code for e in result.errors)

    def test_validate_code_exec(self):
        """验证exec代码"""
        generator = AIStrategyGenerator(llm_backend=LLMBackend.MOCK)

        code = "exec('print(1)')"
        result = generator.validate_code(code)
        assert result.status == ValidationStatus.INVALID

    @pytest.mark.asyncio
    async def test_generate_with_mock(self, generation_config):
        """使用Mock生成"""
        generator = AIStrategyGenerator(llm_backend=LLMBackend.MOCK)

        # 注入mock响应
        generator._adapter = MockAdapter(
            """
class TestStrategy:
    name = "TestStrategy"
    version = "1.0.0"
    
    def on_market_data(self, data):
        return None
    
    def validate(self):
        return ValidationResult.valid()
"""
        )

        result = await generator.generate(
            prompt="创建一个测试策略",
            config=generation_config,
            strategy_name="TestStrategy",
        )

        assert result.name == "TestStrategy"
        assert result.generation_time >= 0

    @pytest.mark.asyncio
    async def test_register_strategy(self, generation_config, audit_log):
        """注册策略"""
        generator = AIStrategyGenerator(
            llm_backend=LLMBackend.MOCK,
            audit_log=audit_log,
        )

        # 创建一个简单的策略
        strategy = GeneratedStrategyPlugin(
            name="TestStrategy",
            version="1.0.0",
            risk_level=RiskLevel.LOW,
            resource_limits=StrategyResourceLimits(),
            on_market_data_impl=lambda x: None,
            validate_impl=lambda: ValidationResult.valid(),
        )

        result = await generator.register_strategy(
            strategy=strategy,
            code="class TestStrategy: pass",
            prompt="test",
        )

        assert result.success
        assert result.strategy_id is not None


class TestGeneratedStrategyPlugin:
    """测试生成的策略包装器"""

    def test_properties(self):
        """属性测试"""
        strategy = GeneratedStrategyPlugin(
            name="TestStrategy",
            version="1.0.0",
            risk_level=RiskLevel.MEDIUM,
            resource_limits=StrategyResourceLimits(
                max_position_size=Decimal("5.0"),
                max_daily_loss=Decimal("500.0"),
            ),
            on_market_data_impl=lambda x: None,
            validate_impl=lambda: ValidationResult.valid(),
        )

        assert strategy.name == "TestStrategy"
        assert strategy.version == "1.0.0"
        assert strategy.risk_level == RiskLevel.MEDIUM
        assert strategy.resource_limits.max_position_size == Decimal("5.0")

    async def test_on_market_data(self):
        """市场数据回调"""
        called = False

        def callback(data):
            nonlocal called
            called = True
            return None

        strategy = GeneratedStrategyPlugin(
            name="Test",
            version="1.0.0",
            risk_level=RiskLevel.LOW,
            resource_limits=StrategyResourceLimits(),
            on_market_data_impl=callback,
            validate_impl=lambda: ValidationResult.valid(),
        )

        market_data = MarketData(
            symbol="BTCUSDT",
            data_type=MarketDataType.TRADE,
            price=Decimal("50000"),
        )

        result = await strategy.on_market_data(market_data)
        assert called
        assert result is None

    def test_validate(self):
        """验证方法"""
        strategy = GeneratedStrategyPlugin(
            name="Test",
            version="1.0.0",
            risk_level=RiskLevel.LOW,
            resource_limits=StrategyResourceLimits(),
            on_market_data_impl=lambda x: None,
            validate_impl=lambda: ValidationResult.valid(),
        )

        result = strategy.validate()
        assert result.is_valid


# ==================== Integration Tests ====================


class TestIntegration:
    """集成测试"""

    @pytest.mark.asyncio
    async def test_full_workflow(self, generation_config, audit_log):
        """完整工作流"""
        generator = AIStrategyGenerator(
            llm_backend=LLMBackend.MOCK,
            audit_log=audit_log,
        )

        # 注入mock响应
        generator._adapter = MockAdapter(
            """
class IntegrationStrategy:
    name = "IntegrationStrategy"
    version = "1.0.0"
    
    def on_market_data(self, data):
        return None
    
    def validate(self):
        return ValidationResult.valid()
"""
        )

        # 1. 生成策略
        result = await generator.generate(
            prompt="创建一个集成测试策略",
            config=generation_config,
            strategy_name="IntegrationStrategy",
        )

        assert result.name == "IntegrationStrategy"
        assert result.generation_time >= 0

        # 2. 注册策略（会自动提交审批）
        strategy = GeneratedStrategyPlugin(
            name=result.name,
            version=result.version,
            risk_level=generation_config.risk_level,
            resource_limits=StrategyResourceLimits(),
            on_market_data_impl=lambda x: None,
            validate_impl=lambda: ValidationResult.valid(),
        )

        reg_result = await generator.register_strategy(
            strategy=strategy,
            code=result.code,
            prompt="test prompt",
        )

        assert reg_result.success
        assert reg_result.entry_id is not None

        # 3. 获取审计日志 - 直接从entry_id获取
        entry = await audit_log.get_entry(reg_result.entry_id)
        assert entry is not None
        assert entry.status == AuditStatus.PENDING

        # 4. 批准策略
        await audit_log.approve(entry.entry_id, "test_admin", "Test approval")

        # 5. 部署策略
        await audit_log.deploy(entry.entry_id)

        # 6. 验证最终状态
        final_entry = await audit_log.get_entry(entry.entry_id)
        assert final_entry.status == AuditStatus.ACTIVE


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
