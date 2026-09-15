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
    ├── dm-rules.yml.bak      # ↓ 以下 7 个文件 9-15 14:18 上线、14:51 停用（见下）
    ├── mysql-rules.yml.bak
    ├── tidb-rules.yml.bak
    ├── ob-rules.yml.bak
    ├── pg-rules.yml.bak
    ├── oracle-rules.yml.bak
    └── host-rules.yml.bak
```

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
