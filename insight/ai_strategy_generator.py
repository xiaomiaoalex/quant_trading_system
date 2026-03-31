"""
AIStrategyGenerator - AI策略生成器
==================================

核心职责：
1. 基于用户提示生成策略代码
2. 多LLM后端支持（OpenAI/Anthropic/本地模型）
3. 代码验证与安全检查
4. HITL审批对接
5. StrategyPlugin协议实现

设计原则：
1. 协议合规：生成的策略必须实现StrategyPlugin接口
2. 安全第一：所有代码必须经过沙箱验证
3. 审批流程：AI生成代码必须经过人工审批才能部署
4. 完整审计：所有操作都记录到审计日志

使用方式：
    generator = AIStrategyGenerator(
        llm_backend="openai",
        llm_model="gpt-4",
        audit_log=audit_log,
        sandbox=sandbox,
    )
    
    # 生成策略
    result = await generator.generate(
        prompt="创建一个移动平均交叉策略",
        config=GenerationConfig(risk_level=RiskLevel.LOW)
    )
    
    # 验证代码
    validation = generator.validate_code(result.code)
    
    # 注册策略
    registration = generator.register_strategy(result.strategy)
"""

from __future__ import annotations

import asyncio
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Coroutine, Dict, List, Optional, Sequence

# 尝试导入LLM SDK（可选）
try:
    import openai
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from trader.core.application.strategy_protocol import (
    MarketData,
    MarketDataType,
    RiskLevel,
    Signal,
    SignalType,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationError,
    ValidationResult,
    ValidationStatus,
)
from insight.code_sandbox import CodeSandbox, SandboxConfig, SandboxResult, SandboxStatus
from insight.ai_audit_log import AIAuditLog, AuditEntry, AuditStatus, AuditEventType


class LLMBackend(Enum):
    """支持的LLM后端"""
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"  # 本地模型（如vLLM）
    MOCK = "mock"    # 用于测试


@dataclass
class GenerationConfig:
    """
    策略生成配置
    
    属性：
        risk_level: 策略风险等级
        max_position_size: 最大持仓
        max_daily_loss: 最大日亏损
        max_orders_per_minute: 最大订单频率
        timeout_seconds: 生成超时时间
        require_backtest: 是否要求回测验证
        enable_numpy: 是否允许使用numpy
        enable_pandas: 是否允许使用pandas
    """
    risk_level: RiskLevel = RiskLevel.LOW
    max_position_size: Decimal = Decimal("1.0")
    max_daily_loss: Decimal = Decimal("100.0")
    max_orders_per_minute: int = 10
    timeout_seconds: float = 60.0
    require_backtest: bool = False
    enable_numpy: bool = False
    enable_pandas: bool = False


@dataclass
class GeneratedStrategy:
    """
    生成的策略
    
    属性：
        code: 生成的代码
        name: 策略名称
        version: 版本号
        description: 策略描述
        strategy: StrategyPlugin实例
        validation_result: 验证结果
        generation_time: 生成耗时
        metadata: 扩展元数据
    """
    code: str
    name: str
    version: str
    description: str
    strategy: Optional[StrategyPlugin] = None
    validation_result: Optional[ValidationResult] = None
    generation_time: float = 0.0
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    @property
    def is_valid(self) -> bool:
        """是否通过验证"""
        return self.validation_result is not None and self.validation_result.is_valid


@dataclass
class RegistrationResult:
    """策略注册结果"""
    success: bool
    strategy_id: Optional[str] = None
    entry_id: Optional[str] = None
    error: Optional[str] = None


# ==================== LLM适配器接口 ====================

class LLMAdapter(ABC):
    """LLM适配器基类"""
    
    @abstractmethod
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        """生成文本"""
        pass
    
    @abstractmethod
    async def validate_connection(self) -> bool:
        """验证连接"""
        pass


class OpenAIAdapter(LLMAdapter):
    """OpenAI适配器"""
    
    def __init__(self, api_key: str, model: str = "gpt-4", base_url: Optional[str] = None):
        if not OPENAI_AVAILABLE:
            raise ImportError("openai package not installed")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
    
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""
    
    async def validate_connection(self) -> bool:
        try:
            self._client.models.list()
            return True
        except Exception:
            return False


