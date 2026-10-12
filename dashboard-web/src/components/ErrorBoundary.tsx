import { Component, type ErrorInfo, type ReactNode } from 'react';

interface Props {
  children: ReactNode;
  /** What failed, in plain words, for example "the report". */
  label: string;
}

interface State {
  error: Error | null;
}

/**
 * Catches a rendering error in one part of the page, so the rest of the app keeps working.
 * Hooks cannot catch render errors, which is why this is the one class component in the app.
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error(`Error in ${this.props.label}:`, error, info.componentStack);
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="notice notice-error boundary" role="alert">
        <p>Something went wrong while showing {this.props.label}. The rest of the page still works.</p>
        <button type="button" onClick={() => this.setState({ error: null })}>Try again</button>
      </div>
    );
  }
}
