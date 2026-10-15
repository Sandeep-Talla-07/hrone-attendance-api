# Employee Attendance Management API

A FastAPI and MongoDB REST API for employee management, attendance tracking, manual regularization, and workforce analytics.

## Requirements
- Python 3.11+
- MongoDB 6.0+
- Dependencies listed in `requirements.txt`

## Setup
1. Create and activate a Python virtual environment.
2. Install dependencies:
   `pip install -r requirements.txt`
3. Create a `.env` file containing `MONGO_URI` and `MONGO_DB`.
4. Start the API:
   `uvicorn app.main:app --port 8000`

## API Documentation
Once running, visit `/docs` for interactive API documentation.

## Configuration
Keep MongoDB credentials in `.env`. Never commit credentials or secrets.
