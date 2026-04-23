import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { api, type BookSchema, type RegenParams, type UUID } from "./client";

export const qk = {
  books: () => ["books"] as const,
  book: (id: UUID) => ["books", id] as const,
  sections: (bookId: UUID) => ["books", bookId, "sections"] as const,
  section: (id: UUID) => ["sections", id] as const,
  job: (id: UUID) => ["jobs", id] as const,
  regen: (id: UUID) => ["regenerations", id] as const,
  providers: () => ["providers"] as const,
  providerKey: (name: string) => ["providers", name, "keys"] as const,
};

export function useBooks() {
  return useQuery({ queryKey: qk.books(), queryFn: api.listBooks });
}

export function useBook(id: UUID | null) {
  return useQuery({
    queryKey: qk.book(id ?? ""),
    queryFn: () => api.getBook(id!),
    enabled: !!id,
  });
}

export function useSections(bookId: UUID | null, opts?: { pollMs?: number }) {
  return useQuery({
    queryKey: qk.sections(bookId ?? ""),
    queryFn: () => api.listSections(bookId!),
    enabled: !!bookId,
    refetchInterval: opts?.pollMs,
    refetchIntervalInBackground: true,
  });
}

export function useSection(id: UUID | null) {
  return useQuery({
    queryKey: qk.section(id ?? ""),
    queryFn: () => api.getSection(id!),
    enabled: !!id,
  });
}

export function useJob(id: UUID | null, opts?: { pollMs?: number }) {
  const interval = opts?.pollMs ?? 1000;
  return useQuery({
    queryKey: qk.job(id ?? ""),
    queryFn: () => api.getJob(id!),
    enabled: !!id,
    refetchInterval: (query) => {
      const job = query.state.data as { status?: string } | undefined;
      if (job?.status === "succeeded" || job?.status === "failed") return false;
      return interval;
    },
    refetchIntervalInBackground: true,
  });
}

export function useRegeneration(id: UUID | null) {
  return useQuery({
    queryKey: qk.regen(id ?? ""),
    queryFn: () => api.getRegeneration(id!),
    enabled: !!id,
  });
}

export function useBookRegenerations(bookId: UUID | null) {
  return useQuery({
    queryKey: [...qk.book(bookId ?? ""), "regenerations"],
    queryFn: () => api.listRegenerations(bookId!),
    enabled: !!bookId,
  });
}

export function useProviders() {
  return useQuery({ queryKey: qk.providers(), queryFn: api.listProviders });
}

function invalidateBook(qc: QueryClient, bookId: UUID) {
  void qc.invalidateQueries({ queryKey: qk.book(bookId) });
  void qc.invalidateQueries({ queryKey: qk.sections(bookId) });
  void qc.invalidateQueries({ queryKey: qk.books() });
}

export function useUploadBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) =>
      api.uploadBook(file, title),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.books() });
    },
  });
}

export function useDeleteBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: UUID) => api.deleteBook(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.books() });
    },
  });
}

export function useAnalyse() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.analyse(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function usePatchSchema() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ bookId, schema }: { bookId: UUID; schema: BookSchema }) =>
      api.patchSchema(bookId, schema),
    onSuccess: (_data, vars) => invalidateBook(qc, vars.bookId),
  });
}

export function useApprove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.approve(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function useReExtractSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sectionId: UUID) => api.reExtractSection(sectionId),
    onSuccess: (data) => {
      void qc.invalidateQueries({ queryKey: qk.sections(data.book_id) });
    },
  });
}

export function useReExtractBook() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (bookId: UUID) => api.reExtractBook(bookId),
    onSuccess: (_data, bookId) => invalidateBook(qc, bookId),
  });
}

export function useRegenerate() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      bookId,
      params,
      sectionIds,
    }: {
      bookId: UUID;
      params: RegenParams;
      sectionIds?: string[] | null;
    }) => api.regenerate(bookId, params, sectionIds),
    onSuccess: (_data, vars) => invalidateBook(qc, vars.bookId),
  });
}

export function useRerunSection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, sectionId, customInstructions }: { regenId: UUID; sectionId: string; customInstructions: string }) =>
      api.rerunSection(regenId, sectionId, customInstructions),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regen(vars.regenId) });
    },
  });
}

export function useSaveRegeneration() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ regenId, confirmedSectionIds }: { regenId: UUID; confirmedSectionIds: string[] }) =>
      api.saveRegeneration(regenId, confirmedSectionIds),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: qk.regen(vars.regenId) });
    },
  });
}

export function useSaveProviderKeys() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ name, keys }: { name: string; keys: Record<string, unknown> }) =>
      api.saveProviderKeys(name, keys),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: qk.providers() });
    },
  });
}
