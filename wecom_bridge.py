#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Alertmanager webhook -> WeCom group bot markdown bridge.

Receives Alertmanager webhook payloads on :9595, converts each alert
to a WeCom markdown message (orange for firing, green for resolved)
and POSTs it to the group bot.
"""
import json
import logging
import re
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler

WECOM_URL = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=<WECOM_BOT_KEY>"
LISTEN_ADDR = "0.0.0.0"
LISTEN_PORT = 9595
LOG_FILE = "/app/soft/install/monitoring/logs/wecom_bridge.log"

TZ_BEIJING = timezone(timedelta(hours=8))
ALERT_NAME_CN = {
    "DBEndpointDown": "数据库连接中断",
    "DBProbeSlow": "数据库响应缓慢",
    "BlackboxExporterDown": "监控探针异常",
    "DMTablespaceHigh": "DM表空间使用率过高",
    "DMTablespaceCritical": "DM表空间使用率严重过高",
}
SEVERITY_CN = {"critical": "严重", "warning": "警告", "info": "提示"}
DB_NAME_MAP = {
    "dm-107": "mop_dm",
    "kingbase-108": "mop_kingbase",
    "gauss-109": "mop_gauss",
    "mgr-107": "mop_mysql",
    "mgr-108": "mop_mysql",
    "mgr-109": "mop_mysql",
    "ms-master-107": "mop_mysql",
    "ms-slave-108": "mop_mysql",
    "mysql-single-109": "mop_mysql",
    "tidb-107": "mop_tidb",
    "tidb-108": "mop_tidb",
    "tidb-109": "mop_tidb",
    "ob-107": "mop_ob",
    "ob-108": "mop_ob",
    "ob-109": "mop_ob",
    "ob-proxy-107": "mop_ob",
    "oracle-adg-107": "MOPADG",
    "oracle-adg-108": "MOPADG",
    "oracle-single-109": "MOPORA",
    "pg-primary-109": "mop_pg",
    "pg-replica-109": "mop_pg",
    "pg-single-109": "mop_pg",
}
DBTYPE_CN = {
    "dm8": "达梦DM8",
    "kingbase": "人大金仓",
    "opengauss": "openGauss",
    "mysql-mgr": "MySQL MGR",
    "mysql-ms": "MySQL 主从",
    "mysql-single": "MySQL 单机",
    "tidb": "TiDB",
    "oceanbase": "OceanBase",
    "oceanbase-proxy": "OceanBase Proxy",
    "oracle-adg": "Oracle ADG",
    "oracle-single": "Oracle 单机",
    "pg": "PostgreSQL",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_FILE), logging.StreamHandler()],
)
log = logging.getLogger("wecom-bridge")


def send_wecom(content):
    body = json.dumps({"msgtype": "markdown", "markdown": {"content": content}}).encode()
    req = urllib.request.Request(WECOM_URL, data=body, headers={"Content-Type": "application/json"})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read().decode())
            if result.get("errcode") == 0:
                return True
            log.error("wecom api errcode=%s errmsg=%s", result.get("errcode"), result.get("errmsg"))
        except Exception as e:
            log.error("wecom send attempt %d failed: %s", attempt + 1, e)
        time.sleep(2)
    return False


def fmt_time(ts):
    """Convert Alertmanager RFC3339 timestamp (usually UTC) to Beijing time."""
    if not ts:
        return ""
    m = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(Z|[+-]\d{2}:\d{2})?", ts)
    if not m:
        return ts[:19].replace("T", " ")
    date_s, time_s, off = m.group(1), m.group(2), m.group(3)
    try:
        dt = datetime.strptime(date_s + " " + time_s, "%Y-%m-%d %H:%M:%S")
        if off is None or off == "Z":
            dt = dt.replace(tzinfo=timezone.utc)
        else:
            sign = 1 if off[0] == "+" else -1
            dt = dt.replace(tzinfo=timezone(sign * timedelta(hours=int(off[1:3]), minutes=int(off[4:6]))))
        return dt.astimezone(TZ_BEIJING).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return ts[:19].replace("T", " ")


def fmt_duration(start_ts, end_ts):
    start, end = fmt_time(start_ts), fmt_time(end_ts)
    if not start or not end:
        return ""
    delta = datetime.strptime(end, "%Y-%m-%d %H:%M:%S") - datetime.strptime(start, "%Y-%m-%d %H:%M:%S")
    secs = int(delta.total_seconds())
    if secs < 0:
        return ""
    if secs < 60:
        return "%d 秒" % secs
    if secs < 3600:
        return "%d 分钟" % (secs // 60)
    return "%d 小时 %d 分钟" % (secs // 3600, (secs % 3600) // 60)


def alert_to_markdown(a):
    status = a.get("status", "firing")
    labels = a.get("labels", {})
    ann = a.get("annotations", {})
    name = labels.get("alertname", "告警")
    title = ALERT_NAME_CN.get(name, name)
    db = labels.get("db", labels.get("instance", "-"))
    dbtype_raw = labels.get("dbtype", "")
    dbtype = DBTYPE_CN.get(dbtype_raw, dbtype_raw)
    db_line = "%s (%s)" % (db, dbtype) if dbtype else db
    dbname = DB_NAME_MAP.get(db, "")
    severity = SEVERITY_CN.get(labels.get("severity", "-"), labels.get("severity", "-"))
    summary = ann.get("summary", "") or ann.get("description", "")
    start_ts = (a.get("startsAt") or "")
    end_ts = (a.get("endsAt") or "")

    if status == "resolved":
        lines = ["### <font color=\"info\">✅ 已恢复：%s</font>" % title]
        lines.append("**实例**: <font color=\"info\">%s</font>" % db_line)
        if dbname:
            lines.append("**库**: %s" % dbname)
        if start_ts:
            lines.append("**开始**: %s" % fmt_time(start_ts))
            lines.append("**恢复**: %s" % fmt_time(end_ts))
        dur = fmt_duration(start_ts, end_ts)
        if dur:
            lines.append("**持续**: %s" % dur)
    else:
        lines = ["### <font color=\"warning\">🔴 %s</font>" % title]
        lines.append("**实例**: <font color=\"warning\">%s</font>" % db_line)
        if dbname:
            lines.append("**库**: %s" % dbname)
        lines.append("**级别**: %s" % severity)
        if summary:
            lines.append("**详情**: %s" % summary)
        if start_ts:
            lines.append("**开始**: %s" % fmt_time(start_ts))
    lines.append("> Mopheus Demo 监控")
    return "\n".join(lines)


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length).decode() or "{}")
        except Exception:
            self.send_response(400)
            self.end_headers()
            return
        alerts = payload.get("alerts", [])
        ok = 0
        for a in alerts:
            if send_wecom(alert_to_markdown(a)):
                ok += 1
        log.info("received %d alerts, delivered %d", len(alerts), ok)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    server = HTTPServer((LISTEN_ADDR, LISTEN_PORT), Handler)
    log.info("wecom bridge listening on %s:%d", LISTEN_ADDR, LISTEN_PORT)
    server.serve_forever()
