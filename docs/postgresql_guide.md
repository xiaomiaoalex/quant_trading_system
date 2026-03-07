# 面向高中生的 PostgreSQL 讲解 + 数据库基础必修 + 本项目落地（Docker Compose 版本）

这份文档面向零基础读者，目标是用最直观的方式理解数据库与 PostgreSQL，并能在本项目里完成本地部署、连接与验证。

---

## 1. 数据库基础必修

### 1.1 数据模型与表结构
数据库可以理解为“有规则的表格仓库”。  
一张表由“行”和“列”组成。
- 行（Row）：一条记录，比如“某一次风险事件”。
- 列（Column）：这条记录的属性，比如 `event_id`、`scope`。

常见关系类型：
- 1 对 1：一个人对应一个身份证号。
- 1 对多：一个用户对应多条订单。
- 多对多：一个学生选多门课程，一门课程被多名学生选。

### 1.2 主键与外键
- 主键（Primary Key）：一行数据的唯一身份证，比如 `event_id`。
- 外键（Foreign Key）：用来连接另一张表的主键，保证关联正确。
- 参照完整性：不能指向不存在的数据。

### 1.3 范式（简单到够用）
范式是“减少重复、避免异常”的整理规则。
- 1NF：每个格子只放一个值，不要把多个值塞进同一列。
- 2NF：非主键列要完全依赖主键，避免“半依赖”。
- 3NF：非主键列不要依赖另一个非主键列。

适度反范式什么时候合理：
- 读远多于写，且瓶颈在查询性能。
- 能接受一定的数据冗余，换取更快读取。
- 有稳定的同步或定期修正机制。

### 1.4 SQL 必会
SQL 是“和数据库对话的语言”。最常用的四类：
```sql
-- 查询
SELECT * FROM risk_events WHERE scope = 'account' ORDER BY ingested_at DESC LIMIT 10;

-- 新增
INSERT INTO risk_events (event_id, dedup_key, scope, reason, recommended_level, ingested_at, data)
VALUES ('e1', 'k1', 'account', 'example', 2, NOW(), '{}'::jsonb);

-- 修改
UPDATE risk_events SET reason = 'updated' WHERE event_id = 'e1';

-- 删除
DELETE FROM risk_events WHERE event_id = 'e1';
```

### 1.5 索引
索引就像“书的目录”，能加速查询，但会增加写入成本。
- 适合建索引：经常被查询或排序的列。
- 不适合建索引：写入极频繁、查询很少的列。
- 索引不是越多越好。

### 1.6 事务与 ACID
事务是“一组操作要么全成功，要么全失败”。
- 原子性（Atomicity）：全部完成或全部回滚。
- 一致性（Consistency）：操作前后数据规则不被破坏。
- 隔离性（Isolation）：并发操作互不干扰。
- 持久性（Durability）：提交后数据不会丢。

### 1.7 隔离级别（只要概念）
- 读未提交：可能读到未提交的脏数据。
- 读已提交：不会读到脏数据，但可能不可重复读。
- 可重复读：同一事务内多次读取一致。
- 串行化：最安全，性能开销最高。

常见问题名词：
- 脏读：读到别人未提交的内容。
- 不可重复读：同一事务内两次读到不同结果。
- 幻读：同一条件查询，行数变了。

### 1.8 连接池
连接数据库很“贵”，频繁开关会慢。  
连接池会复用一组连接，提升性能和稳定性。

### 1.9 权限与安全
- 最小权限原则：只给必需权限。
- 不要把密码写死在代码里。
- 生产环境要区分账号和环境配置。

### 1.10 备份与恢复
为什么要备份：数据库是系统的“记忆”，一旦丢失会导致不可逆事故。
- 逻辑备份：导出 SQL 或数据文件，通用易迁移。
- 物理备份：直接备份数据文件，恢复快但依赖环境。

---

## 2. PostgreSQL 是什么（高中生版）
你可以把 PostgreSQL 当作“超级稳定的表格仓库”。  
它特点是稳定、可靠、能应对高并发，适合生产环境。

核心关键词：
- 数据库/表/行/列
- SQL
- 主键、唯一约束
- 索引
- 事务

---

## 3. 为什么项目里要用它（项目语境）
交易系统需要“可追溯、可重放、可去重”的事实记录。  
本项目规范强调 **PostgreSQL-First**。参考文件：`quant_trading_system v3.0.6-个人开发者版 技术规范.md`。

---

## 4. 怎么用（通用流程）
1. 启动数据库服务。
1. 创建数据库与用户。
1. 连接数据库。
1. 建表与索引。
1. 写入与查询。
1. 定期维护与备份。

连接方式示例：
```
postgresql://user:pass@host:port/db
```

---

## 5. 本项目里怎么用（落地点位）

### 5.1 PostgreSQL 存储实现
文件：`trader/adapters/persistence/postgres/__init__.py`
- 自动初始化表：`event_log`、`snapshots`、`risk_events`、`risk_upgrades`。
- 使用 `asyncpg` 连接 PostgreSQL。

### 5.2 风险事件仓库（带回退）
文件：`trader/adapters/persistence/risk_repository.py`
- PostgreSQL 优先，连接失败自动回退到内存存储。
- 使用 `dedup_key` 唯一约束保证幂等。

### 5.3 健康检查与测试
文件：`trader/api/routes/health.py`  
文件：`trader/tests/test_postgres_storage.py`

---

## 6. Docker Compose 实操步骤（本地）

### 6.1 最小 Compose 示例
来源：`quant_trading_system v3.0.6-个人开发者版 技术规范.md`
```yaml
version: "3.9"
services:
  postgres:
    image: postgres:16
    container_name: qts-postgres
    restart: unless-stopped
    environment:
      POSTGRES_DB: trading
      POSTGRES_USER: trader
      POSTGRES_PASSWORD: trader_pwd
    ports:
      - "5432:5432"
    volumes:
      - qts_pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U trader -d trading"]
      interval: 5s
      timeout: 3s
      retries: 10

volumes:
  qts_pgdata:
```

### 6.2 启动命令
```powershell
docker compose up -d
```

### 6.3 本地环境变量
```bash
POSTGRES_HOST=127.0.0.1
POSTGRES_PORT=5432
POSTGRES_DB=trading
POSTGRES_USER=trader
POSTGRES_PASSWORD=trader_pwd
# 或
POSTGRES_CONNECTION_STRING=postgresql://trader:trader_pwd@127.0.0.1:5432/trading
```

### 6.4 最小验证
```sql
SELECT 1;
```

---

## 7. 常见问题与排查
- 端口占用：确认本机 `5432` 未被其他服务占用。
- 容器未启动：检查 Docker Desktop 是否运行。
- 账号密码错误：确认环境变量与 Compose 一致。
- 依赖未安装：`asyncpg` 未安装会导致连接失败。
- 连接字符串错误：确认 `host/port/db/user/password` 都正确。

---

## 8. 可选测试
如果已启动 PostgreSQL 并配置环境变量，可运行：
```powershell
pytest -q trader/tests/test_postgres_storage.py
```

---

## 9. 小结
你只需要记住三件事：
- 数据库是“有规则的表格仓库”。
- PostgreSQL 是稳定强大的生产级选择。
- 本项目已经有落地代码，按本文档配置即可接入并验证。
