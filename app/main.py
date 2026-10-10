"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
import calendar
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError
from contextlib import asynccontextmanager

load_dotenv()

client = MongoClient(os.getenv("MONGO_URI", "mongodb://localhost:27017"))
db = client[os.getenv("MONGO_DB", "attendance_db")]

IST = timezone(timedelta(hours=5, minutes=30))

@asynccontextmanager
async def lifespan(app: FastAPI):
    db.employees.create_index("emp_code", unique=True)
    db.attendance_logs.create_index([("emp_code", 1), ("date", 1)], unique=True)
    db.attendance_logs.create_index("date")
    db.attendance_logs.create_index("emp_code")
    yield

app = FastAPI(title="Employee Attendance & Analytics API", version="2.0.0", lifespan=lifespan)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def get_month_bounds(month_str: str):
    y, m = map(int, month_str.split("-"))
    start_dt = datetime(y, m, 1)
    _, last_day = calendar.monthrange(y, m)
    end_dt = datetime(y, m, last_day, 23, 59, 59)
    return start_dt, end_dt

def get_working_days(joined_on: str, month_str: str) -> int:
    y, m = map(int, month_str.split("-"))
    start_date = datetime(y, m, 1).date()
    _, last_day = calendar.monthrange(y, m)
    end_date = datetime(y, m, last_day).date()
    
    joined = datetime.fromisoformat(joined_on).date()
    start_date = max(start_date, joined)
    
    if start_date > end_date:
        return 0
        
    days = 0
    curr = start_date
    while curr <= end_date:
        if curr.weekday() < 5:
            days += 1
        curr += timedelta(days=1)
    return days

def r8_round(value, decimals=2):
    if value is None:
        return None
    return float(Decimal(str(value)).quantize(Decimal("1." + "0"*decimals), rounding=ROUND_HALF_UP))
def get_shift_times(date_str: str, shift_start: str, shift_end: str):
    h_start, m_start = map(int, shift_start.split(":"))
    h_end, m_end = map(int, shift_end.split(":"))
    
    start_dt = datetime.fromisoformat(date_str).replace(hour=h_start, minute=m_start, tzinfo=IST)
    end_dt = datetime.fromisoformat(date_str).replace(hour=h_end, minute=m_end, tzinfo=IST)
    
    if shift_end <= shift_start:
        end_dt += timedelta(days=1)
        
    return start_dt, end_dt

