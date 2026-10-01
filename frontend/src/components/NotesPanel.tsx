import { useEffect, useState } from 'react'
import { Check, CheckCircle2, LoaderCircle, Lock, Pin, Trash2, X } from 'lucide-react'
import { api } from '../api'
import { relativeTime } from '../utils'
import type { ModelNote, User } from '../types'

interface NotesPanelProps {
  repoId: string
  user: User
  onToast: (message: string, tone?: 'success' | 'error') => void
}

// D14: one thread per copy. The author's entries are private until the
// author shares the thread; shared entries carry their author's name;
// the admin sees everything and edits only their own words.
export function NotesPanel({ repoId, user, onToast }: NotesPanelProps) {
  const [notes, setNotes] = useState<ModelNote[]>([])
  const [loaded, setLoaded] = useState(false)
  const [draft, setDraft] = useState('')
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editDraft, setEditDraft] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    let ignore = false
    setNotes([])
    setLoaded(false)
    setDraft('')
    setEditingId(null)
    setError('')
    if (!repoId) return
    api
      .modelNotes(repoId)
      .then((payload) => {
        if (!ignore) {
          setNotes(payload.items)
          setLoaded(true)
        }
      })
      .catch((reason) => {
        if (!ignore) {
          setError(reason instanceof Error ? reason.message : 'Could not read the notes.')
        }
      })
    return () => {
      ignore = true
    }
  }, [repoId])

  async function reload() {
    try {
      setNotes((await api.modelNotes(repoId)).items)
    } catch {
      // The stale list stays on screen; the page shows hard errors elsewhere.
    }
  }

  async function act(work: () => Promise<unknown>, failure: string) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await work()
      await reload()
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : failure)
    } finally {
      setBusy(false)
    }
  }

  async function addNote() {
    const body = draft.trim()
    if (!body) return
    await act(async () => {
      await api.createModelNote(repoId, body)
      setDraft('')
      onToast('Note added (private until you share it).')
    }, 'Could not add the note.')
  }

  async function saveEdit(note: ModelNote) {
    const body = editDraft.trim()
    if (!body) return
    await act(async () => {
      await api.editModelNote(repoId, note.id, body)
      setEditingId(null)
      setEditDraft('')
    }, 'Could not save the note.')
  }

  async function remove(note: ModelNote) {
    if (!window.confirm('Delete this note? This cannot be undone.')) return
    await act(async () => {
      await api.deleteModelNote(repoId, note.id)
    }, 'Could not delete the note.')
  }

  const mine = notes.filter((note) => note.author_id === user.id)
  const threadShared = mine.some((note) => note.shared)

  return (
    <section className="updates-section notes-section">
      <div className="updates-head">
        <div>
          <strong>Notes</strong>
          <p>
            {threadShared
              ? 'Shared with members.'
              : 'Private to your account.'}
          </p>
        </div>
        <span className="notes-count">{notes.length}</span>
      </div>
      {error && <div className="inline-error">{error}</div>}
      {loaded && notes.length === 0 && (
        <p className="notes-empty">No notes yet. Write the first one below.</p>
      )}
      <div className="notes-list">
        {notes.map((note) => {
          const own = note.author_id === user.id
          const editing = editingId === note.id
          return (
            <article key={note.id} className={`note-entry${note.pinned ? ' pinned' : ''}`}>
              <div className="note-entry-meta">
                {note.pinned && (
                  <span className="note-flag" title="Pinned by its author">
                    <Pin size={11} /> pin
                  </span>
                )}
                {note.shared && !own && (
                  <span className="note-flag" title="Shared by its author">
                    <Check size={11} /> shared
                  </span>
                )}
                <strong>{own ? 'You' : note.author_name || note.author_username || 'A member'}</strong>
                <span>{relativeTime(note.created_at)}</span>
                {note.updated_at !== note.created_at && <span title="Edited">(edited)</span>}
              </div>
              {editing ? (
                <div className="note-composer editing">
                  <textarea
                    value={editDraft}
                    onChange={(event) => setEditDraft(event.target.value)}
                    rows={3}
                    autoFocus
                  />
                  <div className="note-composer-actions">
                    <button
                      type="button"
                      className="secondary-button compact"
                      disabled={busy}
                      onClick={() => saveEdit(note)}
                    >
                      <Check size={14} /> Save
                    </button>
                    <button
                      type="button"
                      className="secondary-button compact"
                      disabled={busy}
                      onClick={() => {
                        setEditingId(null)
                        setEditDraft('')
                      }}
                    >
                      <X size={14} /> Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <p className="note-entry-body">{note.body}</p>
              )}
              {own && !editing && (
                <div className="note-actions">
                  <button
                    type="button"
                    className="note-action"
                    disabled={busy}
                    onClick={() => act(() => api.pinModelNote(repoId, note.id, !note.pinned), 'Could not change the pin.')}
                    title={note.pinned ? 'Release this pin' : 'Pin this note (releases your other pin on this model)'}
                    aria-pressed={note.pinned}
                  >
                    <Pin size={12} /> {note.pinned ? 'Unpin' : 'Pin'}
                  </button>
                  <button
                    type="button"
                    className={`note-action${note.shared ? ' on' : ''}`}
                    disabled={busy}
                    onClick={() => act(() => api.shareModelNotes(repoId, note.id, !note.shared), 'Could not change sharing.')}
                    title={note.shared ? 'Unshare your whole thread on this model' : 'Share your whole thread with members'}
                    aria-pressed={note.shared}
                  >
                    {note.shared ? <CheckCircle2 size={12} /> : <Lock size={12} />}
                    {note.shared ? 'Shared' : 'Share'}
                  </button>
                  <button
                    type="button"
                    className="note-action"
                    disabled={busy}
                    onClick={() => {
                      setEditingId(note.id)
                      setEditDraft(note.body)
                    }}
                  >
                    Edit
                  </button>
                  <button
                    type="button"
                    className="note-action danger"
                    disabled={busy}
                    onClick={() => remove(note)}
                  >
                    <Trash2 size={12} /> Delete
                  </button>
                </div>
              )}
            </article>
          )
        })}
      </div>
      <div className="note-composer">
        <textarea
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          rows={2}
          placeholder="Write a note about this model…"
        />
        <div className="note-composer-actions">
          <button
            type="button"
            className="secondary-button compact"
            disabled={busy || !draft.trim()}
            onClick={addNote}
          >
            {busy ? <LoaderCircle size={14} className="spin" /> : <Check size={14} />} Add note
          </button>
        </div>
      </div>
    </section>
  )
}
