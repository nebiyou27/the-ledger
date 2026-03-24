import {
  AgentPerformanceRecord,
  EventThroughputSnapshot,
  ManualReviewBacklogSnapshot,
  LoanApplication,
  ProjectionLagSnapshot,
  ReviewQueueItem,
  TimelineEvent
} from '@/types/loan'

function makeEvent(
  eventName: string,
  timestamp: string,
  streamId: string,
  version: number,
  domain: TimelineEvent['domain'],
  payloadPreview: string,
  sessionId?: string
): TimelineEvent {
  return { eventName, timestamp, streamId, version, domain, payloadPreview, sessionId }
}

const app1Timeline: TimelineEvent[] = [
  makeEvent('LoanApplicationSubmitted', '2026-03-18T08:05:00Z', 'loan-APL-2026-0001', 0, 'loan', '{"companyId":"COMP-001","requestedAmount":1800000}'),
  makeEvent('DocumentUploaded', '2026-03-18T08:10:00Z', 'loan-APL-2026-0001', 1, 'loan', '{"documents":["financial_statements.xlsx","balance_sheet.pdf"]}'),
  makeEvent('ExtractionCompleted', '2026-03-18T08:16:00Z', 'loan-APL-2026-0001', 2, 'agent session', '{"revenue":12400000,"ebitda":2900000}', 'sess-doc-9021'),
  makeEvent('CreditAnalysisCompleted', '2026-03-18T08:21:00Z', 'loan-APL-2026-0001', 3, 'credit', '{"score":92,"debtServiceCoverage":2.4}', 'sess-credit-1184'),
  makeEvent('FraudScreeningCompleted', '2026-03-18T08:24:00Z', 'loan-APL-2026-0001', 4, 'fraud', '{"riskScore":4,"flags":[]}', 'sess-fraud-2044'),
  makeEvent('ComplianceCheckCompleted', '2026-03-18T08:27:00Z', 'loan-APL-2026-0001', 5, 'compliance', '{"result":"passed","policyVersion":"2026.03"}', 'sess-comp-5061'),
  makeEvent('DecisionGenerated', '2026-03-18T08:29:00Z', 'loan-APL-2026-0001', 6, 'loan', '{"outcome":"Approve","confidence":0.94}', 'sess-decision-7712')
]

const app2Timeline: TimelineEvent[] = [
  makeEvent('LoanApplicationSubmitted', '2026-03-18T09:12:00Z', 'loan-APL-2026-0002', 0, 'loan', '{"companyId":"COMP-033","requestedAmount":3200000}'),
  makeEvent('DocumentUploaded', '2026-03-18T09:18:00Z', 'loan-APL-2026-0002', 1, 'loan', '{"documents":["proposal.pdf","financials.xlsx"]}'),
  makeEvent('ExtractionCompleted', '2026-03-18T09:24:00Z', 'loan-APL-2026-0002', 2, 'agent session', '{"revenue":8400000,"ebitda":320000}', 'sess-doc-9033'),
  makeEvent('CreditAnalysisCompleted', '2026-03-18T09:31:00Z', 'loan-APL-2026-0002', 3, 'credit', '{"score":48,"debtServiceCoverage":1.02}', 'sess-credit-1185'),
  makeEvent('FraudScreeningCompleted', '2026-03-18T09:34:00Z', 'loan-APL-2026-0002', 4, 'fraud', '{"riskScore":37,"flags":["beneficial_owner_mismatch"]}', 'sess-fraud-2045'),
  makeEvent('ComplianceCheckCompleted', '2026-03-18T09:38:00Z', 'loan-APL-2026-0002', 5, 'compliance', '{"result":"failed","policyVersion":"2026.03"}', 'sess-comp-5062'),
  makeEvent('DecisionGenerated', '2026-03-18T09:41:00Z', 'loan-APL-2026-0002', 6, 'loan', '{"outcome":"Decline","confidence":0.88}', 'sess-decision-7713')
]