def compute_late_minutes(punch_in: datetime, shift_start_dt: datetime) -> int:
    diff_seconds = (punch_in - shift_start_dt).total_seconds()
    if diff_seconds > 600:
        return int(diff_seconds // 60)
    return 0

def compute_work_hours(punch_in: datetime, punch_out: datetime) -> float:
    hours = (punch_out - punch_in).total_seconds() / 3600
    return float(Decimal(str(hours)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def compute_overtime(punch_out: datetime, shift_end_dt: datetime) -> int:
    diff_seconds = (punch_out - shift_end_dt).total_seconds()
    minutes = int(diff_seconds // 60)
    return minutes if minutes >= 30 else 0


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
class EmployeeIn(BaseModel):
    emp_code: str
    name: str
    email: str
    department: str
    shift_start: str = "09:30"
    shift_end: str = "18:30"
    joined_on: str


class PunchInIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None
    status: str = "PRESENT"


class PunchOutIn(BaseModel):
    emp_code: str
    punched_at: Optional[int] = None


class RegularizeIn(BaseModel):
    status: Optional[str] = None
    punch_in: Optional[int] = None
    punch_out: Optional[int] = None
    reason: str
    regularized_by: str


# --------------------------------------------------------------------------- #
# Endpoints provided
# --------------------------------------------------------------------------- #
@app.get("/health")
def health():
    try:
        db.command("ping")
        return {"status": "ok"}
    except Exception:
        raise HTTPException(503, "Database unavailable")


@app.post("/employees", status_code=201)
def create_employee(body: EmployeeIn):
    doc = body.model_dump()
    doc["created_at"] = datetime.now(timezone.utc)
    try:
        db.employees.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "emp_code already exists")
    doc.pop("_id", None)
    return doc


@app.get("/employees")
def list_employees(department: Optional[str] = None, page: int = 1, page_size: int = 20):
    q = {}
    if department:
        q["department"] = department
    skip = (page - 1) * page_size
    total = db.employees.count_documents(q)
    items = list(db.employees.find(q, {"_id": 0}).sort("emp_code", 1).skip(skip).limit(page_size))
    return {"items": items, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-in", status_code=201)
def punch_in(body: PunchInIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc) if body.punched_at else datetime.now(timezone.utc)
    ts_ist = ts.astimezone(IST)
    
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    
    current_date = ts_ist.date()
    if shift_end <= shift_start:
        h_end, m_end = map(int, shift_end.split(":"))
        if ts_ist.time() <= datetime.min.replace(hour=h_end, minute=m_end).time():
            current_date -= timedelta(days=1)
            
    d = current_date.isoformat()
    start_dt, _ = get_shift_times(d, shift_start, shift_end)
    
    doc = {
        "emp_code": body.emp_code,
        "date": d,
        "status": body.status,
        "punch_in": ts,
        "punch_out": None,
        "work_hours": None,
        "late_minutes": compute_late_minutes(ts, start_dt),
        "overtime_minutes": 0,
        "half_day": False,
        "history": [],
    }
    try:
        res = db.attendance_logs.insert_one(doc)
    except DuplicateKeyError:
        raise HTTPException(409, "already punched in for this date")
        
    doc["id"] = str(res.inserted_id)
    doc.pop("_id", None)
    return doc


@app.get("/attendance")
def list_attendance(
    emp_code: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
):
    q = {}
    if emp_code:
        q["emp_code"] = emp_code
    if date_from or date_to:
        q["date"] = {}
        if date_from:
            q["date"]["$gte"] = date_from
        if date_to:
            q["date"]["$lte"] = date_to
    if status:
        q["status"] = status
        
    skip = (page - 1) * page_size
    total = db.attendance_logs.count_documents(q)
    page_docs = list(db.attendance_logs.find(q).sort("date", -1).skip(skip).limit(page_size))
    
    for d in page_docs:
        d["id"] = str(d.pop("_id"))
    return {"items": page_docs, "total": total, "page": page, "page_size": page_size}


@app.post("/attendance/punch-out")
def punch_out(body: PunchOutIn):
    emp = db.employees.find_one({"emp_code": body.emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    ts = datetime.fromtimestamp(body.punched_at / 1000, tz=timezone.utc) if body.punched_at else datetime.now(timezone.utc)
    
    q = {
        "emp_code": body.emp_code,
        "punch_in": {"$lte": ts}
    }
    record = db.attendance_logs.find_one(q, sort=[("punch_in", -1)])
    if not record:
        raise HTTPException(404, "No punch-in found")
        
    if ts <= record["punch_in"]:
        raise HTTPException(422, "punched_at must be > punch_in")
    if (ts - record["punch_in"]).total_seconds() > 86400:
        raise HTTPException(422, "punched_at must be within 24 hours of punch_in")

    d = record["date"]
    shift_start = emp["shift_start"]
    shift_end = emp["shift_end"]
    
    start_dt, end_dt = get_shift_times(d, shift_start, shift_end)
    
    work_hours = compute_work_hours(record["punch_in"], ts)
    overtime = compute_overtime(ts, end_dt)
    half_day = work_hours < 4.50

    update_doc = {
        "$set": {
            "punch_out": ts,
            "work_hours": work_hours,
            "overtime_minutes": overtime,
            "half_day": half_day
        }
    }
    
    res = db.attendance_logs.find_one_and_update(
        {"_id": record["_id"], "punch_out": None}, 
        update_doc,
        return_document=True
    )
    if not res:
        raise HTTPException(409, "Already punched out")
        
    res["id"] = str(res.pop("_id"))
    return res


@app.patch("/attendance/{emp_code}/{date}")
def regularize_attendance(emp_code: str, date: str, body: RegularizeIn):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")

    record = db.attendance_logs.find_one({"emp_code": emp_code, "date": date})
    if not record:
        raise HTTPException(404, "Record not found")

    changes = {}
    new_status = body.status if body.status else record["status"]
    
    new_in = record.get("punch_in")
    if body.punch_in is not None:
        new_in = datetime.fromtimestamp(body.punch_in / 1000, tz=timezone.utc)
        
    new_out = record.get("punch_out")
    if body.punch_out is not None:
        new_out = datetime.fromtimestamp(body.punch_out / 1000, tz=timezone.utc)

    if new_status in ["ABSENT", "LEAVE"]:
        if body.punch_in is not None or body.punch_out is not None:
            raise HTTPException(422, "Cannot supply punch times for ABSENT/LEAVE")
        new_in = None
        new_out = None
    else:
        if new_in is None:
            raise HTTPException(422, "Presence status requires a punch_in")
        
        ts_ist = new_in.astimezone(IST)
        shift_start = emp["shift_start"]
        shift_end = emp["shift_end"]
        current_date = ts_ist.date()
        if shift_end <= shift_start:
            h_end, m_end = map(int, shift_end.split(":"))
            if ts_ist.time() <= datetime.min.replace(hour=h_end, minute=m_end).time():
                current_date -= timedelta(days=1)
        
        if current_date.isoformat() != date:
            raise HTTPException(422, "punch_in must stay on the record's attendance date")

        if new_out:
            if new_out <= new_in:
                raise HTTPException(422, "punch_out must be after punch_in")
            if (new_out - new_in).total_seconds() > 86400:
                raise HTTPException(422, "punch_out must be within 24 hours")

    to_update = {}
    if new_status != record["status"]:
        changes["status"] = {"from": record["status"], "to": new_status}
        to_update["status"] = new_status
        
    if new_in != record.get("punch_in"):
        changes["punch_in"] = {
            "from": int(record["punch_in"].timestamp()*1000) if record.get("punch_in") else None,
            "to": int(new_in.timestamp()*1000) if new_in else None
        }
        to_update["punch_in"] = new_in
        
    if new_out != record.get("punch_out"):
        changes["punch_out"] = {
            "from": int(record["punch_out"].timestamp()*1000) if record.get("punch_out") else None,
            "to": int(new_out.timestamp()*1000) if new_out else None
        }
        to_update["punch_out"] = new_out

    if not changes:
        raise HTTPException(422, "No changes supplied")

    start_dt, end_dt = get_shift_times(date, emp["shift_start"], emp["shift_end"])
    
    new_late = 0
    new_work = None
    new_overtime = 0
    new_half_day = False
    
    if new_status not in ["ABSENT", "LEAVE"]:
        if new_in:
            new_late = compute_late_minutes(new_in, start_dt)
        if new_in and new_out:
            new_work = compute_work_hours(new_in, new_out)
            new_overtime = compute_overtime(new_out, end_dt)
            new_half_day = new_work < 4.50
            
    if new_late != record.get("late_minutes", 0):
        changes["late_minutes"] = {"from": record.get("late_minutes", 0), "to": new_late}
        to_update["late_minutes"] = new_late
        
    if new_work != record.get("work_hours"):
        changes["work_hours"] = {"from": record.get("work_hours"), "to": new_work}
        to_update["work_hours"] = new_work
        
    if new_overtime != record.get("overtime_minutes", 0):
        changes["overtime_minutes"] = {"from": record.get("overtime_minutes", 0), "to": new_overtime}
        to_update["overtime_minutes"] = new_overtime
        
    if new_half_day != record.get("half_day", False):
        changes["half_day"] = {"from": record.get("half_day", False), "to": new_half_day}
        to_update["half_day"] = new_half_day

    history_entry = {
        "at": datetime.now(timezone.utc),
        "by": body.regularized_by,
        "reason": body.reason,
        "changes": changes
    }
    
    current_history_len = len(record.get("history", []))
    res = db.attendance_logs.find_one_and_update(
        {
            "_id": record["_id"],
            f"history.{current_history_len}": {"$exists": False} 
        },
        {
            "$set": to_update,
            "$push": {"history": history_entry}
        },
        return_document=True
    )
    if not res:
        raise HTTPException(409, "Concurrent edit detected")
        
    res["id"] = str(res.pop("_id"))
    return res


# --------------------------------------------------------------------------- #
# Analytics Pipelines
# --------------------------------------------------------------------------- #
def get_employee_monthly_pipeline(emp_code: str, month: str):
    start_dt, end_dt = get_month_bounds(month)
    return [
        {"$match": {
            "emp_code": emp_code,
            "date": {"$gte": start_dt.strftime("%Y-%m-%d"), "$lte": end_dt.strftime("%Y-%m-%d")}
        }},
        {"$addFields": {
            "date_obj": {"$dateFromString": {"dateString": "$date"}},
        }},
        {"$addFields": {
            "day_of_week": {"$dayOfWeek": "$date_obj"}
        }},
        {"$group": {
            "_id": None,
            "present_days": {
                "$sum": {
                    "$cond": [
                        {"$and": [
                            {"$in": ["$status", ["PRESENT", "WFH", "ON_DUTY"]]},
                            {"$in": ["$day_of_week", [2, 3, 4, 5, 6]]}
                        ]},
                        {"$cond": ["$half_day", 0.5, 1]},
                        0
                    ]
                }
            },
            "leave_days": {
                "$sum": {"$cond": [{"$eq": ["$status", "LEAVE"]}, 1, 0]}
            },
            "late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$late_minutes", 0]}, 0]}, 1, 0]}
            },
            "total_late_minutes": {
                "$sum": {"$ifNull": ["$late_minutes", 0]}
            },
            "total_overtime_minutes": {
                "$sum": {"$ifNull": ["$overtime_minutes", 0]}
            }
        }}
    ]

