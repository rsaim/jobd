/**
 * The wire.
 *
 * One module owns every call to the Python API, so a page never builds a URL
 * by hand and a shape change breaks in one place. Reads go through TanStack
 * Query keyed by the exact search string the page is showing — the key IS the
 * URL, which is what keeps "paste URL in fresh session, get identical view"
 * (M7 gate 1) true now that the view is a client app: there is no state to
 * desync because the query cache is addressed by the same string the address
 * bar holds.
 *
 * Writes are POSTs that return `{ok, message}` rather than redirecting, and
 * every one of them names the queries it invalidates. A mutation that forgets
 * to is how a dashboard shows you a stale record straight after you fixed it.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

export type Direction = "inbound" | "outbound"
export type Turn = "your_turn" | "their_turn" | "none"

export interface BriefingRow {
  company_id: string
  canonical_name: string
  kind: string
  application_id: string | null
  role_title: string | null
  latest_stage: string | null
  last_message_at: string | null
  last_subject: string | null
  days_silent: number
  status: string
}

export interface Stats {
  total_applications: number
  by_outcome: Record<string, number>
  response_rate: number
  ghost_rate: number
  interviews: number
  interview_hours: number
  prep_hours: number
  offers: number
  rejections: number
  engaged: number
  replied: number
  interviewed: number
  rounds_30d: number
  rounds_prev_30d: number
  outbound_30d: number
  outbound_prev_30d: number
  reply_lag_days: number | null
  court_yours: number
  court_theirs: number
}

export interface ActivityCell {
  day: string
  count: number
  level: number
}

export interface ActivityCalendar {
  weeks: (ActivityCell | null)[][]
  month_labels: [number, string][]
  total: number
  active_days: number
  busiest: { day: string; count: number } | null
  since: string
  until: string
  /** What the cells count — "interviews" (confirmed rounds) or "messages"
   *  (inbound job-linked mail). Labels and totals name it. */
  metric: "interviews" | "messages"
}

export type ActivityMetric = ActivityCalendar["metric"]

/** The activity grid on its own, for the graph's metric picker — swapping
 *  series refetches this, not the whole page payload that hosts the graph. */
export const useActivity = (metric: ActivityMetric, companyId?: string) =>
  useQuery({
    queryKey: ["activity", metric, companyId ?? null],
    queryFn: () =>
      get<ActivityCalendar>(
        `/activity${qs({ metric, company: companyId })}`,
      ),
    staleTime: 60_000,
  })

export interface HomePayload {
  calendar: ActivityCalendar
  waiting: BriefingRow[]
  cold: BriefingRow[]
  /** True count of mid-process applications, not capped like `waiting`/
   *  `cold` — Home shows no in-flight row list, only this number. */
  moving_count: number
  stats: Stats
  counts: Record<string, number>
}

/** One semantic hit: a communications line plus the two numbers that let a
 *  reader judge it — how close the embedding put it, and which of the words
 *  they typed actually appear in it. An empty `shared_words` is the claim
 *  this whole search path exists to make. */
export interface SearchHit extends CommunicationRow {
  company_id: string | null
  company_name: string | null
  distance: number
  similarity: number
  shared_words: string[]
}

export interface SearchPayload {
  query: string
  like: { id: string; subject: string | null } | null
  embedded_query: boolean
  coverage: { total: number; embedded: number }
  companies: { id: string; name: string | null; count: number }[]
  hits: SearchHit[]
}

export interface CompanyRow {
  id: string
  canonical_name: string
  domain: string | null
  kind: string
  message_count: number
  application_count: number
  last_touch: string | null
  last_direction: Direction | null
  latest_stage: string | null
  turn: Turn
}

export interface CommunicationRow {
  id: string
  channel: string
  direction: Direction
  sent_at: string
  subject: string | null
  contact_name: string | null
  contact_address: string | null
  application_id: string | null
  role_title: string | null
  is_stage_evidence: boolean
  link_role: string | null
  classification_tag: string
  /** The reply text with its quoted history already cut (envelope.body_text),
   *  when the list query carried it — a preview line, not the whole body. */
  body_text?: string | null
}

export interface Claim {
  stage: string
  occurred_at: string
  evidence_message_id: string
  evidence_subject: string | null
  evidence_direction: Direction
  confidence: number | null
  extracted_by: string
}

export interface ApplicationTimeline {
  application_id: string
  role_title: string | null
  started_at: string
  ended_at: string | null
  outcome: string | null
  message_count: number
  last_message_at: string | null
  last_direction: Direction | null
  ghosted: boolean
  claims: Claim[]
}

