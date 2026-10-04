"""Deterministic structured QA report builder (memory is source of truth)."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from app.agent.memory import RunMemory
from app.application.evidence_paths import sanitize_evidence_index_entry
from app.application.url_normalize import normalize_url as normalize_page_url
from app.reporting.mermaid_builder import (
    build_named_workflow_mermaid,
    build_navigation_mermaid,
    build_user_journey_mermaid,
    build_workflow_mermaid,
)
from app.schemas import (
    BugAnalysisResult,
    BugClassification,
    BugReportEntry,
    CoverageRecord,
    Defect,
    FinalReport,
    ProductOverview,
)
from app.utils.logging import get_logger
from app.utils.sanitization import MASK, sanitize_dict, sanitize_url
from app.runtime_info import build_runtime_info
from app.reporting.blocked_reasons import (
    classify_legacy_scenario_reason,
    classify_scenario_blocked_reason,
)

logger = get_logger("reporting.builder")

REPORT_SECTION_ORDER = [
    "Executive Summary",
    "Run Environment",
    "Configuration and Capability Disclosure",
    "Product Overview",
    "Inferred Business Domain",
    "Application Purpose",
    "Modules",
    "Navigation Structure",
    "Page Inventory",
    "Forms Inventory",
    "Form Lifecycle",
    "Tables Inventory",
    "Collection/Grid Coverage",
    "Role and Permission Observations",
    "Workflow Catalog",
    "User Journeys",
    "Business Rule Observations",
    "Knowledge Graph Summary",
    "Generated Goals",
    "Scenario Planning Summary",
    "QA Strategy Summary",
    "Autonomous Investigation Summary",
    "CRUD Workflow Coverage",
    "Test Scenarios",
    "Test Execution Results",
    "Executed Assertions",
    "Blocked Scenarios",
    "Unexecuted Scenarios",
    "Confirmed Bugs",
    "Suspected Bugs",
    "UX and Quality Observations",
    "Coverage Summary",
    "Temporary Records",
    "Cleanup Status",
    "Regression Checklist",
    "Console Errors",
    "Network Errors",
    "Evidence Index",
    "Stop Reason",
    "Known Limitations",
    "Recommended Next Testing Areas",
]


_SECRET_ASSIGN = re.compile(
    r"(?i)\b(password|passwd|pwd|token|api[_-]?key|secret|cookie|set-cookie)\b\s*[:=]\s*\S+"
)
_AUTH_HEADER = re.compile(r"(?i)\bauthorization\b\s*[:=]\s*\S+(?:\s+\S+)?")
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9\-._~+/]+=*")
_COOKIE_PAIR = re.compile(r"(?i)\b(sessionid|auth|jwt|access_token)=([^\s;]+)")


_NEGATIVE_KEYWORDS = frozenset({
    "invalid", "empty", "missing", "blank", "wrong", "incorrect",
    "error", "fail", "reject", "boundary", "exceed", "too long",
    "special char", "sql", "xss", "injection", "negative", "bad",
    "unauthorized", "forbidden", "duplicate", "already exists",
})

_POSITIVE_KEYWORDS = frozenset({
    "valid", "correct", "success", "happy", "positive", "smoke",
    "confirm", "verify", "submit", "create", "login", "register",
})

# Ordered list: (keywords, MODULE_LABEL)
_MODULE_KEYWORDS: list[tuple[list[str], str]] = [
    (["signup", "sign up", "register", "registration", "create account"], "SIGNUP"),
    (["login", "sign in", "signin", "log in", "authenticate"], "LOGIN"),
    (["logout", "log out", "signout", "sign out"], "LOGOUT"),
    (["contact", "contacts"], "CONTACT"),
    (["search", "find", "filter", "query"], "SEARCH"),
    (["add", "create", "new record", "insert"], "CREATE"),
    (["edit", "update", "modify", "change"], "UPDATE"),
    (["delete", "remove", "trash"], "DELETE"),
    (["nav", "navigate", "navigation", "menu"], "NAV"),
    (["page", "title", "smoke", "sanity"], "SMOKE"),
    (["form"], "FORM"),
    (["profile", "account", "settings"], "PROFILE"),
    (["dashboard", "home"], "DASHBOARD"),
]

# Generic description patterns → better descriptions
_DESCRIPTION_REWRITES: list[tuple[str, str]] = [
    ("empty value on submit", "Verify that the form prevents submission when required fields are left empty."),
    ("smoke: page title present", "Verify that the page loads correctly and the page title is visible."),
    ("smoke:", "Verify that the page or feature loads and renders without errors."),
    ("empty value", "Verify that the form shows validation errors when a required field is left empty."),
    ("valid value on submit", "Verify that the form submits successfully when all required fields contain valid data."),
    ("boundary test", "Verify that the form handles boundary / edge-case input values correctly."),
    ("negative test", "Verify that the application correctly rejects invalid input and shows an appropriate error message."),
]


def _rewrite_description(original: str) -> str:
    """Improve known generic AI-generated descriptions to be more meaningful."""
    if not original:
        return original
    low = original.lower().strip()
    for pattern, replacement in _DESCRIPTION_REWRITES:
        if pattern in low:
            return replacement
    return original


def _extract_module_label(title: str, category: str) -> str:
    """Extract TC module label (e.g. SIGNUP) from scenario title + category."""
    text = (title + " " + category).lower()
    for keywords, label in _MODULE_KEYWORDS:
        if any(kw in text for kw in keywords):
            return label
    return "TEST"


def _infer_test_case_type(scenario) -> str:
    """Infer positive / negative / exploratory from scenario title + description + category."""
    text = " ".join([
        str(getattr(scenario, "title", "") or ""),
        str(getattr(scenario, "description", "") or ""),
        str(getattr(scenario, "category", "") or ""),
    ]).lower()

    neg_hits = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in text)
    pos_hits = sum(1 for kw in _POSITIVE_KEYWORDS if kw in text)

    if neg_hits > pos_hits:
        return "negative"
    if pos_hits > 0:
        return "positive"
    if "smoke" in text or "sanity" in text:
        return "positive"
    return "exploratory"


def _collect_fill_data(memory: "RunMemory") -> dict[str, str]:
    """Extract FILL action field:value pairs as a structured dict.
    Skips bare element IDs (el_001, field_2) and empty values.
    Returns dict like {"Email": "user@test.com", "Password": "••••••••"}
    """
    fill_dict: dict[str, str] = {}
    for action_result in getattr(memory, "actions", []):
        action = getattr(action_result, "action", None)
        if action is None:
            continue
        action_type = getattr(action, "action", None)
        if str(action_type) not in ("fill", "ActionType.FILL"):
            continue
        meta = getattr(action, "metadata", None) or {}
        label = meta.get("label") or meta.get("field") or ""
        value = meta.get("value") or meta.get("fill_value") or ""

        # Skip entries with no actual value
        if not value:
            continue
        # Skip raw element-ID labels like el_001, field_2, input_3, btn_4
        if not label or re.match(r"^(el_|field_|input_|btn_)\d+$", str(label).strip().lower()):
            continue

        is_password_field = any(
            w in str(label).lower() for w in ("password", "pass", "secret", "token")
        )
        display_value = "••••••••" if is_password_field else str(value)
        if label not in fill_dict:
            fill_dict[label] = display_value
    return fill_dict


def _enrich_scenarios_from_actions(
    scenarios: list,
    memory: "RunMemory",
) -> list:
    """Enrich every TestScenario with QA report fields.

    Rules:
    - execution_status is ONLY set to "Executed" when there is an explicit
      TestExecution record for this scenario. Browser action pass-rate is NOT
      used to infer execution — that would violate the assertion-vs-execution
      distinction the QA spec requires.
    - result is "Pass" when the TestExecution status is "passed", "Fail" when
      "failed" or "blocked", and "N/A" when not executed.
    - test_data is only populated for executed scenarios.
    """
    from app.schemas import TestScenario

    if not scenarios:
        return scenarios

    # Build execution lookup: test_id → normalised status
    exec_status: dict[str, str] = {}
    for ex in getattr(memory, "executions", []):
        raw = getattr(ex, "status", "not_run")
        if raw == "passed":
            exec_status[ex.test_id] = "passed"
        elif raw in ("failed", "blocked"):
            exec_status[ex.test_id] = "failed"
        # not_run / skipped → omit (treat as not executed)

    # Collect fill data dict from all agent actions
    fill_dict = _collect_fill_data(memory)
    fill_list = [f"{k}: {v}" for k, v in fill_dict.items()]
    fill_formatted = "\n".join(fill_list)

    # Track module counters per label for TC IDs
    module_counters: dict[str, int] = {}

    enriched = []
    for s in scenarios:
        if not isinstance(s, TestScenario):
            enriched.append(s)
            continue

        # Generate friendly TC ID (TC_SIGNUP_POS_001 / TC_SIGNUP_NEG_001)
        tc_type = getattr(s, "test_case_type", None) or _infer_test_case_type(s)
        module = _extract_module_label(s.title, s.category)
        type_marker = "NEG" if tc_type == "negative" else "POS"
        counter_key = f"{module}_{type_marker}"
        module_counters[counter_key] = module_counters.get(counter_key, 0) + 1
        friendly_id = f"TC_{module}_{type_marker}_{module_counters[counter_key]:03d}"

        # Improve description quality
        description = _rewrite_description(s.description) or _rewrite_description(s.title) or s.title

        # Determine execution status — ONLY from explicit TestExecution records
        scenario_status = exec_status.get(s.test_id)  # "passed" | "failed" | None

        if scenario_status == "passed":
            execution_status_val = "Executed"
            result_val = "Pass"
            actual = "The application responded as expected — test passed."
            internal_status = "passed"
        elif scenario_status == "failed":
            execution_status_val = "Executed"
            result_val = "Fail"
            actual = "The application did not respond as expected — test failed."
            internal_status = "failed"
        else:
            # No TestExecution record → definitely not executed
            execution_status_val = "Not Executed"
            result_val = "N/A"
            actual = ""
            internal_status = "not_tested"

        # Test data: only for executed scenarios; use real fill values only
        if execution_status_val == "Executed" and fill_dict:
            scenario_test_data = fill_list
            scenario_test_data_json = fill_dict
            scenario_test_data_fmt = fill_formatted
        else:
            scenario_test_data = []
            scenario_test_data_json = {}
            scenario_test_data_fmt = ""

        enriched.append(
            s.model_copy(
                update={
                    "friendly_id": friendly_id,
                    "description": description,
                    "test_data": scenario_test_data,
                    "test_data_json": scenario_test_data_json,
                    "test_data_formatted": scenario_test_data_fmt,
                    "status": internal_status,
                    "execution_status": execution_status_val,
                    "result": result_val,
                    "actual_result": actual,
                    "test_case_type": tc_type,
                }
            )
        )

    return enriched


def redact_value(value: Any) -> Any:
    """Redact secrets from scalar/list/dict values used in reports."""
    if isinstance(value, dict):
        return sanitize_dict(value)
    if isinstance(value, list):
        return [redact_value(v) for v in value]
    if isinstance(value, str):
        # Bearer / Authorization first so tokens are not left behind
        text = _BEARER.sub(f"Bearer {MASK}", value)
        text = _AUTH_HEADER.sub(f"Authorization: {MASK}", text)
        text = _SECRET_ASSIGN.sub(lambda m: f"{m.group(1)}={MASK}", text)
        text = _COOKIE_PAIR.sub(lambda m: f"{m.group(1)}={MASK}", text)
        lower = text.lower()
        if text.strip().startswith(("eyJ", "sk-")):
            return MASK
        if "@" in text and "example.com" not in lower and "qa_test" not in lower:
            if not text.lower().startswith("qa_test"):
                parts = text.split("@", 1)
                if len(parts) == 2 and "." in parts[1] and " " not in parts[0]:
                    return f"{parts[0][:2]}{MASK}@{parts[1]}"
        return text
    return value


def _classification_label(bug: Defect) -> str:
    raw = bug.classification or (bug.tags[0] if bug.tags else "")
    return str(raw or "confirmed_bug")


def _bucket_suspected(bug: Defect) -> bool:
    """Suspected and observation findings stay visible, in the suspected list."""
    text = _classification_label(bug).lower()
    if "confirmed" in text:
        return False
    if bug.severity.value in {"critical", "blocker"}:
        return False
    return "suspected" in text or "observation" in text


def _payload_classification(payload: dict[str, Any]) -> str:
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    raw = payload.get("classification") or (tags[0] if tags else "")
    return str(raw or "confirmed_bug")


def _payload_is_suspected(classification: str, severity: str) -> bool:
    text = classification.lower()
    if "confirmed" in text:
        return False
    if str(severity or "").lower() in {"critical", "blocker"}:
        return False
    return "suspected" in text or "observation" in text


def entry_from_stored_payload(
    payload: dict[str, Any],
    *,
    bug_id: str,
    run_id: str,
    title: str,
    severity: str,
) -> dict[str, Any]:
    """Map a persisted Defect payload onto a bug-report row."""
    tags = payload.get("tags") if isinstance(payload.get("tags"), list) else []
    classification = _payload_classification(payload)
    module = str(payload.get("module") or (tags[1] if len(tags) > 1 else "") or "")
    steps = payload.get("steps_to_reproduce") or payload.get("steps") or []
    sev = str(payload.get("severity") or severity or "major")
    return {
        "bug_id": str(payload.get("bug_id") or bug_id),
        "title": str(payload.get("title") or title or "Untitled defect"),
        "module": module,
        "page": str(payload.get("page_title") or payload.get("page") or ""),
        "url": sanitize_url(str(payload.get("page_url") or payload.get("url") or "")),
        "classification": classification,
        "severity": sev,
        "priority": str(payload.get("priority") or "medium"),
        "preconditions": list(payload.get("preconditions") or []),
        "test_data": str(redact_value(payload.get("test_data") or "")),
        "steps_to_reproduce": [str(step) for step in steps] if isinstance(steps, list) else [],
        "expected_result": str(payload.get("expected") or payload.get("expected_result") or ""),
        "actual_result": str(redact_value(payload.get("actual") or payload.get("actual_result") or "")),
        "business_impact": str(payload.get("business_impact") or ""),
        "possible_root_cause_hypothesis": str(
            payload.get("possible_root_cause") or payload.get("possible_root_cause_hypothesis") or ""
        ),
        "confidence": float(payload.get("confidence") or 0.0),
        "screenshot_evidence": list(payload.get("screenshot_evidence") or []),
        "trace_evidence": list(payload.get("trace_evidence") or []),
        "console_evidence": list(payload.get("console_evidence") or []),
        "network_evidence": list(payload.get("network_evidence") or []),
        "discovery_timestamp": payload.get("created_at") or payload.get("discovery_timestamp"),
        "run_id": str(payload.get("run_id") or run_id),
    }


def merge_persisted_bugs(report: dict[str, Any], records: list[Any]) -> dict[str, Any]:
    """Add every stored bug the in-memory report dropped.

    The live report dedupes by title and page, and it used to omit observation
    findings. The database keeps each record. The Bugs tab, final report, and
    CSV should list all of them.
    """
    confirmed = [dict(item) for item in (report.get("confirmed_bugs") or [])]
    suspected = [dict(item) for item in (report.get("suspected_bugs") or [])]
    seen = {str(item.get("bug_id")) for item in confirmed + suspected if item.get("bug_id")}
    for record in records:
        raw = getattr(record, "payload_json", None) or "{}"
        try:
            payload = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        except json.JSONDecodeError:
            payload = {}
        entry = entry_from_stored_payload(
            payload,
            bug_id=str(getattr(record, "id", "") or payload.get("bug_id") or ""),
            run_id=str(getattr(record, "run_id", "") or report.get("run_id") or ""),
            title=str(getattr(record, "title", "") or ""),
            severity=str(getattr(record, "severity", "") or ""),
        )
        if not entry["bug_id"] or not entry["title"] or entry["bug_id"] in seen:
            continue
        seen.add(entry["bug_id"])
        if _payload_is_suspected(entry["classification"], entry["severity"]):
            suspected.append(entry)
        else:
            confirmed.append(entry)
    report["confirmed_bugs"] = confirmed
    report["suspected_bugs"] = suspected
    coverage = report.get("coverage")
    if isinstance(coverage, dict):
        coverage["bugs_found"] = len(confirmed) + len(suspected)
        coverage["suspected_issues"] = max(int(coverage.get("suspected_issues") or 0), len(suspected))
        report["coverage"] = coverage
    return report


class ReportBuilder:
    """Compile synchronized structured documentation from agent memory only."""

    def build(self, memory: RunMemory) -> FinalReport:
        if memory.app_store:
            memory.app_store.refresh_purpose()
            memory.app_store.consolidate_modules()
        # Only commit workflows when the run is stopping — mid-run doc sync must
        # not finalize partial journeys (which previously duplicated the catalog).
        if not memory.workflows and memory.stop_reason:
            memory.finalize_workflow()
        memory.rebuild_journeys()

        coverage = self.compute_coverage(memory)
        app = memory.app_store.model if memory.app_store else None

        # Prefer canonical visited pages/edges for navigation map
        nav_pages = memory.pages
        nav_edges = memory.navigation_edges
        if app and app.pages:
            from app.schemas import PageState as PS
            from app.agent.memory import NavigationEdge as NE

            from app.application.coverage import canonical_pages

            nav_pages = [
                PS(
                    page_id=p.id,
                    url=p.canonical_url,
                    title=p.heading or p.title,
                    headings=[p.heading] if p.heading else [],
                )
                for p in canonical_pages(app)
            ]
            nav_edges = []
            for e in app.navigation_edges:
                src = app.page_by_id(e.from_page_id)
                dst = app.page_by_id(e.to_page_id)
                if not src or not dst:
                    continue
                nav_edges.append(
                    NE(
                        source_url=src.canonical_url,
                        target_url=dst.canonical_url,
                        via_action=e.action_type,
                        element_id=e.element_id,
                        action_label=e.action_label or e.action_type,
                    )
                )
            # Fall back to memory edges if store edges empty
            if not nav_edges and memory.navigation_edges:
                nav_edges = list(memory.navigation_edges)

        journeys = list(memory.user_journeys)
        if not journeys and nav_edges:
            journeys = [
                f"{e.source_url} -- {e.action_label or e.via_action} --> {e.target_url}"
                for e in nav_edges
            ]

        mermaid = {
            "navigation": build_navigation_mermaid(nav_pages, nav_edges),
            "actions": build_workflow_mermaid(memory.actions),
            "user_journey": build_user_journey_mermaid(
                journeys or [p.title or p.url for p in memory.pages[:8]]
            ),
        }
        for wf in memory.workflows:
            mermaid[f"workflow_{wf.workflow_id[:8]}"] = build_named_workflow_mermaid(wf)

        confirmed = self._confirmed_bug_entries(memory)
        suspected = self._suspected_bug_entries(memory)
        page_inv = self._page_inventory(memory)
        forms_inv = self._forms_inventory(memory)
        tables_inv = self._tables_inventory(memory)
        evidence_index = self._evidence_index(memory)
        console_errors = self._collect_console(memory)
        network_errors = self._collect_network(memory)
        business_rules = self._business_rules(memory)
        ux_notes = self._ux_observations(memory)
        limitations = self._known_limitations(memory)
        next_areas = self._recommended_next(memory, coverage)

        # Purpose: prefer store inference; never leave Under analysis when evidence exists
        purpose = "Under analysis"
        if app and app.purpose and app.purpose_confidence >= 0.45:
            purpose = app.purpose
        elif memory.domain_purpose and memory.domain_purpose != "Under analysis":
            purpose = memory.domain_purpose
        elif memory.domain_hypothesis and memory.domain_hypothesis != "Under analysis":
            purpose = memory.domain_hypothesis

        overview = memory.product_overview or ProductOverview(
            run_id=memory.run_id,
            product_name=(
                app.application_name if app and app.application_name else self._guess_name(memory)
            ),
            summary=f"Exploratory QA of {sanitize_url(memory.start_url)}",
            primary_purpose=purpose,
        )
        if app and app.purpose and app.purpose_confidence >= 0.45:
            overview.primary_purpose = app.purpose
            overview.product_name = app.application_name or overview.product_name
            overview.key_features = list(
                dict.fromkeys([*(overview.key_features or []), *[m.name for m in app.modules]])
            )[:12]
            purpose = app.purpose

        domain = (
            (app.inferred_domain if app and app.inferred_domain else "")
            or memory.domain_hypothesis
            or memory.domain_purpose
            or f"Inferred from exploration of {sanitize_url(memory.start_url)}"
        )
        if domain == "Under analysis" and purpose != "Under analysis":
            domain = purpose

        executive = self._clean_text(
            self._executive_summary(memory, coverage, confirmed, suspected, overview)
        )

        modules = (
            memory.app_store.sync_legacy_modules()
            if memory.app_store and memory.app_store.model.modules
            else list(memory.modules)
        )

        # Scenarios: prefer canonical store (deduped); fall back to memory list
        scenarios = list(memory.scenarios)
        if app and app.scenarios:
            from app.schemas import TestScenario as TS

            scenarios = [
                TS(
                    test_id=s.id,
                    title=s.title,
                    description=s.description,
                    category=s.category,
                    steps=list(s.steps),
                    expected_results=list(s.expected_results),
                )
                for s in app.scenarios
            ]

        # ── Enrich scenarios with test_data + status from actual actions ──────
        scenarios = _enrich_scenarios_from_actions(scenarios, memory)

        # Drop smoke / off-scope / wrong-type cases when the operator asked
        # for a specific feature or test-case type (e.g. signup + positive).
        from app.agent.test_scope import case_matches_request

        cfg = getattr(memory, "configuration", None)
        objective = getattr(memory, "testing_objective", None)
        focus = list(getattr(cfg, "focus_modules", None) or [])
        types = list(getattr(cfg, "test_case_types", None) or [])
        if objective or focus or types:
            scenarios = [
                s
                for s in scenarios
                if case_matches_request(
                    title=getattr(s, "title", ""),
                    description=getattr(s, "description", ""),
                    category=getattr(s, "category", ""),
                    test_case_type=getattr(s, "test_case_type", ""),
                    objective=objective,
                    focus_modules=focus,
                    test_case_types=types,
                )
            ]

        investigation_stop_report = (
            memory.investigation_stop_report() if hasattr(memory, "investigation_stop_report") else None
        )

        runtime_dict = {
            **build_runtime_info(
                decisions_validated=getattr(memory, "decisions_validated", 0),
                decisions_rejected=getattr(memory, "decisions_rejected", 0),
                actions_executed=len(
                    [a for a in memory.actions if a.action.action.value != "finish"]
                ),
                stop_reason=memory.stop_reason,
                adapter_connection_status=getattr(
                    memory, "adapter_connection_status", None
                ),
                adapter_capabilities=getattr(memory, "adapter_capabilities", None)
                or None,
                adapter_execution_failures=int(
                    getattr(memory, "adapter_execution_failures", 0) or 0
                ),
                unsupported_evidence_features=list(
                    getattr(memory, "unsupported_evidence_features", None) or []
                ),
                browser_adapter_id=getattr(memory, "browser_adapter_id", None),
                enable_autonomous_investigation=getattr(memory, "investigation_engine", None) is not None,
            ),
            "authenticated": bool(getattr(memory, "authenticated", False)),
            "auth_status": getattr(memory, "auth_status", "unknown"),
            "auth_method": getattr(memory, "auth_method", None),
            "auth_blocker": getattr(memory, "auth_blocker", None),
            "anonymous_page_count": int(getattr(memory, "anonymous_page_count", 0) or 0),
            "authenticated_page_count": int(getattr(memory, "authenticated_page_count", 0) or 0),
            "auth_signals": list(getattr(memory, "auth_signals", None) or [])[:12],
            "investigation_stop_report": (
                investigation_stop_report.model_dump(mode="json") if investigation_stop_report is not None else None
            ),
        }

        # Reasoning-engine reporting -- see the block of private helpers
        # right before _default_regression for what each of these does and
        # degrades to when the corresponding engine isn't attached.
        scenario_rows = self._scenario_rows(memory)
        blocked_scenarios, unexecuted_scenarios = self._blocked_and_unexecuted_scenarios(memory, scenario_rows)

        report = FinalReport(
            run_id=memory.run_id,
            target_url=sanitize_url(memory.start_url),
            testing_objective=getattr(memory, "testing_objective", None),
            executive_summary=executive,
            product_overview=overview,
            inferred_business_domain=self._clean_text(domain),
            application_purpose=self._clean_text(purpose),
            modules=modules,
            navigation_structure=mermaid["navigation"],
            page_inventory=page_inv,
            forms_inventory=forms_inv,
            tables_inventory=tables_inv,
            role_observations=list(memory.roles),
            workflows=list(memory.workflows),
            user_journeys=journeys,
            business_rule_observations=business_rules,
            test_scenarios=scenarios,
            test_executions=list(memory.executions),
            confirmed_bugs=confirmed,
            suspected_bugs=suspected,
            ux_quality_observations=ux_notes,
            coverage=coverage,
            regression_checklist=list(memory.regression_checklist)
            or self._default_regression(),
            console_errors=console_errors,
            network_errors=network_errors,
            evidence_index=evidence_index,
            known_limitations=limitations,
            recommended_next_testing_areas=next_areas,
            mermaid=mermaid,
            runtime=runtime_dict,
            summary=executive,
            domain_purpose=self._clean_text(domain),
            navigation_map=mermaid["navigation"],
            bugs=list(memory.bugs),
            mermaid_diagrams=list(mermaid.values()),
            knowledge_graph_summary=self._knowledge_graph_summary(memory),
            generated_goals=self._generated_goals_summary(memory),
            scenario_planning_summary=self._scenario_planning_summary(memory, scenario_rows),
            qa_strategy_summary=self._qa_strategy_summary(memory),
            autonomous_investigation_summary=self._autonomous_investigation_summary(memory),
            crud_workflow_coverage=self._crud_workflow_coverage(memory, coverage),
            form_lifecycle_summary=self._form_lifecycle_summary(memory),
            collection_coverage=self._collection_coverage(coverage),
            executed_assertions=self._executed_assertions(memory),
            blocked_scenarios=blocked_scenarios,
            unexecuted_scenarios=unexecuted_scenarios,
            temporary_records=self._temporary_records_summary(memory),
            model_context_summary=(
                memory.model_context_summary() if hasattr(memory, "model_context_summary") else {}
            ),
            submission_outcome_summary=(
                memory.submission_outcome_summary() if hasattr(memory, "submission_outcome_summary") else {}
            ),
            write_permission_gap=(
                memory.write_permission_gap() if hasattr(memory, "write_permission_gap") else {}
            ),
            cleanup_status=self._cleanup_status_summary(memory, coverage),
            stop_summary=self._stop_summary(memory, coverage, unexecuted_scenarios),
            capability_disclosure=self._capability_disclosure(memory, runtime_dict),
        )
        report.sections_markdown = self.build_sections_markdown(report)
        # Enforce page-count consistency (inventory == discovered)
        if report.coverage is not None:
            inv_n = len(report.page_inventory)
            if report.coverage.pages_discovered != inv_n:
                logger.warning(
                    "Page count mismatch repaired: coverage=%s inventory=%s",
                    report.coverage.pages_discovered,
                    inv_n,
                )
                report.coverage.pages_discovered = inv_n
                explored = min(report.coverage.pages_explored, inv_n)
                report.coverage.pages_explored = explored
                if inv_n > 0:
                    pct = round(min(100.0, (explored / inv_n) * 100.0), 1)
                    report.coverage.observed_coverage_pct = pct
                    report.coverage.explored_coverage_pct = pct
                else:
                    report.coverage.observed_coverage_pct = 0.0
                    report.coverage.explored_coverage_pct = 0.0
                # Refresh coverage section body
                report.sections_markdown = self.build_sections_markdown(report)
        logger.info("Structured report built for run %s", memory.run_id)
        return report

    def compute_coverage(self, memory: RunMemory) -> CoverageRecord:
        # Single coverage service (app.application.coverage, via RunMemory.coverage)
        # — no second, divergent calculation lives here.
        return memory.coverage()

    def build_sections_markdown(self, report: FinalReport) -> dict[str, str]:
        cov = report.coverage
        sections: dict[str, str] = {}
        # Bodies only — UI already shows the section title (avoid duplicated headings)

        def _body(text: str, title: str) -> str:
            raw = (text or "").strip()
            if raw.startswith(f"## {title}"):
                raw = raw[len(f"## {title}") :].lstrip("\n")
            return raw

        sections["Executive Summary"] = _body(report.executive_summary, "Executive Summary")
        sections["Run Environment"] = self._runtime_section(report)
        sections["Configuration and Capability Disclosure"] = self._capability_disclosure_md(report.capability_disclosure)
        po = report.product_overview
        sections["Product Overview"] = (
            (
                f"**Name:** {po.product_name}\n\n{po.summary}\n\n"
                f"**Purpose:** {po.primary_purpose}\n\n"
                f"**Features observed:** {', '.join(po.key_features) or 'n/a'}"
            )
            if po
            else "_No product overview recorded._"
        )
        sections["Inferred Business Domain"] = report.inferred_business_domain or "_Unknown_"
        sections["Application Purpose"] = report.application_purpose or "_Unknown_"
        mod_lines: list[str] = []
        if not report.modules:
            mod_lines.append("_No modules inferred._")
        for m in report.modules:
            # Count only page_ids that still exist in the inventory
            inv_ids = {p.get("page_id") for p in report.page_inventory}
            n_pages = len([pid for pid in set(m.page_ids) if pid in inv_ids]) or len(
                [p for p in report.page_inventory if p.get("module_id") == m.module_id]
            )
            mod_lines.append(f"- **{m.name}**: {m.description} ({n_pages} pages)")
        sections["Modules"] = "\n".join(mod_lines)
        sections["Navigation Structure"] = (
            "```mermaid\n"
            + (report.navigation_structure or "flowchart TD\n  A[Empty]")
            + "\n```"
        )
        sections["Page Inventory"] = self._md_table(
            "Page Inventory",
            ["Title", "URL", "Type"],
            [
                [p.get("title", ""), p.get("url", ""), p.get("page_type", "")]
                for p in report.page_inventory
            ],
        )
        sections["Forms Inventory"] = self._md_table(
            "Forms Inventory",
            ["Form ID", "Page URL", "Fields", "Method"],
            [
                [
                    f.get("form_id", ""),
                    f.get("page_url", ""),
                    str(f.get("field_count", 0)),
                    f.get("method", "") or "",
                ]
                for f in report.forms_inventory
            ],
        )
        sections["Form Lifecycle"] = self._form_lifecycle_md(report.form_lifecycle_summary)
        sections["Tables Inventory"] = self._md_table(
            "Tables Inventory",
            ["Table ID", "Page URL", "Headers", "Rows"],
            [
                [
                    t.get("table_id", ""),
                    t.get("page_url", ""),
                    ", ".join(t.get("headers") or [])[:80],
                    str(t.get("row_count", 0)),
                ]
                for t in report.tables_inventory
            ],
        )
        sections["Collection/Grid Coverage"] = self._collection_coverage_md(report.collection_coverage)
        role_lines: list[str] = []
        if not report.role_observations:
            role_lines.append("_No role observations in this single-session run._")
        for r in report.role_observations:
            role_lines.append(
                f"- **{r.role_name}**: {', '.join(r.observed_capabilities) or 'n/a'}"
            )
        sections["Role and Permission Observations"] = "\n".join(role_lines)
        wf_lines: list[str] = []
        if not report.workflows:
            wf_lines.append("_No workflows discovered._")
        for wf in report.workflows:
            wf_lines.append(f"**{wf.name}**")
            wf_lines.append(f"- Start: `{wf.starting_page}`")
            for step in wf.steps:
                wf_lines.append(f"  {step.order}. {step.action} — {step.description}")
        sections["Workflow Catalog"] = "\n".join(wf_lines)
        sections["User Journeys"] = (
            "\n".join(f"- {j}" for j in report.user_journeys) or "_None recorded._"
        )
        sections["Business Rule Observations"] = (
            "\n".join(f"- {b}" for b in report.business_rule_observations)
            or "_None observed deterministically._"
        )
        sections["Knowledge Graph Summary"] = self._knowledge_graph_md(report.knowledge_graph_summary)
        sections["Generated Goals"] = self._generated_goals_md(report.generated_goals)
        sections["Scenario Planning Summary"] = self._scenario_planning_md(report.scenario_planning_summary)
        sections["QA Strategy Summary"] = self._qa_strategy_md(report.qa_strategy_summary)
        sections["Autonomous Investigation Summary"] = self._autonomous_investigation_md(
            report.autonomous_investigation_summary
        )
        sections["CRUD Workflow Coverage"] = self._crud_workflow_coverage_md(report.crud_workflow_coverage)
        # Legacy app.agent.tester.Tester scenarios -- clearly labeled as the
        # OLDER, form/field-driven generator, distinct from Scenario Planning
        # above. Every not_run entry here gets a reason (see Unexecuted
        # Scenarios below), never a bare unexplained "not_run".
        exec_status_by_id = {ex.test_id: ex.status for ex in report.test_executions}
        generated = [
            f"- **{s.title}** ({s.category}) — {exec_status_by_id.get(s.test_id, 'not_run')}"
            for s in report.test_scenarios
        ]
        sections["Test Scenarios"] = "\n".join(generated) or "_No legacy scenarios generated this run._"
        exec_counts: dict[str, int] = {}
        for ex in report.test_executions:
            exec_counts[ex.status] = exec_counts.get(ex.status, 0) + 1
        sections["Test Execution Results"] = (
            "\n".join(f"- {k}: {v}" for k, v in sorted(exec_counts.items()))
            or "_No executions (generated scenarios remain not_run until executed)._"
        )
        sections["Executed Assertions"] = self._executed_assertions_md(report.executed_assertions)
        sections["Blocked Scenarios"] = self._scenario_reason_md(report.blocked_scenarios, empty_note="_No scenarios are currently blocked._")
        sections["Unexecuted Scenarios"] = self._scenario_reason_md(
            report.unexecuted_scenarios, empty_note="_Every generated scenario reached a terminal executed outcome._"
        )
        sections["Confirmed Bugs"] = self._bugs_md("Confirmed Bugs", report.confirmed_bugs)
        sections["Suspected Bugs"] = self._bugs_md("Suspected Bugs", report.suspected_bugs)
        sections["UX and Quality Observations"] = (
            "\n".join(f"- {o}" for o in report.ux_quality_observations) or "_None._"
        )
        if cov:
            rt = report.runtime or {}
            anon = int(rt.get("anonymous_page_count") or 0)
            authed = int(rt.get("authenticated_page_count") or 0)
            auth_status = rt.get("auth_status") or "unknown"
            auth_method = rt.get("auth_method")
            auth_blocker = rt.get("auth_blocker")
            sections["Coverage Summary"] = (
                f"- Pages discovered: {cov.pages_discovered}\n"
                f"- Pages explored: {cov.pages_explored}\n"
                f"- Observed anonymous surface coverage: "
                f"{'partial' if anon else 'none'} ({anon} anonymous page(s))\n"
                f"- Authenticated exploration coverage: "
                f"{'partial' if authed else 'none'} ({authed} authenticated page(s))\n"
                f"- Known-application coverage confidence: exploratory / incomplete\n"
                f"- Overall discovery completeness: not complete application coverage\n"
                f"- Observed coverage (visited/discovered ratio): {cov.observed_coverage_pct}%\n"
                f"- Explored coverage: {cov.explored_coverage_pct}%\n"
                f"- Executed coverage: {cov.executed_coverage_pct}%\n"
                f"- Authentication status: {auth_status}\n"
                f"- Authentication method: {auth_method or 'n/a'}\n"
                f"- Authentication blocker: {auth_blocker or 'none'}\n"
                f"- Forms discovered/inspected/tested: "
                f"{cov.forms_discovered}/{cov.forms_inspected}/{cov.forms_tested}\n"
                f"- Tables discovered/inspected: {cov.tables_discovered}/{cov.tables_inspected}\n"
                f"- Tests generated/executed (legacy Tester): {cov.tests_generated}/{cov.tests_executed} "
                f"(passed={cov.passed}, failed={cov.failed})\n"
                f"- Local-control coverage: {cov.local_controls_exercised}/{cov.local_controls_discovered} "
                f"({cov.local_control_coverage_pct}%)\n"
                f"- Collections/grids discovered/inspected: "
                f"{cov.collections_discovered}/{cov.collections_inspected} ({cov.collection_coverage_pct}%)\n"
                f"- Goals generated: {cov.goals_generated}\n"
                f"- Scenario Planning scenarios generated/executable/executed: "
                f"{cov.scenarios_generated}/{cov.scenarios_executable}/{cov.scenarios_executed} "
                f"(passed={cov.scenarios_passed}, failed={cov.scenarios_failed}, blocked={cov.scenarios_blocked})\n"
                f"- CRUD create discovered/executed: {cov.crud_create_discovered}/{cov.crud_create_executed}\n"
                f"- CRUD read (collection) discovered/executed: {cov.crud_read_discovered}/{cov.crud_read_executed}\n"
                f"- CRUD update discovered/executed: {cov.crud_update_discovered}/{cov.crud_update_executed}\n"
                f"- CRUD delete discovered/executed: {cov.crud_delete_discovered}/{cov.crud_delete_executed}\n"
                f"- Verification coverage (assertions evaluated/supported): "
                f"{cov.assertions_evaluated}/{cov.assertions_supported} "
                f"(contradicted={cov.assertions_contradicted}, inconclusive={cov.assertions_inconclusive})\n"
                f"- Cleanup coverage (succeeded/pending/manual): "
                f"{cov.cleanup_succeeded}/{cov.cleanup_pending}/{cov.cleanup_manual_required} "
                f"({cov.cleanup_coverage_pct}%)\n"
                f"- Bugs / suspected / observations: "
                f"{cov.bugs_found}/{cov.suspected_issues}/{cov.observations}\n"
                f"- Actions executed: {cov.action_budget_used}\n\n"
                f"_{cov.disclaimer}_"
            )
        else:
            sections["Coverage Summary"] = "_No coverage data._"
        sections["Temporary Records"] = self._temporary_records_md(report.temporary_records)
        sections["Cleanup Status"] = self._cleanup_status_md(report.cleanup_status)
        sections["Regression Checklist"] = "\n".join(
            f"- [ ] {item}" for item in report.regression_checklist
        )
        sections["Console Errors"] = (
            "\n".join(f"- `{e}`" for e in report.console_errors) or "_None recorded._"
        )
        sections["Network Errors"] = (
            "\n".join(f"- `{e}`" for e in report.network_errors) or "_None recorded._"
        )
        sections["Evidence Index"] = self._md_table(
            "Evidence Index",
            ["ID", "Kind", "Relative path", "URL"],
            [
                [
                    e.get("evidence_id", ""),
                    e.get("kind", ""),
                    e.get("relative_path") or e.get("path", ""),
                    e.get("public_url", ""),
                ]
                for e in report.evidence_index
            ],
        )
        sections["Stop Reason"] = self._stop_summary_md(report.stop_summary)
        sections["Known Limitations"] = "\n".join(
            f"- {x}" for x in report.known_limitations
        )
        sections["Recommended Next Testing Areas"] = "\n".join(
            f"- {x}" for x in report.recommended_next_testing_areas
        )
        return sections

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _runtime_section(self, report: FinalReport) -> str:
        rt = report.runtime or {}
        shot = "supported" if rt.get("screenshots_supported", True) else "unsupported"
        console = (
            "supported" if rt.get("console_capture_supported", True) else "unsupported"
        )
        network = (
            "supported" if rt.get("network_capture_supported", True) else "unsupported"
        )
        conn = rt.get("adapter_connection_status") or "n/a"
        adapter_id = str(rt.get("browser_adapter_id") or "")
        conn_label = (
            "MCP connection"
            if adapter_id.endswith("mcp") or "MCP" in str(rt.get("browser_adapter") or "")
            else "Adapter connection"
        )
        lines = [
            f"- **Provider:** {rt.get('provider') or 'n/a'}",
            f"- **Model:** {rt.get('model') or 'None'}",
            f"- **Browser adapter:** {rt.get('browser_adapter') or 'n/a'}",
            f"- **{conn_label}:** {conn}",
            f"- **Screenshots:** {shot}",
            f"- **Console capture:** {console}",
            f"- **Network capture:** {network}",
            f"- **Adapter execution failures:** {rt.get('adapter_execution_failures', 0)}",
            f"- **Run mode:** {rt.get('run_mode') or 'n/a'}",
            f"- **Validated decisions:** {rt.get('decisions_validated', 0)}",
            f"- **Rejected decisions:** {rt.get('decisions_rejected', 0)}",
            f"- **Executed actions:** {rt.get('actions_executed', 0)}",
        ]
        unsupported = rt.get("unsupported_evidence_features") or []
        if unsupported:
            lines.append(
                f"- **Unsupported evidence features:** {', '.join(unsupported)}"
            )
        if rt.get("api_base_url"):
            lines.append(f"- **API base URL:** `{rt.get('api_base_url')}`")
        if rt.get("stop_reason"):
            lines.append(f"- **Stop reason:** {rt.get('stop_reason')}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Markdown renderers for the reasoning-engine / capability / blocked-work
    # sections — one per new FinalReport field, each degrading to a clear
    # "unavailable" note rather than an empty/misleading table.
    # ------------------------------------------------------------------

    def _capability_disclosure_md(self, d: dict[str, Any]) -> str:
        lines = [
            f"- **Provider:** {d.get('provider') or 'n/a'}",
            f"- **Model:** {d.get('model') or 'None'}",
            f"- **Provider capability mode:** {d.get('provider_capability_mode') or 'n/a'}",
            f"- **Browser adapter:** {d.get('browser_adapter') or 'n/a'}",
            f"- **Autonomous investigation:** {'enabled' if d.get('autonomous_investigation_enabled') else 'disabled'}",
            f"- **Controlled writes:** {'enabled' if d.get('controlled_writes_enabled') else 'disabled'}",
            f"- **Safe mode:** {'on' if d.get('safe_mode') else 'off'}",
            f"- **Destructive actions:** {'allowed' if d.get('destructive_actions_allowed') else 'blocked'}",
            f"- **Actor switching:** {'available' if d.get('actor_switching_available') else 'unavailable'} "
            f"({d.get('actor_profile_count', 0)} credential profile(s))",
            f"- **Visual perception:** {'enabled' if d.get('visual_perception_enabled') else 'disabled'}",
            f"- **Test-data generation:** {'enabled' if d.get('test_data_generation_enabled') else 'disabled'}",
        ]
        if d.get("mock_exploration_only_notice"):
            lines.append(f"\n> **{d['mock_exploration_only_notice']}**")
        return "\n".join(lines)

    def _form_lifecycle_md(self, d: dict[str, Any]) -> str:
        if not d.get("forms_discovered"):
            return "_No forms discovered this run._"
        lines = [f"- Forms discovered: {d['forms_discovered']}", "", "**By lifecycle state:**"]
        for state, n in sorted((d.get("by_lifecycle_state") or {}).items()):
            lines.append(f"- {state}: {n}")
        reasons = d.get("by_not_tested_reason") or {}
        if reasons:
            lines.append("")
            lines.append("**Not-tested reasons:**")
            for reason, n in sorted(reasons.items()):
                lines.append(f"- {reason}: {n}")
        return "\n".join(lines)

    def _collection_coverage_md(self, d: dict[str, Any]) -> str:
        if not d.get("collections_discovered"):
            return "_No record-collection (grid/table/card-list) surfaces detected this run._"
        return (
            f"- Collections discovered: {d['collections_discovered']}\n"
            f"- Collections inspected (CRUD-reasoned-about): {d['collections_inspected']}\n"
            f"- Collection coverage: {d['collection_coverage_pct']}%"
        )

    def _knowledge_graph_md(self, d: dict[str, Any]) -> str:
        if not d.get("available"):
            return f"_{d.get('note', 'Not available this run.')}_"
        lines = [
            f"- Graph version: {d['graph_version']}",
            f"- Nodes / edges: {d['total_nodes']} / {d['total_edges']}",
            f"- Node counts by type: {d['node_counts_by_type'] or 'n/a'}",
            f"- Edge counts by type: {d['edge_counts_by_type'] or 'n/a'}",
            f"- Observed / inferred / contradicted / stale edges: "
            f"{d['observed_edge_count']} / {d['inferred_edge_count']} / {d['contradicted_edge_count']} / {d['stale_edge_count']}",
            f"- Unresolved references: {d['unresolved_reference_count']}",
            f"- Consistency issues: {d['consistency_issue_count']}",
            f"- Gaps: {d['gap_count']}",
            f"- Connected components / isolated nodes: {d['connected_component_count']} / {d['isolated_node_count']}",
        ]
        return "\n".join(lines)

    def _generated_goals_md(self, d: dict[str, Any]) -> str:
        if not d.get("available"):
            return f"_{d.get('note', 'Not available this run.')}_"
        lines = [
            f"- Total goals: {d['total_goals']}",
            f"- By type: {d['goals_by_type'] or 'n/a'}",
            f"- By status: {d['goals_by_status'] or 'n/a'}",
            f"- Average priority: {d['average_priority']}",
            f"- High-priority / blocked: {d['high_priority_count']} / {d['blocked_count']}",
            "",
            "**Top goals:**",
        ]
        for g in d.get("top_goals") or []:
            lines.append(
                f"- [{g['priority_score']}] {g['title']} ({g['goal_type']}, {g['goal_status']})"
            )
        if not d.get("top_goals"):
            lines.append("_None._")
        return "\n".join(lines)

    def _scenario_planning_md(self, d: dict[str, Any]) -> str:
        if not d.get("available"):
            return f"_{d.get('note', 'Not available this run.')}_"
        return (
            f"- Total scenarios: {d['total_scenarios']}\n"
            f"- By type: {d['scenarios_by_type'] or 'n/a'}\n"
            f"- By status: {d['scenarios_by_status'] or 'n/a'}\n"
            f"- By feasibility: {d['scenarios_by_feasibility'] or 'n/a'}\n"
            f"- By risk: {d['scenarios_by_risk'] or 'n/a'}\n"
            f"- Read-only / mutating / cross-actor / high-risk: "
            f"{d['read_only_count']} / {d['mutating_count']} / {d['cross_actor_count']} / {d['high_risk_count']}\n"
            f"- Average complexity / confidence-gain: {d['average_complexity_score']} / {d['average_confidence_gain']}"
        )

    def _qa_strategy_md(self, d: dict[str, Any]) -> str:
        if not d.get("available"):
            return f"_{d.get('note', 'Not available this run.')}_"
        return (
            f"- Total candidates: {d['total_candidates']}\n"
            f"- By queue: {d['candidates_by_queue'] or 'n/a'}\n"
            f"- By recommended action: {d['candidates_by_action'] or 'n/a'}\n"
            f"- Batches: {d['batch_count']}\n"
            f"- Average priority / risk: {d['average_priority']} / {d['average_risk']}\n"
            f"- Policy used: {d['policy_used'] or 'n/a'}"
        )

    def _autonomous_investigation_md(self, d: dict[str, Any]) -> str:
        if not d.get("available"):
            return f"_{d.get('note', 'Autonomous Investigation is disabled this run.')}_"
        lines = [
            f"- Total investigations: {d['total_investigations']}",
            f"- By outcome: {d['investigations_by_outcome'] or 'n/a'}",
            f"- Steps executed: {d['total_steps_executed']}",
            f"- Assertions evaluated (supported/contradicted/inconclusive): "
            f"{d['total_assertions_evaluated']} "
            f"({d['supported_assertion_count']}/{d['contradicted_assertion_count']}/{d['inconclusive_assertion_count']})",
            f"- Recovery attempts: {d['total_recovery_attempts']}",
        ]
        stop = d.get("stop_report")
        if stop:
            lines.append("")
            lines.append("**Stop report:**")
            lines.append(f"- Stop reason: {stop.get('stop_reason')}")
            lines.append(f"- Unexecuted scenario count: {stop.get('unexecuted_scenario_count')}")
            lines.append(f"- Recommended next action: {stop.get('recommended_next_action') or 'n/a'}")
        return "\n".join(lines)

    def _crud_workflow_coverage_md(self, d: dict[str, Any]) -> str:
        if not d:
            return "_No CRUD workflow coverage data._"
        lines = []
        for op in ("create", "read", "update", "delete"):
            block = d.get(op) or {}
            lines.append(f"- **{op.capitalize()}:** discovered={block.get('discovered', 0)}, executed={block.get('executed', 0)}")
        if d.get("read", {}).get("note"):
            lines.append(f"  _{d['read']['note']}_")
        return "\n".join(lines)

    def _executed_assertions_md(self, rows: list[dict[str, Any]]) -> str:
        if not rows:
            return "_No autonomous-investigation assertions were evaluated this run._"
        return self._md_table(
            "Executed Assertions",
            ["Scenario", "Subject", "Operator", "Outcome", "Observed", "Expected", "Confidence"],
            [
                [
                    r.get("scenario_id", ""), r.get("subject_id", ""), r.get("operator", ""),
                    r.get("outcome", ""), r.get("observed_value", ""), r.get("expected_value", ""),
                    str(r.get("confidence", "")),
                ]
                for r in rows
            ],
        )

    def _scenario_reason_md(self, rows: list[dict[str, Any]], *, empty_note: str) -> str:
        if not rows:
            return empty_note
        return self._md_table(
            "Scenarios",
            ["Title", "Status", "Reason"],
            [[r.get("title", ""), r.get("status", ""), r.get("reason", "other")] for r in rows],
        )

    def _temporary_records_md(self, d: dict[str, Any]) -> str:
        if not d.get("available") or not d.get("total_records"):
            return "_No temporary test records were created this run._"
        lines = [f"- Total records: {d['total_records']}", f"- By state: {d.get('by_state') or 'n/a'}"]
        return "\n".join(lines)

    def _cleanup_status_md(self, d: dict[str, Any]) -> str:
        lines = [
            f"- Succeeded: {d.get('succeeded', 0)}",
            f"- Pending: {d.get('pending', 0)}",
            f"- Manual cleanup required: {d.get('manual_required', 0)}",
            f"- Cleanup coverage: {d.get('cleanup_coverage_pct', 0.0)}%",
        ]
        instructions = d.get("manual_cleanup_instructions") or []
        if instructions:
            lines.append("")
            lines.append("**Manual cleanup instructions:**")
            for instr in instructions:
                lines.append(f"- {instr.get('instructions', instr)}")
        return "\n".join(lines)

    def _stop_summary_md(self, d: dict[str, Any]) -> str:
        untested = d.get("untested_summary") or {}
        gap = d.get("write_permission_gap") or {}
        lines = [
            f"- **Why did the run stop?** {d.get('stop_reason', 'n/a')}",
        ]
        if gap:
            lines.append(f"- **Blocked by configuration:** {gap.get('explanation', '')}")
        lines += [
            f"- **Last meaningful operation:** {d.get('last_meaningful_operation', 'n/a')}",
            f"- **What remained untested?** {untested.get('unexecuted_scenario_count', 0)} scenario(s), "
            f"{untested.get('forms_not_tested', 0)} form(s), {untested.get('crud_operations_not_executed', 0)} CRUD operation(s)",
            f"- **What prevented execution?** {', '.join(d.get('what_prevented_execution') or ['n/a'])}",
            f"- **Temporary records left behind:** {d.get('temporary_records_left_behind', 0)}",
            "",
            "**Recommended configuration changes for the next run:**",
        ]
        for suggestion in d.get("recommended_configuration_changes") or []:
            lines.append(f"- {suggestion}")
        return "\n".join(lines)

    def _executive_summary(
        self,
        memory: RunMemory,
        coverage: CoverageRecord,
        confirmed: list[BugReportEntry],
        suspected: list[BugReportEntry],
        overview: ProductOverview,
    ) -> str:
        stop = (memory.stop_reason or "").strip()
        stop_clause = f" Stop reason: {stop}." if stop else ""
        return (
            f"Exploratory QA of **{overview.product_name}** at `{sanitize_url(memory.start_url)}` "
            f"visited {coverage.pages_discovered} unique page(s), explored {coverage.pages_explored}, "
            f"executed {coverage.action_budget_used} action(s), "
            f"generated {coverage.tests_generated} scenario(s), and recorded "
            f"{len(confirmed)} confirmed bug(s) and {len(suspected)} suspected issue(s). "
            f"Observed exploratory coverage {coverage.observed_coverage_pct}% "
            f"(not complete application coverage).{stop_clause}"
        )

    def _page_inventory(self, memory: RunMemory) -> list[dict[str, Any]]:
        rows = []
        if memory.app_store and memory.app_store.model.pages:
            from app.application.coverage import canonical_pages

            memory.app_store.prune_stub_pages()
            for p in canonical_pages(memory.app_store.model):
                rows.append(
                    {
                        "page_id": p.id,
                        "title": redact_value(p.title),
                        "url": sanitize_url(p.canonical_url),
                        "normalized_path": p.normalized_path,
                        "page_type": p.page_type,
                        "heading": redact_value(p.heading),
                        "visit_count": p.visit_count,
                        "exploration_status": p.exploration_status.value
                        if hasattr(p.exploration_status, "value")
                        else p.exploration_status,
                        "module_id": p.module_id,
                        "fingerprint": p.fingerprint,
                    }
                )
            return rows
        seen: set[str] = set()
        for p in memory.pages:
            key = normalize_page_url(p.url) or p.url
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                {
                    "page_id": p.page_id,
                    "title": redact_value(p.title),
                    "url": sanitize_url(key),
                    "page_type": p.classification.page_type if p.classification else "unknown",
                    "headings": [redact_value(h) for h in (p.headings or [])[:10]],
                    "forms": len(p.forms),
                    "tables": len(p.tables),
                    "fingerprint": p.state_fingerprint,
                }
            )
        return rows

    def _forms_inventory(self, memory: RunMemory) -> list[dict[str, Any]]:
        rows = []
        if memory.app_store and memory.app_store.model.forms:
            for form in memory.app_store.model.forms:
                page = memory.app_store.model.page_by_id(form.page_id)
                rows.append(
                    {
                        "form_id": form.id,
                        "page_url": sanitize_url(page.canonical_url) if page else "",
                        "method": form.method,
                        "action": redact_value(form.action),
                        "inspected": form.inspected,
                        "tested": form.tested,
                        "field_count": len(form.fields),
                        "fields": [
                            {
                                "name": redact_value(f.label),
                                "field_type": f.input_type,
                                "label": redact_value(f.label),
                                "required": f.required,
                                "stable_field_key": f.stable_field_key,
                            }
                            for f in form.fields
                        ],
                    }
                )
            return rows
        seen: set[str] = set()
        for page in memory.pages:
            for form in page.forms:
                if form.form_id in seen:
                    continue
                seen.add(form.form_id)
                rows.append(
                    {
                        "form_id": form.form_id,
                        "page_url": sanitize_url(page.url),
                        "method": form.method,
                        "action": redact_value(form.action),
                        "field_count": len(form.fields),
                        "fields": [
                            {
                                "name": redact_value(f.name),
                                "field_type": f.field_type,
                                "label": redact_value(f.label),
                                "required": f.required,
                            }
                            for f in form.fields
                        ],
                    }
                )
        return rows

    def _tables_inventory(self, memory: RunMemory) -> list[dict[str, Any]]:
        rows = []
        seen: set[str] = set()
        for page in memory.pages:
            for table in page.tables:
                if table.table_id in seen:
                    continue
                seen.add(table.table_id)
                rows.append(
                    {
                        "table_id": table.table_id,
                        "page_url": sanitize_url(page.url),
                        "headers": [redact_value(h) for h in table.headers],
                        "row_count": table.row_count,
                    }
                )
        return rows

    def _evidence_index(self, memory: RunMemory) -> list[dict[str, Any]]:
        from app.config import get_settings

        root = get_settings().run_evidence_dir(memory.run_id)
        if memory.app_store and memory.app_store.model.evidence:
            return [
                {
                    "evidence_id": e.id,
                    "kind": e.kind,
                    "relative_path": e.relative_path,
                    "public_url": e.public_url,
                    "path": e.relative_path,
                    "description": redact_value(e.description),
                }
                for e in memory.app_store.model.evidence
            ]
        rows = []
        for e in memory.evidence:
            entry = sanitize_evidence_index_entry(
                run_id=memory.run_id,
                evidence_id=e.evidence_id,
                kind=e.kind,
                path=e.path,
                run_root=root,
                description=str(redact_value(e.description or "")),
            )
            entry["path"] = entry["relative_path"]
            entry["evidence_id"] = e.evidence_id
            entry["page_id"] = e.page_id
            entry["action_id"] = e.action_id
            rows.append(entry)
        return rows

    def _collect_console(self, memory: RunMemory) -> list[str]:
        out: list[str] = []
        for p in memory.pages:
            for err in p.console_errors:
                out.append(str(redact_value(err)))
        # dedupe preserve order
        seen: set[str] = set()
        uniq = []
        for e in out:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        return uniq[:100]

    def _collect_network(self, memory: RunMemory) -> list[str]:
        out: list[str] = []
        for p in memory.pages:
            for err in p.network_failures:
                out.append(str(redact_value(err)))
        seen: set[str] = set()
        uniq = []
        for e in out:
            if e not in seen:
                seen.add(e)
                uniq.append(e)
        return uniq[:100]

    def _business_rules(self, memory: RunMemory) -> list[str]:
        rules: list[str] = []
        for form in memory.forms:
            required = [f.label or f.name or f.element_id for f in form.fields if f.required]
            if required:
                rules.append(
                    f"Form {form.form_id} marks required fields: {', '.join(str(r) for r in required if r)}"
                )
        for page in memory.pages:
            if page.classification and page.classification.page_type == "authentication":
                rules.append(f"Authentication surface observed at {sanitize_url(page.url)}")
        return rules

    def _ux_observations(self, memory: RunMemory) -> list[str]:
        notes = [o.detail or o.title for o in memory.observations if o.title or o.detail]
        for p in memory.pages:
            if not (p.title or "").strip():
                notes.append(f"Missing page title at {sanitize_url(p.url)}")
            if p.alerts:
                notes.append(f"Alerts on {sanitize_url(p.url)}: {p.alerts[0][:120]}")
        return [str(redact_value(n)) for n in notes[:50]]

    def _known_limitations(self, memory: RunMemory) -> list[str]:
        from app.runtime_info import browser_adapter_display, provider_display_name

        provider = provider_display_name(getattr(memory, "provider_type", None))
        adapter = browser_adapter_display(getattr(memory, "browser_adapter_id", None))
        limitations = [
            "Exploration is budget-limited and does not claim complete coverage.",
            "Single authenticated role unless multiple credentials were provided.",
            "Destructive, payment, invite, and messaging actions remain blocked.",
            "Gemma wording assistance must not invent pages, bugs, or evidence.",
            f"Provider: {provider} | Browser adapter: {adapter}",
            f"Run stop reason: {memory.stop_reason or 'n/a'}",
        ]
        # A form left untested because it names a record that does not exist is
        # not the same limitation as one the budget never reached, and reporting
        # them alike hides the only one the operator can act on.
        for form_id, labels in sorted(
            getattr(memory, "unsatisfied_form_references", {}).items()
        ):
            limitations.append(
                f"Form {form_id} could not be completed: {', '.join(labels)} "
                "only accept records the application does not currently hold."
            )
        return limitations

    def _recommended_next(self, memory: RunMemory, coverage: CoverageRecord) -> list[str]:
        from app.application.url_normalize import is_external_doc_or_social, same_origin

        tips: list[str] = []
        allowed = memory.app_store.model.allowed_origin if memory.app_store else ""
        if memory.app_store:
            candidates = memory.app_store.filter_unexplored(
                list(memory.unexplored_urls) + list(memory.app_store.model.candidate_urls)
            )
            for u in candidates[:8]:
                if is_external_doc_or_social(u):
                    continue
                if allowed and not same_origin(u, allowed):
                    continue
                tips.append(f"Explore unvisited application URL: {sanitize_url(u)}")
        else:
            for u in memory.unexplored_urls[:8]:
                if is_external_doc_or_social(u):
                    continue
                tips.append(f"Explore unvisited application URL: {sanitize_url(u)}")
        # Never recommend external documentation / social domains
        tips = [
            t
            for t in tips
            if "postman" not in t.lower()
            and "documenter" not in t.lower()
            and "facebook.com" not in t.lower()
            and "twitter.com" not in t.lower()
            and "linkedin.com" not in t.lower()
        ]
        for labels in getattr(memory, "unsatisfied_form_references", {}).values():
            for label in labels:
                tips.append(
                    f"Create a record that '{label}' can reference, then re-run — "
                    "the form cannot be submitted until one exists."
                )
        if coverage.forms_discovered > coverage.forms_tested:
            tips.append("Increase controlled-write tests on remaining forms (safe test data only).")
        if coverage.suspected_issues:
            tips.append("Triage suspected bugs with targeted reproduction.")
        if coverage.tests_generated and coverage.tests_executed == 0:
            tips.append("Execute generated scenarios that remain not_run.")
        if not tips:
            tips.append("Continue exploring remaining modules, or End Run when you are done.")
        return list(dict.fromkeys(tips))[:12]

    # ------------------------------------------------------------------
    # Reasoning-engine reporting (Knowledge Graph / Goal Generation /
    # Scenario Planning / QA Strategy / Autonomous Investigation) plus
    # CRUD/collection/form-lifecycle/cleanup coverage and blocked/unexecuted
    # scenario reasons. Every method degrades to an "unavailable" shape
    # when the corresponding engine wasn't attached to this run — never a
    # crash, and never invented data.
    # ------------------------------------------------------------------

    def _capability_disclosure(self, memory: RunMemory, runtime: dict[str, Any]) -> dict[str, Any]:
        from app.config import get_settings

        settings = get_settings()
        cfg = memory.configuration
        allow_controlled_writes = bool(cfg.allow_controlled_writes) if cfg else False
        allow_destructive_actions = bool(cfg.allow_destructive_actions) if cfg else False
        allow_safe_test_data_creation = bool(cfg.allow_safe_test_data_creation) if cfg else False
        safe_mode = bool(cfg.safe_mode) if cfg else True

        profile_count = 0
        if memory.auth_strategy is not None:
            try:
                profile_count = len(memory.auth_strategy.vault.public_flags().get("profile_ids") or [])
            except Exception:
                profile_count = 0

        capability_mode = runtime.get("capability_mode")
        return {
            "provider": runtime.get("provider"),
            "model": runtime.get("model") or runtime.get("model_id"),
            "provider_capability_mode": capability_mode,
            "provider_capability_explanation": runtime.get("capability_mode_explanation", ""),
            "browser_adapter": runtime.get("browser_adapter"),
            "autonomous_investigation_enabled": bool(runtime.get("enable_autonomous_investigation")),
            "controlled_writes_enabled": allow_controlled_writes,
            "safe_mode": safe_mode,
            "destructive_actions_allowed": allow_destructive_actions,
            "actor_switching_available": profile_count > 1,
            "actor_profile_count": profile_count,
            "visual_perception_enabled": bool(getattr(settings, "effective_gemma_supports_images", False)),
            "test_data_generation_enabled": allow_safe_test_data_creation,
            "mock_exploration_only_notice": (
                "Provider is Mock: exploration-only. Mock can navigate, inspect forms/tables, and "
                "generate/classify scenarios, but it CANNOT make the general semantic CRUD decisions "
                "needed to autonomously drive scenarios to completion for an arbitrary target "
                "application. Generated-scenario counts under this provider are not a claim of "
                "autonomous CRUD testing capability."
                if capability_mode == "exploration_only"
                else ""
            ),
        }

    def _knowledge_graph_summary(self, memory: RunMemory) -> dict[str, Any]:
        stats = memory.knowledge_graph_statistics()
        if stats is None:
            return {"available": False, "note": "No Knowledge Graph attached this run."}
        _dump = lambda x: x.model_dump(mode="json") if hasattr(x, "model_dump") else x
        return {
            "available": True,
            "graph_version": stats.graph_version,
            "total_nodes": stats.total_nodes,
            "total_edges": stats.total_edges,
            "node_counts_by_type": dict(stats.node_counts_by_type),
            "edge_counts_by_type": dict(stats.edge_counts_by_type),
            "observed_edge_count": stats.observed_edge_count,
            "inferred_edge_count": stats.inferred_edge_count,
            "contradicted_edge_count": stats.contradicted_edge_count,
            "stale_edge_count": stats.stale_edge_count,
            "unresolved_reference_count": stats.unresolved_reference_count,
            "consistency_issue_count": stats.consistency_issue_count,
            "gap_count": stats.gap_count,
            "connected_component_count": stats.connected_component_count,
            "isolated_node_count": stats.isolated_node_count,
            "top_gaps": [_dump(g) for g in memory.knowledge_graph_gaps()[:10]],
            "top_consistency_issues": [_dump(i) for i in memory.knowledge_graph_consistency_issues()[:10]],
        }

    def _generated_goals_summary(self, memory: RunMemory) -> dict[str, Any]:
        stats = memory.goal_statistics()
        if stats is None:
            return {"available": False, "note": "No Goal Generation Engine attached this run."}
        top = memory.highest_priority_goals(limit=20)
        return {
            "available": True,
            "total_goals": stats.total_goals,
            "goals_by_type": dict(stats.goals_by_type),
            "goals_by_status": dict(stats.goals_by_status),
            "average_priority": round(stats.average_priority, 3),
            "high_priority_count": stats.high_priority_count,
            "blocked_count": stats.blocked_count,
            "group_count": stats.group_count,
            "dependency_count": stats.dependency_count,
            "top_goals": [
                {
                    "goal_id": g.goal_id,
                    "goal_type": g.goal_type,
                    "title": g.title,
                    "goal_status": g.goal_status,
                    "priority_score": round(g.priority_score, 3),
                }
                for g in top
            ],
        }

    def _scenario_rows(self, memory: RunMemory) -> list[dict[str, Any]]:
        """One row per Scenario Planning scenario, cross-referenced with its
        QA Strategy candidate (if any) and that candidate's eligibility
        classification (if the Autonomous Investigation Engine has run) —
        the single source every scenario-status section below reads from."""
        if memory.scenario_engine is None:
            return []
        scenarios = memory.scenario_engine.query_engine.all_scenarios()
        candidates = memory.strategy_engine.query_engine.all_candidates() if memory.strategy_engine is not None else []
        candidates_by_scenario = {c.scenario_id: c for c in candidates}
        eligibility: dict[str, dict[str, str]] = {}
        if memory.investigation_engine is not None:
            eligibility = dict(getattr(memory.investigation_engine.memory, "eligibility_by_candidate_id", {}) or {})
        rows = []
        for s in scenarios:
            candidate = candidates_by_scenario.get(s.scenario_id)
            elig = eligibility.get(candidate.candidate_id) if candidate is not None else None
            rows.append(
                {
                    "scenario_id": s.scenario_id,
                    "title": s.title,
                    "scenario_type": s.scenario_type,
                    "status": s.status,
                    "feasibility_status": s.feasibility_status,
                    "candidate_id": candidate.candidate_id if candidate is not None else None,
                    "queue_type": candidate.queue_type if candidate is not None else None,
                    "risk_class": candidate.risk_class if candidate is not None else None,
                    "recommended_action": candidate.recommended_action if candidate is not None else None,
                    "eligibility_status": elig.get("status") if elig else None,
                    "eligibility_reason": elig.get("reason") if elig else None,
                }
            )
        return rows

    def _scenario_planning_summary(self, memory: RunMemory, scenario_rows: list[dict[str, Any]]) -> dict[str, Any]:
        stats = memory.scenario_engine.statistics() if memory.scenario_engine is not None else None
        if stats is None:
            return {"available": False, "note": "No Scenario Planning Engine attached this run."}
        return {
            "available": True,
            "total_scenarios": stats.total_scenarios,
            "scenarios_by_type": dict(stats.scenarios_by_type),
            "scenarios_by_status": dict(stats.scenarios_by_status),
            "scenarios_by_feasibility": dict(stats.scenarios_by_feasibility),
            "scenarios_by_risk": dict(stats.scenarios_by_risk),
            "read_only_count": stats.read_only_count,
            "mutating_count": stats.mutating_count,
            "cross_actor_count": stats.cross_actor_count,
            "high_risk_count": stats.high_risk_count,
            "average_complexity_score": round(stats.average_complexity_score, 3),
            "average_confidence_gain": round(stats.average_confidence_gain, 3),
            "scenario_plan_version": stats.scenario_plan_version,
            "scenarios": [
                {k: v for k, v in row.items() if k in {"scenario_id", "title", "scenario_type", "status", "feasibility_status"}}
                for row in scenario_rows
            ],
        }

    def _qa_strategy_summary(self, memory: RunMemory) -> dict[str, Any]:
        stats = memory.strategy_engine.statistics() if memory.strategy_engine is not None else None
        if stats is None:
            return {"available": False, "note": "No QA Strategy Engine attached this run."}
        candidates = memory.strategy_engine.query_engine.all_candidates()
        return {
            "available": True,
            "total_candidates": stats.total_candidates,
            "candidates_by_queue": dict(stats.candidates_by_queue),
            "candidates_by_action": dict(stats.candidates_by_action),
            "candidates_by_batch_type": dict(stats.candidates_by_batch_type),
            "batch_count": stats.batch_count,
            "average_priority": round(stats.average_priority, 3),
            "average_risk": round(stats.average_risk, 3),
            "policy_used": stats.policy_used,
            "strategy_version": stats.strategy_version,
            "candidates": [
                {
                    "candidate_id": c.candidate_id,
                    "scenario_id": c.scenario_id,
                    "queue_type": c.queue_type,
                    "recommended_action": c.recommended_action,
                    "risk_class": c.risk_class,
                    "priority_score": round(c.priority_score, 3),
                    "blocking_reasons": list(c.blocking_reasons),
                }
                for c in candidates[:50]
            ],
        }

    def _autonomous_investigation_summary(self, memory: RunMemory) -> dict[str, Any]:
        stats = memory.investigation_engine.statistics() if memory.investigation_engine is not None else None
        if stats is None:
            return {"available": False, "note": "Autonomous Investigation is disabled this run."}
        stop_report = memory.investigation_stop_report()
        return {
            "available": True,
            "total_investigations": stats.total_investigations,
            "investigations_by_outcome": dict(stats.investigations_by_outcome),
            "total_steps_executed": stats.total_steps_executed,
            "total_assertions_evaluated": stats.total_assertions_evaluated,
            "supported_assertion_count": stats.supported_assertion_count,
            "contradicted_assertion_count": stats.contradicted_assertion_count,
            "inconclusive_assertion_count": stats.inconclusive_assertion_count,
            "total_recovery_attempts": stats.total_recovery_attempts,
            "stop_report": stop_report.model_dump(mode="json") if stop_report is not None else None,
        }

    def _crud_workflow_coverage(self, memory: RunMemory, coverage: CoverageRecord) -> dict[str, Any]:
        hypotheses = memory.crud_hypotheses()
        return {
            "create": {"discovered": coverage.crud_create_discovered, "executed": coverage.crud_create_executed},
            "read": {
                "discovered": coverage.crud_read_discovered,
                "executed": coverage.crud_read_executed,
                "note": "Read coverage reads record-collection discovery/inspection — see 'Collection/Grid Coverage'.",
            },
            "update": {"discovered": coverage.crud_update_discovered, "executed": coverage.crud_update_executed},
            "delete": {"discovered": coverage.crud_delete_discovered, "executed": coverage.crud_delete_executed},
            "hypotheses": [h.to_summary_dict() for h in hypotheses[:50]],
        }

    def _form_lifecycle_summary(self, memory: RunMemory) -> dict[str, Any]:
        forms = memory.app_store.model.forms if memory.app_store else []
        by_state: dict[str, int] = {}
        by_not_tested_reason: dict[str, int] = {}
        for f in forms:
            by_state[f.lifecycle_state] = by_state.get(f.lifecycle_state, 0) + 1
            if f.not_tested_reason:
                by_not_tested_reason[f.not_tested_reason] = by_not_tested_reason.get(f.not_tested_reason, 0) + 1
        return {
            "forms_discovered": len(forms),
            "by_lifecycle_state": by_state,
            "by_not_tested_reason": by_not_tested_reason,
        }

    def _collection_coverage(self, coverage: CoverageRecord) -> dict[str, Any]:
        return {
            "collections_discovered": coverage.collections_discovered,
            "collections_inspected": coverage.collections_inspected,
            "collection_coverage_pct": coverage.collection_coverage_pct,
        }

    def _executed_assertions(self, memory: RunMemory) -> list[dict[str, Any]]:
        if memory.investigation_engine is None:
            return []
        rows: list[dict[str, Any]] = []
        for result in memory.investigation_engine.query_engine.all_results():
            if result.verification is None:
                continue
            for a in result.verification.assertion_results:
                rows.append(
                    {
                        "investigation_id": result.investigation_id,
                        "scenario_id": result.scenario_id,
                        "subject_id": a.subject_id,
                        "operator": a.operator,
                        "outcome": a.outcome,
                        "observed_value": str(redact_value(a.observed_value)),
                        "expected_value": str(redact_value(a.expected_value)),
                        "confidence": round(a.confidence, 3),
                        "explanation": a.explanation,
                    }
                )
        return rows[:200]

    def _blocked_and_unexecuted_scenarios(
        self, memory: RunMemory, scenario_rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Blocked = currently in a blocked-shaped eligibility/status.
        Unexecuted = every scenario that has not reached a terminal
        completed/passed/failed/contradicted/skipped outcome — a superset of
        blocked that also includes ones simply never reached yet. Every row
        carries a reason from the closed BLOCKED_REASONS vocabulary — never
        a bare 'not_run'."""
        autonomous_enabled = memory.investigation_engine is not None
        provider_capable = getattr(memory, "provider_type", "") != "mock"
        stop_reason = memory.stop_reason
        terminal_executed = {"completed", "passed", "failed", "contradicted", "skipped", "cleaned_up"}

        blocked: list[dict[str, Any]] = []
        unexecuted: list[dict[str, Any]] = []
        for row in scenario_rows:
            status = row.get("status") or "draft"
            if status in terminal_executed:
                continue
            reason = classify_scenario_blocked_reason(
                eligibility_status=row.get("eligibility_status"),
                autonomous_investigation_enabled=autonomous_enabled,
                provider_capable=provider_capable,
                stop_reason=stop_reason,
            )
            entry = {
                "scenario_id": row["scenario_id"],
                "title": row["title"],
                "status": status,
                "feasibility_status": row.get("feasibility_status"),
                "reason": reason,
            }
            unexecuted.append(entry)
            if status == "blocked" or (row.get("eligibility_status") or "").startswith("blocked_by") or row.get(
                "eligibility_status"
            ) in {"duplicate", "stale"}:
                blocked.append(entry)

        # Legacy app.agent.tester.Tester-generated TestScenario/TestExecution
        # entries stayed `not_run` — no per-scenario eligibility data exists
        # for this older generator, so the reason is coarser by design.
        budget_exhausted = memory.remaining_action_budget <= 0
        not_run_ids = {e.test_id for e in memory.executions if e.status == "not_run"}
        for scenario in memory.scenarios:
            if scenario.test_id not in not_run_ids:
                continue
            reason = classify_legacy_scenario_reason(budget_exhausted=budget_exhausted, stop_reason=stop_reason)
            unexecuted.append(
                {
                    "scenario_id": scenario.test_id,
                    "title": scenario.title,
                    "status": "not_run",
                    "feasibility_status": None,
                    "reason": reason,
                    "source": "legacy_tester",
                }
            )
        return blocked[:100], unexecuted[:200]

    def _temporary_records_summary(self, memory: RunMemory) -> dict[str, Any]:
        snapshot = memory.temporary_record_snapshot()
        if snapshot is None:
            return {"available": False, "total_records": 0, "note": "No temporary test records were created this run."}
        return {"available": True, **snapshot}

    def _cleanup_status_summary(self, memory: RunMemory, coverage: CoverageRecord) -> dict[str, Any]:
        return {
            "pending": coverage.cleanup_pending,
            "succeeded": coverage.cleanup_succeeded,
            "failed": coverage.cleanup_failed,
            "manual_required": coverage.cleanup_manual_required,
            "cleanup_coverage_pct": coverage.cleanup_coverage_pct,
            "manual_cleanup_instructions": memory.manual_cleanup_instructions(),
        }

    def _stop_summary(self, memory: RunMemory, coverage: CoverageRecord, unexecuted_scenarios: list[dict[str, Any]]) -> dict[str, Any]:
        last_action = memory.actions[-1] if memory.actions else None
        temp_snapshot = memory.temporary_record_snapshot()
        temp_left_behind = 0
        if temp_snapshot:
            temp_left_behind = temp_snapshot.get("pending_cleanup", 0) + temp_snapshot.get("manual_cleanup_required", 0)
        config_suggestions: list[str] = []
        reasons_present = {row["reason"] for row in unexecuted_scenarios}
        if "autonomous_mode_disabled" in reasons_present:
            config_suggestions.append("Enable autonomous investigation (enable_autonomous_investigation=True) to execute generated scenarios.")
        if "provider_incapable" in reasons_present:
            config_suggestions.append("Configure a real reasoning provider — Mock cannot drive scenarios to completion.")
        if "controlled_writes_disabled" in reasons_present:
            config_suggestions.append("Enable allow_controlled_writes/allow_safe_test_data_creation to execute write-shaped scenarios.")
        if "budget_exhausted" in reasons_present:
            config_suggestions.append("End Run when you are done, or re-run with write permissions enabled if scenarios were blocked.")
        if temp_left_behind:
            config_suggestions.append("Re-run cleanup for pending temporary records (see Temporary Records / Cleanup Status).")

        # Work the application offered and policy refused. This is the most
        # actionable thing the stop summary can say, so it leads the
        # what-prevented-execution list rather than sitting below scenario
        # bookkeeping — a run that stopped with most of its action budget unused
        # because writes were disabled must say exactly that.
        permission_gap = memory.write_permission_gap() if hasattr(memory, "write_permission_gap") else {}
        prevented = sorted({row["reason"] for row in unexecuted_scenarios})
        if permission_gap:
            prevented = ["write_permissions_not_granted", *prevented]
            config_suggestions.insert(
                0,
                "Enable "
                + ", ".join(permission_gap["missing_permissions"])
                + " to exercise "
                + ", ".join(permission_gap["blocked_capabilities"])
                + " — the application offered "
                + ("this" if len(permission_gap["blocked_capabilities"]) == 1 else "these")
                + " and this run was not permitted to.",
            )
        return {
            "stop_reason": memory.stop_reason or "n/a",
            "write_permission_gap": permission_gap,
            "last_meaningful_operation": (
                f"{last_action.action.action.value} on {last_action.action.element_id or last_action.after_url or 'n/a'}"
                if last_action is not None
                else "No actions were executed this run."
            ),
            "untested_summary": {
                "unexecuted_scenario_count": len(unexecuted_scenarios),
                "forms_not_tested": max(coverage.forms_discovered - coverage.forms_tested, 0),
                "crud_operations_not_executed": max(coverage.crud_operations_discovered - coverage.crud_operations_executed, 0),
            },
            "what_prevented_execution": prevented or ["n/a"],
            "recommended_configuration_changes": config_suggestions or ["No configuration changes indicated — nothing left blocked this run."],
            "temporary_records_left_behind": temp_left_behind,
        }

    def _default_regression(self) -> list[str]:
        return [
            "Critical navigation paths remain reachable",
            "Primary forms render without console errors",
            "No new network 5xx on core pages",
            "Page titles remain present on explored pages",
            "Required fields reject empty submission",
        ]

    def _confirmed_bug_entries(self, memory: RunMemory) -> list[BugReportEntry]:
        entries: list[BugReportEntry] = []
        for bug in memory.bugs:
            if _bucket_suspected(bug):
                continue
            entries.append(
                self._defect_to_entry(bug, memory, classification=_classification_label(bug))
            )
        for analysis in memory.suspected_bugs:
            if analysis.classification == BugClassification.CONFIRMED_BUG:
                entries.append(self._analysis_to_entry(analysis, memory))
        return self._dedupe_entries(entries)

    def _suspected_bug_entries(self, memory: RunMemory) -> list[BugReportEntry]:
        entries: list[BugReportEntry] = []
        for bug in memory.bugs:
            if _bucket_suspected(bug):
                entries.append(
                    self._defect_to_entry(bug, memory, classification=_classification_label(bug))
                )
        for analysis in memory.suspected_bugs:
            if analysis.classification != BugClassification.CONFIRMED_BUG:
                entries.append(self._analysis_to_entry(analysis, memory))
        return self._dedupe_entries(entries)

    def _defect_to_entry(
        self,
        bug: Defect,
        memory: RunMemory,
        classification: str,
    ) -> BugReportEntry:
        shots, traces, console, network = self._split_evidence(bug.evidence_ids, memory)
        return BugReportEntry(
            bug_id=bug.bug_id,
            title=bug.title,
            module=bug.module or "",
            page=bug.page_title or "",
            url=sanitize_url(bug.page_url or ""),
            classification=classification,
            severity=bug.severity.value,
            priority=bug.priority or "medium",
            preconditions=list(bug.preconditions or []),
            test_data=str(redact_value(bug.test_data or "")),
            steps_to_reproduce=list(bug.steps_to_reproduce or []),
            expected_result=bug.expected,
            actual_result=str(redact_value(bug.actual)),
            business_impact=bug.business_impact or "",
            possible_root_cause_hypothesis=bug.possible_root_cause
            or "Hypothesis only — not verified",
            confidence=float(bug.confidence or 0.0),
            screenshot_evidence=shots,
            trace_evidence=traces,
            console_evidence=console,
            network_evidence=network,
            discovery_timestamp=bug.created_at,
            run_id=bug.run_id,
        )

    def _analysis_to_entry(
        self,
        analysis: BugAnalysisResult,
        memory: RunMemory,
    ) -> BugReportEntry:
        shots, traces, console, network = self._split_evidence(analysis.evidence_ids, memory)
        return BugReportEntry(
            bug_id=f"analysis-{abs(hash(analysis.title)) % 10_000_000:07d}",
            title=analysis.title or "Untitled finding",
            module=analysis.module,
            page="",
            url=sanitize_url(analysis.page_url or ""),
            classification=analysis.classification.value,
            severity=analysis.severity,
            priority=analysis.priority,
            preconditions=list(analysis.preconditions),
            test_data=str(redact_value(analysis.test_data or "")),
            steps_to_reproduce=list(analysis.steps),
            expected_result=analysis.expected_result,
            actual_result=str(redact_value(analysis.actual_result)),
            business_impact=analysis.business_impact,
            possible_root_cause_hypothesis=analysis.possible_root_cause
            or "Hypothesis only — not verified",
            confidence=analysis.confidence,
            screenshot_evidence=shots,
            trace_evidence=traces,
            console_evidence=console,
            network_evidence=network,
            discovery_timestamp=datetime.utcnow(),
            run_id=memory.run_id,
        )

    def _split_evidence(
        self,
        evidence_ids: list[str],
        memory: RunMemory,
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        by_id = {e.evidence_id: e for e in memory.evidence}
        shots: list[str] = []
        traces: list[str] = []
        console: list[str] = []
        network: list[str] = []
        from pathlib import Path

        from app.application.evidence_paths import public_evidence_url, to_relative_evidence_path
        from app.config import get_settings

        root = get_settings().run_evidence_dir(memory.run_id)
        for eid in evidence_ids or []:
            item = by_id.get(eid)
            if not item:
                continue
            rel = to_relative_evidence_path(item.path, root)
            safe = public_evidence_url(memory.run_id, rel) if rel else Path(item.path).name
            if item.kind == "screenshot":
                shots.append(safe)
            elif item.kind == "trace":
                traces.append(safe)
            elif item.kind == "console":
                console.append(safe)
            elif item.kind == "network":
                network.append(safe)
        return shots, traces, console, network

    def _dedupe_entries(self, entries: list[BugReportEntry]) -> list[BugReportEntry]:
        """Keep every distinct bug record.

        Findings that share a title and page are still separate bugs when they
        have different ids. Only synthesized analysis rows collapse into a
        stored defect of the same title and URL.
        """
        seen_ids: set[str] = set()
        seen_titles: set[tuple[str, str]] = set()
        out: list[BugReportEntry] = []
        for e in entries:
            if not e.title or e.bug_id in seen_ids:
                continue
            title_key = (e.title, e.url)
            if str(e.bug_id).startswith("analysis-") and title_key in seen_titles:
                continue
            seen_ids.add(e.bug_id)
            seen_titles.add(title_key)
            out.append(e)
        return out

    def _guess_name(self, memory: RunMemory) -> str:
        if memory.pages and memory.pages[0].title:
            return memory.pages[0].title.split("|")[0].split("-")[0].strip() or "Application"
        host = urlparse(memory.start_url).hostname or "Application"
        return host

    @staticmethod
    def _clean_text(text: str) -> str:
        """Fix duplicate punctuation / spacing in generated documentation."""
        if not text:
            return text
        cleaned = re.sub(r"\.{2,}", ".", text)
        cleaned = re.sub(r"\s+([,.;:!?])", r"\1", cleaned)
        cleaned = re.sub(r"([,.;:!?]){2,}", r"\1", cleaned)
        cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
        cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
        return cleaned.strip()

    def _md_table(self, title: str, headers: list[str], rows: list[list[str]]) -> str:
        lines: list[str] = []
        if not rows:
            lines.append("_None recorded._")
            return "\n".join(lines)
        lines.append("| " + " | ".join(headers) + " |")
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        for row in rows:
            safe = [str(redact_value(c)).replace("|", "/") for c in row]
            lines.append("| " + " | ".join(safe) + " |")
        return "\n".join(lines)

    def _bugs_md(self, title: str, bugs: list[BugReportEntry]) -> str:
        lines: list[str] = []
        if not bugs:
            lines.append("_None recorded._")
            return "\n".join(lines)
        for b in bugs:
            lines.append(f"**{b.title}**")
            lines.append(f"- **Bug ID:** `{b.bug_id}`")
            lines.append(f"- **Module:** {b.module or 'n/a'}")
            lines.append(f"- **Page / URL:** {b.page or 'n/a'} / `{b.url}`")
            lines.append(f"- **Classification:** {b.classification}")
            lines.append(f"- **Severity / Priority:** {b.severity} / {b.priority}")
            lines.append(f"- **Expected:** {b.expected_result}")
            lines.append(f"- **Actual:** {b.actual_result}")
            lines.append(
                f"- **Possible root cause (hypothesis only):** {b.possible_root_cause_hypothesis}"
            )
            lines.append(f"- **Confidence:** {b.confidence}")
            if b.steps_to_reproduce:
                lines.append("- **Steps:**")
                for i, step in enumerate(b.steps_to_reproduce, 1):
                    lines.append(f"  {i}. {step}")
            lines.append("")
        return "\n".join(lines)