def get_department_summary_pipeline(month: str, department: Optional[str] = None):
    start_dt, end_dt = get_month_bounds(month)
    month_start_str = start_dt.strftime("%Y-%m-%d")
    month_end_str = end_dt.strftime("%Y-%m-%d")
    
    match_stage = {"joined_on": {"$lte": month_end_str}}
    if department:
        match_stage["department"] = department
        
    return [
        {"$match": match_stage},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp_code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp_code"]},
                    "date": {"$gte": month_start_str, "$lte": month_end_str}
                }}
            ],
            "as": "logs"
        }},
        {"$unwind": {
            "path": "$logs",
            "preserveNullAndEmptyArrays": True
        }},
        {"$addFields": {
            "is_present": {"$in": ["$logs.status", ["PRESENT", "WFH", "ON_DUTY"]]},
            "day_of_week": {"$dayOfWeek": {"$dateFromString": {"dateString": "$logs.date", "onError": start_dt}}}
        }},
        {"$addFields": {
            "counts_present": {
                "$and": [
                    "$is_present",
                    {"$in": ["$day_of_week", [2,3,4,5,6]]}
                ]
            }
        }},
        {"$group": {
            "_id": {"dept": "$department", "emp": "$emp_code"},
            "emp_present_days": {
                "$sum": {
                    "$cond": [
                        "$counts_present",
                        {"$cond": [{"$eq": ["$logs.half_day", True]}, 0.5, 1]},
                        0
                    ]
                }
            },
            "emp_work_hours": {
                "$push": {
                    "$cond": [
                        {"$and": ["$is_present", {"$ne": ["$logs.work_hours", None]}]},
                        "$logs.work_hours",
                        "$$REMOVE"
                    ]
                }
            },
            "emp_late_count": {
                "$sum": {"$cond": [{"$gt": [{"$ifNull": ["$logs.late_minutes", 0]}, 0]}, 1, 0]}
            },
            "emp_late_minutes": {
                "$sum": {"$ifNull": ["$logs.late_minutes", 0]}
            },
            "emp_leave_count": {
                "$sum": {"$cond": [{"$eq": ["$logs.status", "LEAVE"]}, 1, 0]}
            },
            "emp_on_duty_count": {
                "$sum": {"$cond": [{"$eq": ["$logs.status", "ON_DUTY"]}, 1, 0]}
            }
        }},
        {"$group": {
            "_id": "$_id.dept",
            "headcount": {"$sum": 1},
            "present_days": {"$sum": "$emp_present_days"},
            "all_work_hours": {"$push": "$emp_work_hours"},
            "late_count": {"$sum": "$emp_late_count"},
            "total_late_minutes": {"$sum": "$emp_late_minutes"},
            "leave_count": {"$sum": "$emp_leave_count"},
            "on_duty_count": {"$sum": "$emp_on_duty_count"}
        }},
        {"$addFields": {
            "flat_hours": {
                "$reduce": {
                    "input": "$all_work_hours",
                    "initialValue": [],
                    "in": {"$concatArrays": ["$$value", "$$this"]}
                }
            }
        }},
        {"$addFields": {
            "avg_work_hours": {"$avg": "$flat_hours"}
        }},
        {"$sort": {"_id": 1}},
        {"$project": {
            "_id": 0,
            "department": "$_id",
            "headcount": 1,
            "present_days": 1,
            "avg_work_hours": 1,
            "late_count": 1,
            "total_late_minutes": 1,
            "leave_count": 1,
            "on_duty_count": 1
        }}
    ]

