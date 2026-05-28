// Folder API client + hooks.
//
// Folders are the V-Studio "book folder" entity: each one groups multiple
// uploaded chapter PDFs. The backend computes the aggregate counts
// (chapters / questions / figures + status breakdown) so the library page
// doesn't have to fan-out per folder.

import { useCallback, useEffect, useState } from 'react';

import { ApiError, req } from './client';

export type Folder = {
  id: string;
  name: string;
  color: string;
  subject: string | null;
  created_at: string;
  updated_at: string;
  chapters: number;
  questions: number;
  figures: number;
  chapters_ready: number;
  chapters_processing: number;
  chapters_queued: number;
};

export type FolderCreate = {
  name: string;
  subject?: string;
  color?: string;
};

// ---------- HTTP ----------

export const listFolders = () => req<Folder[]>('/api/folders');

export const getFolder = (id: string) => req<Folder>(`/api/folders/${id}`);

export const createFolder = (body: FolderCreate) =>
  req<Folder>('/api/folders', { method: 'POST', body: JSON.stringify(body) });

export const deleteFolder = (id: string) =>
  req<void>(`/api/folders/${id}`, { method: 'DELETE' });

// ---------- Hooks ----------

type ListState =
  | { kind: 'loading' }
  | { kind: 'ready'; folders: Folder[] }
  | { kind: 'error'; error: string };

export function useFolders() {
  const [state, setState] = useState<ListState>({ kind: 'loading' });

  const load = useCallback(async () => {
    setState({ kind: 'loading' });
    try {
      const folders = await listFolders();
      setState({ kind: 'ready', folders });
    } catch (err) {
      setState({ kind: 'error', error: explain(err) });
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...state, refetch: load };
}

type OneState =
  | { kind: 'loading' }
  | { kind: 'ready'; folder: Folder }
  | { kind: 'error'; error: string };

export function useFolder(id: string | undefined) {
  const [state, setState] = useState<OneState>({ kind: 'loading' });

  const load = useCallback(async () => {
    if (!id) {
      setState({ kind: 'error', error: 'No folder id in URL' });
      return;
    }
    setState({ kind: 'loading' });
    try {
      const folder = await getFolder(id);
      setState({ kind: 'ready', folder });
    } catch (err) {
      setState({ kind: 'error', error: explain(err) });
    }
  }, [id]);

  useEffect(() => {
    void load();
  }, [load]);

  return { ...state, refetch: load };
}

function explain(err: unknown): string {
  if (err instanceof ApiError) return `Backend ${err.status}: ${err.message}`;
  if (err instanceof Error) return err.message;
  return 'Unknown error';
}
