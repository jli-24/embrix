from __future__ import annotations

from typing import Protocol

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from embedded_copilot.api.models import AnalyzeRequest, AnalyzeResponse
from embedded_copilot.integration.report import EngineeringReport
from embedded_copilot.schemas.api import ChatRequest, ChatResponse, HealthResponse
from embedded_copilot.schemas.result import ErrorCode, ErrorDetail
from embedded_copilot.services.analysis import AnalysisCommand
from embedded_copilot.services.execution import (
    ExecutionCapacityError,
    ExecutionNotFoundError,
    ExecutionSnapshot,
    ReportNotReadyError,
)


class ChatService(Protocol):
    async def chat(self, message: str, *, trace_id: str) -> ChatResponse: ...


class ProductAnalysisService(Protocol):
    async def submit(self, command: AnalysisCommand) -> ExecutionSnapshot: ...

    def get_status(self, execution_id: str) -> ExecutionSnapshot: ...

    def get_report(self, execution_id: str) -> EngineeringReport: ...


def get_chat_service(request: Request) -> ChatService | None:
    return request.app.state.copilot_service


def get_analysis_service(request: Request) -> ProductAnalysisService:
    return request.app.state.analysis_service


def _error_status(error_code: ErrorCode) -> int:
    return {
        ErrorCode.VALIDATION_ERROR: 422,
        ErrorCode.PERMISSION_DENIED: 403,
        ErrorCode.TIMEOUT: 504,
        ErrorCode.RETRIEVAL_ERROR: 503,
        ErrorCode.MODEL_ERROR: 503,
        ErrorCode.INTERNAL_ERROR: 500,
    }[error_code]


router = APIRouter()


@router.post(
    "/api/v1/analyze",
    response_model=AnalyzeResponse,
    status_code=202,
)
async def analyze(
    payload: AnalyzeRequest,
    service: ProductAnalysisService = Depends(get_analysis_service),
) -> AnalyzeResponse | JSONResponse:
    try:
        snapshot = await service.submit(payload.to_command())
    except ExecutionCapacityError:
        return JSONResponse(
            status_code=503,
            content={"detail": "Analysis capacity is full."},
        )
    return AnalyzeResponse(
        execution_id=snapshot.execution_id,
        status=snapshot.status,
    )


@router.get(
    "/api/v1/status/{execution_id}",
    response_model=ExecutionSnapshot,
)
async def analysis_status(
    execution_id: str,
    service: ProductAnalysisService = Depends(get_analysis_service),
) -> ExecutionSnapshot | JSONResponse:
    try:
        return service.get_status(execution_id)
    except ExecutionNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": "Analysis execution was not found."},
        )


@router.get(
    "/api/v1/report/{execution_id}",
    response_model=EngineeringReport,
)
async def analysis_report(
    execution_id: str,
    service: ProductAnalysisService = Depends(get_analysis_service),
) -> EngineeringReport | JSONResponse:
    try:
        return service.get_report(execution_id)
    except ExecutionNotFoundError:
        return JSONResponse(
            status_code=404,
            content={"detail": "Analysis execution was not found."},
        )
    except ReportNotReadyError:
        return JSONResponse(
            status_code=409,
            content={"detail": "Analysis report is not ready."},
        )


@router.post("/api/v1/chat", response_model=ChatResponse)
@router.post("/chat", response_model=ChatResponse, include_in_schema=False)
async def chat(
    payload: ChatRequest,
    request: Request,
    service: ChatService | None = Depends(get_chat_service),
) -> ChatResponse | JSONResponse:
    if service is None:
        response = ChatResponse(
            answer="",
            trace_id=request.state.trace_id,
            error=ErrorDetail(
                code=ErrorCode.MODEL_ERROR,
                message="Chat runtime is unavailable.",
                retryable=False,
            ),
        )
        return JSONResponse(status_code=503, content=response.model_dump(mode="json"))
    response = await service.chat(payload.message, trace_id=request.state.trace_id)
    if response.error is not None:
        return JSONResponse(
            status_code=_error_status(response.error.code),
            content=response.model_dump(mode="json"),
        )
    return response


@router.get("/api/v1/health", response_model=HealthResponse)
@router.get("/health", response_model=HealthResponse, include_in_schema=False)
async def health(request: Request) -> HealthResponse:
    return HealthResponse(
        status=request.app.state.health_status,
        mode=request.app.state.settings.runtime_mode,
    )
