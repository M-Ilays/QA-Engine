import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { FailureState, LoadingState } from "../components/States";
import { api } from "../services/api";
import type { TestCase } from "../types";

/* ─── Badges ─────────────────────────────────────────────────────────────── */

function ExecBadge({ status }: { status: string }) {
  return status === "Executed" ? (
    <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium bg-blue-500/10 text-blue-400">
      <span className="h-1.5 w-1.5 rounded-full bg-blue-400" />
      Executed
    </span>
  ) : (
    <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-medium bg-slate-500/10 text-slate-400">
      <span className="h-1.5 w-1.5 rounded-full bg-slate-500" />
      Not Executed
    </span>
  );
}

function ResultBadge({ result }: { result: string }) {
  if (result === "Pass") return (
    <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold bg-emerald-500/10 text-emerald-400">
      ✓ Pass
    </span>
  );
  if (result === "Fail") return (
    <span className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold bg-rose-500/10 text-rose-400">
      ✗ Fail
    </span>
  );
  return (
    <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-slate-700/50 text-slate-500">
      N/A
    </span>
  );
}

function TypeBadge({ type }: { type: string }) {
  const map: Record<string, string> = {
    positive: "text-emerald-400 bg-emerald-400/10",
    negative: "text-rose-400 bg-rose-400/10",
    exploratory: "text-violet-400 bg-violet-400/10",
  };
  const icon: Record<string, string> = { positive: "✅", negative: "❌", exploratory: "🔍" };
  return (
    <span className={`rounded px-1.5 py-0.5 text-xs font-medium ${map[type] ?? map.exploratory}`}>
      {icon[type] ?? "🔍"} {type.charAt(0).toUpperCase() + type.slice(1)}
    </span>
  );
}

/* ─── Test Data display ─────────────────────────────────────────────────── */

function TestDataCell({ tc }: { tc: TestCase }) {
  // Prefer structured JSON dict
  let entries: [string, string][] = [];

  if (tc.test_data_json && Object.keys(tc.test_data_json).length > 0) {
    entries = Object.entries(tc.test_data_json) as [string, string][];
  } else if (tc.test_data) {
    // tc.test_data is always a string here (normalized in api.ts)
    const raw: unknown = tc.test_data;
    const lines = Array.isArray(raw)
      ? (raw as string[]).filter(l => !/^(el_|field_|input_)\d+$/i.test(String(l)))
      : String(raw).split("\n").filter(Boolean);
    entries = lines
      .filter(l => l.includes(":"))
      .map(line => {
        const idx = line.indexOf(":");
        return [line.slice(0, idx).trim(), line.slice(idx + 1).trim()] as [string, string];
      });
  }

  if (entries.length === 0) return <span className="text-slate-600 text-xs">—</span>;

  return (
    <div className="space-y-0.5 max-h-24 overflow-hidden">
      {entries.slice(0, 5).map(([k, v], i) => (
        <div key={i} className="flex gap-1 text-xs">
          <span className="text-slate-500 shrink-0">{k}:</span>
          <span className="text-slate-200 font-mono truncate">{v}</span>
        </div>
      ))}
      {entries.length > 5 && <p className="text-xs text-slate-600">+{entries.length - 5} more…</p>}
    </div>
  );
}

/* ─── Expandable detail panel ───────────────────────────────────────────── */

