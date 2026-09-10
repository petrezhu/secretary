# Secretary — 系统规格说明

## 监控阈值 (MonitorThresholds)

| 资源   | 默认阈值 | 说明                     |
|--------|----------|--------------------------|
| 内存   | 95%      | memory_percent            |
| CPU    | 95%      | cpu_percent (5min 均值)   |
| 磁盘   | 95%      | disk_percent              |

阈值在 `config.py` → `MonitorThresholds` 中定义，可通过 YAML 配置或环境变量覆盖。

## 调度器 (Scheduler)

- 默认 tick 间隔：30s
- 检查管线 (check_pipeline) 间隔：300s
- 最大重试次数：3

## 资源保护 (ResourceGuard)

- 超过阈值后进入指数退避：600s → 1200s → 2400s → 4800s → 9600s
- 退避状态持久化到 `/tmp/secretary_backoff.json`

## 自动修复 (AutoRepairer)

| 异常类型     | 修复动作                     | 需确认 |
|-------------|------------------------------|--------|
| service_down | systemctl restart {service}  | 否     |
| docker_down  | docker restart {container}   | 否     |
| disk_full    | 清理 >7d 日志 / >30d 备份     | 否     |
| cron_stuck   | 重置 last_run                | 否     |

## 通知层 (Notify)

- 支持 QQ Bot 和 Email 双通道
- 通知级别：INFO / WARNING / CRITICAL
