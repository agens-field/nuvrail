"""
Web Push notification sender.

Generates VAPID key pair on first run.
Sends push notifications to registered browser subscriptions when
operations are staged.

Sub-milestone: 2.2
"""


async def notify_staged(operation_id: str) -> None:
    """Send a Web Push notification for a newly staged operation."""
    # TODO: implement in sub-milestone 2.2
    raise NotImplementedError
