# OMS 开发参考

用于订单状态机、CAS、幂等和成交记账。先查 `docs/INTERFACE_CONTRACTS.md` 及当前 OMS 实现与测试，沿用实际接口。

订单状态通过统一 CAS 入口迁移，FILLED/CANCELLED/REJECTED/EXPIRED 等终态不得被后续状态回退；允许迁移以实现和契约的状态表为准。成交事件按 `(cl_ord_id, exec_id)` 去重，不能把 symbol 当订单身份。

WS 与 REST 并发和重放不得重复记账。更新经过事件流程和 append-only Event Log；按订单分区处理竞争。终态订单收到重复回报应维持幂等，晚到成交是否影响账务按契约单独判断。

修改时验证合法与非法迁移、重复成交、同 symbol 不同订单、乱序回报、CAS 竞争及回放一致性。避免照抄脱离实际接口的示例状态图。
