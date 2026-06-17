const MCP_ENDPOINT = import.meta.env.VITE_MCP_URL ?? 'http://localhost:8000/mcp/'

class MCPClient {
  constructor(endpoint) {
    this.endpoint     = endpoint
    this._id          = 0 // JSON-RPC request ID counter
    this._sessionId   = null // MCP session ID for server-side session management
    this._initialized = false // whether the MCP server has been initialized (handshake)
    this._initPromise = null // promise for ongoing initialization to avoid duplicate requests
  }

  // if 2nd tool call arrives while 1st is initializing, wait for it to finish instead of sending another initialize
  async _ensureInitialized() {
    if (this._initialized) return
    if (this._initPromise) return this._initPromise
    this._initPromise = this._doInitialize()
    return this._initPromise
  }

  // initialize request server's capabilities and session id, 
  async _doInitialize() {
    try {
      await this._request({
        method: 'initialize',
        params: {
          protocolVersion: '2024-11-05',
          capabilities: {},
          clientInfo: { name: 'aca-web', version: '1.0.0' },
        },
      })
      await this._notify({ method: 'notifications/initialized' }) // indicate server -> client is ready
      this._initialized = true
    } catch (err) {
      this._initialized = false
      this._initPromise = null
      this._sessionId   = null  // if either step fails, state is reset so next call can retry 
      throw err
    }
  }

  async _notify(message) {
    const headers = {
      'Content-Type': 'application/json',
      'Accept':       'application/json, text/event-stream',
    }
    if (this._sessionId) headers['Mcp-Session-Id'] = this._sessionId
    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ jsonrpc: '2.0', ...message }),
    })
    if (!res.ok) throw new Error(`MCP notify failed: ${res.status} ${res.statusText}`)
  }

  async _request(message) {
    const id      = ++this._id
    const headers = {
      'Content-Type': 'application/json',
      'Accept':       'application/json, text/event-stream',
    }
    if (this._sessionId) headers['Mcp-Session-Id'] = this._sessionId

    const res = await fetch(this.endpoint, {
      method: 'POST',
      headers,
      body: JSON.stringify({ jsonrpc: '2.0', id, ...message }),
    })

    // text/event-stream -- calls --> _parseSSE to extract JSON-RPC res from SSE (server sent events) format
    const sid = res.headers.get('mcp-session-id') // after every request, check for new session id in response headers & updates if present
    if (sid) this._sessionId = sid

    const contentType = res.headers.get('content-type') || ''

    let data
    if (contentType.includes('text/event-stream')) {
      data = await this._parseSSE(res, id)
    } else if (!res.ok) {
      this._initialized = false
      this._initPromise = null
      this._sessionId   = null
      throw new Error(`MCP server returned ${res.status} ${res.statusText}`)
    } else {
      data = await res.json()
    }

    if (data.error) throw new Error(data.error.message || JSON.stringify(data.error))
    return data.result
  }

  async _parseSSE(response, targetId) {
    const text  = await response.text()
    const lines = text.split('\n')

    // SSE (Server-Sent Events) format sends data: {json}\n\n lines, to to find id match with req JSON-RPC id for right res 
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue
      try {
        const msg = JSON.parse(line.slice(6))
        if (msg.id === targetId) return msg
        if (msg.result !== undefined || msg.error !== undefined) return msg
      } catch { /* skip malformed */ }
    }
    throw new Error('No matching response in SSE stream')
  }

  // MCP tool result comes as content[0].text, JSON string -> py fun return val
  async callTool(name, args = {}) {
    await this._ensureInitialized()
    try {
      const result = await this._request({
        method: 'tools/call',
        params: { name, arguments: args },
      })
      if (!result?.content?.length) throw new Error(`Tool "${name}" returned empty content`)
      const text = result.content[0].text
      if (result.isError) throw new Error(text)
      try { return JSON.parse(text) } catch { return { text } }
    } catch (err) {
      if (err.message.includes('406') && !this._initialized) {  // Error: session expired, server != sessionid -> re-init & try again 
        await this._ensureInitialized()
        const result = await this._request({
          method: 'tools/call',
          params: { name, arguments: args },
        })
        if (!result?.content?.length) throw new Error(`Tool "${name}" returned empty content`)
        const text = result.content[0].text
        if (result.isError) throw new Error(text)
        try { return JSON.parse(text) } catch { return { text } }
      }
      throw err
    }
  }
}

const mcpClient = new MCPClient(MCP_ENDPOINT)

export function callTool(name, args = {}) {
  return mcpClient.callTool(name, args)
}
