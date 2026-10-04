"""Chat orchestrator for conversational QA agent interface."""

import uuid
from datetime import datetime
from typing import AsyncGenerator, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ChatConversation, ChatMessage, ConversationContext
from app.chat.instruction_parser import InstructionParser, TestInstruction
from app.schemas import CreateRunRequest, RunConfiguration, RunStatusEnum
from app.agent.run_manager import RunManager


class ChatOrchestrator:
    """Orchestrates conversational QA testing through chat interface."""
    
    def __init__(
        self,
        db: AsyncSession,
        run_manager: RunManager,
    ):
        self.db = db
        self.run_manager = run_manager
        self.parser = InstructionParser()
    
    async def create_conversation(self, title: Optional[str] = None) -> ChatConversation:
        """Create a new chat conversation."""
        conversation = ChatConversation(
            id=str(uuid.uuid4()),
            title=title or "New Conversation",
            status="active",
            context_json="{}",
        )
        self.db.add(conversation)
        await self.db.commit()
        return conversation
    
    async def get_conversation(self, conversation_id: str) -> Optional[ChatConversation]:
        """Get conversation by ID."""
        from sqlalchemy import select
        result = await self.db.execute(
            select(ChatConversation).where(ChatConversation.id == conversation_id)
        )
        return result.scalar_one_or_none()
    
    async def send_message(
        self,
        conversation_id: str,
        user_message: str,
        settings=None,
    ) -> AsyncGenerator[str, None]:
        """
        Process user message and stream response.
        
        Yields JSON-formatted chunks:
        - {"type": "thinking", "content": "..."}
        - {"type": "response", "content": "..."}
        - {"type": "action", "action": "start_run", "run_id": "..."}
        - {"type": "complete"}
        """
        import json
        
        # Get conversation
        conversation = await self.get_conversation(conversation_id)
        if not conversation:
            yield json.dumps({"type": "error", "content": "Conversation not found"})
            return
        
        # Load context
        import json as json_module
        context_data = json_module.loads(conversation.context_json) if conversation.context_json != '{}' else {}
        context = ConversationContext.from_dict(context_data)
        
        # Save user message
        user_msg = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            role="user",
            content=user_message,
            timestamp=datetime.utcnow(),
        )
        self.db.add(user_msg)
        await self.db.commit()
        
        # Parse instruction
        yield json.dumps({"type": "thinking", "content": "Understanding your request..."})
        
        instruction = self.parser.parse(user_message, context.to_dict())
        user_msg.intent_json = json.dumps(instruction.dict())
        await self.db.commit()
        
        # Generate response based on intent
        async for chunk in self._handle_instruction(
            conversation,
            context,
            instruction,
            user_message,
            settings=settings,
        ):
            yield chunk
        
        # Update conversation context
        conversation.context_json = json.dumps(context.to_dict())
        conversation.updated_at = datetime.utcnow()
        await self.db.commit()
        
        yield json.dumps({"type": "complete"})
    
    async def _handle_instruction(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
        instruction: TestInstruction,
        original_message: str,
        settings=None,
    ) -> AsyncGenerator[str, None]:
        """Handle parsed instruction and generate response."""
        import json

        if instruction.intent == "test":
            async for chunk in self._handle_test_intent(
                conversation, context, instruction, settings=settings
            ):
                yield chunk
        
        elif instruction.intent == "report":
            async for chunk in self._handle_report_intent(
                conversation, context, instruction
            ):
                yield chunk
        
        elif instruction.intent == "continue":
            async for chunk in self._handle_continue_intent(
                conversation, context
            ):
                yield chunk
        
        elif instruction.intent == "stop":
            async for chunk in self._handle_stop_intent(
                conversation, context
            ):
                yield chunk
        
        elif instruction.intent == "chat":
            async for chunk in self._handle_chat_intent(
                conversation, context, original_message
            ):
                yield chunk

        else:
            yield json.dumps({
                "type": "response",
                "content": f"I'll {instruction.intent} that for you."
            })
    
    async def _handle_test_intent(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
        instruction: TestInstruction,
        settings=None,
    ) -> AsyncGenerator[str, None]:
        """Handle test intent - start a new QA run."""
        import json
        
        # Determine target URL (instruction > settings > context)
        settings_url = getattr(settings, 'targetUrl', None) if settings else None
        target_url = instruction.target_url or settings_url or context.target_url
        if not target_url:
            yield json.dumps({
                "type": "response",
                "content": "I need a target URL to test. Please provide a URL like: https://example.com"
            })
            return
        
        # Update context
        if instruction.target_url:
            context.target_url = instruction.target_url
        
        # Build response about what we'll test
        test_description = self._generate_test_description(instruction)
        yield json.dumps({
            "type": "response",
            "content": f"Got it! {test_description}"
        })
        
        # ── Build testing_objective ───────────────────────────────────────────
        # Write a structured completion checklist so the AI knows exactly when
        # the task is DONE and should call FINISH — no hard action caps needed.
        named_modules  = instruction.modules
        named_features = instruction.features
        settings_obj   = getattr(settings, 'testingObjective', None) if settings else None

        # Determine what test case types to execute
        raw_types = getattr(settings, 'testCaseTypes', None) if settings else None
        test_case_types = raw_types if raw_types else ["positive"]  # default = happy path only
        do_positive = "positive" in test_case_types
        do_negative = "negative" in test_case_types

        # Build test-type instruction block
        if do_positive and do_negative:
            type_instruction = (
                "Test Case Types to Execute:\n"
                "  [POSITIVE] Happy path: use valid, correctly formatted inputs and verify successful outcomes.\n"
                "  [NEGATIVE] Error cases: use invalid inputs (empty fields, wrong formats, too-long values, "
                "wrong credentials) and verify the application shows appropriate error messages.\n"
                "Generate and execute BOTH positive and negative test cases for each form/feature you test.\n"
            )
            type_steps = (
                "2. Tested the POSITIVE (happy) path — valid inputs → successful outcome\n"
                "3. Tested NEGATIVE cases — invalid/empty inputs → appropriate error messages shown\n"
                "4. Verified the system's response to both attempts\n"
            )
        elif do_negative:
            type_instruction = (
                "Test Case Types to Execute:\n"
                "  [NEGATIVE ONLY] Focus exclusively on error/edge cases: empty required fields, "
                "invalid formats, wrong credentials, boundary values, and missing required data. "
                "Verify the application shows appropriate error messages for each invalid input.\n"
                "Do NOT test the happy path — your scope is negative test cases only.\n"
            )
            type_steps = (
                "2. Attempted submission with empty required fields — verified error messages\n"
                "3. Attempted submission with invalid format inputs — verified error messages\n"
                "4. Verified the application rejects invalid data gracefully\n"
            )
        else:
            # Default: positive / happy path only
            type_instruction = (
                "Test Case Types to Execute:\n"
                "  [POSITIVE ONLY] Focus on the happy path: use valid, correctly formatted inputs "
                "and verify that the application accepts them and shows the expected success outcome.\n"
                "Do NOT test error cases — your scope is positive/happy-path test cases only.\n"
            )
            type_steps = (
                "2. Tested the happy path with valid inputs → successful outcome verified\n"
                "3. Verified the expected success response (page change, confirmation message, etc.)\n"
            )

        if named_modules or named_features:
            targets = " and ".join(named_modules + named_features)
            extra   = f"\n\nAdditional context: {settings_obj}" if settings_obj else ""
            # Detect auth-focused objectives so we can add an explicit post-auth stop rule
            auth_terms = ["signup", "sign up", "register", "registration", "login", "log in", "sign in"]
            is_auth_scope = any(t in targets.lower() for t in auth_terms)
            post_auth_stop = (
                "\n⚠️ CRITICAL STOP RULE: Once the form has been submitted and the "
                "application responds (redirect to another page, success message, OR error "
                "message), call FINISH immediately. Do NOT follow the redirect. Do NOT "
                "explore pages that open after submission — they are outside your scope.\n"
            ) if is_auth_scope else ""
            testing_objective = (
                f"Task: Test ONLY the {targets} feature(s).\n\n"
                f"{type_instruction}\n"
                f"You are DONE when you have completed ALL of the following:\n"
                f"1. Located and loaded the {targets} page/form\n"
                f"{type_steps}"
                f"{post_auth_stop}"
                f"\nOnce ALL steps are complete, call FINISH immediately.\n"
                f"Do NOT navigate to any other section, module, or page — "
                f"your scope is strictly limited to {targets}."
                f"{extra}"
            )
        else:
            # Open-ended run — still guide the type of tests
            base_obj = settings_obj or ""
            testing_objective = (f"{base_obj}\n\n{type_instruction}").strip() if base_obj else type_instruction

        # ── Max actions (user-explicit only — no auto-cap) ───────────────────
        max_actions_str = getattr(settings, 'maxActions', None) if settings else None
        max_actions = (
            instruction.constraints.get("max_actions") or
            (int(max_actions_str) if max_actions_str else None)
        )

        # ── Runtime limit (user-explicit only) ───────────────────────────────
        max_runtime_seconds = instruction.constraints.get("max_runtime_seconds")
        
        # Permissions: settings are the baseline, instruction can override
        allow_writes   = instruction.constraints.get("allow_safe_test_data_creation",
                            getattr(settings, 'allowTestDataCreation', False) if settings else False)
        allow_delete   = instruction.constraints.get("allow_destructive_actions",
                            getattr(settings, 'allowDeletion', False) if settings else False)
        allow_ctrl     = getattr(settings, 'allowControlledWrites', False) if settings else False
        headless       = getattr(settings, 'headlessMode', True) if settings else True
        safe_mode      = getattr(settings, 'safeMode', True) if settings else True

        config = RunConfiguration(
            allow_safe_test_data_creation=allow_writes,
            allow_destructive_actions=allow_delete,
            allow_controlled_writes=allow_ctrl,
            allow_login=True,
            allow_test_account_creation=True,
            headless=headless,
            safe_mode=safe_mode,
            max_actions=max_actions,            # only set if user explicitly gave a number
            max_runtime_seconds=max_runtime_seconds,
            testing_objective=testing_objective,
            test_case_types=test_case_types,
            focus_modules=list(named_modules or named_features or []),
        )
        
        # Credentials from settings
        username = getattr(settings, 'username', None) if settings else None
        password = getattr(settings, 'password', None) if settings else None
        
        # Create run request
        run_request = CreateRunRequest(
            url=target_url,
            authorization_ack=True,
            configuration=config,
            username=username or None,
            password=password or None,
        )
        
        yield json.dumps({"type": "thinking", "content": "Launching browser and starting QA run..."})
        
        try:
            # Create then immediately start the run (same as /api/runs endpoint)
            run_id = await self.run_manager.create_run(run_request, self.db)
            context.active_run_id = run_id
            await self.run_manager.start_run(run_id, self.db)
            
            # Save assistant message
            assistant_msg = ChatMessage(
                id=str(uuid.uuid4()),
                conversation_id=conversation.id,
                role="assistant",
                content=f"✅ QA run started! Testing: {test_description}. View the live run for real-time activity.",
                timestamp=datetime.utcnow(),
                run_id=run_id,
            )
            self.db.add(assistant_msg)
            await self.db.commit()
            
            yield json.dumps({"type": "action", "action": "start_run", "run_id": run_id})
            yield json.dumps({
                "type": "response",
                "content": (
                    f"✅ Browser launched! I'm now testing **{test_description}**.\n\n"
                    f"🔗 Run ID: `{run_id}`\n"
                    f"👉 View live run at: /runs/{run_id}\n\n"
                    f"You can continue chatting while I test. Ask me to stop, pause, or focus on something else anytime!"
                )
            })
            
        except Exception as e:
            yield json.dumps({"type": "error", "content": f"Failed to start run: {str(e)}"})
    
    async def _handle_report_intent(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
        instruction: TestInstruction,
    ) -> AsyncGenerator[str, None]:
        """Handle report intent - show results."""
        import json
        
        if not context.active_run_id:
            yield json.dumps({
                "type": "response",
                "content": "No active test run. Start a test first by saying something like 'test the login form'"
            })
            return
        
        # Get run status
        run = await self.run_manager.get_run(context.active_run_id)
        if not run:
            yield json.dumps({
                "type": "response",
                "content": "Couldn't find the test run."
            })
            return
        
        # Generate summary
        summary = f"""
📊 **Test Run Summary**

**Status**: {run.status}
**Started**: {run.created_at}
**Actions**: {len(run.actions)} actions performed
**Pages**: {len(run.pages)} pages visited

**Modules Tested**: {', '.join(context.tested_modules) or 'Still exploring...'}
"""
        
        yield json.dumps({
            "type": "response",
            "content": summary.strip()
        })
        
        if run.status == "completed":
            yield json.dumps({
                "type": "action",
                "action": "show_report",
                "run_id": context.active_run_id,
            })
    
    async def _handle_continue_intent(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
    ) -> AsyncGenerator[str, None]:
        """Handle continue intent - resume paused run."""
        import json
        
        if not context.active_run_id:
            yield json.dumps({
                "type": "response",
                "content": "No active test run to continue. What would you like me to test?"
            })
            return
        
        try:
            await self.run_manager.resume_run(context.active_run_id)
            yield json.dumps({
                "type": "response",
                "content": "✅ Continuing the test..."
            })
        except Exception as e:
            yield json.dumps({
                "type": "error",
                "content": f"Failed to continue: {str(e)}"
            })
    
    async def _handle_stop_intent(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
    ) -> AsyncGenerator[str, None]:
        """Handle stop intent - pause/end run."""
        import json
        
        if not context.active_run_id:
            yield json.dumps({
                "type": "response",
                "content": "No active test run to stop."
            })
            return
        
        try:
            await self.run_manager.pause_run(context.active_run_id)
            yield json.dumps({
                "type": "response",
                "content": "⏸️ Test paused. Say 'continue' to resume, or give me a new task!"
            })
        except Exception as e:
            yield json.dumps({
                "type": "error",
                "content": f"Failed to pause: {str(e)}"
            })
    
    async def _handle_chat_intent(
        self,
        conversation: ChatConversation,
        context: ConversationContext,
        message: str,
    ) -> AsyncGenerator[str, None]:
        """Handle casual chat, greetings, and questions — never starts a run."""
        import json

        msg = message.strip().lower()

        # Greetings
        if any(w in msg for w in ["hi", "hey", "hello", "howdy", "yo", "hiya"]):
            reply = (
                "Hey! 👋 I'm GemmaQA, your AI testing assistant.\n\n"
                "I can test websites for you. Just tell me what to test — for example:\n"
                "• *\"Test the signup feature of https://example.com\"*\n"
                "• *\"Check the login form\"*\n"
                "• *\"Test contacts and search\"*\n\n"
                f"{'🔗 Target URL: ' + context.target_url if context.target_url else 'No URL set yet — share one to get started!'}"
            )
        elif any(w in msg for w in ["thanks", "thank you", "great", "awesome", "cool", "nice"]):
            reply = "You're welcome! 😊 Let me know if you need anything else tested."
        elif any(w in msg for w in ["help", "what can you do", "who are you"]):
            reply = (
                "**I'm GemmaQA** — an autonomous QA testing agent. Here's what I can do:\n\n"
                "🧪 **Test specific features** — *\"test the signup form\"*\n"
                "🔍 **Test multiple modules** — *\"test login and contacts\"*\n"
                "📊 **Get a report** — *\"show me the results\"*\n"
                "⏹️ **Stop a run** — *\"stop\"* or *\"pause\"*\n\n"
                "Set your target URL and credentials in ⚙️ **Settings** before testing."
            )
        elif any(w in msg for w in ["bye", "goodbye", "see you"]):
            reply = "Goodbye! Come back whenever you need something tested. 👋"
        else:
            # Generic question / unclear intent
            reply = (
                "I'm not sure what you'd like me to do. 🤔\n\n"
                "I'm a QA testing assistant — try something like:\n"
                "• *\"Test the signup feature of https://example.com\"*\n"
                "• *\"Check the login and dashboard\"*\n\n"
                "Or open ⚙️ **Settings** to configure your target URL first."
            )

        assistant_msg = ChatMessage(
            id=str(uuid.uuid4()),
            conversation_id=conversation.id,
            role="assistant",
            content=reply,
            timestamp=datetime.utcnow(),
        )
        self.db.add(assistant_msg)
        await self.db.commit()

        yield json.dumps({"type": "response", "content": reply})

    def _generate_test_description(self, instruction: TestInstruction) -> str:
        """Generate human-readable description of what will be tested."""
        parts = []
        
        if instruction.modules:
            parts.append(f"the {', '.join(instruction.modules)} module(s)")
        elif instruction.features:
            parts.append(f"these features: {', '.join(instruction.features)}")
        else:
            parts.append("the application")
        
        if instruction.scope == "focused":
            parts.append("(targeted testing)")
        elif instruction.scope == "comprehensive":
            parts.append("(comprehensive testing)")
        
        return " ".join(parts)
