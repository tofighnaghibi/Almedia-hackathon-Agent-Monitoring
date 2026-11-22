import { Component, type ReactNode } from 'react'

// A render error in one view shows a small card instead of unmounting the app (and the globe behind it).
type Props = { name: string; children: ReactNode; quiet?: boolean }
type State = { error: Error | null; key: number }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, key: 0 }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error) {
    console.warn(`[argus] ${this.props.name} failed to render:`, error)
  }

  render() {
    if (!this.state.error) return <div key={this.state.key} className="contents">{this.props.children}</div>
    if (this.props.quiet) return null
    return (
      <div role="alert" className="frost pointer-events-auto rounded-2xl px-5 py-4 text-[16px] text-fg ring-1 ring-line">
        Something went wrong here.
        <span className="ml-3 inline-flex gap-3">
          <button onClick={() => this.setState((s) => ({ error: null, key: s.key + 1 }))} className="text-ok hover:underline">
            Try again
          </button>
          <button onClick={() => window.location.reload()} className="text-fg-2 hover:text-fg">
            Reload
          </button>
        </span>
      </div>
    )
  }
}
