/**
 * ScannerTableSkeleton — placeholder for the Scanner page table section.
 *
 * Mirrors the live-scanner layout: stats bar at the top,
 * then a full table with shimmer rows.
 */
import { SkeletonBlock } from "../SkeletonBlock";

function ScannerStatsSkeleton() {
  return (
    <div className="scanner-stats">
      {Array.from({ length: 5 }).map((_, i) => (
        <div key={i} className="scanner-stat-item">
          <SkeletonBlock width="50px" height="1.1rem" />
          <SkeletonBlock width="30px" height="0.7rem" />
        </div>
      ))}
    </div>
  );
}

function ScannerTableBody() {
  return (
    <div className="scanner-table-wrapper">
      <table className="scanner-table">
        <thead>
          <tr>
            <th>Symbol</th>
            <th>Price</th>
            <th>Score</th>
            <th>Signals</th>
            <th>Last Update</th>
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: 8 }).map((_, i) => (
            <tr key={i}>
              <td><SkeletonBlock width="55px" height="0.9rem" /></td>
              <td><SkeletonBlock width="70px" height="0.85rem" /></td>
              <td><SkeletonBlock width="45px" height="0.85rem" /></td>
              <td>
                <div style={{ display: "flex", gap: "4px" }}>
                  <SkeletonBlock width="40px" height="0.75rem" radius={8} />
                  <SkeletonBlock width="40px" height="0.75rem" radius={8} />
                </div>
              </td>
              <td><SkeletonBlock width="80px" height="0.85rem" /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ScannerTableSkeleton() {
  return (
    <>
      <ScannerStatsSkeleton />
      <ScannerTableBody />
    </>
  );
}
