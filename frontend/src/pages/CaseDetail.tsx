/**
 * Page 6b -- Case detail + the human review workspace.
 *
 * The review form is driven ENTIRELY by `available_actions` from the backend's
 * state machine. The UI never hardcodes which actions are legal: if it did, a
 * button would eventually appear that the server rejects with 409, and the
 * reviewer would learn the workflow by hitting errors.
 *
 * `reviewer` is required with no default, mirroring the backend. An
 * unattributed compliance decision is not a compliance decision, so the submit
 * button stays disabled until a name is entered.
 */

import { useState } from "react"
import { Link, useParams } from "react-router-dom"
import { Activity, ArrowLeft, FileText, Gavel, History, ScrollText } from "lucide-react"
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  Input,
  LoadingBlock,
  Select,
  Textarea,
} from "@/components/ui"
import { EvidenceCard, RiskBadge, StatusChip } from "@/components/domain"
import { useCase, useGenerateSar, useMonitorClient, useSubmitReview } from "@/hooks/queries"
import { fmtDateTime, humanize } from "@/lib/utils"
import type { ActionRequirement, CaseDetail, ReviewAction } from "@/api/types"
import { Page } from "./Dashboard"

/**
 * Which actions need a target_id is a fact the BACKEND owns, and it now says so
 * in `action_requirements`. This page used to keep its own copy of that table --
 * it listed four actions and the state machine had six, so choosing APPROVE
 * rendered a form with no target field and the server rejected the submission
 * with "Action APPROVE requires a target_id".
 *
 * The lesson is not "the list was wrong", it is "there was a list". Anything
 * derived from the state machine gets read from the response, never restated
 * here -- the same rule that already governs `available_actions`.
 *
 * The fallback exists only for a backend predating the field: it asks for a
 * target rather than assuming none is needed, because a spurious question is
 * recoverable and a rejected submission is not.
 */
function requirementFor(detail: CaseDetail, action: ReviewAction | ""): ActionRequirement | null {
  if (!action) return null
  const rules = detail.action_requirements
  if (!rules) return null
  return rules.find((r) => r.action === action) ?? null
}

