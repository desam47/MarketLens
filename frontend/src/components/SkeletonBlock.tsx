/**
 * SkeletonBlock — primitive shimmer placeholder block.
 *
 * Props:
 *   width   — CSS width (default "100%")
 *   height  — CSS height (default "1rem")
 *   radius  — border-radius in px (default 4)
 *   className — additional class names
 */
interface SkeletonBlockProps {
  width?: string;
  height?: string;
  radius?: number;
  className?: string;
}

export function SkeletonBlock({
  width = "100%",
  height = "1rem",
  radius = 4,
  className = "",
}: SkeletonBlockProps) {
  return (
    <div
      className={`skeleton-block ${className}`.trim()}
      style={{ width, height, borderRadius: radius }}
    />
  );
}
