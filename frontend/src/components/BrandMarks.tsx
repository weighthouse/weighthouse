/**
 * `BrandMark` is this product's own mark — flat shapes, single color, no
 * outlines — drawn with `currentColor` so it follows the app theme.
 *
 * `MadeByToks` renders the toks.love heart, used only inside the
 * attribution phrase; the heart is toks.love's mark and appears nowhere
 * else in this app.
 */

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" fill="currentColor" className={className} aria-hidden="true" focusable="false">
      <defs>
        <mask id="wh-mark-inline">
          <rect width="64" height="64" fill="#000" />
          <path fill="#fff" d="M32 6.5 L57.4 25.4 V56.5 H6.6 V25.4 Z" />
          <path
            fill="#000"
            d="M10.8 28.7 H53.2 V31.7 H10.8 Z
              M13.5 31.7 L14.25 31.7 L9.0 37.6 L8.35 37.6 Z
              M13.75 31.7 L14.5 31.7 L20.05 37.6 L19.4 37.6 Z
              M50.5 31.7 L51.25 31.7 L55.65 37.6 L54.9 37.6 Z
              M50.2 31.7 L50.95 31.7 L44.65 37.6 L43.9 37.6 Z
              M20.3 37.6 A6.1 6.1 0 0 1 8.1 37.6 Z
              M55.9 37.6 A6.1 6.1 0 0 1 43.7 37.6 Z
              M30.6 31.6 H33.4 V56.9 H30.6 Z"
          />
        </mask>
      </defs>
      <path d="M32 6.5 L57.4 25.4 V56.5 H6.6 V25.4 Z" mask="url(#wh-mark-inline)" />
      <path d="M12.6 33.9 H15.8 L16.8 37.9 H11.6 Z
        M15.2 32.7 A1 1 0 1 0 13.2 32.7 A1 1 0 1 0 15.2 32.7 Z
        M48.2 33.9 H51.4 L52.4 37.9 H47.2 Z
        M50.8 32.7 A1 1 0 1 0 48.8 32.7 A1 1 0 1 0 50.8 32.7 Z" />
    </svg>
  )
}

export function MadeByToks() {
  return (
    <span className="made-by-toks">
      made by toks.
      <svg className="toks-heart" viewBox="0 0 64 64" aria-hidden="true" focusable="false">
        <path
          fill="currentColor"
          d="M32 57C28.8 53.7 9 40 9 23.4 9 14.8 15.8 9 24 9c4.1 0 7.1 1.8 8 5 0.9-3.2 3.9-5 8-5 8.2 0 15 5.8 15 14.4C55 40 35.2 53.7 32 57Z"
        />
      </svg>
    </span>
  )
}
