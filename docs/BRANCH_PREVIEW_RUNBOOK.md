# 分支预览（branch preview）运行手册

版本：3；生效日期：2026-11-13。本手册描述**分支预览入口**：用一套与共享本地栈、
与 Demo 发布栈都隔离的 Compose 叠加层，在自己的服务器上提供一个**可访问的真实
控制台**（UI + Control + Research/History + 独立 PostgreSQL）。

**这不是正式发布。** 预览不产生任何发布证据、观察窗口、DR 证明或回滚承诺；
`deploy/compose.demo.yml` 与 Release Build 的 main provenance / DR / 外部告警门禁
完全不受影响，也不得因为预览而放宽。

## 1. 边界（先读这一节）

| 允许 | 禁止 |
|---|---|
| 通过网关同源访问 UI、Control、Research、History | 启动 execution / 任何下单、撤单或行情私有流 |
| 看到真实的 blocked / empty 状态 | 伪造 READY、伪造 DR 证据或屏蔽 DR 门禁 |
| 使用一次性随机数据库口令与 scrypt 口令哈希 | 复用生产/Demo 凭据、默认弱口令或把秘密写进仓库 |
| 在自己的服务器上以自己的 Compose project 运行 | 触碰服务器上既有的 Hermes、WebUI、rclone 或备份对象 |
| 用 `docker compose stop` / `down` 回滚（保留卷） | `down -v`、清理其他应用或删除既有备份 |

预览栈的关键性质（由 `deploy/compose.preview.yml` 强制）：

- `execution` 与 `backup` 服务带 `profiles: ["disabled"]`，**永远不会启动**；
- 渲染后的栈里**没有任何 exchange 凭据**（顶层 `secrets` 被清空，Control 保持
  无凭据契约）；
- Control 的 `/var/lib/qts/dr` **不挂载任何 DR 证据目录**，`QTS_DR_REQUIRED=1`
  因此保持不满足：`/health/trading` 返回 503 且 `reasons` 含
  `DR_EVIDENCE_MISSING`，控制台据此显示真实阻断状态；
- 唯一对外端口是网关的 `127.0.0.1:18080`；PostgreSQL 与 Control **不发布端口**，
  只在 Compose 网络内可达。

## 2. 组件与版本绑定

预览必须绑定到**确定的**构件，不能用浮动标签：

| 构件 | 绑定方式 | 说明 |
|---|---|---|
| 运行时镜像 | `QTS_PREVIEW_RUNTIME_IMAGE`（显式 tag）+ `QTS_PREVIEW_RUNTIME_IMAGE_ID`（实际 image ID） | 由有 Docker 的机器构建后 `save`/`load` 过去，预览主机**不构建、不拉取** |
| 控制台镜像 | `QTS_PREVIEW_UI_IMAGE`（显式 tag） | `Frontend/Dockerfile.preview`：Node 构建 → nginx 运行期 |
| PostgreSQL 镜像 | 固定在叠加层：`postgres:16@sha256:5db6dac2...` | 与 Demo 发布路径同一不可变引用，不从环境变量读取 |
| 代码 | `QTS_PREVIEW_COMMIT`（40 位 commit） | 作为容器 label 与 `QTS_RELEASE_COMMIT` 写入 |
| 预览标识 | `QTS_PREVIEW_ID` | 例如 `prev-2026-11-12-1` |

基础镜像按 manifest digest 固定（`Frontend/Dockerfile.preview` 内已写死）：
`nginx@sha256:583b7f2fce1cdb8da5ad5c39ebd3c33b98b87d493190e548bdee1fa8c619c311`
（2026-11-12 核验为当前 manifest）与
`node@sha256:fb4cd12c85ee03686f6af5362a0b0d56d50c58a04632e6c0fb8363f609372293`。
不得改用 `latest` 或未固定的 tag。

## 3. 环境文件（放在仓库之外）

仓库只跟踪 `deploy/preview.env.example`。**实际使用的环境文件必须放在仓库之外**
（预览主机上例如 `/opt/qts-preview/preview.env`，或部署目录下的 `app/preview.env`），
避免误提交，也不受仓库更新影响。后文命令里的 `<preview.env>` 就是这个实际路径。

填充后先做一次 fail-closed 校验：

```text
python scripts/validate_preview_env.py --env-file <preview.env> --check-images
```

校验器会拒绝：缺失或空值、占位符（`REPLACE_WITH` 等）、非 40 位十六进制 commit、
非 `sha256:<64 hex>` 的镜像 ID、非 `scrypt$<salt>$<hash>` 的口令哈希、未加单引号
因而会被 Compose 展开 `$` 的口令哈希、非 loopback 的 `QTS_PREVIEW_ORIGIN`、以及
仍在使用的默认弱口令。`--check-images` 还会用
`docker image inspect` 比对**记录的 image ID 与主机上实际镜像的 ID** 是否一致，
不一致即失败——预览因此绑定到一次具体构建，而不是一个记不住的标签。

