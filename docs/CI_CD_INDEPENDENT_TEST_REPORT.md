# 新 CI/CD 阶段独立测试报告

## 身份与范围

- Role: independent tester。
- 角色 ID：`/root/cicd_independent_tester`。
- Task：验证父工作单 `artifacts/cicd/cross-platform-20261118/parent-work-order.md` 的 A1–A4。
- Base commit：`a547baf8bc0a36e9aaca76e09ece8c0463450b88`。
- 第 2 轮实现源码指纹：`2d484ec04ad8edd4d2ac721bfd8eeb446c49ef26968434236b0c6341911fb883`。
- 第 3 轮最终实现源码指纹（工程师提供并由本角色用 `artifacts/cicd/cross-platform-20261118/make_fingerprint.py` 复算）：`4d83a8d33ee8c5d9ff98bc0dc0c63e34953f393162029f0f4df099e2899a2066`。
- 独立测试文件（本角色新增）：`trader/tests/test_cicd_cicd_independent_a1_a4.py`。
- 本角色只写入独立测试文件、本报告和 `artifacts/cicd/cross-platform-20261118/tester-*`，未修改实现、CI manifest、工作流或旧 scan 证据。

## 测试设计

期望值来自父工作单 A1–A4 与通用 fail-closed 原则，不复用工程师实现的断言。

| 编号 | 验收意图 | 独立反例 |
|---|---|---|
| A1 | 统一入口保留真实退出码与 stdout/stderr；跨平台请求不支持时失败；缺证据失败 | 用假的失败 checker 触发 exit 7；请求 Windows 上执行 Linux-only scope；构造 missing JUnit 记录；越界 reports 路径 |
| A2 | PR 候选镜像、任务隔离、浏览器 smoke、Required 聚合 | 检查 job 在 `ci-required.needs`；project 含 run id/attempt；`down -v`；浏览器启动异常仍写脱敏证据；生产脚本无脏 checkout 旁路 |
| A3 | Compose 渲染与镜像在副作用前 fail-closed，报告不含秘密 | 非 loopback origin/端口、venue secret、缺少 env 时断言 env 文件未写出；摘要中无 password hash/PG 口令 |
| A4 | LF 约定与 runbook 责任说明 | `.gitattributes` 的 `*.sh text eol=lf`；runbook 含各阶段命令、构建构件与回滚边界 |

## 第 1 轮结果：FAIL

原始命令：

```text
python -m pytest -q trader/tests/test_cicd_cicd_independent_a1_a4.py --tb=short
```

结果：6 failed、8 passed。初始尝试还被测试分类门禁正确拒绝：
`ERROR: test file is not classified in ci/test_manifest.json`；随后工程师只登记该测试文件，未修改其内容。

| Finding | 结果 | 说明 |
|---|---|---|
| CICD-IND-1 | FAIL | 绝对 Python checker 路径被 runner 拒绝，fake check 返回 127 而非真实 7 |
| CICD-IND-2 | FAIL | PR Compose project 仅含 PR 号，重复运行会复用卷 |
| CICD-IND-3 | FAIL | 清理只 `down --remove-orphans`，未清理任务自有卷 |
| CICD-IND-4 | FAIL | 浏览器启动异常向外抛出，未写 evidence |
| CICD-IND-5 | FAIL | 生产 preflight 存在 `QTS_PLAN_ALLOW_DIRTY_FOR_CONTRACT_TEST` 脏 checkout 旁路 |
| CICD-IND-6 | FAIL | runbook 未明确“不得在预览主机重建” |

finding 已发给工程师；独立失败断言保留，未改为 skip/xfail，也未修改实现制造绿灯。

## 第 2 轮复验：PASS

工程师报告上述六项已修复，最终实现指纹 `2d484ec0...fb883`。复验命令：

```text
New-Item -ItemType Directory -Force -Path artifacts/cicd/cross-platform-20261118/pytest-tmp-independent-final
$env:PYTEST_DEBUG_TEMPROOT='artifacts/cicd/cross-platform-20261118/pytest-tmp-independent-final'
python -m pytest -q trader/tests/test_cicd_cicd_independent_a1_a4.py --tb=short -p no:cacheprovider
```

结果：`14 passed`（exit 0）。所有 6 个 finding 的反向断言均命中修复后的契约。

补充检查：

