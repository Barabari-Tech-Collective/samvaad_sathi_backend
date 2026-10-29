"""The analysis endpoint must accept the identity supplied by the shared auth gate."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from fastapi import FastAPI, HTTPException
from src.api.routes import analysis
from src.models.schemas.analysis import AnalysisMetadata, AggregatedAnalysis


@pytest.mark.asyncio
async def test_analysis_uses_verified_identity_without_second_browser_bearer(monkeypatch):
    app = FastAPI()
    app.include_router(analysis.router)
    app.dependency_overrides[analysis.get_current_user] = lambda: SimpleNamespace(id=17)
    app.dependency_overrides[analysis.get_async_session] = lambda: object()
    route = next(route for route in app.routes if getattr(route, 'path', '') == '/complete-analysis')
    for dep in route.dependant.dependencies:
        if dep.name == 'question_repo':
            app.dependency_overrides[dep.call] = lambda: object()
    aggregate = AsyncMock(return_value=(AggregatedAnalysis(), AnalysisMetadata(
        total_latency_ms=1, completed_analyses=['pace'], failed_analyses=[], partial_failure=False
    ), True, None))
    monkeypatch.setattr(analysis.analysis_service, 'aggregate_question_analysis', aggregate)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='https://api.test') as client:
        response = await client.post('/complete-analysis', json={'questionAttemptId': 9, 'analysisTypes': ['pace']})
        assert response.status_code == 200
        assert aggregate.await_args.kwargs['user_id'] == 17
        assert aggregate.await_args.kwargs['auth_token'] == ''
        async def revoked():
            raise HTTPException(403, 'Access revoked')
        app.dependency_overrides[analysis.get_current_user] = revoked
        assert (await client.post('/complete-analysis', json={'questionAttemptId': 9})).status_code == 403
        assert aggregate.await_count == 1