class AnthropicAdapter(LLMAdapter):
    """Anthropic适配器"""
    
    def __init__(self, api_key: str, model: str = "claude-3-opus-20240229"):
        if not ANTHROPIC_AVAILABLE:
            raise ImportError("anthropic package not installed")
        self._api_key = api_key
        self._model = model
        self._client = anthropic.Anthropic(api_key=api_key)
    
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        response = self._client.messages.create(
            model=self._model,
            system=system_prompt,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.content[0].text
    
    async def validate_connection(self) -> bool:
        try:
            self._client.messages.create(
                model=self._model,
                max_tokens=1,
                messages=[{"role": "user", "content": "test"}],
            )
            return True
        except Exception:
            return False


class MockAdapter(LLMAdapter):
    """Mock适配器（用于测试）"""
    
    def __init__(self, response: str = "def strategy():\n    pass"):
        self._response = response
    
    async def generate(
        self,
        prompt: str,
        system_prompt: str,
        max_tokens: int = 4096,
        temperature: float = 0.7,
    ) -> str:
        return self._response
    
    async def validate_connection(self) -> bool:
        return True


# ==================== 提示词模板 ====================

SYSTEM_PROMPT_TEMPLATE = """你是一个专业的量化交易策略开发助手。

任务：根据用户需求生成Python交易策略代码。

要求：
1. 策略必须实现以下接口：
   - name: str 属性，返回策略名称
   - version: str 属性，返回版本号（semver格式）
   - risk_level: RiskLevel 属性，返回风险等级
   - resource_limits: StrategyResourceLimits 属性，返回资源限制
   - on_market_data(data: MarketData) -> Optional[Signal] 方法
   - validate() -> ValidationResult 方法

2. 风险等级选项：LOW, MEDIUM, HIGH, CRITICAL

3. 只能使用以下模块：
   - math, random, datetime, time, collections, functools, operator, itertools, re
   - decimal, statistics, copy, json, base64, hashlib

4. 禁止：
   - 任何网络请求
   - 文件读写
   - subprocess, threading, multiprocessing
   - exec, eval, compile
   - os, sys, io相关操作

5. 返回格式：只返回Python代码，不要解释。

代码必须能够通过以下验证：
- 语法正确
- 实现所有必需的属性和方法
- 不包含危险操作
"""

USER_PROMPT_TEMPLATE = """生成一个交易策略，需求如下：

{requirements}

策略名称：{name}
风险等级：{risk_level}
最大持仓：{max_position_size}
最大日亏损：{max_daily_loss}

请生成完整的策略代码。"""


# ==================== 动态策略包装器 ====================

class GeneratedStrategyPlugin:
    """
    动态生成的策略包装器
    
    将AI生成的代码包装为符合StrategyPlugin协议的对象。
    """
    
    def __init__(
        self,
        name: str,
        version: str,
        risk_level: RiskLevel,
        resource_limits: StrategyResourceLimits,
        on_market_data_impl: Callable[[MarketData], Optional[Signal]],
        validate_impl: Callable[[], ValidationResult],
    ):
        self._name = name
        self._version = version
        self._risk_level = risk_level
        self._resource_limits = resource_limits
        self._on_market_data = on_market_data_impl
        self._validate = validate_impl
    
    @property
    def name(self) -> str:
        return self._name
    
    @property
    def version(self) -> str:
        return self._version
    
    @version.setter
    def version(self, value: str) -> None:
        """设置策略版本（用于热插拔）"""
        self._version = value
    
    @property
    def risk_level(self) -> RiskLevel:
        return self._risk_level
    
    @property
    def resource_limits(self) -> StrategyResourceLimits:
        return self._resource_limits
    
    async def on_market_data(self, data: MarketData) -> Optional[Signal]:
        """异步市场数据处理 - 支持同步和异步底层实现"""
        # 检查底层实现是否是协程函数
        if asyncio.iscoroutinefunction(self._on_market_data):
            return await self._on_market_data(data)  # type: ignore[misc]
        return self._on_market_data(data)
    
    def validate(self) -> ValidationResult:
        return self._validate()


# ==================== AI策略生成器 ====================

class AIStrategyGenerator:
    """
    AI策略生成器
    
    核心功能：
    1. 多LLM后端支持
    2. 代码生成与验证
    3. 沙箱执行
    4. 审计日志
    
    使用流程：
    1. 初始化生成器
    2. 调用generate()生成策略
    3. 调用validate_code()验证代码
    4. 调用register_strategy()注册策略
    """
    
    def __init__(
        self,
        llm_backend: LLMBackend = LLMBackend.MOCK,
        llm_model: str = "gpt-4",
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        audit_log: Optional[AIAuditLog] = None,
        sandbox: Optional[CodeSandbox] = None,
        hitl_governance: Any = None,  # HITLGovernance
    ):
        self._llm_backend = llm_backend
        self._llm_model = llm_model
        self._audit_log = audit_log
        self._sandbox = sandbox or CodeSandbox()
        self._hitl_governance = hitl_governance
        
        # 初始化LLM适配器
        self._adapter = self._create_adapter(api_key, base_url)
        
        # 生成的代码缓存
        self._generated_strategies: Dict[str, GeneratedStrategy] = {}
    
    def _create_adapter(self, api_key: Optional[str], base_url: Optional[str]) -> LLMAdapter:
        """创建LLM适配器"""
        if self._llm_backend == LLMBackend.OPENAI:
            return OpenAIAdapter(
                api_key=api_key or "",
                model=self._llm_model,
                base_url=base_url,
            )
        elif self._llm_backend == LLMBackend.ANTHROPIC:
            return AnthropicAdapter(
                api_key=api_key or "",
                model=self._llm_model,
            )
        elif self._llm_backend == LLMBackend.LOCAL:
            return OpenAIAdapter(
                api_key="dummy",
                model=self._llm_model,
                base_url=base_url or "http://localhost:8000/v1",
            )
        else:
            return MockAdapter()
    
    async def _call_llm(self, prompt: str) -> str:
        """调用LLM生成代码"""
        system_prompt = SYSTEM_PROMPT_TEMPLATE
        return await self._adapter.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            max_tokens=4096,
            temperature=0.7,
        )
    
    def _parse_strategy_metadata(self, code: str) -> tuple[str, str, str]:
        """从代码中解析策略元数据"""
        import re
        
        # 尝试从注释或字符串中提取
        name_match = re.search(r'name\s*[:=]\s*["\']([^"\']+)["\']', code)
        version_match = re.search(r'version\s*[:=]\s*["\']([^"\']+)["\']', code)
        desc_match = re.search(r'description\s*[:=]\s*["\']([^"\']+)["\']', code)
        
        name = name_match.group(1) if name_match else f"GeneratedStrategy_{uuid.uuid4().hex[:8]}"
        version = version_match.group(1) if version_match else "1.0.0"
        description = desc_match.group(1) if desc_match else "AI生成策略"
        
        return name, version, description
    
    def _extract_strategy_class(self, code: str) -> Optional[type]:
        """
        从代码中提取策略类
        
        警告：此方法使用 exec() 执行动态代码。
        调用前必须先通过 validate_code() 验证代码安全性。
        """
        import re
        import builtins
        
        # 查找class定义
        class_matches = list(re.finditer(r'^class\s+(\w+)\s*[:\(]', code, re.MULTILINE))
        if not class_matches:
            return None
        
        # 获取最后一个类定义
        last_class = class_matches[-1]
        class_name = last_class.group(1)
        
        # 受限的 builtins（只允许安全函数）
        _safe_builtins = {k: v for k, v in vars(builtins).items() if k in [
            'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'chr', 'dict',
            'dir', 'divmod', 'enumerate', 'filter', 'float', 'format',
            'frozenset', 'hash', 'hex', 'int', 'isinstance', 'issubclass',
            'iter', 'len', 'list', 'map', 'max', 'min', 'next', 'oct',
            'ord', 'pow', 'range', 'repr', 'reversed', 'round', 'set',
            'slice', 'sorted', 'str', 'sum', 'tuple', 'zip',
        ]}
        
        # 在受限的命名空间中执行代码
        namespace: dict[str, Any] = {'__builtins__': _safe_builtins}
        try:
            compiled = compile(code, '<generated>', 'exec')
            exec(compiled, namespace)
            result = namespace.get(class_name)
            if result is not None and isinstance(result, type):
                return result
            return None
        except Exception:
            return None
    
    def _create_strategy_instance(
        self,
        code: str,
        name: str,
        version: str,
        risk_level: RiskLevel,
        resource_limits: StrategyResourceLimits,
    ) -> Optional[StrategyPlugin]:
        """创建策略实例"""
        strategy_class = self._extract_strategy_class(code)
        if strategy_class is None:
            return None
        
        try:
            # 实例化策略
            instance = strategy_class()
            
            # 验证必要的属性
            if not hasattr(instance, 'name'):
                setattr(instance, 'name', name)
            if not hasattr(instance, 'version'):
                setattr(instance, 'version', version)
            if not hasattr(instance, 'risk_level'):
                setattr(instance, 'risk_level', risk_level)
            if not hasattr(instance, 'resource_limits'):
                setattr(instance, 'resource_limits', resource_limits)
            if not hasattr(instance, 'on_market_data'):
                def default_on_market_data(data):
                    return None
                setattr(instance, 'on_market_data', default_on_market_data)
            if not hasattr(instance, 'validate'):
                def default_validate():
                    return ValidationResult.valid()
                setattr(instance, 'validate', default_validate)
            
            return instance
        except Exception:
            return None
    
    async def generate(
        self,
        prompt: str,
        config: GenerationConfig,
        strategy_name: Optional[str] = None,
    ) -> GeneratedStrategy:
        """
        生成策略
        
        Args:
            prompt: 用户需求描述
            config: 生成配置
            strategy_name: 策略名称（可选）
            
        Returns:
            GeneratedStrategy: 生成的策略
        """
        import time
        start_time = time.time()
        
        # 构建用户提示
        name = strategy_name or f"GeneratedStrategy_{uuid.uuid4().hex[:8]}"
        user_prompt = USER_PROMPT_TEMPLATE.format(
            requirements=prompt,
            name=name,
            risk_level=config.risk_level.value,
            max_position_size=config.max_position_size,
            max_daily_loss=config.max_daily_loss,
        )
        
        # 调用LLM生成代码
        code = await self._call_llm(user_prompt)
        
        # 解析元数据
        strategy_name, version, description = self._parse_strategy_metadata(code)
        if strategy_name.startswith("GeneratedStrategy"):
            strategy_name = name
        
        # 资源限制
        resource_limits = StrategyResourceLimits(
            max_position_size=config.max_position_size,
            max_daily_loss=config.max_daily_loss,
            max_orders_per_minute=config.max_orders_per_minute,
            timeout_seconds=config.timeout_seconds,
        )
        
        # 验证代码安全性（必须在创建实例之前）
        validation_result = self.validate_code(code)
        if not validation_result.is_valid:
            # 验证失败，返回无效结果
            generation_time = time.time() - start_time
            result = GeneratedStrategy(
                code=code,
                name=strategy_name,
                version=version,
                description=description,
                strategy=None,
                validation_result=validation_result,
                generation_time=generation_time,
                metadata={
                    'llm_backend': self._llm_backend.value,
                    'llm_model': self._llm_model,
                    'risk_level': config.risk_level.value,
                    'prompt': prompt,
                },
            )
            strategy_id = f"{strategy_name}_{version}"
            self._generated_strategies[strategy_id] = result
            return result
        
        # 验证通过后创建策略实例
        strategy = self._create_strategy_instance(
            code=code,
            name=strategy_name,
            version=version,
            risk_level=config.risk_level,
            resource_limits=resource_limits,
        )
        
        generation_time = time.time() - start_time
        
        result = GeneratedStrategy(
            code=code,
            name=strategy_name,
            version=version,
            description=description,
            strategy=strategy,
            validation_result=validation_result,
            generation_time=generation_time,
            metadata={
                'llm_backend': self._llm_backend.value,
                'llm_model': self._llm_model,
                'risk_level': config.risk_level.value,
                'prompt': prompt,
            },
        )
        
        # 缓存结果
        strategy_id = f"{strategy_name}_{version}"
        self._generated_strategies[strategy_id] = result
        
        return result
    
    def validate_code(self, code: str) -> ValidationResult:
        """
        验证代码安全性
        
        Args:
            code: 待验证的代码
            
        Returns:
            ValidationResult: 验证结果
        """
        errors: list[ValidationError] = []
        warnings: list[str] = []
        
        # 1. 沙箱静态分析
        sandbox_result = self._sandbox.validate_code(code)
        
        if sandbox_result.status == SandboxStatus.INVALID_CODE:
            errors.append(ValidationError(
                field="syntax",
                message=sandbox_result.error or "语法错误",
                code="SYNTAX_ERROR",
            ))
            return ValidationResult.invalid(errors)
        
        if sandbox_result.status == SandboxStatus.DANGEROUS_CODE:
            for pattern, message, line in sandbox_result.detected_dangers:
                if line:
                    errors.append(ValidationError(
                        field=f"line_{line}",
                        message=f"{message} ({pattern})",
                        code=pattern,
                    ))
                else:
                    errors.append(ValidationError(
                        field="security",
                        message=f"{message} ({pattern})",
                        code=pattern,
                    ))
        
        # 2. 尝试AST解析验证
        try:
            import ast
            tree = ast.parse(code)
            
            # 检查必要的接口实现
            has_strategy_class = False
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    # 检查是否有Strategy相关
                    if 'Strategy' in node.name:
                        has_strategy_class = True
                        # 检查必要的方法
                        methods = [m.name for m in node.body if isinstance(m, ast.FunctionDef)]
                        if 'on_market_data' not in methods:
                            errors.append(ValidationError(
                                field=node.name,
                                message="缺少on_market_data方法",
                                code="MISSING_METHOD",
                            ))
            
            if not has_strategy_class:
                warnings.append("未找到策略类定义")
                
        except SyntaxError as e:
            errors.append(ValidationError(
                field="syntax",
                message=f"语法错误: {e}",
                code="SYNTAX_ERROR",
            ))
        
        if errors:
            return ValidationResult.invalid(errors)
        
        if warnings:
            return ValidationResult.with_warnings(warnings)
        
        return ValidationResult.valid({
            'sandbox_status': sandbox_result.status.value,
        })
    
    async def register_strategy(
        self,
        strategy: StrategyPlugin,
        code: str,
        prompt: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> RegistrationResult:
        """
        注册策略
        
        Args:
            strategy: 策略实例
            code: 策略代码
            prompt: 生成提示词
            metadata: 扩展元数据
            
        Returns:
            RegistrationResult: 注册结果
        """
        # 1. 验证策略
        validation_result = strategy.validate()
        if not validation_result.is_valid:
            return RegistrationResult(
                success=False,
                error=f"策略验证失败: {validation_result.errors}",
            )
        
        # 2. 如果有审计日志，记录生成
        if self._audit_log:
            entry = await self._audit_log.log_generation(
                prompt=prompt,
                generated_code=code,
                llm_backend=self._llm_backend.value,
                llm_model=self._llm_model,
                strategy_name=strategy.name,
                metadata=metadata or {},
            )
            
            # 提交审批
            await self._audit_log.submit_for_approval(entry.entry_id)
            
            return RegistrationResult(
                success=True,
                strategy_id=entry.strategy_id,
                entry_id=entry.entry_id,
            )
        
        # 3. 生成策略ID
        strategy_id = f"{strategy.name}_{strategy.version}"
        
        return RegistrationResult(
            success=True,
            strategy_id=strategy_id,
        )
    
    async def submit_for_approval(self, strategy_id: str) -> bool:
        """提交策略审批"""
        if not self._audit_log:
            return False
        
        # 查找策略
        for entry in await self._audit_log.get_strategy_versions(strategy_id):
            if entry.status == AuditStatus.DRAFT:
                await self._audit_log.submit_for_approval(entry.entry_id)
                return True
        
        return False
    
    async def approve_strategy(
        self,
        strategy_id: str,
        approver: str,
        comment: Optional[str] = None,
    ) -> bool:
        """批准策略"""
        if not self._audit_log:
            return False
        
        # 查找待审批的策略
        for entry in await self._audit_log.get_strategy_versions(strategy_id):
            if entry.status == AuditStatus.PENDING:
                await self._audit_log.approve(entry.entry_id, approver, comment)
                return True
        
        return False
    
    async def deploy_strategy(self, strategy_id: str) -> bool:
        """部署策略"""
        if not self._audit_log:
            return False
        
        # 查找已批准的策略
        for entry in await self._audit_log.get_strategy_versions(strategy_id):
            if entry.status == AuditStatus.APPROVED:
                await self._audit_log.deploy(entry.entry_id)
                return True
        
        return False
    
    def get_sandbox(self) -> CodeSandbox:
        """获取沙箱实例"""
        return self._sandbox
    
    def get_adapter(self) -> LLMAdapter:
        """获取LLM适配器"""
        return self._adapter
    
    async def validate_connection(self) -> bool:
        """验证LLM连接"""
        return await self._adapter.validate_connection()
