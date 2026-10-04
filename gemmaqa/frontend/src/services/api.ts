import { env, resolveApiUrl, resolveWsUrl } from "../config/env";
import type {
  ActionItem,
  ActivityLogPage,
  BugItem,
  CreateRunRequest,
  CreateRunResponse,
  EvidenceFile,
  FinalReport,
  PageItem,
  ProviderCatalog,
  RunStatus,
} from "../types";
import { demoApi } from "../demo/fixtures";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(resolveApiUrl(path), {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.headers || {}),
    },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(text || `Request failed (${res.status})`);
  }
  if (res.status === 204) return undefined as T;
  const ct = res.headers.get("content-type") || "";
  if (ct.includes("application/json")) {
    return res.json() as Promise<T>;
  }
  return (await res.text()) as T;
}

/** FastAPI `{detail}` bodies, otherwise the raw error text. */
export function parseApiError(err: unknown): string {
  if (!(err instanceof Error)) return "Request failed";
  try {
    const parsed = JSON.parse(err.message) as { detail?: unknown };
    if (typeof parsed.detail === "string") return parsed.detail;
    if (Array.isArray(parsed.detail)) {
      return parsed.detail
        .map((item) =>
          typeof item === "object" && item && "msg" in item
            ? String((item as { msg: unknown }).msg)
            : String(item),
        )
        .join("; ");
    }
  } catch {
    return err.message;
  }
  return err.message;
}

export type ExportKind = "json" | "md" | "html" | "bugs.csv" | "tests.csv" | "navigation.mmd";

