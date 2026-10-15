"""Employee Attendance & Analytics API (HROne assignment)."""
import os
import re
from calendar import monthrange
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Literal

import certifi
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, OperationFailure
from bson import json_util
import json

load_dotenv()
IST = timezone(timedelta(hours=5, minutes=30))
PRESENT = {"PRESENT", "WFH", "ON_DUTY"}
ABSENT = {"ABSENT", "LEAVE"}
STATUSES = PRESENT | ABSENT
MIN_EPOCH_MS = 100_000_000_000
MAX_EPOCH_MS = 4_102_444_800_000
client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"), tlsCAFile=certifi.where(), serverSelectionTimeoutMS=5000)
db = client[os.getenv("MONGO_DB", "attendance_db")]
app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0")


def round_num(v, places=2):
    if v is None:
        return None
    return float(Decimal(str(v)).quantize(Decimal("1").scaleb(-places), rounding=ROUND_HALF_UP))


def parse_day(s: str) -> date:
    try:
        d = date.fromisoformat(s)
        if d.isoformat() != s:
            raise ValueError
        return d
    except (TypeError, ValueError):
        raise HTTPException(422, "Invalid date; expected YYYY-MM-DD")


def parse_month(s: str) -> tuple[date, date]:
    if not isinstance(s, str) or not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", s):
        raise HTTPException(422, "Invalid month; expected YYYY-MM")
    y, m = map(int, s.split("-"))
    return date(y, m, 1), date(y, m, monthrange(y, m)[1])


def parse_shift(s: str) -> time:
    if not isinstance(s, str) or not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", s):
        raise HTTPException(422, "Invalid shift time; expected HH:MM")
    h, m = map(int, s.split(":"))
    return time(h, m)


def now_ist() -> datetime:
    return datetime.now(IST).replace(microsecond=0)


def validate_epoch(v):
    if type(v) is not int or not MIN_EPOCH_MS <= v <= MAX_EPOCH_MS:
        raise ValueError("must be an integer epoch-millisecond timestamp in the supported range")
    return v


def from_ms(v: int) -> datetime:
    validate_epoch(v)
    return datetime.fromtimestamp(v / 1000, timezone.utc).replace(microsecond=0)


def to_ms(v: Optional[datetime]):
    if v is None:
        return None
    if v.tzinfo is None:
        v = v.replace(tzinfo=timezone.utc)  # PyMongo stores UTC and returns naive UTC datetimes.
    return int(v.timestamp() * 1000)


def as_utc(v: datetime) -> datetime:
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


def ist_dt(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=IST)


def attendance_day(punch_in: datetime, shift_start: str, shift_end: str) -> str:
    local = as_utc(punch_in).astimezone(IST)
    start, end = parse_shift(shift_start), parse_shift(shift_end)
    d = local.date()
    if end <= start and local.time().replace(tzinfo=None) < end:
        d -= timedelta(days=1)
    return d.isoformat()


def shift_start_dt(day: str, shift_start: str, shift_end: str, punch_in: datetime) -> datetime:
    d = parse_day(day)
    local = as_utc(punch_in).astimezone(IST)
    start, end = parse_shift(shift_start), parse_shift(shift_end)
    if end <= start and local.date() > d:
        d += timedelta(days=0)
    return ist_dt(d, start)


