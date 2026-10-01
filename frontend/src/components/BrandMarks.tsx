/**
 * Brand marks.
 *
 * `BrandMark` is this product's own mark — a flat sailboat drawn from the
 * same geometric language as the toks.love house style (flat shapes, single
 * color, no outlines). It is deliberately *not* a toks.love mark; it uses
 * currentColor so it follows the app theme automatically.
 *
 * `MadeByToks` is the toks.love heart, used strictly inside the attribution
 * phrase "made by toks.<heart>" (the clean heart — the slash version was
 * maintenance-page signage). Per the brand charter, the heart belongs to
 * toks.love and appears nowhere else in this app.
 */

export function BrandMark({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" fill="currentColor" className={className} aria-hidden="true" focusable="false">
      {/* hull: circle segment */}
      <path d="M 17 36 L 49 36 A 16 16 0 0 1 17 36 Z" />
      {/* sail */}
      <path d="M 34 8 L 52 29 L 34 29 Z" />
      {/* mast */}
      <rect x="32.5" y="8" width="3" height="21" rx="1.5" />
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
