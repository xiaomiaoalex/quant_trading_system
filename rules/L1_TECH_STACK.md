# Python 工程约定

- CI 使用 Python 3.12.5；环境和检查命令以当前依赖与 CI 配置为准。
- 新增或修改的公开接口保持类型注解；沿用 `str | None`、适合时使用 `dataclass(slots=True)`。不要为风格改写无关代码。
- 异步业务使用 asyncio，避免阻塞网络调用；同订单并发使用既有 hashed lock / actor / queue 机制。
- 格式采用 Black line-length 100 与 isort black profile。局部工作只格式化改动文件；合并所需检查以 CI 为准。
- 外部事件按既有契约保留 `local_receive_ts_ms`、`exchange_event_ts_ms`、`seq/update_id`、`source`、`out_of_order`、`gap_detected`；关键状态转换保留 `trace_id`、`stream_key`、`schema_version`。诊断日志避免记录凭据。
- 单测使用 `trader/tests/fakes/` 的 clock/http/ws 替身；验证范围见 `../AGENTS.md`。

依赖缺失时在项目环境使用 `python -m pip install -r trader/requirements-ci.txt`。按变更运行类型检查与格式检查，不每次重新安装或全仓格式化。
