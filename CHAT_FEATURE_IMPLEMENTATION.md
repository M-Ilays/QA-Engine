# Chat Feature Implementation Guide

## Overview

This document describes the new chat-based conversational interface for GemmaQA that allows users to interact with the QA agent through natural language, specify targeted testing tasks, and maintain context across multiple conversations.

## Architecture

```
┌─────────────────────────────────────────┐
│   Frontend Chat UI (React Component)    │
│   - Message input                        │
│   - Conversation history                 │
│   - Streaming responses                  │
└──────────────────┬──────────────────────┘
                   │ HTTP/SSE
                   ▼
┌─────────────────────────────────────────┐
│      Chat API (/api/chat)               │
│   - POST /conversations                  │
│   - GET /conversations/{id}              │
│   - POST /conversations/{id}/messages    │
└──────────────────┬──────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────┐
│       Chat Orchestrator                  │
│   - Parse user instructions              │
│   - Maintain conversation context        │
│   - Coordinate QA runs                   │
│   - Stream responses                     │
└──────────────────┬──────────────────────┘
                   │
                   ├─> Instruction Parser
                   ├─> Run Manager
                   └─> Agent Controller
```

## Key Features

### 1. **Natural Language Instructions**

Users can say things like:
- "Test the login and signup forms"
- "Check the dashboard after login"
- "Now test the contact CRUD operations"
- "Run comprehensive tests on https://example.com"
- "Stop the current test"
- "Show me the results"

### 2. **Context Preservation**

The system maintains:
- Target URL across conversations
- Previously tested modules
- Discovered features
- Past findings and bugs
- User preferences
- Active run status

### 3. **Intelligent Parsing**

The `InstructionParser` understands:
- **Intents**: test, analyze, report, continue, stop
- **Modules**: login, signup, dashboard, profile, CRUD, etc.
- **Scope**: focused, exploratory, comprehensive
- **Constraints**: max actions, time limits, permissions

### 4. **Streaming Responses**

Uses Server-Sent Events (SSE) to stream:
- Thinking status
- Responses
- Actions (start run, show report)
- Errors
- Completion signals

## Implementation Files

### Backend (Created)

1. **`app/models.py`** (Updated)
   - Added `ChatConversation` table
   - Added `ChatMessage` table

2. **`app/chat/instruction_parser.py`** (New)
   - Parses natural language to structured instructions
   - Extracts URLs, modules, features, constraints
   - Generates test configurations

3. **`app/chat/orchestrator.py`** (New)
   - Main chat coordination logic
   - Handles different intents
   - Manages conversation context
   - Streams responses via SSE

4. **`app/routers/chat.py`** (New)
   - FastAPI endpoints for chat
   - SSE streaming support
   - CORS-friendly headers

5. **`app/main.py`** (Updated)
   - Registered chat router

### Frontend (To Be Created)

Create these React components:

#### 1. `ChatInterface.tsx`
```typescript
import React, { useState, useEffect, useRef } from 'react';

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
  messages: Message[];
  targetUrl?: string;
}

export const ChatInterface: React.FC = () => {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConversation, setActiveConversation] = useState<string | null>(null);
  const [inputMessage, setInputMessage] = useState('');
  const [isStreaming, setIsStreaming] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  // Create new conversation
  const createConversation = async () => {
    const response = await fetch('/api/chat/conversations', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title: 'New Chat' }),
    });
    const data = await response.json();
    setActiveConversation(data.id);
    await loadConversations();
  };

  // Load conversations list
  const loadConversations = async () => {
    const response = await fetch('/api/chat/conversations');
    const data = await response.json();
    setConversations(data.conversations);
  };

  // Load specific conversation
  const loadConversation = async (id: string) => {
    const response = await fetch(`/api/chat/conversations/${id}`);
    const data = await response.json();
    setActiveConversation(id);
    // Update conversation in state
  };

  // Send message with SSE streaming
  const sendMessage = async (message: string) => {
    if (!activeConversation) return;

    setIsStreaming(true);
    const eventSource = new EventSource(
      `/api/chat/conversations/${activeConversation}/messages?message=${encodeURIComponent(message)}`
    );

    let assistantMessage = '';

    eventSource.onmessage = (event) => {
      const data = JSON.parse(event.data);

      if (data.type === 'thinking') {
        // Show thinking indicator
        console.log('Thinking:', data.content);
      } else if (data.type === 'response') {
        // Stream response content
        assistantMessage += data.content;
        // Update UI
      } else if (data.type === 'action') {
        // Handle action (e.g., start_run, show_report)
        if (data.action === 'start_run') {
          // Navigate to run page or show in sidebar
          console.log('Run started:', data.run_id);
        }
      } else if (data.type === 'complete') {
        eventSource.close();
        setIsStreaming(false);
      } else if (data.type === 'error') {
        console.error('Error:', data.content);
        eventSource.close();
        setIsStreaming(false);
      }
    };

    eventSource.onerror = () => {
      eventSource.close();
      setIsStreaming(false);
    };
  };

  // UI rendering
  return (
    <div className="chat-interface">
      {/* Sidebar with conversations list */}
      <div className="conversations-sidebar">
        <button onClick={createConversation}>New Chat</button>
        {conversations.map((conv) => (
          <div
            key={conv.id}
            onClick={() => loadConversation(conv.id)}
            className={activeConversation === conv.id ? 'active' : ''}
          >
            {conv.title}
          </div>
        ))}
      </div>

      {/* Main chat area */}
      <div className="chat-main">
        <div className="messages">
          {/* Render messages */}
          <div ref={messagesEndRef} />
        </div>

        <div className="input-area">
          <input
            type="text"
            value={inputMessage}
            onChange={(e) => setInputMessage(e.target.value)}
            onKeyPress={(e) => {
              if (e.key === 'Enter' && !isStreaming) {
                sendMessage(inputMessage);
                setInputMessage('');
              }
            }}
            placeholder="Type your message... (e.g., 'Test the login form')"
            disabled={isStreaming}
          />
          <button
            onClick={() => {
              sendMessage(inputMessage);
              setInputMessage('');
            }}
            disabled={isStreaming || !inputMessage.trim()}
          >
            Send
          </button>
        </div>
      </div>
    </div>
  );
};
```