const app3Timeline: TimelineEvent[] = [
  makeEvent('LoanApplicationSubmitted', '2026-03-19T10:03:00Z', 'loan-APL-2026-0003', 0, 'loan', '{"companyId":"COMP-058","requestedAmount":850000}'),
  makeEvent('DocumentUploaded', '2026-03-19T10:08:00Z', 'loan-APL-2026-0003', 1, 'loan', '{"documents":["financial_summary.csv","bank_statements.pdf"]}'),
  makeEvent('ExtractionCompleted', '2026-03-19T10:14:00Z', 'loan-APL-2026-0003', 2, 'agent session', '{"revenue":5600000,"ebitda":410000}', 'sess-doc-9047'),
  makeEvent('CreditAnalysisCompleted', '2026-03-19T10:19:00Z', 'loan-APL-2026-0003', 3, 'credit', '{"score":67,"debtServiceCoverage":1.38}', 'sess-credit-1186'),
  makeEvent('FraudScreeningCompleted', '2026-03-19T10:23:00Z', 'loan-APL-2026-0003', 4, 'fraud', '{"riskScore":18,"flags":["file_name_variance","missing_bank_page"]}', 'sess-fraud-2046'),
  makeEvent('ComplianceCheckCompleted', '2026-03-19T10:27:00Z', 'loan-APL-2026-0003', 5, 'compliance', '{"result":"review","policyVersion":"2026.03"}', 'sess-comp-5063'),
  makeEvent('HumanReviewRequested', '2026-03-19T10:30:00Z', 'loan-APL-2026-0003', 6, 'loan', '{"reason":"ambiguous bank statement support","confidence":0.67}', 'sess-decision-7714')
]

const app4Timeline: TimelineEvent[] = [
  makeEvent('LoanApplicationSubmitted', '2026-03-19T13:40:00Z', 'loan-APL-2026-0004', 0, 'loan', '{"companyId":"COMP-079","requestedAmount":1200000}'),
  makeEvent('DocumentUploaded', '2026-03-19T13:44:00Z', 'loan-APL-2026-0004', 1, 'loan', '{"documents":["proposal.pdf"]}')
]

