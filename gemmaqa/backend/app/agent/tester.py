"""Safe exploratory test proposal and recording with scenario_key dedup."""

from __future__ import annotations

from app.agent.test_data import values_for_field
from app.agent.test_scope import (
    focus_terms,
    page_matches_focus,
    requested_types,
)
from app.application.store import form_fingerprint, scenario_key
from app.application.url_normalize import normalize_url
from app.gemma.base import GemmaProvider
from app.schemas import PageState, TestExecution, TestScenario
from app.utils.ids import new_id
from app.utils.logging import get_logger

logger = get_logger("agent.tester")


class Tester:
    """Propose and track safe exploratory test scenarios."""

    def __init__(self, gemma: GemmaProvider | None = None) -> None:
        self.gemma = gemma
        self.scenarios: list[TestScenario] = []
        self.executions: list[TestExecution] = []
        self._scenario_keys: set[str] = set()
        # Canonical page keys that already received a generate_test_scenarios
        # attempt this run. Re-observe of the same page must not pay for a
        # second Ollama call (Contact List login was observed twice; the
        # second call added nothing and timed out).
        self._pages_with_gemma_scenarios: set[str] = set()

    def _page_key(self, page_state: PageState, app_store=None) -> str:
        canonical = normalize_url(page_state.url) or page_state.url
        if app_store:
            page = app_store.model.page_by_url(canonical)
            if page:
                return page.id
        return canonical

    async def propose_for_page(
        self,
        page_state: PageState,
        run_id: str,
        *,
        allow_controlled_writes: bool = False,
        app_store=None,
        skip_llm: bool = False,
        focus_modules: list[str] | None = None,
        test_case_types: list[str] | None = None,
        testing_objective: str | None = None,
    ) -> list[TestScenario]:
        created: list[TestScenario] = []
        page_id = self._page_key(page_state, app_store)
        types = requested_types(testing_objective, test_case_types)
        focus = focus_terms(testing_objective, focus_modules)

        for item in self._deterministic_specs(
            page_state,
            focus_modules=focus,
            test_case_types=types,
            testing_objective=testing_objective,
        ):
            key = scenario_key(
                page_id=page_id,
                form_fingerprint=item.get("form_fp", ""),
                field_key=item.get("field_key", ""),
                category=item["category"],
                data_class=item.get("data_class", "generic"),
            )
            if key in self._scenario_keys:
                continue
            if app_store and any(s.scenario_key == key for s in app_store.model.scenarios):
                self._scenario_keys.add(key)
                continue
            self._scenario_keys.add(key)
            sc = TestScenario(
                test_id=new_id(),
                title=item["title"],
                description=item.get("description", ""),
                category=item["category"],
                priority=item.get("priority", "medium"),
                steps=list(item.get("steps") or []),
                expected_results=list(item.get("expected") or []),
                test_case_type=item.get("test_case_type", "positive"),
            )
            created.append(sc)
            self.scenarios.append(sc)
            self.executions.append(
                TestExecution(
                    execution_id=new_id(),
                    test_id=sc.test_id,
                    run_id=run_id,
                    status="not_run",
                    notes=f"Generated for {page_state.url}; not executed unless a run step performs it.",
                )
            )
            if app_store:
                app_store.add_scenario(
                    title=sc.title,
                    category=sc.category,
                    page_id=page_id,
                    form_fp=item.get("form_fp", ""),
                    field_key=item.get("field_key", ""),
                    data_class=item.get("data_class", "generic"),
                    description=sc.description,
                    steps=sc.steps,
                    expected=sc.expected_results,
                )

        if skip_llm:
            logger.info(
                "Skipping generate_test_scenarios on %s (live loop must not block on Gemma)",
                page_id,
            )
        elif self.gemma is not None and page_id in self._pages_with_gemma_scenarios:
            logger.info(
                "Skipping generate_test_scenarios; scenarios already proposed for %s",
                page_id,
            )
        elif self.gemma is not None:
            self._pages_with_gemma_scenarios.add(page_id)
            try:
                proposals = await self.gemma.generate_test_scenarios(
                    page_state,
                    {
                        "run_id": run_id,
                        "safe_only": True,
                        "forbidden": [
                            "sql injection",
                            "auth bypass",
                            "dos",
                            "data theft",
                            "destructive",
                        ],
                    },
                )
                for raw in proposals:
                    title = str(raw.get("title", "")).lower()
                    if any(
                        bad in title
                        for bad in ("injection", "bypass", "dos", "flood", "steal", "exploit")
                    ):
                        continue
                    # Skip AI duplicates of deterministic required-field / smoke scenarios
                    if any(
                        p in title
                        for p in (
                            "inspect required",
                            "required field",
                            "page title present",
                            "empty value on el_",
                        )
                    ):
                        continue
                    cat = str(raw.get("category", "exploratory"))
                    field_key = title[:40]
                    key = scenario_key(
                        page_id=page_id,
                        form_fingerprint="",
                        field_key=field_key,
                        category=cat,
                        data_class="ai",
                    )
                    if key in self._scenario_keys:
                        continue
                    if app_store and any(s.scenario_key == key for s in app_store.model.scenarios):
                        self._scenario_keys.add(key)
                        continue
                    self._scenario_keys.add(key)
                    sc = TestScenario(
                        test_id=new_id(),
                        title=str(raw.get("title", "Untitled scenario")),
                        description=str(raw.get("description", "")),
                        category=cat,
                        priority=str(raw.get("priority", "medium")),
                        preconditions=list(raw.get("preconditions") or []),
                        steps=list(raw.get("steps") or []),
                        expected_results=list(raw.get("expected_results") or []),
                        test_case_type="positive" if "positive" in (types or ["positive"]) else "exploratory",
                    )
                    created.append(sc)
                    self.scenarios.append(sc)
                    self.executions.append(
                        TestExecution(
                            execution_id=new_id(),
                            test_id=sc.test_id,
                            run_id=run_id,
                            status="not_run",
                            notes=f"Generated for {page_state.url}",
                        )
                    )
                    if app_store:
                        app_store.add_scenario(
                            title=sc.title,
                            category=sc.category,
                            page_id=page_id,
                            field_key=field_key,
                            data_class="ai",
                            description=sc.description,
                            steps=sc.steps,
                            expected=sc.expected_results,
                        )
            except Exception as exc:
                logger.warning("Gemma test proposal failed: %s", type(exc).__name__)

        logger.info("Proposed %s new scenarios for %s", len(created), page_state.url)
        return created

    def _field_display_name(self, field) -> str:
        for attr in ("label", "name", "placeholder", "accessible_name"):
            val = getattr(field, attr, None)
            if val and str(val).strip() and not str(val).lower().startswith("el_"):
                return str(val).strip()
        ftype = (getattr(field, "field_type", None) or "field").strip()
        return f"{ftype} field"

    def _form_label(self, page_state: PageState) -> str:
        heading = (page_state.headings[0] if page_state.headings else "") or ""
        title = (page_state.title or "").strip()
        return heading.strip() or title or "form"

    def _deterministic_specs(
        self,
        page_state: PageState,
        *,
        focus_modules: list[str] | None = None,
        test_case_types: list[str] | None = None,
        testing_objective: str | None = None,
    ) -> list[dict]:
        """Generate only the scenarios the operator asked for.

        Focused runs (e.g. signup + positive) never emit page-title smoke
        tests or empty-field negatives, and skip pages outside the feature.
        """
        from urllib.parse import urlparse

        types = list(test_case_types or [])
        focus = list(focus_modules or [])
        want_positive = not types or "positive" in types
        want_negative = "negative" in types
        want_exploratory = "exploratory" in types or not (focus or types)

        if focus and not page_matches_focus(
            url=page_state.url,
            title=page_state.title,
            headings=list(page_state.headings or []),
            extra=" ".join(
                self._field_display_name(f)
                for form in page_state.forms
                for f in form.fields
            ),
            focus=focus,
        ):
            logger.info(
                "Skipping scenario generation on %s — outside focused scope %s",
                page_state.url,
                focus,
            )
            return []

        path = urlparse(page_state.url).path or "/"
        page_label = (page_state.title or "").strip() or path
        if path and path != "/" and page_label != path:
            smoke_label = f"{page_label} ({path})"
        else:
            smoke_label = page_label
        form_label = self._form_label(page_state)
        out: list[dict] = []

        # Smoke / page-title tests are not a requested feature test.
        if want_exploratory and not focus and not types:
            out.append(
                {
                    "title": f"Smoke: page title present on {smoke_label}",
                    "description": f"Verify that {smoke_label} loads and the page title is visible.",
                    "category": "smoke",
                    "test_case_type": "positive",
                    "data_class": "smoke",
                    "field_key": "page-title",
                    "steps": ["Open page", "Confirm title is non-empty"],
                    "expected": ["Title is displayed"],
                }
            )

        for form in page_state.forms:
            fp = form_fingerprint(form)
            required = [f for f in form.fields if f.required]
            field_names = ", ".join(
                self._field_display_name(f) for f in (required or form.fields)[:6]
            ) or "required fields"

            if want_positive:
                out.append(
                    {
                        "title": f"Submit {form_label} with valid data",
                        "description": (
                            f"Verify that {form_label} accepts valid values for "
                            f"{field_names} and completes successfully."
                        ),
                        "category": "form",
                        "test_case_type": "positive",
                        "form_fp": fp,
                        "field_key": "valid-submit",
                        "data_class": "valid",
                        "steps": [
                            f"Open {form_label}",
                            f"Enter valid values for {field_names}",
                            "Submit the form",
                            "Confirm the success outcome (redirect, confirmation, or new session)",
                        ],
                        "expected": [
                            "The form accepts valid data and the application shows the expected success state."
                        ],
                    }
                )

            if want_negative:
                if required:
                    out.append(
                        {
                            "title": f"Required field validation on {form_label}",
                            "description": (
                                f"Verify that {form_label} blocks submit when "
                                f"{field_names} are left empty."
                            ),
                            "category": "negative",
                            "test_case_type": "negative",
                            "form_fp": fp,
                            "field_key": "required-group",
                            "data_class": "empty",
                            "steps": [
                                f"Open {form_label}",
                                "Leave required fields empty",
                                "Attempt submit",
                            ],
                            "expected": ["Validation prevents submission or shows errors"],
                        }
                    )
                for field in form.fields:
                    display = self._field_display_name(field)
                    fkey = (field.name or field.label or field.element_id or display).lower()
                    values = values_for_field(field.field_type, field.label)
                    chosen = next((v for v in values if v.category == "empty"), None)
                    if chosen is None:
                        continue
                    out.append(
                        {
                            "title": f"{chosen.description} on {display}",
                            "description": (
                                f"Verify that {form_label} rejects an empty {display} value."
                            ),
                            "category": "form",
                            "test_case_type": "negative",
                            "form_fp": fp,
                            "field_key": fkey,
                            "data_class": chosen.category,
                            "steps": [
                                f"Leave {display} empty",
                                "Attempt submit",
                                "Observe validation",
                            ],
                            "expected": ["Application shows a validation error and does not succeed"],
                        }
                    )

        if want_exploratory and not focus:
            if page_state.search_fields:
                out.append(
                    {
                        "title": "Search with no results",
                        "category": "exploratory",
                        "test_case_type": "exploratory",
                        "data_class": "search-empty",
                        "field_key": "search",
                        "steps": ["Enter nonsense query QA_TEST_NO_RESULTS_ZZZ", "Submit search"],
                        "expected": ["Empty state shown without crash"],
                    }
                )
            if page_state.pagination_controls:
                out.append(
                    {
                        "title": "Pagination edge case",
                        "category": "exploratory",
                        "test_case_type": "exploratory",
                        "data_class": "pagination",
                        "field_key": "pagination",
                        "steps": ["Navigate pagination controls", "Observe list stability"],
                        "expected": ["Page changes without console/network 500s"],
                    }
                )
        return out
