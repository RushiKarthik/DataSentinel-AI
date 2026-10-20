import './style.css'

type Severity = 'critical' | 'high' | 'medium' | 'low' | 'info'
type Finding = {
  issue_id: string
  category: string
  severity: Severity
  title: string
  description: string
  column: string | null
  count: number | null
  total: number | null
  percentage: number | null
  recommendation: string
}
type ColumnProfile = { name: string; dtype: string; non_null_count: number; null_count: number; missing_count?: number; null_percentage: number; unique_count: number }
type Analysis = {
  dataset_id: string
  profile: { filename: string; row_count: number; column_count: number; columns: ColumnProfile[]; memory_usage_mb: number; upload_timestamp: string }
  quality_score: { overall_score: number; completeness_score: number; uniqueness_score: number; validity_score: number; consistency_score: number }
  findings: Finding[]
  total_issues: number
  csv_warnings?: string[]
  analysis_timestamp: string
}

const apiBase = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')
document.querySelector<HTMLDivElement>('#app')!.innerHTML = `
  <div class="app-shell" id="overview">
    <aside class="sidebar" aria-label="Main navigation">
      <a class="brand" href="#overview"><span class="brand-icon" aria-hidden="true">✳</span><span>DataSentinel<span class="accent"> AI</span></span></a>
      <div class="nav-label">WORKSPACE</div>
      <a class="nav-item active" href="#overview">▦ &nbsp; Overview</a>
      <a class="nav-item" href="#upload">↑ &nbsp; Analyze data</a>
      <a class="nav-item" href="#findings">⌁ &nbsp; Findings</a>
      <div class="sidebar-bottom">
        <div class="status-dot" id="health-dot"></div>
        <div><strong>Data engine</strong><small id="health-label">Checking connection…</small></div>
      </div>
    </aside>

    <main class="main">
      <header class="topbar"><div><span class="eyebrow">DATA QUALITY PLATFORM</span><h1>Data overview</h1></div><div class="online" id="health-badge" role="status"><span></span> Checking API</div></header>
      <section class="welcome"><div><div class="eyebrow light">YOUR DATA, UNDER CONTROL</div><h2>Make your data<br> <span>trustworthy.</span></h2><p>Analyze a CSV to find missing values, duplicates, invalid records and other measurable quality issues.</p></div><div class="hero-art" aria-hidden="true"><div class="orbit orbit-one"></div><div class="orbit orbit-two"></div><div class="shield">✓</div><span class="float-tag tag-one">✓ Evidence-based checks</span><span class="float-tag tag-two">⌁ Data health</span></div></section>

      <section class="section-heading" id="upload"><div><h2>Analyze your dataset</h2><p>Choose a CSV file from your computer or try the included sample.</p></div><span class="step-tag">STEP 01 / UPLOAD</span></section>
      <section class="upload-card" id="drop-zone" aria-label="CSV upload area">
        <input type="file" id="csv-file" accept=".csv,text/csv" hidden>
        <div class="upload-icon" aria-hidden="true">↑</div><h3>Drop your CSV file here</h3><p>or <button id="browse-btn" class="text-button" type="button">browse files</button> from your computer</p><div class="file-types">CSV FILES ONLY <span>•</span> MAX 50 MB</div>
        <div id="file-info" class="file-info" hidden></div>
        <div class="upload-actions"><button id="analyze-btn" class="primary-btn" disabled>Analyze dataset <span>→</span></button><button id="sample-btn" class="secondary-btn" type="button">Try sample dataset</button></div>
        <p id="upload-message" class="message" role="status" aria-live="polite">Files are analyzed in memory and are not saved by this application.</p>
      </section>

      <section class="metrics" aria-label="Dataset metrics">
        <article class="metric-card"><div class="metric-top"><span>DATA QUALITY</span><span class="metric-icon">◉</span></div><div class="metric-value" id="quality-value">—<small>/ 100</small></div><p>Weighted score from the quality engine</p></article>
        <article class="metric-card"><div class="metric-top"><span>MISSING VALUES</span><span class="metric-icon">▤</span></div><div class="metric-value" id="missing-value">—</div><p>Empty cells detected</p></article>
        <article class="metric-card"><div class="metric-top"><span>FINDINGS</span><span class="metric-icon">⌁</span></div><div class="metric-value" id="issues-value">—</div><p>Checks that reported an issue</p></article>
      </section>

      <section class="dataset-stats" id="dataset-stats" hidden aria-live="polite"></section>
      <section class="results" id="findings"><div class="section-heading"><div><h2>Analysis findings</h2><p id="result-subtitle">Your findings will appear here after analysis.</p></div><span class="step-tag">STEP 02 / REVIEW</span></div><div class="empty-results" id="empty-results"><div class="empty-icon" aria-hidden="true">⌕</div><strong>Waiting for your first dataset</strong><p>Upload a CSV above to see real analysis results.</p></div><div id="findings-content"></div></section>
      <footer>DataSentinel AI <span>•</span> Data quality, made clearer.</footer>
    </main>
  </div>
`

