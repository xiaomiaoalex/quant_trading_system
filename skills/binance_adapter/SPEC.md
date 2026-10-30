# Binance 适配开发参考

用于连接器、Public/Private WebSocket、REST Alignment、限流退避与 DegradedCascade。先核对当前实现、测试和 `docs/INTERFACE_CONTRACTS.md`，外部协议变化查询交易所官方文档。

Public/Private Stream 状态隔离；重连完成 REST Alignment 后才恢复业务。按现有协议检测缺口、重复、乱序与静默断流，保留令牌桶及全抖动退避，正确处理限流和错误码。

无法确认一致性时降级并经 Control Plane 触发 KillSwitch；不要在每条消息回调中临时重连后无条件继续处理。验证本次修改涉及的断线、恢复、退避、重复事件和降级级联路径，单测使用 HTTP/WS 替身。
