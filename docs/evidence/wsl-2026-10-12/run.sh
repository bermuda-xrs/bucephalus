#!/bin/bash
set -u
SP="$1"
EV=~/buc-evidence2; rm -rf ~/buc ~/buc-evidence2 ~/.bucephalus-build ~/.buc-venv ~/.buc-build-venv; mkdir -p $EV
git clone -q -b harness-10 "$SP/h10.bundle" ~/buc && cd ~/buc
export PYTHONUTF8=1 BUCEPHALUS_DATA_DIR=/tmp/buc-data BUCEPHALUS_CONFIG_DIR=/tmp/buc-cfg
mkdir -p /tmp/buc-data /tmp/buc-cfg
{ python3 --version; uname -a; git log --oneline -1; nproc; free -g; } > $EV/step0.txt 2>&1
python3 -m venv ~/.buc-venv && . ~/.buc-venv/bin/activate
pip install -q -e ".[dev]" > $EV/step1.txt 2>&1; pip install -q ruff >> $EV/step1.txt 2>&1; echo "pip exit $?" >> $EV/step1.txt
python -m pytest tests/test_cli_subcommands.py -q -n 8 -p no:cacheprovider > $EV/step2.txt 2>&1; tail -3 $EV/step2.txt
python -m pytest tests -q -rs -n 8 -p no:cacheprovider > $EV/step3.txt 2>&1; tail -3 $EV/step3.txt
setsid python - > $EV/step4a.txt 2>&1 <<'PY'
import subprocess, sys
sys.path.insert(0, "src")
from agent.proc import kill_tree, process_table
print("process table rows:", len(process_table()))
c = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
open("/tmp/buc_probe_alive", "w").write("before")
kill_tree(c.pid)
open("/tmp/buc_probe_alive", "w").write("survived")
PY
echo "probe: $(cat /tmp/buc_probe_alive)" >> $EV/step4a.txt
python - > $EV/step4b.txt 2>&1 <<'PY'
import sys; sys.path.insert(0, "src")
from agent.execute.sandbox import run_sandboxed
r = run_sandboxed("b = bytearray(640 * 2**20); print('allocated')", timeout_s=60)
print(r["returncode"], r["stdout"].strip(), r["stderr"][-200:])
PY
python -m pytest tests/test_posix_platform.py tests/test_tombstone_floor.py -q -rs -p no:cacheprovider > $EV/step5a.txt 2>&1; tail -2 $EV/step5a.txt
python -m pytest tests/test_m5_action.py tests/test_sqlite_routes.py tests/test_sandbox_libraries.py tests/test_frozen_sandbox.py tests/test_every_verifier_can_fail.py tests/test_desktop.py tests/test_runlock.py tests/test_reaper_parentage.py -q -n 8 -p no:cacheprovider > $EV/step5b.txt 2>&1; tail -2 $EV/step5b.txt
deactivate
python3 -m venv ~/.buc-build-venv && . ~/.buc-build-venv/bin/activate
pip install -q . pyinstaller pytest pytest-asyncio pywebview "sympy==1.13.3" > $EV/step6-install.txt 2>&1; echo "pip exit $?" >> $EV/step6-install.txt
python packaging/build.py --probe > $EV/step6-build.txt 2>&1; echo "build exit $?" >> $EV/step6-build.txt; tail -3 $EV/step6-build.txt
P=$(ls -d ~/.bucephalus-build/dist/bucephalus-probe/bucephalus-probe 2>/dev/null)
if [ -n "$P" ]; then "$P" > $EV/step6.txt 2>&1; echo "probe exit $?" >> $EV/step6.txt; tail -3 $EV/step6.txt; fi
echo "probes left: $(ps -ef | grep -c '[b]ucephalus-probe')" >> $EV/step6.txt
python packaging/build.py > $EV/step7-build.txt 2>&1; echo "build exit $?" >> $EV/step7-build.txt; tail -3 $EV/step7-build.txt
A=$(ls -d ~/.bucephalus-build/dist/bucephalus/bucephalus 2>/dev/null)
if [ -n "$A" ]; then
  BUCEPHALUS_CONFIG_DIR=/tmp/buc-app-cfg BUCEPHALUS_DATA_DIR=/tmp/buc-app-data timeout 40 "$A" > $EV/step7.txt 2>&1; echo "app exit $? (124 = still running at 40 s, killed by timeout)" >> $EV/step7.txt
  sleep 2; echo "left after: $(ps -ef | grep -c '[d]ist/bucephalus/bucephalus')" >> $EV/step7.txt; ls /tmp/buc-app-data >> $EV/step7.txt 2>&1
  tail -6 $EV/step7.txt
fi
echo ALL_DONE
