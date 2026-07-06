import { Route, Routes } from "react-router-dom";
import Nav from "./components/Nav";
import Dashboard from "./pages/Dashboard";
import PipelineBoard from "./pages/PipelineBoard";
import NewVideo from "./pages/NewVideo";
import ProjectDetail from "./pages/ProjectDetail";
import ApprovalQueue from "./pages/ApprovalQueue";
import SeriesPage from "./pages/SeriesPage";
import Trends from "./pages/Trends";
import Analytics from "./pages/Analytics";
import Settings from "./pages/Settings";

export default function App() {
  return (
    <div className="min-h-screen bg-canvas">
      <Nav />
      <main className="mx-auto max-w-7xl px-4 py-6 sm:px-6">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/pipeline" element={<PipelineBoard />} />
          <Route path="/new" element={<NewVideo />} />
          <Route path="/projects/:id" element={<ProjectDetail />} />
          <Route path="/approvals" element={<ApprovalQueue />} />
          <Route path="/series" element={<SeriesPage />} />
          <Route path="/trends" element={<Trends />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
