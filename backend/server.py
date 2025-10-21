from fastapi import FastAPI, APIRouter, HTTPException, Query
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field
from typing import List, Optional
import uuid
from datetime import datetime, timezone, date
from enum import Enum

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Create the main app without a prefix
app = FastAPI()

# Create a router with the /api prefix
api_router = APIRouter(prefix="/api")

# Enums for patient management
class TaskStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"

class TaskType(str, Enum):
    CALL_PATIENT = "call_patient"
    REQUEST_PAPERWORK = "request_paperwork"
    TAN_RENEWAL = "tan_renewal"
    BILLING_FOLLOW_UP = "billing_follow_up"
    MEDICAID_VERIFICATION = "medicaid_verification"

class ContactOutcome(str, Enum):
    CONTACTED = "contacted"
    NO_ANSWER = "no_answer"
    BUSY = "busy"
    DISCONNECTED = "disconnected"
    LEFT_MESSAGE = "left_message"

# Patient Models
class Doctor(BaseModel):
    name: str
    phone: str
    fax: str
    address: Optional[str] = None

class Patient(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    first_name: str
    last_name: str
    phone: str
    address: str
    height: Optional[str] = None
    weight: Optional[str] = None
    icd10_codes: List[str] = []
    doctor: Doctor
    current_tan: str
    tan_expiry_date: str  # ISO format date string
    medicaid_id: str
    medicaid_eligible: bool = True
    last_billing_date: Optional[str] = None  # ISO format date string
    products: List[str] = []  # List of products (diapers, underpads, etc.)
    notes: str = ""
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

class PatientCreate(BaseModel):
    first_name: str
    last_name: str
    phone: str
    address: str
    height: Optional[str] = None
    weight: Optional[str] = None
    icd10_codes: List[str] = []
    doctor: Doctor
    current_tan: str
    tan_expiry_date: str
    medicaid_id: str
    medicaid_eligible: bool = True
    products: List[str] = []
    notes: str = ""

class PatientUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    height: Optional[str] = None
    weight: Optional[str] = None
    icd10_codes: Optional[List[str]] = None
    doctor: Optional[Doctor] = None
    current_tan: Optional[str] = None
    tan_expiry_date: Optional[str] = None
    medicaid_id: Optional[str] = None
    medicaid_eligible: Optional[bool] = None
    last_billing_date: Optional[str] = None
    products: Optional[List[str]] = None
    notes: Optional[str] = None

# Task Models
class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    task_type: TaskType
    title: str
    description: str = ""
    assigned_to: str = "admin"  # For now, single user
    status: TaskStatus = TaskStatus.PENDING
    due_date: Optional[str] = None  # ISO format date string
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    completed_at: Optional[str] = None

class TaskCreate(BaseModel):
    patient_id: str
    task_type: TaskType
    title: str
    description: str = ""
    due_date: Optional[str] = None

# Contact Log Models
class ContactLog(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    patient_id: str
    contact_date: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    outcome: ContactOutcome
    notes: str = ""
    follow_up_needed: bool = False
    follow_up_date: Optional[str] = None

class ContactLogCreate(BaseModel):
    patient_id: str
    outcome: ContactOutcome
    notes: str = ""
    follow_up_needed: bool = False
    follow_up_date: Optional[str] = None

# Helper functions
def prepare_for_mongo(data):
    """Convert date objects to ISO strings before storing in MongoDB"""
    if isinstance(data, dict):
        for key, value in data.items():
            if isinstance(value, date):
                data[key] = value.isoformat()
    return data

# Patient endpoints
@api_router.post("/patients", response_model=Patient)
async def create_patient(patient_data: PatientCreate):
    patient_dict = patient_data.dict()
    patient_dict = prepare_for_mongo(patient_dict)
    patient_obj = Patient(**patient_dict)
    patient_dict = patient_obj.dict()
    await db.patients.insert_one(patient_dict)
    return patient_obj

@api_router.get("/patients", response_model=List[Patient])
async def get_patients(
    search: Optional[str] = Query(None, description="Search by name or phone"),
    tan_expiring: Optional[bool] = Query(None, description="Filter patients with expiring TANs")
):
    query = {}
    
    if search:
        query["$or"] = [
            {"first_name": {"$regex": search, "$options": "i"}},
            {"last_name": {"$regex": search, "$options": "i"}},
            {"phone": {"$regex": search, "$options": "i"}}
        ]
    
    if tan_expiring:
        # Get patients with TANs expiring in next 30 days
        from datetime import datetime, timedelta
        thirty_days = (datetime.now() + timedelta(days=30)).isoformat()
        query["tan_expiry_date"] = {"$lte": thirty_days}
    
    patients = await db.patients.find(query).to_list(1000)
    return [Patient(**patient) for patient in patients]

@api_router.get("/patients/{patient_id}", response_model=Patient)
async def get_patient(patient_id: str):
    patient = await db.patients.find_one({"id": patient_id})
    if not patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    return Patient(**patient)

@api_router.put("/patients/{patient_id}", response_model=Patient)
async def update_patient(patient_id: str, patient_update: PatientUpdate):
    # Get existing patient
    existing_patient = await db.patients.find_one({"id": patient_id})
    if not existing_patient:
        raise HTTPException(status_code=404, detail="Patient not found")
    
    # Update fields
    update_dict = {k: v for k, v in patient_update.dict().items() if v is not None}
    update_dict["updated_at"] = datetime.now(timezone.utc).isoformat()
    update_dict = prepare_for_mongo(update_dict)
    
    await db.patients.update_one({"id": patient_id}, {"$set": update_dict})
    
    # Return updated patient
    updated_patient = await db.patients.find_one({"id": patient_id})
    return Patient(**updated_patient)

@api_router.delete("/patients/{patient_id}")
async def delete_patient(patient_id: str):
    result = await db.patients.delete_one({"id": patient_id})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Patient not found")
    return {"message": "Patient deleted successfully"}

# Task endpoints
@api_router.post("/tasks", response_model=Task)
async def create_task(task_data: TaskCreate):
    task_dict = task_data.dict()
    task_dict = prepare_for_mongo(task_dict)
    task_obj = Task(**task_dict)
    task_dict = task_obj.dict()
    await db.tasks.insert_one(task_dict)
    return task_obj

@api_router.get("/tasks", response_model=List[Task])
async def get_tasks(
    patient_id: Optional[str] = Query(None, description="Filter by patient ID"),
    status: Optional[TaskStatus] = Query(None, description="Filter by task status"),
    due_today: Optional[bool] = Query(None, description="Filter tasks due today")
):
    query = {}
    
    if patient_id:
        query["patient_id"] = patient_id
    
    if status:
        query["status"] = status
    
    if due_today:
        today = datetime.now(timezone.utc).date().isoformat()
        query["due_date"] = today
    
    tasks = await db.tasks.find(query).to_list(1000)
    return [Task(**task) for task in tasks]

@api_router.put("/tasks/{task_id}/complete")
async def complete_task(task_id: str):
    result = await db.tasks.update_one(
        {"id": task_id},
        {
            "$set": {
                "status": TaskStatus.COMPLETED,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }
        }
    )
    if result.modified_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    return {"message": "Task completed successfully"}

# Contact Log endpoints
@api_router.post("/contact-logs", response_model=ContactLog)
async def create_contact_log(contact_data: ContactLogCreate):
    contact_dict = contact_data.dict()
    contact_dict = prepare_for_mongo(contact_dict)
    contact_obj = ContactLog(**contact_dict)
    contact_dict = contact_obj.dict()
    await db.contact_logs.insert_one(contact_dict)
    return contact_obj

@api_router.get("/contact-logs/{patient_id}", response_model=List[ContactLog])
async def get_patient_contact_logs(patient_id: str):
    logs = await db.contact_logs.find({"patient_id": patient_id}).sort("contact_date", -1).to_list(1000)
    return [ContactLog(**log) for log in logs]

# Reports endpoints
@api_router.get("/reports/daily-calls")
async def get_daily_call_report():
    today = datetime.now(timezone.utc).date().isoformat()
    
    # Get tasks due today
    daily_tasks = await db.tasks.find({
        "due_date": today,
        "status": {"$ne": TaskStatus.COMPLETED}
    }).to_list(1000)
    
    # Get patients needing callbacks
    callback_logs = await db.contact_logs.find({
        "follow_up_needed": True,
        "follow_up_date": {"$lte": today}
    }).to_list(1000)
    
    # Get patients with expiring TANs (next 30 days)
    from datetime import timedelta
    thirty_days = (datetime.now() + timedelta(days=30)).isoformat()
    expiring_tans = await db.patients.find({
        "tan_expiry_date": {"$lte": thirty_days}
    }).to_list(1000)
    
    return {
        "daily_tasks": [Task(**task) for task in daily_tasks],
        "callbacks_needed": [ContactLog(**log) for log in callback_logs],
        "expiring_tans": [Patient(**patient) for patient in expiring_tans],
        "total_items": len(daily_tasks) + len(callback_logs) + len(expiring_tans)
    }

@api_router.get("/reports/monthly-summary")
async def get_monthly_summary():
    # Get current month data
    from datetime import datetime
    current_month = datetime.now().strftime("%Y-%m")
    
    # Total patients
    total_patients = await db.patients.count_documents({})
    
    # New patients this month
    new_patients = await db.patients.count_documents({
        "created_at": {"$regex": f"^{current_month}"}
    })
    
    # Billing completed this month
    billed_patients = await db.patients.count_documents({
        "last_billing_date": {"$regex": f"^{current_month}"}
    })
    
    # Unable to contact (patients with failed contact attempts)
    unable_to_contact = await db.contact_logs.count_documents({
        "outcome": {"$in": [ContactOutcome.NO_ANSWER, ContactOutcome.DISCONNECTED]},
        "contact_date": {"$regex": f"^{current_month}"}
    })
    
    # Medicaid eligibility issues
    medicaid_issues = await db.patients.count_documents({
        "medicaid_eligible": False
    })
    
    return {
        "total_patients": total_patients,
        "new_patients": new_patients,
        "billed_patients": billed_patients,
        "unable_to_contact": unable_to_contact,
        "medicaid_issues": medicaid_issues,
        "month": current_month
    }

# Include the router in the main app
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()