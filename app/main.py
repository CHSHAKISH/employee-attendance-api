"""
Employee Attendance & Analytics API - STARTER

Run:  uvicorn app.main:app --port 8000
Env:  MONGO_URI, MONGO_DB (a local .env is loaded for convenience)

This file was written quickly by a colleague who has left the company. The happy path
works, but nobody has reviewed it. Read PROBLEM_STATEMENT.docx for what is expected of you,
openapi.yaml for the contract and DATA_MODEL.md for what is stored in MongoDB.
"""
import os
from datetime import datetime, timedelta, timezone
from typing import Optional
from decimal import Decimal, ROUND_HALF_UP

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
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
# TODO - the rest of the contract (see openapi.yaml):
#   GET   /analytics/employees/{emp_code}/monthly
#   GET   /analytics/departments/summary
#   GET   /analytics/leaderboard/late
#   GET   /analytics/departments/{department}/trend
#   GET   /admin/explain/{endpoint}
# --------------------------------------------------------------------------- #