export const applications: LoanApplication[] = [
  {
    id: 'APL-2026-0001',
    companyId: 'COMP-001',
    businessName: 'Apex Industrial Supplies Ltd',
    loanType: 'Working Capital',
    requestedAmount: 1800000,
    loanPurpose: 'Inventory ramp and seasonal working capital',
    annualRevenue: 12400000,
    yearsInBusiness: 11,
    industry: 'Industrial distribution',
    collateralType: 'Receivables and equipment',
    riskTier: 'Low',
    status: 'Approved',
    currentStatus: 'Underwriting complete',
    finalRecommendation: 'Approve',
    confidenceScore: 94,
    lastUpdated: '2026-03-18T08:29:00Z',
    decisionTimeMinutes: 24,
    assignedReviewer: 'N. Patel',
    policyNotes: 'Meets minimum DSCR, clean compliance record, and low fraud indicators.',
    documentCount: 5,
    pipeline: [
      { name: 'Documents', state: 'complete', owner: 'Document AI', completedAt: '2026-03-18T08:16:00Z' },
      { name: 'Credit', state: 'complete', owner: 'Credit Analyst', completedAt: '2026-03-18T08:21:00Z' },
      { name: 'Fraud', state: 'complete', owner: 'Fraud Screen', completedAt: '2026-03-18T08:24:00Z' },
      { name: 'Compliance', state: 'complete', owner: 'Compliance Agent', completedAt: '2026-03-18T08:27:00Z' },
      { name: 'Decision', state: 'complete', owner: 'Decision Engine', completedAt: '2026-03-18T08:29:00Z' }
    ],
    facts: [
      { label: 'Revenue', value: '$12.4M', tone: 'good' },
      { label: 'EBITDA', value: '$2.9M', tone: 'good' },
      { label: 'Debt', value: '$1.1M', tone: 'neutral' },
      { label: 'Cash Flow', value: '$1.6M', tone: 'good' },
      { label: 'Flags', value: 'None material', tone: 'good' }
    ],
    documents: [
      { name: 'financial_statements.xlsx', type: 'Financials', size: '184 KB', status: 'verified' },
      { name: 'balance_sheet_2024.pdf', type: 'Balance Sheet', size: '1.2 MB', status: 'verified' },
      { name: 'income_statement_2024.pdf', type: 'Income Statement', size: '1.0 MB', status: 'verified' }
    ],
    analyses: {
      credit: {
        score: 92,
        verdict: 'Strong repayment profile',
        notes: 'Revenue growth and cash conversion support the requested facility.',
        reasons: ['DSCR above policy minimum', 'Low delinquency history', 'Stable supplier relationships']
      },
      fraud: {
        score: 4,
        verdict: 'Low fraud risk',
        notes: 'No adverse media or identity mismatches detected.',
        reasons: ['Document metadata consistent', 'Bank statement reconciliation passed', 'No synthetic signals']
      },
      compliance: {
        score: 100,
        verdict: 'Passed',
        notes: 'All required controls satisfied under policy 2026.03.',
        reasons: ['KYC complete', 'Sanctions screening clear', 'Beneficial ownership verified']
      }
    },
    decisionReasons: ['Healthy cash flow coverage', 'Low fraud exposure', 'Compliance passed without exceptions'],
    timeline: app1Timeline,
    complianceResults: [
      { ruleId: 'KYC-001', label: 'Identity and ownership verification', status: 'passed', note: 'Ownership documented and matches registry.', regulationVersion: '2026.03' },
      { ruleId: 'AML-003', label: 'Sanctions and adverse media screening', status: 'passed', note: 'No watchlist or adverse media findings.', regulationVersion: '2026.03' },
      { ruleId: 'CRD-002', label: 'Minimum cash flow coverage', status: 'passed', note: 'Cash flow exceeds threshold by 18%.', regulationVersion: '2026.03' }
    ],
    extractedFacts: {
      revenue: '$12.4M',
      ebitda: '$2.9M',
      debt: '$1.1M',
      cashFlow: '$1.6M',
      flags: ['No adverse media', 'Bank statements reconciled']
    }
  },
  {
    id: 'APL-2026-0002',
    companyId: 'COMP-033',
    businessName: 'Cedar Peak Manufacturing Inc',
    loanType: 'Commercial Real Estate',
    requestedAmount: 3200000,
    loanPurpose: 'Equipment expansion and facility refurbishment',
    annualRevenue: 8400000,
    yearsInBusiness: 6,
    industry: 'Light manufacturing',
    collateralType: 'Plant and machinery',
    riskTier: 'High',
    status: 'Declined',
    currentStatus: 'Final decision issued',
    finalRecommendation: 'Decline',
    confidenceScore: 88,
    lastUpdated: '2026-03-18T09:41:00Z',
    decisionTimeMinutes: 39,
    assignedReviewer: 'S. Mensah',
    reviewReason: 'Policy exception required and fraud/compliance combined risk exceeded threshold.',
    policyNotes: 'Debt service coverage below threshold with compliance failure on ownership validation.',
    documentCount: 4,
    pipeline: [
      { name: 'Documents', state: 'complete', owner: 'Document AI', completedAt: '2026-03-18T09:24:00Z' },
      { name: 'Credit', state: 'complete', owner: 'Credit Analyst', completedAt: '2026-03-18T09:31:00Z' },
      { name: 'Fraud', state: 'complete', owner: 'Fraud Screen', completedAt: '2026-03-18T09:34:00Z' },
      { name: 'Compliance', state: 'complete', owner: 'Compliance Agent', completedAt: '2026-03-18T09:38:00Z' },
      { name: 'Decision', state: 'complete', owner: 'Decision Engine', completedAt: '2026-03-18T09:41:00Z' }
    ],
    facts: [
      { label: 'Revenue', value: '$8.4M', tone: 'neutral' },
      { label: 'EBITDA', value: '$320K', tone: 'warning' },
      { label: 'Debt', value: '$3.8M', tone: 'bad' },
      { label: 'Cash Flow', value: '$240K', tone: 'warning' },
      { label: 'Flags', value: 'Ownership mismatch, thin coverage', tone: 'bad' }
    ],
    documents: [
      { name: 'APEX-0031_financials.xlsx', type: 'Financials', size: '229 KB', status: 'verified' },
      { name: 'APEX-0031_income_stmt_2024.pdf', type: 'Income Statement', size: '1.1 MB', status: 'verified' },
      { name: 'APEX-0031_balance_sheet_2024.pdf', type: 'Balance Sheet', size: '1.3 MB', status: 'verified' }
    ],
    analyses: {
      credit: {
        score: 48,
        verdict: 'Weak repayment profile',
        notes: 'Coverage is too tight for the requested facility size.',
        reasons: ['DSCR below policy floor', 'Elevated leverage', 'Negative trend in operating margin']
      },
      fraud: {
        score: 37,
        verdict: 'Elevated fraud risk',
        notes: 'File naming inconsistency and ownership mismatch require rejection or exception.',
        reasons: ['Beneficial owner mismatch', 'Document provenance inconsistent', 'High manual override likelihood']
      },
      compliance: {
        score: 41,
        verdict: 'Failed',
        notes: 'Policy checks failed on ownership validation and exposure concentration.',
        reasons: ['UBO verification incomplete', 'Exposure cap exceeded', 'Regulatory exception denied']
      }
    },
    decisionReasons: ['Below-threshold credit quality', 'Fraud and compliance findings reinforce decline', 'Policy exception not justified'],
    timeline: app2Timeline,
    complianceResults: [
      { ruleId: 'KYC-001', label: 'Identity and ownership verification', status: 'failed', note: 'Ownership chain could not be validated to policy standard.', regulationVersion: '2026.03' },
      { ruleId: 'AML-003', label: 'Sanctions and adverse media screening', status: 'passed', note: 'No direct sanctions matches, but review escalated due to ownership gap.', regulationVersion: '2026.03' },
      { ruleId: 'CRD-002', label: 'Minimum cash flow coverage', status: 'failed', note: 'Coverage ratio below minimum by 16%.', regulationVersion: '2026.03' }
    ],
    extractedFacts: {
      revenue: '$8.4M',
      ebitda: '$320K',
      debt: '$3.8M',
      cashFlow: '$240K',
      flags: ['Beneficial owner mismatch', 'Exposure cap exceeded']
    }
  },
  {
    id: 'APL-2026-0003',
    companyId: 'COMP-058',
    businessName: 'Greenline Logistics Services Ltd',
    loanType: 'SBA 7(a)',
    requestedAmount: 850000,
    loanPurpose: 'Fleet working capital and warehouse systems',
    annualRevenue: 5600000,
    yearsInBusiness: 8,
    industry: 'Logistics',
    collateralType: 'Receivables',
    riskTier: 'Moderate',
    status: 'Human Review',
    currentStatus: 'Pending manual review',
    finalRecommendation: 'Human Review',
    confidenceScore: 67,
    lastUpdated: '2026-03-19T10:30:00Z',
    decisionTimeMinutes: 27,
    assignedReviewer: 'A. Okafor',
    reviewReason: 'Missing source page in bank statement package and an amber fraud signal.',
    reviewerNotes: 'Review bank statement evidence before final disposition.',
    policyNotes: 'Risk is not disqualifying, but the case is not clean enough for auto-decision.',
    documentCount: 4,
    pipeline: [
      { name: 'Documents', state: 'complete', owner: 'Document AI', completedAt: '2026-03-19T10:14:00Z' },
      { name: 'Credit', state: 'complete', owner: 'Credit Analyst', completedAt: '2026-03-19T10:19:00Z' },
      { name: 'Fraud', state: 'complete', owner: 'Fraud Screen', completedAt: '2026-03-19T10:23:00Z' },
      { name: 'Compliance', state: 'complete', owner: 'Compliance Agent', completedAt: '2026-03-19T10:27:00Z' },
      { name: 'Decision', state: 'in-progress', owner: 'Human Reviewer' }
    ],
    facts: [
      { label: 'Revenue', value: '$5.6M', tone: 'neutral' },
      { label: 'EBITDA', value: '$410K', tone: 'neutral' },
      { label: 'Debt', value: '$1.9M', tone: 'warning' },
      { label: 'Cash Flow', value: '$530K', tone: 'neutral' },
      { label: 'Flags', value: 'Incomplete bank support, ownership complexity', tone: 'warning' }
    ],
    documents: [
      { name: 'financial_summary.csv', type: 'Financial Summary', size: '38 KB', status: 'verified' },
      { name: 'financial_statements.xlsx', type: 'Financials', size: '211 KB', status: 'verified' },
      { name: 'bank_statements.pdf', type: 'Bank Statements', size: '1.5 MB', status: 'uploaded' }
    ],
    analyses: {
      credit: {
        score: 67,
        verdict: 'Borderline but workable',
        notes: 'Business appears viable, though leverage is somewhat tight.',
        reasons: ['Coverage slightly above floor', 'Seasonal cash variability', 'Industry cycle manageable']
      },
      fraud: {
        score: 18,
        verdict: 'Moderate concern',
        notes: 'Minor document anomalies and a missing bank statement page triggered review.',
        reasons: ['File naming inconsistency', 'Bank support incomplete', 'Identity check needs human confirmation']
      },
      compliance: {
        score: 74,
        verdict: 'Pass with caution',
        notes: 'No hard policy fail, but the case merits human confirmation.',
        reasons: ['KYC complete', 'Sanctions clear', 'EDD review recommended']
      }
    },
    decisionReasons: ['Borderline credit quality', 'Fraud signal requires human judgment', 'Compliance clear but not enough to auto-approve'],
    timeline: app3Timeline,
    complianceResults: [
      { ruleId: 'KYC-001', label: 'Identity and ownership verification', status: 'passed', note: 'Identity checks passed, but ownership structure is layered.', regulationVersion: '2026.03' },
      { ruleId: 'AML-003', label: 'Sanctions and adverse media screening', status: 'passed', note: 'No sanctions or adverse media issues detected.', regulationVersion: '2026.03' },
      { ruleId: 'EDD-005', label: 'Enhanced due diligence review', status: 'passed', note: 'Manual review recommended instead of hard rejection.', regulationVersion: '2026.03' }
    ],
    extractedFacts: {
      revenue: '$5.6M',
      ebitda: '$410K',
      debt: '$1.9M',
      cashFlow: '$530K',
      flags: ['Incomplete bank support', 'Ownership complexity']
    }
  },
  {
    id: 'APL-2026-0004',
    companyId: 'COMP-079',
    businessName: 'North Harbor Coffee Roasters',
    loanType: 'Equipment Finance',
    requestedAmount: 1200000,
    loanPurpose: 'Roasting line upgrade and distribution fleet',
    annualRevenue: 4700000,
    yearsInBusiness: 5,
    industry: 'Food and beverage',
    collateralType: 'Equipment and inventory',
    riskTier: 'Moderate',
    status: 'In Progress',
    currentStatus: 'Awaiting document completion',
    finalRecommendation: 'Human Review',
    confidenceScore: 0,
    lastUpdated: '2026-03-19T13:44:00Z',
    decisionTimeMinutes: 0,
    assignedReviewer: 'Unassigned',
    policyNotes: 'Application is still collecting evidence.',
    documentCount: 1,
    pipeline: [
      { name: 'Documents', state: 'in-progress', owner: 'Document Intake' },
      { name: 'Credit', state: 'pending', owner: 'Credit Analyst' },
      { name: 'Fraud', state: 'pending', owner: 'Fraud Screen' },
      { name: 'Compliance', state: 'pending', owner: 'Compliance Agent' },
      { name: 'Decision', state: 'pending', owner: 'Decision Engine' }
    ],
    facts: [
      { label: 'Revenue', value: '$4.7M', tone: 'neutral' },
      { label: 'EBITDA', value: 'Pending', tone: 'neutral' },
      { label: 'Debt', value: 'Pending', tone: 'neutral' },
      { label: 'Cash Flow', value: 'Pending', tone: 'neutral' },
      { label: 'Flags', value: 'Documents still loading', tone: 'warning' }
    ],
    documents: [{ name: 'application_proposal.pdf', type: 'Proposal', size: '1.0 MB', status: 'uploaded' }],
    analyses: {
      credit: { score: 0, verdict: 'Not yet assessed', notes: 'Waiting for full financial package.', reasons: [] },
      fraud: { score: 0, verdict: 'Not yet assessed', notes: 'Waiting for full financial package.', reasons: [] },
      compliance: { score: 0, verdict: 'Not yet assessed', notes: 'Waiting for full financial package.', reasons: [] }
    },
    decisionReasons: ['Awaiting more documents'],
    timeline: app4Timeline,
    complianceResults: [],
    extractedFacts: {
      revenue: 'Pending',
      ebitda: 'Pending',
      debt: 'Pending',
      cashFlow: 'Pending',
      flags: ['Documents still loading']
    }
  }
]