def calc_late(punch_in: Optional[datetime], shift_start: str, shift_end: str, day: str) -> int:
    if punch_in is None:
        return 0
    d = parse_day(day)
    local = as_utc(punch_in).astimezone(IST)
    start, end = parse_shift(shift_start), parse_shift(shift_end)
    start_day = d
    if end <= start and local.date() < d:
        start_day = d - timedelta(days=1)
    delta = (local - ist_dt(start_day, start)).total_seconds()
    minutes = int(delta // 60)
    return minutes if delta > 600 else 0


def calc_work(punch_in: Optional[datetime], punch_out: Optional[datetime]):
    if punch_in is None or punch_out is None:
        return None
    return round_num((as_utc(punch_out) - as_utc(punch_in)).total_seconds() / 3600, 2)


def calc_overtime(punch_out: Optional[datetime], shift_start: str, shift_end: str, day: str) -> int:
    if punch_out is None:
        return 0
    d = parse_day(day)
    start, end = parse_shift(shift_start), parse_shift(shift_end)
    if end <= start:
        d += timedelta(days=1)
    delta = (as_utc(punch_out) - ist_dt(d, end).astimezone(timezone.utc)).total_seconds()
    mins = int(delta // 60)
    return mins if mins >= 30 else 0


def calc_derived(doc: dict, emp: dict):
    pi, po = doc.get("punch_in"), doc.get("punch_out")
    doc["late_minutes"] = calc_late(pi, emp["shift_start"], emp["shift_end"], doc["date"])
    doc["work_hours"] = calc_work(pi, po)
    doc["overtime_minutes"] = calc_overtime(po, emp["shift_start"], emp["shift_end"], doc["date"])
    doc["half_day"] = bool(doc["work_hours"] is not None and doc["work_hours"] < 4.50)
    return doc


def public_attendance(d):
    return {"emp_code": d["emp_code"], "date": d["date"], "status": d["status"],
            "punch_in": to_ms(d.get("punch_in")), "punch_out": to_ms(d.get("punch_out")),
            "work_hours": d.get("work_hours"), "late_minutes": int(d.get("late_minutes", 0) or 0),
            "overtime_minutes": int(d.get("overtime_minutes", 0) or 0), "half_day": bool(d.get("half_day", False)),
            "history": [{**h, "at": to_ms(h.get("at")), "changes": {
                k: {"from": to_ms(v.get("from")) if k in ("punch_in", "punch_out") else v.get("from"),
                    "to": to_ms(v.get("to")) if k in ("punch_in", "punch_out") else v.get("to")}
                for k, v in h.get("changes", {}).items()}} for h in d.get("history", [])]}


def public_employee(d):
    return {k: (to_ms(v) if k == "created_at" else v) for k, v in d.items() if k != "_id"}


def require_pagination(page, page_size):
    if page < 1 or page_size < 1 or page_size > 100:
        raise HTTPException(422, "page must be >= 1 and page_size must be between 1 and 100")


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str = Field(pattern=r"^EMP\d{4,6}$")
    name: str = Field(min_length=1, max_length=100)
    email: str = Field(max_length=120, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    department: str = Field(min_length=1, max_length=50)
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str

    @field_validator("joined_on")
    @classmethod
    def valid_joined(cls, v):
        parse_day(v); return v

    @field_validator("shift_start", "shift_end")
    @classmethod
    def valid_shift(cls, v):
        parse_shift(v); return v

    @model_validator(mode="after")
    def distinct_shifts(self):
        if self.shift_start == self.shift_end:
            raise ValueError("shift_start and shift_end must differ")
        return self


class PunchInIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None
    status: Literal["PRESENT", "WFH", "ON_DUTY"] = "PRESENT"

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None: validate_epoch(v)
        return v


class PunchOutIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    emp_code: str
    punched_at: Optional[int] = None

    @field_validator("punched_at")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None: validate_epoch(v)
        return v


class RegularizeIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str = Field(min_length=5, max_length=200)
    regularized_by: str = Field(min_length=1, max_length=50)

    @field_validator("punch_in", "punch_out")
    @classmethod
    def valid_timestamp(cls, v):
        if v is not None: validate_epoch(v)
        return v


@app.on_event("startup")
def create_indexes():
    db.employees.create_index([("emp_code", ASCENDING)], unique=True, name="uq_employee_code")
    db.employees.create_index([("department", ASCENDING), ("joined_on", ASCENDING)], name="department_joined")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING)], unique=True, name="uq_attendance_employee_date")
    db.attendance_logs.create_index([("date", DESCENDING), ("emp_code", ASCENDING)], name="attendance_date_employee")
    db.attendance_logs.create_index([("emp_code", ASCENDING), ("date", ASCENDING), ("status", ASCENDING)], name="attendance_employee_date_status")
    db.attendance_logs.create_index([("date", ASCENDING), ("status", ASCENDING), ("late_minutes", DESCENDING)], name="attendance_month_status_late")
    db.attendance_logs.create_index([("punch_in", DESCENDING)], name="attendance_punch_in")


