#!/bin/sh
# G1 replication on HARNESS 6: control and no-code-as-action, 3x each, interleaved.
PY=/c/Users/char1/.alpha-test-1-venv/Scripts/python.exe
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8
unset ANTHROPIC_API_KEY
for rep in 1 2 3; do
  for arm in control no-code-as-action; do
    echo "=== rep $rep $arm $(date +%T)"
    $PY bench/g1_ablation_run.py --suite bench/tasks --only $arm --out runs/g1-rep-v6/rep$rep-$arm
  done
done
echo "=== all done $(date +%T)"