function DetailPanel({ tc }: { tc: TestCase }) {
  return (
    <tr className="bg-slate-800/40">
      <td colSpan={5} className="px-6 py-4">
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3 text-xs">
          {/* Full test data */}
          {(tc.test_data_json && Object.keys(tc.test_data_json).length > 0) && (
            <div className="space-y-1">
              <p className="font-semibold uppercase tracking-wider text-slate-500">Test Data</p>
              <div className="space-y-0.5">
                {Object.entries(tc.test_data_json).map(([k, v]) => (
                  <div key={k} className="flex gap-1.5">
                    <span className="text-slate-400 w-28 shrink-0">{k}:</span>
                    <span className="text-slate-200 font-mono">{v}</span>
                  </div>
                ))}
              </div>
            </div>
          )}
          {/* Test Steps */}
          {tc.test_steps?.length > 0 && (
            <div className="space-y-1">
              <p className="font-semibold uppercase tracking-wider text-slate-500">Test Steps</p>
              <ol className="list-decimal pl-4 space-y-0.5 text-slate-300">
                {tc.test_steps.map((s, i) => <li key={i}>{s}</li>)}
              </ol>
            </div>
          )}
          {/* Expected vs Actual */}
          <div className="space-y-3">
            {tc.expected_result?.length > 0 && (
              <div className="space-y-1">
                <p className="font-semibold uppercase tracking-wider text-slate-500">Expected Result</p>
                <ul className="list-disc pl-4 space-y-0.5 text-slate-300">
                  {tc.expected_result.map((r, i) => <li key={i}>{r}</li>)}
                </ul>
              </div>
            )}
            {tc.actual_result && (
              <div className="space-y-1">
                <p className="font-semibold uppercase tracking-wider text-slate-500">Actual Result</p>
                <p className={tc.result === "Pass" ? "text-emerald-400" : tc.result === "Fail" ? "text-rose-400" : "text-slate-400"}>
                  {tc.actual_result}
                </p>
              </div>
            )}
          </div>
          {/* Meta */}
          <div className="space-y-1">
            <p className="font-semibold uppercase tracking-wider text-slate-500">Details</p>
            <p className="text-slate-300">Category: <span className="text-slate-100">{tc.category}</span></p>
            <p className="text-slate-300">Priority: <span className="text-slate-100">{tc.priority}</span></p>
            <p className="text-slate-300 flex items-center gap-1">Type: <TypeBadge type={tc.test_case_type ?? "exploratory"} /></p>
          </div>
          {/* Preconditions */}
          {tc.preconditions?.length > 0 && (
            <div className="space-y-1">
              <p className="font-semibold uppercase tracking-wider text-slate-500">Preconditions</p>
              <ul className="list-disc pl-4 space-y-0.5 text-slate-300">
                {tc.preconditions.map((p, i) => <li key={i}>{p}</li>)}
              </ul>
            </div>
          )}
        </div>
      </td>
    </tr>
  );
}

/* ─── Table row ─────────────────────────────────────────────────────────── */

function TableRow({ tc }: { tc: TestCase }) {
  const [expanded, setExpanded] = useState(false);
  return (
    <>
      <tr
        onClick={() => setExpanded(e => !e)}
        className="border-b border-slate-700/40 hover:bg-slate-800/40 cursor-pointer transition-colors"
      >
        <td className="px-4 py-3 align-top">
          <span className="font-mono text-xs font-semibold text-tide-400 whitespace-nowrap">{tc.test_case_id}</span>
        </td>
        <td className="px-4 py-3 align-top">
          <p className="text-sm text-slate-200">{tc.description || tc.title}</p>
        </td>
        <td className="px-4 py-3 align-top w-56">
          <TestDataCell tc={tc} />
        </td>
        <td className="px-4 py-3 align-top whitespace-nowrap">
          <ExecBadge status={tc.execution_status} />
        </td>
        <td className="px-4 py-3 align-top whitespace-nowrap">
          <div className="flex items-center gap-2">
            <ResultBadge result={tc.result} />
            <span className="text-slate-600 text-xs">{expanded ? "▲" : "▼"}</span>
          </div>
        </td>
      </tr>
      {expanded && <DetailPanel tc={tc} />}
    </>
  );
}

/* ─── Page ──────────────────────────────────────────────────────────────── */

type StatusFilter = "all" | "Executed" | "Not Executed";
type ResultFilter = "all" | "Pass" | "Fail" | "N/A";
type TypeFilter   = "all" | "positive" | "negative" | "exploratory";