def get_late_leaderboard_pipeline(month: str, department: Optional[str] = None, limit: int = 10):
    start_dt, end_dt = get_month_bounds(month)
    month_start = start_dt.strftime("%Y-%m-%d")
    month_end = end_dt.strftime("%Y-%m-%d")
    
    match_emp = {}
    if department:
        match_emp["department"] = department
        
    return [
        {"$match": match_emp},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"emp_code": "$emp_code"},
            "pipeline": [
                {"$match": {
                    "$expr": {"$eq": ["$emp_code", "$$emp_code"]},
                    "date": {"$gte": month_start, "$lte": month_end},
                    "late_minutes": {"$gt": 0}
                }}
            ],
            "as": "logs"
        }},
        {"$match": {"logs.0": {"$exists": True}}},
        {"$unwind": "$logs"},
        {"$group": {
            "_id": "$emp_code",
            "name": {"$first": "$name"},
            "department": {"$first": "$department"},
            "total_late_minutes": {"$sum": "$logs.late_minutes"},
            "late_count": {"$sum": 1}
        }},
        {"$setWindowFields": {
            "sortBy": {"total_late_minutes": -1},
            "output": {
                "rank": {
                    "$rank": {} 
                }
            }
        }},
        {"$match": {"rank": {"$lte": limit}}},
        {"$sort": {"total_late_minutes": -1, "_id": 1}},
        {"$project": {
            "_id": 0,
            "rank": 1,
            "emp_code": "$_id",
            "name": 1,
            "department": 1,
            "total_late_minutes": 1,
            "late_count": 1
        }}
    ]

