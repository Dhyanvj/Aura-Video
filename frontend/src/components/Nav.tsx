import { NavLink } from "react-router-dom";

const links = [
  { to: "/", label: "Pipeline", end: true },
  { to: "/approvals", label: "Approval Queue" },
  { to: "/series", label: "Series" },
  { to: "/trends", label: "Trends" },
  { to: "/analytics", label: "Analytics" },
  { to: "/settings", label: "Settings" },
];

export default function Nav() {
  return (
    <header className="flex items-center justify-between border-b border-border bg-panel px-6 py-3">
      <div className="text-lg font-semibold tracking-tight text-slate-100">
        Aura <span className="text-accent">Video Studio</span>
      </div>
      <nav className="flex gap-1">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            end={link.end}
            className={({ isActive }) =>
              `rounded px-3 py-1.5 text-sm font-medium transition-colors ${
                isActive ? "bg-accent text-white" : "text-slate-300 hover:bg-panel2 hover:text-white"
              }`
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
    </header>
  );
}
