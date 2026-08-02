from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin,
    admin_sessions,
    health,
    model_gateway,
    scenarios,
    sessions,
)

api_router = APIRouter()
api_router.include_router(admin.router)
api_router.include_router(admin_sessions.router)
api_router.include_router(health.router, tags=["health"])
api_router.include_router(scenarios.router)
api_router.include_router(sessions.router)
api_router.include_router(model_gateway.router)