const liveApi = {
  health: () => request<{ status: string; app: string; version: string }>("/health"),
  listProviders: () => request<ProviderCatalog>("/api/config/providers"),
  setProvider: (provider: string) =>
    request<ProviderCatalog>("/api/config/provider", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  listRuns: async () => {
    const data = await request<{ items: RunStatus[] } | RunStatus[]>("/api/runs");
    return Array.isArray(data) ? data : data.items;
  },
  getRun: (runId: string) => request<RunStatus>(`/api/runs/${runId}`),
  createRun: (body: CreateRunRequest) =>
    request<CreateRunResponse>("/api/runs", {
      method: "POST",
      body: JSON.stringify({ ...body, auto_start: true }),
    }),
  startRun: (runId: string) =>
    request<RunStatus>(`/api/runs/${runId}/start`, { method: "POST" }),
  cancelRun: (runId: string) =>
    request<RunStatus>(`/api/runs/${runId}/end`, { method: "POST" }),
  pauseRun: (runId: string) =>
    request<RunStatus>(`/api/runs/${runId}/pause`, { method: "POST" }),
  resumeRun: (runId: string) =>
    request<RunStatus>(`/api/runs/${runId}/resume`, { method: "POST" }),
  setRunPacing: (
    runId: string,
    body: { execution_speed?: number; action_pause?: number },
  ) =>
    request<RunStatus>(`/api/runs/${runId}/pacing`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getActions: (runId: string) => request<ActionItem[]>(`/api/runs/${runId}/actions`),
  // The durable activity log. `sinceSeq` fetches only what the caller has not
  // seen, so the live view can hydrate its full history on mount and then keep
  // up without refetching everything.
  getActivity: (runId: string, sinceSeq = 0, limit = 500) =>
    request<ActivityLogPage>(
      `/api/runs/${runId}/activity?since_seq=${sinceSeq}&limit=${limit}`,
    ),
  getBugs: (runId: string) => request<BugItem[]>(`/api/runs/${runId}/bugs`),
  getPages: (runId: string) => request<PageItem[]>(`/api/runs/${runId}/pages`),
  getReport: (runId: string) => request<FinalReport>(`/api/runs/${runId}/report`),
  getTestCases: async (runId: string) => {
    type RawTC = Record<string, unknown>;
    const data = await request<{ test_cases: RawTC[]; total: number }>(`/api/runs/${runId}/test-cases`);

    /** Normalize both old (test_id/status/steps/expected_results) and new
     *  (test_case_id/execution_status/result) API shapes into TestCase. */
    function norm(raw: RawTC): import('../types').TestCase {
      // ── status/result ──────────────────────────────────────────────────
      const internal = (raw.status as string) || "not_tested";
      const execStatus =
        (raw.execution_status as string) ||
        (internal === "passed" || internal === "failed" ? "Executed" : "Not Executed");
      const result =
        (raw.result as string) ||
        (internal === "passed" ? "Pass" : internal === "failed" ? "Fail" : "N/A");

      // ── test data ──────────────────────────────────────────────────────
      let testDataStr = "";
      let testDataJson: Record<string, string> = {};
      const rawTdJson = raw.test_data_json;
      if (rawTdJson && typeof rawTdJson === "object" && !Array.isArray(rawTdJson)) {
        testDataJson = rawTdJson as Record<string, string>;
        testDataStr = Object.entries(testDataJson).map(([k, v]) => `${k}: ${v}`).join("\n");
      } else if (raw.test_data) {
        // Handle both array (old) and string (new)
        const lines: string[] = Array.isArray(raw.test_data)
          ? (raw.test_data as string[]).filter(l => !/^(el_|field_|input_|btn_)\d+$/i.test(String(l)))
          : String(raw.test_data).split("\n").filter(Boolean);
        testDataStr = lines.join("\n");
        lines.forEach(line => {
          const idx = line.indexOf(":");
          if (idx > 0) testDataJson[line.slice(0, idx).trim()] = line.slice(idx + 1).trim();
        });
      }

      // ── friendly test case ID ──────────────────────────────────────────
      // If the backend already gave a friendly ID (TC_SIGNUP_POS_001) use it.
      // Otherwise derive one from title + category so UUIDs never appear.
      const rawId = (raw.test_case_id as string) || (raw.test_id as string) || "";
      const isUuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(rawId);
      let friendlyId = rawId;
      if (!rawId || isUuid) {
        const MODULE_MAP: [string[], string][] = [
          [["signup","sign up","register","registration"], "SIGNUP"],
          [["login","sign in","signin"], "LOGIN"],
          [["logout","log out"], "LOGOUT"],
          [["contact"], "CONTACT"],
          [["search","filter"], "SEARCH"],
          [["add","create","new"], "CREATE"],
          [["edit","update","modify"], "UPDATE"],
          [["delete","remove"], "DELETE"],
          [["smoke","page title","sanity"], "SMOKE"],
          [["form"], "FORM"],
          [["nav","navigation","menu"], "NAV"],
        ];
        const text = ((raw.title as string || "") + " " + (raw.category as string || "") + " " + (raw.description as string || "")).toLowerCase();
        let module = "TEST";
        for (const [kws, lbl] of MODULE_MAP) {
          if (kws.some(k => text.includes(k))) { module = lbl; break; }
        }
        const negKws = ["invalid","empty","missing","wrong","error","fail","reject","negative","bad","boundary"];
        const isNeg = negKws.some(k => text.includes(k));
        const typeMarker = isNeg ? "NEG" : "POS";
        // Short hash from uuid or index for uniqueness
        const shortHash = rawId ? rawId.slice(0, 3).toUpperCase() : "001";
        friendlyId = `TC_${module}_${typeMarker}_${shortHash}`;
      }

      return {
        test_case_id:     friendlyId,
        test_case_type:   ((raw.test_case_type as string) || "exploratory") as "positive" | "negative" | "exploratory",
        title:            (raw.title as string) || "",
        description:      (raw.description as string) || (raw.title as string) || "",
        test_data:        testDataStr,
        test_data_json:   testDataJson,
        execution_status: execStatus as "Executed" | "Not Executed",
        result:           result as "Pass" | "Fail" | "N/A",
        category:         (raw.category as string) || "",
        priority:         (raw.priority as string) || "medium",
        preconditions:    (raw.preconditions as string[]) || [],
        test_steps:       (raw.test_steps as string[]) || (raw.steps as string[]) || [],
        expected_result:  (raw.expected_result as string[]) || (raw.expected_results as string[]) || [],
        actual_result:    (raw.actual_result as string) || "",
      };
    }

    return { test_cases: data.test_cases.map(norm), total: data.total };
  },
  getReportMarkdown: (runId: string) => request<string>(`/api/runs/${runId}/report.md`),
  getEvidence: (runId: string) => request<EvidenceFile[]>(`/api/runs/${runId}/evidence`),
  getNavigationMermaid: (runId: string) =>
    request<string>(`/api/runs/${runId}/navigation.mmd`),
  evidenceFileUrl: (runId: string, relativePath: string) =>
    resolveApiUrl(
      `/api/runs/${runId}/evidence/file/${relativePath.replace(/^\/+/, "")}`
    ),
  exportUrl: (runId: string, kind: ExportKind) => {
    const paths: Record<ExportKind, string> = {
      json: `/api/runs/${runId}/report.json`,
      md: `/api/runs/${runId}/report.md`,
      html: `/api/runs/${runId}/report.html`,
      "bugs.csv": `/api/runs/${runId}/bugs.csv`,
      "tests.csv": `/api/runs/${runId}/tests.csv`,
      "navigation.mmd": `/api/runs/${runId}/navigation.mmd`,
    };
    return resolveApiUrl(paths[kind]);
  },
};

export const api = env.demoMode ? demoApi : liveApi;
export { resolveWsUrl as wsUrl, env };
