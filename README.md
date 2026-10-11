# Employee Attendance & Analytics API

A robust, high-performance REST API built with **FastAPI** and **MongoDB 7.0+** for managing employee profiles, shift attendance tracking, punch regularizations, and departmental workforce analytics.

Designed for high concurrency and scale, featuring atomic race condition protection, overnight shift handling, and optimized MongoDB aggregation pipelines with custom indexing.

---

## Tech Stack

- **Framework**: [FastAPI](https://fastapi.tiangolo.com/) (Python 3.12+)
- **Database**: [MongoDB 7.0+](https://www.mongodb.com/) via [PyMongo](https://pymongo.readthedocs.io/)
- **Server**: [Uvicorn](https://www.uvicorn.org/) (ASGI)
- **Data Validation**: [Pydantic v2](https://docs.pydantic.dev/)

---

## Core Features & Business Rules

1. **Employee Management**
   - Unique employee code enforcement with database-level uniqueness.
   - 1-indexed pagination and deterministic sorting by `emp_code`.

2. **Shift & Attendance Tracking**
   - **Punch-In & Punch-Out**: Records check-ins and check-outs with strict validation.
   - **Overnight Shift Handling**: Automatically maps punch times before shift end back to the shift's starting calendar date.
   - **Grace Period & Late Calculation (R1)**: A 10-minute grace period; late minutes are credited only when arrival strictly exceeds 10 minutes (`> 600s`).
   - **Work Hours Computation (R2)**: Standard decimal calculation using exact `ROUND_HALF_UP` rounding to 2 decimal places.
   - **Overtime Threshold (R3)**: Minimum 30 minutes threshold required to qualify for overtime hours.
   - **Race Condition Prevention**: Enforces a unique compound index on `(emp_code, date)` to prevent duplicate punch-in attempts atomically.

3. **Attendance Regularization**
   - `PATCH /attendance/{record_id}`: Allows authorized managers to adjust punch-in/out timestamps, reason codes, and record statuses with automatic recalculation of hours, late minutes, and overtime.

4. **High-Performance Analytics Pipelines**
   - **Employee Monthly (`/analytics/employee-monthly`)**: Aggregates total hours, overtime, late counts, and present days for a given employee and month.
   - **Department Summary (`/analytics/department-summary`)**: Summarizes attendance records and hours worked grouped across departments.
   - **Late Leaderboard (`/analytics/late-leaderboard`)**: Dense ranking of top-late employees using MongoDB `$rank` window operators, gracefully preserving ties.
   - **Department Trend (`/analytics/department-trend`)**: Multi-month departmental attendance trends tracking active employee headcounts using null-preserving `$lookup` joins.

5. **Performance & Query Explainability**
   - Index optimization across operational and analytical query paths.
   - `GET /admin/explain/{endpoint}` exposes query execution plans (`executionStats`) verifying index utilization and preventing full collection scans (`COLLSCAN`) at 100k+ documents.

---

## Project Structure

```text
├── app/
│   ├── __init__.py
│   └── main.py              # Complete application (routes, models, pipelines, db indexes)
├── sample_data/             # MongoDB Extended JSON sample records
├── sample_seed.py           # Database seeding script
├── REVIEW.md                # Codebase defect review and resolution log (11 bugs resolved)
├── DECISIONS.md             # Architectural decisions (indexes, concurrency, scaling)
├── requirements.txt         # Project dependencies
└── README.md                # Project documentation
```

For detailed architectural decisions and bug fixes, refer to:
- [REVIEW.md](REVIEW.md) – Detailed root-cause analysis and resolution for all 11 starter defects.
- [DECISIONS.md](DECISIONS.md) – Technical write-up on indexing strategies, race prevention, tie resolution, and scaling roadmap.

---

## Getting Started

### Prerequisites

- Python 3.12 or newer
- MongoDB 6.0 or newer (MongoDB 7.0 recommended, or MongoDB Atlas)

### 1. Environment Setup

Clone the repository and set up a virtual environment:

```bash
git clone <repository-url>
cd candidate_kit

python -m venv .venv
# On Linux / macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1
```

Install the dependencies:

```bash
pip install -r requirements.txt
```

### 2. Configure Environment Variables

Create a `.env` file in the project root (or configure your shell environment):

```bash
cp .env.example .env
```

Set the database connection parameters in `.env`:

```env
MONGO_URI=mongodb://localhost:27017
MONGO_DB=attendance_db
PORT=8000
```

*(If running MongoDB locally via Docker: `docker run -d --name mongo -p 27017:27017 mongo:7`)*

### 3. Seed Sample Data (Optional)

Populate the database with initial sample employee and attendance documents:

```bash
python sample_seed.py
```

### 4. Run the Application

Start the development server with Uvicorn:

```bash
uvicorn app.main:app --port 8000 --reload
```

The API will be available at `http://localhost:8000`.

---

## API Endpoints Reference

| Category | Method | Endpoint | Description |
|---|---|---|---|
| **Health** | `GET` | `/health` | Live database ping and service status |
| **Employees** | `POST` | `/employees` | Create a new employee profile |
| | `GET` | `/employees` | List employees with pagination and department filter |
| **Attendance** | `POST` | `/attendance/punch-in` | Record employee punch-in |
| | `POST` | `/attendance/punch-out` | Record employee punch-out |
| | `GET` | `/attendance` | List attendance logs sorted by date descending |
| | `PATCH` | `/attendance/{record_id}` | Regularize and update attendance record |
| **Analytics** | `GET` | `/analytics/employee-monthly` | Monthly employee attendance aggregate |
| | `GET` | `/analytics/department-summary` | Department-level attendance metrics |
| | `GET` | `/analytics/late-leaderboard` | Top late arrivals leaderboard with `$rank` |
| | `GET` | `/analytics/department-trend` | Multi-month departmental attendance trends |
| **Admin** | `GET` | `/admin/explain/{endpoint}` | Query execution plan & index stats |

### Interactive Documentation

Once the server is running, visit:
- **Swagger UI**: [http://localhost:8000/docs](http://localhost:8000/docs)
- **ReDoc**: [http://localhost:8000/redoc](http://localhost:8000/redoc)