export function TestCasesPage() {
  const { runId = "" } = useParams();
  const [cases, setCases] = useState<TestCase[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [resultFilter, setResultFilter] = useState<ResultFilter>("all");
  const [typeFilter, setTypeFilter]     = useState<TypeFilter>("all");

  useEffect(() => {
    let alive = true;
    api.getTestCases(runId)
      .then((data: { test_cases: TestCase[]; total: number }) => {
        if (alive) { setCases(data.test_cases); setLoading(false); }
      })
      .catch((e: unknown) => {
        if (alive) { setError(e instanceof Error ? e.message : "Failed to load"); setLoading(false); }
      });
    return () => { alive = false; };
  }, [runId]);

  if (loading) return <LoadingState label="Loading test cases…" />;
  if (error)   return <FailureState title="Test cases unavailable" detail={error} />;

  // Stats computed from canonical fields
  const stats = {
    total:        cases.length,
    executed:     cases.filter(c => c.execution_status === "Executed").length,
    not_executed: cases.filter(c => c.execution_status === "Not Executed").length,
    pass:         cases.filter(c => c.result === "Pass").length,
    fail:         cases.filter(c => c.result === "Fail").length,
  };

  const filtered = cases
    .filter(c => statusFilter === "all" || c.execution_status === statusFilter)
    .filter(c => resultFilter === "all" || c.result === resultFilter)
    .filter(c => typeFilter   === "all" || (c.test_case_type ?? "exploratory") === typeFilter);

  return (
    <div className="mx-auto max-w-7xl space-y-6 animate-rise">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <Link to={`/runs/${runId}`} className="text-xs text-slate-500 hover:text-tide-400">
            ← Back to run
          </Link>
          <h1 className="mt-2 text-2xl font-bold text-white">Test Cases</h1>
          <p className="text-sm text-slate-400">Run <code className="text-xs text-slate-300">{runId}</code></p>
        </div>
        <a
          href={`/api/runs/${runId}/tests.csv`}
          className="text-xs text-tide-400 hover:text-tide-300 border border-tide-400/30 rounded-lg px-3 py-1.5"
          download
        >
          ↓ Export CSV
        </a>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 sm:grid-cols-5 gap-3">
        {[
          { label: "Total",        value: stats.total,        colour: "text-white" },
          { label: "Executed",     value: stats.executed,     colour: "text-blue-400" },
          { label: "Not Executed", value: stats.not_executed, colour: "text-slate-400" },
          { label: "Pass",         value: stats.pass,         colour: "text-emerald-400" },
          { label: "Fail",         value: stats.fail,         colour: "text-rose-400" },
        ].map(s => (
          <div key={s.label} className="surface rounded-xl p-4 text-center">
            <p className={`text-2xl font-bold ${s.colour}`}>{s.value}</p>
            <p className="text-xs text-slate-500 mt-1">{s.label}</p>
          </div>
        ))}
      </div>

      {/* Filters */}
      <div className="flex flex-wrap gap-3 items-center">
        <div className="flex gap-1.5 items-center">
          <span className="text-xs text-slate-500">Status:</span>
          {(["all", "Executed", "Not Executed"] as StatusFilter[]).map(f => (
            <button key={f} onClick={() => setStatusFilter(f)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                statusFilter === f ? "bg-blue-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"
              }`}>
              {f === "all" ? "All" : f}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5 items-center">
          <span className="text-xs text-slate-500">Result:</span>
          {(["all", "Pass", "Fail", "N/A"] as ResultFilter[]).map(f => (
            <button key={f} onClick={() => setResultFilter(f)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium transition-colors ${
                resultFilter === f
                  ? f === "Pass" ? "bg-emerald-600 text-white"
                    : f === "Fail" ? "bg-rose-600 text-white"
                    : "bg-tide-500 text-white"
                  : "bg-slate-800 text-slate-400 hover:text-slate-200"
              }`}>
              {f}
            </button>
          ))}
        </div>
        <div className="flex gap-1.5 items-center">
          <span className="text-xs text-slate-500">Type:</span>
          {(["all", "positive", "negative", "exploratory"] as TypeFilter[]).map(f => (
            <button key={f} onClick={() => setTypeFilter(f)}
              className={`rounded-lg px-2.5 py-1 text-xs font-medium capitalize transition-colors ${
                typeFilter === f ? "bg-violet-600 text-white" : "bg-slate-800 text-slate-400 hover:text-slate-200"
              }`}>
              {f === "all" ? "All Types" : f}
            </button>
          ))}
        </div>
      </div>

      {/* Table */}
      {filtered.length === 0 ? (
        <div className="text-center py-16 text-slate-500">
          <p className="text-4xl mb-3">🧪</p>
          <p>No test cases match the selected filters.</p>
          {cases.length === 0 && (
            <p className="text-xs mt-2">Test cases are generated when the run completes its report.</p>
          )}
        </div>
      ) : (
        <div className="surface rounded-xl overflow-hidden">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-slate-700/60 bg-slate-800/60">
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400 w-40">
                  Test Case ID
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400">
                  Test Case Description
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400 w-52">
                  Test Data
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400 w-36">
                  Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wider text-slate-400 w-28">
                  Result
                </th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(tc => (
                <TableRow key={tc.test_case_id} tc={tc} />
              ))}
            </tbody>
          </table>
          <div className="px-4 py-2 border-t border-slate-700/40 text-xs text-slate-500">
            Showing {filtered.length} of {cases.length} test cases · Click any row to expand details
          </div>
        </div>
      )}
    </div>
  );
}