export interface CompanyTimeline {
  company_id: string
  canonical_name: string
  domain: string | null
  kind: string
  first_seen_at: string
  last_seen_at: string
  unlinked_messages: number
  applications: ApplicationTimeline[]
}

export interface PersonRow {
  contact_id: string
  name: string
  address: string
  channel: string
  role_title: string | null
  message_count: number
  first_at: string | null
  last_at: string | null
  stages: string[]
  company_count: number
  automated_reason: string | null
  is_system: boolean
}

export interface SuggestedRule {
  match_type: string
  value: string
  verdict: string
  reason?: string | null
}

export interface Verification {
  id: string
  model: string
  message_count: number
  classification_correct: boolean | null
  verified_name: string | null
  verified_kind: string | null
  verified_domain: string | null
  reasoning: string | null
  suggested_rules: SuggestedRule[]
  created_at: string
}

export interface CompanyPayload {
  timeline: CompanyTimeline
  communications: CommunicationRow[]
  turn: Turn
  people: PersonRow[]
  calendar: ActivityCalendar
  verification: Verification | null
}

export interface MessageDetail {
  id: string
  subject: string | null
  sent_at: string
  direction: Direction
  channel: string
  sender_address: string | null
  recipient_addresses: string[]
  thread_id: string | null
  body_text: string
  tag: string
  classified_by: string | null
  company_id: string | null
  company_name: string | null
  contact_id: string | null
  contact_name: string | null
  contact_address: string | null
  evidences: [string, string | null][]
  correspondent: string | null
  self_address: string | null
}

export interface MessagesPayload {
  messages: CommunicationRow[]
  total: number
  page: number
  pages: number
}

export interface ReviewItem {
  id: string
  message_id: string
  extraction: Record<string, unknown>
  confidence: number | null
  reason: string
  extracted_by: string
  created_at: string
  subject: string | null
  sent_at: string
  direction: Direction
}

export interface RuleRow {
  id: string
  match_type: string
  value: string
  verdict: string
  category: string | null
  source: string
  created_at: string
  company_id: string | null
  company_name: string | null
}

export interface CategoryRow {
  category: string
  verdict: string
  company_name: string | null
  rule_count: number
}

export interface AuditRow {
  id: string
  company_id: string | null
  company_name: string | null
  model: string
  message_count: number
  classification_correct: boolean | null
  verified_name: string | null
  verified_kind: string | null
  reasoning: string | null
  suggested_rules: SuggestedRule[]
  created_at: string
}

export interface UnresolvedSender {
  key: string
  kind: string
  count: number
  sample_subject: string | null
}

export interface TriagePayload {
  tab: string
  counts: Record<string, number>
  page: number
  items?: ReviewItem[]
  reasons?: [string, number][]
  selected?: MessageDetail | null
  /** Queue tab only — total matching rows and the server's page size, for
   *  the Next/Previous control (unrelated to `page`, which is the current
   *  page number and is set on every tab). */
  total?: number
  page_size?: number
  coverage?: Record<string, number>
  unresolved?: UnresolvedSender[]
  categories?: CategoryRow[]
  rules?: RuleRow[]
  verifications?: AuditRow[]
}

export interface PipelinePayload {
  home_stats: {
    total_messages: number
    unclassified: number
    negative_filtered: number
    recorded: number
    not_job_related: number
    queued_for_review: number
    companies_by_kind: Record<string, number>
    rules_by_source: Record<string, number>
    review_queue_pending: number
  }
  stats: Stats
  coverage: Record<string, number>
  ingest: {
    runs: number
    failures: number
    last_run_at: string | null
    days_covered: number
    first_day: string | null
    last_day: string | null
    missing_days: number
    recent: (string | number | null)[][]
  }
}

export interface ContactPayload {
  display_name: string | null
  identities: [string, string][]
  companies: {
    company_id: string
    canonical_name: string
    kind: string
    role_title: string | null
    message_count: number
    first_seen_at: string
    last_seen_at: string
  }[]
  messages: CommunicationRow[]
}

export class ApiError extends Error {
  status: number

  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`/api${path}`, {
    headers: { accept: "application/json" },
  })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((body) => body.detail as string)
      .catch(() => response.statusText)
    throw new ApiError(detail, response.status)
  }
  return (await response.json()) as T
}

export interface WriteResult {
  ok: boolean
  message: string
}

async function post(path: string, body?: unknown): Promise<WriteResult> {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((payload) => payload.detail as string)
      .catch(() => response.statusText)
    throw new ApiError(detail ?? "Write failed.", response.status)
  }
  return (await response.json()) as WriteResult
}

