/**
 * Page 2 -- Customer Explorer.
 *
 * HONEST SCOPE NOTE. The brief asks for search, country filter, sort, and a
 * risk-level filter. The backend's /customers endpoint accepts ONLY
 * limit/offset/sanctions_flag/pep_flag/sector_risk/mapped_only -- there is no
 * search, no country filter, no sort, and ClientRead carries no risk score.
 * Backend redesign is out of scope for this phase.
 *
 * So this page is explicit about the split:
 *  - SERVER-SIDE (real, across all 2,000 clients): sector risk, sanctions flag,
 *    PEP flag, mapped-only, pagination.
 *  - CLIENT-SIDE (the loaded page only, and LABELLED as such): name/ID search,
 *    country filter, sort.
 *
 * Client-side filtering that silently searched only the current page while
 * looking like a global search would be the most misleading thing this page
 * could do -- a compliance officer would conclude a customer does not exist.
 * The banner says exactly what is being searched.
 */

import { useMemo, useState } from "react"
import { Link, useSearchParams } from "react-router-dom"
import { ArrowUpDown, Info, Search, Users } from "lucide-react"
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
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  Button,
} from "@/components/ui"
import { TierBadge } from "@/components/domain"
import { useCustomers } from "@/hooks/queries"
import { Page } from "./Dashboard"
import type { Client } from "@/api/types"

const PAGE_SIZE = 50
type SortKey = "client_name" | "external_client_id" | "country" | "sector_risk" | "ownership_opacity_score"

