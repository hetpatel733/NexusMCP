from datetime import datetime, timezone
from typing import Any, Dict, Optional

from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorDatabase

from .base import BaseRepository


class UserRepository(BaseRepository):
    def __init__(self, db: AsyncIOMotorDatabase):
        super().__init__(db.users)

    async def get_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        return await self.collection.find_one({"email": email.strip().lower()})

    async def create_user(self, email: str, hashed_password: str, full_name: str) -> Dict[str, Any]:
        now = datetime.now(timezone.utc)
        user_doc = {
            "email": email.strip().lower(),
            "hashed_password": hashed_password,
            "full_name": full_name.strip(),
            "is_active": True,
            "created_at": now,
            "updated_at": now,
        }
        res = await self.collection.insert_one(user_doc)
        user_doc["_id"] = res.inserted_id
        return user_doc

    async def update_profile(self, user_id: str, full_name: Optional[str] = None, hashed_password: Optional[str] = None) -> bool:
        now = datetime.now(timezone.utc)
        update_data: Dict[str, Any] = {"updated_at": now}
        if full_name:
            update_data["full_name"] = full_name.strip()
        if hashed_password:
            update_data["hashed_password"] = hashed_password

        try:
            res = await self.collection.update_one(
                {"_id": ObjectId(user_id)},
                {"$set": update_data}
            )
            return res.modified_count > 0
        except Exception:
            return False
