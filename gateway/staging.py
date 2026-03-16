"""
Staging engine.

Takes a ParsedOperation and:
  1. Takes a DB snapshot of affected messages (for rollback)
  2. Inserts a staged_operations row (status=pending)
  3. Updates local state optimistically (AI sees the change)
  4. Writes a 'staged' audit_log row
  5. Returns the operation_id

Sub-milestone: 1.1
"""
from gateway.operation_parser import ParsedOperation


async def stage_operation(operation: ParsedOperation, session_id: str) -> str:
    """Stage an operation and return its ID (e.g. 'op_a3f9c2')."""
    # TODO: implement in sub-milestone 1.1
    raise NotImplementedError
