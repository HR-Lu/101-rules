# 101-rules

172.20.22.101 监控服务器（Prometheus / Alertmanager / blackbox-exporter / 各数据库 exporter）配置的 git 化管理。

**目的**：监控配置是多人共用的，谁改了什么、什么时候改的、怎么回滚，全部要有记录，避免互相覆盖。

## 目录结构

```
├── prometheus.yml            # Prometheus 主配置（抓取目标 + rule_files 指向 rules/*.yml）
├── alertmanager.yml          # 告警路由：Mopheus 工单 + 企微推送双通道
├── alert_rules.yml           # 旧版规则（9-10，已被 rules/ 目录取代，仅存档）
├── blackbox.yml              # blackbox-exporter TCP 探针模块
├── docker-compose.monitor.yml # 监控栈容器编排
├── wecom_bridge.py           # Alertmanager → 企微群机器人 markdown 卡片桥接（:9595）
├── daily_check.sh            # 每日晨检脚本（22 连接登录验证，08:00 触发）
└── rules/                    # 告警规则（Prometheus rule_files 加载目录）
    ├── blackbox-tcp.yml      # TCP 探针可用性规则（9-11 上线，长期稳定）
    ├── dm-rules.yml          # DM 表空间规则（9-16 修复后恢复）
    ├── pg-rules.yml          # 9-18 新上线：仅 PG 死锁规则
    ├── ob-rules.yml          # 9-18 新上线：仅 OB 会话数 >100 规则
    ├── host-rules.yml        # 9-18 新上线：仅主机 CPU 规则（node_exporter 四台已部署）
    ├── dm-rules.yml.bak      # ↓ 以下 .bak 为 9-15 14:18 上线、14:51 停用的旧规则（见下），待对齐修复后合并回同名 .yml
    ├── mysql-rules.yml.bak
    ├── tidb-rules.yml.bak
    ├── ob-rules.yml.bak
    ├── pg-rules.yml.bak
    ├── oracle-rules.yml.bak
    └── host-rules.yml.bak
```

## 2026-09-18 变更记录

新增三条告警上线（只上新规则，.bak 旧规则不动，待对齐后合并去重）：

| 规则 | 表达式 | 阈值 | 级别 |
|---|---|---|---|
| PGDeadlockOccurred | `increase(pg_stat_database_deadlocks{dbtype="pg",datname!~"template.*"}[5m]) > 0` | 死锁事件增量 | critical（即时触发） |
| OBSessionsHigh | `mysql_global_status_threads_connected{job="ob-metrics"} > 100` | 会话绝对值 | warning（持续 2m） |
| HostCPUHigh / HostCPUCritical | `100 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m]))*100` | 80% / 95% | warning 10m / critical 5m |

配套变更：
- **node_exporter v1.12.1 首次部署**，四台主机全覆盖：101(amd64，`exporter_pkgs/bin/node_exporter`)、107/108/109(arm64，`/app/soft/install/node_exporter/`)，systemd 服务统一 `mop-node.service`，端口 9100；108 的 firewalld 已放行 9100。prometheus.yml 新增 `node` 抓取 job（db 标签 host-101~109，供 alertmanager group_by/inhibit 按主机区分）
- **发现 .bak 中 OBConnectionsHigh 是死规则**：OB 经 2881 返回 max_connections=2147483647（INT32_MAX 假值），使用率永远≈0。恢复旧规则时需换绝对值阈值（本次 OBSessionsHigh 即为替代实现）
- **教训：node job 刚上线时 rate() 冷启动失真**——前几分钟样本不足，CPU 使用率外推虚高至 70%+（真实仅 5-8%，top 交叉验证），HostCPUHigh 短暂 pending 后自动消除，未发出误报。新指标 job 上线后应等 2 个完整 rate 窗口（10 分钟）再评估告警取值


## 2026-09-15 误报事件记录

14:18 上线的 7 个指标规则文件中有 4 处 bug，14:24 起造成误报风暴（企微 + 工单），14:51 全部改名 `.bak` 停用，待修复后恢复：

| 规则 | Bug | 修法 |
|---|---|---|
| OracleResourceLimitHigh | `oracledb_resource_current_utilization > 95` 拿**原始会话数**当百分比（实际利用率 10.5%） | `100 * oracledb_resource_current_utilization / oracledb_resource_limit_value > 95` |
| OracleSessionsHigh（潜伏） | 分母用了当前进程数（95）而非 processes 上限（640） | 分母改 `oracledb_resource_limit_value{resource_name="processes"}` |
| DMArchiveLogStale / DMArchiveStatusInvalid | DM 为非归档模式（ARCH_MODE=N，备份走 dexp），`arch_last_create_time=0`、`arch_status=-1` 均为正常状态 | 删除，或仅在归档开启时检查（`and dmdbms_arch_status > 0`） |
| DMBufferPoolHitRatioLow（潜伏） | `dmdbms_bufferpool_info` 返回 1（状态标志），不是命中率小数 | 换正确的命中率指标 |
| OBTabletCountMismatchMissing | `vector(0)` TODO 占位规则，永久为真 | 删除（缺 OB 专属 exporter，属待办事项非告警） |

## 部署 / 生效方式

配置改动在 **101 宿主机** `/app/soft/install/monitoring/` 上进行（容器内挂载为只读），改完热加载：

```bash
# 校验
docker exec dbmonitor-prometheus-1 promtool check config /etc/prometheus/prometheus.yml
# 热加载（不要重启容器）
curl -X POST http://localhost:9090/-/reload
```

## 协作规范

1. 改任何配置前，先在群里说一声，并在 101 上 `w` 确认没有别人正在操作
2. 改动后：commit 写清楚"谁 + 改了什么文件 + 为什么"，再 push 到本仓库
3. 规则文件按数据库类型分文件（mysql/tidb/ob/pg/oracle/dm/host）
4. 新规则上线前先用 PromQL 在 Graph 里实际验证取值，确认指标语义（是计数、小数还是百分比）

## 敏感信息说明

仓库内以下内容为**占位符**，真实值只存在于 101 服务器本地，不入库：

- `<WECOM_BOT_KEY>`：企微群机器人 key（wecom_bridge.py）
- `<MOPHEUS_WEBHOOK_TOKEN_*>`：Mopheus 自动化任务 webhook token（alertmanager.yml / daily_check.sh）

数据库 exporter 的连接密码在 101 `/app/soft/install/monitoring/exporter_pkgs/conf/`，不纳入本仓库。
