"""
Money - 金额值对象
=====================
金融系统中最基础的值对象，必须使用Decimal避免浮点精度问题。

为什么不用float？
- float在金融计算中会产生精度误差，如 0.1 + 0.2 = 0.30000000000000004
- Decimal可以精确表示十进制数
- 交易所API通常返回的是字符串，我们需要安全地转换

使用示例：
    >>> from trader.core.domain.models.money import Money
    >>> price = Money.from_float(100.50, "USDT")
    >>> quantity = Money(Decimal("0.5"), "BTC")
    >>> total = price * quantity  # 自动计算
"""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Union


@dataclass(frozen=True)
class Money:
    """
    金额值对象（不可变）

    使用frozen=True确保实例不可变，这对于哈希和并发安全很重要。
    例如：可以作为dict的key使用
    """
    amount: Decimal
    currency: str = "USDT"

    def __post_init__(self):
        """确保amount是Decimal类型"""
        if isinstance(self.amount, (int, float, str)):
            object.__setattr__(self, 'amount', Decimal(str(self.amount)))

    # ==================== 工厂方法 ====================

    @classmethod
    def from_float(cls, amount: float, currency: str = "USDT") -> "Money":
        """从float创建（主要用于兼容旧代码）"""
        return cls(amount=Decimal(str(amount)), currency=currency)

    @classmethod
    def from_int(cls, amount: int, currency: str = "USDT") -> "Money":
        """从int创建"""
        return cls(amount=Decimal(amount), currency=currency)

    @classmethod
    def zero(cls, currency: str = "USDT") -> "Money":
        """创建零金额"""
        return cls(amount=Decimal("0"), currency=currency)

    # ==================== 算术运算 ====================

    def __add__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"货币类型不一致: {self.currency} vs {other.currency}")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: "Money") -> "Money":
        if self.currency != other.currency:
            raise ValueError(f"货币类型不一致: {self.currency} vs {other.currency}")
        return Money(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, multiplier: Union[Decimal, int, float]) -> "Money":
        if isinstance(multiplier, (int, float)):
            multiplier = Decimal(str(multiplier))
        return Money(amount=self.amount * multiplier, currency=self.currency)

    def __truediv__(self, divisor: Union[Decimal, int, float]) -> "Money":
        if isinstance(divisor, (int, float)):
            divisor = Decimal(str(divisor))
        if divisor == 0:
            raise ValueError("不能除以零")
        return Money(amount=self.amount / divisor, currency=self.currency)

    def __neg__(self) -> "Money":
        return Money(amount=-self.amount, currency=self.currency)

    def __abs__(self) -> "Money":
        return Money(amount=abs(self.amount), currency=self.currency)

    # ==================== 比较运算 ====================

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Money):
            return False
        return self.amount == other.amount and self.currency == other.currency

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    def __lt__(self, other: "Money") -> bool:
        self._ensure_same_currency(other)
        return self.amount < other.amount

    def __le__(self, other: "Money") -> bool:
        self._ensure_same_currency(other)
        return self.amount <= other.amount

    def __gt__(self, other: "Money") -> bool:
        self._ensure_same_currency(other)
        return self.amount > other.amount

    def __ge__(self, other: "Money") -> bool:
        self._ensure_same_currency(other)
        return self.amount >= other.amount

    # ==================== 辅助方法 ====================

    def _ensure_same_currency(self, other: "Money") -> None:
        if self.currency != other.currency:
            raise ValueError(f"货币类型不一致: {self.currency} vs {other.currency}")

    def is_zero(self) -> bool:
        return self.amount == 0

    def is_positive(self) -> bool:
        return self.amount > 0

    def is_negative(self) -> bool:
        return self.amount < 0

    def to_float(self) -> float:
        """转换为float（主要用于兼容性）"""
        return float(self.amount)

    def to_int(self, decimals: int = 8) -> int:
        """转换为整数（指定小数位数）"""
        multiplier = Decimal("10") ** decimals
        return int(self.amount * multiplier)

    def round_to(self, decimals: int, mode=ROUND_HALF_UP) -> "Money":
        """四舍五入到指定小数位"""
        quantized = self.amount.quantize(Decimal("10") ** -decimals, rounding=mode)
        return Money(amount=quantized, currency=self.currency)

    def __repr__(self) -> str:
        return f"Money({self.amount} {self.currency})"

    def __hash__(self) -> int:
        return hash((self.amount, self.currency))
