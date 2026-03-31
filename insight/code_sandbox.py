"""
CodeSandbox - 安全代码执行沙箱
================================

核心职责：
1. 安全的代码执行环境
2. 网络调用拦截（只允许必要的API调用）
3. 资源限制（CPU、内存、执行时间）
4. 导入限制（只允许标准库和许可的第三方库）

危险代码拦截机制：
1. 静态分析：AST扫描危险模式
2. 网络拦截：禁止socket/http请求
3. 文件系统拦截：禁止文件读写
4. 资源限制：CPU时间、内存限制

设计原则：
- Fail-Closed：任何未明确允许的都是禁止的
- 确定性：相同输入产生相同输出
"""

from __future__ import annotations

import ast
import io
import re
import sys
import time
import builtins
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Callable, Optional, Sequence

# Unix-specific modules - these don't exist on Windows
# Use explicit platform check with stub values for Windows
if sys.platform != "win32":
    import fcntl
    import select
    import pty
    import tty
    import termios
    import resource
    import signal
    UNIX_AVAILABLE = True
else:
    # Windows stub implementations - modules set to None, feature checks use UNIX_AVAILABLE
    fcntl = None  # type: ignore[assignment,misc]
    select = None  # type: ignore[assignment,misc]
    pty = None  # type: ignore[assignment,misc]
    tty = None  # type: ignore[assignment,misc]
    termios = None  # type: ignore[assignment,misc]
    resource = None  # type: ignore[assignment,misc]
    signal = None  # type: ignore[assignment,misc]
    UNIX_AVAILABLE = False

# 危险模式正则表达式
DANGEROUS_PATTERNS = [
    # 文件系统操作
    (r'\bopen\s*\(', 'FILE_OPEN', '禁止直接文件操作'),
    (r'\bexec\s*\(', 'EXEC', '禁止使用exec'),
    (r'\beval\s*\(', 'EVAL', '禁止使用eval'),
    (r'\bcompile\s*\(', 'COMPILE', '禁止动态编译'),
    (r'__import__', 'DYNAMIC_IMPORT', '禁止动态导入'),
    (r'\binput\s*\(', 'INPUT', '禁止用户输入'),
    (r'\bexecfile\s*\(', 'EXECFILE', '禁止execfile'),
    (r'\bopen\s*=', 'FILE_OVERWRITE', '禁止覆盖文件'),
    
    # 网络操作
    (r'\bsocket\s*\.', 'SOCKET_OPERATION', '禁止socket操作'),
    (r'\brequests\.', 'REQUESTS_LIBRARY', '禁止requests库'),
    (r'\bhttpx\.', 'HTTPX_LIBRARY', '禁止httpx库'),
    (r'\baiohttp', 'AIOHTTP_LIBRARY', '禁止aiohttp库'),
    (r'\burllib\.', 'URLLIB_LIBRARY', '禁止urllib库'),
    (r'\bhttp\.client', 'HTTP_CLIENT', '禁止http.client'),
    (r'\bwebsocket', 'WEBSOCKET', '禁止websocket'),
    
    # 系统操作
    (r'\bos\.system', 'OS_SYSTEM', '禁止系统调用'),
    (r'\bos\.popen', 'OS_POPEN', '禁止popen'),
    (r'\bsubprocess', 'SUBPROCESS', '禁止子进程'),
    (r'\bpty\.', 'PTY_OPERATION', '禁止pty操作'),
    (r'\bmultiprocessing', 'MULTIPROCESSING', '禁止多进程'),
    (r'\bthreading', 'THREADING', '禁止多线程'),
    (r'\basyncio\.create_subprocess', 'ASYNCIO_SUBPROCESS', '禁止异步子进程'),
    
    # 危险函数
    (r'\bgetattr\s*\(', 'GETATTR', '禁止getattr'),
    (r'\bsetattr\s*\(', 'SETATTR', '禁止setattr'),
    (r'\bdelattr\s*\(', 'DELATTR', '禁止delattr'),
    (r'\bhasattr\s*\(', 'HASATTR', '禁止hasattr'),
    (r'\bmemoryview', 'MEMORYVIEW', '禁止memoryview'),
    (r'\bslice\s*\(', 'SLICE_WITH_OBJECT', '禁止对象切片'),
    (r'\bvars\s*\(', 'VARS_SCOPE', '禁止vars'),
    (r'\bglobals\s*\(', 'GLOBALS_SCOPE', '禁止globals'),
    (r'\blocals\s*\(', 'LOCALS_SCOPE', '禁止locals'),
    
    # 序列化危险
    (r'\bpickle\.', 'PICKLE', '禁止pickle'),
    (r'\byaml\.load', 'YAML_LOAD', '禁止yaml.load'),
    (r'\bmarshal\.', 'MARSHAL', '禁止marshal'),
    
    # 代码生成
    (r'\btype\s*\(\s*str\s*\(', 'DYNAMIC_TYPE', '禁止动态类型创建'),
    (r'\bFunctionType', 'FUNCTION_TYPE', '禁止函数类型'),
    (r'\bCodeType', 'CODE_TYPE', '禁止代码类型'),
]