export const timelineEvents: TimelineEvent[] = applications
  .flatMap((application) => application.timeline)
  .sort((left, right) => new Date(right.timestamp).getTime() - new Date(left.timestamp).getTime())

export const reviewQueue: ReviewQueueItem[] = applications
  .filter((application) => application.status === 'Human Review')
  .map((application) => ({
    applicationId: application.id,
    businessName: application.businessName,
    reason: application.reviewReason ?? application.policyNotes,
    confidence: application.confidenceScore,
    assignedReviewer: application.assignedReviewer ?? 'Unassigned',
    lastUpdated: application.lastUpdated
  }))

function buildManualReviewBacklogSnapshot(items: ReviewQueueItem[], asOf = new Date()): ManualReviewBacklogSnapshot {
  const pendingCount = items.length
  const assignedCount = items.filter((item) => item.assignedReviewer !== 'Unassigned').length
  const unassignedCount = pendingCount - assignedCount
  const pendingAges = items
    .map((item) => Math.max(0, asOf.getTime() - new Date(item.lastUpdated).getTime()))
    .filter((age) => Number.isFinite(age))
  const oldestPendingAgeMillis = pendingAges.length ? Math.max(...pendingAges) : 0
  const averagePendingAgeMillis = pendingAges.length
    ? Math.round(pendingAges.reduce((sum, age) => sum + age, 0) / pendingAges.length)
    : 0

  return {
    backlogCount: pendingCount,
    pendingCount,
    resolvedCount: 0,
    assignedCount,
    unassignedCount,
    averagePendingAgeMillis,
    oldestPendingAgeMillis,
    oldestPendingAt: items.reduce<string | null>((oldest, item) => {
      if (oldest === null) {
        return item.lastUpdated
      }
      return new Date(item.lastUpdated).getTime() < new Date(oldest).getTime() ? item.lastUpdated : oldest
    }, null),
    staleCount: pendingAges.filter((age) => age >= 24 * 60 * 60 * 1000).length
  }
}