export default function CaseDetailPage() {
  const { caseId } = useParams()
  const id = Number(caseId)
  const query = useCase(id)
  const review = useSubmitReview(id)
  const sar = useGenerateSar(id)
  const monitor = useMonitorClient(id)

  const [reviewer, setReviewer] = useState("")
  const [action, setAction] = useState<ReviewAction | "">("")
  const [comment, setComment] = useState("")
  const [targetId, setTargetId] = useState("")

  if (query.isLoading) return <LoadingBlock label="Loading case" rows={8} />
  if (query.error)
    return (
      <Page title="Case">
        <ErrorState title="Could not load case" detail={(query.error as Error).message} />
      </Page>
    )

  const detail = query.data!
  const c = detail.case
  const rule = requirementFor(detail, action)
  // No rule found for a chosen action means the backend didn't declare one
  // (older build). Ask for the target rather than silently omitting it.
  const needsTarget = action ? (rule ? rule.requires_target : true) : false
  const canSubmit = reviewer.trim().length > 0 && action !== "" && (!needsTarget || targetId.trim() !== "")

  return (
    <Page
      title={`${c.case_ref} - ${c.client_name}`}
      subtitle={`Opened ${fmtDateTime(c.opened_at)}${c.assigned_to ? ` - ${c.assigned_to}` : ""}`}
      actions={
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" asChild>
            <Link to="/cases">
              <ArrowLeft className="h-3.5 w-3.5" /> Cases
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to={`/timeline/${c.id}`}>
              <History className="h-3.5 w-3.5" /> Timeline
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to={`/audit/${c.id}`}>
              <ScrollText className="h-3.5 w-3.5" /> Audit
            </Link>
          </Button>
          <Button variant="outline" size="sm" asChild>
            <Link to={`/sar/${c.id}`}>
              <FileText className="h-3.5 w-3.5" /> Draft SAR
            </Link>
          </Button>
        </div>
      }
    >
      <div className="flex flex-wrap items-center gap-2">
        <StatusChip status={c.status} />
        <RiskBadge band={c.current_risk_band} score={c.current_risk_score} neverMonitored={c.current_risk_score === null} />
        {detail.human_decision_required ? <Badge variant="warning">Human decision required</Badge> : null}
        {c.closed_at ? <Badge variant="muted">Closed {fmtDateTime(c.closed_at)}</Badge> : null}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          {/* --------------------------------------- risk + events */}
          <Card>
            <CardHeader>
              <CardTitle>Risk</CardTitle>
            </CardHeader>
            <CardContent>
              {detail.risk_current ? (
                <>
                  <div className="flex items-baseline gap-2">
                    <span className="text-2xl font-semibold tabular-nums">{detail.risk_current.score}</span>
                    <span className="text-xs text-muted-foreground">/100</span>
                    <RiskBadge band={detail.risk_current.band} />
                  </div>
                  <p className="mt-1 text-xs text-muted-foreground">{detail.risk_current.explanation}</p>
                </>
              ) : (
                <EmptyState
                  title="Not assessed"
                  description="This client has never been scored, so there is nothing to show yet. Running a monitoring cycle scores it deterministically from the config-driven risk engine -- no model is involved."
                  action={
                    <div className="space-y-2">
                      <Button size="sm" onClick={() => monitor.mutate(c.external_client_id)} disabled={monitor.isPending}>
                        <Activity className="h-3.5 w-3.5" />
                        {monitor.isPending ? "Running monitoring cycle..." : "Run monitoring cycle"}
                      </Button>
                      {monitor.isError ? (
                        <p className="text-xs text-destructive">{(monitor.error as Error).message}</p>
                      ) : null}
                    </div>
                  }
                />
              )}
              {detail.risk_events.length > 0 ? (
                <ul className="mt-3 space-y-1 border-t pt-2">
                  {detail.risk_events.slice(0, 6).map((e) => (
                    <li key={e.id} className="flex items-center gap-2 text-xs">
                      <RiskBadge band={e.severity} />
                      <span className="truncate">{e.summary ?? humanize(e.type)}</span>
                    </li>
                  ))}
                </ul>
              ) : null}
            </CardContent>
          </Card>

          {/* --------------------------------------- investigations */}
          <Card>
            <CardHeader>
              <CardTitle>Investigations</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {detail.investigations.length === 0 ? (
                <EmptyState title="No investigations" />
              ) : (
                detail.investigations.map((i) => (
                  <Link
                    key={i.id}
                    to={`/investigations/${i.id}`}
                    className="block rounded border p-2 text-xs hover:bg-accent"
                  >
                    <div className="flex items-center gap-2">
                      <Badge variant={i.status === "FAILED" ? "destructive" : "muted"}>{humanize(i.status)}</Badge>
                      {i.grounding_passed === false ? <Badge variant="destructive">Fabricated citations</Badge> : null}
                      <span className="ml-auto text-[10px] text-muted-foreground">{fmtDateTime(i.opened_at)}</span>
                    </div>
                    <p className="mt-1 line-clamp-2 text-muted-foreground">{i.summary ?? i.error_message ?? "--"}</p>
                  </Link>
                ))
              )}
            </CardContent>
          </Card>

          {/* --------------------------------------- evidence */}
          <Card>
            <CardHeader>
              <CardTitle>Evidence ({detail.evidence.length})</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {detail.evidence.length === 0 ? (
                <EmptyState title="No evidence on file" />
              ) : (
                detail.evidence.slice(0, 6).map((e) => <EvidenceCard key={e.id} evidence={e} />)
              )}
            </CardContent>
          </Card>

          {/* --------------------------------------- entity matches */}
          <Card>
            <CardHeader>
              <CardTitle>Entity matches</CardTitle>
            </CardHeader>
            <CardContent>
              {detail.entity_matches.length === 0 ? (
                <EmptyState
                  title="No entity matches"
                  description="Confirming or rejecting a match is a human action and needs the match id."
                />
              ) : (
                <ul className="space-y-1.5">
                  {detail.entity_matches.map((m) => (
                    <li key={m.id} className="flex items-center gap-2 text-xs">
                      <span className="font-mono text-[10px] text-muted-foreground">#{m.id}</span>
                      <span className="truncate font-medium">{m.candidate_name}</span>
                      <Badge variant="outline">{humanize(m.status)}</Badge>
                      <span className="ml-auto font-mono tabular-nums">{m.confidence.toFixed(0)}</span>
                    </li>
                  ))}
                </ul>
              )}
            </CardContent>
          </Card>
        </div>

        {/* ------------------------------------------------ review panel */}
        <div className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle>
                <Gavel className="mr-1 inline h-3.5 w-3.5" />
                Human review
              </CardTitle>
              <p className="text-[10px] text-muted-foreground">
                Only actions the backend permits from {c.status} are offered.
              </p>
            </CardHeader>
            <CardContent className="space-y-2">
              {detail.available_actions.length === 0 ? (
                <EmptyState
                  title="No actions available"
                  description="This case is closed. A closed compliance case is never reopened -- open a new one instead."
                />
              ) : (
                <>
                  <div>
                    <label htmlFor="reviewer" className="mb-1 block text-xs font-medium">
                      Reviewer <span className="text-destructive">*</span>
                    </label>
                    <Input
                      id="reviewer"
                      required
                      value={reviewer}
                      placeholder="your.name"
                      onChange={(e) => setReviewer(e.target.value)}
                    />
                  </div>
                  <div>
                    <label htmlFor="action" className="mb-1 block text-xs font-medium">
                      Action <span className="text-destructive">*</span>
                    </label>
                    <Select
                      id="action"
                      className="w-full"
                      value={action}
                      onChange={(e) => setAction(e.target.value as ReviewAction)}
                    >
                      <option value="">Select an action</option>
                      {detail.available_actions.map((a) => (
                        <option key={a} value={a}>
                          {humanize(a)}
                        </option>
                      ))}
                    </Select>
                  </div>
                  {needsTarget ? (
                    <div>
                      <label htmlFor="target" className="mb-1 block text-xs font-medium">
                        {rule?.target_type ? `${humanize(rule.target_type)} ID` : "Target ID"}{" "}
                        <span className="text-destructive">*</span>
                      </label>
                      <Input
                        id="target"
                        inputMode="numeric"
                        value={targetId}
                        placeholder={rule?.target_type === "SARDraft" ? "Draft SAR id" : rule?.target_type === "EntityMatch" ? "Entity match id" : "Record id"}
                        onChange={(e) => setTargetId(e.target.value)}
                      />
                      {/* The candidate ids are on this very page, so offer them
                          rather than making the reviewer hunt for a number. */}
                      {rule?.target_type === "SARDraft" && detail.sar_drafts.length > 0 ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {detail.sar_drafts.map((d) => (
                            <button
                              key={d.id}
                              type="button"
                              onClick={() => setTargetId(String(d.id))}
                              className="rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              Use draft #{d.id}
                            </button>
                          ))}
                        </div>
                      ) : null}
                      {rule?.target_type === "EntityMatch" && detail.entity_matches.length > 0 ? (
                        <div className="mt-1 flex flex-wrap gap-1">
                          {detail.entity_matches.slice(0, 8).map((m) => (
                            <button
                              key={m.id}
                              type="button"
                              onClick={() => setTargetId(String(m.id))}
                              className="rounded border px-1.5 py-0.5 text-[10px] hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                            >
                              Use match #{m.id}
                            </button>
                          ))}
                        </div>
                      ) : null}
                      <p className="mt-1 text-[10px] text-muted-foreground">
                        {rule?.description ??
                          "This action decides on a specific record, so the backend requires its id."}
                      </p>
                    </div>
                  ) : null}
                  <div>
                    <label htmlFor="comment" className="mb-1 block text-xs font-medium">
                      Comment
                    </label>
                    <Textarea id="comment" value={comment} onChange={(e) => setComment(e.target.value)} />
                  </div>
                  <Button
                    className="w-full"
                    disabled={!canSubmit || review.isPending}
                    onClick={() =>
                      review.mutate(
                        {
                          reviewer: reviewer.trim(),
                          action: action as ReviewAction,
                          comment: comment || undefined,
                          target_id: targetId ? Number(targetId) : undefined,
                        },
                        { onSuccess: () => { setComment(""); setTargetId(""); setAction("") } },
                      )
                    }
                  >
                    {review.isPending ? "Recording..." : "Record decision"}
                  </Button>
                  {review.isError ? (
                    <ErrorState title="Review rejected" detail={(review.error as Error).message} />
                  ) : null}
                  {review.isSuccess ? (
                    <p className="rounded border border-emerald-200 bg-emerald-50 p-2 text-xs text-emerald-800">
                      Decision recorded and audited.
                    </p>
                  ) : null}
                </>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Draft SAR</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {detail.sar_drafts.length ? (
                detail.sar_drafts.map((s) => (
                  <Link key={s.id} to={`/sar/${c.id}`} className="block rounded border p-2 text-xs hover:bg-accent">
                    <span className="font-mono">{s.sar_ref}</span>
                    <Badge className="ml-2" variant={s.status === "APPROVED" ? "success" : "muted"}>
                      {humanize(s.status)}
                    </Badge>
                  </Link>
                ))
              ) : (
                <p className="text-xs text-muted-foreground">No draft yet.</p>
              )}
              <Button
                variant="outline"
                className="w-full"
                size="sm"
                disabled={sar.isPending || !reviewer.trim() || c.status === "CLOSED"}
                onClick={() => sar.mutate({ requested_by: reviewer.trim() })}
              >
                {sar.isPending ? "Generating..." : "Generate draft SAR"}
              </Button>
              <p className="text-[10px] text-muted-foreground">
                Requires your name above (recorded in the audit trail). Always a DRAFT -- this system never files.
              </p>
              {sar.isError ? <ErrorState title="Could not generate" detail={(sar.error as Error).message} /> : null}
            </CardContent>
          </Card>

          {/* --------------------------------------- reviews */}
          <Card>
            <CardHeader>
              <CardTitle>Reviewer history ({detail.reviews.length})</CardTitle>
              <p className="text-[10px] text-muted-foreground">Append-only. Reviews are never overwritten.</p>
            </CardHeader>
            <CardContent className="space-y-2">
              {detail.reviews.length === 0 ? (
                <p className="text-xs text-muted-foreground">No decisions recorded yet.</p>
              ) : (
                detail.reviews.map((r) => (
                  <div key={r.id} className="rounded border p-2 text-xs">
                    <div className="flex items-center gap-2">
                      <Badge variant="default">{humanize(r.action)}</Badge>
                      <span className="ml-auto text-[10px] text-muted-foreground">{fmtDateTime(r.decided_at)}</span>
                    </div>
                    <p className="mt-1 font-medium">{r.reviewer_name}</p>
                    {r.comment ? <p className="text-muted-foreground">{r.comment}</p> : null}
                    {r.previous_state && r.new_state ? (
                      <p className="mt-1 font-mono text-[10px] text-muted-foreground">
                        {r.previous_state} -&gt; {r.new_state}
                      </p>
                    ) : null}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>
    </Page>
  )
}