# 允许的模块白名单
ALLOWED_MODULES = {
    # 标准库
    'math', 'random', 'datetime', 'time', 'collections', 'functools',
    'operator', 'itertools', 're', 'struct', 'decimal', 'fractions',
    'statistics', 'copy', 'pprint', 'json', 'base64', 'hashlib',
    'hmac', 'secrets', 'bisect', 'array', 'weakref', 'types',
    'contextlib', 'typing', 'warnings', 'abc', 'atexit',
    
    # 量化相关许可模块（可选）
    # 'numpy', 'pandas', 'scipy' - 需要在config中启用
}

# 禁止的模块黑名单
FORBIDDEN_MODULES = {
    'os', 'sys', 'io', ' pathlib', 'tempfile', 'shutil', 'glob',
    'fnmatch', 'linecache', 'tokenize', 'keyword', 'ast', 'dis',
    'inspect', 'traceback', 'gc', 'tblib', 'platform', 'errno',
    'ctypes', 'signal', 'mmap', 'readline', 'tty', 'pty', 'templos',
}


class SandboxError(Exception):
    """沙箱异常基类"""
    pass


class DangerousCodeError(SandboxError):
    """危险代码异常"""
    def __init__(self, pattern: str, message: str, line: Optional[int] = None):
        self.pattern = pattern
        self.message = message
        self.line = line
        super().__init__(f"[{pattern}] 第{line or '?'}行: {message}")


class SandboxTimeoutError(SandboxError):
    """沙箱超时异常"""
    pass


class SandboxResourceError(SandboxError):
    """沙箱资源超限异常"""
    pass


class SandboxImportError(SandboxError):
    """沙箱导入异常"""
    pass


class SandboxStatus(Enum):
    """沙箱执行状态"""
    SUCCESS = "SUCCESS"
    DANGEROUS_CODE = "DANGEROUS_CODE"
    TIMEOUT = "TIMEOUT"
    RESOURCE_EXCEEDED = "RESOURCE_EXCEEDED"
    IMPORT_ERROR = "IMPORT_ERROR"
    RUNTIME_ERROR = "RUNTIME_ERROR"
    INVALID_CODE = "INVALID_CODE"


@dataclass(slots=True)
class SandboxConfig:
    """
    沙箱配置
    
    属性：
        timeout_seconds: 执行超时时间（秒）
        max_memory_mb: 最大内存限制（MB）
        max_cpu_percent: 最大CPU使用百分比
        allowed_modules: 允许的额外模块列表
        enable_network: 是否允许网络调用（默认False）
        enable_file_ops: 是否允许文件操作（默认False）
    """
    timeout_seconds: float = 5.0
    max_memory_mb: int = 256
    max_cpu_percent: int = 50
    allowed_modules: tuple[str, ...] = ()
    enable_network: bool = False
    enable_file_ops: bool = False
    
    def __post_init__(self):
        if isinstance(self.timeout_seconds, (int, float)) and self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if isinstance(self.max_memory_mb, int) and self.max_memory_mb <= 0:
            raise ValueError("max_memory_mb must be positive")


@dataclass(slots=True)
class SandboxResult:
    """
    沙箱执行结果
    
    属性：
        status: 执行状态
        output: 执行输出
        error: 错误信息
        execution_time: 执行时间（秒）
        resource_usage: 资源使用情况
        detected_dangers: 检测到的危险模式
    """
    status: SandboxStatus
    output: Any = None
    error: Optional[str] = None
    execution_time: float = 0.0
    resource_usage: dict[str, Any] = field(default_factory=dict)
    detected_dangers: list[tuple[str, str, Optional[int]]] = field(default_factory=list)
    
    @property
    def is_safe(self) -> bool:
        """是否安全执行"""
        return self.status == SandboxStatus.SUCCESS
    
    @property
    def has_danger(self) -> bool:
        """是否有危险代码"""
        return self.status == SandboxStatus.DANGEROUS_CODE or len(self.detected_dangers) > 0


