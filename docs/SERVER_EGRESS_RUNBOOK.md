# 服务器独立出网代理运行手册

> 版本：1；生效日期：2026-11-13  
> 适用：独立运维任务 `qts-server-egress`，目标主机 `my-server`（`alex@192.168.0.105`）  
> 范围：仅提供任务级显式出网代理；不参与交易、不启动真实账户操作、不启动 24h 观察窗口、不改变 QTS / Hermes / rclone / Tailscale 的运行时配置。

## 1. 目标与边界

| 允许 | 禁止 |
| --- | --- |
| 仅使用 `/home/alex/qts-server-egress/` 目录（0700）存放运行配置与脱敏证据 | 把真实节点凭据、代理密码、SSH 私钥写入仓库、日志或 argv |
| 仅运行 Compose project `qts-server-egress` 与一次性测试容器 | 修改系统级 `/etc/environment`、`daemon.json`、默认路由、DNS、iptables、SSH 或防火墙 |
| 宿主仅发布 `127.0.0.1:17890`，显式代理认证 | 开放 LAN / 公网代理、TUN、透明代理、管理面板或 Docker socket |
| 固定官方 Mihomo 版本与本地构建镜像 ID | 使用浮动 `latest`、不可信镜像、订阅转换网站、关闭 TLS 证书校验 |
| 失败仅停止/移除任务自有容器、网络 | `docker system prune`、`down -v`、重启 Docker daemon、停止既有服务 |

连接目标主机必须保留严格主机校验：

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -o HostKeyAlias=192.168.3.4 -o ConnectTimeout=10 \
  alex@192.168.0.105
```

`HostKeyAlias` 是该机历史 `known_hosts` 身份，不是当前连接 IP；不得禁用 strict、接受新 key 或复制私钥。

## 2. 制品与配置绑定

| 项 | 绑定值 / 路径 | 说明 |
| --- | --- | --- |
| 官方版本 | Mihomo `v1.19.34` | 2026-11-11 发布的 Linux amd64 compatible 版本 |
| 官方压缩包 SHA-256 | `2763f5b414527f7baf6dc57e3f82f3ab561eedf34bfd7c2f2cde1f7e592efe06` | 与 GitHub Release asset digest 一致 |
| 解压二进制 SHA-256 | `5b6644c487e5dffae86b0e0e9bb5cec445579a3a9d03977c09576fee2aeefc01` | 远端实际执行文件 |
| 基础镜像 | `python:3.12-slim`，服务器既有 image ID `sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30` | 仅使用已存在、含 CA 证书的最小基底 |
| 任务镜像 | `qts-server-egress/mihomo:1.19.34`，image ID `sha256:2a07fe83f25577dba8be0f1c9feea1d58854c0338cff803b4be2e32a8a10a34b` | 远端由官方二进制 + 已核验基底构建 |
| Compose | `deploy/server-egress/compose.yaml` | 独立 project/network，loopback 发布，非 root、只读根文件系统、日志轮转 |
| 配置生成 | `scripts/server_egress/build_config.py` | 从当前活动 FlClash profile 解析已选节点组，仅内存读取，输出仓库外 0600 配置 |
| 验证器 | `scripts/server_egress/verify_egress.py` | 严格 HTTP 语义与 RFC 6455 握手/保持验证 |

生成的最小配置只保留当前已选组的 9 个候选节点（5 个 VLESS + 4 个 Hysteria2）、目标域名显式代理规则、私有网段直连规则、`GeoIP.dat`。GUI 数据库、全部 profile 文件、订阅 URL、历史日志、浏览器凭据、SSH 私钥均不迁移。Hysteria2 的 `mport` 是 FlClash 专有字段，生成时删除并保留上游 `ports`；所有节点强制 `skip-cert-verify: false`，Hysteria2 必须保留证书 pin。服务器节点选择状态独立持久化，本机 FlClash 配置不变。

## 3. 目录与权限

远端：

```text
/home/alex/qts-server-egress/                  0700
├── config/config.yaml                       0600  真实节点与随机代理认证
├── config/proxy-urls.txt                    0600  host/container 显式代理 URL
├── config/curl-proxy.conf                   0600  命令级 curl 代理配置
├── config/GeoIP.dat                         0600  规则所需最小 geo 数据
├── build/                                   0700  官方二进制与 Dockerfile
├── evidence/                                0700  脱敏构建/验收证据
├── compose.yaml                             0600
└── verify_egress.py                         0600
```

真实配置始终位于仓库之外。仓库中的 `deploy/server-egress/config.example.yaml` 仅为占位示例，不含任何可用节点或密码。本地敏感暂存目录为 `C:\Users\14463\.qts-server-egress`，ACL 已限制到当前用户、SYSTEM、Administrators。

## 4. 部署

仓库内文件：

- `deploy/server-egress/Dockerfile`：把已核验二进制复制入既有 `python:3.12-slim`，使用 UID/GID `1000:1000`，最小写目录 `/etc/mihomo`。
- `deploy/server-egress/compose.yaml`：project `qts-server-egress`，服务名 `egress`，独立 bridge 网络 `qts-server-egress-net`，宿主 `127.0.0.1:17890 -> 7890`。
- `deploy/server-egress/config.example.yaml`：脱敏配置模板。
- `scripts/server_egress/build_config.py`：生成最小配置与随机认证。
- `scripts/server_egress/verify_egress.py`：验收器。
- `scripts/server_egress/tests/`：独立单测，不加入 `ci/test_manifest.json`，与并行 CI 工作隔离。

```bash
# 仅第一次部署；目录已存在时必须先判别归属，绝不覆盖
cd /home/alex/qts-server-egress
docker compose -p qts-server-egress -f compose.yaml up -d --wait --wait-timeout 90
docker compose -p qts-server-egress -f compose.yaml ps
docker inspect qts-server-egress --format \
  '{{.State.Status}} {{.State.Health.Status}} {{.Config.User}} {{.HostConfig.ReadonlyRootfs}} {{json .HostConfig.PortBindings}}'