```text
python -m black --check --line-length 100 trader/tests/test_cicd_cicd_independent_a1_a4.py
python -m isort --check-only --profile black --line-length 100 trader/tests/test_cicd_cicd_independent_a1_a4.py
python scripts/verify_test_manifest.py --json-out artifacts/cicd/cross-platform-20261118/tester-manifest.json
```

结果：格式检查 exit 0；manifest `ok=true`，offline=198，总计 247 个测试文件。独立测试文件始终没有格式化失败。

## 环境执行记录

默认 `C:\Users\14463\AppData\Local\Temp\pytest-of-14463` 间歇返回 `PermissionError: [WinError 5]`；这是宿主临时目录 ACL/占用问题，不是产品失败。使用 `PYTEST_DEBUG_TEMPROOT` 指向任务自有目录后 14/14 通过。未修改 ACL 或宿主机配置。

## 未执行与限制

- 未在 Linux runner 实际运行 GitHub Actions；工作流结构和命令参数仅做静态契约检查。
- 未启动 Compose，未运行真实浏览器登录，未构建 PR 镜像；本轮验证的是 fail-closed 行为、必需 job 聚合和证据保存契约。
- 未运行完整 offline/P0/Postgres 套件；本角色只负责本阶段新增契约及基础清单校验。
- 未验证远端失败 CI run 的根因；该问题由父工作单 A1 单独记录，selector.poll 仍只是栈位置。

## Verdict

```json
{
  "overall": "pass",
  "round": 2,
  "blocking_findings": [],
  "closed_findings": [
    "CICD-IND-1",
    "CICD-IND-2",
    "CICD-IND-3",
    "CICD-IND-4",
    "CICD-IND-5",
    "CICD-IND-6"
  ],
  "pytest_exit_code": 0,
  "passed": 14,
  "failed": 0,
  "skipped": 0,
  "xfailed": 0,
  "workspace_write_scope_respected": true
}
```

## 第 3 轮复验（fresh reviewer 发现项修复后）

范围限定：本角色原有 14 项、reviewer 修复的 env hash 引号行为、clean-checkout bypass 移除。

复算得到相同实现指纹 `4d83a8d33ee8c5d9ff98bc0dc0c63e34953f393162029f0f4df099e2899a2066`。
本角色在自有测试文件新增 1 项行为断言：`write_env` 必须把含 `$` 的 `QTS_PREVIEW_PASSWORD_HASH`
单引号包裹，避免 Compose 展开；并把 bypass 检查扩展为禁止生产脚本读取 `getenv`/`environ`
或使用 `DEVNULL`。这些只写入 tester 自有文件，未改实现。

原始命令与结果：

```text
python artifacts/cicd/cross-platform-20261118/make_fingerprint.py
# -> 4d83a8d33ee8c5d9ff98bc0dc0c63e34953f393162029f0f4df099e2899a2066

New-Item -ItemType Directory -Force -Path artifacts/cicd/cross-platform-20261118/pytest-tmp-independent-r3b
$env:PYTEST_DEBUG_TEMPROOT=(Resolve-Path 'artifacts/cicd/cross-platform-20261118/pytest-tmp-independent-r3b').Path
python -m pytest -q trader/tests/test_cicd_cicd_independent_a1_a4.py --tb=short -p no:cacheprovider
# -> 15 passed, exit 0

python -m black --check --line-length 100 trader/tests/test_cicd_cicd_independent_a1_a4.py
python -m isort --check-only --profile black --line-length 100 trader/tests/test_cicd_cicd_independent_a1_a4.py
python scripts/verify_test_manifest.py --json-out artifacts/cicd/cross-platform-20261118/tester-manifest-r3.json
# -> format exit 0/0; manifest ok=true (offline=198,total=247)
```

首次 r3 命令因传给 `PYTEST_DEBUG_TEMPROOT` 的相对路径与 pytest 的目录创建顺序不一致而报
`WinError 3`；使用绝对路径后复验通过。这是执行环境路径问题，不是产品失败。默认 Windows
临时目录的间歇 `WinError 5` 仍是宿主 ACL 问题，未修改 ACL 或宿主配置。

第 3 轮 verdict：

```json
{
  "overall": "pass",
  "round": 3,
  "implementation_fingerprint": "4d83a8d33ee8c5d9ff98bc0dc0c63e34953f393162029f0f4df099e2899a2066",
  "blocking_findings": [],
  "pytest_exit_code": 0,
  "passed": 15,
  "failed": 0,
  "skipped": 0,
  "xfailed": 0,
  "workspace_write_scope_respected": true
}
```