export const manualReviewBacklogSnapshot = buildManualReviewBacklogSnapshot(reviewQueue)

export const complianceRows = applications.map((application) => ({
  applicationId: application.id,
  businessName: application.businessName,
  regulationVersion: application.complianceResults[0]?.regulationVersion ?? '2026.03',
  passedRules: application.complianceResults.filter((rule) => rule.status === 'passed'),
  failedRules: application.complianceResults.filter((rule) => rule.status === 'failed'),
  notes: application.policyNotes
}))

export const agentPerformance: AgentPerformanceRecord[] = [
  {
    agentName: 'Document AI',
    modelVersion: 'v2.8.1',
    decisions: 128,
    overrides: 6,
    averageConfidence: 0.91,
    referralRate: 0.09,
    approved: 74,
    declined: 31,
    humanReview: 23
  },
  {
    agentName: 'Credit Analyst',
    modelVersion: 'v3.1.4',
    decisions: 128,
    overrides: 12,
    averageConfidence: 0.86,
    referralRate: 0.14,
    approved: 68,
    declined: 36,
    humanReview: 24
  },
  {
    agentName: 'Fraud Screen',
    modelVersion: 'v1.9.7',
    decisions: 128,
    overrides: 8,
    averageConfidence: 0.88,
    referralRate: 0.11,
    approved: 70,
    declined: 33,
    humanReview: 25
  },
  {
    agentName: 'Compliance Agent',
    modelVersion: 'v4.0.2',
    decisions: 128,
    overrides: 4,
    averageConfidence: 0.95,
    referralRate: 0.07,
    approved: 79,
    declined: 28,
    humanReview: 21
  }
]