const fileInput = document.querySelector<HTMLInputElement>('#csv-file')!
const browseBtn = document.querySelector<HTMLButtonElement>('#browse-btn')!
const analyzeBtn = document.querySelector<HTMLButtonElement>('#analyze-btn')!
const sampleBtn = document.querySelector<HTMLButtonElement>('#sample-btn')!
const dropZone = document.querySelector<HTMLElement>('#drop-zone')!
const fileInfo = document.querySelector<HTMLDivElement>('#file-info')!
const message = document.querySelector<HTMLParagraphElement>('#upload-message')!
const healthBadge = document.querySelector<HTMLDivElement>('#health-badge')!
const healthDot = document.querySelector<HTMLDivElement>('#health-dot')!
const healthLabel = document.querySelector<HTMLElement>('#health-label')!
const maxFileSize = 50 * 1024 * 1024
let selectedFile: File | null = null
let loading = false

browseBtn.addEventListener('click', () => fileInput.click())

function selectFile(file?: File) {
  if (!file) return
  if (!file.name.toLowerCase().endsWith('.csv')) {
    message.textContent = 'Please choose a .csv file.'
    message.className = 'message error'
    return
  }
  if (file.size > maxFileSize) {
    message.textContent = 'This file exceeds the 50 MB upload limit.'
    message.className = 'message error'
    return
  }
  selectedFile = file
  fileInfo.hidden = false
  fileInfo.textContent = `📄 ${file.name} · ${formatBytes(file.size)}`
  analyzeBtn.disabled = loading
  message.textContent = 'File selected. Ready to analyze.'
  message.className = 'message'
}

fileInput.addEventListener('change', () => selectFile(fileInput.files?.[0]))
dropZone.addEventListener('dragover', event => {
  event.preventDefault()
  dropZone.classList.add('dragging')
})
dropZone.addEventListener('dragleave', event => {
  if (!dropZone.contains((event as DragEvent).relatedTarget as Node | null)) dropZone.classList.remove('dragging')
})
dropZone.addEventListener('drop', event => {
  event.preventDefault()
  dropZone.classList.remove('dragging')
  selectFile((event as DragEvent).dataTransfer?.files[0])
})

analyzeBtn.addEventListener('click', async () => {
  if (!selectedFile || loading) return
  const form = new FormData()
  form.append('file', selectedFile)
  await runAnalysis(() => fetch(`${apiBase}/api/upload`, { method: 'POST', body: form }), selectedFile.name)
})
sampleBtn.addEventListener('click', async () => {
  if (loading) return
  await runAnalysis(() => fetch(`${apiBase}/api/analyze-sample`, { method: 'POST' }), 'Built-in sample dataset')
})

async function runAnalysis(request: () => Promise<Response>, label: string) {
  clearPreviousResults()
  setLoading(true)
  message.textContent = 'Analyzing dataset with the DataSentinel quality engine…'
  message.className = 'message'
  try {
    const response = await request()
    const payload: unknown = await response.json().catch(() => null)
    if (!response.ok) throw new Error(readApiError(payload, response.status))
    const result = parseAnalysis(payload)
    renderResults(result)
    message.textContent = `Analysis complete for ${label}.`
    message.className = 'message success'
  } catch (error) {
    document.querySelector('#result-subtitle')!.textContent = 'Analysis could not be completed.'
    document.querySelector('#empty-results strong')!.textContent = 'Analysis failed'
    document.querySelector('#empty-results p')!.textContent = 'Check the upload message above, then try again.'
    message.textContent = error instanceof TypeError
      ? 'Could not reach the API. Start the backend and check the connection status above.'
      : error instanceof Error ? error.message : 'Analysis failed. Please try again.'
    message.className = 'message error'
  } finally {
    setLoading(false)
  }
}

function clearPreviousResults() {
  document.querySelector('#quality-value')!.innerHTML = '—<small>/ 100</small>'
  document.querySelector('#missing-value')!.textContent = '—'
  document.querySelector('#issues-value')!.textContent = '—'
  document.querySelector('#result-subtitle')!.textContent = 'Analysis in progress…'
  document.querySelector<HTMLDivElement>('#dataset-stats')!.hidden = true
  document.querySelector<HTMLDivElement>('#findings-content')!.replaceChildren()
  document.querySelector<HTMLDivElement>('#empty-results')!.hidden = false
  document.querySelector('#empty-results strong')!.textContent = 'Analysis in progress'
  document.querySelector('#empty-results p')!.textContent = 'The quality engine is checking this dataset.'
}

function readApiError(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object' && 'detail' in payload && typeof payload.detail === 'string') return payload.detail
  return `The API returned an unexpected response (HTTP ${status}). Please try again.`
}

