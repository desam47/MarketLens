import { render, screen } from '@testing-library/react';
import { SkeletonBlock } from './SkeletonBlock';

describe('SkeletonBlock', () => {
  it('renders with the default width and height', () => {
    const { container } = render(<SkeletonBlock />);
    const el = container.firstChild as HTMLElement;
    expect(el).toBeInTheDocument();
    expect(el).toHaveStyle({ width: '100%', height: '1rem', borderRadius: '4px' });
  });

  it('applies custom width, height, and radius', () => {
    const { container } = render(
      <SkeletonBlock width="50%" height="2rem" radius={8} />
    );
    const el = container.firstChild as HTMLElement;
    expect(el).toHaveStyle({ width: '50%', height: '2rem', borderRadius: '8px' });
  });

  it('merges className', () => {
    const { container } = render(<SkeletonBlock className="my-class" />);
    expect(container.firstChild).toHaveClass('skeleton-block');
    expect(container.firstChild).toHaveClass('my-class');
  });

  it('renders without crashing in a WatchlistSkeleton', () => {
    const { WatchlistSkeleton } = require('./skeletons/WatchlistSkeleton');
    expect(() => render(<WatchlistSkeleton />)).not.toThrow();
  });
});
