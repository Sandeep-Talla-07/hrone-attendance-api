# Employee Attendance Management API

A REST API built using **Python, FastAPI, and MongoDB** for employee management, attendance tracking, attendance regularization, and workforce analytics.

## Features

- Employee creation and paginated employee listing.
- Attendance punch-in and punch-out.
- Late arrival, overtime, work-hour, and half-day calculations.
- Attendance filtering, pagination, and manual corrections with audit history.
- Monthly employee analytics and department summaries.
- Late-arrival leaderboard with tie handling.
- Department attendance trends and moving averages.
- MongoDB indexes and query execution diagnostics.

## Technology Stack

- Python 3.11+
- FastAPI
- MongoDB 6.0+
- PyMongo
- Pydantic
- Uvicorn

## Setup and Installation

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Configure MongoDB

Create a `.env` file in the project root:

```text
MONGO_URI=your_mongodb_connection_string
MONGO_DB=hrone_attendance
```

Replace the example connection string with your own MongoDB connection details. Never commit `.env` or credentials.

### 4. Run the application

```bash
uvicorn app.main:app --port 8000
```

### 5. Access API documentation

Once the application is running, open:

- Swagger UI: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`

## Project Structure

- `app/main.py` — API implementation.
- `requirements.txt` — Python dependencies.
- `REVIEW.md` — Starter-code defects and fixes.
- `DECISIONS.md` — Design decisions and trade-offs.
- `DATA_MODEL.md` — MongoDB data model.
- `openapi.yaml` — API contract.

## Configuration and Security

MongoDB credentials must remain in environment configuration and must not be committed to Git. MongoDB 6.0 or newer is required.
