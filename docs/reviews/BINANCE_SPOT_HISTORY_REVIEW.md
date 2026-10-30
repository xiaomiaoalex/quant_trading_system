# Binance Vision 历史数据与回测闭环交付审核

## 版本与角色

- 分支：feature/binance-spot-history-backtest
- 基线：56a0fa08a07eb8bc29b4f71bf1117ea66bd8666c
- 初始实现提交：90664a2d31d0945a96e4101edc5e4e95ea96bb53
- 本记录随第 1 轮修复提交交付；修复提交的父提交为上述初始实现。
- 实现：用户指定 profile「实现者（MiniMax）」，实际 claude/claude-opus-5[1M]。
- 独立只读审核及复核：profile「审核员（DeepSeek）」，实际 deepseek/deepseek-v4-pro。
- 实现者完成修复代码后因 Token Plan 429 停止，主代理未替换实现模型；由同一审核员独立验证未提交修复，主代理完成最终 P0、构建、真实 E2E 和本地提交收尾。
- 共完成 1 轮修复及复核，未发现遗留 P0/P1；未推送、合并或部署。

## 审核发现与闭合情况

| 严重度 | ID | 首次发现 | 第 1 轮处理 |
| --- | --- | --- | --- |
| P1 | R1 | 重复提交先访问远程 HEAD，可导致已有数据集误报 404、拖慢 202 | 提交全程走数据库；缺失归档在 worker 下载阶段记录 FAILED / ARCHIVE_NOT_FOUND |
| P2 | R2 | 详情从最多 500 条列表筛选，旧数据集可能误报不存在 | 按 dataset_id 直接查询，600 条数据集回归验证 |
| P2 | R3 | 批量写入并发异值冲突可被 DO NOTHING 静默吞掉 | 单事务 INSERT RETURNING + 回读 hash；异值回滚且不覆盖旧值；真实 PG 并发及批内重复测试 |
| P3 | R4 | 名为 hash 变化的测试实际使用不同币对 | 改为同 symbol/month 的 v1 → v2，核对旧文件保留 |
| P3 | R5 | commit 复用文件未校验已有 hash、缺 CHECKSUM 无恢复 | 复用前核 hash，原子补写 CHECKSUM，篡改与崩溃窗口测试 |
| P3 | R6 | 校验和解析未按文件名匹配 | 按预期 ZIP 文件名匹配 |
| P3 | R7 | 本地版本化文件名与官方校验和原始名称不同 | 保留官方 CHECKSUM 原文，provenance 记录官方及本地名称 |
| P3 | R8 | 已发布数据集重复提交累计创建成功任务 | 返回关联成功任务并验证原 job_id 复用 |
| P3 | R9 | 前端资格校验未核 gap_count / row_count | 补零缺口及有效行数等于预期条数校验 |
| P3 | R10 | 无引用 utc_datetime | 已删除 |

复核中的 N2 已撤回：审核员在 PostgreSQL session-local TEMP 表实证，SUCCEEDED 新行不受仅收录 QUEUED/RUNNING 的部分唯一索引约束，因此不存在所述返回活动任务的路径。该实验连接关闭后自动清理。

### 当前唯一保留发现

**N1 / P3**：trader/adapters/persistence/history_dataset_store.py 的 find_active_job 及 trader/tests/fakes/fake_history_store.py 对应替身没有调用点。活动任务复用实际依靠 create_job 的数据库冲突处理，故不影响当前正确性；后续可删除无用方法。无 P0、P1 或 P2 遗留。

## 第 1 轮独立验证

审核员执行：

- 三个任务 / Adapter / API 测试文件：61 passed。
- 数据集解析回测准入：23 passed。
- FeatureStore / OHLCV / 现有 Binance worker 相关回归：62 passed。
- 真实 PostgreSQL 隔离库 qts_history_test：12 passed，含并发同值、异值、整批回滚、批内重复及严格模式故障。
- 前端全量 Vitest：90 passed / 11 files；TypeScript 检查通过。
- 改动 Python 文件 Black / isort 检查通过；Core 无 IO 架构检查通过。
- 审核及复核没有修改 tracked 文件。

