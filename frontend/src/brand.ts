/**
 * Single source of product identity for the frontend.
 *
 * `backend/app/brand.py` agrees with this file (a consistency test
 * enforces it), and the slug appears in no other module (a boundary
 * test enforces that).
 *
 * Import this module with an explicit `.ts` extension: the node-based test
 * runner resolves real files, not bundler paths.
 */
const slug = 'weighthouse'

export const BRAND = {
  displayName: 'Weighthouse',
  slug,
  description:
    'Self-hosted model library: browse, acquire, track, and place model weights.',
  markPath: '/brand-mark.svg',
  unauthorizedEvent: `${slug}:unauthorized`,
} as const
