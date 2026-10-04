# Chat Feature - Quick Setup Guide

## ✅ What's Been Implemented

### Backend (Complete)
- ✅ Database models (`ChatConversation`, `ChatMessage`)
- ✅ Instruction parser (understands natural language)
- ✅ Chat orchestrator (coordinates tests)
- ✅ API endpoints (`/api/chat/*`)
- ✅ SSE streaming support
- ✅ Context preservation across conversations

### Frontend (Complete)
- ✅ `ChatInterface.tsx` component
- ✅ `ChatInterface.css` styling
- ⏳ Need to add route to app

## 🚀 Setup Instructions

### Step 1: Database Migration

The chat tables will be auto-created when you start the backend. No manual migration needed!

### Step 2: Add Chat Route to Frontend

Add this to your frontend router (usually `src/App.tsx` or `src/router.tsx`):

```typescript
import { ChatInterface } from './components/ChatInterface';

// In your routes:
<Route path="/chat" element={<ChatInterface />} />
```

### Step 3: Add Navigation Link

Add a link to the chat in your main navigation:

```tsx
<Link to="/chat">💬 Chat</Link>
```

### Step 4: Restart Servers

```bash
# Backend is already running
# Frontend is already running
# Just refresh the browser!
```

## 🎯 How to Use

### 1. Start a New Chat

Click "New Chat" in the sidebar.

### 2. Type Natural Language Commands

**Examples:**

```
Test the login and signup forms on https://thinking-tester-contact-list.herokuapp.com/
```

```
Only check the dashboard, limit to 30 actions
```

```
Test the contact CRUD operations
```

```
What have you found so far?
```

```
Stop
```

```
Continue
```

### 3. Watch It Work!

The agent will:
- ✅ Understand your intent
- ✅ Parse modules and constraints
- ✅ Start a targeted QA run
- ✅ Stream responses in real-time
- ✅ Remember context for follow-up questions

## 📋 Supported Commands

### Test Commands
- "Test the [module]"
- "Check only [features]"
- "Run comprehensive tests"
- "Test [URL]"

### Control Commands
- "Stop" / "Pause"
- "Continue" / "Resume"
- "End the test"

### Query Commands
- "What have you found?"
- "Show me the results"
- "Give me a report"

### Module Keywords

The parser recognizes:
- `login`, `signin`, `authentication`
- `signup`, `register`, `create account`
- `dashboard`, `home page`
- `profile`, `account settings`
- `contacts`, `address book`
- `forms`, `crud`, `search`, `cart`, `checkout`

### Constraints

You can specify:
- Action limits: "limit to 50 actions"
- Time limits: "run for 5 minutes"
- Permissions: "don't create test data", "allow delete"
- Scope: "only check", "comprehensive", "explore"

## 🔧 Technical Details

### API Endpoints

```http
POST   /api/chat/conversations              # Create conversation
GET    /api/chat/conversations              # List conversations
GET    /api/chat/conversations/:id          # Get conversation
POST   /api/chat/conversations/:id/messages # Send message (SSE stream)
DELETE /api/chat/conversations/:id          # Delete conversation
```

### Database Schema

```sql
chat_conversations (
  id, title, status, target_url, 
  context_json, created_at, updated_at
)

chat_messages (
  id, conversation_id, role, content,
  intent_json, run_id, timestamp
)
```

### Context Storage

Each conversation maintains:
- `target_url`: Current testing target
- `tested_modules`: Modules already tested
- `discovered_features`: Features found
- `previous_findings`: Past test results
- `user_preferences`: User settings
- `active_run_id`: Currently running test

## 🎨 UI Features

- **Sidebar**: List of conversations
- **Chat Area**: Message history
- **Streaming**: Real-time response streaming
- **Run Links**: Click to view associated QA runs
- **Empty State**: Helpful examples for new users
- **Responsive**: Works on desktop and tablet

## 🐛 Troubleshooting

### Backend errors?
Check the terminal running `python run.py`. Chat router is loaded at startup.

### Frontend not showing chat?
1. Make sure you added the route to your router
2. Check browser console for errors
3. Verify `/api/chat/conversations` returns 200

### SSE not working?
- Check CORS settings in `app/main.py`
- Ensure backend allows streaming responses
- Try in a different browser (Chrome/Firefox recommended)

### Database errors?
The chat tables are auto-created. If you get errors:
```bash
cd gemmaqa/backend
rm *.db  # Delete old database
python run.py  # Restart to recreate tables
```

## 📖 Example Conversation

```
User: Test https://thinking-tester-contact-list.herokuapp.com/
Assistant: 🤔 Understanding your request...
Assistant: Got it! I'll test the application (exploratory testing).
           ✅ QA run started! I'm now testing the application.
           You can watch the live progress or continue our conversation.
           [Run ID: abc-123]

User: Focus only on the login module
Assistant: ⏸️ Test paused.
           Got it! I'll test the login module(s) (targeted testing).
           ✅ QA run started! Testing the login module now.

User: What did you find?
Assistant: 📊 Test Run Summary
           Status: running
           Actions: 15 actions performed
           Pages: 3 pages visited
           Modules Tested: login

User: Stop
Assistant: ⏸️ Test paused. Say 'continue' to resume, or give me a new task!
```

## 🎉 Benefits

### For Users
- ✅ **Natural**: Talk to the agent like a human
- ✅ **Fast**: No forms to fill
- ✅ **Flexible**: Change focus mid-test
- ✅ **Contextual**: Agent remembers your testing history

### For Developers
- ✅ **Clean API**: RESTful with SSE streaming
- ✅ **Extensible**: Easy to add new intents
- ✅ **Integrated**: Works with existing QA engine
- ✅ **Persistent**: All data saved to database

## 🔮 Future Enhancements

Potential improvements:
- Voice input/output
- Multi-modal (show screenshots in chat)
- Collaborative testing (multiple users in same conversation)
- Smart suggestions based on previous tests
- Integration with bug tracking systems
- Scheduled tests via chat ("Test this every night")

## 📚 Code Structure

```
Backend:
  app/models.py                      # Added ChatConversation, ChatMessage
  app/chat/instruction_parser.py     # Natural language parsing
  app/chat/orchestrator.py           # Chat coordination logic
  app/routers/chat.py                # API endpoints
  app/main.py                        # Router registration

Frontend:
  src/components/ChatInterface.tsx   # Main chat UI
  src/components/ChatInterface.css   # Styling
  src/App.tsx                        # Add route here
```

## ✨ Ready to Use!

The feature is fully implemented and ready to use. Just:
1. Add the route to your frontend
2. Refresh your browser
3. Click "💬 Chat" in the navigation
4. Start testing with natural language!

---

**Questions?** Check the full implementation details in `CHAT_FEATURE_IMPLEMENTATION.md`