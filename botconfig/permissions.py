"""
The Vice Verify Bot authenticates with the same `X-Bot-Secret` header as the
Vice Arcade bot, so the arcade's constant-time check is reused rather than
written a second time.
"""

import logging

from arcade.permissions import IsBot

logger = logging.getLogger(__name__)


class IsVerifyBot(IsBot):
    message = 'This endpoint is for the Vice Verify Bot.'

    def has_permission(self, request, view):
        try:
            return super().has_permission(request, view)
        except RuntimeError as error:
            # BOT_API_SECRET unset or blank. Refuse rather than 500, and never let a
            # missing secret count as a match.
            logger.error('Bot config refused a request: %s', error)
            return False
