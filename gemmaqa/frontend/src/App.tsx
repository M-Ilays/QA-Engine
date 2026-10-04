import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { BugDetailsPage } from "./pages/BugDetailsPage";
import { EvidenceViewerPage } from "./pages/EvidenceViewerPage";
import { FinalReportPage } from "./pages/FinalReportPage";
import { LandingPage } from "./pages/LandingPage";
import { LiveRunPage } from "./pages/LiveRunPage";
import { NewRunPage } from "./pages/NewRunPage";
import { RunHistoryPage } from "./pages/RunHistoryPage";
import { SettingsPage } from "./pages/SettingsPage";
import { TestDetailsPage } from "./pages/TestDetailsPage";
import { TestCasesPage } from "./pages/TestCasesPage";
import { ChatInterface } from "./components/ChatInterface";

export default function App() {
  return (
    <Routes>
      {/* Chat gets its own full-page layout */}
      <Route path="/chat" element={<ChatInterface />} />

      {/* All other pages use AppShell */}
      <Route path="*" element={
        <AppShell>
          <Routes>
            <Route path="/" element={<LandingPage />} />
            <Route path="/runs/new" element={<NewRunPage />} />
            <Route path="/runs/:runId" element={<LiveRunPage />} />
            <Route path="/runs/:runId/report" element={<FinalReportPage />} />
            <Route path="/runs/:runId/bugs/:bugId" element={<BugDetailsPage />} />
            <Route path="/runs/:runId/tests/:testId" element={<TestDetailsPage />} />
            <Route path="/runs/:runId/test-cases" element={<TestCasesPage />} />
            <Route path="/runs/:runId/evidence" element={<EvidenceViewerPage />} />
            <Route path="/history" element={<RunHistoryPage />} />
            <Route path="/reports" element={<Navigate to="/history" replace />} />
            <Route path="/settings" element={<SettingsPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </AppShell>
      } />
    </Routes>
  );
}
