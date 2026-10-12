cd "/c/Users/char1/OneDrive/Desktop/Βουκεφάλας/alpha-test-1"
SP="C:/Users/char1/AppData/Local/Temp/claude/C--Users-char1-Desktop-----------/308c7c1e-7367-4168-975c-194e621756e1/scratchpad/mut"
export BUCEPHALUS_DATA_DIR="$SP/data" BUCEPHALUS_CONFIG_DIR="$SP/cfg" PYTHONIOENCODING=utf-8
PY=~/.bucephalus-venv/Scripts/python.exe
m() { name=$1; shift; $PY scripts/mutate.py "$@" --jobs 6 --out "$SP/$name.json" > "$SP/$name.log" 2>&1; tail -1 "$SP/$name.log"; }
m profiles src/agent/execute/tools.py tests/test_profiles.py tests/test_small_profile.py --only apply_profile,tool_apply_patch,SMALL_TOOLS,SMALL_ONLY,PATCH_ONLY
m config src/agent/config.py tests/test_read_first.py tests/test_inline_observation.py tests/test_profiles.py --only BackendConfig.requires_observation,BackendConfig.inline_observation_tokens
m loop src/agent/execute/loop.py tests/test_stuck_detection.py tests/test_read_first.py tests/test_rewind.py tests/test_inline_observation.py tests/test_window_check_at_start.py tests/test_profiles.py tests/test_hooks.py --only _warn_if_stuck,_changes_something,_observed,_inline_cap,_elision_hint,_check_window,_checkpoint,_file_bytes,_stop_blocks,STUCK_WARN,STUCK_HALT,MAX_STOP_BLOCKS
m rewind src/agent/execute/rewind.py tests/test_rewind.py
m shadow src/agent/execute/shadow.py tests/test_shadow_repo.py tests/test_rewind.py
m hooks src/agent/execute/hooks.py tests/test_hooks.py
m mcp src/agent/execute/mcp.py tests/test_mcp.py
m caps src/agent/security/capabilities.py tests/test_mcp.py tests/test_agents_md.py tests/test_tool_exposure.py --only _path_strings,_pathish_value,_names_something,_file_uri_path,_protected_at,within_scope,_looks_like_path
m agentsmd src/agent/context.py tests/test_agents_md.py --only AgentContext._load_project_notes,AgentContext.freeze_project_notes
m diffview src/agent/execute/diffview.py tests/test_diff_view.py
m say src/agent/execute/say.py tests/test_say.py
m queue src/agent/channel/telegram.py tests/test_channel_queue.py tests/test_channel.py --only TelegramBot._waiting,TelegramBot.drain_once,TelegramBot.work_queue,TelegramBot._handle_message,TelegramBot._handle_callback
m readfirst src/agent/execute/schema.py tests/test_read_first.py tests/test_constrained_calls.py
echo MUT_DONE