export default function Customers() {
  const [params, setParams] = useSearchParams()
  const [offset, setOffset] = useState(0)
  const [search, setSearch] = useState("")
  const [country, setCountry] = useState("")
  const [sortKey, setSortKey] = useState<SortKey>("external_client_id")
  const [sortAsc, setSortAsc] = useState(true)

  const sectorRisk = params.get("sector_risk") ?? ""
  const sanctionsOnly = params.get("sanctions_flag") === "true"
  const pepOnly = params.get("pep_flag") === "true"

  const query = useCustomers({
    limit: PAGE_SIZE,
    offset,
    sector_risk: sectorRisk || undefined,
    sanctions_flag: sanctionsOnly || undefined,
    pep_flag: pepOnly || undefined,
  })

  const rows = useMemo(() => {
    let list: Client[] = query.data ?? []
    if (search.trim()) {
      const q = search.trim().toLowerCase()
      list = list.filter(
        (c) => c.client_name.toLowerCase().includes(q) || String(c.external_client_id).includes(q),
      )
    }
    if (country) list = list.filter((c) => c.country === country)
    const dir = sortAsc ? 1 : -1
    return [...list].sort((a, b) => {
      const x = a[sortKey]
      const y = b[sortKey]
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir
      return String(x).localeCompare(String(y)) * dir
    })
  }, [query.data, search, country, sortKey, sortAsc])

  const countries = useMemo(
    () => Array.from(new Set((query.data ?? []).map((c) => c.country))).sort(),
    [query.data],
  )

  function setFilter(key: string, value: string) {
    const next = new URLSearchParams(params)
    if (value) next.set(key, value)
    else next.delete(key)
    setParams(next)
    setOffset(0)
  }

  function toggleSort(key: SortKey) {
    if (key === sortKey) setSortAsc((v) => !v)
    else {
      setSortKey(key)
      setSortAsc(true)
    }
  }

  // /customers has no `total`. The page count is what we loaded; a full page
  // means there is probably more. Showing a fabricated total would be worse
  // than showing an honest "page N".
  const loaded = query.data?.length ?? 0
  const hasMore = loaded === PAGE_SIZE
  const clientSideActive = Boolean(search.trim() || country)

  return (
    <Page
      title="Customer Explorer"
      subtitle="Server-side filters apply to the whole portfolio; search, country and sort apply to the loaded page."
    >
      <Card>
        <CardContent className="space-y-3 p-3">
          <div className="flex flex-wrap items-end gap-2">
            <div className="min-w-[200px] flex-1">
              <label htmlFor="cust-search" className="mb-1 block text-xs font-medium">
                Search name or ID
              </label>
              <div className="relative">
                <Search className="pointer-events-none absolute left-2 top-2.5 h-4 w-4 text-muted-foreground" />
                <Input
                  id="cust-search"
                  className="pl-8"
                  placeholder="Filters the loaded page only"
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                />
              </div>
            </div>
            <div>
              <label htmlFor="f-sector" className="mb-1 block text-xs font-medium">
                Sector risk
              </label>
              <Select id="f-sector" value={sectorRisk} onChange={(e) => setFilter("sector_risk", e.target.value)}>
                <option value="">All</option>
                <option value="High">High</option>
                <option value="Medium">Medium</option>
                <option value="Low">Low</option>
              </Select>
            </div>
            <div>
              <label htmlFor="f-country" className="mb-1 block text-xs font-medium">
                Country
              </label>
              <Select id="f-country" value={country} onChange={(e) => setCountry(e.target.value)}>
                <option value="">All (page)</option>
                {countries.map((c) => (
                  <option key={c} value={c}>
                    {c}
                  </option>
                ))}
              </Select>
            </div>
            <Button
              variant={sanctionsOnly ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter("sanctions_flag", sanctionsOnly ? "" : "true")}
              aria-pressed={sanctionsOnly}
            >
              Sanctions flag
            </Button>
            <Button
              variant={pepOnly ? "default" : "outline"}
              size="sm"
              onClick={() => setFilter("pep_flag", pepOnly ? "" : "true")}
              aria-pressed={pepOnly}
            >
              PEP flag
            </Button>
          </div>

          {/* The honesty banner. Without it, a page-scoped search looks global. */}
          <p className="flex items-start gap-1.5 rounded border border-blue-200 bg-blue-50 p-2 text-[11px] text-blue-800">
            <Info className="mt-px h-3.5 w-3.5 shrink-0" />
            <span>
              <strong>Sector risk, sanctions and PEP filters are server-side</strong> and apply to the whole
              portfolio. <strong>Search, country and sort are client-side</strong> and
              apply only to the {rows.length} rows loaded on this page -- the backend&apos;s /customers endpoint
              exposes no search, country, or sort parameter, and returns no total count.
              {clientSideActive ? " A client-side filter is active now." : ""}
            </span>
          </p>
        </CardContent>
      </Card>

      {query.isLoading ? (
        <LoadingBlock label="Loading customers" rows={8} />
      ) : query.error ? (
        <ErrorState title="Could not load customers" detail={(query.error as Error).message} />
      ) : rows.length === 0 ? (
        <EmptyState
          icon={Users}
          title="No customers match"
          description={
            loaded === 0 && offset === 0
              ? "The portfolio is empty. Run ingestion on the backend: POST /api/v1/ingestion/load"
              : "No rows on this page match the client-side filters. Try clearing them or paging further."
          }
        />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <SortHeader label="ID" active={sortKey === "external_client_id"} asc={sortAsc} onClick={() => toggleSort("external_client_id")} />
                <SortHeader label="Name" active={sortKey === "client_name"} asc={sortAsc} onClick={() => toggleSort("client_name")} />
                <TableHead>Type</TableHead>
                <SortHeader label="Country" active={sortKey === "country"} asc={sortAsc} onClick={() => toggleSort("country")} />
                <TableHead>Sector</TableHead>
                <SortHeader label="Sector risk" active={sortKey === "sector_risk"} asc={sortAsc} onClick={() => toggleSort("sector_risk")} />
                <TableHead>Flags</TableHead>
                <SortHeader label="Opacity" active={sortKey === "ownership_opacity_score"} asc={sortAsc} onClick={() => toggleSort("ownership_opacity_score")} />
                <TableHead>Provenance</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((c) => (
                <TableRow key={c.id}>
                  <TableCell className="font-mono text-xs">{c.external_client_id}</TableCell>
                  <TableCell>
                    <Link
                      to={`/customers/${c.id}`}
                      className="font-medium text-primary hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      {c.client_name}
                    </Link>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">{c.client_type}</TableCell>
                  <TableCell className="font-mono text-xs">{c.country}</TableCell>
                  <TableCell className="max-w-[12rem] truncate text-xs">{c.sector}</TableCell>
                  <TableCell>
                    <Badge
                      variant={c.sector_risk === "High" ? "destructive" : c.sector_risk === "Medium" ? "warning" : "muted"}
                    >
                      {c.sector_risk}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {/* Labelled as upstream. Phase 0 measured 0/2000 client names
                          matching the authoritative lists -- this flag is an
                          upstream label the platform did not derive. */}
                      {c.sanctions_flag ? (
                        <Badge variant="destructive" title="Upstream label. NOT independently verified by this platform.">
                          Sanctions
                        </Badge>
                      ) : null}
                      {c.pep_flag ? (
                        <Badge variant="warning" title="Upstream label. Not independently verified.">
                          PEP
                        </Badge>
                      ) : null}
                      {c.fatf_country_flag ? <Badge variant="outline">FATF</Badge> : null}
                    </div>
                  </TableCell>
                  <TableCell className="font-mono text-xs tabular-nums">
                    {c.ownership_opacity_score.toFixed(2)}
                  </TableCell>
                  <TableCell>
                    <TierBadge tier={c.source_tier} />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <div className="flex items-center justify-between border-t p-2 text-xs">
            <span className="text-muted-foreground">
              Showing {loaded === 0 ? 0 : offset + 1}-{offset + loaded}
            </span>
            <div className="flex gap-2">
              <Button variant="outline" size="sm" disabled={offset === 0} onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}>
                Previous
              </Button>
              <Button
                variant="outline"
                size="sm"
                disabled={!hasMore}
                onClick={() => setOffset((o) => o + PAGE_SIZE)}
              >
                Next
              </Button>
            </div>
          </div>
        </Card>
      )}
    </Page>
  )
}

function SortHeader({ label, active, asc, onClick }: { label: string; active: boolean; asc: boolean; onClick: () => void }) {
  return (
    <TableHead>
      <button
        type="button"
        onClick={onClick}
        aria-sort={active ? (asc ? "ascending" : "descending") : "none"}
        className="inline-flex items-center gap-1 rounded hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {label}
        <ArrowUpDown className={active ? "h-3 w-3 text-primary" : "h-3 w-3 opacity-40"} />
      </button>
    </TableHead>
  )
}
