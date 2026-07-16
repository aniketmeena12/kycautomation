/**
 * App shell + routing.
 *
 * Two zones. `/welcome` (landing) and `/signin` are PUBLIC -- the front door.
 * Everything else lives behind `RequireSession`, which redirects to the landing
 * page when no reviewer identity has been set. That gate is not security (there
 * is no server auth, and client-side redirects protect nothing); it exists so
 * every action inside the console carries a name, which is what the backend's
 * compliance workflow already demands. All app routes keep their original
 * absolute paths so no internal link had to change.
 *
 * Every page except the dashboard is lazy-loaded, so the initial bundle carries
 * the shell and whichever entry point (landing or dashboard) the visitor lands on.
 */

import { Suspense, lazy } from "react"
import { NavLink, Navigate, Route, Routes } from "react-router-dom"
import {
  Activity,
  ClipboardList,
  FileText,
  GaugeCircle,
  History,
  LogOut,
  ScrollText,
  ShieldCheck,
  Users,
} from "lucide-react"
import { Button, LoadingBlock } from "@/components/ui"
import { cn } from "@/lib/utils"
import { useSession } from "@/lib/session"

const Landing = lazy(() => import("@/pages/Landing"))
const SignIn = lazy(() => import("@/pages/SignIn"))
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

/** The signed-in reviewer, shown wherever an action will be attributed to them,
 *  with the one-click way to forget that identity. Sign-out clears the local
 *  session; RequireSession then redirects to the landing page on re-render. */
function IdentityCard() {
  const { session, signOut } = useSession()
  if (!session) return null
  const initials = session.name
    .split(/\s+/)
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase()
  return (
    <div className="border-t p-3">
      <div className="flex items-center gap-2">
        <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-primary/10 text-xs font-semibold text-primary">
          {initials || "?"}
        </div>
        <div className="min-w-0 flex-1">
          <p className="truncate text-xs font-medium">{session.name}</p>
          <p className="truncate text-[10px] text-muted-foreground">{session.role}</p>
        </div>
        <Button variant="ghost" size="icon" onClick={signOut} title="Sign out" aria-label="Sign out">
          <LogOut className="h-4 w-4" />
        </Button>
      </div>
      <p className="mt-2 text-[10px] leading-relaxed text-muted-foreground">
        Your decisions are recorded under this name. Demo identity — not authentication.
      </p>
    </div>
  )
}

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
      <IdentityCard />
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

/** The authenticated console. Rendered only when a reviewer identity exists;
 *  otherwise the visitor is sent to the landing page to establish one. */
function Console() {
  const { session } = useSession()
  if (!session) return <Navigate to="/welcome" replace />
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

export default function App() {
  return (
    <Suspense fallback={<LoadingBlock label="Loading" rows={6} />}>
      <Routes>
        <Route path="/welcome" element={<Landing />} />
        <Route path="/signin" element={<SignIn />} />
        <Route path="/*" element={<Console />} />
      </Routes>
    </Suspense>
  )
}
