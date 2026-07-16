/**
 * Page 4 -- Investigation Workspace.
 *
 * The page where the LLM's output is shown, so it is also the page where the
 * boundary must be most visible. Three things are deliberate:
 *
 *  1. GROUNDING IS SHOWN BEFORE THE PROSE. If the model cited evidence that
 *     does not exist, the reader learns that before reading a word of the
 *     summary -- not in a footnote afterwards.
 *  2. FINDINGS CARRY THEIR CITATIONS INLINE. An uncited claim looks uncited.
 *  3. CONFLICTING EVIDENCE GETS EQUAL BILLING with supporting evidence.
 *     Burying exculpatory findings below the fold would quietly bias every
 *     review that happens here.
 */

import { Link, useParams } from "react-router-dom"
import { AlertTriangle, ArrowLeft, Bot, FileText, Scale } from "lucide-react"
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  EmptyState,
  ErrorState,
  LoadingBlock,
} from "@/components/ui"
import { GroundingIndicator } from "@/components/domain"
import { useInvestigation } from "@/hooks/queries"
import { fmtDateTime, fmtOrDash, humanize } from "@/lib/utils"
import { Page } from "./Dashboard"

export default function InvestigationPage() {
  const { investigationId } = useParams()
  const query = useInvestigation(Number(investigationId))

  if (query.isLoading) return <LoadingBlock label="Loading investigation" rows={8} />
  if (query.error)
    return (
      <Page title="Investigation">
        <ErrorState title="Could not load investigation" detail={(query.error as Error).message} />
      </Page>
    )

  const { investigation, report, recommendations, evaluation } = query.data!
  const failed = investigation.status === "FAILED"

  return (
    <Page
      title={`Investigation #${investigation.id}`}
      subtitle={investigation.trigger_reason ?? undefined}
      actions={
        <Button variant="outline" size="sm" asChild>
          <Link to={`/customers/${investigation.client_id}`}>
            <ArrowLeft className="h-3.5 w-3.5" /> Customer
          </Link>
        </Button>
      }
    >
      {/* The boundary, stated at the top of the page. */}
      <div className="flex flex-wrap items-center gap-2 rounded border border-violet-200 bg-violet-50 p-2 text-xs text-violet-900">
        <Bot className="h-4 w-4 shrink-0" />
        <span>
          Written by a language model. It explains evidence and recommends next steps; it never computes a risk
          score, resolves an entity, or decides an outcome.
        </span>
        <span className="ml-auto flex items-center gap-2">
          <GroundingIndicator passed={evaluation.grounding_passed} hallucinated={evaluation.hallucinated_citation_count} />
          <Badge variant="muted">Human review required</Badge>
        </span>
      </div>

      {failed ? (
        <ErrorState
          title="This investigation could not run"
          detail={investigation.error_message ?? "No reason recorded."}
        />
      ) : null}

      {evaluation.grounding_passed === false ? (
        <div role="alert" className="flex items-start gap-2 rounded border border-destructive/30 bg-destructive/5 p-3 text-xs">
          <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-destructive" />
          <p className="text-destructive">
            <strong>This report cites evidence that does not exist.</strong> {evaluation.hallucinated_citation_count}{" "}
            fabricated citation(s) were detected by deterministic validation. Treat every claim below as unverified.
          </p>
        </div>
      ) : null}

      <div className="grid gap-3 lg:grid-cols-3">
        <div className="space-y-3 lg:col-span-2">
          <Card>
            <CardHeader>
              <CardTitle>Summary</CardTitle>
            </CardHeader>
            <CardContent>
              {report?.summary ? (
                <p className="text-sm leading-relaxed">{report.summary}</p>
              ) : (
                <EmptyState icon={Bot} title="No report" description={investigation.error_message ?? undefined} />
              )}
            </CardContent>
          </Card>

          {report ? (
            <>
              <FindingList title="Key findings" items={report.key_findings} />
              <FindingList title="Supporting evidence" items={report.supporting_evidence} />
              <FindingList
                title="Conflicting / exculpatory evidence"
                items={report.conflicting_evidence}
                emptyHint="The agent reported no evidence weakening the assessed risk."
              />

              <Card>
                <CardHeader>
                  <CardTitle>Reasoning</CardTitle>
                  <p className="text-[10px] text-muted-foreground">
                    The report&apos;s analytical rationale -- an authored section, not the model&apos;s
                    chain-of-thought, which is never requested or stored.
                  </p>
                </CardHeader>
                <CardContent>
                  <p className="whitespace-pre-wrap text-sm leading-relaxed">{report.reasoning}</p>
                </CardContent>
              </Card>

              <div className="grid gap-3 md:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle>Missing information</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <BulletList items={report.missing_information} empty="None recorded." />
                  </CardContent>
                </Card>
                <Card>
                  <CardHeader>
                    <CardTitle>Stated limitations</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <BulletList items={report.limitations} empty="None recorded." />
                  </CardContent>
                </Card>
              </div>
            </>
          ) : null}
        </div>

        {/* ------------------------------------------------ sidebar */}
        <div className="space-y-3">
          <Card>
            <CardHeader>
              <CardTitle>Recommendations</CardTitle>
              <p className="text-[10px] text-muted-foreground">
                Investigative next steps only. The agent cannot recommend approving or rejecting a client.
              </p>
            </CardHeader>
            <CardContent className="space-y-2">
              {recommendations.length === 0 ? (
                <EmptyState title="No recommendations" />
              ) : (
                recommendations.map((r) => (
                  <div key={r.id} className="rounded border p-2">
                    <Badge variant="default">{humanize(r.action)}</Badge>
                    <p className="mt-1 text-xs text-muted-foreground">{r.rationale}</p>
                  </div>
                ))
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Model metadata</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-xs">
              <Row label="Provider" value={<span className="font-mono">{evaluation.llm_provider ?? "--"}</span>} />
              <Row label="Model" value={<span className="font-mono text-[10px]">{evaluation.llm_model ?? "--"}</span>} />
              <Row label="Prompt version" value={<span className="font-mono">{evaluation.prompt_version ?? "--"}</span>} />
              <Row label="Latency" value={fmtOrDash(evaluation.latency_ms, " ms")} />
              <Row label="Input tokens" value={fmtOrDash(evaluation.input_tokens)} />
              <Row label="Output tokens" value={fmtOrDash(evaluation.output_tokens)} />
              <Row
                label="Temperature"
                value={
                  evaluation.temperature === null ? (
                    <span
                      className="text-muted-foreground"
                      title="Null because no sampling parameter was sent. Some models reject them outright; recording 0.0 would fabricate a request field."
                    >
                      Not sent
                    </span>
                  ) : (
                    evaluation.temperature
                  )
                }
              />
              <Row
                label="Context hash"
                value={<span className="font-mono text-[10px]">{evaluation.context_hash?.slice(0, 12) ?? "--"}</span>}
              />
              <Row label="Generated" value={fmtDateTime(evaluation.generated_at)} />
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Grounding references</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1.5 text-xs">
              <Row label="Evidence available" value={fmtOrDash(evaluation.evidence_available_count)} />
              <Row label="Evidence cited" value={fmtOrDash(evaluation.evidence_used_count)} />
              <Row label="Evidence ignored" value={fmtOrDash(evaluation.evidence_ignored_count)} />
              <Row label="Ungrounded findings" value={fmtOrDash(evaluation.ungrounded_finding_count)} />
              <Row label="Fabricated citations" value={fmtOrDash(evaluation.hallucinated_citation_count)} />
              {report?.citations.length ? (
                <p className="pt-1 font-mono text-[10px] text-muted-foreground">
                  cited: {report.citations.join(", ")}
                </p>
              ) : null}
              {evaluation.injection_flags.length > 0 ? (
                <div className="mt-2 rounded border border-amber-200 bg-amber-50 p-1.5">
                  <p className="text-[10px] font-medium text-amber-800">
                    {evaluation.injection_flags.length} prompt-injection pattern(s) detected in this client&apos;s
                    evidence text. Content was quarantined and treated as data.
                  </p>
                </div>
              ) : null}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {/* These live on the CASE, not here: a review and a SAR are case
                  actions requiring a named reviewer, and the backend exposes no
                  endpoint that acts on an investigation directly. */}
              <Button variant="outline" size="sm" className="w-full" asChild>
                <Link to="/cases">
                  <Scale className="h-3.5 w-3.5" /> Request review (via case)
                </Link>
              </Button>
              <Button variant="outline" size="sm" className="w-full" asChild>
                <Link to="/sar">
                  <FileText className="h-3.5 w-3.5" /> Generate draft SAR (via case)
                </Link>
              </Button>
              <p className="text-[10px] text-muted-foreground">
                Reviews and SAR drafting are case actions requiring a named reviewer -- they are not performed
                against an investigation directly.
              </p>
            </CardContent>
          </Card>
        </div>
      </div>
    </Page>
  )
}

function FindingList({
  title,
  items,
  emptyHint,
}: {
  title: string
  items: { finding: string; evidence_ids: number[]; confidence_statement: string }[]
  emptyHint?: string
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>
          {title} ({items.length})
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        {items.length === 0 ? (
          <p className="text-xs text-muted-foreground">{emptyHint ?? "None."}</p>
        ) : (
          items.map((f, i) => (
            <div key={i} className="rounded border p-2">
              <p className="text-sm">{f.finding}</p>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                {f.evidence_ids.length ? (
                  <span className="font-mono text-[10px] text-primary">evidence {f.evidence_ids.join(", ")}</span>
                ) : (
                  <Badge variant="muted" title="No evidence id cited for this statement.">
                    Uncited
                  </Badge>
                )}
                {f.confidence_statement ? (
                  <span className="text-[10px] text-muted-foreground">{f.confidence_statement}</span>
                ) : null}
              </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  )
}

function BulletList({ items, empty }: { items: string[]; empty: string }) {
  if (!items.length) return <p className="text-xs text-muted-foreground">{empty}</p>
  return (
    <ul className="list-inside list-disc space-y-1 text-xs text-muted-foreground">
      {items.map((s, i) => (
        <li key={i}>{s}</li>
      ))}
    </ul>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-2">
      <span className="text-muted-foreground">{label}</span>
      <span className="font-medium">{value}</span>
    </div>
  )
}
