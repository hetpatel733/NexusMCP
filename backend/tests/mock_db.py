import copy
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId


def _get_nested(doc: Dict[str, Any], key: str) -> Any:
    if "." not in key:
        return doc.get(key)
    parts = key.split(".")
    cur = doc
    for p in parts:
        if isinstance(cur, dict) and p in cur:
            cur = cur[p]
        else:
            return None
    return cur


def _set_nested(doc: Dict[str, Any], set_dict: Dict[str, Any]) -> None:
    for k, v in set_dict.items():
        if "." in k:
            parts = k.split(".")
            cur = doc
            for p in parts[:-1]:
                if p not in cur or not isinstance(cur[p], dict):
                    cur[p] = {}
                cur = cur[p]
            cur[parts[-1]] = copy.deepcopy(v)
        else:
            doc[k] = copy.deepcopy(v)


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
            doc_val = _get_nested(doc, k)
            for op, target in v.items():
                if op == "$gt":
                    if doc_val is None or doc_val <= target:
                        return False
                elif op == "$gte":
                    if doc_val is None or doc_val < target:
                        return False
                elif op == "$lt":
                    if doc_val is None or doc_val >= target:
                        return False
                elif op == "$lte":
                    if doc_val is None or doc_val > target:
                        return False
                elif op == "$in":
                    if not isinstance(target, (list, tuple, set)) or doc_val not in target:
                        return False
                elif op == "$ne":
                    if doc_val == target:
                        return False
        else:
            if _get_nested(doc, k) != v:
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
    def __init__(self, inserted_id=None, modified_count=0, deleted_count=0, upserted_id=None):
        self.inserted_id = inserted_id
        self.modified_count = modified_count
        self.deleted_count = deleted_count
        self.upserted_id = upserted_id or inserted_id


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

    async def update_one(self, filter_dict: Dict[str, Any], update_dict: Dict[str, Any], upsert: bool = False):
        mod_count = 0
        found = False
        for d in self.docs:
            if match_filter(d, filter_dict):
                found = True
                if "$set" in update_dict:
                    _set_nested(d, update_dict["$set"])
                if "$inc" in update_dict:
                    for inc_k, inc_v in update_dict["$inc"].items():
                        d[inc_k] = d.get(inc_k, 0) + inc_v
                mod_count = 1
                return MockResult(modified_count=mod_count)

        if not found and upsert:
            new_doc = copy.deepcopy(filter_dict)
            if "$set" in update_dict:
                _set_nested(new_doc, update_dict["$set"])
            if "$setOnInsert" in update_dict:
                _set_nested(new_doc, update_dict["$setOnInsert"])
            if "$inc" in update_dict:
                for inc_k, inc_v in update_dict["$inc"].items():
                    new_doc[inc_k] = inc_v
            if "_id" not in new_doc:
                new_doc["_id"] = ObjectId()
            self.docs.append(new_doc)
            return MockResult(inserted_id=new_doc["_id"], modified_count=1)

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

    def aggregate(self, pipeline: List[Dict[str, Any]]) -> MockCursor:
        current_docs = [copy.deepcopy(d) for d in self.docs]

        for stage in pipeline:
            if "$match" in stage:
                current_docs = [d for d in current_docs if match_filter(d, stage["$match"])]
            elif "$sort" in stage:
                sort_spec = stage["$sort"]
                for k, v in reversed(list(sort_spec.items())):
                    current_docs.sort(
                        key=lambda d: d.get(k) if d.get(k) is not None else datetime.min.replace(tzinfo=timezone.utc),
                        reverse=(v == -1),
                    )
            elif "$limit" in stage:
                current_docs = current_docs[: stage["$limit"]]
            elif "$project" in stage:
                proj = stage["$project"]
                projected = []
                for d in current_docs:
                    new_d = {"_id": d.get("_id")}
                    for k, flag in proj.items():
                        if flag:
                            new_d[k] = d.get(k)
                    projected.append(new_d)
                current_docs = projected
            elif "$group" in stage:
                group_spec = stage["$group"]
                group_key_field = group_spec.get("_id")
                # Group by group_key_field
                groups: Dict[Any, List[Dict[str, Any]]] = {}
                for d in current_docs:
                    if isinstance(group_key_field, str) and group_key_field.startswith("$"):
                        g_val = _get_nested(d, group_key_field[1:])
                    else:
                        g_val = group_key_field
                    groups.setdefault(g_val, []).append(d)

                aggregated = []
                for g_val, group_docs in groups.items():
                    res_item = {"_id": g_val}
                    for out_field, accum in group_spec.items():
                        if out_field == "_id":
                            continue
                        if not isinstance(accum, dict):
                            continue

                        if "$sum" in accum:
                            sum_spec = accum["$sum"]
                            if sum_spec == 1:
                                res_item[out_field] = len(group_docs)
                            elif isinstance(sum_spec, dict) and "$cond" in sum_spec:
                                cond_spec = sum_spec["$cond"]
                                count = 0
                                for gd in group_docs:
                                    if len(cond_spec) == 3:
                                        test_expr, true_val, false_val = cond_spec
                                        if "$gt" in test_expr:
                                            f, val = test_expr["$gt"]
                                            f_name = f[1:] if f.startswith("$") else f
                                            passed = (gd.get(f_name) or 0) > val
                                        elif "$eq" in test_expr:
                                            f, val = test_expr["$eq"]
                                            f_name = f[1:] if f.startswith("$") else f
                                            passed = gd.get(f_name) == val
                                        else:
                                            passed = False
                                        count += true_val if passed else false_val
                                res_item[out_field] = count
                            elif isinstance(sum_spec, str) and sum_spec.startswith("$"):
                                res_item[out_field] = sum(gd.get(sum_spec[1:], 0) for gd in group_docs)
                        elif "$avg" in accum:
                            avg_field = accum["$avg"]
                            f_name = avg_field[1:] if isinstance(avg_field, str) and avg_field.startswith("$") else avg_field
                            vals = [gd.get(f_name) for gd in group_docs if gd.get(f_name) is not None]
                            res_item[out_field] = (sum(vals) / len(vals)) if vals else None
                        elif "$first" in accum:
                            first_field = accum["$first"]
                            f_name = first_field[1:] if isinstance(first_field, str) and first_field.startswith("$") else first_field
                            res_item[out_field] = group_docs[0].get(f_name) if group_docs else None
                        elif "$last" in accum:
                            last_field = accum["$last"]
                            f_name = last_field[1:] if isinstance(last_field, str) and last_field.startswith("$") else last_field
                            res_item[out_field] = group_docs[-1].get(f_name) if group_docs else None
                        elif "$max" in accum:
                            max_field = accum["$max"]
                            f_name = max_field[1:] if isinstance(max_field, str) and max_field.startswith("$") else max_field
                            vals = [gd.get(f_name) for gd in group_docs if gd.get(f_name) is not None]
                            res_item[out_field] = max(vals) if vals else None
                        elif "$min" in accum:
                            min_field = accum["$min"]
                            f_name = min_field[1:] if isinstance(min_field, str) and min_field.startswith("$") else min_field
                            vals = [gd.get(f_name) for gd in group_docs if gd.get(f_name) is not None]
                            res_item[out_field] = min(vals) if vals else None

                    aggregated.append(res_item)
                current_docs = aggregated

        return MockCursor(current_docs)


class MockDatabase:
    def __init__(self):
        self.collections: Dict[str, MockCollection] = {}

    def __getattr__(self, name: str) -> MockCollection:
        if name not in self.collections:
            self.collections[name] = MockCollection(name)
        return self.collections[name]

    def __getitem__(self, name: str) -> MockCollection:
        return self.__getattr__(name)
