/**
 * Sign in / sign up -- the gateway into the console.
 *
 * Honest framing, front and centre: this is NOT authentication. There is no
 * server account to create or verify. Both tabs do the same real thing --
 * record who is operating this console so the name can be attributed to the
 * compliance decisions the backend requires a named reviewer for. "Sign up"
 * and "Sign in" differ only in copy, because a returning demo user and a first
 * one are the same to a system with no user store; claiming otherwise would be
 * theatre. The page says all of this rather than hiding it behind a lock icon.
 */

import { useState } from "react"
import { Link, useNavigate } from "react-router-dom"
import { ArrowLeft, ArrowRight, Info, ShieldCheck } from "lucide-react"
import { Button, Input, Select } from "@/components/ui"
import { REVIEWER_ROLES, useSession, type ReviewerRole } from "@/lib/session"

type Tab = "signin" | "signup"

export default function SignIn() {
  const { session, signIn } = useSession()
  const navigate = useNavigate()
  const [tab, setTab] = useState<Tab>(session ? "signin" : "signup")
  const [name, setName] = useState(session?.name ?? "")
  const [role, setRole] = useState<ReviewerRole>(session?.role ?? "Compliance Officer")

  function submit(e: React.FormEvent) {
    e.preventDefault()
    if (!name.trim()) return
    signIn(name, role)
    navigate("/", { replace: true })
  }

  return (
    <div className="grid min-h-screen lg:grid-cols-2">
      {/* Left: brand + the one thing that matters here */}
      <div className="hidden flex-col justify-between bg-gradient-to-br from-primary/10 to-muted/40 p-10 lg:flex">
        <Link to="/" className="flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-primary" />
          <div>
            <p className="text-sm font-semibold leading-tight">Continuous KYC</p>
            <p className="text-[10px] text-muted-foreground">Autonomous Auditor</p>
          </div>
        </Link>
        <div className="max-w-md">
          <h2 className="text-2xl font-semibold tracking-tight">Every decision carries a name.</h2>
          <p className="mt-3 text-sm leading-relaxed text-muted-foreground">
            The console won't let you approve a SAR, confirm a match, or close a case anonymously — the backend
            rejects an unattributed compliance decision. The name you enter here is recorded, verbatim, on everything
            you do, and appears in the audit trail as the human accountable for it.
          </p>
        </div>
        <p className="text-[11px] text-muted-foreground">
          Deterministic scoring · AI explains, never decides · Humans approve
        </p>
      </div>

      {/* Right: the form */}
      <div className="flex flex-col justify-center p-6 sm:p-10">
        <div className="mx-auto w-full max-w-sm">
          <Link to="/" className="mb-6 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground">
            <ArrowLeft className="h-3.5 w-3.5" /> Back to home
          </Link>

          <div className="mb-4 flex rounded-lg border p-0.5 text-sm">
            <button
              type="button"
              onClick={() => setTab("signin")}
              aria-pressed={tab === "signin"}
              className={`flex-1 rounded-md px-3 py-1.5 transition-colors ${tab === "signin" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              Sign in
            </button>
            <button
              type="button"
              onClick={() => setTab("signup")}
              aria-pressed={tab === "signup"}
              className={`flex-1 rounded-md px-3 py-1.5 transition-colors ${tab === "signup" ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"}`}
            >
              Sign up
            </button>
          </div>

          <h1 className="text-xl font-semibold">
            {tab === "signin" ? "Sign in to the console" : "Set up your reviewer identity"}
          </h1>
          <p className="mt-1 text-xs text-muted-foreground">
            {tab === "signin"
              ? "Enter the name your decisions should be recorded under."
              : "Choose the name and role that will be attributed to your compliance decisions."}
          </p>

          <form onSubmit={submit} className="mt-5 space-y-4">
            <div>
              <label htmlFor="name" className="mb-1 block text-xs font-medium">
                Full name <span className="text-destructive">*</span>
              </label>
              <Input
                id="name"
                autoFocus
                placeholder="e.g. Aniket Meena"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>
            <div>
              <label htmlFor="role" className="mb-1 block text-xs font-medium">
                Role
              </label>
              <Select id="role" value={role} onChange={(e) => setRole(e.target.value as ReviewerRole)}>
                {REVIEWER_ROLES.map((r) => (
                  <option key={r} value={r}>
                    {r}
                  </option>
                ))}
              </Select>
              <p className="mt-1 text-[10px] text-muted-foreground">
                Recorded alongside your name for context. It does not grant permissions — the backend gates actions by
                case state, not by role.
              </p>
            </div>

            <Button type="submit" className="w-full" disabled={!name.trim()}>
              {tab === "signin" ? "Enter workspace" : "Create session & enter"} <ArrowRight className="h-4 w-4" />
            </Button>
          </form>

          {/* The honest disclosure. Not buried -- this is the whole truth of the screen. */}
          <p className="mt-5 flex items-start gap-1.5 rounded border border-blue-200 bg-blue-50 p-2.5 text-[11px] leading-relaxed text-blue-800">
            <Info className="mt-px h-3.5 w-3.5 shrink-0" />
            <span>
              This is <strong>demo session identity, not authentication</strong>. There is no password and no server
              account — the name is stored only in this browser and used to attribute your actions in the audit trail.
              It protects nothing; it makes decisions accountable.
            </span>
          </p>
        </div>
      </div>
    </div>
  )
}
