# 阶段 5 运行与恢复手册

日期：2026-11-03。适用分支：`feat/foundation-refactor`。本手册描述单机 Linux
Docker Compose 运行、隔离演练、备份/恢复和故障处理；不授权生产部署、真实资金或
对既有数据/资源的破坏性操作。

## 1. 拓扑

`compose.yml` 提供：

| 服务 | 作用 | 凭据 |
|---|---|---|
| `postgres` | 唯一 PostgreSQL 16 数据源 | `POSTGRES_*` |
| `control` | 独立 Control API（只读查询、命令入队） | 无交易所凭据 |
| `execution` | 唯一执行 owner（租约、对账、发送） | Docker secrets 中的交易所密钥 |
| `research` | 研究任务 worker | 仅 PG |
| `history` | Binance Vision 归档 worker | 仅 PG |
| `backup` | 定时 `pg_dump` + S3 兼容目标回读校验 | PG + S3 |
| `alert-sink` | 演练用告警接收端（webhook + 心跳 + ack） | 无 |

所有宿主端口、卷和项目名都参数化；不存在固定 `container_name`。默认项目名为
`${QTS_COMPOSE_PROJECT:-qts}`，默认 PG 宿主端口 `127.0.0.1:55432`，不复用现有
`qts-postgres` / `qts_pgdata`。

## 2. 启动前检查

```text
docker context show
docker info
docker compose -p qts_ops config --quiet
python scripts/check_credential_isolation.py --compose compose.yml
```

- Docker daemon 必须实际可用；`docker compose config` 只验证配置，不证明容器能启动。
- 交易所密钥写入 `./secrets/binance_api_key` 与 `./secrets/binance_secret_key`（已
  `.gitignore`），或通过 `BINANCE_API_KEY_FILE_PATH` / `BINANCE_SECRET_KEY_FILE_PATH`
  指向其他文件。不要把密钥写入 `.env`、argv、报告或 Compose 渲染输出。
- `QTS_DR_EVIDENCE_DIR` 必须存在；Control/Execution 只读挂载
  `dr-evidence.json`。没有足够 DR 证据时交易保持非 READY，这是预期行为。
- 备份必须配置真实 S3 兼容异机目标：`QTS_S3_ENDPOINT_URL`、`QTS_S3_BUCKET`、
  `QTS_S3_PREFIX`、`QTS_S3_ACCESS_KEY`、`QTS_S3_SECRET_KEY`，并由运维确认
  `QTS_BACKUP_OFF_HOST_ATTESTED=1` 与 `QTS_BACKUP_ATTESTED_BY`。本地 MinIO/emulator
  只能用于测试，不能作为 5.6 证据。

## 3. 正常启动与健康检查

```text
docker compose -p qts_ops up -d
docker compose -p qts_ops ps
curl -sS http://127.0.0.1:8090/health/live
curl -sS http://127.0.0.1:8090/health/ready
curl -sS http://127.0.0.1:8090/health/trading
```

- `/health/live` 200 仅表示进程存活。
- `/health/ready` 200 `degraded` + `STORAGE_UNAVAILABLE` 表示 API 请求依赖不可用（硬门禁仍是 `/health/trading`）。
- `/health/trading` 503 + `reasons` 表示不可交易；常见原因：
  `EXECUTION_INSTANCE_UNAVAILABLE`（执行进程/租约缺失）、
  `EXECUTION_INSTANCE_CONFLICT`（另一个实例持有租约）、
  `BALANCE_MISMATCH`、`UNKNOWN_COMMAND_PENDING`、`DR_*`。
- Execution 缺少密钥会以 `BINANCE_CREDENTIALS_MISSING` 退出码 2；不会带病启动。

## 4. 隔离演练（不得使用默认项目）

演练必须使用唯一项目、独立端口和独立卷：

```text
export QTS_COMPOSE_PROJECT=qts_p5_drill_$(date +%s)
export QTS_PG_PORT=55432
export QTS_CONTROL_PORT=58090
export QTS_ALERT_SINK_PORT=58085
python scripts/compose_smoke.py --project "$QTS_COMPOSE_PROJECT"
```

`compose_smoke.py` 会：

