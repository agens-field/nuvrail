"""
Operation revert logic.

Restores local state DB to pre-operation values using the
snapshot stored in staged_operations.snapshot.

Called on: POST /operations/:id/reject

Sub-milestone: 1.2
"""


async def revert_operation(operation_id: str) -> None:
    """
    Revert local state from snapshot and mark operation rejected.

    State machine:
      pending → rejected
        └─► restore messages.flags from snapshot
        └─► restore folder membership if op_type = move/copy
        └─► write audit_log event=rejected
    """
    # TODO: implement in sub-milestone 1.2
    raise NotImplementedError
