/**
 * WatchlistSkeleton — placeholder for the Watchlist page.
 *
 * Mirrors the two-column layout: sidebar with watchlist items,
 * main area with a symbol table.
 */
import { SkeletonBlock } from "../SkeletonBlock";

function SidebarSkeleton() {
  return (
    <aside className="watchlist-sidebar">
      <div className="skeleton-sidebar-list">
        {Array.from({ length: 4 }).map((_, i) => (
          <div key={i} className="skeleton-sidebar-item">
            <SkeletonBlock width="70%" height="0.9rem" />
            <SkeletonBlock width="40%" height="0.7rem" />
          </div>
        ))}
      </div>
    </aside>
  );
}

function TableSkeleton() {
  return (
    <div className="watchlist-main">
      <div className="watchlist-table-container">
        <table className="watchlist-table">
          <thead>
            <tr>
              <th>Symbol</th>
              <th>Score</th>
              <th>Regime</th>
              <th>Last Updated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: 6 }).map((_, i) => (
              <tr key={i}>
                <td><SkeletonBlock width="60px" height="0.9rem" /></td>
                <td><SkeletonBlock width="100px" height="0.85rem" /></td>
                <td><SkeletonBlock width="80px" height="0.85rem" /></td>
                <td><SkeletonBlock width="120px" height="0.85rem" /></td>
                <td><SkeletonBlock width="60px" height="0.8rem" /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function WatchlistSkeleton() {
  return (
    <div className="watchlist-page">
      <div className="watchlist-header">
        <SkeletonBlock width="180px" height="1.5rem" />
        <SkeletonBlock width="140px" height="2rem" radius={6} />
      </div>
      <div className="watchlist-layout">
        <SidebarSkeleton />
        <TableSkeleton />
      </div>
    </div>
  );
}
