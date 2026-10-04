# Product Requirements Document

## QA Engine

| | |
|---|---|
| **Product** | QA Engine |
| **Document** | Product Requirements Document |
| **Version** | 1.1 |
| **Date** | 4 October 2026 |
| **Status** | Hackathon submission |
| **Audience** | Judges, QA leads, and engineers evaluating the product |
| **Repository** | [github.com/M-Ilays/QA-Engine](https://github.com/M-Ilays/QA-Engine) |
| **Target** | Any website the operator is authorized to test |
| **Example demo** | [Thinking Tester Contact List](https://thinking-tester-contact-list.herokuapp.com/) |

---

## 1. Executive summary

QA Engine is an autonomous testing agent for any website the operator is authorized to test. It is not built for one product. The operator supplies the URL. The agent opens that site in Chromium, reads the page that is actually there, and tests the feature the operator named. No site-specific script is required.

The report contains the test cases that were actually executed, screenshots, an activity log, and bugs. A bug is recorded when a form stays on the page after submit and no validation message is shown. A submit that leaves the form is treated as success.

QA Engine is a first-pass exploratory, retest, and regression-smoke tool. It is not a saved scripted suite and it does not replace a full QA strategy.

---

## 2. Problem

After a feature lands, teams still spend a human browser session on three repeating jobs:

1. A first look at an unfamiliar or changed screen.
2. A retest after a fix, to see whether the same flow still fails.
3. Another pass after a release, to confirm the main paths still work.

Those passes are slow to start. The tester has to remember the URL, the credentials, which fields matter, and how to prove what they saw. Notes, screenshots, and bug titles are assembled by hand, and they often disagree with what the browser actually did.

QA Engine takes that first pass on whatever authorized site the team is working on: a public demo, a staging app, or an internal tool. The operator states the URL and the scope. The agent drives the browser, keeps the evidence, and produces test cases and bugs that match the run.

---

## 3. Product goals

| ID | Goal |
|---|---|
| G1 | Test any authorized website from a URL, from the New Run screen or from chat, with no per-site script. |
| G2 | Limit written and executed cases to the feature and test type the operator asked for. |
| G3 | Exercise the forms and record flows that site actually presents, including create, update, and delete, when those permissions are granted. |
| G4 | Treat a missing validation message on a form that stays open as a bug, and name the bug for that operation. |
| G5 | Show every stored bug, the executed test cases, and a downloadable report for the run. |
| G6 | Let the operator pause, continue, or end the run while the browser is open. |
| G7 | Keep model choice optional. Gemini, Ollama, a local Transformers model, or Mock can supply page reasoning. Safety rules do not depend on the model. |

## 4. Non-goals

| ID | Non-goal |
|---|---|
| NG1 | A stored library of hand-written scripts that replay the same steps forever. |
| NG2 | A guarantee that every screen, role, or edge case of the target application was tested. |
| NG3 | Testing systems the operator does not own and is not allowed to test. |
| NG4 | Deleting, purchasing, sending email, or changing credentials on the target application. |
| NG5 | A public multi-tenant service. The API has no sign-in of its own. |
| NG6 | Replacing a human QA strategy, a formal regression suite, or accessibility certification. |

---

## 5. Users

| Persona | Need |
|---|---|
| **QA engineer** | A first browser pass on any authorized website, with test cases, screenshots, and bugs that can be reviewed after the run. |
| **Developer** | A retest after a fix, or a smoke pass after a change, on that same site, without writing a new script for each screen. |
| **Hackathon judge** | A visible run against any authorized URL. The public Contact List app is one example: the browser moves, the log updates, and the report matches what ran. |

The operator must confirm they are allowed to test the target before a run starts.

---

## 6. How the product works

```
Operator
  → React UI (Chat or New Run)
    → FastAPI
      → AgentController
        → optional model (Gemini, Ollama, Transformers, or Mock)
        → Playwright Chromium
          → authorized website
      → SQLite, screenshots, test cases, bugs, report
```

The same loop runs on every site. The agent does not look up a map of that product. On each page it observes the screen, classifies it, plans one safe action, executes that action, and checks the result. After a form submit it looks for a validation message, including the browser’s own “Please fill out this field” message. If the form is still showing and no message appeared, that operation is filed as a bug. If the page changes, the submit is a successful step.

The operator can watch the live activity log, pause, continue, or end the run. Ending the run keeps the evidence collected so far.

Architecture diagram: [docs/architecture.png](docs/architecture.png). Narrative: [ARCHITECTURE.md](ARCHITECTURE.md).

---

## 7. Primary user journeys

### 7.1 Scoped feature test

1. The operator opens New Run or Chat.
2. They enter an authorized URL and confirm authorization.
3. They name the feature on that site and the test type, for example signup, positive only.
4. The agent fills the fields that page actually shows and writes the executed case, such as `TC_SIGNUP_POS_001` when the feature is signup.
5. The test-case table and the CSV list that case, with status Executed and result Pass or Fail. Unexecuted cases are not marked Pass or Fail.

### 7.2 Validation defect

1. The agent submits a form empty, or submits values the application should reject.
2. If a validation message is visible, or the browser shows its native field message, the step is not a bug.
3. If the form stays on the screen and no validation message is shown, the agent records a bug titled for that operation and page, with the URL, expected result, actual result, and steps.

### 7.3 Create, update, and delete on the site under test

When the operator allows test-data creation, the agent can create a record through the form that site presents, edit that record, and delete it when deletion of its own records is allowed. On the public Contact List example this appears as signup, add contact, edit contact, and delete contact. On another site the same permissions follow that site’s own forms. A rejected value, such as an invalid phone number, is application feedback: the agent recovers and continues. One run is not a claim that every screen of that site was tested.

### 7.4 Review

1. The operator opens the live run, the Bugs tab, and the test-case report.
2. The bug count matches the bugs stored for the run.
3. The operator downloads the report and the bugs and tests CSV files.

---

## 8. Functional requirements

| ID | Requirement | Acceptance |
|---|---|---|
| FR-01 | The operator must acknowledge authorization before a run starts. | A run without `authorization_ack` is rejected. |
| FR-02 | The operator can point the agent at any authorized website from New Run or from chat. | Both paths create a run and open Chromium against that URL. No script for that site is required. |
| FR-03 | The operator can restrict the run to a feature and to positive, negative, or both. | If no type is selected, the run is positive only. Off-scope cases are not written as the executed list. |
| FR-04 | Executed cases use stable IDs such as `TC_SIGNUP_POS_001` and store the values that were typed, not internal element IDs. | The UI and the tests CSV agree on ID, status, and result. |
| FR-05 | Status is Executed or Not Executed. Result is Pass, Fail, or N/A. | A case that did not run is not Pass or Fail. |
| FR-06 | The agent fills and submits the forms the current website presents, when writes are permitted. | The same run works on a different authorized URL without a new script. |
| FR-07 | Empty submit is used as a validation probe before a happy-path fill. | The probe is part of the flow. A visible validation message is not filed as a bug. |
| FR-08 | A submit that stays on the form with no validation message is a confirmed bug. | The bug title names the operation and the page. Expected, actual, steps, and URL are stored. |
| FR-09 | A submit that navigates away is success. | It is not filed as a validation bug. |
| FR-10 | Delete runs only when the operator allows deletion of the agent’s own test records. | The agent does not delete records it did not create. A delete without a confirmation or a visible change is a bug. |
| FR-11 | The Bugs view lists every bug stored for the run. | The count, the list, the bug detail, and `bugs.csv` include the same records. |
| FR-12 | The operator can pause, continue, and end a live run, and can slow the action pace. | Controls take effect without disabling safety checks. |
| FR-13 | Evidence is kept per run: screenshots, activity log, test cases, bugs, and HTML, Markdown, and JSON reports. | Files are written under the run’s evidence directory and can be opened from the UI. |
| FR-14 | The operator can select Gemini, Ollama, Transformers, or Mock. | Mock runs with no live model. A missing key for Gemini is reported as not configured. The active provider for this submission is Gemini. |
| FR-15 | Page-title absence is not a product bug. | New runs do not file “Missing page title” defects. |

---

## 9. Safety requirements

| ID | Requirement |
|---|---|
| SR-01 | Only `http` and `https` targets are accepted. Local and private hosts are refused unless explicitly enabled for a local demo. |
| SR-02 | The agent stays on the authorized site. |
| SR-03 | Payments, refunds, purchases, credential changes, email sending, invitations, permission changes, and production deploys are blocked. |
| SR-04 | Writes require an explicit permission. Destructive cleanup requires a second, separate permission. |
| SR-05 | Test records the agent creates are tagged so later cleanup can tell them from existing application data. |
| SR-06 | Every browser action passes a deterministic safety check before it runs. The model may propose an action. It cannot bypass that check. |
| SR-07 | Model API keys stay in server environment variables. They are not written into reports or the browser. |

---

## 10. Reporting requirements

Each finished or ended run provides:

- An activity log of what the agent did.
- A test-case list and `tests.csv` with the QA columns used in the UI.
- A bug list and `bugs.csv` covering confirmed and suspected items that were stored.
- A final report in HTML, Markdown, and JSON.
- Screenshots tied to the actions that produced them.

Password values are not stored in the clear. The UI shows a password as a masked value.

---

## 11. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-01 | The browser is headless Chromium through Playwright. The default adapter is Direct Playwright. |
| NFR-02 | The live log is pushed over WebSocket. Run control uses the HTTP API. |
| NFR-03 | Run state, bugs, and chat history are stored in SQLite. Screenshots and reports are stored on disk for that run. |
| NFR-04 | The UI is a React, TypeScript, and Vite application. The API is FastAPI on Python 3.11 or newer. |
| NFR-05 | A run continues until the operator ends it, the scoped task is finished, or a safety stop fires, such as repeated model failures or no progress. There is no fixed action-count cap and no 15-minute watchdog. |
| NFR-06 | Intelligence modules that fail do not, by themselves, kill the browser loop. The failure is logged and the run continues when that is still safe. |

---

## 12. System context

| Layer | Technology | Role |
|---|---|---|
| UI | React, TypeScript, Vite, Tailwind | Chat, New Run, live log, bugs, test cases, reports |
| API | Python, FastAPI, Pydantic | Runs, chat, reports, WebSocket |
| Agent | AgentController | Observe, classify, plan, execute, report |
| Model | Gemini, Ollama, Transformers, or Mock | Optional page reasoning |
| Browser | Playwright, Chromium | Click, type, submit, screenshot |
| Storage | SQLite and the evidence directory | Runs, bugs, screenshots, reports |

The current reasoning provider for demonstration is Google Gemini (`gemini-3.5-flash`). Ollama and Transformers are local alternatives. Mock is the deterministic provider used for tests and for a run with no model configured.

---

## 13. Hackathon demo script

The product is for any authorized website. For a public judging session, the Thinking Tester Contact List is a convenient example because anyone can open it. The same steps apply if the judge supplies a different URL they are allowed to test.

1. Open QA Engine and confirm the model is Google Gemini.
2. In Chat or New Run, authorize that website and request one feature, such as a positive signup test.
3. Show the browser filling First Name, Last Name, email, and password, then submitting.
4. Open the test-case report and show one executed positive case with those field values.
5. With test-data creation enabled, let the agent create a record through the form on that site. Show that a validation message on an empty or invalid submit is not called a bug.
6. If a later submit stays on the form with no message, open that bug and show the URL, expected result, and actual result.
7. End the run and download the report.

Suggested narration: QA Engine tests the website you give it. It does the first browser pass a tester would do by hand, keeps the proof, and files a bug only when the application fails to say what is wrong.

---

## 14. Success criteria for this submission

| Criterion | Met when |
|---|---|
| A judge can start a run on any authorized website. | Chromium opens that URL and the activity log moves. Contact List is one public example. |
| Scope is honored. | A positive signup request produces the signup case, not a list of unrelated smoke cases. |
| Evidence matches the run. | Test-case status and the CSV agree. The bug list shows every stored bug. |
| Validation behavior is correct. | A message on the form is not a bug. A silent form that stays open is a bug. A successful navigation is not a bug. |
| The operator stays in control. | Pause, continue, and end work during the run. |
| The architecture is the one in the repository. | UI, FastAPI, AgentController, optional model, Playwright, evidence. |

---

## 15. Out of scope and known limits

- QA Engine will not cover every page of a large application in one run.
- It establishes one authenticated session per run. It does not switch between two user roles in the same session.
- The product API has no operator login. It must not be exposed on the public internet without an access control layer in front of it.
- Reports and screenshots live on the machine that ran the agent. Replacing that machine without copying the evidence directory removes them.
- Older runs may still contain historical “Missing page title” rows. Current code does not create those bugs. The Bugs view shows stored rows so a past run can still be inspected.
- Created test values use a fixed prefix so the agent can recognize its own records. That prefix is an implementation marker, not the product name shown in the UI.

---

## 16. Risks

| Risk | Mitigation in the product |
|---|---|
| The agent tests a site the operator does not own. | Authorization acknowledgement, URL checks, and a documented rule to test only permitted systems. |
| The agent deletes real customer data. | Deletion is off until enabled, and cleanup targets only records the agent created. |
| A model invents a selector or ignores safety. | Actions are chosen from observed elements and rejected by the safety validator before execution. |
| A model key leaks into a report. | Keys stay in environment variables and are excluded from health and report payloads. |
| Judges confuse a partial pass with full coverage. | The report and this document state that the run is a first pass, not exhaustive coverage. |

---

## 17. Release contents for the submission

| Item | Location |
|---|---|
| Source | [github.com/M-Ilays/QA-Engine](https://github.com/M-Ilays/QA-Engine) |
| This PRD | [PRD.md](PRD.md) |
| Architecture | [ARCHITECTURE.md](ARCHITECTURE.md), [docs/architecture.png](docs/architecture.png) |
| Operator setup | [README.md](README.md) |
| Example website | https://thinking-tester-contact-list.herokuapp.com/ |

QA Engine is ready for judging as a local or reviewer-run agent against any authorized website, with Gemini as the optional reasoning model and Playwright as the browser.
