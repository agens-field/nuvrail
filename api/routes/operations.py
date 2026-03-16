"""
/api/v1/operations endpoints.

GET    /operations          — list (filter by status)
GET    /operations/:id      — detail with message previews
POST   /operations/:id/approve
POST   /operations/:id/reject
POST   /operations/:id/undo
POST   /operations/batch/approve
POST   /operations/batch/reject

Sub-milestone: 1.2 (approve/reject), 1.3 (execution), 3.2 (undo)
"""
from fastapi import APIRouter

router = APIRouter()

# TODO: implement endpoints in sub-milestone 1.2