class CodeSandbox:
    """
    代码沙箱
    
    核心功能：
    1. 静态代码分析（AST扫描）
    2. 危险模式检测
    3. 网络调用拦截
    4. 资源限制
    
    使用方式：
        sandbox = CodeSandbox(config=SandboxConfig())
        result = sandbox.execute(code, market_data)
    """
    
    def __init__(self, config: Optional[SandboxConfig] = None):
        self._config = config or SandboxConfig()
        self._allowed_modules = set(ALLOWED_MODULES) | set(self._config.allowed_modules)
    
    @property
    def config(self) -> SandboxConfig:
        """获取沙箱配置"""
        return self._config
    
    def validate_code(self, code: str) -> SandboxResult:
        """
        验证代码安全性（静态分析）
        
        Args:
            code: 待验证的代码
            
        Returns:
            SandboxResult: 验证结果
        """
        detected_dangers: list[tuple[str, str, Optional[int]]] = []
        
        # 1. 尝试解析AST
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return SandboxResult(
                status=SandboxStatus.INVALID_CODE,
                error=f"语法错误: {e}",
            )
        
        # 2. 第一遍扫描：收集危险函数别名（如 d = getattr; d(...)）
        dangerous_aliases: set = set()
        for node in ast.walk(tree):
            alias = self._track_dangerous_alias(node)
            if alias:
                dangerous_aliases.add(alias)
        
        # 将别名集合存储在实例属性中，供 _check_ast_node 使用
        self._dangerous_aliases = dangerous_aliases
        
        # 3. 第二遍扫描：检查危险节点
        for node in ast.walk(tree):
            danger = self._check_ast_node(node)
            if danger:
                detected_dangers.append(danger)
        
        # 4. 正则模式扫描（作为 AST 检查的补充）
        for pattern, pattern_name, message in DANGEROUS_PATTERNS:
            if re.search(pattern, code):
                # 尝试定位行号
                line_num = self._find_line_number(code, pattern)
                detected_dangers.append((pattern_name, message, line_num))
        
        # 5. 导入检查
        import_errors = self._check_imports(code)
        if import_errors:
            for error_msg in import_errors:
                detected_dangers.append(('IMPORT', error_msg, None))
        
        # 6. 网络检查
        if not self._config.enable_network:
            network_dangers = self._check_network_usage(code)
            if network_dangers:
                detected_dangers.extend(network_dangers)
        
        # 7. 文件操作检查
        if not self._config.enable_file_ops:
            file_dangers = self._check_file_operations(code)
            if file_dangers:
                detected_dangers.extend(file_dangers)
        
        if detected_dangers:
            return SandboxResult(
                status=SandboxStatus.DANGEROUS_CODE,
                detected_dangers=detected_dangers,
                error=f"检测到 {len(detected_dangers)} 个危险模式",
            )
        
        return SandboxResult(status=SandboxStatus.SUCCESS)
    
    def _track_dangerous_alias(self, node: ast.AST) -> Optional[str]:
        """
        跟踪危险函数的别名赋值。
        
        检测形如 `d = getattr` 或 `d = setattr` 的别名赋值，
        并将别名添加到危险别名集合中。
        
        Args:
            node: AST节点
            
        Returns:
            别名名称，如果不是危险函数赋值则返回 None
        """
        # 检测赋值语句：alias = dangerous_func
        if isinstance(node, ast.Assign):
            # 危险函数名称集合
            dangerous_funcs = {'getattr', 'setattr', 'delattr', 'hasattr', 
                              'eval', 'exec', 'compile', 'open',
                              '__import__', 'input'}
            
            # 检查赋值目标是否为简单名称（如 d = ...）
            if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
                alias_name = node.targets[0].id
                
                # 检查右侧是否为危险函数调用
                if isinstance(node.value, ast.Name):
                    # alias = getattr (直接引用)
                    if node.value.id in dangerous_funcs:
                        return alias_name
                elif isinstance(node.value, ast.Attribute):
                    # alias = os.getattr 等模块属性（暂不追踪模块属性）
                    pass
        
        return None
    
    def _check_ast_node(self, node: ast.AST) -> Optional[tuple[str, str, Optional[int]]]:
        """检查AST节点是否危险"""
        # 检查函数调用
        if isinstance(node, ast.Call):
            func_name = self._get_func_name(node.func)
            
            # 危险函数（直接调用）
            if func_name in ('getattr', 'setattr', 'delattr', 'hasattr'):
                return ('DYNAMIC_ATTR', f'禁止动态属性操作: {func_name}', node.lineno)
            
            # 检查通过别名调用危险函数（如 d = getattr; d(...)）
            if hasattr(self, '_dangerous_aliases') and func_name in self._dangerous_aliases:
                return ('DYNAMIC_ATTR_VIA_ALIAS', 
                        f'禁止通过别名调用动态属性操作: {func_name}', node.lineno)
            
            # eval/exec
            if func_name in ('eval', 'exec', 'compile'):
                return ('CODE_EXEC', f'禁止执行动态代码: {func_name}', node.lineno)
        
        # 检查导入
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            return self._check_import_node(node)
        
        return None
    
    def _check_import_node(self, node: ast.Import | ast.ImportFrom) -> Optional[tuple[str, str, int]]:
        """检查导入节点"""
        if isinstance(node, ast.Import):
            for alias in node.names:
                module_name = alias.name.split('.')[0]
                if not self._is_module_allowed(module_name):
                    return ('FORBIDDEN_MODULE', f'禁止导入模块: {module_name}', node.lineno)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                module_name = node.module.split('.')[0]
                if not self._is_module_allowed(module_name):
                    return ('FORBIDDEN_MODULE', f'禁止从 {node.module} 导入', node.lineno)
        return None
    
    def _is_module_allowed(self, module_name: str) -> bool:
        """检查模块是否允许"""
        if module_name in self._allowed_modules:
            return True
        if module_name in FORBIDDEN_MODULES:
            return False
        # 默认禁止未明确允许的模块
        return False
    
    def _get_func_name(self, node: ast.AST) -> str:
        """获取函数名"""
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return node.attr
        return ''
    
    def _check_imports(self, code: str) -> list[str]:
        """检查所有导入语句"""
        errors = []
        for line_num, line in enumerate(code.split('\n'), 1):
            line = line.strip()
            if line.startswith('import ') or line.startswith('from '):
                # 简单检查，未使用白名单的导入
                if 'numpy' in line or 'pandas' in line:
                    if 'numpy' not in self._allowed_modules and 'pandas' not in self._allowed_modules:
                        errors.append(f"第{line_num}行: 需要启用numpy/pandas支持")
        return errors
    
    def _check_network_usage(self, code: str) -> list[tuple[str, str, int | None]]:
        """检查网络使用"""
        dangers: list[tuple[str, str, int | None]] = []
        network_patterns = [
            (r'\.connect\s*\(', 'SOCKET_CONNECT'),
            (r'\.send\s*\(', 'NETWORK_SEND'),
            (r'\.recv\s*\(', 'NETWORK_RECV'),
            (r'http://', 'HTTP_URL'),
            (r'https://', 'HTTPS_URL'),
            (r'ws://', 'WEBSOCKET_URL'),
            (r'wss://', 'WEBSOCKET_SECURE_URL'),
        ]
        for pattern, name in network_patterns:
            matches = list(re.finditer(pattern, code))
            for match in matches:
                line_num = code[:match.start()].count('\n') + 1
                dangers.append((name, f'禁止网络操作: {pattern}', line_num))
        return dangers
    
    def _check_file_operations(self, code: str) -> list[tuple[str, str, int | None]]:
        """检查文件系统操作"""
        dangers: list[tuple[str, str, int | None]] = []
        file_patterns = [
            (r'\bopen\s*\(', 'FILE_OPEN'),
            (r'\bread\s*\(\s*\)', 'FILE_READ'),
            (r'\bwrite\s*\(', 'FILE_WRITE'),
            (r'\bwith\s+open', 'FILE_CONTEXT'),
        ]
        for pattern, name in file_patterns:
            matches = list(re.finditer(pattern, code))
            for match in matches:
                line_num = code[:match.start()].count('\n') + 1
                dangers.append((name, f'禁止文件操作: {pattern}', line_num))
        return dangers
    
    def _find_line_number(self, code: str, pattern: str) -> Optional[int]:
        """查找匹配行的行号"""
        try:
            match = re.search(pattern, code)
            if match:
                return code[:match.start()].count('\n') + 1
        except Exception:
            pass
        return None
    
    def execute(
        self,
        code: str,
        market_data: Optional[Any] = None,
        context: Optional[dict[str, Any]] = None,
    ) -> SandboxResult:
        """
        在沙箱中执行代码
        
        Args:
            code: 待执行的代码
            market_data: 市场数据（可选）
            context: 执行上下文（可选）
            
        Returns:
            SandboxResult: 执行结果
        """
        # 1. 先进行静态验证
        validation_result = self.validate_code(code)
        if validation_result.status == SandboxStatus.DANGEROUS_CODE:
            return validation_result
        
        if validation_result.status == SandboxStatus.INVALID_CODE:
            return validation_result
        
        # 2. 执行代码
        start_time = time.time()
        result = SandboxResult(status=SandboxStatus.SUCCESS)
        
        # 设置资源限制（Unix only）
        if UNIX_AVAILABLE:
            try:
                # 内存限制
                max_memory_bytes = self._config.max_memory_mb * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (max_memory_bytes, max_memory_bytes))  # type: ignore[attr-defined]
                
                # CPU时间限制
                resource.setrlimit(resource.RLIMIT_CPU, (int(self._config.timeout_seconds), int(self._config.timeout_seconds) + 10))  # type: ignore[attr-defined]
            except (ValueError, OSError):
                pass  # 可能某些平台不支持
        
        # 创建受限执行环境
        # 注意：移除了 getattr/setattr/hasattr/delattr 等危险函数
        restricted_builtins = {k: v for k, v in vars(builtins).items() if k in [
            'abs', 'all', 'any', 'ascii', 'bin', 'bool', 'chr', 'dict', 
            'dir', 'divmod', 'enumerate', 'filter', 'float', 'format', 
            'frozenset', 'hash', 'hex', 'int', 'isinstance', 'issubclass',
            'iter', 'len', 'list', 'map', 'max', 'min', 'next', 'oct',
            'ord', 'pow', 'range', 'repr', 'reversed', 'round', 'set',
            'slice', 'sorted', 'str', 'sum', 'tuple', 'zip',
            '__import__',
        ]}
        
        # 注入交易相关的上下文
        sandbox_context = context or {}
        sandbox_context['market_data'] = market_data
        sandbox_context['Decimal'] = Decimal
        
        # 执行代码
        try:
            # 捕获超时信号（Unix only）
            if UNIX_AVAILABLE:
                def timeout_handler(signum, frame):
                    raise TimeoutError("Execution timeout")
                
                old_handler = signal.signal(signal.SIGALRM, timeout_handler)  # type: ignore[attr-defined]
                signal.alarm(int(self._config.timeout_seconds))  # type: ignore[attr-defined]
            
            try:
                # 创建执行环境
                exec_globals = {
                    '__builtins__': restricted_builtins,
                    '__name__': '__sandbox__',
                    **sandbox_context,
                }
                
                # 执行代码
                exec(code, exec_globals)
                
                # 如果代码中定义了strategy对象，获取它
                if 'strategy' in exec_globals:
                    result.output = exec_globals['strategy']
                else:
                    result.output = exec_globals.get('result')
                
            finally:
                if UNIX_AVAILABLE:
                    signal.alarm(0)  # type: ignore[attr-defined]
                    signal.signal(signal.SIGALRM, old_handler)  # type: ignore[attr-defined]
                    
        except TimeoutError:
            result.status = SandboxStatus.TIMEOUT
            result.error = f"执行超时（{self._config.timeout_seconds}秒）"
        except MemoryError:
            result.status = SandboxStatus.RESOURCE_EXCEEDED
            result.error = f"内存超限（{self._config.max_memory_mb}MB）"
        except Exception as e:
            result.status = SandboxStatus.RUNTIME_ERROR
            result.error = f"执行错误: {type(e).__name__}: {str(e)}"
        finally:
            result.execution_time = time.time() - start_time
        
        # 获取资源使用情况（Unix only）
        if UNIX_AVAILABLE:
            try:
                usage = resource.getrusage(resource.RUSAGE_SELF)  # type: ignore[attr-defined]
                result.resource_usage = {
                    'memory_max_rss_mb': usage.ru_maxrss / 1024,
                    'user_time': usage.ru_utime,
                    'system_time': usage.ru_stime,
                }
            except Exception:
                pass
        
        return result
    
    def get_allowed_modules(self) -> set[str]:
        """获取允许的模块列表"""
        return self._allowed_modules.copy()
    
    def add_allowed_module(self, module_name: str) -> None:
        """添加允许的模块"""
        self._allowed_modules.add(module_name)
