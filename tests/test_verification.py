from backend.verification import (
    map_command_status,
    aggregate_run_status,
    ITEM_SUBMITTED,
    ITEM_COMPLETED,
    ITEM_FAILED,
    ITEM_EXPIRED,
    ITEM_LEGACY,
    RUN_SUCCESS,
    RUN_PENDING,
    RUN_PARTIAL,
    RUN_FAILED,
    RUN_UNVERIFIED,
)


class TestMapCommandStatus:
    def test_completed_command_counts_as_completed(self):
        assert map_command_status(200, {"status": "completed"}) == ITEM_COMPLETED

    def test_failed_command_counts_as_failed(self):
        assert map_command_status(200, {"status": "failed"}) == ITEM_FAILED

    def test_aborted_and_cancelled_count_as_failed(self):
        assert map_command_status(200, {"status": "aborted"}) == ITEM_FAILED
        assert map_command_status(200, {"status": "cancelled"}) == ITEM_FAILED

    def test_status_is_matched_case_insensitively(self):
        assert map_command_status(200, {"status": "Completed"}) == ITEM_COMPLETED

    def test_running_command_stays_open_for_the_next_pass(self):
        assert map_command_status(200, {"status": "queued"}) == ITEM_SUBMITTED
        assert map_command_status(200, {"status": "started"}) == ITEM_SUBMITTED

    def test_unknown_command_is_expired(self):
        assert map_command_status(404, None) == ITEM_EXPIRED

    def test_transient_server_error_stays_open(self):
        # 500 or a network failure (0) must not be mistaken for a verdict.
        assert map_command_status(500, None) == ITEM_SUBMITTED
        assert map_command_status(0, None) == ITEM_SUBMITTED

    def test_malformed_payload_stays_open(self):
        assert map_command_status(200, None) == ITEM_SUBMITTED
        assert map_command_status(200, {}) == ITEM_SUBMITTED

    def test_result_field_is_ignored(self):
        # `result` decays to "unknown" over time; only `status` is authoritative.
        assert map_command_status(200, {"status": "completed", "result": "unknown"}) == ITEM_COMPLETED


class TestAggregateRunStatus:
    def test_run_without_items_is_success(self):
        assert aggregate_run_status([]) == RUN_SUCCESS

    def test_all_completed_is_success(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_COMPLETED]) == RUN_SUCCESS

    def test_any_open_item_keeps_the_run_pending(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_SUBMITTED]) == RUN_PENDING
        assert aggregate_run_status([ITEM_FAILED, ITEM_SUBMITTED]) == RUN_PENDING

    def test_mixed_outcome_is_partial(self):
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_FAILED]) == RUN_PARTIAL
        assert aggregate_run_status([ITEM_COMPLETED, ITEM_EXPIRED]) == RUN_PARTIAL

    def test_only_failures_is_failed(self):
        assert aggregate_run_status([ITEM_FAILED, ITEM_FAILED]) == RUN_FAILED

    def test_failed_plus_expired_is_failed(self):
        assert aggregate_run_status([ITEM_FAILED, ITEM_EXPIRED]) == RUN_FAILED

    def test_only_expired_is_unverified_not_failed(self):
        # An outcome *arr no longer knows is not a proven failure.
        assert aggregate_run_status([ITEM_EXPIRED, ITEM_EXPIRED]) == RUN_UNVERIFIED

    def test_legacy_items_are_ignored(self):
        assert aggregate_run_status([ITEM_LEGACY, ITEM_LEGACY]) == RUN_SUCCESS
        assert aggregate_run_status([ITEM_LEGACY, ITEM_FAILED]) == RUN_FAILED