@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "MongoDB unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = as_utc(now_ist())
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    return public_employee(doc)


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    require_pagination(page, page_size)
    q = {"department": department} if department is not None else {}
    total = db.employees.count_documents(q)
    items = [public_employee(x) for x in db.employees.find(q, {"_id": 0}).sort("emp_code", ASCENDING).skip((page-1)*page_size).limit(page_size)]
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    pi = as_utc(from_ms(body.punched_at)) if body.punched_at is not None else as_utc(now_ist())
    day = attendance_day(pi, emp["shift_start"], emp["shift_end"])
    doc = {"emp_code": body.emp_code, "date": day, "status": body.status, "punch_in": pi, "punch_out": None,
           "work_hours": None, "late_minutes": 0, "overtime_minutes": 0, "half_day": False, "history": []}
    calc_derived(doc, emp)
    try:
        db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "Already punched in for this attendance date")
    return public_attendance(doc)


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
    po = as_utc(from_ms(body.punched_at)) if body.punched_at is not None else as_utc(now_ist())
    rec = db.attendance_logs.find_one({"emp_code": body.emp_code, "punch_in": {"$ne": None, "$lte": po}}, sort=[("punch_in", DESCENDING)])
    if not rec:
        raise HTTPException(404, "No punch-in found")
    if rec.get("punch_out") is not None:
        raise HTTPException(409, "Already punched out")
    pi = as_utc(rec["punch_in"])
    if po <= pi or po > pi + timedelta(hours=24):
        raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    updated = dict(rec); updated["punch_out"] = po; calc_derived(updated, emp)
    result = db.attendance_logs.update_one({"_id": rec["_id"], "punch_out": None}, {"$set": {"punch_out": po, "work_hours": updated["work_hours"], "overtime_minutes": updated["overtime_minutes"], "half_day": updated["half_day"]}})
    if result.modified_count != 1:
        raise HTTPException(409, "Already punched out")
    rec.update({"punch_out": po, "work_hours": updated["work_hours"], "overtime_minutes": updated["overtime_minutes"], "half_day": updated["half_day"]})
    return public_attendance(rec)


