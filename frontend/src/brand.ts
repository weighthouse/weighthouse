/**
 * Single source of product identity for the frontend.
 *
 * Every user-visible occurrence of the product's name lives here or in
 * `backend/app/brand.py`; a cross-language consistency test asserts the two
 * modules agree. The manifest filename is part of the durable library
 * contract and stays brand-neutral across rebrands.
 *
 * Import this module with an explicit `.ts` extension: the node-based test
 * runner resolves real files, not bundler paths.
 */
const slug = 'modelharbor'

export const BRAND = {
  displayName: 'ModelHarbor',
  slug,
  description:
    'Self-hosted model library: browse, acquire, track, and place model weights.',
  manifestFilename: '.modelmanifest.json',
  legacyManifestFilename: '.hugginghack.json',
  markPath: '/brand-mark.svg',
  unauthorizedEvent: `${slug}:unauthorized`,
} as const
