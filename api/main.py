"""
Nuvrail Approval REST API.

FastAPI application exposing:
  /api/v1/operations  — list, approve, reject
  /api/v1/audit       — audit log
  /api/v1/push        — Web Push subscription management
  /api/v1/rules       — auto-approval rules (sub-milestone 3.3)

Base URL: http://localhost:8080/api/v1

Sub-milestone: 1.2
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Nuvrail Approval API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes registered in sub-milestone 1.2
# from api.routes import operations, audit, push, rules
# app.include_router(operations.router, prefix="/api/v1")
# app.include_router(audit.router, prefix="/api/v1")
