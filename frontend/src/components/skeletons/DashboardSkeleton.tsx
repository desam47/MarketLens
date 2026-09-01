/**
 * DashboardSkeleton — content-shaped placeholder for the Dashboard page.
 *
 * Mirrors the layout of the loaded Dashboard:
 *   - Page title
 *   - Top card grid (Regime, MarketContext, Confluence, Strategy)
 *   - Trends section header + 4 trend cards
 *   - Bottom row (TopMovers + NLSearchBar + TransitionsMini)
 */
import { SkeletonBlock } from "../SkeletonBlock";

function SkeletonCard({ rows = 3 }: { rows?: number }) {
  return (
    <div className="card">
      <SkeletonBlock width="60%" height="1.25rem" />
      <div className="skeleton-rows">
        {Array.from({ length: rows }).map((_, i) => (
          <SkeletonBlock key={i} width={i === rows - 1 ? "70%" : "100%"} height="0.85rem" />
        ))}
      </div>
    </div>
  );
}

function SkeletonTrendCard() {
  return (
    <div className="card trend-card-skeleton">
      <SkeletonBlock width="50%" height="1.1rem" />
      <SkeletonBlock width="35%" height="0.8rem" />
      <div className="skeleton-spark">
        <SkeletonBlock width="100%" height="60px" radius={6} />
      </div>
      <div className="skeleton-rows">
        <SkeletonBlock width="80%" height="0.75rem" />
        <SkeletonBlock width="60%" height="0.75rem" />
      </div>
    </div>
  );
}

export function DashboardSkeleton() {
  return (
    <div className="dashboard-page">
      <div className="dashboard-header">
        <SkeletonBlock width="240px" height="1.75rem" />
        <SkeletonBlock width="160px" height="2.25rem" radius={6} />
      </div>

      <div className="dashboard-grid">
        <SkeletonCard rows={4} />
        <SkeletonCard rows={3} />
        <SkeletonCard rows={5} />
        <SkeletonCard rows={3} />
      </div>

      <div className="dashboard-section">
        <SkeletonBlock width="180px" height="1.25rem" />
        <div className="trend-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <SkeletonTrendCard key={i} />
          ))}
        </div>
      </div>

      <div className="dashboard-bottom-row">
        <div className="card">
          <SkeletonBlock width="50%" height="1.1rem" />
          <div className="skeleton-rows">
            {Array.from({ length: 5 }).map((_, i) => (
              <SkeletonBlock key={i} width="100%" height="0.85rem" />
            ))}
          </div>
        </div>
        <div className="card">
          <SkeletonBlock width="60%" height="1.1rem" />
          <SkeletonBlock width="100%" height="2.5rem" radius={6} />
          <SkeletonBlock width="40%" height="0.75rem" />
        </div>
        <div className="card">
          <SkeletonBlock width="50%" height="1.1rem" />
          <div className="skeleton-rows">
            {Array.from({ length: 3 }).map((_, i) => (
              <SkeletonBlock key={i} width="90%" height="0.75rem" />
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
