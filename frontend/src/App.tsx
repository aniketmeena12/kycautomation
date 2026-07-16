/**
 * App shell + routing.
 *
 * Every page except the dashboard is lazy-loaded, so the initial bundle carries
 * the shell and the landing page only.
 */

import { Suspense, lazy } from "react"
import { NavLink, Navigate, Route, Routes } from "react-router-dom"
import {
  Activity,
  ClipboardList,
  FileText,
  GaugeCircle,
  History,
  ScrollText,
  ShieldCheck,
  Users,
} from "lucide-react"
import { LoadingBlock } from "@/components/ui"
import { cn } from "@/lib/utils"

const Dashboard = lazy(() => import("@/pages/Dashboard"))
const Customers = lazy(() => import("@/pages/Customers"))
const Customer360Page = lazy(() => import("@/pages/Customer360"))
const InvestigationPage = lazy(() => import("@/pages/Investigation"))
const TimelinePage = lazy(() => import("@/pages/Timeline"))
const CasesPage = lazy(() => import("@/pages/Cases"))
const CaseDetailPage = lazy(() => import("@/pages/CaseDetail"))
const SarPage = lazy(() => import("@/pages/Sar"))
const AuditPage = lazy(() => import("@/pages/Audit"))
const SystemHealthPage = lazy(() => import("@/pages/SystemHealth"))

const NAV = [
  { to: "/", label: "Dashboard", icon: GaugeCircle, end: true },
  { to: "/customers", label: "Customers", icon: Users },
  { to: "/cases", label: "Cases", icon: ClipboardList },
  { to: "/timeline", label: "Timeline", icon: History },
  { to: "/sar", label: "Draft SAR", icon: FileText },
  { to: "/audit", label: "Audit trail", icon: ScrollText },
  { to: "/system", label: "System health", icon: Activity },
]

function Sidebar() {
  return (
    <nav aria-label="Main navigation" className="hidden w-56 shrink-0 flex-col border-r bg-card md:flex">
      <div className="flex items-center gap-2 border-b px-4 py-3">
        <ShieldCheck className="h-5 w-5 text-primary" aria-hidden="true" />
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold leading-tight">Continuous KYC</p>
          <p className="text-[10px] text-muted-foreground">Autonomous Auditor</p>
        </div>
      </div>
      <ul className="flex-1 space-y-0.5 p-2">
        {NAV.map((item) => (
          <li key={item.to}>
            <NavLink
              to={item.to}
              end={item.end}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-2 rounded-md px-3 py-2 text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                  isActive ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-accent",
                )
              }
            >
              <item.icon className="h-4 w-4" aria-hidden="true" />
              {item.label}
            </NavLink>
          </li>
        ))}
      </ul>
      <div className="border-t p-3">
        <p className="text-[10px] leading-relaxed text-muted-foreground">
          Deterministic scoring. AI explains, never decides. Humans approve.
        </p>
      </div>
    </nav>
  )
}

/** Mobile: the sidebar becomes a horizontal scroller rather than a hamburger
 *  drawer. Every destination stays one tap away and there is no second
 *  navigation model to keep in sync -- "mobile graceful", not mobile-first. */
function MobileNav() {
  return (
    <nav aria-label="Main navigation" className="flex gap-1 overflow-x-auto border-b bg-card p-2 md:hidden">
      {NAV.map((item) => (
        <NavLink
          key={item.to}
          to={item.to}
          end={item.end}
          className={({ isActive }) =>
            cn(
              "flex shrink-0 items-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs",
              isActive ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground",
            )
          }
        >
          <item.icon className="h-3.5 w-3.5" aria-hidden="true" />
          {item.label}
        </NavLink>
      ))}
    </nav>
  )
}

export default function App() {
  return (
    <div className="flex min-h-screen">
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50 focus:rounded focus:bg-primary focus:px-3 focus:py-2 focus:text-primary-foreground"
      >
        Skip to content
      </a>
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <MobileNav />
        <main id="main" className="flex-1 p-4 md:p-6">
          <Suspense fallback={<LoadingBlock label="Loading page" rows={6} />}>
            <Routes>
              <Route path="/" element={<Dashboard />} />
              <Route path="/customers" element={<Customers />} />
              <Route path="/customers/:clientId" element={<Customer360Page />} />
              <Route path="/investigations/:investigationId" element={<InvestigationPage />} />
              <Route path="/timeline" element={<TimelinePage />} />
              <Route path="/timeline/:caseId" element={<TimelinePage />} />
              <Route path="/cases" element={<CasesPage />} />
              <Route path="/cases/:caseId" element={<CaseDetailPage />} />
              <Route path="/sar" element={<SarPage />} />
              <Route path="/sar/:caseId" element={<SarPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/audit/:caseId" element={<AuditPage />} />
              <Route path="/system" element={<SystemHealthPage />} />
              <Route path="*" element={<Navigate to="/" replace />} />
            </Routes>
          </Suspense>
        </main>
      </div>
    </div>
  )
}
