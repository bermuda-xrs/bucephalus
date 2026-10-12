import importlib.util, sys
from pathlib import Path
REPO = Path(r"C:\Users\char1\OneDrive\Desktop\Βουκεφάλας\alpha-test-1")
sys.path.insert(0, str(REPO / "src"))
spec = importlib.util.spec_from_file_location("repo_conftest", REPO / "tests" / "conftest.py")
mod = importlib.util.module_from_spec(spec); sys.modules["repo_conftest"] = mod; spec.loader.exec_module(mod)
for k in dir(mod):
    if not k.startswith("__"):
        globals()[k] = getattr(mod, k)
