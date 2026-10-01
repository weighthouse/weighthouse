import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import {
  Bookmark,
  Box,
  Download,
  Eye,
  HardDrive,
  LogOut,
  Menu,
  Search,
  Server,
  Settings,
  UploadCloud,
  UserCircle,
  X,
} from 'lucide-react'
import { NavLink, useLocation, useNavigate } from 'react-router-dom'
import { api } from '../api'
import { BRAND } from '../brand.ts'
import { BrandMark } from './BrandMarks'
import type { User } from '../types'

interface ShellProps {
  children: ReactNode
  activeDownloads: number
  staleWatches: number
  user: User
  onLogout: () => void
}

const links = [
  { to: '/models', label: 'Models', icon: Box },
  { to: '/local', label: 'Library', icon: HardDrive },
  { to: '/saved', label: 'Saved', icon: Bookmark },
  { to: '/watched', label: 'Watched', icon: Eye },
  { to: '/uploads', label: 'Uploads', icon: UploadCloud },
  { to: '/downloads', label: 'Downloads', icon: Download },
  { to: '/runtimes', label: 'Runtimes', icon: Server, adminOnly: true },
]

export default function Shell({ children, activeDownloads, staleWatches, user, onLogout }: ShellProps) {
  const navigate = useNavigate()
  const location = useLocation()
  const searchInput = useRef<HTMLInputElement>(null)
  const [query, setQuery] = useState('')
  const [runtimeTargetCount, setRuntimeTargetCount] = useState<number | null>(null)
  const [mobileOpen, setMobileOpen] = useState(false)

  useEffect(() => {
    // Runtime dispatch is a deliberately dormant feature: the nav entry
    // stays hidden until at least one target is configured server-side.
    // An unknown count (health request failed) degrades to showing the
    // admin entry, same as before.
    let ignore = false
    api
      .health()
      .then((payload) => {
        if (!ignore) setRuntimeTargetCount(payload.runtime_target_count ?? 0)
      })
      .catch(() => undefined)
    return () => {
      ignore = true
    }
  }, [])

  // The Explore page owns the query in the URL; the top bar box mirrors
  // it so the field and the results never disagree.
  useEffect(() => {
    if (location.pathname !== '/models') return
    const params = new URLSearchParams(location.search)
    setQuery(params.get('search') || '')
  }, [location])

  useEffect(() => {
    function focusSearch(event: KeyboardEvent) {
      const target = event.target as HTMLElement | null
      const isTyping = target?.matches('input, textarea, select, [contenteditable="true"]')
      if (event.key === '/' && !isTyping) {
        event.preventDefault()
        searchInput.current?.focus()
      }
    }
    window.addEventListener('keydown', focusSearch)
    return () => window.removeEventListener('keydown', focusSearch)
  }, [])

  function search(event: FormEvent) {
    event.preventDefault()
    navigate(`/models?search=${encodeURIComponent(query.trim())}`)
    setMobileOpen(false)
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <NavLink to="/models" className="brand" aria-label={`${BRAND.displayName} home`}>
            <BrandMark className="brand-mark" />
            <span className="brand-name">{BRAND.displayName}</span>
          </NavLink>

          <form className="global-search" onSubmit={search} role="search">
            <Search size={17} aria-hidden="true" />
            <input
              ref={searchInput}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search models on the Hub"
              aria-label="Search models on Hugging Face"
            />
            <kbd>/</kbd>
          </form>

          <nav className="primary-nav" aria-label="Primary navigation">
            {links
              .filter((link) => !link.adminOnly || user.role === 'admin')
              .filter((link) => link.to !== '/runtimes' || (runtimeTargetCount ?? 1) > 0)
              .map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} className={({ isActive }) => (isActive ? 'active' : '')}>
                <Icon size={16} aria-hidden="true" />
                <span>{label}</span>
                {to === '/downloads' && activeDownloads > 0 && (
                  <span className="nav-count">{activeDownloads}</span>
                )}
                {to === '/watched' && staleWatches > 0 && (
                  <span className={`nav-count ${staleWatches >= 4 ? 'nav-count-warn' : ''}`}>
                    {staleWatches}
                  </span>
                )}
              </NavLink>
            ))}
            <NavLink to="/settings" aria-label="Settings">
              <Settings size={17} aria-hidden="true" />
              <span className="desktop-hidden-label">Settings</span>
            </NavLink>
          </nav>

          <div className="account-chip" title={`${user.display_name} (${user.role})`}>
            <UserCircle size={18} />
            <span>{user.display_name}</span>
            <button type="button" onClick={onLogout} aria-label="Sign out" title="Sign out">
              <LogOut size={15} />
            </button>
          </div>
          <button
            type="button"
            className="icon-button mobile-menu-button"
            onClick={() => setMobileOpen(!mobileOpen)}
            aria-label="Toggle navigation"
            aria-expanded={mobileOpen}
          >
            {mobileOpen ? <X size={20} /> : <Menu size={20} />}
          </button>
        </div>
        {mobileOpen && (
          <div className="mobile-panel">
            <form className="mobile-search" onSubmit={search}>
              <Search size={17} />
              <input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder="Search the Hub"
              />
            </form>
            {links
              .filter((link) => !link.adminOnly || user.role === 'admin')
              .filter((link) => link.to !== '/runtimes' || (runtimeTargetCount ?? 1) > 0)
              .map(({ to, label, icon: Icon }) => (
              <NavLink key={to} to={to} onClick={() => setMobileOpen(false)}>
                <Icon size={18} />
                {label}
                {to === '/downloads' && activeDownloads > 0 && (
                  <span className="nav-count">{activeDownloads}</span>
                )}
                {to === '/watched' && staleWatches > 0 && (
                  <span className={`nav-count ${staleWatches >= 4 ? 'nav-count-warn' : ''}`}>
                    {staleWatches}
                  </span>
                )}
              </NavLink>
            ))}
            <NavLink to="/settings" onClick={() => setMobileOpen(false)}>
              <Settings size={18} />
              Settings
            </NavLink>
            <button type="button" className="mobile-account" onClick={onLogout}>
              <LogOut size={18} />
              Sign out {user.display_name}
            </button>
          </div>
        )}
      </header>
      <main>{children}</main>
    </div>
  )
}
