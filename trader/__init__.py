# Trader - 生产级量化交易系统
#
# 架构：Clean Architecture + Hexagonal + DDD
# 核心原则：
#   1. Core层无外部依赖，所有IO通过Port
#   2. 统一使用Decimal处理金融精度
#   3. 统一使用epoch_ms作为时间标准
#   4. OMS是订单唯一入口

__version__ = "0.1.0"