def get_department_trend_pipeline(department: str, date_from: str, date_to: str):
    dt_from = datetime.fromisoformat(date_from)
    dt_to = datetime.fromisoformat(date_to)
    
    return [
        {"$match": {"department": department}},
        {"$limit": 1},
        {"$project": {"_id": 0, "dummy": {"$literal": 1}}},
        {"$densify": {
            "field": "date_obj",
            "range": {
                "step": 1,
                "unit": "day",
                "bounds": [dt_from, dt_to + timedelta(days=1)]
            }
        }},
        {"$addFields": {
            "date": {"$dateToString": {"format": "%Y-%m-%d", "date": "$date_obj"}},
            "day_of_week": {"$dayOfWeek": "$date_obj"}
        }},
        {"$addFields": {
            "is_working_day": {"$in": ["$day_of_week", [2,3,4,5,6]]}
        }},
        {"$lookup": {
            "from": "employees",
            "let": {"d": "$date"},
            "pipeline": [
                {"$match": {
                    "department": department,
                    "$expr": {"$lte": ["$joined_on", "$$d"]}
                }}
            ],
            "as": "emps"
        }},
        {"$addFields": {
            "headcount": {"$size": "$emps"},
            "emp_codes": {
                "$map": {
                    "input": "$emps",
                    "as": "e",
                    "in": "$$e.emp_code"
                }
            }
        }},
        {"$lookup": {
            "from": "attendance_logs",
            "let": {"d": "$date", "codes": "$emp_codes"},
            "pipeline": [
                {"$match": {
                    "$expr": {
                        "$and": [
                            {"$eq": ["$date", "$$d"]},
                            {"$in": ["$emp_code", "$$codes"]}
                        ]
                    }
                }}
            ],
            "as": "logs"
        }},
        {"$addFields": {
            "present_count": {
                "$sum": {
                    "$map": {
                        "input": "$logs",
                        "as": "l",
                        "in": {
                            "$cond": [
                                {"$in": ["$$l.status", ["PRESENT", "WFH", "ON_DUTY"]]},
                                {"$cond": [{"$eq": ["$$l.half_day", True]}, 0.5, 1]},
                                0
                            ]
                        }
                    }
                }
            },
            "late_count": {
                "$size": {
                    "$filter": {
                        "input": "$logs",
                        "as": "l",
                        "cond": {"$gt": [{"$ifNull": ["$$l.late_minutes", 0]}, 0]}
                    }
                }
            }
        }},
        {"$addFields": {
            "attendance_rate": {
                "$cond": [
                    {"$and": ["$is_working_day", {"$gt": ["$headcount", 0]}]},
                    {"$divide": ["$present_count", "$headcount"]},
                    None
                ]
            }
        }},
        {"$setWindowFields": {
            "sortBy": {"date": 1},
            "output": {
                "moving_avg_7d": {
                    "$avg": "$attendance_rate",
                    "window": {
                        "documents": [-6, "current"]
                    }
                }
            }
        }},
        {"$project": {
            "_id": 0,
            "date": 1,
            "is_working_day": 1,
            "headcount": 1,
            "present_count": 1,
            "late_count": 1,
            "attendance_rate": 1,
            "moving_avg_7d": 1
        }}
    ]

