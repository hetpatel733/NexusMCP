from datetime import datetime, timezone
from typing import Any, Dict, Optional

from motor.motor_asyncio import AsyncIOMotorDatabase

from .base import BaseRepository


class TokenRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.refresh_tokens)

    async def store_refresh_token(
        self,
        user_id: str,
        token_hash: str,
        jti: str,
        expires_at: datetime
    ) -> str:
        now = datetime.now(timezone.utc)
        doc = {
            "user_id": user_id,
            "token_hash": token_hash,
            "jti": jti,
            "expires_at": expires_at,
            "revoked": False,
            "created_at": now,
        }
        res = await self.collection.insert_one(doc)
        return str(res.inserted_id)

    async def get_valid_refresh_token(self, token_hash: str) -> Optional[Dict[str, Any]]:
        now = datetime.now(timezone.utc)
        return await self.collection.find_one({
            "token_hash": token_hash,
            "revoked": False,
            "expires_at": {"$gt": now}
        })

    async def revoke_token(self, token_hash: str) -> bool:
        res = await self.collection.update_one(
            {"token_hash": token_hash},
            {"$set": {"revoked": True, "revoked_at": datetime.now(timezone.utc)}}
        )
        return res.modified_count > 0

    async def revoke_all_user_tokens(self, user_id: str) -> int:
        res = await self.collection.update_many(
            {"user_id": user_id, "revoked": False},
            {"$set": {"revoked": True, "revoked_at": datetime.now(timezone.utc)}}
        )
        return res.modified_count