export const projectionLagSnapshot: ProjectionLagSnapshot = {
  application_summary: { positionsBehind: 0, millis: 180 },
  agent_session_failures: { positionsBehind: 1, millis: 760 },
  agent_performance: { positionsBehind: 0, millis: 95 },
  compliance_audit: { positionsBehind: 3, millis: 2460 },
  manual_reviews: { positionsBehind: 0, millis: 120 }
}

export const eventThroughputSnapshot: EventThroughputSnapshot = {
  windowMinutes: 60,
  bucketMinutes: 5,
  windowStartAt: '2026-03-19T09:30:00Z',
  windowEndAt: '2026-03-19T10:30:00Z',
  latestEventAt: '2026-03-19T10:30:00Z',
  totalEvents: 42,
  eventsPerMinute: 0.7,
  eventsPerHour: 42,
  peakBucketEvents: 6,
  peakBucketLabel: '10:00',
  buckets: [
    { label: '09:30', events: 2, startAt: '2026-03-19T09:30:00Z', endAt: '2026-03-19T09:35:00Z' },
    { label: '09:35', events: 3, startAt: '2026-03-19T09:35:00Z', endAt: '2026-03-19T09:40:00Z' },
    { label: '09:40', events: 4, startAt: '2026-03-19T09:40:00Z', endAt: '2026-03-19T09:45:00Z' },
    { label: '09:45', events: 5, startAt: '2026-03-19T09:45:00Z', endAt: '2026-03-19T09:50:00Z' },
    { label: '09:50', events: 4, startAt: '2026-03-19T09:50:00Z', endAt: '2026-03-19T09:55:00Z' },
    { label: '09:55', events: 6, startAt: '2026-03-19T09:55:00Z', endAt: '2026-03-19T10:00:00Z' },
    { label: '10:00', events: 6, startAt: '2026-03-19T10:00:00Z', endAt: '2026-03-19T10:05:00Z' },
    { label: '10:05', events: 5, startAt: '2026-03-19T10:05:00Z', endAt: '2026-03-19T10:10:00Z' },
    { label: '10:10', events: 4, startAt: '2026-03-19T10:10:00Z', endAt: '2026-03-19T10:15:00Z' },
    { label: '10:15', events: 3, startAt: '2026-03-19T10:15:00Z', endAt: '2026-03-19T10:20:00Z' },
    { label: '10:20', events: 2, startAt: '2026-03-19T10:20:00Z', endAt: '2026-03-19T10:25:00Z' },
    { label: '10:25', events: 4, startAt: '2026-03-19T10:25:00Z', endAt: '2026-03-19T10:30:00Z' }
  ]
}