主代理收尾执行：

- 仓库规定的五文件 P0 集：通过，exit 0。
- Frontend/npm run build：通过，exit 0；保留既有 bundle 体积和 Browserslist 过期提示。
- 构建生成的两个已跟踪 tsbuildinfo 已还原，不混入本次提交。
- python scripts/e2e_history_backtest.py --symbols BTCUSDT,ETHUSDT --month 2026-09 --test-db qts_history_e2e_review_final --keep-db：PASS，exit 0。

全量 Python 3034 项测试的 32 failures + 1 error 为实现者报告的基线既有问题（cl_ord_id 命名收口）。主代理与审核员均未独立重跑完整失败集合，不将全量测试标为通过；上述受影响回归及 P0 已独立通过。

## 最终真实数据验收证据

隔离数据库 qts_history_e2e_review_final；UTC 覆盖 2026-09-01T00:00:00Z 至 2026-09-30T23:59:00Z（最后 K 线开盘），每个数据集 43,200 条、0 缺口，官方与实际 SHA-256 一致。

| 币对 | dataset_id | job_id | 回测 run_id | 状态 |
| --- | --- | --- | --- | --- |
| BTCUSDT | hds_6597ef8743d04362 | hjob_2e089cc4ec80481f | da9564af-58b1-4229-8021-ecdf6c021c18 | COMPLETED |
| ETHUSDT | hds_4022a0debd904efe | hjob_224daa9441c84d20 | 4df3ce79-87f2-4f2f-af5a-edaecfac7c8e | COMPLETED |

- BTC 官方及实际 SHA-256：7a021fb5981cfdb1a56457d68fa81360ab27350c981ddfe63112b2322df44b56
- ETH 官方及实际 SHA-256：827b46043bdc877d542edf8ff178dc0411cfa166a56fa73f2f850ad06c951b50
- 来源分别为 https://data.binance.vision/data/spot/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2026-09.zip 与 https://data.binance.vision/data/spot/monthly/klines/ETHUSDT/1m/ETHUSDT-1m-2026-09.zip。
- 两次回测均为 vectorbt / real_feature_store，verified_rows=43200，fee_bps=10，slippage_bps=5。
- BTC 793 笔、ETH 804 笔；样例策略收益均为负，不作为盈利验收或投资结论。
- 新 store/service 实例回读：2 个任务均 SUCCEEDED，2 个数据集，两币各 43,200 条行情。
- 此重启验证是组件重新实例化与数据库回读；本次未执行完整 uvicorn 进程重启的浏览器 E2E。
- 原始 ZIP、官方 CHECKSUM 保留在 data/market/binance/spot/raw/klines/<symbol>/1m/；该目录不提交 Git。
- 独立测试数据库保留供复查；未访问交易账户或执行策略部署。

## 能力与限制

交付覆盖真实 OHLCV 研究数据与现有回测闭环；单实例、单任务并发。尚无逐笔成交、盘口回测、组合回测、实时补数新功能及付费数据接入。前端未进行真实浏览器端到端测试；现有接口与组件测试、TypeScript、生产构建已通过。

### 实现者补充（第 1 轮修复之后的增量提交）

- N1（`find_active_job` 无调用点）：新增真实 PostgreSQL 并发用例
  `test_concurrent_job_creation_reuses_single_active_job` —— 并发提交同一
  `(source, symbol, interval, month)` 只保留**一个**活动任务，并直接用 `find_active_job`
  断言库内活动任务唯一。该方法由此获得明确调用点与用途（校验部分唯一索引的实际结果）。
- 归档复用路径补上临时文件清理：命中已发布数据集直接复用时丢弃下载临时文件
  （`BinanceVisionArchiveClient.discard`），避免 `.tmp` 目录残留。
- 归档提交路径统一为「总是调用 `commit()`」：同 hash 复用并补写缺失的 CHECKSUM，
  异 hash 抛错不覆盖；不再由调用方判断目标文件是否已存在。