/** Like `post`, but for the handful of endpoints that don't return the
 *  standard `{ok, message}` shape — the summary generator returns the
 *  summary itself, not a write confirmation. */
async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api${path}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body ?? {}),
  })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((payload) => payload.detail as string)
      .catch(() => response.statusText)
    throw new ApiError(detail ?? "Request failed.", response.status)
  }
  return (await response.json()) as T
}

async function del(path: string): Promise<WriteResult> {
  const response = await fetch(`/api${path}`, { method: "DELETE" })
  if (!response.ok) {
    const detail = await response
      .json()
      .then((payload) => payload.detail as string)
      .catch(() => response.statusText)
    throw new ApiError(detail ?? "Delete failed.", response.status)
  }
  return (await response.json()) as WriteResult
}

/** `?a=1&b=2` from the parts a page actually set — empty values are dropped
 *  rather than sent as `&kind=`, so the URL says only what is filtered. */
export function qs(params: Record<string, string | number | undefined | null>) {
  const search = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") {
      search.set(key, String(value))
    }
  }
  const text = search.toString()
  return text ? `?${text}` : ""
}

export const useChrome = () =>
  useQuery({
    queryKey: ["chrome"],
    queryFn: () =>
      get<{
        pending: number
        chat_model: string | null
        chat_models: string[]
        storage_configured: boolean
      }>("/chrome"),
    staleTime: 30_000,
  })

/** Which companies have a logo cached on this machine.
 *
 *  One request for the whole app, not one per company: asking `/company/<id>
 *  /logo` per row would take a 404 for every company `jobd logos` found
 *  nothing for. The answer only changes when that command runs again, so it
 *  never goes stale on its own. */
export const useLogos = () =>
  useQuery({
    queryKey: ["logos"],
    queryFn: async () => {
      const payload = await get<{
        ids: string[]
        coverage: { companies: number; with_domain: number; found: number; missing: number }
      }>("/logos")
      return { has: new Set(payload.ids), coverage: payload.coverage }
    },
    staleTime: Infinity,
  })

export const useHome = () =>
  useQuery({ queryKey: ["home"], queryFn: () => get<HomePayload>("/home") })

/** Its own page (pages/offers.tsx), not a Home section — every offer ever
 *  received, not the recency-bounded slice `/home` shows. */
export const useOffers = () =>
  useQuery({
    queryKey: ["offers"],
    queryFn: () => get<{ offers: BriefingRow[] }>("/offers"),
  })

/** Its own page (pages/rejections.tsx), mirroring `useOffers`. */
export const useRejections = () =>
  useQuery({
    queryKey: ["rejections"],
    queryFn: () => get<{ rejections: BriefingRow[] }>("/rejections"),
  })

export const useCompanies = (search: string) =>
  useQuery({
    queryKey: ["companies", search],
    queryFn: () => get<{ companies: CompanyRow[]; total: number; stats: Stats }>(
      `/companies${search}`,
    ),
  })

/** Company lookup for the command palette. Separate from `useCompanies`
 *  because it must not fire on an empty box: the unfiltered list is 577 rows
 *  and the palette opens on every ⌘K, whether or not anything is typed. */
export const useCompanySearch = (query: string) =>
  useQuery({
    queryKey: ["company-search", query],
    queryFn: () =>
      get<{ companies: CompanyRow[] }>(
        `/companies?sort=recency&search=${encodeURIComponent(query)}`,
      ),
    enabled: query.trim().length >= 2,
    staleTime: 60_000,
  })

/** Semantic search. `staleTime: Infinity` is not a nicety here: a typed
 *  query costs one embedding call, so a background refetch would spend money
 *  to produce a result the reader is already looking at. Nothing about an
 *  embedded message changes under it either. */
export const useSemanticSearch = (search: string, enabled: boolean) =>
  useQuery({
    queryKey: ["search", search],
    queryFn: () => get<SearchPayload>(`/search${search}`),
    enabled,
    staleTime: Infinity,
    retry: false,
  })

export const useCompany = (id: string, search: string) =>
  useQuery({
    queryKey: ["company", id, search],
    queryFn: () => get<CompanyPayload>(`/company/${id}${search}`),
  })

export interface CompanySummary {
  markdown: string
  model: string
  created_at: string
  stale: boolean
}

export const useCompanySummary = (id: string) =>
  useQuery({
    queryKey: ["company-summary", id],
    queryFn: () => get<{ summary: CompanySummary | null }>(`/company/${id}/summary`),
    enabled: Boolean(id),
  })

