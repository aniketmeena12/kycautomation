/**
 * Page 5 -- Timeline.
 *
 * The backend GENERATES the chronology (its builder has no add_entry); this
 * page only filters and renders it. Filtering is client-side, which is
 * legitimate here because the endpoint returns the whole case at once and
 * exposes no filter parameter -- unlike the Customers page, nothing is hidden
 * behind pagination, so a client-side filter is complete rather than partial.
 */

import { useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { History } from "lucide-react"
import { Badge, Button, Card, CardContent, EmptyState, ErrorState, LoadingBlock, Select } from "@/components/ui"
import { TimelineItem } from "@/components/domain"
import { useCaseTimeline, useCases } from "@/hooks/queries"
import type { TimelineEntryType } from "@/api/types"
import { fmtDateTime, humanize } from "@/lib/utils"
import { Page } from "./Dashboard"

const TYPES: TimelineEntryType[] = [
  "MONITORING",
  "PROVIDER_RESULT",
  "ENTITY_RESOLUTION",
  "EVIDENCE",
  "RISK_EVENT",
  "RISK_SCORE_CHANGE",
  "ALERT",
  "INVESTIGATION",
  "HUMAN_REVIEW",
  "SAR",
]

export default function TimelinePage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const cases = useCases({ limit: 100 })
  const selected = caseId ? Number(caseId) : cases.data?.cases[0]?.id
  const query = useCaseTimeline(selected)
  const [active, setActive] = useState<Set<TimelineEntryType>>(new Set())

  const entries = useMemo(() => {
    const all = query.data?.timeline.entries ?? []
    return active.size === 0 ? all : all.filter((e) => active.has(e.entry_type))
  }, [query.data, active])

  function toggle(t: TimelineEntryType) {
    setActive((prev) => {
      const next = new Set(prev)
      if (next.has(t)) next.delete(t)
      else next.add(t)
      return next
    })
  }

  if (cases.isLoading) return <LoadingBlock label="Loading cases" rows={4} />
  if (!cases.data?.cases.length)
    return (
      <Page title="Timeline">
        <EmptyState icon={History} title="No cases yet" description="A timeline is generated per case." />
      </Page>
    )

  const counts = query.data?.timeline.counts_by_type ?? {}

  return (
    <Page
      title="Timeline"
      subtitle="Generated from stored records. Nothing here is hand-written."
      actions={
        <Select
          aria-label="Select case"
          value={selected ?? ""}
          onChange={(e) => navigate(`/timeline/${e.target.value}`)}
        >
          {cases.data.cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.case_ref} - {c.client_name}
            </option>
          ))}
        </Select>
      }
    >
      <Card>
        <CardContent className="flex flex-wrap gap-1.5 p-3">
          <Button size="sm" variant={active.size === 0 ? "default" : "outline"} onClick={() => setActive(new Set())}>
            All
          </Button>
          {TYPES.map((t) => (
            <Button
              key={t}
              size="sm"
              variant={active.has(t) ? "default" : "outline"}
              aria-pressed={active.has(t)}
              onClick={() => toggle(t)}
              disabled={!counts[t]}
            >
              {humanize(t)}
              {counts[t] ? <span className="ml-1 opacity-70">{counts[t]}</span> : null}
            </Button>
          ))}
        </CardContent>
      </Card>

      {query.isLoading ? (
        <LoadingBlock label="Loading timeline" rows={8} />
      ) : query.error ? (
        <ErrorState title="Could not load timeline" detail={(query.error as Error).message} />
      ) : entries.length === 0 ? (
        <EmptyState
          icon={History}
          title="No entries"
          description={
            active.size
              ? "No entries match the selected filters."
              : // The timeline is GENERATED from stored domain rows -- monitoring runs,
                // risk events, evidence, investigations, reviews. It is not an activity
                // log that the case writes to when it is opened, which is why a brand
                // new case on a never-monitored client is empty rather than showing
                // "case opened". That event lives in the audit trail, which is a
                // different record with a different purpose.
                "This case has no recorded activity yet. The timeline is built from stored monitoring, risk, evidence and review records -- opening a case does not itself create one. Run a monitoring cycle from the case workspace to generate the first entries."
          }
          action={
            active.size ? null : (
              <Button variant="outline" size="sm" asChild>
                <Link to={`/cases/${selected}`}>Back to case workspace</Link>
              </Button>
            )
          }
        />
      ) : (
        <Card>
          <CardContent className="p-4">
            <div className="mb-3 flex items-center gap-2">
              <Badge variant="muted">{entries.length} entries</Badge>
              <span className="text-[10px] text-muted-foreground">
                Generated {fmtDateTime(query.data?.timeline.generated_at)}
              </span>
            </div>
            <ol className="list-none">
              {entries.map((e, i) => (
                <TimelineItem key={e.entry_key} entry={e} last={i === entries.length - 1} />
              ))}
            </ol>
          </CardContent>
        </Card>
      )}
    </Page>
  )
}
