export type ApplicationStatus = 'Approved' | 'Declined' | 'Human Review' | 'In Progress'
export type RiskTier = 'Low' | 'Moderate' | 'Elevated' | 'High'
export type LoanType = 'Working Capital' | 'Equipment Finance' | 'Commercial Real Estate' | 'SBA 7(a)'
export type PipelineStageName = 'Documents' | 'Credit' | 'Fraud' | 'Compliance' | 'Decision'
export type StageState = 'complete' | 'in-progress' | 'blocked' | 'pending'
export type TimelineDomain = 'loan' | 'credit' | 'fraud' | 'compliance' | 'agent session'
export type DecisionOutcome = 'Approve' | 'Decline' | 'Human Review'
export type ReplayProgressStatus = 'IDLE' | 'REPLAYING' | 'COMPLETED' | 'FAILED'

export interface KPIRecord {
  label: string
  value: string
  helperText: string
}

export interface TimelineEvent {
  eventName: string
  timestamp: string
  streamId: string
  version: number
  domain: TimelineDomain
  payloadPreview: string
  sessionId?: string
}

export interface PipelineStage {
  name: PipelineStageName
  state: StageState
  owner: string
  completedAt?: string
}

export interface AnalysisSummary {
  score: number
  verdict: string
  notes: string
  reasons: string[]
}

export interface ComplianceRuleResult {
  ruleId: string
  label: string
  status: 'passed' | 'failed'
  note: string
  regulationVersion: string
}

export interface ReviewQueueItem {
  applicationId: string
  businessName: string
  reason: string
  confidence: number
  assignedReviewer: string
  lastUpdated: string
}

export interface ManualReviewBacklogSnapshot {
  backlogCount: number
  pendingCount: number
  resolvedCount: number
  assignedCount: number
  unassignedCount: number
  averagePendingAgeMillis: number
  oldestPendingAgeMillis: number
  oldestPendingAt: string | null
  staleCount: number
}

export interface FactItem {
  label: string
  value: string
  tone?: 'good' | 'warning' | 'bad' | 'neutral'
}

export interface DocumentItem {
  name: string
  type: string
  size: string
  status: 'uploaded' | 'extracted' | 'verified'
}

export interface LoanApplication {
  id: string
  companyId: string
  businessName: string
  loanType: LoanType
  requestedAmount: number
  loanPurpose: string
  annualRevenue: number
  yearsInBusiness: number
  industry: string
  collateralType: string
  riskTier: RiskTier
  status: ApplicationStatus
  currentStatus: string
  finalRecommendation: DecisionOutcome
  confidenceScore: number
  lastUpdated: string
  decisionTimeMinutes: number
  assignedReviewer?: string
  reviewReason?: string
  reviewerNotes?: string
  policyNotes: string
  documentCount: number
  pipeline: PipelineStage[]
  facts: FactItem[]
  documents: DocumentItem[]
  analyses: {
    credit: AnalysisSummary
    fraud: AnalysisSummary
    compliance: AnalysisSummary
  }
  decisionReasons: string[]
  timeline: TimelineEvent[]
  complianceResults: ComplianceRuleResult[]
  extractedFacts: {
    revenue: string
    ebitda: string
    debt: string
    cashFlow: string
    flags: string[]
  }
}

export interface AgentPerformanceRecord {
  agentName: string
  modelVersion: string
  decisions: number
  overrides: number
  averageConfidence: number
  referralRate: number
  approved: number
  declined: number
  humanReview: number
}

export interface ProjectionLagEntry {
  positionsBehind: number
  millis: number
}

export type ProjectionLagSnapshot = Record<string, ProjectionLagEntry>

export interface EventThroughputBucket {
  label: string
  events: number
  startAt: string
  endAt: string
}

export interface EventThroughputSnapshot {
  windowMinutes: number
  bucketMinutes: number
  windowStartAt: string
  windowEndAt: string
  latestEventAt: string
  totalEvents: number
  eventsPerMinute: number
  eventsPerHour: number
  peakBucketEvents: number
  peakBucketLabel: string
  buckets: EventThroughputBucket[]
}

export interface StreamSizeSnapshotEntry {
  streamName: string
  eventCount: number
  streamPosition: number
  firstEventAt: string | null
  lastEventAt: string | null
}

export type StreamSizeSnapshot = StreamSizeSnapshotEntry[]

export interface ReplayProgressSnapshot {
  status: ReplayProgressStatus
  is_replaying: boolean
  projection_name: string | null
  events_processed: number
  total_events: number
  percent_complete: number
  started_at: string | null
  estimated_completion: string | null
  last_updated: string | null
}
