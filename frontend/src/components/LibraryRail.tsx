import { taskLabel } from '../utils'
import type { FacetBucket, LibraryFacets } from '../types'
import {
  hasActiveFacets,
  type BooleanFacet,
  type LibraryQuery,
  type ValueFacet,
} from '../libraryQuery'

interface LibraryRailProps {
  facets: LibraryFacets | null
  active: LibraryQuery
  onToggle: (key: BooleanFacet) => void
  onPick: (key: ValueFacet, value: string) => void
  onClear: () => void
}

interface FacetGroupProps {
  label: string
  buckets: FacetBucket[]
  selected: string
  facetKey: ValueFacet
  onPick: (key: ValueFacet, value: string) => void
}

// The facet rail (D16): computed by the backend from the actual library
// — favorited and watching are the only built-ins; every other group is
// a value that exists on disk right now, with its live count. Counts
// follow the search text, never the filters themselves.
function FacetGroup({ label, buckets, selected, facetKey, onPick }: FacetGroupProps) {
  if (buckets.length === 0) return null
  return (
    <div className="rail-group">
      <h4>{label}</h4>
      {buckets.map((bucket) => (
        <button
          key={bucket.value}
          type="button"
          className={`rail-facet${selected === bucket.value ? ' on' : ''}`}
          aria-pressed={selected === bucket.value}
          onClick={() => onPick(facetKey, bucket.value)}
          title={facetKey === 'task' ? taskLabel(bucket.value) : bucket.value}
        >
          <span className="rail-value">{facetKey === 'task' ? taskLabel(bucket.value) : bucket.value}</span>
          <span className="count">{bucket.count}</span>
        </button>
      ))}
    </div>
  )
}

export function LibraryRail({ facets, active, onToggle, onPick, onClear }: LibraryRailProps) {
  return (
    <aside className="library-rail" aria-label="Library filters">
      <div className="rail-group">
        <h4>Filters</h4>
        <button
          type="button"
          className={`rail-facet${active.favorited ? ' on' : ''}`}
          aria-pressed={active.favorited}
          onClick={() => onToggle('favorited')}
        >
          <span className="rail-value">Favorited</span>
          <span className="count">{facets ? facets.favorited : 0}</span>
        </button>
        <button
          type="button"
          className={`rail-facet${active.watching ? ' on' : ''}`}
          aria-pressed={active.watching}
          onClick={() => onToggle('watching')}
        >
          <span className="rail-value">Watching</span>
          <span className="count">{facets ? facets.watching : 0}</span>
        </button>
      </div>
      {facets && (
        <>
          <FacetGroup
            label="Task"
            buckets={facets.tasks}
            selected={active.task}
            facetKey="task"
            onPick={onPick}
          />
          <FacetGroup
            label="Library"
            buckets={facets.libraries}
            selected={active.library}
            facetKey="library"
            onPick={onPick}
          />
          <FacetGroup
            label="Location"
            buckets={facets.locations}
            selected={active.location}
            facetKey="location"
            onPick={onPick}
          />
          <FacetGroup
            label="Storage"
            buckets={facets.storage}
            selected={active.storage}
            facetKey="storage"
            onPick={onPick}
          />
        </>
      )}
      {hasActiveFacets(active) && (
        <button type="button" className="rail-clear" onClick={onClear}>
          Clear filters
        </button>
      )}
    </aside>
  )
}
