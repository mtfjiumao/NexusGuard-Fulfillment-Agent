from fastapi import APIRouter

from nexusguard.modules.aftersales.router import router as aftersales_router
from nexusguard.modules.approvals.router import router as approvals_router
from nexusguard.modules.logistics.router import router as logistics_router
from nexusguard.modules.presales.router import router as presales_router
from nexusguard.modules.supervisor.router import router as supervisor_router

router = APIRouter(prefix="/api/v1")
router.include_router(supervisor_router, prefix="/supervisor", tags=["supervisor"])
router.include_router(presales_router, prefix="/presales", tags=["presales"])
router.include_router(logistics_router, prefix="/logistics", tags=["logistics"])
router.include_router(aftersales_router, prefix="/aftersales", tags=["aftersales"])
router.include_router(approvals_router, prefix="/approvals", tags=["approvals"])