# --------------------------------------------------------------------------- #
# Analytics Endpoints
# --------------------------------------------------------------------------- #
@app.get("/analytics/employees/{emp_code}/monthly")
def get_employee_monthly(emp_code: str, month: str):
    emp = db.employees.find_one({"emp_code": emp_code})
    if not emp:
        raise HTTPException(404, "Employee not found")
        
    working_days = get_working_days(emp["joined_on"], month)
    pipeline = get_employee_monthly_pipeline(emp_code, month)
    
    res = list(db.attendance_logs.aggregate(pipeline))
    if not res:
        data = {
            "present_days": 0, "leave_days": 0, "late_count": 0, 
            "total_late_minutes": 0, "total_overtime_minutes": 0
        }
    else:
        data = res[0]
        
    pct = None
    if working_days > 0:
        pct = (data["present_days"] / working_days) * 100
        
    return {
        "emp_code": emp_code,
        "month": month,
        "working_days": working_days,
        "present_days": float(data["present_days"]),
        "leave_days": data["leave_days"],
        "late_count": data["late_count"],
        "total_late_minutes": data["total_late_minutes"],
        "total_overtime_minutes": data["total_overtime_minutes"],
        "attendance_pct": r8_round(pct, 2)
    }

@app.get("/analytics/departments/summary")
def get_department_summary(month: str, department: Optional[str] = None):
    pipeline = get_department_summary_pipeline(month, department)
    docs = list(db.employees.aggregate(pipeline))
    for d in docs:
        d["avg_work_hours"] = r8_round(d.get("avg_work_hours"), 2)
    return {"month": month, "items": docs}

