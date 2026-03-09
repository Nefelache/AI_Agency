"""
Session Manager — Conversation lifecycle.

Responsibilities:
  1. Create new sessions or resume active ones.
  2. Record turns (user / assistant messages).
  3. Detect termination and trigger session sealing.
  4. Enforce single-active-session-per-user invariant.
"""

from __future__ import annotations

import logging

from my_agent_os.memory_layer.models import Session, SessionStatus
from my_agent_os.memory_layer.store import MemoryStore
from my_agent_os.memory_layer.writer import MemoryWriter

logger = logging.getLogger(__name__)


class SessionManager:
    """Manages the lifecycle of conversation sessions."""

    def __init__(self, store: MemoryStore, writer: MemoryWriter):
        self._store = store
        self._writer = writer

    async def get_or_create(self, user_id: str) -> Session:
        """Return the active session or start a new one."""
        session = await self._store.get_active_session(user_id)
        if session:
            return session
        session = await self._store.create_session(user_id)
        logger.info("New session started: %s (user=%s)", session.id, user_id)
        return session

    async def record_turn(
        self, session_id: str, role: str, content: str
    ) -> None:
        await self._store.add_turn(session_id, role, content)

    async def process_and_maybe_seal(
        self,
        session_id: str,
        user_msg: str,
        assistant_msg: str,
        user_id: str = "default",
    ) -> bool:
        """
        Run the write pipeline on this turn.
        If the extraction signals should_seal, seal the session.
        Returns True if the session was sealed.
        """
        extraction = await self._writer.process_turn(
            session_id=session_id,
            user_msg=user_msg,
            assistant_msg=assistant_msg,
            user_id=user_id,
        )

        if extraction.should_seal:
            await self._writer.seal_session(session_id, user_id)
            logger.info("Session sealed: %s (topic=%s)", session_id, extraction.topic)
            return True

        return False

    async def force_seal(self, session_id: str, user_id: str = "default") -> None:
        """Manually seal a session (e.g. user explicitly ends conversation)."""
        await self._writer.seal_session(session_id, user_id)
        logger.info("Session force-sealed: %s", session_id)
