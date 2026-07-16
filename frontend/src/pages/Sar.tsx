/**
 * Page 7 -- Draft SAR viewer.
 *
 * A document layout, not a dashboard. Three deliberate choices:
 *
 *  1. THE DRAFT WATERMARK IS NOT DECORATION. It is fixed behind the page and
 *     repeats in print, because the single worst outcome this product could
 *     produce is an unapproved draft being mistaken for a filed report.
 *  2. EVERY SECTION SHOWS WHO WROTE IT. Eight of nine are deterministic; one is
 *     model narrative. A reviewer signing this must be able to see, per
 *     section, how much of it a machine wrote.
 *  3. "EDIT" IS HONEST ABOUT ITS SCOPE. The backend has no SAR-update endpoint
 *     (drafts are frozen at generation, by design), so edits here are LOCAL
 *     working notes for the reviewer -- clearly labelled, and never presented as
 *     if they were persisted.
 *
 * Export uses window.print() against a print stylesheet rather than pulling in
 * a PDF library: the browser's own engine produces a correct, selectable,
 * paginated PDF, and it is one fewer dependency for the same result.
 */

import { useMemo, useState } from "react"
import { Link, useNavigate, useParams } from "react-router-dom"
import { AlertTriangle, ArrowLeft, Check, FileText, Printer, X } from "lucide-react"
import {
  Badge,
  Button,
  Card,
  CardContent,
  EmptyState,
  ErrorState,
  Input,
  LoadingBlock,
  Select,
  Textarea,
} from "@/components/ui"
import { GroundingIndicator } from "@/components/domain"
import { ApiError } from "@/api/client"
import { useCase, useCases, useSar, useSubmitReview } from "@/hooks/queries"
import { fmtDateTime, humanize } from "@/lib/utils"
import { useSession } from "@/lib/session"
import { Page } from "./Dashboard"

