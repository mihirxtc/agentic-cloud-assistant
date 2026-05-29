import { Component } from 'react'

export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(err) {
    return { error: err }
  }

  render() {
    if (this.state.error) {
      return (
        <div style={{
          minHeight: '100vh', background: 'var(--bg-root)', color: 'var(--text-primary)',
          display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 16,
        }}>
          <div style={{ fontSize: 13, color: 'var(--error)' }}>Something went wrong</div>
          <pre style={{ fontSize: 11, color: 'var(--text-muted)', maxWidth: 600, whiteSpace: 'pre-wrap' }}>
            {this.state.error?.message}
          </pre>
          <button className="aca-btn-ghost small" onClick={() => this.setState({ error: null })}>
            Try again
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
