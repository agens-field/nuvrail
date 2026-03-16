"""
Undo logic for approved operations.

Reverses an executed operation within the undo window (default: 24h).
Not all op_types are undoable — see UNDOABLE_OP_TYPES.

Sub-milestone: 3.2
"""
import os

UNDO_WINDOW_HOURS = int(os.getenv("UNDO_WINDOW_HOURS", "24"))

UNDOABLE_OP_TYPES = {
    "mark_read", "mark_unread", "star", "unstar",
    "archive", "move", "copy", "trash",
}


async def undo_operation(operation_id: str) -> None:
    """
    Reverse an executed operation.
    Raises ValueError if operation is not undoable or window has passed.
    """
    # TODO: implement in sub-milestone 3.2
    raise NotImplementedError
