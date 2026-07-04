import { Route, Routes } from "react-router-dom";
import Nav from "./components/Nav";
import PipelineBoard from "./pages/PipelineBoard";
import ProjectDetail from "./pages/ProjectDetail";
import ApprovalQueue from "./pages/ApprovalQueue";
import Trends from "./pages/Trends";
import Analytics from "./pages/Analytics";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-screen bg-canvas">
      <Nav />
      <main className="mx-auto max-w-7xl px-6 py-6">
        <Routes>
          <Route path="/" element={<PipelineBoard />} />
          <Route path="/projects/:id" element={<ProjectDetail />} />
          <Route path="/approvals" element={<ApprovalQueue />} />
          <Route path="/trends" element={<Trends />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
