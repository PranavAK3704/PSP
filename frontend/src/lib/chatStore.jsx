import React, { createContext, useContext, useState, useEffect, useCallback } from "react";

// Multi-conversation store (Claude-style). Per captain: a list of conversations + which is
// active. Each conversation has its own backend conversation_id and message history. Lifted
// above the state switcher and persisted to localStorage, so chats survive state switches AND
// reloads, and a captain can keep several conversations going at once.
//
// Shape (localStorage key "valmo.chat.v2"):
//   { "<captainId>": { conversations: [{ id, convId, title, messages, createdAt, updatedAt }],
//                      activeId: "<conv id>" } }
const KEY = "valmo.chat.v2";
const V1 = "valmo.chat.v1";
const ChatCtx = createContext(null);
const EMPTY_CONV = { id: null, convId: null, title: "New chat", messages: [] };

const rid = () => (crypto.randomUUID ? crypto.randomUUID() : "c" + Math.random().toString(36).slice(2));
const newConv = () => ({ id: rid(), convId: null, title: "New chat", messages: [], createdAt: Date.now(), updatedAt: Date.now() });

function load() {
  try {
    const v2 = JSON.parse(localStorage.getItem(KEY) || "null");
    if (v2) return v2;
    // migrate v1 (one thread per captain) → v2 (one conversation per captain)
    const v1 = JSON.parse(localStorage.getItem(V1) || "null");
    if (v1 && typeof v1 === "object") {
      const out = {};
      for (const [cid, t] of Object.entries(v1)) {
        const c = { ...newConv(), convId: t.convId || null, messages: t.messages || [] };
        c.title = firstTitle(c.messages);
        out[cid] = { conversations: [c], activeId: c.id };
      }
      return out;
    }
  } catch { /* fall through */ }
  return {};
}

function firstTitle(messages) {
  const firstUser = (messages || []).find((m) => m.who === "captain");
  const t = (firstUser?.text || "").trim();
  return t ? (t.length > 32 ? t.slice(0, 32) + "…" : t) : "New chat";
}

export function ChatStoreProvider({ children }) {
  const [threads, setThreads] = useState(load);

  useEffect(() => {
    try { localStorage.setItem(KEY, JSON.stringify(threads)); } catch { /* quota — ignore */ }
  }, [threads]);

  // Guarantee a captain has at least one conversation and an activeId.
  const ensure = useCallback((cid) => {
    setThreads((t) => {
      const cur = t[cid];
      if (cur && cur.conversations?.length && cur.activeId) return t;
      const c = newConv();
      return { ...t, [cid]: { conversations: [c], activeId: c.id } };
    });
  }, []);

  const getConversations = useCallback((cid) => {
    const list = threads[cid]?.conversations || [];
    return [...list].sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
  }, [threads]);

  const getActive = useCallback((cid) => {
    const t = threads[cid];
    if (!t || !t.conversations?.length) return EMPTY_CONV;
    return t.conversations.find((c) => c.id === t.activeId) || t.conversations[0];
  }, [threads]);

  // mutate the active conversation of a captain
  const _mutActive = (t, cid, fn) => {
    const cur = t[cid];
    if (!cur || !cur.conversations?.length) {
      const c = fn(newConv());
      return { ...t, [cid]: { conversations: [c], activeId: c.id } };
    }
    const conversations = cur.conversations.map((c) =>
      c.id === (cur.activeId || cur.conversations[0].id) ? { ...fn(c), updatedAt: Date.now() } : c);
    return { ...t, [cid]: { ...cur, conversations } };
  };

  const setMessages = useCallback((cid, updater) => {
    setThreads((t) => _mutActive(t, cid, (c) => {
      const messages = typeof updater === "function" ? updater(c.messages) : updater;
      const title = c.title && c.title !== "New chat" ? c.title : firstTitle(messages);
      return { ...c, messages, title };
    }));
  }, []);

  const setConvId = useCallback((cid, convId) => {
    setThreads((t) => _mutActive(t, cid, (c) => ({ ...c, convId })));
  }, []);

  const newConversation = useCallback((cid) => {
    setThreads((t) => {
      const cur = t[cid] || { conversations: [] };
      const c = newConv();
      return { ...t, [cid]: { conversations: [c, ...cur.conversations], activeId: c.id } };
    });
  }, []);

  const switchConversation = useCallback((cid, id) => {
    setThreads((t) => (t[cid] ? { ...t, [cid]: { ...t[cid], activeId: id } } : t));
  }, []);

  // Delete a conversation thread from the captain's active panel. The underlying CASE
  // (if any) persists in the Concern Log / "My Cases" — deleting the chat never loses a case.
  const deleteConversation = useCallback((cid, id) => {
    setThreads((t) => {
      const cur = t[cid];
      if (!cur) return t;
      const conversations = cur.conversations.filter((c) => c.id !== id);
      if (conversations.length === 0) {
        const c = newConv();
        return { ...t, [cid]: { conversations: [c], activeId: c.id } };
      }
      const activeId = cur.activeId === id ? conversations[0].id : cur.activeId;
      return { ...t, [cid]: { conversations, activeId } };
    });
  }, []);

  return (
    <ChatCtx.Provider value={{ ensure, getConversations, getActive, setMessages, setConvId, newConversation, switchConversation, deleteConversation }}>
      {children}
    </ChatCtx.Provider>
  );
}

export function useChatStore() {
  const c = useContext(ChatCtx);
  if (!c) throw new Error("useChatStore must be used inside ChatStoreProvider");
  return c;
}
