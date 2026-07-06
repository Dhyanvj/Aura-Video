import { NavLink } from "react-router-dom";
import { useTheme } from "../theme";

const links = [
  { to: "/", label: "Dashboard", end: true },
  { to: "/pipeline", label: "Pipeline" },
  { to: "/approvals", label: "Approval Queue" },
  { to: "/series", label: "Series" },
  { to: "/trends", label: "Trends" },
  { to: "/analytics", label: "Analytics" },
  { to: "/settings", label: "Settings" },
];

export default function Nav() {
  const [theme, setTheme] = useTheme();

  return (
    <header className="flex flex-col gap-2 border-b border-border bg-panel px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:px-6">
      <div className="flex items-center justify-between">
        <div className="text-lg font-semibold tracking-tight text-slate-900 dark:text-slate-100">
          Aura <span className="text-accent">Video Studio</span>
        </div>
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label="Toggle dark/light theme"
          className="rounded border border-border bg-panel2 px-2 py-1 text-xs text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white sm:hidden"
        >
          {theme === "dark" ? "Light mode" : "Dark mode"}
        </button>
      </div>
      <nav className="flex gap-1 overflow-x-auto">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              `whitespace-nowrap rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                isActive
                  ? "bg-accent text-white"
                  : "text-slate-600 hover:bg-panel2 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white"
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
        <button
          onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label="Toggle dark/light theme"
          className="ml-auto hidden shrink-0 rounded border border-border bg-panel2 px-3 py-1.5 text-sm text-slate-600 hover:text-slate-900 dark:text-slate-300 dark:hover:text-white sm:block"
        >
          {theme === "dark" ? "☀ Light" : "🌙 Dark"}
        </button>
      </nav>
    </header>
  );
}
