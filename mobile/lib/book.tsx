/**
 * BookProvider — which portfolio variant the app is currently showing.
 *
 * Two books run off the same signal stream (see fork_books.py): 'control' only
 * suggests swaps, 'autoswap' executes them. Every portfolio screen reads the
 * selection from here so the switch is app-wide rather than per-screen.
 *
 * This is the app's first React context; every screen before it held its own
 * useState and refetched on focus. Kept deliberately small — one string, one
 * setter, plus the server's book list.
 */
import AsyncStorage from '@react-native-async-storage/async-storage';
import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { BookInfo, BookName, fetchBooks } from './api';

const STORAGE_KEY = 'sb_active_book';
const FALLBACK: BookName = 'control';

interface BookState {
  book: BookName;
  setBook: (b: BookName) => void;
  books: BookInfo[];
  /** False until the stored preference has been read — screens should not
   *  fetch before this, or the first request goes out against the wrong book
   *  and is immediately superseded. */
  ready: boolean;
}

const Ctx = createContext<BookState>({
  book: FALLBACK,
  setBook: () => {},
  books: [],
  ready: false,
});

export function BookProvider({ children }: { children: React.ReactNode }) {
  const [book, setBookState] = useState<BookName>(FALLBACK);
  const [books, setBooks] = useState<BookInfo[]>([]);
  const [ready, setReady] = useState(false);

  // Hydrate inside an effect, never at module scope: AsyncStorage's web backend
  // touches `window`, which does not exist during `expo export`'s Node-based
  // static rendering pass (same constraint as configure() in app/_layout.tsx).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const stored = await AsyncStorage.getItem(STORAGE_KEY);
        if (!cancelled && stored) setBookState(stored);
      } catch {
        // Unreadable storage is not fatal — fall back to the control book.
      } finally {
        if (!cancelled) setReady(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Book list is advisory: if it fails the app still works against the default.
  useEffect(() => {
    let cancelled = false;
    fetchBooks()
      .then((res) => {
        if (!cancelled) setBooks(res?.books ?? []);
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, []);

  const setBook = useCallback((b: BookName) => {
    setBookState(b);
    AsyncStorage.setItem(STORAGE_KEY, b).catch(() => {});
  }, []);

  const value = useMemo(() => ({ book, setBook, books, ready }), [book, setBook, books, ready]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useBook() {
  return useContext(Ctx);
}