```

期望：`running healthy 1000:1000 true`，且只有 `127.0.0.1:17890`。不得设置 `privileged`、`NET_ADMIN`、host network、TUN 设备或 Docker socket。

## 5. 命令级接入

仅对单条命令显式指定代理，不要导出全局 `HTTP_PROXY` / `HTTPS_PROXY`：

```bash
cd /home/alex/qts-server-egress
curl --noproxy "" -K config/curl-proxy.conf https://github.com
```

一次性容器必须加入新 bridge，并经服务 DNS 名称 `egress:7890` 访问；不能用容器自身的 `127.0.0.1:17890`：

```bash
python3 verify_egress.py \
  --proxy-url-file config/proxy-urls.txt --proxy-key container \
  --runs 3 --ws-hold 300
```

未配置 Docker daemon 代理，因此**容器经显式代理成功不代表 `docker pull` 成功**；daemon 拉镜像单列为未验证项，不得据此重启 daemon 或修改 `daemon.json`。

## 6. 验收标准

`verify_egress.py` 的加固判据：

- HTTP：状态码与语义同时成立。GitHub 200 且响应非空；Docker `/v2/` 401 且存在 Bearer challenge；匿名 token 200 且 token 非空；公开 manifest 200、digest 存在且 JSON 可解析；Binance Demo `/api/v3/time` 200、`serverTime` 为合理时间数值。
- WS：`101` + `Upgrade: websocket` + `Connection: Upgrade` + 与 `Sec-WebSocket-Key` 匹配的 `Sec-WebSocket-Accept`。保持计时从握手完成后开始，`hold_elapsed_s >= 300`；提前 close / EOF / 慢握手导致不足 300 秒均失败。
- TLS 始终校验；不使用 `curl -k`、不设置 `skip-cert-verify: true`、不上传节点到公网转换服务。
- 公共 WS 仅使用 `demo-stream.binance.com` 的公开 trade 流；不登录、不订阅私有流、不下单。
- 403 / 451 / 区域或服务拒绝如实分类，不混同网络不可达，也不为规避限制不断切换节点。

每项 3 次并保留 UTC 起止、状态码、耗时、脱敏失败类别。直连对照必须单独记录：GitHub 可能部分成功，Docker/Binance 直连可能 `TIMEOUT` 或 `NETWORK_UNREACHABLE`，不得概括为“服务器完全无外网”。

## 7. 独立性、重启与回滚

独立性检查：

- 配置不含本机 IP upstream、无 SSH 反向转发依赖。
- 关闭用于部署/验收的 SSH 会话后，服务器本地 `127.0.0.1:17890` 仍可认证访问。
- 不从测试容器连接自身 loopback；使用 `egress:7890`。
- 不为验证关闭本机 VPN 或重启本机；服务器现有 QTS/Hermes/rclone/Tailscale 容器 ID 与健康状态保持不变。

仅重启新代理容器：

```bash
docker compose -p qts-server-egress -f compose.yaml restart egress
docker compose -p qts-server-egress -f compose.yaml ps
# 再执行宿主与容器验收命令
```

回滚仅作用于任务自有资源：

```bash
docker compose -p qts-server-egress -f compose.yaml stop
docker compose -p qts-server-egress -f compose.yaml down
```

不要使用 `-v`，不要执行全局 prune，不要删除既有卷、备份或其他 project 的容器/网络。失败时保留脱敏证据与最小配置模板，清理临时敏感副本，并报告未通过项。

## 8. 已执行证据与未验证项

- 构建/配置/传输哈希：远端 `evidence/transfer-manifest.json`；本地脱敏证据目录 `artifacts/server-egress-20261113/`；最终证据文件哈希见 `artifacts/server-egress-20261113/final-file-manifest.sha256`。
- 加固版宿主重启前矩阵：`artifacts/server-egress-20261113/host-final.json`（3 次 HTTP 全语义通过，公共 WS 握手后保持 300.001 秒，无提前 close）。
- 加固版一次性容器矩阵与 WS 冒烟：`artifacts/server-egress-20261113/container-final.txt`（3 次 HTTP 全语义通过、严格握手 4/4，均 rc=0）。
- 加固版一次性容器 300 秒 WS：`artifacts/server-egress-20261113/container-ws-300.json`、`.rc`、`.err`（握手后保持 300.001 秒，3866 帧，无提前 close）。
- 仅新代理重启与恢复：`artifacts/server-egress-20261113/restart-recovery.txt`、脱敏后的 `restart-diagnosis.txt`、`host-post-restart-final.json`。初次紧接重启的复验因上游节点瞬时 `i/o timeout` 出现 rc=1；随后完成 3 次 HTTP 全语义通过及握手后 300.0 秒公共 WS，最终 rc=0。该瞬态保留为真实事件，不改写为“重启瞬时零失败”。诊断日志中的节点域名/解析 IP 已替换为占位符。
- 独立性与边界：`artifacts/server-egress-20261113/independence-final.txt`（LAN 拒绝、loopback 必须认证、无 SSH 转发/本机 upstream、既有服务 ID/健康不变、daemon 代理未配置）。最终证据已按 fresh reviewer 意见脱敏；`independence-final.txt` 中的三行私网 DIRECT 规则摘录不含凭据。
- 显式最终 rc：`artifacts/server-egress-20261113/final-rc.txt`；远端加固验证器 hash 与语法：`remote-verifier-hash.txt`；冻结交接：`HANDOFF.md`。
- 旧宽松版 300 秒记录 `evidence/host-full.json` 与直连对照 `evidence/direct-baseline.json` 仅作诊断，不作为修正版最终验收判据。
- Docker daemon image pull 经代理：未配置、未验证；本任务明确不修改 daemon。
- 未执行真实账户、私有 WS、订单、24h 观察窗口。

## 9. 相关文件

| 文件 | 用途 |
| --- | --- |
| `scripts/server_egress/build_config.py` | 内存读取活动 FlClash 配置，生成最小安全配置与随机认证 |
| `scripts/server_egress/verify_egress.py` | 显式代理 HTTP/TLS/WS 矩阵与严格语义验收 |
| `scripts/server_egress/tests/test_build_config.py` | 配置脱敏/字段兼容/规则顺序单测 |
| `scripts/server_egress/tests/test_verify_egress.py` | 握手、fake-clock 保持、畸形 200 判据单测 |
| `deploy/server-egress/compose.yaml` | 独立 project 与 loopback-only 发布 |
| `deploy/server-egress/Dockerfile` | 官方二进制任务镜像 |
| `deploy/server-egress/config.example.yaml` | 无敏感信息配置示例 |