export interface OutboundDraft {
  id: string
  status: "drafted" | "sent"
  subject: string
  body: string
  recipients: string[]
  sent_message_id: string | null
  approved_by: string | null
  approved_at: string | null
  created_at: string
}

export interface PromptSnippet {
  id: string
  label: string
  text: string
  created_at: string
}

/** The saved-prompt library (tones, and anything else worth reusing) — read
 *  by every "extra instructions" picker, managed on its own page. */
export const usePrompts = () =>
  useQuery({
    queryKey: ["prompts"],
    queryFn: () => get<{ prompts: PromptSnippet[] }>("/prompts"),
  })

export function useCreatePrompt() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: { label: string; text: string }) =>
      postJson<{ ok: boolean; message?: string; prompt?: PromptSnippet }>("/prompts", body),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["prompts"] }),
  })
}

export function useDeletePrompt() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (id: string) => del(`/prompts/${id}`),
    onSuccess: () => void client.invalidateQueries({ queryKey: ["prompts"] }),
  })
}

/** Ask the model for a reply body — no draft written, nothing sent, just
 *  text back for the reply box's own textarea. `prompt` is optional
 *  human-added instructions folded into this one call; call again (same or
 *  different prompt) to regenerate. */
export function useSuggestReply(messageId: string) {
  return useMutation({
    mutationFn: (prompt: string) =>
      postJson<{ ok: boolean; message?: string; body?: string }>(
        `/message/${messageId}/reply/suggest`,
        { prompt: prompt.trim() || null },
      ),
  })
}

export interface ReplyRecipients {
  to: string[]
  cc: string[]
}

/** What Gmail's own Reply / Reply-all buttons would pre-fill for this
 *  message — Reply-To over From, everyone-minus-you on Cc — resolved
 *  server-side from the stored raw message. Purely a pre-fill: every line
 *  stays editable before a draft is written. */
export const useReplyContext = (messageId: string) =>
  useQuery({
    queryKey: ["reply-context", messageId],
    queryFn: () =>
      get<{
        ok: boolean
        message?: string
        subject?: string
        reply?: ReplyRecipients
        reply_all?: ReplyRecipients
      }>(`/message/${messageId}/reply/context`),
    enabled: Boolean(messageId),
  })

/** Draft a reply to one message — writes to the channel's own drafts folder
 *  (Gmail, in this build) and records it. Never sends; see useSendReply. */
export function useDraftReply(messageId: string) {
  return useMutation({
    mutationFn: (body: { to: string[]; cc: string[]; subject: string; body: string }) =>
      postJson<{ ok: boolean; message?: string; draft?: OutboundDraft }>(
        `/message/${messageId}/reply/draft`,
        // ReplyDraftBody.to/.cc are comma-separated strings, not lists — see
        // the docstring (api.py): matches the shape every other proposal
        // field already posts.
        { ...body, to: body.to.join(","), cc: body.cc.join(",") },
      ),
  })
}

/** Send a previously drafted reply — the only call that can leave the
 *  mailbox. This request *is* the recorded human approval; there is no
 *  earlier point where consent could have been implied. */
export function useSendReply() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (outboundId: string) =>
      postJson<{ ok: boolean; message?: string; draft?: OutboundDraft }>(
        `/outbound/${outboundId}/send`,
        {},
      ),
    onSuccess: () => void client.invalidateQueries(),
  })
}

export const useMessages = (search: string) =>
  useQuery({
    queryKey: ["messages", search],
    queryFn: () => get<MessagesPayload>(`/messages${search}`),
  })

export const useMessage = (id: string | null) =>
  useQuery({
    queryKey: ["message", id],
    queryFn: () => get<MessageDetail>(`/message/${id}`),
    enabled: id !== null,
    // A body is immutable once ingested, so a row that is closed and reopened
    // should not refetch 3.5k characters it already has.
    staleTime: Infinity,
  })

export const useContact = (id: string) =>
  useQuery({
    queryKey: ["contact", id],
    queryFn: () => get<ContactPayload>(`/contact/${id}`),
  })

export const useTriage = (search: string) =>
  useQuery({
    queryKey: ["triage", search],
    queryFn: () => get<TriagePayload>(`/triage${search}`),
  })

export const usePipeline = () =>
  useQuery({ queryKey: ["pipeline"], queryFn: () => get<PipelinePayload>("/pipeline") })

/** One row per pipeline process (classify worker, sweep, audit, backfill),
 *  kept current by the process itself — see services/metrics.py. */
