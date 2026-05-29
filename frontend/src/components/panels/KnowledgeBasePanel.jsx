import { useState, useEffect } from 'react'
import { callTool } from '../../api/mcpClient'
import { useApiKeys } from '../../contexts/ApiKeyContext'
import { RESOURCE_TYPES } from '../../utils/constants'

function KBQuery() {
  const { keys }                        = useApiKeys()
  const [q,          setQ]          = useState('')
  const [rtype,      setRtype]      = useState('All')
  const [loading,    setLoading]    = useState(false)
  const [res,        setRes]        = useState(null)
  const [showChunks, setShowChunks] = useState(false)

  const search = async () => {
    if (!q.trim() || loading) return
    setLoading(true)
    try {
      const activeKey = keys.model === 'anthropic' ? keys.anthropic_key
                      : keys.model === 'ollama'    ? keys.ollama_url
                      : keys.groq_key
      const r = await callTool('rag_query_tool', {
        question:      q,
        n_results:     3,
        model:         keys.model || 'groq',
        api_key:       activeKey,
        resource_type: rtype === 'All' ? undefined : rtype,
      })
      setRes(r)
    } finally { setLoading(false) }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
      <div className="aca-row" style={{ gap: 8 }}>
        <input className="aca-input" placeholder="Ask the knowledge base…"
          value={q} onChange={(e) => setQ(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter') search() }} />
        <select className="aca-select" value={rtype} onChange={(e) => setRtype(e.target.value)} style={{ width: 140 }}>
          {RESOURCE_TYPES.map((t) => <option key={t} value={t}>{t}</option>)}
        </select>
        <button className="aca-btn-primary" onClick={search} disabled={loading || !q.trim()} style={{ whiteSpace: 'nowrap' }}>
          {loading ? 'Searching…' : 'Search'}
        </button>
      </div>
      {loading && <div className="aca-skel" style={{ height: 120 }} />}
      {res && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
          <div className="aca-summary">{res.answer}</div>
          <div className="aca-row" style={{ gap: 6, flexWrap: 'wrap' }}>
            {res.sources.map((s) => <span key={s} className="aca-source-chip">📄 {s}</span>)}
          </div>
          <div style={{ color: 'var(--text-muted)', fontSize: 11 }} className="mono">
            {res.chunks_used} chunks retrieved
          </div>
          <div>
            <button className="aca-btn-ghost small" onClick={() => setShowChunks((v) => !v)}>
              {showChunks ? '▾' : '▸'} {res.raw_chunks?.length} retrieved chunks
            </button>
          </div>
          {showChunks && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {res.raw_chunks?.map((c, i) => {
                const score = c.relevance_score ?? 0
                const rel = score >= 0.7 ? 'success' : score >= 0.4 ? 'warning' : 'error'
                return (
                  <div key={i} style={{
                    border: '1px solid var(--border)', borderRadius: 6,
                    padding: 12, background: 'var(--bg-elevated)',
                  }}>
                    <div className="aca-row" style={{ gap: 8, marginBottom: 6 }}>
                      <span className={'aca-badge ' + rel}>{score.toFixed(2)}</span>
                      <span className="mono" style={{ color: 'var(--text-primary)', fontSize: 11 }}>{c.metadata?.doc_id}</span>
                      <span className="mono" style={{ color: 'var(--text-muted)', fontSize: 11 }}>#{c.metadata?.chunk_index}</span>
                    </div>
                    <div style={{ color: 'var(--text-secondary)', fontSize: 12, lineHeight: 1.55 }}>
                      {c.text.length > 300 ? c.text.slice(0, 300) + '…' : c.text}
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function KBManage() {
  const [docs,        setDocs]        = useState([])
  const [docId,       setDocId]       = useState('')
  const [text,        setText]        = useState('')
  const [rtype,       setRtype]       = useState('General')
  const [uploadId,    setUploadId]    = useState('')
  const [uploadType,  setUploadType]  = useState('General')
  const [fileName,    setFileName]    = useState('')
  const [fileObj,     setFileObj]     = useState(null)
  const [uploading,   setUploading]   = useState(false)
  const [uploadError, setUploadError] = useState('')
  const [adding,      setAdding]      = useState(false)
  const [addError,    setAddError]    = useState('')

  const refresh = async () => {
    const r = await callTool('rag_list_documents')
    setDocs(r.documents || [])
  }

  useEffect(() => { refresh() }, []) // eslint-disable-line

  const add = async () => {
    if (!docId || !text) return
    setAdding(true)
    setAddError('')
    try {
      await callTool('rag_add_text_document', { doc_id: docId, text, resource_type: rtype })
      await refresh()
      setDocId(''); setText('')
    } catch (err) {
      setAddError(err.message || 'Failed to add document')
    } finally {
      setAdding(false)
    }
  }

  const upload = async () => {
    if (!fileObj || !uploadId) return
    setUploading(true)
    setUploadError('')
    try {
      await new Promise((resolve, reject) => {
        const reader = new FileReader()
        reader.onload = async (e) => {
          try {
            const base64 = e.target.result.split(',')[1]
            await callTool('rag_upload_file', {
              doc_id:              uploadId,
              file_content_base64: base64,
              filename:            fileObj.name,
              resource_type:       uploadType,
            })
            await refresh()
            setUploadId(''); setFileName(''); setFileObj(null)
            resolve()
          } catch (err) {
            reject(err)
          }
        }
        reader.onerror = () => reject(new Error('Failed to read file'))
        reader.readAsDataURL(fileObj)
      })
    } catch (err) {
      setUploadError(err.message || 'Upload failed')
    } finally {
      setUploading(false)
    }
  }

  const del = async (id) => {
    try {
      await callTool('rag_delete_document', { doc_id: id })
      setDocs((d) => d.filter((x) => x.doc_id !== id))
    } catch (err) {
      alert(`Delete failed: ${err.message || 'Unknown error'}`)
    }
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
        <div>
          <div className="aca-panel-title" style={{ marginBottom: 8 }}>Upload file</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <label className="aca-btn-ghost small" style={{ alignSelf: 'flex-start', cursor: 'pointer' }}>
              <input type="file" style={{ display: 'none' }} onChange={(e) => {
                const f = e.target.files[0]
                if (f) { setFileObj(f); setFileName(f.name) }
              }} />
              {fileName || 'Choose file…'}
            </label>
            <input className="aca-input" placeholder="Doc ID"
              value={uploadId} onChange={(e) => setUploadId(e.target.value)} />
            <select className="aca-select" value={uploadType} onChange={(e) => setUploadType(e.target.value)}>
              {RESOURCE_TYPES.filter((t) => t !== 'All').map((t) => <option key={t}>{t}</option>)}
            </select>
            <button className="aca-btn-primary" onClick={upload} disabled={!fileName || !uploadId || uploading}>
              {uploading ? 'Uploading…' : 'Upload'}
            </button>
            {uploadError && (
              <div style={{ color: 'var(--error, #ff4444)', fontSize: 12 }}>{uploadError}</div>
            )}
          </div>
        </div>
        <div>
          <div className="aca-panel-title" style={{ marginBottom: 8 }}>Paste text</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <input className="aca-input" placeholder="Doc ID"
              value={docId} onChange={(e) => setDocId(e.target.value)} />
            <textarea className="aca-textarea" rows={8}
              placeholder="Paste markdown or plain text…"
              value={text} onChange={(e) => setText(e.target.value)} />
            <select className="aca-select" value={rtype} onChange={(e) => setRtype(e.target.value)}>
              {RESOURCE_TYPES.filter((t) => t !== 'All').map((t) => <option key={t}>{t}</option>)}
            </select>
            <button className="aca-btn-primary" onClick={add} disabled={!docId || !text || adding}>
              {adding ? 'Adding…' : 'Add'}
            </button>
            {addError && (
              <div style={{ color: 'var(--error, #ff4444)', fontSize: 12 }}>{addError}</div>
            )}
          </div>
        </div>
      </div>
      <div>
        <div className="aca-row" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
          <div className="aca-panel-title">Current documents</div>
          <button className="aca-btn-ghost small" onClick={refresh}>Refresh</button>
        </div>
        {docs.length === 0 ? (
          <div className="aca-empty">No documents indexed yet</div>
        ) : (
          <table className="aca-table">
            <thead>
              <tr>
                <th>Doc ID</th>
                <th style={{ width: 100 }}>Type</th>
                <th style={{ width: 80 }}>Chunks</th>
                <th style={{ width: 60 }}></th>
              </tr>
            </thead>
            <tbody>
              {docs.map((d) => (
                <tr key={d.doc_id}>
                  <td className="mono" style={{ fontSize: 11, color: 'var(--text-primary)' }}>{d.doc_id}</td>
                  <td><span className="aca-badge muted">{d.resource_type}</span></td>
                  <td className="mono" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>{d.chunk_count}</td>
                  <td>
                    <button className="aca-btn-ghost small danger" onClick={() => del(d.doc_id)}>×</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

export default function KnowledgeBasePanel() {
  const [tab, setTab] = useState('QUERY')
  return (
    <section className="aca-panel" style={{ gridColumn: 'span 12' }}>
      <div className="aca-panel-hd">
        <span className="aca-panel-title">Knowledge Base</span>
        <span className="aca-badge muted">{tab}</span>
      </div>
      <div style={{ padding: '0 20px' }}>
        <div className="aca-tab-bar">
          <button className="aca-tab" data-active={tab === 'QUERY'} onClick={() => setTab('QUERY')}>Query</button>
          <button className="aca-tab" data-active={tab === 'MANAGE'} onClick={() => setTab('MANAGE')}>Manage</button>
        </div>
      </div>
      <div className="aca-panel-body">
        {tab === 'QUERY' ? <KBQuery /> : <KBManage />}
      </div>
    </section>
  )
}