1. 记录既有 `qts-postgres` 容器/卷身份，结束时核对未被触碰；
2. 用空密钥文件验证 Execution 拒绝启动（退出码 2 + 原因码）；
3. 启动 postgres/alert-sink/control/research/history，等待健康；
4. 验证三个健康端点语义、Control 环境无交易所凭据；
5. 查询 alert-sink 的 heartbeat 与 `EXECUTION_LEASE_LOST` 事件；
6. `down -v` 只清理本次项目，输出资源身份与原始证据。

不得对现有项目执行 `down -v`，不得停止/覆盖既有 PG 服务或卷。

## 5. 备份与恢复

```text
# 备份（在应用镜像内或已安装 postgresql-client 的主机）
python scripts/backup_postgres.py --once --output-dir /var/lib/qts/backup
python scripts/backup_postgres.py --loop --output-dir /var/lib/qts/backup

# 恢复到独立任务数据库并验证事实
python scripts/restore_postgres_drill.py \
  --manifest /var/lib/qts/backup/latest-backup.json \
  --target-database qts_restore_$(date +%s) \
  --drop-after \
  --dr-evidence /var/lib/qts/dr/dr-evidence.json
```

- RPO 由 `QTS_BACKUP_INTERVAL_SECONDS` 控制：Compose 默认 `240s`，硬上限 `300s`；
  循环按**上一次开始时间**固定频率排程，避免 dump 耗时把完成间隔推过 RPO。`summarize_rpo`
  以 journal 中真实 `completed_at_ms` 计算间隔，单次备份不能满足 RPO。
- 备份只有在对象上传后 `head_object` 长度/元数据一致且重新下载哈希一致时才记
  `succeeded`；失败记录保留 `failed`，不得当作成功。
- 恢复必须创建独立数据库、比较同一快照的六张事实表摘要、校验 book/account 守恒，
  并实测 RTO；`verified=false` 或 RTO>3600s 的证据会被 DR gate 拒绝。
- 若远端 S3 服务仅绑定远机 loopback，使用 SSH 隧道并从本机连接转发端点；备份记录
  `transport=ssh_tunnel`、真实 `remote_host`、`tunnel_endpoint_host` 与
  `ssh_host_key_verified=true`，DR gate 据此验证异机身份；不得把 loopback 本身标成异机。
- `dr-evidence.json` 必须来自真实异机 S3 目标与真实恢复；本地 emulator 证据会被
  `DR_TARGET_NOT_VERIFIED_OFF_HOST` 拒绝。

## 6. 崩溃与租约演练

```text
python scripts/drill_crash_durability.py --output artifacts/foundation/phase5/evidence/crash-durability.json
python scripts/drill_lease_fencing.py --output artifacts/foundation/phase5/evidence/lease-fencing.json
```

- 崩溃演练：writer 进程提交命令/成交/账本后写入快照，被直接 kill；verifier 用全新
  连接比较六表计数与摘要，并启动新 runtime 确认发送次数为 0。
- 租约演练：冲突实例 `EXECUTION_INSTANCE_CONFLICT` 且零发送；旧租约过期后新实例
  fencing token 递增接管；旧实例 readiness 关闭且拒绝发送。告警监控必须投递
  `EXECUTION_LEASE_LOST`（接收端 ack 才算）。
- 这是任务自有 schema/一次性数据库范围内的演练；不得把历史证据冒充本次主机崩溃后的
  复验。

## 7. 告警

- Control 与 Execution 都会向 `QTS_ALERT_WEBHOOK_URL` 发送阻断告警，向
  `QTS_ALERT_HEARTBEAT_URL` 发送心跳。
- 只有接收端返回 `{"ack": true, ...}` 才记 delivered；失败会重试并保持 pending。
- 演练接收端：`python scripts/alert_receiver.py --journal /data/alerts.jsonl`；
  `/status` 返回 `events_received`、`heartbeats_received`、`last_heartbeat_at_ms`、
  `heartbeat_age_seconds`、`codes`。生产应指向外部告警服务，不把本机 `alert-sink`
  当作唯一告警通道。

## 8. 故障处理原则

- `/health/trading` 503 时不得手工放宽就绪判定；先按 `reasons` 修复依赖、证据或租约。
- 租约冲突时不要手动改 `trading_runtime_leases` 抢占所有权；让旧实例停止或等待过期，
  由 fencing token 决定新 owner。
- DR 证据缺失/过期时允许只读查询与撤单，不允放行新风险。
- 任何 `docker`/PG 环境阻塞都保留原始错误与证据，不用提权、改 ACL、清理既有数据来强行通过。

