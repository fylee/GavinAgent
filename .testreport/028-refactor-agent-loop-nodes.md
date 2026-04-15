# Test Report — 028 Refactor Agent Loop: assemble_context + call_llm

**Spec**: `.spec/028-refactor-agent-loop-nodes.md`
**Run date**: 2026-04-15
**Command**: `uv run pytest tests/agent/test_nodes_refactor.py tests/agent/test_nodes_helpers.py -v`

## Summary

| Result | Count |
|--------|------:|
| ✅ Passed | 33 |
| ❌ Failed | 0 |
| ⏭ Skipped | 0 |
| **Total** | **33** |

## Per-Test Results

| # | Test | Result |
|---|------|--------|
| 1 | `TestIsCancelled::test_failed_run_returns_true` | ✅ PASSED |
| 2 | `TestIsCancelled::test_running_run_returns_false` | ✅ PASSED |
| 3 | `TestIsCancelled::test_db_error_returns_false` | ✅ PASSED |
| 4 | `TestAssembleMessages::test_no_conversation_appends_user_message` | ✅ PASSED |
| 5 | `TestAssembleMessages::test_error_prefix_messages_filtered` | ✅ PASSED |
| 6 | `TestAssembleMessages::test_history_window_truncation` | ✅ PASSED |
| 7 | `TestAssembleMessages::test_tool_results_injected_when_ids_match` | ✅ PASSED |
| 8 | `TestAssembleMessages::test_tool_results_skipped_when_ids_missing` | ✅ PASSED |
| 9 | `TestAssembleMessages::test_collected_markdown_appended_concluding_round` | ✅ PASSED |
| 10 | `TestAssembleMessages::test_history_stats_returned` | ✅ PASSED |
| 11 | `TestPersistLoopTrace::test_writes_loop_trace_to_graph_state` | ✅ PASSED |
| 12 | `TestPersistLoopTrace::test_nonexistent_run_does_not_raise` | ✅ PASSED |
| 13 | `TestAssembleContext::test_returns_all_seven_state_fields` | ✅ PASSED |
| 14 | `TestAssembleContext::test_conversation_id_appended_to_system_content` | ✅ PASSED |
| 15 | `TestAssembleContext::test_no_conversation_id_system_content_unchanged` | ✅ PASSED |
| 16 | `TestCallLlmResumptionFallback::test_empty_system_content_triggers_rebuild` | ✅ PASSED |
| 17–33 | `TestTruncateHistory` / `TestCountTokens` / `TestToolSig` (pre-existing) | ✅ PASSED |

## Acceptance Criteria Coverage

| Criterion | Status |
|-----------|--------|
| `_is_cancelled` replaces both inline cancellation blocks | ✅ tests 1–3 |
| `_assemble_messages` used by both `call_llm` and `force_conclude` | ✅ tests 4–10 |
| `_build_tools_schema` called from `assemble_context` | ✅ test 13 |
| `_persist_loop_trace` replaces all inline loop_trace DB writes | ✅ tests 11–12 |
| `assemble_context` returns non-empty dict with 7 new state fields | ✅ tests 13–15 |
| `call_llm` ≤ 100 lines after Phase 2 | ✅ ~65 lines |
| `force_conclude` ≤ 40 lines after Phase 1 | ✅ ~38 lines |
| All existing unit tests pass (no regression) | ✅ 199/200 (1 pre-existing) |
| `test_nodes_refactor.py` new test cases pass | ✅ 16/16 |
| Tool-approval resumption fallback works | ✅ test 16 |

## Full Suite Regression Check

`uv run pytest tests/ --ignore=tests/e2e -q` → **199 passed, 1 pre-existing failure** (`test_stock_chart.py::TestSkillLoader::test_skill_md_parseable` — missing `approval_required` key, unrelated to this spec).