@app.get("/attendance")
def list_attendance(emp_code: Optional[str] = None, date_from: Optional[str] = None, date_to: Optional[str] = None,
                   status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
                   page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    require_pagination(page, page_size)
    q = {}
    if emp_code: q["emp_code"] = emp_code
    if status: q["status"] = status
    if date_from:
        parse_day(date_from); q.setdefault("date", {})["$gte"] = date_from
    if date_to:
        parse_day(date_to); q.setdefault("date", {})["$lte"] = date_to
    if date_from and date_to and date_from > date_to:
        raise HTTPException(422, "date_from must be on or before date_to")
    total = db.attendance_logs.count_documents(q)
    docs = db.attendance_logs.find(q, {"_id": 0}).sort([("date", DESCENDING), ("emp_code", ASCENDING)]).skip((page-1)*page_size).limit(page_size)
    return {"items": [public_attendance(x) for x in docs], "total": total, "page": page, "page_size": page_size}


@app.patch("/attendance/{emp_code}/{day}")
def regularize(emp_code: str, day: str, body: RegularizeIn):
    parse_day(day)
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp: raise HTTPException(404, "Employee not found")
    rec = db.attendance_logs.find_one({"emp_code": emp_code, "date": day})
    if not rec: raise HTTPException(404, "Attendance record not found")
    fields = body.model_fields_set - {"reason", "regularized_by"}
    if not fields: raise HTTPException(422, "No attendance fields supplied")
    if body.status in ABSENT and ("punch_in" in fields or "punch_out" in fields):
        raise HTTPException(422, "ABSENT and LEAVE cannot include punch times")
    new = dict(rec)
    if body.status is not None: new["status"] = body.status
    for key in ("punch_in", "punch_out"):
        if key in fields: new[key] = as_utc(from_ms(getattr(body, key))) if getattr(body, key) is not None else None
    if new["status"] in ABSENT:
        new["punch_in"] = None; new["punch_out"] = None
    elif new.get("punch_in") is None:
        raise HTTPException(422, "Presence statuses require punch_in")
    if new.get("punch_in") is not None:
        if attendance_day(new["punch_in"], emp["shift_start"], emp["shift_end"]) != day:
            raise HTTPException(422, "punch_in must belong to the record attendance date")
    if new.get("punch_out") is not None:
        if (
    new.get("punch_in") is None
    or as_utc(new["punch_out"]) <= as_utc(new["punch_in"])
    or as_utc(new["punch_out"]) > as_utc(new["punch_in"]) + timedelta(hours=24)
):
            raise HTTPException(422, "punch_out must be after punch_in and within 24 hours")
    calc_derived(new, emp)
    changed = {}
    for key in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day"):
        oldv = rec.get(key, False if key == "half_day" else 0 if key in ("late_minutes", "overtime_minutes") else None)
        newv = new.get(key, False if key == "half_day" else 0 if key in ("late_minutes", "overtime_minutes") else None)
        if oldv != newv:
            changed[key] = {"from": oldv, "to": newv}
    if not changed: raise HTTPException(422, "The correction does not change the record")
    entry = {"at": as_utc(now_ist()), "by": body.regularized_by, "reason": body.reason, "changes": changed}
    old_history = rec.get("history", [])
    result = db.attendance_logs.update_one({"_id": rec["_id"], "history": old_history}, {"$set": {k: new.get(k) for k in ("status", "punch_in", "punch_out", "work_hours", "late_minutes", "overtime_minutes", "half_day")}, "$push": {"history": entry}})
    if result.modified_count != 1: raise HTTPException(409, "Concurrent correction; retry")
    new["history"] = old_history + [entry]
    return public_attendance(new)


def weekdays_between(start: date, end: date) -> int:
    n = 0
    cur = start
    while cur <= end:
        if cur.weekday() < 5: n += 1
        cur += timedelta(days=1)
    return n


def month_bounds(month):
    start, end = parse_month(month)
    return start.isoformat(), end.isoformat(), start, end


@app.get("/analytics/employees/{emp_code}/monthly")
def employee_monthly(emp_code: str, month: str):
    start_s, end_s, start, end = month_bounds(month)
    emp = db.employees.find_one({"emp_code": emp_code}, {"_id": 0})
    if not emp: raise HTTPException(404, "Employee not found")
    work_start = max(start, parse_day(emp["joined_on"]))
    working = weekdays_between(work_start, end) if work_start <= end else 0
    pipeline = [{"$match": {"emp_code": emp_code, "date": {"$gte": start_s, "$lte": end_s}}},
      {"$group": {"_id": None,
        "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$status", list(PRESENT)]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 6]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$date"}}}, 2]}]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}},
        "leave_days": {"$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}},
        "total_overtime_minutes": {"$sum": {"$ifNull": ["$overtime_minutes", 0]}}}}]
    row = next(iter(db.attendance_logs.aggregate(pipeline)), {})
    present = round_num(row.get("present_days", 0), 2)
    return {"emp_code": emp_code, "month": month, "working_days": working, "present_days": present,
            "leave_days": row.get("leave_days", 0), "late_count": row.get("late_count", 0),
            "total_late_minutes": row.get("total_late_minutes", 0), "total_overtime_minutes": row.get("total_overtime_minutes", 0),
            "attendance_pct": round_num(present / working * 100, 2) if working else None}