export type PipelineRun = {
  id: string
  group_id: string
  kind: string
  worker: string
  args: Record<string, unknown>
  status: string
  total: number | null
  processed: number
  counters: Record<string, number | string>
  llm_calls: number
  llm_tokens_in: number
  llm_tokens_out: number
  llm_cost_usd: number
  errors: number
  last_error: string | null
  started_at: string
  updated_at: string
  finished_at: string | null
  elapsed_s: number
  rate_per_min: number
  eta_s: number | null
  stalled: boolean
}

/** One model reading per thread (`thread_extraction`) — the Classifications
 *  page's table. Threads resolved free never appear here, by design. */
export type ThreadExtraction = {
  thread_id: string
  label: string
  company_name: string | null
  company_domain: string | null
  company_kind: string | null
  agency_name: string | null
  agency_domain: string | null
  role_title: string | null
  stage: string | null
  relationship: string | null
  client_companies: { name: string; domain?: string | null; role_title?: string | null }[]
  messages_covered: number
  model: string
  updated_at: string | null
  subject: string | null
  sender: string | null
}

export const useThreadExtractions = (label: string | null) =>
  useQuery({
    queryKey: ["thread-extractions", label],
    queryFn: () =>
      get<{ counts: Record<string, number>; threads: ThreadExtraction[] }>(
        `/thread-extractions${label ? `?label=${label}` : ""}`,
      ),
    staleTime: 10_000,
  })

/** Fast poll only while something is actually running — an idle Runs page
 *  left open in a tab must not hammer the API every 2s forever. */
export const useRuns = () =>
  useQuery({
    queryKey: ["runs"],
    queryFn: () => get<{ runs: PipelineRun[] }>("/runs"),
    refetchInterval: (query) =>
      query.state.data?.runs.some((r) => !r.finished_at) ? 2_000 : 15_000,
  })

/** Every write goes through here so none of them can forget to invalidate.
 *  `touches` is the list of query key prefixes the write can change. */
export function useWrite<TVariables>(
  path: (variables: TVariables) => string,
  touches: string[],
  body?: (variables: TVariables) => unknown,
) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (variables: TVariables) =>
      post(path(variables), body ? body(variables) : variables),
    onSuccess: () => {
      for (const key of touches) {
        void client.invalidateQueries({ queryKey: [key] })
      }
    },
  })
}

// ------------------------------------------------------------------ scrape

export interface ScrapeStatus {
  running: boolean
  started_at: string | null
  params: { accounts?: string[]; window_days?: number | null; model?: string }
  error: string | null
  result: Record<string, number> | null
  last_seq: number
}

export interface ScrapeRun {
  id: string
  started_at: string
  finished_at: string | null
  accounts: string[]
  window_days: number | null
  counters: Record<string, number>
}

/** Polled while a run is live so a crashed worker thread is noticed even if
 *  the SSE stream dies quietly. */
export const useScrapeStatus = (fast: boolean) =>
  useQuery({
    queryKey: ["scrape-status"],
    queryFn: () => get<ScrapeStatus>("/scrape/status"),
    refetchInterval: fast ? 4_000 : 30_000,
  })

export const useScrapeRuns = () =>
  useQuery({
    queryKey: ["scrape-runs"],
    queryFn: () => get<{ runs: ScrapeRun[] }>("/scrape/runs"),
  })

/** The whole-pipeline runner (Scrape page's "Full pipeline" card):
 *  `just daily` generalized — pick steps and a trailing window. */
export type PipelineRunStatus = {
  running: boolean
  step: string | null
  steps: string[]
  started_at: string | null
  finished_at: string | null
  results: { step: string; ok: boolean; tail: string }[]
  error: string | null
}

export const usePipelineRun = (fast: boolean) =>
  useQuery({
    queryKey: ["pipeline-run"],
    queryFn: () => get<PipelineRunStatus>("/pipeline/status"),
    refetchInterval: fast ? 3_000 : 30_000,
  })

export async function startPipeline(body: {
  steps: string[]
  window_days: number
}): Promise<{ ok: boolean; started?: boolean; message?: string }> {
  const response = await fetch("/api/pipeline/run", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!response.ok) return { ok: false, message: response.statusText }
  return (await response.json()) as { ok: boolean; started?: boolean; message?: string }
}

export async function startScrape(body: {
  window_days: number | null
  model: string
}): Promise<{ ok: boolean; started?: boolean; message?: string }> {
  const response = await fetch("/api/scrape/start", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  })
  if (!response.ok) return { ok: false, message: response.statusText }
  return (await response.json()) as { ok: boolean; started?: boolean; message?: string }
}
