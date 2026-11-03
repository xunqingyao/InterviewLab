"""Model-split resume claims. Official questions are not generated here."""

from __future__ import annotations

from agents.prompts import EXTRACTOR_V1
from domain.claims import extract_claims, ground_extracted_claims
from domain.errors import DependencyUnavailable, QuotaExceeded
from domain.requirements import extract_requirements, ground_extracted_requirements
from domain.schemas import ClaimsExtraction, RequirementsExtraction
from llm_gateway.factory import build_gateway
from llm_gateway.interface import AgentInputSnapshot, AgentRole, BudgetContext

assert EXTRACTOR_V1


async def extract_resume_claims(text: str, *, owner_id: str, session_id: str | None = None) -> dict:
    fallback = extract_claims(text)
    try:
        result = await build_gateway().generate_structured(
            role=AgentRole.EXTRACTOR,
            input_snapshot=AgentInputSnapshot(
                id="extract-claims",
                role=AgentRole.EXTRACTOR,
                payload={"text": text},
            ),
            output_schema=ClaimsExtraction,
            tools=[],
            budget=BudgetContext(owner_id=owner_id, session_id=session_id),
        )
        grounded = ground_extracted_claims(text, (result.data or {}).get("claims") or [])
        if grounded:
            return {"items": grounded, "source": "model"}
    except (DependencyUnavailable, QuotaExceeded):
        raise
    except Exception:
        pass
    return fallback


async def extract_job_requirements(text: str, *, owner_id: str) -> dict:
    """Extract complete JD requirements; only retain text present in the JD."""
    try:
        result = await build_gateway().generate_structured(
            role=AgentRole.EXTRACTOR,
            input_snapshot=AgentInputSnapshot(
                id="extract-requirements",
                role=AgentRole.EXTRACTOR,
                payload={"task": "extract_requirements", "text": text},
            ),
            output_schema=RequirementsExtraction,
            tools=[],
            budget=BudgetContext(owner_id=owner_id),
        )
        items = ground_extracted_requirements(text, (result.data or {}).get("requirements") or [])
        if items:
            return {"items": items, "source": "model"}
    except (DependencyUnavailable, QuotaExceeded):
        raise
    except Exception:
        pass
    return extract_requirements(text)