export default function SarPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const cases = useCases({ limit: 100 })
  const selected = caseId ? Number(caseId) : cases.data?.cases.find((c) => c.has_sar_draft)?.id ?? cases.data?.cases[0]?.id
  const sar = useSar(selected)
  const detail = useCase(selected)
  const review = useSubmitReview(selected ?? 0)
  const { session } = useSession()

  // Prefilled from the signed-in reviewer; still editable, because the name on
  // a SAR approval is the accountable human's, and the backend is the authority
  // that rejects an empty one.
  const [reviewer, setReviewer] = useState(session?.name ?? "")
  const [notes, setNotes] = useState("")
  const [editing, setEditing] = useState(false)

  const canDecide = useMemo(
    () => (detail.data?.available_actions ?? []).includes("APPROVE_DRAFT_SAR"),
    [detail.data],
  )

  if (cases.isLoading) return <LoadingBlock label="Loading cases" rows={4} />
  if (!cases.data?.cases.length)
    return (
      <Page title="Draft SAR">
        <EmptyState icon={FileText} title="No cases yet" description="A SAR is drafted from a case." />
      </Page>
    )

  const notFound = sar.error instanceof ApiError && sar.error.isNotFound

  return (
    <Page
      title="Draft SAR"
      subtitle="Never filed. Requires human approval."
      actions={
        <div className="flex flex-wrap gap-2 print:hidden">
          <Select aria-label="Select case" value={selected ?? ""} onChange={(e) => navigate(`/sar/${e.target.value}`)}>
            {cases.data.cases.map((c) => (
              <option key={c.id} value={c.id}>
                {c.case_ref} - {c.client_name}
              </option>
            ))}
          </Select>
          <Button variant="outline" size="sm" asChild>
            <Link to={`/cases/${selected}`}>
              <ArrowLeft className="h-3.5 w-3.5" /> Case
            </Link>
          </Button>
          {sar.data ? (
            <Button variant="outline" size="sm" onClick={() => window.print()}>
              <Printer className="h-3.5 w-3.5" /> Export PDF
            </Button>
          ) : null}
        </div>
      }
    >
      {sar.isLoading ? (
        <LoadingBlock label="Loading SAR draft" rows={8} />
      ) : notFound ? (
        <EmptyState
          icon={FileText}
          title="No SAR draft for this case"
          description="Generate one from the case workspace. It requires a named requester and is always a draft."
          action={
            <Button size="sm" className="mt-2" asChild>
              <Link to={`/cases/${selected}`}>Go to case</Link>
            </Button>
          }
        />
      ) : sar.error ? (
        <ErrorState title="Could not load SAR" detail={(sar.error as Error).message} />
      ) : sar.data ? (
        <>
          {/* --------------------------------------- status bar */}
          <div className="flex flex-wrap items-center gap-2 print:hidden">
            <Badge variant={sar.data.status === "APPROVED" ? "success" : sar.data.status === "REJECTED" ? "destructive" : "warning"}>
              {humanize(sar.data.status)}
            </Badge>
            <GroundingIndicator passed={sar.data.grounding_passed} hallucinated={sar.data.hallucinated_citation_count} />
            <Badge variant="muted">{sar.data.cited_evidence_ids.length} evidence citation(s)</Badge>
            {sar.data.narrative_error ? (
              <Badge variant="muted" title={sar.data.narrative_error}>
                Narrative unavailable
              </Badge>
            ) : (
              <Badge variant="muted" title={`Narrative written by ${sar.data.narrative_generated_by}`}>
                Narrative: {sar.data.narrative_model ?? "n/a"}
              </Badge>
            )}
            {sar.data.reviewed_by ? (
              <Badge variant="secondary">
                {humanize(sar.data.status)} by {sar.data.reviewed_by} {fmtDateTime(sar.data.reviewed_at)}
              </Badge>
            ) : null}
          </div>

          {sar.data.grounding_passed === false ? (
            <div role="alert" className="flex items-start gap-2 rounded border border-destructive/30 bg-destructive/5 p-3">
              <AlertTriangle className="mt-px h-4 w-4 shrink-0 text-destructive" />
              <p className="text-xs text-destructive">
                <strong>The narrative in this draft cites evidence that does not exist.</strong> The factual sections
                remain deterministic and trustworthy; treat the executive summary as unreliable.
              </p>
            </div>
          ) : null}

          {/* --------------------------------------- the document */}
          <div className="relative">
            {/* Watermark. Fixed, repeated, and preserved in print. */}
            <div
              aria-hidden="true"
              className="pointer-events-none absolute inset-0 z-10 flex select-none items-center justify-center overflow-hidden"
            >
              <span className="rotate-[-30deg] whitespace-nowrap text-[5rem] font-black uppercase tracking-widest text-destructive/[0.07] md:text-[8rem]">
                Draft
              </span>
            </div>

            <Card className="relative bg-white print:border-0 print:shadow-none">
              <CardContent className="p-6 md:p-10">
                <header className="border-b-2 border-foreground pb-4">
                  <p className="text-center text-xs font-bold uppercase tracking-[0.2em] text-destructive">
                    {sar.data.marking}
                  </p>
                  <h2 className="mt-3 text-center text-lg font-bold uppercase tracking-wide">
                    Suspicious Activity Report -- Draft
                  </h2>
                  <div className="mt-3 flex flex-wrap justify-center gap-x-6 gap-y-1 text-[11px] text-muted-foreground">
                    <span>
                      Reference: <span className="font-mono">{sar.data.sar_ref}</span>
                    </span>
                    <span>Generated: {fmtDateTime(sar.data.generated_at)}</span>
                    <span>
                      Prompt: <span className="font-mono">{sar.data.prompt_version ?? "n/a"}</span>
                    </span>
                  </div>
                </header>

                {sar.data.sections.map((s) => (
                  <section key={s.key} className="mt-6 break-inside-avoid">
                    <div className="flex flex-wrap items-baseline gap-2 border-b pb-1">
                      <h3 className="text-sm font-bold uppercase tracking-wide">{s.title}</h3>
                      {/* Per-section attribution. A reviewer must see how much a
                          machine wrote before they sign it. */}
                      <span
                        className={
                          s.generated_by.startsWith("llm")
                            ? "rounded bg-violet-50 px-1.5 py-0.5 text-[9px] font-medium text-violet-700"
                            : "rounded bg-slate-100 px-1.5 py-0.5 text-[9px] font-medium text-slate-600"
                        }
                        title={
                          s.generated_by.startsWith("llm")
                            ? "Written by a language model from the deterministic sections."
                            : "Assembled deterministically from stored records."
                        }
                      >
                        {s.generated_by.startsWith("llm") ? "AI narrative" : s.generated_by === "unavailable" ? "Unavailable" : "Deterministic"}
                      </span>
                      {s.evidence_ids.length ? (
                        <span className="ml-auto font-mono text-[9px] text-muted-foreground">
                          evidence: {s.evidence_ids.slice(0, 12).join(", ")}
                        </span>
                      ) : null}
                    </div>
                    <SarBody body={s.body} />
                  </section>
                ))}

                {/* Local working notes. Clearly not persisted. */}
                <section className="mt-6 break-inside-avoid print:hidden">
                  <div className="flex items-center gap-2 border-b pb-1">
                    <h3 className="text-sm font-bold uppercase tracking-wide">Reviewer working notes</h3>
                    <Button variant="ghost" size="sm" onClick={() => setEditing((v) => !v)}>
                      {editing ? "Done" : "Edit"}
                    </Button>
                  </div>
                  {editing ? (
                    <Textarea className="mt-2" value={notes} onChange={(e) => setNotes(e.target.value)} rows={6} />
                  ) : (
                    <pre className="mt-2 whitespace-pre-wrap font-sans text-[12px]">{notes || "(no notes)"}</pre>
                  )}
                  <p className="mt-1 text-[10px] text-muted-foreground">
                    Local to this browser only. The backend deliberately freezes a draft at generation and exposes no
                    update endpoint -- a SAR is a point-in-time assertion, and a reviewer approves the document they
                    read. Record a durable decision using Approve / Reject below.
                  </p>
                </section>
              </CardContent>
            </Card>
          </div>

          {/* --------------------------------------- decision */}
          <Card className="print:hidden">
            <CardContent className="space-y-2 p-3">
              <p className="text-xs font-medium">Reviewer decision</p>
              {!canDecide ? (
                <p className="text-xs text-muted-foreground">
                  This case is {humanize(detail.data?.case.status ?? "")}. A SAR can only be approved or rejected while
                  the case is in SAR review.
                </p>
              ) : (
                <>
                  <Input
                    aria-label="Reviewer name"
                    placeholder="your.name (required)"
                    value={reviewer}
                    onChange={(e) => setReviewer(e.target.value)}
                  />
                  <div className="flex flex-wrap gap-2">
                    <Button
                      size="sm"
                      disabled={!reviewer.trim() || review.isPending}
                      onClick={() =>
                        review.mutate({
                          reviewer: reviewer.trim(),
                          action: "APPROVE_DRAFT_SAR",
                          comment: notes || undefined,
                          target_id: sar.data!.id,
                        })
                      }
                    >
                      <Check className="h-3.5 w-3.5" /> Approve draft
                    </Button>
                    <Button
                      size="sm"
                      variant="destructive"
                      disabled={!reviewer.trim() || review.isPending}
                      onClick={() =>
                        review.mutate({
                          reviewer: reviewer.trim(),
                          action: "REJECT_DRAFT_SAR",
                          comment: notes || undefined,
                          target_id: sar.data!.id,
                        })
                      }
                    >
                      <X className="h-3.5 w-3.5" /> Reject draft
                    </Button>
                  </div>
                  <p className="text-[10px] text-muted-foreground">
                    Approving marks the draft fit to file. It does not file it, and it does not close the case -- this
                    system never files a SAR.
                  </p>
                  {review.isError ? <ErrorState title="Rejected" detail={(review.error as Error).message} /> : null}
                </>
              )}
            </CardContent>
          </Card>
        </>
      ) : null}
    </Page>
  )
}

