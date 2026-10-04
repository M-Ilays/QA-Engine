"""Turn the actions a run actually performed into the test cases shown to the user.

The page catalog (smoke titles, empty-field probes) is not what the operator
asked for. When a run is scoped to a feature, the test list and CSV must
describe the submission that ran.
"""

from __future__ import annotations

import csv
import io
import json
import re
from typing import Any

from app.agent.test_scope import focus_terms, requested_types

_FIELD_NAMES = {
    "first_name": "First Name",
    "last_name": "Last Name",
    "email": "Email",
    "password": "Password",
    "username": "Username",
    "phone": "Phone",
}

_SUBMIT_HINTS = ("submit", "register", "sign up", "signup", "log in", "login", "create account")


def _loads(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _action(payload: dict) -> dict:
    inner = payload.get("action")
    return inner if isinstance(inner, dict) else payload


def _meta(action: dict) -> dict:
    meta = action.get("metadata") or {}
    return meta if isinstance(meta, dict) else {}


def _field_label(action: dict) -> str:
    meta = _meta(action)
    ref = str(meta.get("credential_ref") or "")
    key = ref.split(".")[-1].lower()
    if key in _FIELD_NAMES:
        return _FIELD_NAMES[key]
    label = str(meta.get("field") or meta.get("label") or "").strip()
    if label and not re.match(r"^(el_|field_|input_|btn_)\d+$", label, re.I):
        # Form headings are stored as the action label; they are not field names.
        if len(label) > 40:
            return ""
        return label
    return ""


def _is_submit(action: dict) -> bool:
    meta = _meta(action)
    blob = " ".join(
        [
            str(action.get("reason") or ""),
            str(meta.get("action_label") or ""),
            str(meta.get("decision") or ""),
        ]
    ).lower()
    if "click navigation" in blob:
        return False
    return any(hint in blob for hint in _SUBMIT_HINTS)


def _module_label(focus: list[str]) -> str:
    raw = (focus[0] if focus else "test").upper().replace(" ", "_")
    return re.sub(r"[^A-Z0-9_]", "", raw) or "TEST"


def cases_from_actions(
    payloads: list[Any],
    *,
    objective: str | None,
    focus_modules: list[str] | None = None,
    test_case_types: list[str] | None = None,
) -> list[dict] | None:
    """Return executed feature cases, or None when the run was not scoped.

    None means the caller should keep the normal catalog. A list (including
    empty) means the catalog must not be shown.
    """
    focus = focus_terms(objective, focus_modules)
    if not focus:
        return None
    types = requested_types(objective, test_case_types)
    module = _module_label(focus)
    feature = focus[0]

    pending: dict[str, str] = {}
    cases: list[dict] = []
    pos_n = 0
    neg_n = 0

    for raw in payloads:
        payload = _loads(raw)
        action = _action(payload)
        kind = str(action.get("action") or payload.get("action_type") or "").lower()
        if kind == "fill":
            label = _field_label(action)
            if not label:
                continue
            value = action.get("value")
            if label.lower() == "password" or "password" in label.lower():
                pending[label] = "••••••••"
            elif value is None or str(value) == "":
                pending[label] = ""
            else:
                pending[label] = str(value)
            continue
        if kind != "click" or not _is_submit(action):
            continue
        if not pending:
            continue

        filled = {k: v for k, v in pending.items() if v}
        empty_only = not filled
        case_type = "negative" if empty_only else "positive"
        if types == ["positive"] and case_type != "positive":
            pending = {}
            continue
        if types == ["negative"] and case_type != "negative":
            pending = {}
            continue

        if case_type == "negative":
            neg_n += 1
            number = neg_n
            marker = "NEG"
            title = f"Submit {feature} with invalid or empty data"
            description = (
                f"Verify that {feature} rejects empty or invalid input and does not complete."
            )
            expected = ["The application shows a validation error and does not create the account."]
        else:
            pos_n += 1
            number = pos_n
            marker = "POS"
            title = f"Submit {feature} with valid data"
            description = (
                f"Verify that {feature} accepts valid input and completes successfully."
            )
            expected = ["The application accepts the data and shows the expected success state."]

        success = bool(payload.get("success", True))
        after = str(payload.get("after_url") or "")
        data_json = dict(pending)
        data_text = "\n".join(f"{k}: {v if v else '(empty)'}" for k, v in data_json.items())
        cases.append(
            {
                "test_case_id": f"TC_{module}_{marker}_{number:03d}",
                "test_case_type": case_type,
                "title": title,
                "description": description,
                "test_data": data_text,
                "test_data_json": data_json,
                "execution_status": "Executed",
                "result": "Pass" if success else "Fail",
                "category": feature,
                "priority": "high",
                "preconditions": [f"The {feature} form is open."],
                "test_steps": [
                    f"Open the {feature} form",
                    "Enter the test data",
                    "Submit the form",
                    "Observe the application response",
                ],
                "expected_result": expected,
                "actual_result": (
                    f"Submitted successfully. Landed on {after}."
                    if success and after
                    else ("The submission completed." if success else "The submission did not succeed.")
                ),
            }
        )
        pending = {}

    return cases


def cases_to_csv(cases: list[dict]) -> str:
    buffer = io.StringIO()
    fields = [
        "test_case_id",
        "description",
        "test_data",
        "test_steps",
        "expected_result",
        "actual_result",
        "execution_status",
        "result",
        "category",
        "priority",
    ]
    writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for case in cases:
        writer.writerow(
            {
                "test_case_id": case.get("test_case_id", ""),
                "description": case.get("description", ""),
                "test_data": case.get("test_data", ""),
                "test_steps": " | ".join(case.get("test_steps") or []),
                "expected_result": " | ".join(case.get("expected_result") or []),
                "actual_result": case.get("actual_result", ""),
                "execution_status": case.get("execution_status", ""),
                "result": case.get("result", ""),
                "category": case.get("category", ""),
                "priority": case.get("priority", ""),
            }
        )
    return buffer.getvalue()
