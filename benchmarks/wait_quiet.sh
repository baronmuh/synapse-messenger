#!/bin/bash
# Quiet-window monitor for the perf gate bench.
# Samples load/mem/temp every 20s; prints a line when the heavy-bench
# conditions are met (load1 < 2.5, MemAvail >= 3.0Gi, temp < 80C),
# then exits 0. Otherwise exits 1 after the timeout.
TIMEOUT_S="${1:-1800}"   # default 30 min
DEADLINE=$(( $(date +%s) + TIMEOUT_S ))
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  LOAD1=$(awk '{print $1}' /proc/loadavg)
  MEM=$(awk '/MemAvailable/ {printf "%.1f", $2/1024/1024}' /proc/meminfo)
  TEMP=$(sensors 2>/dev/null | awk '/Package id 0:/ {print $4}' | tr -d '+°C' | cut -d. -f1)
  [ -z "$TEMP" ] && TEMP=0
  NOW=$(date +%H:%M:%S)
  if awk -v l="$LOAD1" -v m="$MEM" -v t="$TEMP" 'BEGIN{exit !(l<2.5 && m>=2.5 && t<80)}'; then
    echo "QUIET at $NOW: load1=$LOAD1 MemAvail=${MEM}Gi temp=${TEMP}C"
    exit 0
  fi
  echo "waiting $NOW: load1=$LOAD1 MemAvail=${MEM}Gi temp=${TEMP}C"
  sleep 20
done
echo "TIMEOUT: no quiet window in ${TIMEOUT_S}s"
exit 1
