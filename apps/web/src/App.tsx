import { Route, Routes } from "react-router-dom";
import AppShell from "./components/AppShell";
import PreparePage from "./pages/PreparePage";
import HallPage from "./pages/HallPage";
import InterviewPage from "./pages/InterviewPage";
import ReportPage from "./pages/ReportPage";
import TrainingPage from "./pages/TrainingPage";
import SkillsPage from "./pages/SkillsPage";
import NotFoundPage from "./pages/NotFoundPage";

export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route path="/" element={<PreparePage />} />
        <Route path="/hall" element={<HallPage />} />
        <Route path="/interviews/:id" element={<InterviewPage />} />
        <Route path="/interviews/:id/report" element={<ReportPage />} />
        <Route path="/training" element={<TrainingPage />} />
        <Route path="/skills" element={<SkillsPage />} />
        <Route path="*" element={<NotFoundPage />} />
      </Route>
    </Routes>
  );
}