## 9. 指定异机备份目标（my-server）

用户指定并经 SSH 严格 host-key 校验的备份机。地址**不再内置默认值**：备份机地址
必须显式配置（`--remote-host` 优先于环境变量 `QTS_OFFHOST_HOST`），两者都没有时
脚本在产生任何远端副作用之前 fail-closed（退出码 2，错误
`REMOTE_HOST_NOT_CONFIGURED`），不会猜测旧地址或新地址。

- 当前地址：`192.168.0.105`，用户 `alex`，期望主机名 `my-server`；使用
  `BatchMode=yes` 与 `StrictHostKeyChecking=yes`。不复制私钥，不修改远端
  SSH/防火墙/ACL。
- 已知主机身份：该机器在 `known_hosts` 中登记的身份仍是历史地址 `192.168.3.4`。
  用 `--host-key-alias`（或 `QTS_OFFHOST_HOST_KEY_ALIAS`）显式指定该别名，SSH 以
  `HostKeyAlias=192.168.3.4` 去核对既有条目；**不关闭 host-key 校验**。例如：

```text
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o HostKeyAlias=192.168.3.4 \
  alex@192.168.0.105
```

  旧地址 `192.168.3.4` 在此只作为**已知主机身份别名**保留，不再是任何脚本的默认
  连接地址。
- 远端目录：`~/qts-backups/{bootstrap,data,secrets,artifacts}`（700），其中
  `bootstrap/rclone` 为固定来源的 rclone v1.75.2 二进制（SHA-256
  `ed7ab1f17876bb006c3df3949a3044959671afd9112362dacaa5dd1647735bc1`，
  来源 `https://downloads.rclone.org/rclone-current-linux-amd64.zip`，zip SHA-256
  `349ac8fba6ff65d6247043f1750cdcb518ec5d500ef91463a10d37c0ccdf3702`）。
- 远端任务自有 Docker 镜像由 `FROM scratch` + 该二进制构建，容器名
  `qts-p5-rclone-<run_id>`，仅绑定 `127.0.0.1:<remote_port>:9000`；bucket 在
  `~/qts-backups/data/<bucket>`，S3 凭据在 `~/qts-backups/secrets/rclone.env`
  （600，内容不进入仓库/argv/证据）。远端现有应用容器、端口和网络不触碰。
- 本机通过严格 SSH local forward 访问远端 loopback S3；证据记录
  `transport=ssh_tunnel`、`remote_host=192.168.0.105`、
  `remote_host_key_alias=192.168.3.4`、`tunnel_endpoint_host=127.0.0.1`、
  `ssh_host_key_verified=true`，不把 loopback 本身当作异机。
- 演练命令：

```text
python scripts/drill_offhost_backup.py \
  --remote-host 192.168.0.105 --host-key-alias 192.168.3.4 \
  --ssh-user alex --expected-hostname my-server \
  --evidence-dir artifacts/foundation/phase5/evidence/offhost
```

  也可以用环境变量等价配置（命令行优先）：

```text
export QTS_OFFHOST_HOST=192.168.0.105
export QTS_OFFHOST_HOST_KEY_ALIAS=192.168.3.4
```

该脚本会创建任务自有本地 PostgreSQL 容器/卷和源/恢复数据库，执行 3 次间隔 120s 的
真实备份（RPO 观测 <=300s），逐次核对远端 `head`+完整回读哈希，从远端对象恢复独立
数据库并核对六表事实/守恒，最后验证 DR gate 由 `DR_EVIDENCE_MISSING` 转为 ready。
远端 bucket/对象保留；本地任务 PostgreSQL 容器/卷和临时秘密文件清理。已结束的失败
尝试容器/网络/镜像也仅清理任务自有资源。

关闭/删除远端演练存储（只在需要清理时；会删除已验证备份对象，执行前另行确认）：

```text
ssh -o HostKeyAlias=192.168.3.4 alex@192.168.0.105 \
  'docker stop qts-p5-rclone-<run_id>; docker rm qts-p5-rclone-<run_id>; \
  docker network rm qts-p5-offhost-net-<run_id>; docker rmi qts-p5-rclone:<run_id>'
```

不要把 `~/qts-backups/data` 或 secrets 文件复制到本仓库/聊天/argv/证据。