function parseAnalysis(data: unknown): Analysis {
  if (!data || typeof data !== 'object') throw new Error('The API returned an invalid analysis response.')
  const value = data as Partial<Analysis>
  if (!value.profile || !Array.isArray(value.profile.columns) || !value.quality_score || !Array.isArray(value.findings)
      || typeof value.profile.row_count !== 'number' || typeof value.profile.column_count !== 'number'
      || typeof value.quality_score.overall_score !== 'number' || typeof value.total_issues !== 'number') {
    throw new Error('The API response is missing required analysis fields. Check that the backend is up to date.')
  }
  return value as Analysis
}

function renderResults(data: Analysis) {
  const missing = data.profile.columns.reduce((sum, column) => sum + (column.missing_count ?? column.null_count), 0)
  document.querySelector('#quality-value')!.innerHTML = `${data.quality_score.overall_score.toFixed(1)}<small>/ 100</small>`
  document.querySelector('#missing-value')!.textContent = missing.toLocaleString()
  document.querySelector('#issues-value')!.textContent = data.total_issues.toLocaleString()
  document.querySelector('#result-subtitle')!.textContent = `${data.profile.filename} · Analysis complete`

  const stats = document.querySelector<HTMLDivElement>('#dataset-stats')!
  stats.hidden = false
  stats.replaceChildren()
  const heading = document.createElement('h3')
  heading.textContent = 'Dataset summary'
  const summary = document.createElement('p')
  summary.textContent = `${data.profile.row_count.toLocaleString()} rows · ${data.profile.column_count.toLocaleString()} columns · ${formatBytes(data.profile.memory_usage_mb * 1024 * 1024)} in memory`
  stats.append(heading, summary)
  for (const warning of data.csv_warnings ?? []) {
    const note = document.createElement('p')
    note.className = 'csv-normalization-note'
    note.textContent = warning
    stats.append(note)
  }

  const content = document.querySelector<HTMLDivElement>('#findings-content')!
  content.replaceChildren()
  document.querySelector<HTMLDivElement>('#empty-results')!.hidden = true
  if (!data.findings.length) {
    const clean = document.createElement('div')
    clean.className = 'empty-results clean-results'
    const title = document.createElement('strong')
    title.textContent = 'No findings from the configured checks'
    const description = document.createElement('p')
    description.textContent = 'The quality engine completed its checks and did not report any issues.'
    clean.append(title, description)
    content.append(clean)
    return
  }

  const wrapper = document.createElement('div')
  wrapper.className = 'table-wrap'
  const table = document.createElement('table')
  table.className = 'findings-table'
  table.innerHTML = '<thead><tr><th scope="col">Issue type</th><th scope="col">Column</th><th scope="col">Count</th><th scope="col">Severity</th><th scope="col">Description</th></tr></thead>'
  const tbody = document.createElement('tbody')
  for (const finding of data.findings) {
    const row = document.createElement('tr')
    const type = document.createElement('td')
    type.textContent = finding.title
    const column = document.createElement('td')
    column.textContent = finding.column ?? 'Dataset'
    const count = document.createElement('td')
    count.textContent = finding.count === null ? '—' : finding.count.toLocaleString()
    const severity = document.createElement('td')
    const badge = document.createElement('span')
    badge.className = `severity severity-${finding.severity}`
    badge.textContent = finding.severity
    severity.append(badge)
    const description = document.createElement('td')
    description.textContent = finding.description
    description.title = finding.recommendation
    row.append(type, column, count, severity, description)
    tbody.append(row)
  }
  table.append(tbody)
  wrapper.append(table)
  content.append(wrapper)
}

function setLoading(value: boolean) {
  loading = value
  analyzeBtn.disabled = value || !selectedFile
  sampleBtn.disabled = value
  analyzeBtn.innerHTML = value ? 'Analyzing…' : 'Analyze dataset <span>→</span>'
  sampleBtn.textContent = value ? 'Please wait…' : 'Try sample dataset'
}

function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const index = Math.min(Math.floor(Math.log(bytes) / Math.log(1024)), units.length - 1)
  return `${(bytes / 1024 ** index).toFixed(index === 0 ? 0 : 1)} ${units[index]}`
}

async function checkHealth() {
  try {
    const response = await fetch(`${apiBase}/api/health`, { signal: AbortSignal.timeout(5000), cache: 'no-store' })
    if (!response.ok) throw new Error('unavailable')
    const data: unknown = await response.json()
    if (!data || typeof data !== 'object' || !('status' in data) || data.status !== 'healthy') throw new Error('unavailable')
    healthBadge.innerHTML = '<span></span> API connected'
    healthBadge.classList.add('connected')
    healthDot.classList.remove('offline')
    healthLabel.textContent = 'Ready to analyze'
  } catch {
    healthBadge.innerHTML = '<span></span> API unavailable'
    healthBadge.classList.remove('connected')
    healthDot.classList.add('offline')
    healthLabel.textContent = 'Start the backend server'
  }
}

void checkHealth()
window.setInterval(() => void checkHealth(), 10_000)

