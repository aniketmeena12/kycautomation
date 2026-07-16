import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function fmtDate(value?: string | null) {
  if (!value) return "--"
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? "--"
    : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "2-digit" })
}

export function fmtDateTime(value?: string | null) {
  if (!value) return "--"
  const d = new Date(value)
  return Number.isNaN(d.getTime())
    ? "--"
    : d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
      })
}

/**
 * Render a possibly-null number.
 *
 * `null` and `0` mean genuinely different things across this API -- a null
 * latency means "nothing ran", not "instant"; a null laundering count means
 * "the source carries no label", not "we checked and found none". Coercing
 * null to 0 for display would erase a distinction the backend went out of its
 * way to preserve, so this returns an em-dash instead.
 */
export function fmtOrDash(value: number | null | undefined, suffix = "") {
  return value === null || value === undefined ? "--" : `${value.toLocaleString()}${suffix}`
}

export function fmtNumber(value?: number | null) {
  return fmtOrDash(value)
}

export function fmtMoney(value?: number | null) {
  if (value === null || value === undefined) return "--"
  return value.toLocaleString(undefined, { maximumFractionDigits: 0 })
}

/** Human-readable enum: RISK_SCORE_CHANGE -> Risk score change */
export function humanize(value?: string | null) {
  if (!value) return "--"
  const s = value.replace(/_/g, " ").toLowerCase()
  return s.charAt(0).toUpperCase() + s.slice(1)
}
