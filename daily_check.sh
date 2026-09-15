#!/bin/bash
# Demo 工作区每日晨检：swissql 全量连接测试，失败则推送 Mopheus 工单
# 由 crontab 以 moclaw 用户执行，日志在 monitoring/logs/
export PATH="/home/moclaw/.local/bin:/usr/local/bin:/usr/bin:/bin"

DEMO=130a2b20-170f-4869-bd15-8b3c5994eafe
WEBHOOK=http://172.20.22.101:8080/api/v1/webhooks/jobs/<MOPHEUS_WEBHOOK_TOKEN_DAILY>
LOGDIR=/app/soft/install/monitoring/logs
mkdir -p "$LOGDIR"
LOG="$LOGDIR/daily_check.log"
exec >> "$LOG" 2>&1

echo "=== $(date '+%F %T') daily check start ==="

if [ "$(whoami)" != "moclaw" ]; then
  echo "ERROR: must run as moclaw"
  exit 1
fi

swissql connections list --isolation-domain "$DEMO" -o json > /tmp/daily_conns.json 2>&1
python3 - > /tmp/daily_pids.txt <<'EOF'
import json
raw = open('/tmp/daily_conns.json').read()
start = raw.find('[')
end = raw.rfind(']')
if start < 0 or end < 0:
    print('PARSE_FAIL')
else:
    for c in json.loads(raw[start:end+1]):
        print(c.get('profile_id'), '|', c.get('name'))
EOF

FAILS=""
TOTAL=0
OK=0
while read -r PID REST; do
  [ "$PID" = "PARSE_FAIL" ] && { echo "ERROR: list parse failed"; exit 1; }
  NAME=$(echo "$REST" | sed 's/^| *//')
  TOTAL=$((TOTAL+1))
  OUT=$(swissql connections test "$PID" --isolation-domain "$DEMO" 2>&1)
  if echo "$OUT" | grep -q '"status": *"ok"'; then
    OK=$((OK+1))
  else
    MSG=$(echo "$OUT" | grep -o '"message": *"[^"]*"' | head -1 | sed 's/"message": *"//;s/"$//')
    [ -z "$MSG" ] && MSG=$(echo "$OUT" | grep -o 'Error: .*' | head -1 | head -c 120)
    [ -z "$MSG" ] && MSG="unknown error"
    MSG=${MSG//\"/\'}  
    FAILS="$FAILS{\"labels\":{\"alertname\":\"DBMorningCheckFail\",\"db\":\"$NAME\",\"env\":\"demo\",\"severity\":\"warning\"},\"annotations\":{\"summary\":\"【每日晨检失败】$NAME\",\"description\":\"$MSG\"}},"
    echo "FAIL $NAME | $MSG"
  fi
done < /tmp/daily_pids.txt

echo "result: total=$TOTAL ok=$OK fail=$((TOTAL-OK))"

if [ -n "$FAILS" ]; then
  FAILS=${FAILS%,}
  BODY="{\"receiver\":\"mopheus\",\"status\":\"firing\",\"alerts\":[$FAILS]}"
  RESP=$(curl -s -m 10 -X POST "$WEBHOOK" -H 'Content-Type: application/json' -d "$BODY")
  echo "webhook resp: $RESP"
else
  echo "all ok, no webhook"
fi
echo "=== daily check done ==="