## 4. 构建与传输（在有 Docker 与网络的机器上执行）

```text
# 1) 控制台镜像（构建上下文是 Frontend/，不含 node_modules、.env、证据文件）
docker build -f Frontend/Dockerfile.preview -t qts-preview-ui:preview-2026-11-12 Frontend

# 2) 运行时镜像（若本机尚未构建）
docker build --build-arg QTS_REVISION=<40位commit> -t qts-runtime:preview-<tag> .

# 3) 记录实际 image ID，必须与 <preview.env> 中的值一致
docker image inspect --format '{{.Id}}' qts-runtime:preview-<tag>
docker image inspect --format '{{.Id}}' qts-preview-ui:preview-2026-11-12

# 4) PostgreSQL：叠加层固定引用 postgres:16@sha256:5db6dac2...
#    没有外网的主机不能 pull，必须先在有 registry 访问的机器上按该 digest 取出：
docker pull postgres:16@sha256:5db6dac2ef5dcff512f4f00a009c3c332b9f2f28f24277175bf3e4a3d02ef83a

# 5) 导出并传输到预览主机
docker save qts-runtime:preview-<tag> | gzip > qts-runtime-preview-<tag>.tar.gz
docker save qts-preview-ui:preview-2026-11-12 | gzip > qts-preview-ui-<tag>.tar.gz
docker save postgres:16@sha256:5db6dac2... | gzip > qts-postgres-pinned.tar.gz
scp qts-runtime-preview-<tag>.tar.gz qts-preview-ui-<tag>.tar.gz \
    qts-postgres-pinned.tar.gz alex@192.168.0.105:/tmp/

# 6) 在预览主机导入（主机不联网，不能 pull）
ssh alex@192.168.0.105 'gunzip -c /tmp/qts-runtime-preview-<tag>.tar.gz | docker load'
ssh alex@192.168.0.105 'gunzip -c /tmp/qts-preview-ui-<tag>.tar.gz | docker load'
ssh alex@192.168.0.105 'gunzip -c /tmp/qts-postgres-pinned.tar.gz | docker load'
```

**关于 digest 的如实说明**：`docker save` 保存的是**本地镜像**与其已知的
`RepoDigest`。如果导出时用的是浮动 tag（`postgres:16`），镜像携带的是**该 tag
当时指向的** manifest digest，可能与叠加层固定的 `5db6dac2...` 不同；这种
“只有 tag、digest 不匹配”的镜像**不满足**叠加层的固定引用，`up` 会失败。
正确做法是按上面的第 4 步**按 digest 引用**取出再导出，或在有 registry 访问的
机器上 `docker pull` 后核对：

```text
docker image inspect postgres:16@sha256:5db6dac2... --format '{{.Id}} {{index .RepoDigests 0}}'
```

预览的绑定证据是**实际 image ID**（`QTS_PREVIEW_RUNTIME_IMAGE_ID` 与校验器的
`--check-images` 比对），不是"看起来像 digest"的字符串。

## 5. 凭据（每次部署现生成，不入库）

```text
# 数据库口令：随机生成
python -c "import secrets; print('pg_' + secrets.token_urlsafe(24))"

# owner 口令哈希：在预览主机上用运行时镜像生成（交互输入，不进 argv）
docker run --rm -it qts-runtime:preview-<tag> python -m trader.api.auth
# 输出形如 scrypt$<32位十六进制盐>$<128位十六进制散列>
```

把两个值填进 `<preview.env>`。示例文件里只有 `REPLACE_WITH_*` 占位符，不得写入
真实值，也不得提交。

**口令哈希必须用单引号包起来**：

```text
QTS_PREVIEW_PASSWORD_HASH='scrypt$<32位盐>$<128位散列>'
```

Compose 会对未加引号（以及双引号）的 dotenv 值做 `$NAME` 展开，`scrypt$<盐>$<散列>`
会被截断成 `scrypt`，登录必然失败——这正是“部署 env 用错变量/占位”导致登录失败的
根因。单引号是唯一保持字面量的形式；把每个 `$` 写成 `$$` 也可行。校验器接受单引号
形式并会拒绝未加引号后被截断/含 `$` 展开风险的值。`token_urlsafe` 生成的数据库口令
不含 `$`，按上面的方式生成即可。

## 6. 启动

预览主机上需要仓库中的 `compose.yml`、`deploy/compose.preview.yml`、`<preview.env>`
与 `scripts/alert_receiver.py`（alert-sink 以只读方式挂载 `./scripts`）。

```text
python scripts/validate_preview_env.py --env-file <preview.env> --check-images

docker compose -f compose.yml -f deploy/compose.preview.yml \
  --env-file <preview.env> -p qts-branch-preview up -d

docker compose -f compose.yml -f deploy/compose.preview.yml \
  --env-file <preview.env> -p qts-branch-preview ps
```

