cd "/c/Users/char1/OneDrive/Desktop/Βουκεφάλας/alpha-test-1"
SP="C:/Users/char1/AppData/Local/Temp/claude/C--Users-char1-Desktop-----------/308c7c1e-7367-4168-975c-194e621756e1/scratchpad/mut5"
export BUCEPHALUS_DATA_DIR="$SP/data" BUCEPHALUS_CONFIG_DIR="$SP/cfg" PYTHONIOENCODING=utf-8
PY=~/.bucephalus-venv/Scripts/python.exe
m() { name=$1; shift; $PY scripts/mutate.py "$@" --jobs 6 --out "$SP/$name.json" > "$SP/$name.log" 2>&1; tail -1 "$SP/$name.log"; }
GATE="tests/test_agents_md.py tests/test_audit_2026_10_12.py tests/test_audit_iteration_3_fixes.py tests/test_empty_means_nothing.py tests/test_gate_mutants.py tests/test_junction_escape.py tests/test_m5_action.py tests/test_m8_security.py tests/test_mcp.py tests/test_mcp_protocol.py tests/test_new_code_mutants.py tests/test_parallel_tools.py tests/test_secrets_location.py tests/test_state_unreachable.py tests/test_sync_folder_state.py tests/test_tool_exposure.py"
m caps src/agent/security/capabilities.py $GATE --only _path_strings,_pathish_value,_names_something,_file_uri_path,_protected_at,within_scope,_argv_files,_modules
m mcp src/agent/execute/mcp.py tests/test_mcp.py tests/test_mcp_protocol.py tests/test_audit_2026_10_12.py
m shadow src/agent/execute/shadow.py tests/test_shadow_repo.py tests/test_rewind.py tests/test_audit_2026_10_12.py tests/test_diff_view.py
echo MUT5_DONE
