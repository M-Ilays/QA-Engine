import React, { useState, useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import './ChatInterface.css';

/* ─── Types ─────────────────────────────────────────────────── */

interface Message {
  id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  timestamp: string;
  runId?: string;
}

interface Conversation {
  id: string;
  title: string;
  target_url?: string;
  updated_at: string;
}

interface ConversationDetail {
  id: string;
  title: string;
  status: string;
  messages: Message[];
  context: any;
}

interface ChatSettings {
  targetUrl: string;
  testingObjective: string;
  username: string;
  password: string;
  allowControlledWrites: boolean;
  allowTestDataCreation: boolean;
  allowDeletion: boolean;
  headlessMode: boolean;
  safeMode: boolean;
  maxActions: string;
  /** Which test case types to generate & execute. Default = ['positive'] (happy path only). */
  testCaseTypes: string[];
}

const DEFAULT_SETTINGS: ChatSettings = {
  targetUrl: 'https://thinking-tester-contact-list.herokuapp.com/',
  testingObjective: '',
  username: '',
  password: '',
  allowControlledWrites: false,
  allowTestDataCreation: false,
  allowDeletion: false,
  headlessMode: true,
  safeMode: true,
  maxActions: '',
  testCaseTypes: ['positive'],
};

const SETTINGS_KEY = 'gemmaqa_chat_settings';

function loadSettings(): ChatSettings {
  try {
    const stored = localStorage.getItem(SETTINGS_KEY);
    return stored ? { ...DEFAULT_SETTINGS, ...JSON.parse(stored) } : DEFAULT_SETTINGS;
  } catch { return DEFAULT_SETTINGS; }
}

/* ─── Component ─────────────────────────────────────────────── */

export const ChatInterface: React.FC = () => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversationId, setActiveConversationId] = useState<string | null>(null);
  const [activeConversation, setActiveConversation] = useState<ConversationDetail | null>(null);
  const [inputMessage, setInputMessage] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const [streamingContent, setStreamingContent] = useState('');
  const [thinkingText, setThinkingText] = useState('');
  const [showSettings, setShowSettings] = useState(false);
  const [settings, setSettings] = useState<ChatSettings>(loadSettings);
  const [settingsSaved, setSettingsSaved] = useState(false);

  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => { loadConversations(); }, []);
  useEffect(() => { scrollToBottom(); }, [activeConversation?.messages, streamingContent]);

  const scrollToBottom = () =>
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });

  const loadConversations = async () => {
    try {
      const r = await fetch('/api/chat/conversations');
      const d = await r.json();
      setConversations(d.conversations || []);
    } catch (e) { console.error(e); }
  };

  const loadConversation = async (id: string) => {
    try {
      const r = await fetch(`/api/chat/conversations/${id}`);
      const d = await r.json();
      setActiveConversation(d);
      setActiveConversationId(id);
    } catch (e) { console.error(e); }
  };

  const createConversation = async () => {
    try {
      const r = await fetch('/api/chat/conversations', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: 'New Chat' }),
      });
      const d = await r.json();
      await loadConversations();
      await loadConversation(d.id);
      setShowSettings(false);
      setTimeout(() => textareaRef.current?.focus(), 100);
    } catch (e) { console.error(e); }
  };

  const saveSettings = () => {
    localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings));
    setSettingsSaved(true);
    setTimeout(() => setSettingsSaved(false), 2000);
  };

  const updateSetting = <K extends keyof ChatSettings>(key: K, value: ChatSettings[K]) =>
    setSettings(prev => ({ ...prev, [key]: value }));

  const sendMessage = async () => {
    if (!activeConversationId || !inputMessage.trim() || isStreaming) return;

    const userMessage = inputMessage.trim();
    setInputMessage('');
    setIsStreaming(true);
    setStreamingContent('');
    setThinkingText('');

    // Optimistically add user message
    const tempMsg: Message = {
      id: 'tmp-' + Date.now(), role: 'user', content: userMessage,
      timestamp: new Date().toISOString(),
    };
    setActiveConversation(prev =>
      prev ? { ...prev, messages: [...prev.messages, tempMsg] } : null
    );

    try {
      const resp = await fetch(
        `/api/chat/conversations/${activeConversationId}/messages`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          // Include current settings so backend can use them
          body: JSON.stringify({ message: userMessage, settings }),
        }
      );
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);

      const reader = resp.body?.getReader();
      const decoder = new TextDecoder();
      if (!reader) throw new Error('No body');

      let accumulated = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        const lines = decoder.decode(value, { stream: true }).split('\n');
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const str = line.slice(6).trim();
          if (!str) continue;
          try {
            const data = JSON.parse(str);
            if (data.type === 'thinking') { setThinkingText(data.content); }
            else if (data.type === 'response') {
              accumulated += data.content;
              setStreamingContent(accumulated);
              setThinkingText('');
            }
            else if (data.type === 'action' && data.action === 'start_run') {
              window.open(`/runs/${data.run_id}`, '_blank');
            }
            else if (data.type === 'complete') {
              setIsStreaming(false);
              setStreamingContent('');
              setThinkingText('');
              await loadConversation(activeConversationId);
              await loadConversations();
            }
            else if (data.type === 'error') {
              accumulated += `\n❌ ${data.content}`;
              setStreamingContent(accumulated);
            }
          } catch { /* ignore malformed */ }
        }
      }
    } catch (e) {
      setStreamingContent(`❌ Failed to send: ${e}`);
    } finally {
      setIsStreaming(false);
      setTimeout(() => textareaRef.current?.focus(), 50);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
  };

  /* ─── Render ─────────────────────────────────────────────── */
  return (
    <div className="chat-page">
      {/* ── Header ── */}
      <header className="chat-header-bar">
        <Link to="/" className="chat-back-btn">← Back</Link>
        <span className="chat-header-title">QA Engine Chat</span>
        <span className="chat-header-subtitle">Talk to QA Engine</span>
        <div style={{ marginLeft: 'auto', display: 'flex', gap: 8 }}>
          <button
            className={`btn-header-toggle ${showSettings ? 'active' : ''}`}
            onClick={() => setShowSettings(s => !s)}
            title="Testing settings"
          >
            ⚙️ Settings
          </button>
        </div>
      </header>

      <div className="chat-body">
        {/* ── Sidebar ── */}
        <aside className="chat-sidebar">
          <button className="btn-new-chat" onClick={createConversation}>+ New Chat</button>
          <div className="conversations-list">
            {conversations.length === 0 && (
              <p className="no-convos-hint">Click "+ New Chat" to begin.</p>
            )}
            {conversations.map((conv: Conversation) => (
              <div
                key={conv.id}
                onClick={() => { loadConversation(conv.id); setShowSettings(false); }}
                className={`conversation-item ${activeConversationId === conv.id ? 'active' : ''}`}
              >
                <div className="conv-title">{conv.title}</div>
                {conv.target_url && (
                  <div className="conv-url">
                    {(() => { try { return new URL(conv.target_url!).hostname; } catch { return conv.target_url; } })()}
                  </div>
                )}
                <div className="conv-time">
                  {conv.updated_at ? new Date(conv.updated_at).toLocaleTimeString() : ''}
                </div>
              </div>
            ))}
          </div>
        </aside>

        {/* ── Settings Panel ── */}
        {showSettings && (
          <div className="settings-panel">
            <div className="settings-header">
              <h3>⚙️ Testing Preferences</h3>
              <p className="settings-subtitle">These settings apply to every run started from chat.</p>
            </div>

            <div className="settings-body">
              {/* Target URL */}
              <div className="setting-group">
                <label className="setting-label">🌐 Default Target URL *</label>
                <input
                  className="setting-input"
                  type="url"
                  value={settings.targetUrl}
                  onChange={e => updateSetting('targetUrl', e.target.value)}
                  placeholder="https://example.com"
                />
                <span className="setting-hint">You can override this in chat: "test https://..."</span>
              </div>

              {/* Testing Objective */}
              <div className="setting-group">
                <label className="setting-label">🎯 Default Testing Objective</label>
                <textarea
                  className="setting-textarea"
                  value={settings.testingObjective}
                  onChange={e => updateSetting('testingObjective', e.target.value)}
                  placeholder="e.g. Exercise the contact CRUD features"
                  rows={2}
                />
              </div>

              {/* Credentials */}
              <div className="setting-group">
                <label className="setting-label">🔐 Credentials (optional)</label>
                <div className="setting-row">
                  <input
                    className="setting-input"
                    type="text"
                    value={settings.username}
                    onChange={e => updateSetting('username', e.target.value)}
                    placeholder="Username / Email"
                    autoComplete="off"
                  />
                  <input
                    className="setting-input"
                    type="password"
                    value={settings.password}
                    onChange={e => updateSetting('password', e.target.value)}
                    placeholder="Password"
                    autoComplete="new-password"
                  />
                </div>
                <span className="setting-hint">Stored in memory only — never written to disk.</span>
              </div>

              {/* Write Permissions */}
              <div className="setting-group">
                <label className="setting-label">✍️ Write Permissions</label>
                <div className="setting-checks">
                  <label className="check-item">
                    <input type="checkbox" checked={settings.allowControlledWrites}
                      onChange={e => updateSetting('allowControlledWrites', e.target.checked)} />
                    <div>
                      <span>Allow controlled writes</span>
                      <span className="check-hint">Submit forms the application already presents.</span>
                    </div>
                  </label>
                  <label className="check-item">
                    <input type="checkbox" checked={settings.allowTestDataCreation}
                      onChange={e => updateSetting('allowTestDataCreation', e.target.checked)} />
                    <div>
                      <span>Allow test-data creation</span>
                      <span className="check-hint">Create records clearly tagged as QA Engine test data.</span>
                    </div>
                  </label>
                  <label className="check-item">
                    <input type="checkbox" checked={settings.allowDeletion}
                      onChange={e => updateSetting('allowDeletion', e.target.checked)} />
                    <div>
                      <span>Allow deletion of own test records</span>
                      <span className="check-hint">Only deletes records QA Engine created.</span>
                    </div>
                  </label>
                </div>
              </div>

              {/* Mode toggles */}
              <div className="setting-group">
                <label className="setting-label">🛡️ Behaviour</label>
                <div className="setting-checks">
                  <label className="check-item">
                    <input type="checkbox" checked={settings.headlessMode}
                      onChange={e => updateSetting('headlessMode', e.target.checked)} />
                    <div>
                      <span>Headless mode</span>
                      <span className="check-hint">Browser runs in background (no visible window).</span>
                    </div>
                  </label>
                  <label className="check-item">
                    <input type="checkbox" checked={settings.safeMode}
                      onChange={e => updateSetting('safeMode', e.target.checked)} />
                    <div>
                      <span>Safe mode</span>
                      <span className="check-hint">Extra caution. Does not by itself allow writes.</span>
                    </div>
                  </label>
                </div>
              </div>

              {/* Test Case Types */}
              <div className="setting-group">
                <label className="setting-label">🧪 Test Case Types</label>
                <div className="setting-checks">
                  <label className="check-item">
                    <input
                      type="checkbox"
                      checked={settings.testCaseTypes.includes('positive')}
                      onChange={e => {
                        const types = settings.testCaseTypes.filter(t => t !== 'positive');
                        updateSetting('testCaseTypes', e.target.checked ? [...types, 'positive'] : types);
                      }}
                    />
                    <div>
                      <span>✅ Positive (Happy Path)</span>
                      <span className="check-hint">Valid inputs → successful outcome. Default behaviour.</span>
                    </div>
                  </label>
                  <label className="check-item">
                    <input
                      type="checkbox"
                      checked={settings.testCaseTypes.includes('negative')}
                      onChange={e => {
                        const types = settings.testCaseTypes.filter(t => t !== 'negative');
                        updateSetting('testCaseTypes', e.target.checked ? [...types, 'negative'] : types);
                      }}
                    />
                    <div>
                      <span>❌ Negative (Error Cases)</span>
                      <span className="check-hint">Invalid inputs, empty fields, wrong formats → error messages.</span>
                    </div>
                  </label>
                </div>
                {settings.testCaseTypes.length === 0 && (
                  <span className="check-hint" style={{ color: '#f59e0b' }}>
                    ⚠️ No type selected — will default to happy path testing.
                  </span>
                )}
              </div>

              {/* Max Actions */}
              <div className="setting-group">
                <label className="setting-label">🔢 Max Actions (optional)</label>
                <input
                  className="setting-input"
                  type="number"
                  value={settings.maxActions}
                  onChange={e => updateSetting('maxActions', e.target.value)}
                  placeholder="Leave blank for unlimited"
                  min={1}
                  max={10000}
                />
              </div>

              {/* Save button */}
              <button className="btn-save-settings" onClick={saveSettings}>
                {settingsSaved ? '✅ Saved!' : '💾 Save Settings'}
              </button>
              <p className="settings-note">
                💡 You can still override any setting in chat:<br />
                <em>"Test only login, limit to 30 actions"</em>
              </p>
            </div>
          </div>
        )}

        {/* ── Main Chat Area ── */}
        <div className="chat-main">
          {activeConversation ? (
            <>
              <div className="messages-area">
                {activeConversation.messages.length === 0 && !streamingContent && !thinkingText && (
                  <div className="empty-chat-hint">
                    <p>💬 Ready to test! Type your task below.</p>
                    {settings.targetUrl && (
                      <p className="hint-url">🌐 Default target: <strong>{settings.targetUrl}</strong></p>
                    )}
                    <p className="hint-examples">
                      Try: <em>"test the login form"</em> or <em>"check the signup and dashboard"</em>
                    </p>
                  </div>
                )}

                {activeConversation.messages.map(msg => (
                  <div key={msg.id} className={`message msg-${msg.role}`}>
                    <div className="msg-role">{msg.role === 'user' ? '👤 You' : '🤖 QA Engine'}</div>
                    <div className="msg-content">{msg.content}</div>
                    {msg.runId && (
                      <a href={`/runs/${msg.runId}`} className="msg-run-link" target="_blank" rel="noopener noreferrer">
                        📊 View Live Run →
                      </a>
                    )}
                    <div className="msg-time">{new Date(msg.timestamp).toLocaleTimeString()}</div>
                  </div>
                ))}

                {thinkingText && (
                  <div className="message msg-assistant thinking-msg">
                    <div className="msg-role">🤖 QA Engine</div>
                    <div className="msg-content thinking-text">🤔 {thinkingText}</div>
                  </div>
                )}
                {streamingContent && (
                  <div className="message msg-assistant streaming-msg">
                    <div className="msg-role">🤖 QA Engine</div>
                    <div className="msg-content">{streamingContent}<span className="cursor-blink">▋</span></div>
                  </div>
                )}
                <div ref={messagesEndRef} />
              </div>

              {/* Settings badge strip */}
              <div className="settings-strip">
                {settings.targetUrl && (
                  <span className="badge">🌐 {(() => { try { return new URL(settings.targetUrl).hostname; } catch { return settings.targetUrl; } })()}</span>
                )}
                {settings.allowTestDataCreation && <span className="badge">✍️ Test data</span>}
                {settings.allowDeletion && <span className="badge">🗑️ Deletion</span>}
                {settings.maxActions && <span className="badge">🔢 Max {settings.maxActions}</span>}
                {settings.testCaseTypes.includes('positive') && <span className="badge">✅ Positive</span>}
                {settings.testCaseTypes.includes('negative') && <span className="badge">❌ Negative</span>}
                <button className="badge badge-btn" onClick={() => setShowSettings(s => !s)}>⚙️ Edit</button>
              </div>

              <div className="input-area">
                <textarea
                  ref={textareaRef}
                  value={inputMessage}
                  onChange={e => setInputMessage(e.target.value)}
                  onKeyDown={handleKeyDown}
                  placeholder={isStreaming ? 'Waiting for response...' : 'Type your task... (Enter to send, Shift+Enter new line)'}
                  disabled={isStreaming}
                  rows={2}
                  className="chat-textarea"
                  autoFocus
                />
                <button
                  onClick={sendMessage}
                  disabled={isStreaming || !inputMessage.trim()}
                  className="btn-send"
                >
                  {isStreaming ? '...' : 'Send ↑'}
                </button>
              </div>
            </>
          ) : (
            <div className="welcome-state">
              <h2>👋 Welcome to QA Engine Chat</h2>
              <p>Test web apps using natural language. Configure your preferences first.</p>
              <div style={{ display: 'flex', gap: 12 }}>
                <button className="btn-start" onClick={() => setShowSettings(s => !s)}>
                  ⚙️ Configure Settings
                </button>
                <button className="btn-start btn-start-secondary" onClick={createConversation}>
                  + New Chat
                </button>
              </div>
              <div className="example-box">
                <h4>Example commands:</h4>
                <ul>
                  <li>"Test the login and signup forms"</li>
                  <li>"Only check the dashboard, limit to 30 actions"</li>
                  <li>"Test the contact CRUD operations"</li>
                  <li>"What have you found so far?"</li>
                  <li>"Stop" / "Continue"</li>
                </ul>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