@app.get("/analytics/departments/summary")
def department_summary(month: str, department: Optional[str] = None):
    start_s, end_s, start, end = month_bounds(month)
    match_emp = {"joined_on": {"$lte": end_s}}
    if department is not None: match_emp["department"] = department
    pipeline = [
      {"$match": match_emp},
      {"$lookup": {"from": "attendance_logs", "let": {"code": "$emp_code"}, "pipeline": [
        {"$match": {"$expr": {"$and": [{"$eq": ["$emp_code", "$$code"]}, {"$gte": ["$date", start_s]}, {"$lte": ["$date", end_s]}]}}}], "as": "logs"}},
      {"$unwind": {"path": "$logs", "preserveNullAndEmptyArrays": True}},
      {"$group": {"_id": "$department", "headcount": {"$sum": {"$cond": [{"$or": [{"$eq": ["$logs", None]}, {"$eq": ["$logs", {}]}]}, 1, 0]}},
        "present_days": {"$sum": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT)]}, {"$lte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 6]}, {"$gte": [{"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date"}}}, 2]}]}, {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]}, 0]}},
        "work_hours_values": {"$push": {"$cond": [{"$and": [{"$in": ["$logs.status", list(PRESENT)]}, {"$ne": ["$logs.work_hours", None]}]}, "$logs.work_hours", "$$REMOVE"]}},
        "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}},
        "total_late_minutes": {"$sum": {"$ifNull": ["$logs.late_minutes", 0]}},
        "leave_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}},
        "on_duty_count": {"$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}}}},
      {"$project": {"_id": 0, "department": "$_id", "headcount": 1, "present_days": {"$round": ["$present_days", 2]},
        "avg_work_hours": {"$cond": [{"$gt": [{"$size": "$work_hours_values"}, 0]}, {"$round": [{"$avg": "$work_hours_values"}, 2]}, None]},
        "late_count": 1, "total_late_minutes": 1, "leave_count": 1, "on_duty_count": 1}}, {"$sort": {"department": 1}}]
    # Grouped headcount needs distinct employees, including employees with no logs; use employee-level count separately.
    rows = list(db.employees.aggregate(pipeline))
    # Replace headcount with exact employees joined by period end, including those with no attendance.
    for row in rows:
        row["headcount"] = db.employees.count_documents({"department": row["department"], "joined_on": {"$lte": end_s}})
    return {"month": month, "items": [r for r in rows if r["headcount"] > 0]}


@app.get("/analytics/leaderboard/late")
def late_leaderboard(month: str, limit: int = Query(10, ge=1, le=50), department: Optional[str] = None):
    start_s, end_s, _, _ = month_bounds(month)
    pipeline = [{"$match": {"date": {"$gte": start_s, "$lte": end_s}, "late_minutes": {"$gt": 0}}},
      {"$group": {"_id": "$emp_code", "total_late_minutes": {"$sum": {"$ifNull": ["$late_minutes", 0]}}, "late_count": {"$sum": 1}}},
      {"$match": {"total_late_minutes": {"$gt": 0}}},
      {"$lookup": {"from": "employees", "localField": "_id", "foreignField": "emp_code", "as": "employee"}}, {"$unwind": "$employee"}]
    if department is not None: pipeline.append({"$match": {"employee.department": department}})
    pipeline += [{"$setWindowFields": {"sortBy": {"total_late_minutes": -1}, "output": {"rank": {"$rank": {}}}}},
      {"$match": {"rank": {"$lte": limit}}}, {"$sort": {"total_late_minutes": -1, "_id": 1}},
      {"$project": {"_id": 0, "rank": 1, "emp_code": "$_id", "name": "$employee.name", "department": "$employee.department", "total_late_minutes": 1, "late_count": 1}}]
    return {"month": month, "items": list(db.attendance_logs.aggregate(pipeline))}


# @app.get("/analytics/departments/{department}/trend")
# def department_trend(department: str, from_day: str = Query(..., alias="from"), to_day: str = Query(..., alias="to")):
#     if not from_day or not to_day: raise HTTPException(422, "from and to are required")
#     start, end = parse_day(from_day), parse_day(to_day)
#     if end < start or (end - start).days + 1 > 92: raise HTTPException(422, "Invalid date range; maximum is 92 days")
#     if not db.employees.find_one({"department": department}): raise HTTPException(404, "Department not found")
#     pipeline = [
#     {"$match": {"date": {"$gte": from_day, "$lte": to_day}}},
#     {"$lookup": {"from": "employees", "localField": "emp_code", "foreignField": "emp_code", "as": "employee"}}, {"$unwind": "$employee"},
#     {"$match": {"employee.department": department}},
#     {"$group": {"_id": "$date", "present_count": {"$sum": {"$cond": [{"$in": ["$status", list(PRESENT)]}, {"$cond": [{"$eq": ["$half_day", True]}, 0.5, 1]}, 0]}}, "late_count": {"$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}}}},
#     {"$project": {"_id": 0, "date": {"$dateFromString": {"dateString": "$_id"}}, "present_count": 1, "late_count": 1}},
#     {"$densify": {"field": "date", "range": {"step": 1, "unit": "day", "bounds": [datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)]}}},
#     {"$set": {"date_s": {"$dateToString": {"date": "$date", "format": "%Y-%m-%d"}}, "present_count": {"$ifNull": ["$present_count", 0]}, "late_count": {"$ifNull": ["$late_count", 0]}}},
#     {"$lookup": {"from": "employees", "let": {"day": "$date_s"}, "pipeline": [{"$match": {"department": department}}, {"$match": {"$expr": {"$lte": ["$joined_on", "$$day"]}}}, {"$count": "n"}], "as": "hc"}},
#     {"$set": {"headcount": {"$ifNull": [{"$arrayElemAt": ["$hc.n", 0]}, 0]}, "is_working_day": {"$and": [{"$gte": [{"$dayOfWeek": "$date"}, 2]}, {"$lte": [{"$dayOfWeek": "$date"}, 6]}]}}},
#     {"$set": {"attendance_rate": {"$cond": [{"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]}, {"$round": [{"$divide": ["$present_count", "$headcount"]}, 4]}, None]}}},
#     {"$setWindowFields": {"sortBy": {"date": 1}, "output": {"moving_avg_7d": {"$avg": "$attendance_rate", "window": {"documents": [-6, "current"]}}}}},
#     {"$project": {"_id": 0, "date": "$date_s", "is_working_day": 1, "headcount": 1, "present_count": 1, "late_count": 1, "attendance_rate": 1, "moving_avg_7d": {"$round": ["$moving_avg_7d", 4]}}}, {"$sort": {"date": 1}}]
    
#     items = list(db.attendance_logs.aggregate(pipeline))

#     expected_dates = {
#     (start + timedelta(days=i)).isoformat()
#     for i in range((end - start).days + 1)
#     }
#     existing_dates = {item["date"] for item in items}
#     if existing_dates != expected_dates:
#         raise HTTPException(500, "Trend pipeline did not generate every requested date")
#     # For an empty range with no logs, densify has no input rows; seed a calendar document and still use DB pipeline is required.
#     return {"department": department, "items": items}

@app.get("/analytics/departments/{department}/trend")
def department_trend(
    department: str,
    from_day: str = Query(..., alias="from"),
    to_day: str = Query(..., alias="to"),
):
    start, end = parse_day(from_day), parse_day(to_day)

    if end < start or (end - start).days + 1 > 92:
        raise HTTPException(422, "Invalid date range; maximum is 92 days")

    if not db.employees.find_one({"department": department}):
        raise HTTPException(404, "Department not found")

    pipeline = [
        {"$match": {"date": {"$gte": from_day, "$lte": to_day}}},
        {
            "$lookup": {
                "from": "employees",
                "localField": "emp_code",
                "foreignField": "emp_code",
                "as": "employee",
            }
        },
        {"$unwind": "$employee"},
        {"$match": {"employee.department": department}},
        {
            "$group": {
                "_id": "$date",
                "present_count": {
                    "$sum": {
                        "$cond": [
                            {"$in": ["$status", list(PRESENT)]},
                            {
                                "$cond": [
                                    {"$eq": ["$half_day", True]},
                                    0.5,
                                    1,
                                ]
                            },
                            0,
                        ]
                    }
                },
                "late_count": {
                    "$sum": {
                        "$cond": [
                            {"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]},
                            1,
                            0,
                        ]
                    }
                },
            }
        },
    ]

    daily_records = {
        item["_id"]: item
        for item in db.attendance_logs.aggregate(pipeline)
    }

    employees = list(
        db.employees.find(
            {"department": department},
            {"_id": 0, "joined_on": 1},
        )
    )

    items = []

    current = start
    while current <= end:
        day = current.isoformat()
        weekday = current.weekday() < 5

        headcount = sum(
            1 for employee in employees
            if employee.get("joined_on", "9999-12-31") <= day
        )

        record = daily_records.get(day, {})
        present_count = record.get("present_count", 0)
        late_count = record.get("late_count", 0)

        attendance_rate = (
            round(present_count / headcount, 4)
            if weekday and headcount > 0
            else None
        )

        items.append({
            "date": day,
            "is_working_day": weekday,
            "headcount": headcount,
            "present_count": present_count,
            "late_count": late_count,
            "attendance_rate": attendance_rate,
            "moving_avg_7d": None,
        })

        current += timedelta(days=1)

    for i, item in enumerate(items):
        rates = [
            row["attendance_rate"]
            for row in items[max(0, i - 6):i + 1]
            if row["attendance_rate"] is not None
        ]

        item["moving_avg_7d"] = (
            round(sum(rates) / len(rates), 4)
            if rates
            else None
        )

    return {"department": department, "items": items}


def json_safe(v):
    return json.loads(json_util.dumps(v))


@app.get("/admin/explain/{endpoint}")
def explain_endpoint(endpoint: Literal["attendance_list", "employee_monthly", "department_summary", "late_leaderboard", "department_trend"],
                     emp_code: Optional[str] = None, month: Optional[str] = None, department: Optional[str] = None,
                     limit: int = Query(10, ge=1, le=50), date_from: Optional[str] = None, date_to: Optional[str] = None,
                     status: Optional[Literal["PRESENT", "ABSENT", "LEAVE", "WFH", "ON_DUTY"]] = None,
                     page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), from_: Optional[str] = Query(None, alias="from"), to: Optional[str] = None):
    require_pagination(page, page_size)
    if endpoint == "attendance_list":
        q = {}
        if emp_code: q["emp_code"] = emp_code
        if status: q["status"] = status
        if date_from: parse_day(date_from); q.setdefault("date", {})["$gte"] = date_from
        if date_to: parse_day(date_to); q.setdefault("date", {})["$lte"] = date_to
        if date_from and date_to and date_from > date_to: raise HTTPException(422, "date_from must be <= date_to")
        exp = db.command("explain", {"find": "attendance_logs", "filter": q, "sort": {"date": -1, "emp_code": 1}, "skip": (page-1)*page_size, "limit": page_size}, verbosity="executionStats")
        coll = "attendance_logs"
    elif endpoint == "employee_monthly":
        if not emp_code or not month: raise HTTPException(422, "emp_code and month are required")
        start_s, end_s, _, _ = month_bounds(month)
        pipeline = [{"$match": {"emp_code": emp_code, "date": {"$gte": start_s, "$lte": end_s}}}, {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}}]
        exp = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats"); coll = "attendance_logs"
    elif endpoint == "department_summary":
        if not month: raise HTTPException(422, "month is required")
        start_s, end_s, _, _ = month_bounds(month)
        pipeline = [{"$match": {"date": {"$gte": start_s, "$lte": end_s}}}, {"$group": {"_id": "$emp_code", "n": {"$sum": 1}}}]
        exp = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats"); coll = "attendance_logs"
    elif endpoint == "late_leaderboard":
        if not month: raise HTTPException(422, "month is required")
        start_s, end_s, _, _ = month_bounds(month)
        pipeline = [{"$match": {"date": {"$gte": start_s, "$lte": end_s}, "late_minutes": {"$gt": 0}}}, {"$group": {"_id": "$emp_code", "total": {"$sum": "$late_minutes"}}}]
        exp = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats"); coll = "attendance_logs"
    else:
        if not department or not from_ or not to: raise HTTPException(422, "department, from and to are required")
        start, end = parse_day(from_), parse_day(to)
        if end < start or (end-start).days+1 > 92: raise HTTPException(422, "Invalid date range")
        pipeline = [{"$match": {"date": {"$gte": from_, "$lte": to}}}, {"$group": {"_id": "$date", "n": {"$sum": 1}}}]
        exp = db.command("explain", {"aggregate": "attendance_logs", "pipeline": pipeline, "cursor": {}}, verbosity="executionStats"); coll = "attendance_logs"
    return {"endpoint": endpoint, "collection": coll, "explain": json_safe(exp)}
