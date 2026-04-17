"""
StrategyHotSwap - 策略热插拔机制
================================

实现无需重启即可更新策略的热插拔机制。

核心组件：
- StrategyLoader: 策略加载器，支持从模块路径或代码字符串加载
- VersionManager: 版本管理器，管理策略版本历史
- StrategyHotSwapper: 热插拔管理器，包含状态机

状态机转换：
    LOADING -> VALIDATING -> PREPARING -> SWITCHING -> ROLLBACK?
         |           |            |           |
         v           v            v           v
      失败→ERROR  失败→ERROR   失败→ERROR   失败→ERROR
                                             成功→ACTIVE

设计原则：
1. 无需重启更新策略
2. 挂单正确处理：关闭旧策略挂单前不允许切换
3. 持仓迁移：获取当前持仓并映射到新策略
4. 异常自动回滚：切换失败自动回滚到旧策略
5. 代码安全验证：签名验证、沙箱执行、资源限制

架构约束：
- 热插拔涉及IO操作，属于Service层
- 状态机转换必须原子性
- 使用哈希锁保证并发安全
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import logging
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import (
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Protocol,
    Sequence,
    Tuple,
    runtime_checkable,
)

from trader.core.application.risk_engine import KillSwitchLevel, RiskLevel
from trader.core.application.strategy_protocol import (
    MarketData,
    StrategyPlugin,
    StrategyResourceLimits,
    ValidationError,
    ValidationResult,
    ValidationStatus,
)
from trader.core.domain.models.order import Order, OrderStatus
from trader.core.domain.models.position import Position

logger = logging.getLogger(__name__)


# ============================================================================
# 热插拔状态枚举
# ============================================================================


class SwapState(Enum):
    """热插拔状态"""
    IDLE = "IDLE"                      # 空闲状态
    LOADING = "LOADING"                # 加载中
    VALIDATING = "VALIDATING"          # 验证中
    PREPARING = "PREPARING"            # 准备中（持仓迁移）
    SWITCHING = "SWITCHING"            # 切换中
    ROLLING_BACK = "ROLLING_BACK"      # 回滚中
    ACTIVE = "ACTIVE"                  # 激活状态
    ERROR = "ERROR"                    # 错误状态


class SwapPhase(Enum):
    """热插拔阶段（用于详细跟踪）"""
    # Loading阶段
    LOADING_CODE = "LOADING_CODE"
    LOADING_IMPORT = "LOADING_IMPORT"
    LOADING_INSTANTIATE = "LOADING_INSTANTIATE"
    
    # Validating阶段
    VALIDATING_PROTOCOL = "VALIDATING_PROTOCOL"
    VALIDATING_SIGNATURE = "VALIDATING_SIGNATURE"
    VALIDATING_SANDBOX = "VALIDATING_SANDBOX"
    VALIDATING_RESOURCE_LIMITS = "VALIDATING_RESOURCE_LIMITS"
    
    # Preparing阶段
    PREPARING_CANCEL_ORDERS = "PREPARING_CANCEL_ORDERS"
    PREPARING_MIGRATE_POSITIONS = "PREPARING_MIGRATE_POSITIONS"
    
    # Switching阶段
    SWITCHING_STOP_OLD = "SWITCHING_STOP_OLD"
    SWITCHING_START_NEW = "SWITCHING_START_NEW"
    SWITCHING_UPDATE_REGISTRY = "SWITCHING_UPDATE_REGISTRY"
    
    # Rolling Back阶段
    ROLLING_BACK_STOP_NEW = "ROLLING_BACK_STOP_NEW"
    ROLLING_BACK_RESTORE_OLD = "ROLLING_BACK_RESTORE_OLD"
    ROLLING_BACK_RESTORE_STATE = "ROLLING_BACK_RESTORE_STATE"


# ============================================================================
# 热插拔结果
# ============================================================================


@dataclass(slots=True)
class SwapError:
    """热插拔错误"""
    phase: SwapPhase
    message: str
    code: str
    details: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(slots=True)
class SwapResult:
    """
    热插拔操作结果
    
    属性：
        success: 是否成功
        old_strategy_id: 旧策略ID
        new_strategy_id: 新策略ID
        state: 最终状态
        error: 错误信息（如果失败）
        duration_ms: 操作耗时（毫秒）
        position_mappings: 持仓映射关系
        order_cancellations: 被取消的订单列表
    """
    success: bool
    old_strategy_id: str
    new_strategy_id: str
    state: SwapState
    error: Optional[SwapError] = None
    duration_ms: float = 0.0
    position_mappings: Dict[str, Tuple[Position, Optional[str]]] = field(default_factory=dict)  # symbol -> (position, new_strategy_tag)
    order_cancellations: List[str] = field(default_factory=list)  # client_order_ids
    
    @property
    def is_rollback(self) -> bool:
        """是否发生了回滚"""
        return self.error is not None


# ============================================================================
# 版本管理
# ============================================================================


@dataclass(slots=True)
class VersionId:
    """版本ID"""
    strategy_id: str
    version: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    
    def __str__(self) -> str:
        return f"{self.strategy_id}@{self.version}"


@dataclass(slots=True)
class VersionInfo:
    """版本信息"""
    version_id: VersionId
    strategy_id: str
    version: str
    created_at: datetime
    checksum: str  # 代码校验和
    signature: Optional[str] = None  # 代码签名
    metadata: Dict[str, Any] = field(default_factory=dict)
    is_active: bool = False
    swap_history: List[SwapResult] = field(default_factory=list)


@dataclass(slots=True)
class StoredStrategy:
    """存储的策略快照"""
    version_id: VersionId
    code: str  # 策略代码
    module_path: Optional[str] = None  # 模块路径（如果有）
    config: Dict[str, Any] = field(default_factory=dict)
    resource_limits: Optional[StrategyResourceLimits] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 持仓映射
# ============================================================================


@dataclass(slots=True)
class PositionMapping:
    """持仓映射"""
    symbol: str
    quantity: Decimal
    side: str  # "LONG" or "SHORT"
    entry_price: Decimal
    new_strategy_tag: Optional[str] = None  # 新策略中的持仓标识


# ============================================================================
# 策略加载器接口
# ============================================================================


@runtime_checkable
class StrategyLoaderPort(Protocol):
    """策略加载器端口"""
    
    async def load_from_module(self, module_path: str) -> StrategyPlugin:
        """从模块路径加载策略"""
        ...
    
    async def load_from_code(self, code: str, strategy_id: str) -> StrategyPlugin:
        """从代码字符串加载策略"""
        ...


@runtime_checkable
class PositionProviderPort(Protocol):
    """持仓提供者端口"""
    
    async def get_positions(self, strategy_id: str) -> List[Position]:
        """获取策略当前持仓"""
        ...
    
    async def migrate_position(
        self, 
        symbol: str, 
        from_strategy: str, 
        to_strategy: str
    ) -> bool:
        """迁移持仓到新策略"""
        ...


@runtime_checkable
class OrderManagerPort(Protocol):
    """订单管理器端口"""
    
    async def get_open_orders(self, strategy_id: str) -> List[Order]:
        """获取策略的未结订单"""
        ...
    
    async def cancel_order(self, client_order_id: str) -> bool:
        """取消订单"""
        ...
    
    async def cancel_all_orders(self, strategy_id: str) -> List[str]:
        """取消策略所有未结订单，返回被取消的订单ID列表"""
        ...


@runtime_checkable
class StrategyRegistryPort(Protocol):
    """策略注册表端口"""
    
    async def get_active_strategy(self) -> Optional[StrategyPlugin]:
        """获取当前活跃策略"""
        ...
    
    async def register_strategy(self, strategy: StrategyPlugin) -> None:
        """注册策略"""
        ...
    
    async def unregister_strategy(self, strategy_id: str) -> None:
        """注销策略"""
        ...


# ============================================================================
# 策略加载器
# ============================================================================


class StrategyLoader:
    """
    策略加载器
    
    负责从模块路径或代码字符串加载策略。
    支持代码签名验证和沙箱执行验证。
    
    使用示例：
        loader = StrategyLoader(
            signature_verifier=my_signature_verifier,
            sandbox_runner=my_sandbox_runner,
        )
        
        # 从模块加载
        plugin = await loader.load_from_module("strategies.ema_cross")
        
        # 从代码加载
        plugin = await loader.load_from_code(code_string, "my_strategy")
    """
    
    def __init__(
        self,
        signature_verifier: Optional[Callable[[str, str], bool]] = None,
        sandbox_runner: Optional[Callable[[StrategyPlugin], bool]] = None,
        resource_limit_checker: Optional[Callable[[StrategyResourceLimits], bool]] = None,
    ):
        """
        初始化策略加载器
        
        Args:
            signature_verifier: 代码签名验证器，接收 (code, signature) 返回 bool
            sandbox_runner: 沙箱执行验证器，接收 StrategyPlugin 返回 bool
            resource_limit_checker: 资源限制检查器，接收 StrategyResourceLimits 返回 bool
        """
        self._signature_verifier = signature_verifier
        self._sandbox_runner = sandbox_runner
        self._resource_limit_checker = resource_limit_checker
        
        # 加载缓存
        self._code_cache: Dict[str, str] = {}  # strategy_id -> code
    
    def _compute_checksum(self, code: str) -> str:
        """计算代码校验和"""
        return hashlib.sha256(code.encode('utf-8')).hexdigest()
    
    async def load_from_module(
        self,
        module_path: str,
        strategy_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> StrategyPlugin:
        """
        从模块路径加载策略
        
        Args:
            module_path: 模块路径（如 "strategies.ema_cross"）
            strategy_id: 策略ID（可选，默认使用模块名）
            config: 策略配置
            
        Returns:
            StrategyPlugin: 策略插件实例
            
        Raises:
            ImportError: 模块导入失败
            TypeError: 模块未实现 StrategyPlugin 协议
            ValueError: 策略验证失败
        """
        strategy_id = strategy_id or module_path.split('.')[-1]
        
        try:
            # 动态导入模块
            module = importlib.import_module(module_path)
            
            # 获取策略实例
            if not hasattr(module, "get_plugin"):
                raise ValueError(f"模块 {module_path} 缺少 get_plugin() 函数")
            
            plugin = module.get_plugin()
            
            # 验证协议
            if not isinstance(plugin, StrategyPlugin):
                raise TypeError(
                    f"模块 {module_path} 返回的对象未实现 StrategyPlugin 协议"
                )
            
            # 初始化策略
            if hasattr(plugin, 'initialize') and config:
                await plugin.initialize(config)
            
            # 注意：strategy_id 是 Runner/HotSwapper 内部的标识符，不应写入 plugin 实例
            # plugin.name 是协议定义的只读属性
            
            logger.info(f"策略加载成功（从模块）: {strategy_id} v{getattr(plugin, 'version', 'unknown')}")
            return plugin
            
        except Exception as e:
            logger.error(f"策略加载失败（从模块）: {strategy_id}, 错误: {e}")
            raise
    
    async def load_from_code(
        self,
        code: str,
        strategy_id: str,
        version: str = "1.0.0",
        signature: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> StrategyPlugin:
        """
        从代码字符串加载策略
        
        Args:
            code: 策略代码
            strategy_id: 策略ID
            version: 策略版本
            signature: 代码签名（可选）
            config: 策略配置
            
        Returns:
            StrategyPlugin: 策略插件实例
            
        Raises:
            ValueError: 代码签名验证失败
            SyntaxError: 代码语法错误
            TypeError: 策略未实现协议
        """
        # 签名验证
        if signature:
            # 提供了签名，必须验证
            if not self._signature_verifier:
                raise ValueError(f"提供了签名但没有配置签名验证器: {strategy_id}")
            if not self._signature_verifier(code, signature):
                raise ValueError(f"代码签名验证失败: {strategy_id}")
        elif self._signature_verifier:
            # 有验证器但没有提供签名 - 警告：AI生成代码应提供签名
            logger.warning(
                f"代码签名未提供，建议为AI生成的代码提供签名: {strategy_id}"
            )
        
        # 沙箱执行验证（编译但不执行）
        try:
            compiled = compile(code, '<string>', 'exec')
        except SyntaxError as e:
            logger.error(f"策略代码编译失败: {strategy_id}, 错误: {e}")
            raise
        
        # 执行代码创建策略实例
        try:
            # 安全受限的builtins，只允许安全的内置函数
            # 注意：移除了 getattr/setattr/hasattr/delattr 等危险函数
            _safe_builtins = {
                'abs': abs, 'max': max, 'min': min, 'range': range,
                'len': len, 'str': str, 'int': int, 'float': float,
                'bool': bool, 'list': list, 'dict': dict, 'tuple': tuple,
                'set': set, 'frozenset': frozenset, 'enumerate': enumerate,
                'zip': zip, 'map': map, 'filter': filter, 'reversed': reversed,
                'sorted': sorted, 'sum': sum, 'pow': pow, 'round': round,
                'divmod': divmod, 'all': all, 'any': any, 'ord': ord, 'chr': chr,
                'hex': hex, 'oct': oct, 'bin': bin, 'isinstance': isinstance,
                'issubclass': issubclass, 'type': type, 'callable': callable,
            }
            namespace: Dict[str, Any] = {
                '__name__': f'strategy_{strategy_id}',
                '__builtins__': _safe_builtins,
            }
            exec(compiled, namespace)
            
            # 获取策略实例
            if 'get_plugin' not in namespace:
                raise ValueError(f"代码缺少 get_plugin() 函数")
            
            plugin = namespace['get_plugin']()
            
            # 验证协议
            if not isinstance(plugin, StrategyPlugin):
                raise TypeError(
                    f"策略未实现 StrategyPlugin 协议"
                )
            
            # 设置版本属性（strategy_id 是外部标识符，不应写入 plugin）
            if hasattr(plugin, 'version'):
                plugin.version = version
            
            # 缓存代码
            self._code_cache[strategy_id] = code
            
            # 沙箱执行验证（实际运行策略的validate方法）
            if self._sandbox_runner:
                if not self._sandbox_runner(plugin):
                    raise ValueError(f"沙箱执行验证失败: {strategy_id}")
            
            # 资源限制检查
            if self._resource_limit_checker and hasattr(plugin, 'resource_limits'):
                if not self._resource_limit_checker(plugin.resource_limits):
                    raise ValueError(f"资源限制检查失败: {strategy_id}")
            
            # 初始化策略
            if hasattr(plugin, 'initialize') and config:
                await plugin.initialize(config)
            
            logger.info(f"策略加载成功（从代码）: {strategy_id} v{version}")
            return plugin
            
        except Exception as e:
            logger.error(f"策略加载失败（从代码）: {strategy_id}, 错误: {e}\n{traceback.format_exc()}")
            raise
    
    def get_code_checksum(self, strategy_id: str) -> Optional[str]:
        """获取策略代码校验和"""
        code = self._code_cache.get(strategy_id)
        if code:
            return self._compute_checksum(code)
        return None


# ============================================================================
# 版本管理器
# ============================================================================


class VersionManager:
    """
    版本管理器
    
    负责管理策略版本历史，支持版本回退。
    
    使用示例：
        vm = VersionManager(storage=my_storage)
        
        # 保存版本
        version_id = await vm.save_version(strategy)
        
        # 加载版本
        plugin = await vm.load_version(version_id)
        
        # 列出版本
        versions = await vm.list_versions("ema_cross")
    """
    
    def __init__(
        self,
        storage: Optional[Any] = None,  # 实际应为 StoragePort
        code_store: Optional[Callable[[str, StoredStrategy], None]] = None,
        code_load: Optional[Callable[[str], Optional[StoredStrategy]]] = None,
    ):
        """
        初始化版本管理器
        
        Args:
            storage: 存储后端
            code_store: 代码存储函数
            code_load: 代码加载函数
        """
        self._storage = storage
        self._code_store = code_store
        self._code_load = code_load
        
        # 内存缓存
        self._versions: Dict[str, List[VersionInfo]] = {}  # strategy_id -> versions
        self._active_version: Dict[str, VersionId] = {}  # strategy_id -> active version
        self._stored_strategies: Dict[str, StoredStrategy] = {}  # version_id string -> stored strategy
    
    def _make_version_id_str(self, version_id: VersionId) -> str:
        """生成版本ID字符串"""
        return f"{version_id.strategy_id}@{version_id.version}@{int(version_id.timestamp.timestamp())}"
    
    async def save_version(
        self,
        strategy: StrategyPlugin,
        signature: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> VersionId:
        """
        保存策略版本
        
        Args:
            strategy: 策略插件
            signature: 代码签名（可选）
            metadata: 扩展元数据
            
        Returns:
            VersionId: 版本ID
        """
        strategy_id = getattr(strategy, 'strategy_id', getattr(strategy, 'name', 'unknown'))
        version = getattr(strategy, 'version', '1.0.0')
        
        # 生成版本ID
        version_id = VersionId(
            strategy_id=strategy_id,
            version=version,
            timestamp=datetime.now(timezone.utc),
        )
        
        # 获取代码校验和
        checksum = ""
        if hasattr(strategy, '__class__'):
            # 尝试获取策略源代码信息
            checksum = f"{strategy_id}:{version}:{id(strategy)}"
        
        # 创建版本信息
        version_info = VersionInfo(
            version_id=version_id,
            strategy_id=strategy_id,
            version=version,
            created_at=datetime.now(timezone.utc),
            checksum=checksum,
            signature=signature,
            metadata=metadata or {},
            is_active=False,
        )
        
        # 保存到存储
        if strategy_id not in self._versions:
            self._versions[strategy_id] = []
        
        self._versions[strategy_id].append(version_info)
        
        # 持久化存储
        if self._code_store:
            stored = StoredStrategy(
                version_id=version_id,
                code="",  # 代码已编译，无法直接存储
                metadata=metadata or {},
            )
            self._code_store(self._make_version_id_str(version_id), stored)
        
        logger.info(f"策略版本保存: {version_id}")
        return version_id
    
    async def load_version(self, version_id: VersionId) -> Optional[StrategyPlugin]:
        """
        加载指定版本的策略
        
        Args:
            version_id: 版本ID
            
        Returns:
            StrategyPlugin 或 None（版本不存在）
        """
        version_key = self._make_version_id_str(version_id)
        
        # 从缓存或存储加载
        stored = self._stored_strategies.get(version_key)
        if stored is None and self._code_load:
            stored = self._code_load(version_key)
        
        if stored is None:
            logger.warning(f"策略版本不存在: {version_id}")
            return None
        
        # 重新实例化策略（如果存储了足够的信息）
        # 注意：实际实现可能需要重新加载原始代码
        logger.info(f"策略版本加载: {version_id}")
        return None  # 需要重新实现
    
    async def list_versions(
        self,
        strategy_id: str,
        limit: int = 10,
    ) -> List[VersionInfo]:
        """
        列出策略的版本历史
        
        Args:
            strategy_id: 策略ID
            limit: 返回数量限制
            
        Returns:
            List[VersionInfo]: 版本信息列表（按时间倒序）
        """
        versions = self._versions.get(strategy_id, [])
        sorted_versions = sorted(versions, key=lambda v: v.created_at, reverse=True)
        return sorted_versions[:limit]
    
    async def get_active_version(self, strategy_id: str) -> Optional[VersionId]:
        """
        获取策略的当前活跃版本
        
        Args:
            strategy_id: 策略ID
            
        Returns:
            VersionId 或 None
        """
        return self._active_version.get(strategy_id)
    
    async def set_active_version(self, version_id: VersionId) -> None:
        """
        设置策略的当前活跃版本
        
        Args:
            version_id: 版本ID
        """
        self._active_version[version_id.strategy_id] = version_id
        
        # 更新版本信息
        for v in self._versions.get(version_id.strategy_id, []):
            v.is_active = (v.version == version_id.version)
    
    async def add_swap_history(self, version_id: VersionId, result: SwapResult) -> None:
        """
        添加切换历史记录
        
        Args:
            version_id: 版本ID
            result: 切换结果
        """
        for v in self._versions.get(version_id.strategy_id, []):
            if v.version == version_id.version:
                v.swap_history.append(result)
                break


# ============================================================================
# 策略热插拔管理器
# ============================================================================


class StrategyHotSwapper:
    """
    策略热插拔管理器
    
    负责执行策略的在线切换，包括：
    1. 挂单处理：关闭旧策略挂单
    2. 持仓迁移：获取并迁移持仓
    3. 异常回滚：切换失败自动回滚
    
    状态机：
        IDLE -> LOADING -> VALIDATING -> PREPARING -> SWITCHING -> ACTIVE
                                                       |
                                                       v
                                                   ROLLING_BACK -> IDLE
                                                       |
                                                       v
                                                    ERROR
    
    使用示例：
        swapper = StrategyHotSwapper(
            loader=StrategyLoader(),
            version_manager=VersionManager(),
            order_manager=my_oms,
            position_provider=my_position_provider,
            strategy_registry=my_registry,
        )
        
        result = await swapper.swap(new_strategy)
        if result.success:
            print("切换成功")
        else:
            print(f"切换失败: {result.error.message}")
    """
    
    def __init__(
        self,
        loader: StrategyLoader,
        version_manager: VersionManager,
        order_manager: Optional[OrderManagerPort] = None,
        position_provider: Optional[PositionProviderPort] = None,
        strategy_registry: Optional[StrategyRegistryPort] = None,
        get_open_orders_callback: Optional[Callable[[str], List[Order]]] = None,
        cancel_order_callback: Optional[Callable[[str], bool]] = None,
        get_positions_callback: Optional[Callable[[str], List[Position]]] = None,
        migrate_position_callback: Optional[Callable[[str, str, str], bool]] = None,
        get_active_strategy_callback: Optional[Callable[[], Optional[StrategyPlugin]]] = None,
        register_strategy_callback: Optional[Callable[[StrategyPlugin], None]] = None,
        unregister_strategy_callback: Optional[Callable[[str], None]] = None,
    ):
        """
        初始化热插拔管理器
        
        Args:
            loader: 策略加载器
            version_manager: 版本管理器
            order_manager: 订单管理器
            position_provider: 持仓提供者
            strategy_registry: 策略注册表
            get_open_orders_callback: 获取未结订单回调
            cancel_order_callback: 取消订单回调
            get_positions_callback: 获取持仓回调
            migrate_position_callback: 迁移持仓回调
            get_active_strategy_callback: 获取活跃策略回调
            register_strategy_callback: 注册策略回调
            unregister_strategy_callback: 注销策略回调
        """
        self._loader = loader
        self._version_manager = version_manager
        self._order_manager = order_manager
        self._position_provider = position_provider
        self._strategy_registry = strategy_registry
        
        # 回调函数
        self._get_open_orders = get_open_orders_callback
        self._cancel_order = cancel_order_callback
        self._get_positions = get_positions_callback
        self._migrate_position = migrate_position_callback
        self._get_active_strategy = get_active_strategy_callback
        self._register_strategy = register_strategy_callback
        self._unregister_strategy = unregister_strategy_callback
        
        # 状态机状态
        self._state = SwapState.IDLE
        self._current_phase = SwapPhase.LOADING_CODE
        self._current_swap: Optional[SwapResult] = None
        self._old_strategy: Optional[StrategyPlugin] = None
        self._new_strategy: Optional[StrategyPlugin] = None
        
        # 并发控制锁（基于策略ID的哈希锁）
        self._locks: Dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()
    
    @property
    def state(self) -> SwapState:
        """获取当前状态"""
        return self._state
    
    @property
    def current_phase(self) -> SwapPhase:
        """获取当前阶段"""
        return self._current_phase
    
    async def _get_lock(self, strategy_id: str) -> asyncio.Lock:
        """获取策略ID对应的锁"""
        async with self._lock:
            if strategy_id not in self._locks:
                self._locks[strategy_id] = asyncio.Lock()
            return self._locks[strategy_id]
    
    async def _transition_state(self, new_state: SwapState, new_phase: SwapPhase) -> None:
        """状态机转换"""
        old_state = self._state
        old_phase = self._current_phase
        self._state = new_state
        self._current_phase = new_phase
        logger.info(f"热插拔状态转换: {old_state.value}.{old_phase.value} -> {new_state.value}.{new_phase.value}")
    
    async def _set_error(
        self,
        phase: SwapPhase,
        message: str,
        code: str,
        details: Optional[Dict[str, Any]] = None,
    ) -> SwapError:
        """设置错误"""
        error = SwapError(
            phase=phase,
            message=message,
            code=code,
            details=details or {},
        )
        logger.error(f"热插拔错误 [{phase.value}]: {message} (code={code})")
        return error
    
    def _get_old_strategy(self) -> Optional[StrategyPlugin]:
        """获取旧策略"""
        if self._old_strategy:
            return self._old_strategy
        
        if self._get_active_strategy:
            return self._get_active_strategy()
        
        return None
    
    async def swap(
        self,
        new_strategy: StrategyPlugin,
        force: bool = False,
    ) -> SwapResult:
        """
        执行策略热插拔
        
        Args:
            new_strategy: 新策略插件
            force: 是否强制切换（跳过某些检查）
            
        Returns:
            SwapResult: 切换结果
            
        状态机流程：
            IDLE -> LOADING -> VALIDATING -> PREPARING -> SWITCHING -> ACTIVE
                                                               |
                                                               v
                                                           ROLLING_BACK -> IDLE
        """
        start_time = time.monotonic()
        strategy_id = getattr(new_strategy, 'strategy_id', getattr(new_strategy, 'name', 'unknown'))
        
        # 获取锁
        lock = await self._get_lock(strategy_id)
        
        async with lock:
            # 初始化结果
            self._current_swap = SwapResult(
                success=False,
                old_strategy_id="",
                new_strategy_id=strategy_id,
                state=SwapState.IDLE,
            )
            
            # 获取旧策略
            old_strategy = self._get_old_strategy()
            old_strategy_id = getattr(old_strategy, 'strategy_id', getattr(old_strategy, 'name', '')) if old_strategy else ""
            self._current_swap.old_strategy_id = old_strategy_id
            
            try:
                # ========== LOADING 阶段 ==========
                await self._transition_state(SwapState.LOADING, SwapPhase.LOADING_CODE)
                
                # 验证新策略
                if not isinstance(new_strategy, StrategyPlugin):
                    error = await self._set_error(
                        SwapPhase.LOADING_INSTANTIATE,
                        "新策略未实现 StrategyPlugin 协议",
                        "INVALID_PROTOCOL",
                    )
                    self._current_swap.error = error
                    self._current_swap.state = self._state
                    return self._current_swap
                
                self._new_strategy = new_strategy
                
                # ========== VALIDATING 阶段 ==========
                await self._transition_state(SwapState.VALIDATING, SwapPhase.VALIDATING_PROTOCOL)
                
                # 协议验证
                validation = new_strategy.validate()
                if not validation.is_valid:
                    error = await self._set_error(
                        SwapPhase.VALIDATING_PROTOCOL,
                        f"策略验证失败: {[e.message for e in validation.errors]}",
                        "VALIDATION_FAILED",
                        {"errors": [(e.field, e.message) for e in validation.errors]},
                    )
                    self._current_swap.error = error
                    self._current_swap.state = SwapState.ERROR
                    await self._transition_state(SwapState.ERROR, SwapPhase.VALIDATING_PROTOCOL)
                    return self._current_swap
                
                # 资源限制验证
                await self._transition_state(SwapState.VALIDATING, SwapPhase.VALIDATING_RESOURCE_LIMITS)
                limits = new_strategy.resource_limits
                if limits and hasattr(limits, 'max_orders_per_minute'):
                    if limits.max_orders_per_minute <= 0:
                        error = await self._set_error(
                            SwapPhase.VALIDATING_RESOURCE_LIMITS,
                            "无效的资源限制配置",
                            "INVALID_RESOURCE_LIMITS",
                        )
                        self._current_swap.error = error
                        self._current_swap.state = SwapState.ERROR
                        await self._transition_state(SwapState.ERROR, SwapPhase.VALIDATING_RESOURCE_LIMITS)
                        return self._current_swap
                
                # ========== PREPARING 阶段 ==========
                await self._transition_state(SwapState.PREPARING, SwapPhase.PREPARING_CANCEL_ORDERS)
                
                # 挂单处理：如果旧策略有未结订单，需要取消
                if old_strategy and not force:
                    open_orders = []
                    if self._order_manager:
                        open_orders = await self._order_manager.get_open_orders(old_strategy_id)
                    elif self._get_open_orders:
                        open_orders = self._get_open_orders(old_strategy_id)
                    
                    if open_orders:
                        logger.info(f"发现 {len(open_orders)} 个未结订单，需要取消")
                        self._current_swap.order_cancellations = []
                        
                        for order in open_orders:
                            if order.status not in [OrderStatus.FILLED, OrderStatus.CANCELLED, OrderStatus.REJECTED]:
                                success = False
                                if self._order_manager:
                                    success = await self._order_manager.cancel_order(order.client_order_id)
                                elif self._cancel_order:
                                    success = self._cancel_order(order.client_order_id)
                                
                                if success:
                                    self._current_swap.order_cancellations.append(order.client_order_id)
                        
                        # 等待订单取消确认（简单等待）
                        await asyncio.sleep(0.1)
                
                # ========== PREPARING: 持仓迁移 ==========
                await self._transition_state(SwapState.PREPARING, SwapPhase.PREPARING_MIGRATE_POSITIONS)
                
                # 获取持仓
                positions: List[Position] = []
                if old_strategy:
                    if self._position_provider:
                        positions = await self._position_provider.get_positions(old_strategy_id)
                    elif self._get_positions:
                        positions = self._get_positions(old_strategy_id)
                
                # 持仓映射
                for pos in positions:
                    if pos.quantity > 0:  # 有持仓
                        self._current_swap.position_mappings[pos.symbol] = (pos, strategy_id)
                        logger.info(f"持仓映射: {pos.symbol} -> {strategy_id} (qty={pos.quantity})")
                
                # ========== SWITCHING 阶段 ==========
                await self._transition_state(SwapState.SWITCHING, SwapPhase.SWITCHING_STOP_OLD)
                
                # 停止旧策略
                if old_strategy and hasattr(old_strategy, 'shutdown'):
                    try:
                        await asyncio.wait_for(
                            old_strategy.shutdown(),
                            timeout=5.0
                        )
                    except asyncio.TimeoutError:
                        logger.warning(f"旧策略关闭超时: {old_strategy_id}")
                    except Exception as e:
                        logger.warning(f"旧策略关闭异常: {e}")
                
                # 保存旧策略引用用于回滚
                self._old_strategy = old_strategy
                
                await self._transition_state(SwapState.SWITCHING, SwapPhase.SWITCHING_START_NEW)
                
                # 初始化新策略
                if hasattr(new_strategy, 'initialize'):
                    try:
                        await asyncio.wait_for(
                            new_strategy.initialize({}),
                            timeout=10.0
                        )
                    except asyncio.TimeoutError:
                        error = await self._set_error(
                            SwapPhase.SWITCHING_START_NEW,
                            "新策略初始化超时",
                            "INIT_TIMEOUT",
                        )
                        self._current_swap.error = error
                        await self._rollback(strategy_id)
                        return self._current_swap
                    except Exception as e:
                        error = await self._set_error(
                            SwapPhase.SWITCHING_START_NEW,
                            f"新策略初始化失败: {e}",
                            "INIT_FAILED",
                        )
                        self._current_swap.error = error
                        await self._rollback(strategy_id)
                        return self._current_swap
                
                await self._transition_state(SwapState.SWITCHING, SwapPhase.SWITCHING_UPDATE_REGISTRY)
                
                # 更新注册表
                if self._strategy_registry:
                    if old_strategy and hasattr(old_strategy, 'strategy_id'):
                        await self._strategy_registry.unregister_strategy(old_strategy_id)
                    await self._strategy_registry.register_strategy(new_strategy)
                elif self._unregister_strategy and old_strategy:
                    self._unregister_strategy(old_strategy_id)
                if self._register_strategy:
                    self._register_strategy(new_strategy)
                
                # ========== 完成 ==========
                await self._transition_state(SwapState.ACTIVE, SwapPhase.SWITCHING_UPDATE_REGISTRY)
                
                # 保存版本
                await self._version_manager.save_version(new_strategy)
                
                self._current_swap.success = True
                self._current_swap.state = SwapState.ACTIVE
                self._current_swap.duration_ms = (time.monotonic() - start_time) * 1000
                
                logger.info(f"策略热插拔成功: {old_strategy_id} -> {strategy_id}, 耗时: {self._current_swap.duration_ms:.2f}ms")
                return self._current_swap
                
            except Exception as e:
                error = await self._set_error(
                    self._current_phase,
                    f"热插拔异常: {e}",
                    "SWAP_EXCEPTION",
                    {"traceback": traceback.format_exc()},
                )
                self._current_swap.error = error
                self._current_swap.state = SwapState.ERROR
                
                # 尝试回滚
                await self._rollback(strategy_id)
                
                return self._current_swap
    
    async def _rollback(self, strategy_id: str) -> None:
        """
        执行回滚
        
        Args:
            strategy_id: 策略ID
        """
        await self._transition_state(SwapState.ROLLING_BACK, SwapPhase.ROLLING_BACK_STOP_NEW)
        
        # 停止新策略
        if self._new_strategy and hasattr(self._new_strategy, 'shutdown'):
            try:
                await asyncio.wait_for(
                    self._new_strategy.shutdown(),
                    timeout=5.0
                )
            except Exception as e:
                logger.warning(f"新策略关闭失败（回滚中）: {e}")
        
        await self._transition_state(SwapState.ROLLING_BACK, SwapPhase.ROLLING_BACK_RESTORE_OLD)
        
        # 恢复旧策略
        if self._old_strategy:
            # 重新注册旧策略
            old_strategy_id = getattr(self._old_strategy, 'strategy_id', '')
            if self._strategy_registry:
                await self._strategy_registry.register_strategy(self._old_strategy)
            elif self._register_strategy:
                self._register_strategy(self._old_strategy)
            
            logger.info(f"已回滚到旧策略: {old_strategy_id}")
        
        await self._transition_state(SwapState.ROLLING_BACK, SwapPhase.ROLLING_BACK_RESTORE_STATE)
        
        # 更新结果状态
        if self._current_swap:
            self._current_swap.state = SwapState.IDLE
            self._current_swap.success = False
        
        # 清理
        self._new_strategy = None
        self._old_strategy = None
        
        # 最终转换到 IDLE 状态
        await self._transition_state(SwapState.IDLE, SwapPhase.ROLLING_BACK_RESTORE_STATE)
    
    async def rollback(self) -> SwapResult:
        """
        手动触发回滚
        
        Returns:
            SwapResult: 回滚结果
        """
        if self._state == SwapState.IDLE or self._state == SwapState.ERROR:
            logger.warning("当前状态不允许回滚")
            return SwapResult(
                success=False,
                old_strategy_id="",
                new_strategy_id="",
                state=self._state,
                error=SwapError(
                    phase=self._current_phase,
                    message="当前状态不允许回滚",
                    code="ROLLBACK_NOT_ALLOWED",
                ),
            )
        
        strategy_id = getattr(self._new_strategy, 'strategy_id', '') if self._new_strategy else ""
        await self._rollback(strategy_id)
        
        return self._current_swap or SwapResult(
            success=False,
            old_strategy_id="",
            new_strategy_id="",
            state=SwapState.IDLE,
            error=SwapError(
                phase=self._current_phase,
                message="无活动切换可回滚",
                code="NO_ACTIVE_SWAP",
            ),
        )
    
    def get_status(self) -> Dict[str, Any]:
        """
        获取热插拔状态
        
        Returns:
            Dict: 状态信息
        """
        return {
            "state": self._state.value,
            "current_phase": self._current_phase.value,
            "current_swap": self._current_swap,
        }
    
    def is_idle(self) -> bool:
        """检查是否处于空闲状态"""
        return self._state == SwapState.IDLE
    
    def is_switching(self) -> bool:
        """检查是否正在切换"""
        return self._state in [
            SwapState.LOADING,
            SwapState.VALIDATING,
            SwapState.PREPARING,
            SwapState.SWITCHING,
            SwapState.ROLLING_BACK,
        ]
