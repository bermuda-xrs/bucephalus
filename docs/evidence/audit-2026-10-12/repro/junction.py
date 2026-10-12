import os, sys, subprocess, tempfile, shutil
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from agent.execute.shadow import ShadowRepo
base = Path(tempfile.mkdtemp(prefix="jx_"))
root = base / "proj"; (root / "work" / "d").mkdir(parents=True)
outside = base / "outside"; outside.mkdir()
(outside / "keep.txt").write_text("precious", encoding="utf-8")
(root / "work" / "d" / "f.txt").write_text("v1", encoding="utf-8")
repo = ShadowRepo(base / "shadow", root, ["work"])
sha1 = repo.checkpoint("step0")
# model swaps work/d for a junction to outside
shutil.rmtree(root / "work" / "d")
subprocess.run(["cmd", "/c", "mklink", "/J", str(root / "work" / "d"), str(outside)], check=True, capture_output=True)
(root / "work" / "new.txt").write_text("x", encoding="utf-8")
sha2 = repo.checkpoint("step1")
print("diff:", repo.changed(sha1, sha2))
repo.restore(sha1)
print("outside after restore:", sorted(os.listdir(outside)))
print("work/d is junction:", os.path.isjunction(root/"work"/"d") if hasattr(os.path,"isjunction") else os.path.islink(root/"work"/"d"))
print("work/d contents:", sorted(os.listdir(root/"work"/"d")))
shutil.rmtree(base, ignore_errors=True)
