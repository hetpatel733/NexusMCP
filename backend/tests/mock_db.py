import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId


def match_filter(doc: Dict[str, Any], filter_dict: Dict[str, Any]) -> bool:
    for k, v in filter_dict.items():
        if k == "$or":
            if not isinstance(v, list):
                return False
            if not any(match_filter(doc, cond) for cond in v):
                return False
        elif k == "$and":
            if not isinstance(v, list):
                return False
            if not all(match_filter(doc, cond) for cond in v):
                return False
        elif isinstance(v, dict):
            # Operators like {"$gt": val}
            doc_val = doc.get(k)
            for op, target in v.items():
                if op == "$gt":
                    if doc_val is None or doc_val <= target:
                        return False
                elif op == "$lt":
                    if doc_val is None or doc_val >= target:
                        return False
                elif op == "$in":
                    if doc_val not in target:
                        return False
        else:
            if doc.get(k) != v:
                return False
    return True


class MockCursor:
    def __init__(self, docs: List[Dict[str, Any]]):
        self._docs = docs
        self._skip_n = 0
        self._limit_n = len(docs)
        self._sort_key = None
        self._sort_dir = 1

    def sort(self, key_or_list, direction=1):
        if isinstance(key_or_list, list):
            self._sort_key, self._sort_dir = key_or_list[0]
        else:
            self._sort_key = key_or_list
            self._sort_dir = direction
        return self

    def skip(self, n: int):
        self._skip_n = n
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    async def to_list(self, length: int = 100) -> List[Dict[str, Any]]:
        docs = list(self._docs)
        if self._sort_key:
            docs.sort(
                key=lambda d: d.get(self._sort_key, datetime.min.replace(tzinfo=timezone.utc)),
                reverse=(self._sort_dir == -1)
            )
        start = self._skip_n
        end = start + min(self._limit_n, length)
        return [copy.deepcopy(d) for d in docs[start:end]]


class MockResult:
    def __init__(self, inserted_id=None, modified_count=0, deleted_count=0):
        self.inserted_id = inserted_id
        self.modified_count = modified_count
        self.deleted_count = deleted_count


class MockCollection:
    def __init__(self, name: str):
        self.name = name
        self.docs: List[Dict[str, Any]] = []

    async def create_index(self, keys, **kwargs):
        pass

    async def insert_one(self, doc: Dict[str, Any]):
        doc_copy = copy.deepcopy(doc)
        if "_id" not in doc_copy:
            doc_copy["_id"] = ObjectId()
        self.docs.append(doc_copy)
        return MockResult(inserted_id=doc_copy["_id"])

    async def find_one(self, filter_dict: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        for d in self.docs:
            if match_filter(d, filter_dict):
                return copy.deepcopy(d)
        return None

    def find(self, filter_dict: Dict[str, Any]):
        matched = [d for d in self.docs if match_filter(d, filter_dict)]
        return MockCursor(matched)

    async def update_one(self, filter_dict: Dict[str, Any], update_dict: Dict[str, Any]):
        mod_count = 0
        for d in self.docs:
            if match_filter(d, filter_dict):
                if "$set" in update_dict:
                    d.update(copy.deepcopy(update_dict["$set"]))
                mod_count = 1
                break
        return MockResult(modified_count=mod_count)

    async def update_many(self, filter_dict: Dict[str, Any], update_dict: Dict[str, Any]):
        mod_count = 0
        for d in self.docs:
            if match_filter(d, filter_dict):
                if "$set" in update_dict:
                    d.update(copy.deepcopy(update_dict["$set"]))
                mod_count += 1
        return MockResult(modified_count=mod_count)

    async def delete_one(self, filter_dict: Dict[str, Any]):
        del_count = 0
        for i, d in enumerate(self.docs):
            if match_filter(d, filter_dict):
                del self.docs[i]
                del_count = 1
                break
        return MockResult(deleted_count=del_count)

    async def delete_many(self, filter_dict: Dict[str, Any]):
        del_count = 0
        remaining = []
        for d in self.docs:
            if match_filter(d, filter_dict):
                del_count += 1
            else:
                remaining.append(d)
        self.docs = remaining
        return MockResult(deleted_count=del_count)

    async def count_documents(self, filter_dict: Dict[str, Any]) -> int:
        return sum(1 for d in self.docs if match_filter(d, filter_dict))


class MockDatabase:
    def __init__(self):
        self.collections: Dict[str, MockCollection] = {}

    def __getattr__(self, name: str) -> MockCollection:
        if name not in self.collections:
            self.collections[name] = MockCollection(name)
        return self.collections[name]

    def __getitem__(self, name: str) -> MockCollection:
        return self.__getattr__(name)
