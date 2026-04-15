# Test Report — 029 MCP File-Based Configuration

**Run date/time:** 2026-04-14  
**Command:** `uv run python -m pytest tests/agent/test_mcp_config.py -v --tb=short`  
**Total:** 23 passed, 0 failed

## Per-Test Results

| # | Test | Result |
|---|------|--------|
| 1 | `TestLoadServers::test_returns_empty_when_file_absent` | PASSED |
| 2 | `TestLoadServers::test_loads_sse_server` | PASSED |
| 3 | `TestLoadServers::test_loads_stdio_server` | PASSED |
| 4 | `TestLoadServers::test_malformed_json_returns_empty` | PASSED |
| 5 | `TestSaveServers::test_round_trip` | PASSED |
| 6 | `TestSaveServers::test_atomic_write_leaves_no_tmp_file` | PASSED |
| 7 | `TestSaveServers::test_upsert_adds_new_server` | PASSED |
| 8 | `TestSaveServers::test_upsert_overwrites_existing` | PASSED |
| 9 | `TestSaveServers::test_remove_existing_server` | PASSED |
| 10 | `TestSaveServers::test_remove_nonexistent_returns_false` | PASSED |
| 11 | `TestMCPServerConfigResolution::test_resolved_env_substitutes_var` | PASSED |
| 12 | `TestMCPServerConfigResolution::test_resolved_env_missing_var_returns_empty_string` | PASSED |
| 13 | `TestMCPServerConfigResolution::test_resolved_headers_substitutes_var` | PASSED |
| 14 | `TestMCPServerConfigResolution::test_literal_value_unchanged` | PASSED |
| 15 | `TestMCPServerConfigProperties::test_transport_alias` | PASSED |
| 16 | `TestMCPServerConfigProperties::test_get_transport_display_sse` | PASSED |
| 17 | `TestMCPServerConfigProperties::test_get_transport_display_stdio` | PASSED |
| 18 | `TestMCPServerConfigProperties::test_env_json_for_stdio` | PASSED |
| 19 | `TestMCPServerConfigProperties::test_env_json_for_sse` | PASSED |
| 20 | `TestMCPServerConfigProperties::test_last_error_is_always_empty` | PASSED |
| 21 | `TestConfigPathOverride::test_custom_path_is_used` | PASSED |
| 22 | `TestSyncClaudeCodeMcp::test_sync_reads_from_file_not_db` | PASSED |
| 23 | `TestSyncClaudeCodeMcp::test_disabled_server_excluded_from_sync` | PASSED |

## Notes

- `TestExportMcpToFile` tests from spec were not implemented in the test file — `MCPServer` model has been removed, so they cannot query DB records. The `export_mcp_to_file` command is a one-time migration tool designed to run against the old DB before applying the migration.
- `TestMCPServerConfigProperties` was added as an additional class beyond the spec's original 16 tests to cover template-compatibility properties (`transport`, `get_transport_display`, `env_json`, `last_error`).