`up` 不会构建任何镜像；`execution`/`backup` 不会启动；PostgreSQL、Control 与
Research/History 的数据写入预览专属卷 `qts-branch-preview_qts_preview_pgdata`
与 `qts-branch-preview_qts_preview_state`。

容器侧还有一道 fail-closed：网关的 entrypoint 在
`QTS_PREVIEW_ID` / `QTS_PREVIEW_COMMIT` / `QTS_PREVIEW_RUNTIME_IMAGE_ID` 缺失或
仍是占位符时直接退出（`PREVIEW_METADATA_MISSING` / `PREVIEW_METADATA_PLACEHOLDER`），
因此预览不可能对外发布一个伪造的 release 身份。

## 7. 访问方式（安全的访问路径）

网关只绑定 `127.0.0.1`。请从工作站通过**严格 SSH 隧道**访问，而不是直接用
服务器 IP 走明文 HTTP：

```text
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes \
  -L 18080:127.0.0.1:18080 alex@192.168.0.105
```

然后在浏览器打开 `http://127.0.0.1:18080/`。这样浏览器看到的源就是
`http://127.0.0.1:18080`，与 `QTS_ORIGIN` 一致，会话 Cookie 才是非 secure 的
loopback Cookie，登录才能成功。

**不要**把 `QTS_ORIGIN` 改成一个局域网 IP 来“图省事直接访问”：`AuthSettings`
只在 `http://127.0.0.1` / `http://localhost` 源下才允许非 secure Cookie，改成
局域网源会让登录在明文 HTTP 上失效；远程访问应当由 HTTPS 反向代理承担
（后续能力，本次不提供）。

## 8. 验收核对（在预览主机上执行）

```text
# 网关确实在服务 SPA，且深链回退到 index.html 而不是 404
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/monitor
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/research/data

# API 走同源代理；控制台可用时返回真实 JSON，出错时返回 HTTP 错误而不是 index.html
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/health/ready
curl -sS http://127.0.0.1:18080/health/trading

# 未登录访问受保护接口必须是 401（auth mandatory）
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/v1/auth/me

# execution / backup 没有启动；没有任何容器持有交易所凭据
docker compose -f compose.yml -f deploy/compose.preview.yml \
  --env-file <preview.env> -p qts-branch-preview ps -a
docker ps --filter label=com.docker.compose.project=qts-branch-preview \
  --format '{{.Names}}' | grep -E 'execution|backup' && echo UNEXPECTED || echo OK

# 镜像与记录的 image ID 一致（再次核对，防止有人换了标签）
docker image inspect --format '{{.Id}}' \
  "$(grep QTS_PREVIEW_RUNTIME_IMAGE= <preview.env> | cut -d= -f2)"
```

预期结果：`/health/trading` 返回 **503**，`reasons` 包含
`DR_EVIDENCE_MISSING`（预览没有 DR 证据，这是**正确**的真实阻断状态）；
`/health/ready` 返回 200；未登录访问 `/v1/auth/me` 返回 401；`/health/trading` 里的
`release` 字段必须是本次部署的真实 `QTS_PREVIEW_ID` / commit / image ID，
不能出现 `REPLACE_WITH`。

## 9. 停止与回滚

```text
# 停止但保留容器与卷（默认回滚方式）
docker compose -f compose.yml -f deploy/compose.preview.yml \
  --env-file <preview.env> -p qts-branch-preview stop

# 停止并移除容器，保留数据卷
docker compose -f compose.yml -f deploy/compose.preview.yml \
  --env-file <preview.env> -p qts-branch-preview down
```

`down` 不加 `-v`，预览卷保留，便于重启后继续查看。回滚只涉及本预览 project
自己的容器与卷；服务器上既有的应用与备份对象一律不动。

## 10. 相关文件

| 文件 | 作用 |
|---|---|
| `deploy/compose.preview.yml` | 预览叠加层：隔离 project/卷、禁用 execution/backup、清空交易所 secret、DR fail-closed、网关唯一端口、固定 PostgreSQL 镜像 |
| `deploy/preview.env.example` | 非密示例环境文件（全部占位符，实际文件放在仓库之外） |
| `scripts/validate_preview_env.py` | 启动前的 fail-closed 校验：占位符、格式、镜像 ID 与实际镜像一致 |
| `Frontend/Dockerfile.preview` | 控制台镜像：Node 构建 → nginx 运行期（digest 固定） |
| `Frontend/nginx.preview.conf` | SPA 静态服务 + `/api`、`/v1`、`/health` 同源代理 + 深链回退 |
| `Frontend/preview-entrypoint.sh` | 网关启动时的 release 身份 fail-closed 检查 |
| `Frontend/.dockerignore` | 控制台镜像构建上下文边界 |
| `docs/BRANCH_PREVIEW_RUNBOOK.md` | 本手册 |
