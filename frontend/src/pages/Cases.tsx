/** Page 6 -- Case Management queue. The status filter is server-side (real). */

import { useSearchParams } from "react-router-dom"
import { ClipboardList } from "lucide-react"
import { Button, Card, CardContent, EmptyState, ErrorState, LoadingBlock } from "@/components/ui"
import { CaseCard } from "@/components/domain"
import { useCaseMetrics, useCases } from "@/hooks/queries"
import type { CaseStatus } from "@/api/types"
import { Page } from "./Dashboard"

const STATUSES: (CaseStatus | "")[] = ["", "OPEN", "UNDER_REVIEW", "ESCALATED", "SAR_REVIEW", "CLOSED"]

export default function CasesPage() {
  const [params, setParams] = useSearchParams()
  const status = (params.get("status") ?? "") as CaseStatus | ""
  const query = useCases({ status: status || undefined, limit: 100 })
  const metrics = useCaseMetrics()

  const counts: Record<string, number | undefined> = {
    "": metrics.data?.total_cases,
    OPEN: metrics.data?.open_cases,
    UNDER_REVIEW: metrics.data?.under_review_cases,
    ESCALATED: metrics.data?.escalated_cases,
    SAR_REVIEW: metrics.data?.sar_review_cases,
    CLOSED: metrics.data?.closed_cases,
  }

  return (
    <Page title="Case Management" subtitle="The compliance workspace queue. Status filtering is server-side.">
      <Card>
        <CardContent className="flex flex-wrap gap-2 p-3">
          {STATUSES.map((s) => (
            <Button
              key={s || "all"}
              size="sm"
              variant={status === s ? "default" : "outline"}
              aria-pressed={status === s}
              onClick={() => {
                const next = new URLSearchParams(params)
                if (s) next.set("status", s)
                else next.delete("status")
                setParams(next)
              }}
            >
              {s ? s.replace(/_/g, " ") : "All"}
              {counts[s] !== undefined ? <span className="ml-1 opacity-70">{counts[s]}</span> : null}
            </Button>
          ))}
        </CardContent>
      </Card>

      {query.isLoading ? (
        <LoadingBlock label="Loading cases" rows={6} />
      ) : query.error ? (
        <ErrorState title="Could not load cases" detail={(query.error as Error).message} />
      ) : query.data?.cases.length ? (
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {query.data.cases.map((c) => (
            <CaseCard key={c.id} item={c} />
          ))}
        </div>
      ) : (
        <EmptyState
          icon={ClipboardList}
          title={status ? "No cases in this state" : "No cases yet"}
          description="Open a case from a customer profile to start the compliance workflow."
        />
      )}
    </Page>
  )
}
