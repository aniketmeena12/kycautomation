/**
 * Page 8 -- Audit Trail.
 *
 * SCOPE NOTE, stated in the UI as well as here: the backend exposes audit
 * records PER CASE (/cases/{id}/audit). There is no global /audit endpoint, so
 * this page is case-scoped. Presenting it as a system-wide feed would imply a
 * completeness it cannot deliver.
 */

import { useMemo, useState } from "react"
import { useNavigate, useParams } from "react-router-dom"
import { Info, ScrollText, Search } from "lucide-react"
import {
  Badge,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  LoadingBlock,
  Select,
  Table,
  TableBody,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui"
import { AuditRow } from "@/components/domain"
import { useCaseAudit, useCases } from "@/hooks/queries"
import type { ActorType } from "@/api/types"
import { Page } from "./Dashboard"

export default function AuditPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const cases = useCases({ limit: 100 })
  const selected = caseId ? Number(caseId) : cases.data?.cases[0]?.id
  const query = useCaseAudit(selected)

  const [q, setQ] = useState("")
  const [actor, setActor] = useState<ActorType | "">("")
  const [action, setAction] = useState("")

  const actions = useMemo(
    () => Array.from(new Set((query.data?.entries ?? []).map((e) => e.action))).sort(),
    [query.data],
  )

  const rows = useMemo(() => {
    let list = query.data?.entries ?? []
    if (actor) list = list.filter((e) => e.actor_type === actor)
    if (action) list = list.filter((e) => e.action === action)
    if (q.trim()) {
      const s = q.trim().toLowerCase()
      list = list.filter((e) =>
        [e.action, e.actor_id, e.target_type, e.target_id, e.reason, e.correlation_id]
          .filter(Boolean)
          .some((v) => String(v).toLowerCase().includes(s)),
      )
    }
    return list
  }, [query.data, q, actor, action])

  if (cases.isLoading) return <LoadingBlock label="Loading cases" rows={4} />
  if (!cases.data?.cases.length)
    return (
      <Page title="Audit Trail">
        <EmptyState icon={ScrollText} title="No cases yet" description="The audit trail is exposed per case." />
      </Page>
    )

  return (
    <Page
      title="Audit Trail"
      subtitle="Immutable. Audit records are never updated or deleted."
      actions={
        <Select aria-label="Select case" value={selected ?? ""} onChange={(e) => navigate(`/audit/${e.target.value}`)}>
          {cases.data.cases.map((c) => (
            <option key={c.id} value={c.id}>
              {c.case_ref} - {c.client_name}
            </option>
          ))}
        </Select>
      }
    >
      <p className="flex items-start gap-1.5 rounded border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-800">
        <Info className="mt-px h-3.5 w-3.5 shrink-0" />
        <span>
          The backend exposes audit records per case; there is no global audit endpoint. This view is scoped to the
          selected case, its client&apos;s monitoring, and its investigations. Filters apply to the loaded rows.
        </span>
      </p>

      <Card>
        <CardContent className="flex flex-wrap items-end gap-2 p-3">
          <div className="min-w-[220px] flex-1">
            <label htmlFor="audit-q" className="mb-1 block text-xs font-medium">
              Search action, target, reason, correlation ID
            </label>
            <div className="relative">
              <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input id="audit-q" className="pl-8" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
          </div>
          <div>
            <label htmlFor="audit-actor" className="mb-1 block text-xs font-medium">
              Actor
            </label>
            <Select id="audit-actor" value={actor} onChange={(e) => setActor(e.target.value as ActorType | "")}>
              <option value="">All</option>
              <option value="SYSTEM">System</option>
              <option value="AGENT">Agent</option>
              <option value="HUMAN">Human</option>
            </Select>
          </div>
          <div>
            <label htmlFor="audit-action" className="mb-1 block text-xs font-medium">
              Action
            </label>
            <Select id="audit-action" value={action} onChange={(e) => setAction(e.target.value)}>
              <option value="">All</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </div>
          <Badge variant="muted">{rows.length} rows</Badge>
        </CardContent>
      </Card>

      {query.isLoading ? (
        <LoadingBlock label="Loading audit trail" rows={8} />
      ) : query.error ? (
        <ErrorState title="Could not load audit trail" detail={(query.error as Error).message} />
      ) : rows.length === 0 ? (
        <EmptyState icon={ScrollText} title="No audit entries match" />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Timestamp</TableHead>
                <TableHead>Actor</TableHead>
                <TableHead>Action</TableHead>
                <TableHead>Target</TableHead>
                <TableHead>Reason / change</TableHead>
                <TableHead>Correlation ID</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((e) => (
                <AuditRow key={e.id} entry={e} />
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </Page>
  )
}