@app.get("/analytics/leaderboard/late")
def get_late_leaderboard(month: str, department: Optional[str] = None, limit: int = 10):
    pipeline = get_late_leaderboard_pipeline(month, department, limit)
    docs = list(db.employees.aggregate(pipeline))
    return {"month": month, "items": docs}

@app.get("/analytics/departments/{department}/trend")
def get_department_trend(department: str, date_from: str = Query(..., alias="from"), date_to: str = Query(..., alias="to")):
    if date_to < date_from:
        raise HTTPException(422, "date_to < date_from")
    
    dt_from = datetime.fromisoformat(date_from)
    dt_to = datetime.fromisoformat(date_to)
    if (dt_to - dt_from).days > 92:
        raise HTTPException(422, "range exceeds 92 days")
        
    emp = db.employees.find_one({"department": department})
    if not emp:
        raise HTTPException(404, "Unknown department")
        
    pipeline = get_department_trend_pipeline(department, date_from, date_to)
    docs = list(db.employees.aggregate(pipeline))
    for d in docs:
        d["attendance_rate"] = r8_round(d.get("attendance_rate"), 4)
        d["moving_avg_7d"] = r8_round(d.get("moving_avg_7d"), 4)
        
    return {"department": department, "items": docs}

@app.get("/admin/explain/{endpoint}")
def explain_endpoint(
    endpoint: str, 
    emp_code: Optional[str] = None, 
    month: Optional[str] = None,
    department: Optional[str] = None,
    limit: int = 10,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 20,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to")
):
    if endpoint == "attendance_list":
        q = {}
        if emp_code:
            q["emp_code"] = emp_code
        if date_from or date_to:
            q["date"] = {}
            if date_from:
                q["date"]["$gte"] = date_from
            if date_to:
                q["date"]["$lte"] = date_to
        if status:
            q["status"] = status
        skip = (page - 1) * page_size
        
        explain_plan = db.command("explain", {
            "find": "attendance_logs",
            "filter": q,
            "sort": {"date": -1},
            "skip": skip,
            "limit": page_size
        }, verbosity="executionStats")
        
        return {"endpoint": endpoint, "collection": "attendance_logs", "explain": explain_plan}
        
    pipeline = None
    collection = "employees"
    
    if endpoint == "employee_monthly":
        if not emp_code or not month:
            raise HTTPException(422)
        pipeline = get_employee_monthly_pipeline(emp_code, month)
        collection = "attendance_logs"
    elif endpoint == "department_summary":
        if not month:
            raise HTTPException(422)
        pipeline = get_department_summary_pipeline(month, department)
    elif endpoint == "late_leaderboard":
        if not month:
            raise HTTPException(422)
        pipeline = get_late_leaderboard_pipeline(month, department, limit)
    elif endpoint == "department_trend":
        if not department or not from_date or not to_date:
            raise HTTPException(422)
        pipeline = get_department_trend_pipeline(department, from_date, to_date)
    else:
        raise HTTPException(422, "Unknown endpoint")
        
    explain_plan = db.command("explain", {
        "aggregate": collection,
        "pipeline": pipeline,
        "cursor": {}
    }, verbosity="executionStats")
    
    return {"endpoint": endpoint, "collection": collection, "explain": explain_plan}
