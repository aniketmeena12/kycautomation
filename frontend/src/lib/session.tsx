/**
 * Demo session identity -- deliberately NOT authentication.
 *
 * This system has no auth backend: no users table, no passwords, no server
 * sessions. Pretending otherwise -- a login that accepts anything and claims to
 * "authenticate" -- would be exactly the fake functionality this project
 * refuses to ship. So this is the honest version of what a login screen is
 * actually for here: capturing WHO is operating the console, so their name can
 * be attributed to the compliance decisions the backend already requires a
 * named reviewer for (CaseService.apply_review rejects an unattributed
 * decision). It is identity for the audit trail, not a security boundary, and
 * the UI says so in plain words wherever it appears.
 *
 * Stored in localStorage, so it survives a refresh but never leaves this
 * browser. Clearing it ("Sign out") just forgets the name; it protects nothing.
 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react"

export type ReviewerRole = "Compliance Officer" | "Senior Compliance" | "Analyst" | "Auditor" | "Observer"

export const REVIEWER_ROLES: ReviewerRole[] = [
  "Compliance Officer",
  "Senior Compliance",
  "Analyst",
  "Auditor",
  "Observer",
]

export interface Session {
  name: string
  role: ReviewerRole
  signedInAt: string
}

interface SessionContextValue {
  session: Session | null
  signIn: (name: string, role: ReviewerRole) => void
  signOut: () => void
}

const STORAGE_KEY = "kyc.session.v1"

function readStored(): Session | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<Session>
    // A stored blob missing the fields we depend on is treated as no session,
    // not patched into a half-identity that could attribute a decision to "".
    if (!parsed.name || !parsed.role) return null
    return { name: parsed.name, role: parsed.role as ReviewerRole, signedInAt: parsed.signedInAt ?? "" }
  } catch {
    return null
  }
}

const SessionContext = createContext<SessionContextValue | null>(null)

export function SessionProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => readStored())

  const signIn = useCallback((name: string, role: ReviewerRole) => {
    const trimmed = name.trim()
    if (!trimmed) return
    // new Date() is fine in the browser; the no-Date rule is a workflow-script
    // constraint, not a frontend one.
    const next: Session = { name: trimmed, role, signedInAt: new Date().toISOString() }
    localStorage.setItem(STORAGE_KEY, JSON.stringify(next))
    setSession(next)
  }, [])

  const signOut = useCallback(() => {
    localStorage.removeItem(STORAGE_KEY)
    setSession(null)
  }, [])

  // Keep two tabs of the same browser consistent: signing out in one should
  // not leave the other operating under a name it thinks is still valid.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === STORAGE_KEY) setSession(readStored())
    }
    window.addEventListener("storage", onStorage)
    return () => window.removeEventListener("storage", onStorage)
  }, [])

  const value = useMemo<SessionContextValue>(() => ({ session, signIn, signOut }), [session, signIn, signOut])
  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>
}

export function useSession(): SessionContextValue {
  const ctx = useContext(SessionContext)
  if (!ctx) throw new Error("useSession must be used within a SessionProvider")
  return ctx
}