## Usage Examples

### User Messages and Expected Behavior

1. **Start a new test:**
   ```
   User: "Test the login and signup forms on https://thinking-tester-contact-list.herokuapp.com/"
   
   Assistant: "Got it! I'll test the login and signup module(s) (targeted testing).
               ✅ QA run started! I'm now testing the login and signup module(s).
               You can watch the live progress or continue our conversation."
   ```

2. **Focused testing:**
   ```
   User: "Only check the contact CRUD operations, limit to 30 actions"
   
   Assistant: "I'll test the contacts and crud module(s) (targeted testing).
                Starting with a 30-action limit..."
   ```

3. **Check status:**
   ```
   User: "What have you found so far?"
   
   Assistant: "📊 Test Run Summary
                Status: running
                Actions: 15 actions performed
                Pages: 5 pages visited
                Modules Tested: login, dashboard, contacts"
   ```

4. **Continue/Stop:**
   ```
   User: "Stop"
   Assistant: "⏸️ Test paused. Say 'continue' to resume, or give me a new task!"
   
   User: "continue"
   Assistant: "✅ Continuing the test..."
   ```

## Database Schema

```sql
CREATE TABLE chat_conversations (
    id VARCHAR(36) PRIMARY KEY,
    title VARCHAR(512),
    status VARCHAR(32) DEFAULT 'active',
    target_url VARCHAR(2048),
    context_json TEXT DEFAULT '{}',
    created_at DATETIME,
    updated_at DATETIME
);

CREATE TABLE chat_messages (
    id VARCHAR(36) PRIMARY KEY,
    conversation_id VARCHAR(36),
    role VARCHAR(32),  -- 'user', 'assistant', 'system'
    content TEXT,
    intent_json TEXT,  -- Parsed instruction
    run_id VARCHAR(36),  -- Link to QA run
    timestamp DATETIME,
    FOREIGN KEY (conversation_id) REFERENCES chat_conversations(id),
    FOREIGN KEY (run_id) REFERENCES qa_runs(id)
);
```

## API Endpoints

### 1. Create Conversation
```http
POST /api/chat/conversations
Content-Type: application/json

{
  "title": "Optional conversation title"
}

Response:
{
  "id": "conv-uuid",
  "title": "New Conversation",
  "created_at": "2026-10-04T10:00:00Z"
}
```

### 2. List Conversations
```http
GET /api/chat/conversations?limit=20

Response:
{
  "conversations": [
    {
      "id": "conv-uuid",
      "title": "Testing Contact List",
      "target_url": "https://example.com",
      "updated_at": "2026-10-04T10:30:00Z"
    }
  ]
}
```

### 3. Get Conversation
```http
GET /api/chat/conversations/{conversation_id}

Response:
{
  "id": "conv-uuid",
  "title": "Testing Contact List",
  "status": "active",
  "target_url": "https://example.com",
  "context": {...},
  "messages": [...]
}
```

### 4. Send Message (SSE Stream)
```http
POST /api/chat/conversations/{conversation_id}/messages
Content-Type: application/json

{
  "message": "Test the login form"
}

Response: (Server-Sent Events stream)
data: {"type":"thinking","content":"Understanding your request..."}

data: {"type":"response","content":"Got it! I'll test the login module..."}

data: {"type":"action","action":"start_run","run_id":"run-uuid"}

data: {"type":"complete"}
```

## Next Steps

1. **Run database migration** to create chat tables
2. **Restart backend** to load chat router
3. **Create frontend components** (`ChatInterface.tsx`, `MessageBubble.tsx`)
4. **Add chat route** to frontend router (`/chat`)
5. **Add navigation link** to main UI
6. **Test the feature** with example conversations

## Benefits

✅ **User-Friendly**: Natural language instead of forms
✅ **Context-Aware**: Remembers previous tests and findings
✅ **Flexible**: Target specific modules or run comprehensive tests
✅ **Real-Time**: Streaming responses for immediate feedback
✅ **Integrated**: Links directly to QA runs and reports
✅ **Persistent**: All conversations saved in database

## Example Conversation Flow

```
User: Hi
Assistant: Hello! I'm your QA testing assistant. I can help you test web applications.
          Give me a URL and tell me what you'd like to test!

User: Test https://thinking-tester-contact-list.herokuapp.com/
Assistant: Got it! I'll test the application (exploratory testing).
          ✅ QA run started! I'm exploring the application now.
          [Run ID: run-12345]

User: Focus only on the login functionality
Assistant: ⏸️ Test paused. 
          Starting a new focused test on the login module...
          ✅ QA run started! Testing login (targeted testing).

User: What did you find?
Assistant: 📊 Test Run Summary
          Status: completed
          Modules Tested: login
          
          Found:
          - Login form accepts valid credentials
          - Error messages display for invalid inputs
          - Password masking works correctly
          
          [View full report]

User: Now test the contact CRUD operations
Assistant: ✅ Starting contact and crud testing...
```

## Configuration

No additional configuration needed! The feature uses:
- Existing database connection
- Existing run manager
- Existing agent controller
- Existing Gemini and Ollama providers

## Status

✅ Backend implementation complete
⏳ Frontend UI components needed
⏳ Database migration needed

---

**Ready to deploy!** Just create the frontend components and restart the servers.
