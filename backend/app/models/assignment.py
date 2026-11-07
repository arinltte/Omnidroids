# SPDX-License-Identifier: AGPL-3.0-or-later
"""Step → model assignment (owner decision, issue #22).

The five pipeline steps (UI vocabulary: ``main.stepNames``) run:

    1 Graph Build        2 Env Setup      3 Run Simulation
    4 Report Generation  5 Deep Interaction

Steps 1–3 keep resolving the model exactly as they always have —
``Config.LLM_MODEL_NAME`` → the ``/v1`` proxy → the ``small`` role. Steps 4 and 5
are both served by ``ReportAgent`` and resolve the ``big`` role instead, so the
measured 9B writes the report and answers follow-up questions.

**Role indirection, never a model name.** The name is re-read from the Model
Manager registry (``models/registry.json``, ``ModelStore.model_for_role``) on
every call, so reassigning the role in the Model Manager — or pointing
``REPORT_LLM_MODEL_NAME`` at another installed 9B-class model — changes the
pipeline without a code change. No model name or machine path lives in this file.

**An unassigned role is a hard error, never a fallback.** Silently dropping back
to the ``small`` role would quietly undo the owner's decision, so the resolver
raises :class:`StepModelUnavailableError` naming the role, the Model Manager and
the config escape hatch.
"""

from __future__ import annotations

from ..config import Config
from ..utils.logger import get_logger
from .store import ModelStore

logger = get_logger("omnidroids.models.assignment")

#: The pipeline steps that resolve the big role (both served by ``ReportAgent``).
REPORT_STEP_LABEL = "Report Generation / Deep Interaction (steps 4-5)"


class StepModelUnavailableError(RuntimeError):
    """A step's assigned role has no usable model — audible, never a downgrade."""


def _unavailable(role: str, reason: str) -> StepModelUnavailableError:
    return StepModelUnavailableError(
        f"{REPORT_STEP_LABEL} need the {role!r} model role, but {reason}. "
        f"Assign a model to the {role!r} role in the Model Manager, or set "
        "REPORT_LLM_MODEL_NAME to an installed model's name. "
        "No fallback to the 'small' role: the step→model assignment is the "
        "owner's decision (issue #22)."
    )


def resolve_report_model() -> str:
    """Model name steps 4–5 (Report Generation, Deep Interaction) run on.

    ``Config.REPORT_LLM_MODEL_NAME`` (when set) wins over the role; otherwise the
    ``Config.REPORT_LLM_ROLE`` role is resolved through the Model Manager registry.
    """

    override = (Config.REPORT_LLM_MODEL_NAME or "").strip()
    store = ModelStore()
    if override:
        if store.get_model(override) is None:
            raise _unavailable(
                Config.REPORT_LLM_ROLE,
                f"REPORT_LLM_MODEL_NAME={override!r} is not an installed model",
            )
        return override

    role = (Config.REPORT_LLM_ROLE or "big").strip().lower()
    name = store.model_for_role(role)
    if not name:
        # model_for_role() is None both when the role is unassigned and when the
        # assigned model was deleted — the same actionable error either way.
        raise _unavailable(
            role,
            "the Model Manager has no model assigned to it (unassigned, or the "
            "assigned model is no longer installed)",
        )
    logger.info("step 4-5 model resolved: role=%s model=%s", role, name)
    return name