/**
 * Render one SAR section body so it reads like a document rather than a raw
 * dump. The backend authors two kinds of content:
 *
 *  - TABULAR sections (Subject Information, Chronology, Risk Indicators) are
 *    hand-aligned with spaces for a fixed-width font. Rendered in a proportional
 *    font they turn to ragged mush -- "Client ID:      44" no longer lines up.
 *    These are shown in monospace so the alignment the author intended actually
 *    holds, and scroll horizontally instead of wrapping (wrapping an aligned row
 *    breaks the columns).
 *  - PROSE sections (the AI narrative, findings, recommendations, disclaimer)
 *    are sentences. These are shown as wrapped paragraphs in a normal reading
 *    font, split on blank lines so paragraphs breathe.
 *
 * A body is treated as tabular when at least two of its lines contain a run of
 * two or more spaces between non-space characters -- the signature of column
 * padding. This is content-driven, so a section that changes shape later still
 * renders correctly without a hardcoded per-section rule.
 */
function SarBody({ body }: { body: string }) {
  const lines = body.split("\n")
  const tabularLines = lines.filter((l) => /\S {2,}\S/.test(l)).length
  const isTabular = tabularLines >= 2

  if (isTabular) {
    return (
      <div className="mt-2 overflow-x-auto">
        <pre className="font-mono text-[11px] leading-relaxed text-foreground">{body}</pre>
      </div>
    )
  }

  // Prose: blank-line-separated blocks become paragraphs; line breaks within a
  // block are preserved (a list of recommendations keeps its lines) but wrap.
  const blocks = body.split(/\n\s*\n/).map((b) => b.trim()).filter(Boolean)
  return (
    <div className="mt-2 space-y-2 text-[12px] leading-relaxed">
      {blocks.map((block, i) => (
        <p key={i} className="whitespace-pre-wrap">
          {block}
        </p>
      ))}
    </div>
  )
}
