# quant_trading_system Crypto v3.4.0 - deployment runtime refactor bundle

这个 bundle 聚焦你指出的三个 P0 问题：

1. 策略插件不能再依赖模块级 `_plugin_instance` 单例。
2. 交易对/账户/模式必须在 **deployment** 阶段配置，而不是 `start` 时临时传一个 `symbol`。
3. 前端必须从“模板控制页”升级为“模板 + deployment 实例控制”。

## 这份 bundle 里包含什么

- `patches/010_strategy_runner.patch`
  - 将 `StrategyRunner` 收敛到 **deployment_id 作为运行时主键**。
  - 在加载期注入 `symbols/account_id/venue/mode`。
  - 对单例插件做显式拒绝：如果策略模块持续返回同一个对象，runner 直接报错，而不是默默共享状态。
- `patches/020_strategies_routes.patch`
  - 把 `/v1/strategies/{strategy_id}/load` 升级为“创建 deployment 运行实例”。
  - 新增 `/v1/deployments/{deployment_id}/start|stop|pause|resume|status|unload`。
  - `/v1/strategies/loaded` 返回 loaded deployments，而不是模板级单例状态。
- `patches/030_schemas.patch`
  - 补齐 deployment mode / runtime schema。
- `patches/040_strategy_lifecycle_manager.patch`
  - 让 lifecycle manager 明确区分 template 与 deployment。
- `patches/000_plugin_factory_migration_example.patch`
  - 给策略插件模块的迁移模板，把 `_plugin_instance` 单例改成 `create_plugin()` factory。
- `replacements/frontend/*`
  - 前端四个文件的完整替换版本：types / api client / hooks / Strategies 页面。

## 没有直接 patch 的文件

这次没有 repo 里以下文件的源码，所以 bundle 只能做兼容式设计或给示例：

- 具体每个策略模块（真正持有 `_plugin_instance` 的插件文件）
- `trader/services/strategy_runtime_orchestrator.py`
- `Frontend/src/hooks/index.ts` / barrel export 文件

其中最关键的是：

- **插件模块必须改成 factory**。否则即使 runner 支持多 deployment，策略模块仍然可能返回同一个 Python 对象。
- 如果 `StrategyRuntimeOrchestrator` 对外暴露/持久化的是 `strategy_id` 而不是 runtime key，仍然需要跟进同步改成 `deployment_id`，或者至少接收 deployment_id 作为外部 key。

## 应用顺序建议

1. 先应用 `000_plugin_factory_migration_example.patch` 到每个真实策略模块。
2. 再应用 backend patches：`030` -> `010` -> `020` -> `040`。
3. 用 `replacements/frontend/*` 覆盖前端四个文件。
4. 如果你项目里有 `@/hooks/index.ts`、`@/types/index.ts`、`@/api/index.ts` 之类 barrel export，把新增/变更的导出同步进去。

## 设计原则

- **应用级单例可以保留**：`StrategyRunner` / route-level service singleton 是正常的。
- **策略实例单例必须删除**：策略代码模块不能持有共享运行对象。
- **start/resume 不再选 symbol**：symbol 是 deployment spec 的一部分，属于 load/create deployment 阶段。
- **运行时主键统一为 deployment_id**：template strategy_id 只负责标识“哪份策略代码”。

